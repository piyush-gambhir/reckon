use crate::store::*;
use crate::*;
use std::{collections::BTreeSet, env, fs};
pub type Registry = serde_json::Map<String, Value>;
pub const BASE_KEYS: &[&str] = &[
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
];
#[derive(Debug, Clone)]
pub struct Context {
    pub org: String,
    pub env: String,
    pub production: bool,
    pub source: String,
    pub data: Value,
    pub link: Value,
}
impl Context {
    pub fn from_data(data: Value, env: &str) -> Self {
        Self {
            org: text(&data["id"]).into(),
            env: env.into(),
            production: array(&data["production"]).contains(&json!(env)),
            source: "arguments".into(),
            data,
            link: Value::Null,
        }
    }
}
pub fn home() -> Result<PathBuf> {
    let raw = env::var("RECKON_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            format!(
                "{}/.reckon",
                env::var("HOME")
                    .or_else(|_| env::var("USERPROFILE"))
                    .unwrap_or_default()
            )
        });
    absolute(&expand_home(&raw))
}
pub fn expand_home(raw: &str) -> PathBuf {
    if raw == "~" || raw.starts_with("~/") {
        PathBuf::from(
            env::var("HOME")
                .or_else(|_| env::var("USERPROFILE"))
                .unwrap_or_default(),
        )
        .join(raw.strip_prefix("~/").unwrap_or(""))
    } else {
        PathBuf::from(raw)
    }
}
pub fn absolute(path: &Path) -> Result<PathBuf> {
    let path = if path.is_absolute() {
        path.to_owned()
    } else {
        env::current_dir()?.join(path)
    };
    let mut out = PathBuf::new();
    for c in path.components() {
        match c {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            _ => out.push(c.as_os_str()),
        }
    }
    Ok(out)
}
pub fn code_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}
pub fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 40
        && (value.as_bytes()[0].is_ascii_lowercase() || value.as_bytes()[0].is_ascii_digit())
        && value
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}
pub fn name<'a>(value: &'a str, kind: &str) -> Result<&'a str> {
    ensure!(
        valid_name(value),
        "{kind} names use lowercase letters, digits and hyphens (at most 40): '{value}'"
    );
    Ok(value)
}
pub fn valid_label(v: &str) -> bool {
    !v.is_empty()
        && v.len() <= 80
        && v.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.:/-".contains(&c))
}
pub fn providers() -> Result<Registry> {
    let data: Value = serde_json::from_str(include_str!("../scripts/providers.json"))?;
    ensure!(
        data["schema_version"] == SCHEMA && data["providers"].is_object(),
        "scripts/providers.json needs schema_version 1 and a providers object"
    );
    Ok(object(&data["providers"]))
}
pub fn org_path(h: &Path, org: &str) -> Result<PathBuf> {
    local_path(h, Path::new("orgs").join(name(org, "org")?))
}
pub fn workspace(h: &Path, org: &str) -> Result<PathBuf> {
    local_path(&org_path(h, org)?, "workspace")
}
pub fn validate(data: &Value, org: &str, registry: &Registry) -> Result<()> {
    ensure!(
        data.is_object() && data["schema_version"] == SCHEMA && text(&data["id"]) == org,
        "org.json for {org} is missing fields or uses another schema"
    );
    let envs = strings(&data["environments"]);
    ensure!(
        data["environments"].is_array()
            && !envs.is_empty()
            && envs.iter().collect::<BTreeSet<_>>().len() == envs.len(),
        "{org}: environments must be a non-empty list of unique names"
    );
    for e in &envs {
        name(e, "environment")?;
    }
    ensure!(
        data["production"].is_array()
            && array(&data["production"])
                .iter()
                .all(|v| v.is_string() && envs.contains(&text(v).to_owned())),
        "{org}: production must list some of the org's environments"
    );
    ensure!(
        data["connections"].is_object(),
        "{org}: connections must be an object"
    );
    for (cname, conn) in object(&data["connections"]) {
        name(&cname, "connection")?;
        let prefix = format!("{org}/{cname}");
        ensure!(
            conn.is_object()
                && object(&conn).keys().all(|k| [
                    "provider",
                    "env",
                    "env_labels",
                    "resources",
                    "account",
                    "note"
                ]
                .contains(&k.as_str())),
            "{prefix}: allowed fields are provider, env, env_labels, resources, account, note"
        );
        if let Some(account) = conn.get("account") {
            let a = text(account);
            ensure!(
                account.is_string()
                    && !a.is_empty()
                    && a.chars().count() <= 200
                    && a.trim() == a
                    && !a.chars().any(char::is_control),
                "{prefix}: account is the login, email or account id the tool reports (a * pattern is allowed)"
            );
        }
        let provider = text(&conn["provider"]);
        let spec = registry.get(provider).unwrap_or(&Value::Null);
        let kinds = object(&spec["resources"]);
        if let Some(bound) = conn.get("resources") {
            ensure!(
                bound.is_object()
                    && object(bound).iter().all(|(k, v)| kinds.contains_key(k)
                        && v.is_array()
                        && !array(v).is_empty()
                        && array(v)
                            .iter()
                            .all(|v| v.is_string() && !text(v).is_empty())),
                "{prefix}: resources must map {} to non-empty lists of names or patterns",
                if kinds.is_empty() {
                    "nothing for this tool".into()
                } else {
                    kinds.keys().cloned().collect::<Vec<_>>().join(", ")
                }
            );
        }
        ensure!(
            registry.contains_key(provider),
            "{prefix}: unknown provider '{provider}'"
        );
        ensure!(
            !(conn.get("env").is_some() && conn.get("env_labels").is_some()),
            "{prefix}: use env for one environment or env_labels for a shared one, not both"
        );
        if let Some(e) = conn.get("env") {
            ensure!(
                envs.contains(&text(e).to_owned()),
                "{prefix}: environment '{e}' is not one of {}",
                envs.join(", ")
            );
        }
        if let Some(labels) = conn.get("env_labels") {
            ensure!(
                labels.is_object()
                    && object(labels)
                        .iter()
                        .all(|(e, v)| envs.contains(e) && v.is_string() && valid_label(text(v))),
                "{prefix}: env_labels must map the org's environments to plain labels"
            );
        }
    }
    Ok(())
}
pub fn budget_problem(data: &Value) -> Option<String> {
    let limit = &data["agent_token_budget"];
    if limit.is_null()
        || (limit.is_object()
            && object(limit).len() == 2
            && limit["tokens"].as_u64().is_some_and(|n| n >= 1)
            && limit["days"]
                .as_u64()
                .is_some_and(|n| (1..=365).contains(&n)))
    {
        None
    } else {
        Some(format!(
            "{}: agent_token_budget needs whole numbers tokens >= 1 and days 1-365 (reckon spend --set-budget 0 clears it)",
            text(&data["id"])
        ))
    }
}
pub fn load(h: &Path, org: &str, registry: &Registry) -> Result<Value> {
    let path = local_path(&org_path(h, org)?, "org.json")?;
    ensure!(
        path.is_file(),
        "unknown org: {org} (create it with: reckon org add {org})"
    );
    let data = read_json(&path)?;
    validate(&data, org, registry)?;
    Ok(data)
}
pub fn save(h: &Path, data: &Value, registry: &Registry) -> Result<()> {
    validate(data, text(&data["id"]), registry)?;
    if let Some(problem) = budget_problem(data) {
        bail!(problem);
    }
    write_json(
        &local_path(&org_path(h, text(&data["id"]))?, "org.json")?,
        data,
    )
}
pub fn orgs(h: &Path) -> Result<Vec<String>> {
    Ok(entries(&local_path(h, "orgs")?)?
        .iter()
        .filter_map(|p| {
            let n = p.file_name()?.to_str()?;
            (valid_name(n) && p.join("org.json").is_file()).then(|| n.to_owned())
        })
        .collect())
}
pub fn create(
    h: &Path,
    org: &str,
    display: Option<&str>,
    envs: Vec<String>,
    production: Option<Vec<String>>,
    registry: &Registry,
) -> Result<Value> {
    let path = org_path(h, org)?;
    ensure!(!path.join("org.json").exists(), "org already exists: {org}");
    let production = production.unwrap_or_else(|| {
        envs.iter()
            .filter(|e| ["prod", "production"].contains(&e.as_str()))
            .cloned()
            .collect()
    });
    let data = json!({"schema_version":SCHEMA,"id":org,"name":display.filter(|s|!s.is_empty()).unwrap_or(org),"environments":envs,"production":production,"connections":{},"created_at":now()});
    validate(&data, org, registry)?;
    for dir in ["secrets", "config", "runtime", "workspace"] {
        private_dir(&local_path(&path, dir)?)?;
    }
    save(h, &data, registry)?;
    Ok(data)
}
pub fn folder(path: &Path) -> Result<PathBuf> {
    let path = absolute(&expand_home(&path.to_string_lossy()))?;
    Ok(fs::canonicalize(&path).unwrap_or(path))
}
pub fn links(h: &Path) -> Result<Value> {
    let path = local_path(h, "links.json")?;
    if !path.is_file() {
        return Ok(json!({}));
    }
    let data = read_json(&path).unwrap_or(Value::Null);
    let entries = &data["links"];
    ensure!(
        data["schema_version"] == SCHEMA
            && entries.is_object()
            && object(entries).values().all(|v| v.is_object()
                && object(v)
                    .keys()
                    .all(|k| ["org", "env", "use"].contains(&k.as_str()))
                && valid_name(text(&v["org"]))
                && valid_name(text(&v["env"]))
                && v.get("use")
                    .is_none_or(|u| u.is_array() && array(u).iter().all(|c| valid_name(text(c))))),
        "{} is malformed; fix or delete it",
        path.display()
    );
    Ok(entries.clone())
}
pub fn linked(h: &Path, cwd: Option<&Path>) -> Result<Value> {
    let here = folder(cwd.unwrap_or(&env::current_dir()?))?;
    let root = folder(h)?;
    if here.starts_with(root) {
        return Ok(Value::Null);
    }
    let entries = links(h)?;
    let best = object(&entries)
        .into_iter()
        .filter(|(p, _)| here.starts_with(p))
        .max_by_key(|(p, _)| Path::new(p).components().count());
    Ok(best
        .map(|(p, mut v)| {
            v["path"] = json!(p);
            v
        })
        .unwrap_or(Value::Null))
}
pub fn context(
    h: &Path,
    org: Option<&str>,
    environment: Option<&str>,
    registry: &Registry,
) -> Result<Context> {
    let pin_org = env::var("RECKON_ORG").unwrap_or_default();
    let pin_env = env::var("RECKON_ENV").unwrap_or_default();
    ensure!(
        !(!pin_org.is_empty() && org.is_some_and(|o| o != pin_org)
            || !pin_env.is_empty() && environment.is_some_and(|e| e != pin_env)),
        "this agent is pinned to {pin_org}/{pin_env}; another org or environment needs its own agent (reckon open <org>/<env>)"
    );
    let saved_path = local_path(h, "context")?;
    let saved = if saved_path.is_file() {
        String::from_utf8(read_bytes(&saved_path, 4096)?)?
            .trim()
            .to_owned()
    } else {
        String::new()
    };
    let (saved_org, saved_env) = saved.split_once('/').unwrap_or((&saved, ""));
    let mut link = if activity::pinned() {
        Value::Null
    } else {
        linked(h, None)?
    };
    let mut source = "arguments".to_owned();
    let selected = if let Some(o) = org.filter(|o| !o.is_empty()) {
        o.to_owned()
    } else if !pin_org.is_empty() {
        source = "RECKON_ORG/RECKON_ENV".into();
        pin_org.clone()
    } else if !link.is_null() {
        source = format!("folder link {}", text(&link["path"]));
        text(&link["org"]).into()
    } else {
        source = saved_path.display().to_string();
        saved_org.into()
    };
    if text(&link["org"]) != selected {
        link = Value::Null;
    }
    let environment = environment
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| {
            if pin_org == selected {
                pin_env.clone()
            } else if !link.is_null() {
                text(&link["env"]).into()
            } else if saved_org == selected {
                saved_env.into()
            } else {
                String::new()
            }
        });
    ensure!(
        !selected.is_empty(),
        "no org selected: run reckon use <org>/<env> (reckon org list shows your orgs)"
    );
    let data = load(h, &selected, registry)?;
    let envs = strings(&data["environments"]);
    ensure!(
        !environment.is_empty(),
        "no environment selected for {selected}; choose one of: {}",
        envs.join(", ")
    );
    ensure!(
        envs.contains(&environment),
        "{selected} has no environment '{environment}'; choose one of: {}",
        envs.join(", ")
    );
    let here = absolute(&env::current_dir()?)?;
    let base = absolute(&h.join("orgs"))?;
    if let Ok(relative) = here.strip_prefix(base) {
        let parts = relative.components().collect::<Vec<_>>();
        if parts.len() >= 2 && parts[1].as_os_str() == "workspace" {
            let owner = parts[0].as_os_str().to_string_lossy();
            ensure!(
                owner == selected,
                "this folder belongs to org {owner}, but the selected org is {selected}"
            );
        }
    }
    let mut ctx = Context::from_data(data, &environment);
    ctx.source = source;
    ctx.link = link;
    Ok(ctx)
}
pub fn set_context(h: &Path, org: &str, environment: &str, registry: &Registry) -> Result<()> {
    let data = load(h, org, registry)?;
    ensure!(
        array(&data["environments"]).contains(&json!(environment)),
        "{org} has no environment '{environment}'; choose one of: {}",
        strings(&data["environments"]).join(", ")
    );
    write(&local_path(h, "context")?, format!("{org}/{environment}\n"))
}
pub fn applies(conn: &Value, environment: &str) -> bool {
    if let Some(labels) = conn.get("env_labels") {
        labels.get(environment).is_some()
    } else {
        conn["env"].is_null() || text(&conn["env"]) == environment
    }
}
pub fn resolve(ctx: &Context, target: &str) -> Result<(String, Value)> {
    let conns = object(&ctx.data["connections"]);
    let preferred_use = strings(&ctx.link["use"]);
    let missing = preferred_use
        .iter()
        .filter(|c| !conns.contains_key(*c))
        .cloned()
        .collect::<Vec<_>>();
    ensure!(
        missing.is_empty(),
        "the folder link at {} prefers {}, which {} no longer has; link the folder again (reckon link)",
        text(&ctx.link["path"]),
        missing.join(", "),
        ctx.org
    );
    let preferred = preferred_use
        .iter()
        .filter(|c| text(&conns[*c]["provider"]) == target)
        .cloned()
        .collect::<Vec<_>>();
    if let Some(conn) = conns.get(target) {
        ensure!(
            preferred.is_empty() || preferred.contains(&target.to_owned()),
            "{target} names a connection, but the folder link at {} prefers {} for that tool; name the connection you mean",
            text(&ctx.link["path"]),
            preferred.join(", ")
        );
        if !applies(conn, &ctx.env) {
            if conn.get("env_labels").is_some() {
                bail!(
                    "connection {target} has no label for {}; add one: reckon connect {target} --label {}=<label>",
                    ctx.env,
                    ctx.env
                );
            }
            bail!(
                "connection {target} belongs to {}; this context is {}/{}",
                text(&conn["env"]),
                ctx.org,
                ctx.env
            );
        }
        return Ok((target.into(), conn.clone()));
    }
    let matches = conns
        .iter()
        .filter(|(n, c)| {
            text(&c["provider"]) == target
                && applies(c, &ctx.env)
                && (preferred.is_empty() || preferred.contains(n))
        })
        .collect::<Vec<_>>();
    if matches.len() == 1 {
        return Ok((matches[0].0.clone(), matches[0].1.clone()));
    }
    if !preferred.is_empty() {
        bail!(
            "the folder link at {} prefers {} for {target}, and {} {}; name a connection or link the folder again",
            text(&ctx.link["path"]),
            preferred.join(", "),
            if matches.is_empty() {
                "none of them serves"
            } else {
                "several serve"
            },
            ctx.env
        );
    }
    ensure!(
        !matches.is_empty(),
        "{} has no connection named or providing '{target}' for {} (reckon status lists connections; reckon connect adds one)",
        ctx.org,
        ctx.env
    );
    bail!(
        "several {target} connections fit {}: {}; name one",
        ctx.env,
        matches
            .iter()
            .map(|(n, _)| n.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    )
}
pub fn link(
    h: &Path,
    path: &Path,
    org: &str,
    environment: &str,
    use_connections: Vec<String>,
    registry: &Registry,
) -> Result<Value> {
    let where_ = folder(path)?;
    let root = folder(h)?;
    ensure!(where_.is_dir(), "not a folder: {}", where_.display());
    ensure!(
        !where_.starts_with(root),
        "folders under {} already belong to their org",
        h.display()
    );
    let data = load(h, org, registry)?;
    let envs = strings(&data["environments"]);
    ensure!(
        envs.contains(&environment.to_owned()),
        "{org} has no environment '{environment}'; choose one of: {}",
        envs.join(", ")
    );
    let conns = object(&data["connections"]);
    let uses = use_connections
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    for c in &uses {
        ensure!(
            conns.contains_key(c),
            "{org} has no connection {c} (reckon status --org {org} lists them)"
        );
    }
    for p in uses
        .iter()
        .map(|c| text(&conns[c]["provider"]))
        .collect::<BTreeSet<_>>()
    {
        ensure!(
            !conns.contains_key(p) || uses.contains(&p.to_owned()),
            "{org} has a connection named {p}, so reckon run {p} always means it; prefer it or rename it"
        );
        for e in &envs {
            let fit = uses
                .iter()
                .filter(|c| text(&conns[*c]["provider"]) == p && applies(&conns[*c], e))
                .cloned()
                .collect::<Vec<_>>();
            ensure!(
                fit.len() <= 1,
                "{} all serve {org}/{e}; prefer one {p} connection per environment",
                fit.join(", ")
            );
        }
    }
    let key = where_.to_string_lossy().to_string();
    let mut entries = links(h)?;
    let replaced = entries[&key].clone();
    let mut entry = json!({"org":org,"env":environment});
    if !uses.is_empty() {
        entry["use"] = json!(uses);
    }
    entries[&key] = entry.clone();
    write_json(
        &local_path(h, "links.json")?,
        &json!({"schema_version":SCHEMA,"links":entries}),
    )?;
    entry["path"] = json!(key);
    entry["replaced"] = replaced;
    Ok(entry)
}
pub fn unlink(h: &Path, path: &Path) -> Result<Value> {
    let where_ = folder(path)?.to_string_lossy().to_string();
    let mut entries = links(h)?;
    let mut entry = object(&entries).remove(&where_).ok_or_else(|| {
        let covering = linked(h, Some(Path::new(&where_))).unwrap_or(Value::Null);
        anyhow!(
            "no link at {where_}{}",
            if covering.is_null() {
                String::new()
            } else {
                format!(
                    "; it follows the link at {}, unlink that folder instead",
                    text(&covering["path"])
                )
            }
        )
    })?;
    entries
        .as_object_mut()
        .ok_or_else(|| anyhow!("invalid links"))?
        .remove(&where_);
    write_json(
        &local_path(h, "links.json")?,
        &json!({"schema_version":SCHEMA,"links":entries}),
    )?;
    entry["path"] = json!(where_);
    Ok(entry)
}
pub fn secrets_path(h: &Path, org: &str, conn: &str) -> Result<PathBuf> {
    local_path(
        &org_path(h, org)?,
        format!("secrets/{}.env", name(conn, "connection")?),
    )
}
pub fn secrets(h: &Path, org: &str, conn: &str, spec: &Value) -> Result<process::Environment> {
    let path = secrets_path(h, org, conn)?;
    if !path.exists() {
        return Ok(Default::default());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            path.symlink_metadata()?.permissions().mode() & 0o077 == 0,
            "{} is readable by other users; run: chmod 600 {}",
            path.display(),
            path.display()
        );
    }
    let mut values = process::Environment::new();
    let regex = regex::Regex::new(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")?;
    for (number, raw) in String::from_utf8(read_bytes(&path, 65536)?)?
        .lines()
        .enumerate()
    {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let captures = regex
            .captures(line)
            .ok_or_else(|| anyhow!("{}.env line {}: expected KEY=value", conn, number + 1))?;
        let key = &captures[1];
        let mut value = captures[2].trim();
        if value.len() >= 2
            && ((value.starts_with('\'') && value.ends_with('\''))
                || (value.starts_with('"') && value.ends_with('"')))
        {
            value = &value[1..value.len() - 1];
        }
        let prefixes = strings(&spec["prefixes"]);
        ensure!(
            prefixes.iter().any(|p| key.starts_with(p)),
            "{conn}.env line {}: {key} is not a setting for this provider (expected {})",
            number + 1,
            prefixes
                .iter()
                .map(|p| format!("{p}*"))
                .collect::<Vec<_>>()
                .join(" or ")
        );
        values.insert(key.into(), value.into());
    }
    Ok(values)
}
pub fn reference(key: &str, value: &str) -> Result<String> {
    let argv: Vec<String> = if let Some(item) = value.strip_prefix("keychain:") {
        let item = item.trim();
        ensure!(!item.is_empty(), "{key}: keychain: needs an item name");
        if cfg!(target_os = "macos") {
            vec!["security", "find-generic-password", "-s", item, "-w"]
        } else {
            vec!["secret-tool", "lookup", "service", item]
        }
        .into_iter()
        .map(str::to_owned)
        .collect()
    } else if value.starts_with("op://") {
        return Ok(crate::onepassword::resolve(
            &home()?,
            &process::Environment::from([(key.into(), value.into())]),
        )?[key]
            .clone());
    } else {
        return Ok(value.into());
    };
    let done = process::run(&argv, None, None, 60.0, 65536, None)?;
    ensure!(
        done.status != process::Status::Unavailable && done.status != process::Status::Timeout,
        "{key}: could not read its secret reference with {}",
        argv[0]
    );
    let secret = String::from_utf8_lossy(&done.stdout)
        .trim_end_matches(['\r', '\n'])
        .to_owned();
    ensure!(
        done.status == process::Status::Ok && !secret.is_empty(),
        "{key}: {} found nothing for {value}",
        argv[0]
    );
    Ok(secret)
}

#[derive(Clone, Default, PartialEq, Eq, serde::Serialize)]
#[serde(transparent)]
pub struct ResolvedSettings {
    values: process::Environment,
    #[serde(skip)]
    protected: BTreeSet<String>,
}
impl std::ops::Deref for ResolvedSettings {
    type Target = process::Environment;
    fn deref(&self) -> &Self::Target {
        &self.values
    }
}
impl From<process::Environment> for ResolvedSettings {
    fn from(values: process::Environment) -> Self {
        Self {
            values,
            protected: BTreeSet::new(),
        }
    }
}
pub fn references(h: &Path, values: &process::Environment) -> Result<ResolvedSettings> {
    let op = values
        .iter()
        .filter(|(_, v)| v.starts_with("op://"))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect::<process::Environment>();
    let mut resolved = if op.is_empty() {
        process::Environment::new()
    } else {
        crate::onepassword::resolve(h, &op)?
    };
    for (key, value) in values {
        if !op.contains_key(key) {
            resolved.insert(key.clone(), reference(key, value)?);
        }
    }
    let protected = values
        .iter()
        .filter(|(_, v)| v.starts_with("op://") || v.starts_with("keychain:"))
        .map(|(k, _)| resolved[k].clone())
        .collect();
    Ok(ResolvedSettings {
        values: resolved,
        protected,
    })
}
pub fn secret_key(key: &str) -> bool {
    regex::Regex::new(r"(?i)TOKEN|PASSWORD|SECRET|PASS$|_PWD$|API_KEY|APIKEY|COOKIE|CREDENTIALS|PRIVATE_KEY|_URI$")
        .expect("constant regex").is_match(key)
}
pub fn placeholder(value: &str) -> bool {
    value.is_empty()
        || regex::Regex::new(
            r"(?i)replace_me|example\.com|\.example\.|@example|xxxx|changeme|your-|^<.*>$",
        )
        .is_ok_and(|r| r.is_match(value))
}
pub fn configured(
    h: &Path,
    org: &str,
    conn: &str,
    spec: &Value,
    values: Option<&process::Environment>,
) -> Result<bool> {
    let owned;
    let values = if let Some(v) = values {
        v
    } else {
        owned = secrets(h, org, conn, spec)?;
        &owned
    };
    let required = strings(&spec["required"]);
    if !required.is_empty()
        && required.iter().all(|group| {
            group
                .split('/')
                .any(|k| !placeholder(values.get(k).map(String::as_str).unwrap_or("")))
        })
    {
        return Ok(true);
    }
    let config = local_path(&org_path(h, org)?, format!("config/{conn}"))?;
    for p in strings(&spec["config_files"]) {
        let p = local_path(&config, p)?;
        if p.is_file() && p.metadata()?.len() > 0 {
            return Ok(true);
        }
    }
    Ok(false)
}
pub fn child_env(
    h: &Path,
    ctx: &Context,
    conn_name: &str,
    conn: &Value,
    spec: &Value,
    interactive: bool,
    values: Option<&ResolvedSettings>,
) -> Result<(process::Environment, ResolvedSettings)> {
    let values = match values {
        Some(v) => v.clone(),
        None => references(h, &secrets(h, &ctx.org, conn_name, spec)?)?,
    };
    let org_dir = org_path(h, &ctx.org)?;
    let mut env = env::vars()
        .filter(|(k, _)| BASE_KEYS.contains(&k.as_str()))
        .collect::<process::Environment>();
    env.extend(values.values.clone());
    let config = local_path(&org_dir, format!("config/{conn_name}"))?;
    private_dir(&config)?;
    env.insert("XDG_CONFIG_HOME".into(), config.display().to_string());
    let runtime = local_path(&org_dir, format!("runtime/{conn_name}"))?;
    for (dir, keys) in [
        ("home", vec!["HOME", "USERPROFILE"]),
        ("cache", vec!["XDG_CACHE_HOME", "LOCALAPPDATA"]),
        ("state", vec!["XDG_STATE_HOME", "APPDATA"]),
        ("tmp", vec!["TMPDIR", "TMP", "TEMP"]),
    ] {
        let target = local_path(&runtime, dir)?;
        private_dir(&target)?;
        for k in keys {
            env.insert(k.into(), target.display().to_string());
        }
    }
    if truth(&spec["real_home"])
        && let Ok(home) = env::var("HOME")
    {
        env.insert("HOME".into(), home);
    }
    for (k, relative) in object(&spec["paths"]) {
        env.insert(
            k,
            local_path(&config, text(&relative))?.display().to_string(),
        );
    }
    for layer in ["defaults", "always"] {
        for (k, v) in object(&spec[layer]) {
            env.insert(k, text(&v).into());
        }
    }
    if !interactive {
        for (k, v) in object(&spec["read_only"]) {
            env.insert(k, text(&v).into());
        }
        for (k, v) in [
            ("NO_COLOR", "1"),
            ("CI", "1"),
            ("PAGER", "cat"),
            ("GIT_PAGER", "cat"),
            ("GIT_TERMINAL_PROMPT", "0"),
        ] {
            env.insert(k.into(), v.into());
        }
    }
    let label = text(&conn["env_labels"][&ctx.env]);
    if !label.is_empty() && !text(&spec["env_label_var"]).is_empty() {
        env.insert(text(&spec["env_label_var"]).into(), label.into());
    }
    for (k, v) in [
        ("RECKON_HOME", h.display().to_string()),
        ("RECKON_ORG", ctx.org.clone()),
        ("RECKON_ENV", ctx.env.clone()),
        ("RECKON_CONNECTION", conn_name.into()),
    ] {
        env.insert(k.into(), v);
    }
    if !label.is_empty() {
        env.insert("RECKON_ENV_LABEL".into(), label.into());
    }
    Ok((env, values))
}
pub trait Redactable {
    fn settings(&self) -> &process::Environment;
    fn protected(&self) -> Vec<&str> {
        vec![]
    }
}
impl Redactable for process::Environment {
    fn settings(&self) -> &process::Environment {
        self
    }
}
impl Redactable for ResolvedSettings {
    fn settings(&self) -> &process::Environment {
        &self.values
    }
    fn protected(&self) -> Vec<&str> {
        self.protected.iter().map(String::as_str).collect()
    }
}
pub fn redact(content: &str, values: &impl Redactable) -> String {
    let mut secrets = values
        .settings()
        .iter()
        .filter(|(k, v)| v.len() >= 4 && secret_key(k))
        .map(|(_, v)| v.clone())
        .chain(
            values
                .protected()
                .into_iter()
                .filter(|v| !v.is_empty())
                .map(str::to_owned),
        )
        .collect::<BTreeSet<_>>();
    for value in secrets.clone() {
        let escaped = serde_json::to_string(&value).expect("strings serialize");
        secrets.insert(escaped[1..escaped.len() - 1].into());
        let ascii = crate::store::canonical(&json!(value));
        secrets.insert(ascii[1..ascii.len() - 1].into());
    }
    let mut secrets = secrets.into_iter().collect::<Vec<_>>();
    secrets.sort_by_key(|s| std::cmp::Reverse(s.len()));
    let mut out = content.to_owned();
    for secret in secrets {
        out = out.replace(&secret, "[REDACTED]");
    }
    let uri = regex::Regex::new(r"(://)[^\s/@:]*:[^\s/@]+@").expect("constant regex");
    out = uri.replace_all(&out, "${1}[REDACTED]@").into_owned();
    regex::Regex::new(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
        .expect("constant regex")
        .replace_all(&out, "${1}[REDACTED]")
        .into_owned()
}
pub fn describe(h: &Path, ctx: &Context, registry: &Registry) -> Vec<Value> {
    object(&ctx.data["connections"]).into_iter().map(|(name,conn)|{let spec=&registry[text(&conn["provider"])];let scope=if truth(&conn["env"]){text(&conn["env"]).to_owned()}else if conn.get("env_labels").is_some(){object(&conn["env_labels"]).keys().cloned().collect::<Vec<_>>().join(", ")}else{"all environments".into()};let mut row=json!({"connection":name,"provider":conn["provider"],"scope":scope,"applies":applies(&conn,&ctx.env),"label":conn["env_labels"][&ctx.env],"account":conn["account"]});match configured(h,&ctx.org,&name,spec,None){Ok(ready)=>{let installed=which(text(&spec["binary"])).is_some();row["configured"]=json!(ready);row["installed"]=json!(installed);row["state"]=json!(if !installed{"no_cli"}else if ready{"ready"}else{"no_credentials"});},Err(e)=>{row["configured"]=json!(false);row["installed"]=json!(false);row["state"]=json!(format!("invalid: {e}"));}}row}).collect()
}
