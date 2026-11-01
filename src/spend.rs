use crate::store::*;
use crate::*;
use chrono::{DateTime, Utc};
use std::collections::{BTreeMap, BTreeSet};
const FIELDS: [&str; 4] = ["input", "output", "cache_read", "cache_write"];
type Totals = BTreeMap<String, [i64; 4]>;
fn historical_path(path: &Path) -> Option<PathBuf> {
    for ancestor in path.ancestors() {
        match ancestor.canonicalize() {
            Ok(resolved) => {
                // Resolve existing symlinks before normalizing a deleted suffix.
                return orgs::absolute(&resolved.join(path.strip_prefix(ancestor).ok()?)).ok();
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                // A dangling symlink has no reliable workspace attribution.
                if ancestor.symlink_metadata().is_ok() {
                    return None;
                }
            }
            Err(_) => return None,
        }
    }
    None
}
fn inside(cwd: &str, root: &Path) -> bool {
    let cwd = Path::new(cwd);
    if !cwd.is_absolute() {
        return false;
    }
    let Some(real_root) = historical_path(root) else {
        return false;
    };
    historical_path(cwd).is_some_and(|path| path.starts_with(real_root))
}
fn counter(usage: &Value, field: &str) -> Result<i64> {
    match usage.get(field) {
        None => Ok(0),
        Some(value) => value
            .as_i64()
            .filter(|n| *n >= 0)
            .ok_or_else(|| anyhow!("invalid agent usage counter {field}: expected a nonnegative whole number within the reporting range")),
    }
}
fn add_counts(total: &mut [i64; 4], counts: &[i64; 4]) -> Result<()> {
    for (total, count) in total.iter_mut().zip(counts) {
        *total = total
            .checked_add(*count)
            .ok_or_else(|| anyhow!("agent usage token total overflow"))?;
    }
    Ok(())
}
fn modified(path: &Path) -> Result<Option<DateTime<Utc>>> {
    let metadata = path
        .metadata()
        .map_err(|error| anyhow!("could not inspect agent usage transcript: {error}"))?;
    if metadata.is_dir() {
        return Ok(None);
    }
    ensure!(
        metadata.is_file(),
        "agent usage transcript must be a regular file"
    );
    let modified = metadata
        .modified()
        .map_err(|error| anyhow!("could not read agent usage modification time: {error}"))?;
    Ok(Some(DateTime::<Utc>::from(modified)))
}
fn recent(path: &Path, since: DateTime<Utc>) -> Result<bool> {
    Ok(modified(path)?.is_some_and(|time| time >= since))
}
fn compressed(path: &Path) -> bool {
    path.file_name()
        .is_some_and(|name| name.to_string_lossy().ends_with(".jsonl.zst"))
}
type ZstdFrame = ruzstd::decoding::StreamingDecoder<
    std::io::BufReader<std::fs::File>,
    ruzstd::decoding::FrameDecoder,
>;
struct ZstdReader(Option<ZstdFrame>);
impl ZstdReader {
    fn next_frame(
        mut source: std::io::BufReader<std::fs::File>,
    ) -> std::io::Result<Option<ZstdFrame>> {
        use std::io::{BufRead, Read};
        loop {
            let prefix = source.fill_buf()?;
            if prefix.is_empty() {
                return Ok(None);
            }
            if prefix.len() >= 4 {
                let magic = u32::from_le_bytes([prefix[0], prefix[1], prefix[2], prefix[3]]);
                if (0x184d2a50..=0x184d2a5f).contains(&magic) {
                    let mut header = [0; 8];
                    source.read_exact(&mut header)?;
                    let length =
                        u32::from_le_bytes([header[4], header[5], header[6], header[7]]) as u64;
                    let copied =
                        std::io::copy(&mut source.by_ref().take(length), &mut std::io::sink())?;
                    if copied != length {
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::UnexpectedEof,
                            "truncated compressed agent usage metadata",
                        ));
                    }
                    continue;
                }
            }
            return ruzstd::decoding::StreamingDecoder::new(source)
                .map(Some)
                .map_err(|_| {
                    std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        "invalid compressed agent usage frame",
                    )
                });
        }
    }
    fn new(file: std::fs::File) -> std::io::Result<Self> {
        let frame = Self::next_frame(std::io::BufReader::new(file))?.ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "empty compressed agent usage transcript",
            )
        })?;
        Ok(Self(Some(frame)))
    }
}
impl std::io::Read for ZstdReader {
    fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
        if output.is_empty() {
            return Ok(0);
        }
        while let Some(frame) = self.0.as_mut() {
            let read = frame.read(output).map_err(|_| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "invalid compressed agent usage data",
                )
            })?;
            if read != 0 {
                return Ok(read);
            }
            // The decoder exposes the stored checksum; compare after collecting all output.
            if let Some(expected) = frame.decoder.get_checksum_from_data()
                && frame.decoder.get_calculated_checksum() != Some(expected)
            {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "compressed agent usage checksum mismatch",
                ));
            }
            if let Some(finished) = self.0.take() {
                self.0 = Self::next_frame(finished.into_inner())?;
            }
        }
        Ok(0)
    }
}
fn indexed_lines(path: &Path) -> Result<impl Iterator<Item = Result<(String, u64)>>> {
    use std::io::{BufRead, BufReader};
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // A replaced transcript must not leave a report waiting on a named pipe.
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = options
        .open(path)
        .map_err(|error| anyhow!("could not open agent usage transcript: {error}"))?;
    ensure!(
        file.metadata()
            .map_err(|error| anyhow!("could not inspect open agent usage transcript: {error}"))?
            .is_file(),
        "agent usage transcript must be a regular file"
    );
    let reader: Box<dyn std::io::Read> = if compressed(path) {
        Box::new(
            ZstdReader::new(file)
                .map_err(|error| anyhow!("could not decode agent usage transcript: {error}"))?,
        )
    } else {
        Box::new(file)
    };
    let mut offset = 0u64;
    let mut reader = BufReader::new(reader);
    Ok(std::iter::from_fn(move || {
        let mut bytes = Vec::new();
        let size = match reader.read_until(b'\n', &mut bytes) {
            Ok(0) => return None,
            Ok(size) => size,
            Err(error) => {
                return Some(Err(anyhow!(
                    "could not read agent usage transcript: {error}"
                )));
            }
        };
        Some((|| {
            offset = offset
                .checked_add(size as u64)
                .ok_or_else(|| anyhow!("agent usage transcript offset overflow"))?;
            if bytes.last() == Some(&b'\n') {
                bytes.pop();
            }
            let line = String::from_utf8(bytes)
                .map_err(|_| anyhow!("invalid agent usage transcript: expected UTF-8"))?;
            Ok((line, offset))
        })())
    }))
}
fn lines(path: &Path) -> Result<impl Iterator<Item = Result<String>>> {
    Ok(indexed_lines(path)?.map(|line| line.map(|(line, _)| line)))
}
fn transcript_record(line: &str) -> Result<Option<Value>> {
    if line.trim().is_empty() {
        return Ok(None);
    }
    let record: Value =
        serde_json::from_str(line).map_err(|_| anyhow!("invalid agent usage transcript JSON"))?;
    ensure!(
        record.is_object()
            && record["type"]
                .as_str()
                .is_some_and(|kind| !kind.trim().is_empty()),
        "invalid agent usage transcript record: expected a typed object"
    );
    Ok(Some(record))
}
fn codex_metadata(record: &Value) -> Result<()> {
    ensure!(
        text(&record["type"]) == "session_meta"
            && record["payload"].is_object()
            && record["payload"]["cwd"]
                .as_str()
                .is_some_and(|cwd| !cwd.trim().is_empty()),
        "invalid agent usage: expected Codex session metadata with a workspace"
    );
    Ok(())
}

