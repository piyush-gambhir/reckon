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
            private_dir(parent)?;
        }
        fs::create_dir(path).or_else(|e| if path.is_dir() { Ok(()) } else { Err(e) })?;
    }
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
pub fn write_json(path: &Path, value: &Value) -> Result<()> {
    write(path, format!("{}\n", serde_json::to_string_pretty(value)?))
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
    format!("{:x}", Sha256::digest(bytes.as_ref()))
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
    file.try_lock()
        .map_err(|_| anyhow!("session is locked by another process"))?;
    Ok(Lock { _file: file })
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
    if !path.is_dir() {
        return Ok(vec![]);
    }
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
    ensure!(
        data.is_object()
            && data["schema_version"] == SCHEMA
            && text(&data["id"]) == id
            && orgs::valid_name(text(&data["environment"])),
        "unsupported or inconsistent session metadata"
    );
    Ok((path, data))
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
    private_dir(&path.join("evidence"))?;
    let mut data = json!({"schema_version":SCHEMA,"id":id,"org":org,"environment":env,"mode":mode,"service":null,"service_context":null,"question":question,"window":null,"playbook":null,"area":null,"created_at":now(),"status":"open","notes":[],"knowledge":knowledge_snapshot(root,env)?,"budget":null});
    for (k, v) in object(&extra) {
        data[k] = v;
    }
    write_json(&path.join("session.json"), &data)?;
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
        let record = read_json(&p)?;
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
