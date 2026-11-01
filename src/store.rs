use crate::*;
use chrono::{DateTime, SecondsFormat, Utc};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::Component,
};

pub const SCHEMA: u64 = 1;
pub fn now() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Micros, true)
}
pub fn instant(value: &str) -> Result<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|v| v.with_timezone(&Utc))
        .map_err(|_| anyhow!("times need ISO 8601 with an explicit timezone (Z or +HH:MM)"))
}
pub fn window(start: &str, end: &str) -> Result<Value> {
    let (a, b) = (instant(start)?, instant(end)?);
    let duration = b.signed_duration_since(a).num_milliseconds();
    ensure!(
        duration > 0 && duration <= 31 * 86400 * 1000,
        "window must be positive and at most 31 days"
    );
    Ok(
        json!({"from":a.to_rfc3339_opts(SecondsFormat::AutoSi,true),"to":b.to_rfc3339_opts(SecondsFormat::AutoSi,true)}),
    )
}
pub fn identifier(value: &str) -> Result<&str> {
    ensure!(
        !value.contains("..")
            && regex::Regex::new(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")?.is_match(value),
        "invalid identifier"
    );
    Ok(value)
}
pub fn redirected(path: &Path) -> bool {
    let Ok(metadata) = path.symlink_metadata() else {
        return false;
    };
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}
pub fn local_path(root: &Path, parts: impl AsRef<Path>) -> Result<PathBuf> {
    let mut path = root.to_owned();
    for component in parts.as_ref().components() {
        match component {
            Component::Normal(p) => path.push(p),
            Component::CurDir => continue,
            _ => bail!("local state paths must stay inside their root"),
        }
        ensure!(
            !redirected(&path),
            "local state paths must not contain symlinks or junctions"
        );
    }
    Ok(path)
}
pub fn private_dir(path: &Path) -> Result<()> {
    ensure!(
        !redirected(path),
        "local state directories must not be symlinks"
    );
    if !path.exists() {
        if let Some(parent) = path.parent() {
            ensure!(
                !redirected(parent),
                "local state directories must not be symlinks"
            );
            // Existing parents can be shared system directories such as /tmp.
            if !parent.exists() {
                private_dir(parent)?;
            }
        }
        let mut builder = fs::DirBuilder::new();
        builder.recursive(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(path).or_else(|e| {
            if path.is_dir() && !redirected(path) {
                Ok(())
            } else {
                Err(e)
            }
        })?;
    }
    ensure!(
        path.is_dir() && !redirected(path),
        "local state directory must be a directory without symlinks"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
pub fn write(path: &Path, content: impl AsRef<[u8]>) -> Result<()> {
    let parent = path.parent().ok_or_else(|| anyhow!("missing parent"))?;
    local_path(
        parent,
        Path::new(
            path.file_name()
                .ok_or_else(|| anyhow!("missing filename"))?,
        ),
    )?;
    private_dir(parent)?;
    let mut temp = tempfile::Builder::new()
        .prefix(".write-")
        .tempfile_in(parent)?;
    temp.write_all(content.as_ref())?;
    temp.as_file().sync_all()?;
    temp.persist(path).map_err(|e| e.error)?;
    Ok(())
}
pub(crate) fn json_content(value: &Value) -> Result<String> {
    let content = format!("{}\n", serde_json::to_string_pretty(value)?);
    ensure!(
        content.len() <= 8 * 1048576,
        "local JSON state exceeds its readable size limit"
    );
    // Serialization permits deeper values than our reader can reopen.
    serde_json::from_str::<Value>(&content)
        .map_err(|_| anyhow!("local JSON state exceeds its readable nesting limit"))?;
    Ok(content)
}
pub fn write_json(path: &Path, value: &Value) -> Result<()> {
    write(path, json_content(value)?)
}
pub fn append_file(path: &Path) -> Result<File> {
    let parent = path.parent().ok_or_else(|| anyhow!("missing parent"))?;
    local_path(
        parent,
        Path::new(
            path.file_name()
                .ok_or_else(|| anyhow!("missing filename"))?,
        ),
    )?;
    private_dir(parent)?;
    let mut opts = OpenOptions::new();
    // Windows locks need read access as well as an append-only handle.
    opts.read(true).append(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = opts.open(path)?;
    ensure!(
        file.metadata()?.is_file(),
        "local log must be a private regular file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        // Check the link count before changing permissions or writing through this inode.
        ensure!(
            file.metadata()?.nlink() == 1,
            "local log must be a private regular file"
        );
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    Ok(file)
}
pub fn append_json(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    let mut file = append_file(path)?;
    // Separate watches and CLI processes share these logs.
    file.lock()?;
    file.write_all(&bytes)?;
    Ok(())
}
fn open_read(path: &Path) -> Result<File> {
    local_path(
        path.parent().ok_or_else(|| anyhow!("missing parent"))?,
        Path::new(
            path.file_name()
                .ok_or_else(|| anyhow!("missing filename"))?,
        ),
    )?;
    let mut opts = OpenOptions::new();
    opts.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    Ok(opts.open(path)?)
}
pub fn read_bytes(path: &Path, maximum: usize) -> Result<Vec<u8>> {
    let file = open_read(path)?;
    let info = file.metadata()?;
    ensure!(
        info.is_file() && info.len() <= maximum as u64,
        "local state must be a regular file within its size limit"
    );
    let mut bytes = Vec::new();
    file.take(maximum as u64 + 1).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= maximum,
        "local state file exceeds its size limit"
    );
    Ok(bytes)
}
pub fn read_json(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(&read_bytes(path, 8 * 1048576)?)?)
}
pub fn read_text(path: &Path) -> Result<String> {
    Ok(String::from_utf8(read_bytes(path, 8 * 1048576)?)?)
}
pub fn json_lines(path: &Path) -> Result<impl Iterator<Item = Value>> {
    use std::io::{BufRead, BufReader};
    let file = open_read(path)?;
    ensure!(
        file.metadata()?.is_file(),
        "local log must be a regular file"
    );
    Ok(BufReader::new(file)
        .split(b'\n')
        .map_while(std::result::Result::ok)
        .filter_map(|line| serde_json::from_slice(&line).ok()))
}
pub fn pick<'a>(data: &'a Value, path: &str) -> &'a Value {
    let mut item = data;
    for part in path.split('.').filter(|p| !p.is_empty()) {
        item = if item.is_array() {
            part.parse::<usize>()
                .ok()
                .and_then(|i| item.get(i))
                .unwrap_or(&Value::Null)
        } else {
            &item[part]
        };
    }
    item
}
pub fn sha256(bytes: impl AsRef<[u8]>) -> String {
    Sha256::digest(bytes.as_ref())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
pub fn file_sha256(path: &Path) -> Result<String> {
    let file = open_read(path)?;
    let metadata = file.metadata()?;
    ensure!(metadata.is_file(), "configuration must be a regular file");
    let length = metadata.len();
    let mut reader = file.take(length.saturating_add(1));
    let mut digest = Sha256::new();
    let mut bytes = [0; 8192];
    let mut read = 0u64;
    loop {
        let count = reader.read(&mut bytes)?;
        if count == 0 {
            break;
        }
        digest.update(&bytes[..count]);
        read += count as u64;
    }
    ensure!(
        read == length,
        "configuration changed while it was being fingerprinted; retry"
    );
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}
// Python's canonical JSON encoding is retained for existing cache and action hashes.
pub fn canonical(value: &Value) -> String {
    match value {
        Value::Array(v) => format!(
            "[{}]",
            v.iter().map(canonical).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(v) => format!(
            "{{{}}}",
            v.iter()
                .map(|(k, v)| format!("{}: {}", canonical(&json!(k)), canonical(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => {
            let raw = value.to_string();
            let mut out = String::new();
            for c in raw.chars() {
                if c.is_ascii() {
                    out.push(c);
                } else {
                    let mut buf = [0; 2];
                    for n in c.encode_utf16(&mut buf) {
                        out.push_str(&format!("\\u{n:04x}"));
                    }
                }
            }
            out
        }
    }
}
pub fn hash_json(value: &Value) -> String {
    sha256(canonical(value))
}
pub struct Lock {
    _file: File,
}
impl Drop for Lock {
    fn drop(&mut self) {
        // Explicit unlock also releases locks briefly inherited by a concurrent spawn.
        let _ = self._file.unlock();
    }
}
pub fn lock(path: &Path) -> Result<Lock> {
    try_lock(path)?.ok_or_else(|| anyhow!("session is locked by another process"))
}
pub fn try_lock(path: &Path) -> Result<Option<Lock>> {
    private_dir(path)?;
    let target = local_path(path, ".lock")?;
    let mut opts = OpenOptions::new();
    opts.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = opts.open(target)?;
    ensure!(
        file.metadata()?.is_file(),
        "session lock must be a private regular file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        ensure!(
            file.metadata()?.nlink() == 1,
            "session lock must be a private regular file"
        );
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    match file.try_lock() {
        Ok(()) => Ok(Some(Lock { _file: file })),
        Err(std::fs::TryLockError::WouldBlock) => Ok(None),
        Err(std::fs::TryLockError::Error(error)) => Err(error.into()),
    }
}
pub fn validate_tree(root: &Path) -> Result<()> {
    for entry in walkdir::WalkDir::new(root).follow_links(false) {
        let e = entry?;
        local_path(root, e.path().strip_prefix(root)?)?;
        ensure!(
            e.file_type().is_file() || e.file_type().is_dir(),
            "session snapshots require regular files and directories"
        );
    }
    Ok(())
}
pub fn copy_tree(source: &Path, target: &Path) -> Result<()> {
    validate_tree(source)?;
    let source_root = source.canonicalize()?;
    let absolute_target = crate::orgs::absolute(target)?;
    let existing_parent = absolute_target
        .ancestors()
        .find(|p| p.exists())
        .ok_or_else(|| anyhow!("copy target has no existing parent"))?;
    ensure!(
        !existing_parent.canonicalize()?.starts_with(&source_root),
        "a directory cannot be copied into itself or its descendants"
    );
    for entry in walkdir::WalkDir::new(source) {
        let e = entry?;
        let dest = local_path(target, e.path().strip_prefix(source)?)?;
        if e.file_type().is_dir() {
            private_dir(&dest)?;
        } else {
            write(&dest, read_bytes(e.path(), 8 * 1048576)?)?;
        }
    }
    Ok(())
}
pub fn entries(path: &Path) -> Result<Vec<PathBuf>> {
    let metadata = match path.metadata() {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
        Err(error) => return Err(error.into()),
    };
    ensure!(metadata.is_dir(), "local state path must be a directory");
    let mut items = fs::read_dir(path)?
        .map(|e| e.map(|v| v.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    items.sort();
    Ok(items)
}
pub fn session_path(root: &Path, id: &str) -> Result<(PathBuf, Value)> {
    let path = local_path(root, Path::new("sessions").join(identifier(id)?))?;
    let p = local_path(&path, "session.json")?;
    ensure!(p.is_file(), "unknown session: {id}");
    let data = read_json(&p)?;
    validate_session(&data, id)?;
    Ok((path, data))
}
pub(crate) fn validate_session(data: &Value, id: &str) -> Result<()> {
    ensure!(
        data.is_object()
            && data["schema_version"] == SCHEMA
            && text(&data["id"]) == id
            && orgs::valid_name(text(&data["environment"])),
        "unsupported or inconsistent session metadata"
    );
    ensure!(
        data["budget"].is_null()
            || data["budget"]
                .as_u64()
                .is_some_and(|n| (1..=1000).contains(&n)),
        "invalid session budget: expected null or a whole number 1-1000"
    );
    let mut notes = std::collections::BTreeSet::new();
    ensure!(
        ["open", "done", "diagnosed", "inconclusive"].contains(&text(&data["status"]))
            && data["notes"].is_array()
            && array(&data["notes"]).iter().all(|n| {
                n.is_object()
                    && identifier(text(&n["id"])).is_ok()
                    && notes.insert(text(&n["id"]))
                    && ["finding", "action", "hypothesis", "rejected", "question"]
                        .contains(&text(&n["kind"]))
                    && n["text"].is_string()
                    && !text(&n["text"]).trim().is_empty()
                    && n.get("evidence")
                        .is_none_or(|v| v.is_array() && array(v).iter().all(Value::is_string))
                    && n.get("follows")
                        .is_none_or(|v| v.is_array() && array(v).iter().all(Value::is_string))
                    && n.get("withdrawn_at").is_none_or(Value::is_string)
            }),
        "invalid session status or notes"
    );
    if let Some(workflow) = data.get("workflow").filter(|v| !v.is_null()) {
        crate::workflows::validate_snapshot(workflow)?;
    }
    if let Some(previous) = data.get("previous").filter(|v| !v.is_null()) {
        let mut actions = std::collections::BTreeSet::new();
        ensure!(
            previous.is_object()
                && identifier(text(&previous["session"])).is_ok()
                && previous["actions"].is_array()
                && array(&previous["actions"]).iter().all(|a| {
                    a.is_object()
                        && identifier(text(&a["id"])).is_ok()
                        && actions.insert(text(&a["id"]))
                        && a["text"].is_string()
                }),
            "invalid session previous review or actions"
        );
    }
    Ok(())
}
pub fn finished_at(data: &Value) -> Result<DateTime<Utc>> {
    let stamp = data
        .get("closed_at")
        .filter(|value| !value.is_null())
        .unwrap_or(&data["created_at"]);
    let stamp = stamp
        .as_str()
        .ok_or_else(|| anyhow!("invalid session finish time"))?;
    instant(stamp).or_else(|_| {
        // Older review summaries can store a UTC date without a time.
        chrono::NaiveDate::parse_from_str(stamp, "%Y-%m-%d")
            .ok()
            .and_then(|date| date.and_hms_opt(0, 0, 0))
            .map(|date| date.and_utc())
            .ok_or_else(|| anyhow!("invalid session finish time"))
    })
}
pub fn services(root: &Path, env: &str) -> Result<Vec<Value>> {
    let path = local_path(root, format!("knowledge/{env}/services.json"))?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let data = read_json(&path)?;
    ensure!(
        data["schema_version"] == SCHEMA && data["services"].is_array(),
        "services.json needs schema_version: 1 and a services array"
    );
    let mut ids = std::collections::BTreeSet::new();
    for service in array(&data["services"]) {
        ensure!(service.is_object(), "each service must be an object");
        let id = identifier(text(&service["id"]))?;
        ensure!(ids.insert(id), "duplicate service ID: {id}");
        ensure!(
            service
                .get("aliases")
                .is_none_or(|v| v.is_array() && array(v).iter().all(Value::is_string)),
            "service aliases must be an array of strings"
        );
        ensure!(
            service.get("sources").is_none_or(Value::is_object),
            "service sources must be an object"
        );
        for (provider, config) in object(&service["sources"]) {
            ensure!(
                [
                    "cubeapm",
                    "grafana",
                    "jenkins",
                    "github",
                    "git",
                    "elasticsearch",
                    "kubernetes",
                    "aws",
                    "jira",
                    "nginxpm"
                ]
                .contains(&provider.as_str())
                    && config.is_object(),
                "unsupported or invalid service source: {provider}"
            );
            ensure!(
                config
                    .get("metrics")
                    .is_none_or(|v| v.is_object() && object(v).values().all(Value::is_string)),
                "metrics must map labels to query strings"
            );
        }
        ensure!(
            service["provenance"].is_object(),
            "service provenance must be an object"
        );
        ensure!(
            truth(&service["provenance"]["source"]) && truth(&service["provenance"]["observed_at"]),
            "every service needs provenance.source and provenance.observed_at"
        );
        instant(text(&service["provenance"]["observed_at"]))?;
    }
    Ok(array(&data["services"]).to_vec())
}
pub fn resolve_service(root: &Path, env: &str, name: &str) -> Result<Value> {
    let found = services(root, env)?
        .into_iter()
        .filter(|s| text(&s["id"]) == name || array(&s["aliases"]).contains(&json!(name)))
        .collect::<Vec<_>>();
    ensure!(found.len() <= 1, "ambiguous service alias: {name}");
    found.into_iter().next().ok_or_else(||anyhow!("service is unmapped; add it to knowledge/{env}/services.json (see examples/services.json)"))
}
pub fn knowledge_snapshot(root: &Path, env: &str) -> Result<Value> {
    let mut files = json!({});
    for layer in ["_shared", env] {
        let base = root.join("knowledge").join(layer);
        if redirected(&base) {
            continue;
        }
        for p in entries(&base)? {
            if p.is_file() && !redirected(&p) && p.metadata()?.len() <= 8 * 1048576 {
                files[format!(
                    "{layer}/{}",
                    p.file_name().unwrap_or_default().to_string_lossy()
                )] = json!(sha256(read_bytes(&p, 8 * 1048576)?));
            }
        }
    }
    Ok(files)
}
pub fn create(
    root: &Path,
    org: &str,
    env: &str,
    question: &str,
    mode: &str,
    extra: Value,
) -> Result<(PathBuf, Value)> {
    let id = format!(
        "{}-{}",
        Utc::now().format("%Y%m%dT%H%M%SZ"),
        &uuid::Uuid::new_v4().simple().to_string()[..10]
    );
    let path = local_path(root, format!("sessions/{id}"))?;
    let mut data = json!({"schema_version":SCHEMA,"id":id,"org":org,"environment":env,"mode":mode,"service":null,"service_context":null,"question":question,"window":null,"playbook":null,"area":null,"created_at":now(),"status":"open","notes":[],"knowledge":knowledge_snapshot(root,env)?,"budget":null});
    for (k, v) in object(&extra) {
        data[k] = v;
    }
    let content = json_content(&data)?;
    private_dir(&path.join("evidence"))?;
    write(&path.join("session.json"), content)?;
    Ok((path, data))
}
pub fn evidence(path: &Path) -> Result<Vec<Value>> {
    let session = read_json(&path.join("session.json"))?;
    let mut records = vec![];
    for dir in entries(&local_path(path, "evidence")?)? {
        let id = dir.file_name().unwrap_or_default().to_string_lossy();
        local_path(path, format!("evidence/{id}"))?;
        ensure!(dir.is_dir(), "unexpected file in evidence directory");
        let p = local_path(&dir, "record.json")?;
        if !p.exists() && entries(&dir)?.is_empty() {
            continue;
        }
        let mut record = read_json(&p)?;
        ensure!(
            record.is_object() && record["schema_version"] == SCHEMA,
            "unsupported evidence metadata"
        );
        ensure!(
            text(&record["id"]) == id
                && record["session"] == session["id"]
                && record["environment"] == session["environment"]
                && record.get("org").unwrap_or(&session["org"]) == &session["org"],
            "evidence identity/org/environment mismatch"
        );
        let artifacts = &record["artifacts"];
        ensure!(
            artifacts.is_object()
                && object(artifacts)
                    .keys()
                    .all(|k| k == "stdout" || k == "stderr"),
            "invalid evidence artifacts"
        );
        ensure!(
            text(&record["status"]) != "ok" || object(artifacts).len() == 2,
            "evidence integrity check failed: successful record lacks artifacts"
        );
        let mut stdout = None;
        for (stream, artifact) in object(artifacts) {
            let expected = format!("evidence/{id}/{stream}.txt");
            ensure!(
                artifact.is_object() && text(&artifact["path"]) == expected,
                "invalid evidence artifact path"
            );
            let content = read_bytes(&local_path(path, &expected)?, 8 * 1048576)
                .map_err(|_| anyhow!("evidence integrity check failed: missing artifact"))?;
            ensure!(
                artifact["bytes"].as_u64() == Some(content.len() as u64)
                    && text(&artifact["sha256"]) == sha256(&content),
                "evidence integrity check failed: {id}/{stream}"
            );
            if stream == "stdout" {
                stdout = Some(content);
            }
        }
        if let Some(stdout) = stdout {
            crate::contracts::apply(&mut record, &stdout);
        }
        records.push(record);
    }
    records.sort_by_key(|r| {
        (
            text(&r["observed_at"]).to_owned(),
            text(&r["id"]).to_owned(),
        )
    });
    Ok(records)
}