fn parse(stamp: &str) -> Option<DateTime<Utc>> {
    instant(stamp).ok().or_else(|| {
        chrono::NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M:%S%.f")
            .ok()
            .map(|t| t.and_utc())
    })
}
fn claude(root: &Path, since: DateTime<Utc>, until: DateTime<Utc>) -> Result<Totals> {
    let base = std::env::var("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| orgs::expand_home("~/.claude"))
        .join("projects");
    let re = regex::Regex::new(r"[^A-Za-z0-9]").expect("constant regex");
    let prefixes = [
        root.to_owned(),
        root.canonicalize().unwrap_or(root.to_owned()),
    ]
    .iter()
    .map(|p| re.replace_all(&p.to_string_lossy(), "-").into_owned())
    .collect::<Vec<_>>();
    let mut messages: BTreeMap<String, (String, [i64; 4])> = BTreeMap::new();
    for project in
        entries(&base).map_err(|error| anyhow!("could not read Claude usage directory: {error}"))?
    {
        if !prefixes.iter().any(|p| {
            project
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .starts_with(p)
        }) {
            continue;
        }
        for entry in walkdir::WalkDir::new(project) {
            let path = entry
                .map_err(|error| anyhow!("could not enumerate Claude usage transcripts: {error}"))?
                .into_path();
            if !path.extension().is_some_and(|e| e == "jsonl") || !recent(&path, since)? {
                continue;
            }
            for line in lines(&path)? {
                let line = line?;
                let Some(e) = transcript_record(&line)? else {
                    continue;
                };
                if text(&e["type"]) != "assistant" {
                    continue;
                }
                let msg = &e["message"];
                ensure!(msg.is_object(), "invalid agent usage assistant message");
                if msg["usage"].is_null() || text(&msg["model"]) == "<synthetic>" {
                    continue;
                }
                let cwd = e["cwd"]
                    .as_str()
                    .filter(|cwd| !cwd.trim().is_empty())
                    .ok_or_else(|| anyhow!("invalid agent usage workspace"))?;
                if !inside(cwd, root) {
                    continue;
                }
                let observed = parse(text(&e["timestamp"]))
                    .ok_or_else(|| anyhow!("invalid agent usage timestamp"))?;
                if !(since..=until).contains(&observed) {
                    continue;
                }
                ensure!(
                    msg["usage"].is_object(),
                    "invalid agent usage: expected a usage object"
                );
                let key = msg["id"]
                    .as_str()
                    .filter(|id| !id.is_empty())
                    .or_else(|| e["uuid"].as_str().filter(|id| !id.is_empty()))
                    .ok_or_else(|| anyhow!("invalid agent usage: message has no id or uuid"))?
                    .to_owned();
                let mut row = [0; 4];
                for (i, field) in [
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ]
                .iter()
                .enumerate()
                {
                    row[i] = counter(&msg["usage"], field)
                        .map_err(|e| anyhow!("{}: {e}", path.display()))?;
                }
                let known = messages
                    .entry(key)
                    .or_insert_with(|| (msg["model"].as_str().unwrap_or("unknown").into(), [0; 4]));
                for (i, n) in row.iter().enumerate() {
                    known.1[i] = known.1[i].max(*n);
                }
            }
        }
    }
    let mut totals = Totals::new();
    for (model, row) in messages.into_values() {
        if row.iter().any(|n| *n != 0) {
            let into = totals.entry(model).or_default();
            add_counts(into, &row)?;
        }
    }
    Ok(totals)
}
fn codex_counts(usage: &Value, observed: bool) -> Result<[i64; 4]> {
    ensure!(
        usage.is_object(),
        "invalid agent usage: expected a usage object"
    );
    if observed {
        for field in ["input_tokens", "output_tokens", "cached_input_tokens"] {
            ensure!(
                usage.get(field).is_some(),
                "invalid agent usage: missing {field}"
            );
        }
    }
    let input = counter(usage, "input_tokens")?;
    let cached = counter(usage, "cached_input_tokens")?;
    ensure!(
        cached <= input,
        "invalid agent usage: cached input exceeds total input"
    );
    Ok([
        input - cached,
        counter(usage, "output_tokens")?,
        cached,
        counter(usage, "cache_write_input_tokens")?,
    ])
}
fn usage_id<'a>(payload: &'a Value, field: &str) -> Result<&'a str> {
    payload[field]
        .as_str()
        .filter(|id| !id.trim().is_empty())
        .ok_or_else(|| anyhow!("invalid agent usage: {field} must be a nonempty string"))
}
struct CodexReceipt {
    at: DateTime<Utc>,
    turn: String,
    model: String,
    row: Option<[i64; 4]>,
}
struct CodexObservedUsage {
    stream: usize,
    position: u64,
    context: usize,
    turn: String,
    row: Result<[i64; 4]>,
}
struct CodexCounter {
    at: DateTime<Utc>,
    thread: Option<String>,
    revision: Option<String>,
    model: String,
    key: String,
    snapshot: String,
    cumulative: Option<Result<[i64; 4]>>,
    stream: usize,
    position: u64,
    inherited: bool,
    scoped: bool,
    complete: bool,
    row: Result<[i64; 4]>,
    epoch: usize,
    reset: bool,
    context: usize,
    covered: Result<bool>,
}
fn cumulative(counter: &CodexCounter) -> Option<[i64; 4]> {
    counter
        .cumulative
        .as_ref()
        .and_then(|counts| counts.as_ref().ok().copied())
}
struct CodexRollout {
    id: String,
    base: Value,
    positions: BTreeMap<u64, u64>,
    scoped: bool,
}
fn codex_revision(metadata: &Value) -> String {
    // Revert preserves the thread ID while replacing its physical history.
    hash_json(&json!([
        metadata["id"],
        metadata["timestamp"],
        metadata["history_base"]
    ]))
}
fn rollout_id(path: &Path, metadata: &Value) -> String {
    let plain = if compressed(path) {
        path.with_extension("")
    } else {
        path.to_owned()
    };
    let path = plain.as_path();
    let name = path.file_stem().unwrap_or_default().to_string_lossy();
    if let Some(id) = name.get(name.len().saturating_sub(36)..)
        && id.len() == 36
        && id.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
    {
        return id.to_owned();
    }
    if metadata["history_base"].is_null() {
        text(&metadata["id"]).to_owned()
    } else {
        path.to_string_lossy().into_owned()
    }
}
fn validate_codex_lineage(rollouts: &[CodexRollout]) -> Result<()> {
    for start in rollouts.iter().filter(|rollout| rollout.scoped) {
        let mut rollout = start;
        let mut seen = BTreeSet::new();
        while !rollout.base.is_null() {
            ensure!(
                seen.insert(rollout.id.as_str()),
                "incomplete agent usage: cyclic history references"
            );
            let id = usage_id(&rollout.base, "thread_id")?;
            let end = rollout.base["end_ordinal_exclusive"]
                .as_u64()
                .filter(|n| *n > 0)
                .ok_or_else(|| anyhow!("invalid agent usage: history prefix ordinal"))?;
            let offset = rollout.base["end_byte_offset"]
                .as_u64()
                .filter(|n| *n > 0)
                .ok_or_else(|| anyhow!("invalid agent usage: history prefix byte offset"))?;
            rollout = rollouts
                .iter()
                .find(|source| source.id == id && source.positions.get(&(end - 1)) == Some(&offset))
                .ok_or_else(|| {
                    anyhow!("incomplete agent usage: history prefix is missing or truncated")
                })?;
        }
    }
    Ok(())
}
fn observation_key(counter: &CodexCounter) -> String {
    if let Some(counts) = cumulative(counter) {
        if counter.thread.is_some() {
            hash_json(&json!([counter.revision, counter.epoch, counts]))
        } else {
            hash_json(&json!([counter.stream, counter.epoch, counts]))
        }
    } else {
        hash_json(&json!([counter.key, counter.epoch]))
    }
}
fn mirrored(counter: &CodexCounter, covered: &BTreeSet<String>) -> bool {
    counter.covered.as_ref().is_ok_and(|covered| *covered)
        || covered.contains(&observation_key(counter))
}
fn counter_coverage(
    counter: &CodexCounter,
    previous: Option<[i64; 4]>,
    pending: &[&CodexObservedUsage],
) -> (Result<bool>, usize) {
    if pending.is_empty() {
        return (Ok(false), 0);
    }
    let result = (|| -> Result<(bool, usize)> {
        let counts = cumulative(counter).ok_or_else(|| {
            anyhow!("incomplete agent usage: mixed formats require cumulative counters")
        })?;
        let previous = previous.unwrap_or_default();
        let mut delta = [0; 4];
        for i in 0..4 {
            delta[i] = counts[i]
                .checked_sub(previous[i])
                .filter(|value| *value >= 0)
                .ok_or_else(|| {
                    anyhow!(
                        "incomplete agent usage: cumulative counters decreased within a rollout"
                    )
                })?;
        }
        let last = counter.row.as_ref().ok().copied();
        let mut credit = [0; 4];
        let mut residual = None;
        for (i, receipt) in pending.iter().enumerate() {
            add_counts(
                &mut credit,
                receipt.row.as_ref().map_err(|error| anyhow!("{error}"))?,
            )?;
            if credit == delta {
                ensure!(
                    receipt.context == counter.context || last.is_some_and(|last| last != delta),
                    "incomplete agent usage: ambiguous response coverage across contexts"
                );
                return Ok((true, i + 1));
            }
            if last.is_some_and(|last| {
                last.iter().any(|count| *count != 0)
                    && (0..4).all(|j| delta[j].checked_sub(credit[j]) == Some(last[j]))
            }) {
                residual = Some(i + 1);
            }
        }
        if let Some(consumed) = residual {
            return Ok((false, consumed));
        }
        ensure!(
            last == Some(delta),
            "incomplete agent usage: legacy counters do not reconcile with response receipts"
        );
        Ok((false, pending.len()))
    })();
    match result {
        Ok((covered, consumed)) => (Ok(covered), consumed),
        Err(error) => (Err(error), pending.len()),
    }
}
fn assign_coverage(
    counters: &mut [CodexCounter],
    observed: &[CodexObservedUsage],
    streams: &[Vec<usize>],
    baselines: &[Option<[i64; 4]>],
) -> BTreeSet<String> {
    let mut covered = BTreeSet::new();
    let mut by_stream = vec![Vec::new(); streams.len()];
    for usage in observed {
        by_stream[usage.stream].push(usage);
    }
    for (stream, indices) in streams.iter().enumerate() {
        let mut receipts = by_stream[stream].iter().copied().peekable();
        let mut pending = Vec::new();
        let mut previous = baselines[stream];
        let mut seen = BTreeSet::new();
        for index in indices {
            let counter = &mut counters[*index];
            while receipts
                .peek()
                .is_some_and(|usage| usage.position < counter.position)
            {
                if let Some(usage) = receipts.next()
                    && !usage
                        .row
                        .as_ref()
                        .is_ok_and(|row| row.iter().all(|count| *count == 0))
                {
                    pending.push(usage);
                }
            }
            if counter.reset {
                if previous != Some([0; 4]) {
                    pending.clear();
                }
                previous = Some([0; 4]);
                seen.clear();
                continue;
            }
            let key = observation_key(counter);
            if !seen.insert(key.clone()) {
                continue;
            }
            if counter.inherited {
                previous = cumulative(counter).or(previous);
                continue;
            }
            let (result, consumed) = counter_coverage(counter, previous, &pending);
            pending.drain(..consumed);
            if result.as_ref().is_ok_and(|covered| *covered) {
                covered.insert(key);
            }
            counter.covered = result;
            previous = cumulative(counter).or(previous);
        }
    }
    covered
}
fn prefix_counter<'a>(
    mut stream: usize,
    rollouts: &[CodexRollout],
    counters: &'a [CodexCounter],
    streams: &[Vec<usize>],
) -> Option<&'a CodexCounter> {
    let mut seen = BTreeSet::new();
    loop {
        let base = &rollouts[stream].base;
        let id = base["thread_id"].as_str()?;
        let end = base["end_ordinal_exclusive"].as_u64()?.checked_sub(1)?;
        let offset = base["end_byte_offset"].as_u64()?;
        stream = rollouts
            .iter()
            .position(|source| source.id == id && source.positions.get(&end) == Some(&offset))?;
        if !seen.insert(stream) {
            return None;
        }
        let count = streams[stream].partition_point(|index| counters[*index].position <= offset);
        if let Some(index) = count.checked_sub(1) {
            return Some(&counters[streams[stream][index]]);
        }
    }
}
fn codex(root: &Path, since: DateTime<Utc>, until: DateTime<Utc>) -> Result<Totals> {
    let window = since..=until;
    let home = std::env::var("CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| orgs::expand_home("~/.codex"));
    let mut totals = Totals::new();
    let mut receipts: BTreeMap<(String, String), CodexReceipt> = BTreeMap::new();
    let mut copied = BTreeMap::new();
    let mut observed_usage: Vec<CodexObservedUsage> = Vec::new();
    let mut counters = Vec::new();
    let mut rollouts = Vec::new();
    for directory in ["sessions", "archived_sessions"] {
        let base = home.join(directory);
        match base.metadata() {
            Ok(metadata) => ensure!(
                metadata.is_dir(),
                "Codex {directory} usage path must be a directory"
            ),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => bail!("could not inspect Codex {directory} usage directory: {error}"),
        }
        for entry in walkdir::WalkDir::new(base).sort_by_file_name() {
            let path = entry
                .map_err(|error| anyhow!("could not enumerate Codex usage transcripts: {error}"))?
                .into_path();
            // Revert rewrites wrapper timestamps. Old originals establish receipt age.
            if !(path.extension().is_some_and(|e| e == "jsonl") || compressed(&path))
                || modified(&path)?.is_none()
            {
                continue;
            }
            let mut rows = indexed_lines(&path)?;
            let mut metadata = None;
            for row in rows.by_ref() {
                let (line, offset) = row?;
                if let Some(record) = transcript_record(&line)? {
                    codex_metadata(&record)?;
                    metadata = Some((record, offset));
                    break;
                }
            }
            let Some((metadata, header_offset)) = metadata else {
                continue;
            };
            let header_scoped = inside(text(&metadata["payload"]["cwd"]), root);
            let thread_id = match metadata["payload"].get("id") {
                None | Some(Value::Null) => None,
                Some(Value::String(id)) if !id.trim().is_empty() => Some(id.as_str()),
                _ if header_scoped => {
                    bail!("invalid agent usage: session ID must be a nonempty string")
                }
                _ => None,
            };
            let mut model = "codex".to_owned();
            let mut scoped = header_scoped;
            let mut relevant = header_scoped;
            let parent = metadata["payload"]["forked_from_id"]
                .as_str()
                .filter(|id| !id.is_empty());
            let mut copied_history =
                parent.is_some() && metadata["payload"]["history_base"].is_null();
            let mut inherited = copied_history;
            let mut source_thread = if inherited { parent } else { thread_id }.map(str::to_owned);
            let revision = codex_revision(&metadata["payload"]);
            let mut source_revision = (!inherited).then(|| revision.clone());
            let mut revisions = BTreeMap::new();
            if let Some(thread) = thread_id {
                revisions.insert(thread.to_owned(), (revision.clone(), header_scoped));
            }
            let boundary = metadata["payload"]["subagent_history_start_ordinal"].as_u64();
            let bad_boundary = metadata["payload"]
                .get("subagent_history_start_ordinal")
                .is_some_and(|value| !value.is_null() && value.as_u64().is_none());
            let mut owner_marker = false;
            let mut invalid_owner = false;
            let mut ambiguous_copy = false;
            let mut turn_id = None;
            let mut positions = BTreeMap::new();
            if let Some(ordinal) = metadata["ordinal"].as_u64() {
                positions.insert(ordinal, header_offset);
            }
            let mut contexts = BTreeMap::new();
            let mut observed_ids: BTreeMap<String, usize> = BTreeMap::new();
            let mut context_version = 0;
            let mut epoch = 0;
            let mut in_reset = false;
            let mut file_counters = Vec::new();
            let mut inherited_snapshots = BTreeMap::new();
            for line in rows {
                let (line, offset) = line?;
                let Some(e) = transcript_record(&line)? else {
                    continue;
                };
                if text(&e["type"]) == "event_msg" {
                    ensure!(
                        e["payload"]["type"]
                            .as_str()
                            .is_some_and(|kind| !kind.trim().is_empty()),
                        "invalid agent usage event type"
                    );
                }
                if ["event_msg", "turn_context", "token_usage_record"].contains(&text(&e["type"])) {
                    ensure!(
                        e["payload"].is_object(),
                        "invalid agent usage record payload: expected an object"
                    );
                }
                if let Some(ordinal) = e["ordinal"].as_u64() {
                    positions.insert(ordinal, offset);
                    if boundary.is_some_and(|start| ordinal >= start) {
                        inherited = false;
                        source_thread = thread_id.map(str::to_owned);
                        source_revision = Some(revision.clone());
                        owner_marker = true;
                    }
                }
                let p = &e["payload"];
                if text(&e["type"]) == "session_meta" {
                    codex_metadata(&e)?;
                    if p == &metadata["payload"] {
                        continue;
                    }
                    // Copied prefixes retain metadata and owner-tagged settings.
                    copied_history = true;
                    inherited = true;
                    source_thread = p["id"]
                        .as_str()
                        .filter(|id| !id.is_empty())
                        .map(str::to_owned);
                    ambiguous_copy |= source_thread.as_deref() == thread_id;
                    let inherited_revision = codex_revision(p);
                    scoped = inside(text(&p["cwd"]), root);
                    relevant |= scoped;
                    if let Some(thread) = &source_thread {
                        revisions.insert(thread.clone(), (inherited_revision.clone(), scoped));
                    }
                    source_revision = Some(inherited_revision);
                    continue;
                }
                if text(&e["type"]) == "event_msg" && text(&p["type"]) == "thread_settings_applied"
                {
                    context_version += 1;
                    invalid_owner |= p
                        .get("thread_id")
                        .is_some_and(|owner| !owner.is_null() && usage_id(p, "thread_id").is_err());
                    if let Some(owner) = p["thread_id"].as_str().filter(|id| !id.is_empty()) {
                        inherited = Some(owner) != thread_id;
                        source_thread = Some(owner.to_owned());
                        source_revision = if inherited {
                            revisions.get(owner).map(|(revision, _)| revision.clone())
                        } else {
                            Some(revision.clone())
                        };
                        if let Some((_, owner_scoped)) = revisions.get(owner) {
                            scoped = *owner_scoped;
                        }
                        owner_marker |= !inherited;
                    }
                    if let Some(cwd) = p["thread_settings"]["cwd"].as_str() {
                        scoped = inside(cwd, root);
                    }
                    if let Some(selected) = p["thread_settings"]["model"]
                        .as_str()
                        .filter(|model| !model.is_empty())
                    {
                        model = selected.to_owned();
                    }
                    relevant |= scoped;
                    continue;
                }
                if text(&e["type"]) == "turn_context" {
                    context_version += 1;
                    if p.get("cwd").is_some() {
                        ensure!(
                            p["cwd"].as_str().is_some_and(|cwd| !cwd.trim().is_empty()),
                            "invalid agent usage workspace"
                        );
                    }
                    if let Some(m) = p["model"].as_str()
                        && !m.is_empty()
                    {
                        model = m.into();
                    }
                    if let Some(cwd) = p["cwd"].as_str() {
                        scoped = inside(cwd, root);
                    }
                    relevant |= scoped;
                    turn_id = p["turn_id"].as_str().map(str::to_owned);
                    if let Some(turn) = p["turn_id"].as_str() {
                        contexts.insert(turn.to_owned(), (model.clone(), scoped));
                    }
                    continue;
                }
                if text(&e["type"]) == "token_usage_record" {
                    let context = p["turn_id"].as_str().and_then(|turn| contexts.get(turn));
                    let (record_model, record_scoped) = context
                        .map(|(model, scoped)| (model.as_str(), *scoped))
                        .unwrap_or((&model, scoped));
                    let owner = p["thread_id"].as_str().filter(|id| !id.trim().is_empty());
                    if record_scoped {
                        usage_id(p, "thread_id")?;
                        usage_id(p, "turn_id")?;
                        usage_id(p, "response_id")?;
                    }
                    let (Some(owner), Some(response)) = (
                        owner,
                        p["response_id"].as_str().filter(|id| !id.trim().is_empty()),
                    ) else {
                        continue;
                    };
                    let key = (owner.to_owned(), response.to_owned());
                    if Some(owner) != thread_id {
                        if record_scoped {
                            let receipt = (
                                usage_id(p, "turn_id")?.to_owned(),
                                codex_counts(&p["usage"], true)?,
                            );
                            if let Some(known) = copied.insert(key, receipt.clone()) {
                                ensure!(
                                    known == receipt,
                                    "conflicting agent usage in copied responses"
                                );
                            }
                        }
                        continue;
                    }
                    let usage = codex_counts(&p["usage"], true);
                    let identity = hash_json(&json!([owner, response]));
                    if let Some(index) = observed_ids.get(&identity) {
                        let known = &mut observed_usage[*index];
                        if known.turn != text(&p["turn_id"])
                            || known.row.as_ref().ok() != usage.as_ref().ok()
                        {
                            known.row =
                                Err(anyhow!("conflicting agent usage in duplicate responses"));
                        }
                    } else {
                        observed_ids.insert(identity, observed_usage.len());
                        observed_usage.push(CodexObservedUsage {
                            stream: rollouts.len(),
                            position: offset,
                            context: context_version,
                            turn: text(&p["turn_id"]).to_owned(),
                            row: usage,
                        });
                    }
                    let at = parse(text(&e["timestamp"]));
                    if record_scoped {
                        ensure!(
                            at.is_some(),
                            "invalid agent usage: response timestamp is missing or invalid"
                        );
                    }
                    let Some(at) = at else {
                        continue;
                    };
                    let row = record_scoped
                        .then(|| codex_counts(&p["usage"], true))
                        .transpose()?;
                    if let Some(known) = receipts.get_mut(&key) {
                        if known.row.is_some() || record_scoped {
                            ensure!(
                                known.row == row && known.turn == text(&p["turn_id"]),
                                "conflicting agent usage in duplicate responses"
                            );
                        }
                        if at < known.at {
                            known.at = at;
                            known.model = record_model.to_owned();
                        }
                    } else {
                        receipts.insert(
                            key,
                            CodexReceipt {
                                at,
                                turn: text(&p["turn_id"]).to_owned(),
                                model: record_model.to_owned(),
                                row,
                            },
                        );
                    }
                    continue;
                }
                if text(&e["type"]) != "event_msg" || text(&p["type"]) != "token_count" {
                    continue;
                }
                let total = &p["info"]["total_token_usage"];
                let cumulative = if total.is_null()
                    || (total.is_object()
                        && ![
                            "input_tokens",
                            "output_tokens",
                            "cached_input_tokens",
                            "cache_write_input_tokens",
                        ]
                        .iter()
                        .any(|field| total.get(field).is_some()))
                {
                    None
                } else {
                    Some(codex_counts(total, false).map_err(|error| {
                        anyhow!("invalid cumulative agent usage or token total overflow: {error}")
                    }))
                };
                let snapshot = hash_json(total);
                let reset = cumulative
                    .as_ref()
                    .is_some_and(|counts| counts.as_ref().is_ok_and(|counts| *counts == [0; 4]))
                    && codex_counts(&p["info"]["last_token_usage"], false)
                        .is_ok_and(|counts| counts == [0; 4])
                    && p["info"]["model_context_window"]
                        .as_i64()
                        .is_some_and(|limit| {
                            limit > 0 && total["total_tokens"].as_i64() == Some(limit)
                        });
                let reset_boundary = reset && !in_reset;
                if reset_boundary {
                    epoch += 1;
                    inherited_snapshots.clear();
                }
                if reset {
                    in_reset = true;
                } else if cumulative.as_ref().is_some_and(|counts| {
                    counts
                        .as_ref()
                        .is_ok_and(|counts| counts.iter().any(|count| *count != 0))
                }) {
                    in_reset = false;
                }
                let identity = cumulative
                    .as_ref()
                    .and_then(|counts| counts.as_ref().ok())
                    .map(|counts| hash_json(&json!(counts)))
                    .unwrap_or_else(|| snapshot.clone());
                if inherited {
                    inherited_snapshots.insert(
                        identity.clone(),
                        (
                            source_thread.clone(),
                            source_revision.clone(),
                            snapshot.clone(),
                        ),
                    );
                }
                let seed = (!inherited)
                    .then(|| inherited_snapshots.get(&identity))
                    .flatten();
                let turn = &p["info"]["last_token_usage"];
                if turn.is_null() || (turn.is_object() && !truth(turn)) {
                    continue;
                }
                let observed = parse(text(&e["timestamp"]));
                ensure!(
                    !scoped || observed.is_some(),
                    "invalid agent usage timestamp"
                );
                let Some(observed) = observed else {
                    continue;
                };
                let key = hash_json(&json!([
                    revision,
                    turn_id,
                    observed.to_rfc3339(),
                    p["info"]["total_token_usage"]
                ]));
                file_counters.push(CodexCounter {
                    at: observed,
                    thread: seed.map_or_else(|| source_thread.clone(), |seed| seed.0.clone()),
                    revision: seed.map_or_else(|| source_revision.clone(), |seed| seed.1.clone()),
                    model: model.clone(),
                    key,
                    snapshot: seed.map_or(snapshot, |seed| seed.2.clone()),
                    cumulative,
                    stream: rollouts.len(),
                    position: offset,
                    inherited: inherited || seed.is_some(),
                    scoped,
                    complete: true,
                    row: codex_counts(turn, false),
                    epoch,
                    reset: reset_boundary,
                    context: context_version,
                    covered: Ok(false),
                });
            }
            if relevant {
                ensure!(
                    !invalid_owner,
                    "invalid agent usage: invalid history owner ID"
                );
                for field in ["id", "forked_from_id"] {
                    if metadata["payload"]
                        .get(field)
                        .is_some_and(|value| !value.is_null())
                    {
                        usage_id(&metadata["payload"], field)?;
                    }
                }
            }
            for mut counter in file_counters {
                counter.complete = !bad_boundary
                    && (!copied_history || owner_marker)
                    && (!ambiguous_copy || boundary.is_some());
                counters.push(counter);
            }
            rollouts.push(CodexRollout {
                id: rollout_id(&path, &metadata["payload"]),
                base: metadata["payload"]["history_base"].clone(),
                positions,
                scoped: relevant,
            });
        }
    }
    validate_codex_lineage(&rollouts)?;
    let mut streams = vec![Vec::new(); rollouts.len()];
    for (index, counter) in counters.iter().enumerate() {
        streams[counter.stream].push(index);
    }
    // Referenced history can seed a new rollout with an already billed snapshot.
    let mut seeds = Vec::new();
    for stream in 0..rollouts.len() {
        let Some(mut source) = prefix_counter(stream, &rollouts, &counters, &streams) else {
            continue;
        };
        let mut seen = BTreeSet::new();
        while seen.insert(source.stream) {
            let Some(parent) = prefix_counter(source.stream, &rollouts, &counters, &streams) else {
                break;
            };
            if cumulative(parent).is_none() || cumulative(parent) != cumulative(source) {
                break;
            }
            source = parent;
        }
        if let Some(counts) = cumulative(source) {
            seeds.push((
                stream,
                counts,
                source.thread.clone(),
                source.revision.clone(),
                source.snapshot.clone(),
            ));
        }
    }
    let mut previous = vec![None; rollouts.len()];
    for (stream, counts, thread, revision, snapshot) in seeds {
        previous[stream] = Some(counts);
        for index in &streams[stream] {
            let counter = &mut counters[*index];
            if counter.epoch == 0 && cumulative(counter) == Some(counts) {
                counter.inherited = true;
                counter.thread.clone_from(&thread);
                counter.revision.clone_from(&revision);
                counter.snapshot.clone_from(&snapshot);
            }
        }
    }
    let covered = assign_coverage(&mut counters, &observed_usage, &streams, &previous);
    let mut regressed = vec![false; rollouts.len()];
    let mut known_snapshots = vec![BTreeSet::new(); rollouts.len()];
    for counter in &counters {
        if counter.reset {
            previous[counter.stream] = Some([0; 4]);
        }
        if let Some(counts) = cumulative(counter) {
            if !known_snapshots[counter.stream].insert((counter.epoch, counts)) {
                continue;
            }
            regressed[counter.stream] |= previous[counter.stream].is_some_and(|known: [i64; 4]| {
                counts
                    .iter()
                    .zip(known)
                    .any(|(current, before)| *current < before)
            });
            previous[counter.stream] = Some(counts);
        }
    }
    ensure!(
        !counters.iter().any(|counter| counter.scoped
            && !counter.inherited
            && window.contains(&counter.at)
            && !mirrored(counter, &covered)
            && regressed[counter.stream]),
        "incomplete agent usage: cumulative counters decreased within a rollout"
    );
    for (key, (turn, row)) in copied {
        let original = receipts.get(&key).ok_or_else(|| {
            anyhow!("incomplete agent usage: copied response has no original receipt")
        })?;
        ensure!(
            original.turn == turn && original.row.is_none_or(|known| known == row),
            "conflicting agent usage in copied responses"
        );
    }
    for receipt in receipts
        .into_values()
        .filter(|receipt| window.contains(&receipt.at))
    {
        if let Some(row) = receipt.row {
            add_counts(totals.entry(receipt.model).or_default(), &row)?;
        }
    }
    let mut originals: BTreeMap<_, Vec<&CodexCounter>> = BTreeMap::new();
    for counter in counters
        .iter()
        .filter(|counter| !counter.inherited && counter.complete)
    {
        originals
            .entry((&counter.thread, &counter.snapshot))
            .or_default()
            .push(counter);
    }
    for counter in counters
        .iter()
        .filter(|counter| counter.scoped && counter.inherited)
    {
        ensure!(
            counter.complete && counter.thread.is_some(),
            "incomplete agent usage: copied history has no reliable ownership boundary"
        );
        let source = originals
            .get(&(&counter.thread, &counter.snapshot))
            .ok_or_else(|| {
                anyhow!("incomplete agent usage: copied counter has no original observation")
            })?;
        let source: Vec<_> = source
            .iter()
            .filter(|original| {
                counter
                    .revision
                    .as_ref()
                    .is_none_or(|revision| original.revision.as_ref() == Some(revision))
            })
            .collect();
        ensure!(
            !source.is_empty(),
            "incomplete agent usage: copied counter has no original revision"
        );
        if !source
            .iter()
            .any(|original| window.contains(&original.at) && !mirrored(original, &covered))
        {
            continue;
        }
        let row = counter.row.as_ref().map_err(|error| anyhow!("{error}"))?;
        if row.iter().all(|count| *count == 0) {
            continue;
        }
        ensure!(
            source
                .iter()
                .any(|original| original.row.as_ref().is_ok_and(|known| known == row)),
            "conflicting agent usage in copied counters"
        );
    }
    // Rate-limit and context updates repeat totals without consuming more tokens.
    let mut observations: BTreeMap<String, Vec<&CodexCounter>> = BTreeMap::new();
    for counter in counters
        .iter()
        .filter(|counter| !counter.inherited && !mirrored(counter, &covered))
    {
        let key = observation_key(counter);
        observations.entry(key).or_default().push(counter);
    }
    let mut seen = BTreeMap::new();
    for observations in observations.into_values() {
        if !observations
            .iter()
            .any(|counter| counter.scoped && window.contains(&counter.at))
        {
            continue;
        }
        let mut original: Option<(&CodexCounter, [i64; 4])> = None;
        for counter in observations {
            ensure!(
                counter.complete,
                "incomplete agent usage: copied history has no reliable ownership boundary"
            );
            let row = *counter.row.as_ref().map_err(|error| anyhow!("{error}"))?;
            counter
                .covered
                .as_ref()
                .map_err(|error| anyhow!("{error}"))?;
            if let Some(counts) = &counter.cumulative {
                counts.as_ref().map_err(|error| anyhow!("{error}"))?;
            }
            if row.iter().all(|count| *count == 0) {
                continue;
            }
            if let Some((known, counts)) = original {
                ensure!(counts == row, "conflicting agent usage in duplicate events");
                if counter.at < known.at {
                    original = Some((counter, row));
                }
            } else {
                original = Some((counter, row));
            }
        }
        let Some((counter, row)) =
            original.filter(|(counter, _)| counter.scoped && window.contains(&counter.at))
        else {
            continue;
        };
        if let Some(known) = seen.insert((&counter.key, counter.epoch), row) {
            ensure!(known == row, "conflicting agent usage in duplicate events");
            continue;
        }
        add_counts(totals.entry(counter.model.clone()).or_default(), &row)?;
    }
    Ok(totals)
}
pub fn budget(h: &Path, org: &str) -> Result<Value> {
    let data = orgs::load(h, org, &orgs::providers()?)?;
    if let Some(problem) = orgs::budget_problem(&data) {
        bail!(problem);
    }
    Ok(data["agent_token_budget"].clone())
}
pub fn set_budget(h: &Path, org: &str, tokens: u64, days: u64) -> Result<Value> {
    let registry = orgs::providers()?;
    let mut data = orgs::load(h, org, &registry)?;
    if tokens == 0 {
        data.as_object_mut()
            .ok_or_else(|| anyhow!("invalid org"))?
            .remove("agent_token_budget");
    } else {
        data["agent_token_budget"] = json!({"tokens":tokens,"days":days});
    }
    orgs::save(h, &data, &registry)?;
    Ok(json!([{"org":org,"agent_token_budget":data["agent_token_budget"]}]))
}
pub fn report(h: &Path, org: &str, days: i64) -> Result<Value> {
    let until = Utc::now();
    let since = until - chrono::Duration::days(days);
    let root = orgs::workspace(h, org)?;
    let path = local_path(h, "prices.json")?;
    let table = if path.try_exists()? {
        read_json(&path)?
    } else {
        json!({})
    };
    ensure!(
        table.is_object()
            && object(&table).values().all(|v| v.is_object()
                && FIELDS.iter().all(|f| v
                    .get(*f)
                    .is_none_or(|rate| rate.as_f64().is_some_and(|n| n.is_finite() && n >= 0.0)))),
        "{} must map each model to nonnegative finite USD per million tokens for {}",
        path.display(),
        FIELDS.join(", ")
    );
    let (mut models, mut unpriced, mut tokens, mut cost) = (vec![], BTreeSet::new(), 0i64, 0.0f64);
    let mut priced = false;
    for (runtime, totals) in [
        ("claude", claude(&root, since, until)?),
        ("codex", codex(&root, since, until)?),
    ] {
        for (model, row) in totals {
            let rate = table.get(&model);
            let usd = rate
                .map(|rate| -> Result<f64> {
                    let usd = (row
                        .iter()
                        .enumerate()
                        .map(|(i, n)| *n as f64 * rate[FIELDS[i]].as_f64().unwrap_or(0.0))
                        .sum::<f64>()
                        / 1e6
                        * 100.0)
                        .round()
                        / 100.0;
                    ensure!(
                        usd.is_finite(),
                        "agent usage cost estimate overflow; check prices.json"
                    );
                    Ok(usd)
                })
                .transpose()?;
            if let Some(usd) = usd {
                priced = true;
                cost += usd;
            } else {
                unpriced.insert(model.clone());
            }
            tokens = row.iter().try_fold(tokens, |sum, n| {
                sum.checked_add(*n)
                    .ok_or_else(|| anyhow!("agent usage token total overflow"))
            })?;
            let mut r = json!({"runtime":runtime,"model":model,"usd":usd});
            for (i, f) in FIELDS.iter().enumerate() {
                r[*f] = json!(row[i]);
            }
            models.push(r);
        }
    }
    ensure!(
        (cost * 100.0).is_finite(),
        "agent usage cost estimate overflow; check prices.json"
    );
    Ok(
        json!({"org":org,"days":days,"tokens":tokens,"models":models,"budget":budget(h,org).unwrap_or(Value::Null),"usd":if priced&&unpriced.is_empty(){Some((cost*100.0).round()/100.0)}else{None},"unpriced_models":unpriced}),
    )
}
pub fn over(h: &Path, org: &str) -> Result<Option<String>> {
    let limit = budget(h, org)?;
    if !truth(&limit) {
        return Ok(None);
    }
    let report = report(h, org, limit["days"].as_i64().unwrap_or(30))?;
    let used = report["tokens"].as_u64().unwrap_or(0);
    Ok((used > limit["tokens"].as_u64().unwrap_or(0)).then(|| {
        format!(
            "{org} has used {used} agent tokens in {} days, over its budget of {}",
            limit["days"], limit["tokens"]
        )
    }))
}
