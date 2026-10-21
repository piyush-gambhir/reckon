//! A single-vault store. Service-account tokens stay in the OS credential store.
use crate::{
    orgs::{Context, Registry},
    process::{self, Environment},
    store::*,
    *,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{IsTerminal, Write},
};

fn valid_id(value: &str) -> bool {
    value.len() == 26
        && value
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
}
fn component(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}
pub fn root(h: &Path) -> Result<PathBuf> {
    local_path(h, "onepassword")
}
pub fn config(h: &Path, required: bool) -> Result<Option<Value>> {
    let path = local_path(&root(h)?, "store.json")?;
    if !path.exists() {
        ensure!(
            !required,
            "1Password is not configured: run reckon secrets setup in your own terminal"
        );
        return Ok(None);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            path.metadata()?.permissions().mode() & 0o077 == 0,
            "1Password configuration must be private (chmod 600)"
        );
    }
    let data = read_json(&path).map_err(|_| anyhow!("1Password configuration is malformed"))?;
    ensure!(
        data.is_object()
            && data["schema_version"] == SCHEMA
            && valid_id(text(&data["vault"]))
            && data["vault_name"].is_string()
            && data["binary"].is_string()
            && Path::new(text(&data["binary"])).is_absolute(),
        "1Password configuration is malformed"
    );
    for role in ["reader", "writer"] {
        let entry = &data[role];
        if role == "writer" && entry.is_null() {
            continue;
        }
        let reference = text(&entry["reference"]);
        let hash = text(&entry["sha256"]);
        ensure!(
            entry.is_object()
                && reference.starts_with("keychain:")
                && !reference[9..].trim().is_empty()
                && hash.len() == 64
                && hash
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
            "1Password configuration is malformed"
        );
    }
    Ok(Some(data))
}
fn required_config(h: &Path) -> Result<Value> {
    config(h, true)?.ok_or_else(|| anyhow!("1Password is not configured"))
}
fn operator() -> Result<()> {
    ensure!(
        !activity::pinned() && std::io::stdin().is_terminal(),
        "secret management is for the operator's own terminal, not a pinned agent"
    );
    Ok(())
}
fn ask(prompt: &str) -> Result<String> {
    eprint!("{prompt}");
    std::io::stderr().flush()?;
    let mut answer = String::new();
    std::io::stdin().read_line(&mut answer)?;
    Ok(answer.trim().into())
}
fn hidden(prompt: &str) -> Result<String> {
    operator()?;
    // Hide input before showing the prompt, including immediately pasted input.
    let _echo = EchoGuard::new()
        .map_err(|_| anyhow!("hidden input is unavailable; use your own terminal"))?;
    eprint!("{prompt}");
    std::io::stderr().flush()?;
    let mut value = String::new();
    std::io::stdin()
        .read_line(&mut value)
        .map_err(|_| anyhow!("hidden input could not be read"))?;
    eprintln!();
    let value = value.trim_end_matches(['\r', '\n']).to_owned();
    ensure!(
        !value.is_empty() && !value.contains('\0'),
        "a non-empty credential without NUL characters is required"
    );
    Ok(value)
}
#[cfg(unix)]
struct EchoGuard(libc::termios);
#[cfg(unix)]
impl EchoGuard {
    fn new() -> std::io::Result<Self> {
        let mut original = std::mem::MaybeUninit::uninit();
        if unsafe { libc::tcgetattr(libc::STDIN_FILENO, original.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        let original = unsafe { original.assume_init() };
        let mut hidden = original;
        hidden.c_lflag &= !(libc::ECHO | libc::ECHONL);
        if unsafe { libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, &hidden) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(Self(original))
    }
}
#[cfg(unix)]
impl Drop for EchoGuard {
    fn drop(&mut self) {
        unsafe {
            libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, &self.0);
        }
    }
}
#[cfg(windows)]
struct EchoGuard(windows_sys::Win32::Foundation::HANDLE, u32);
#[cfg(windows)]
impl EchoGuard {
    fn new() -> std::io::Result<Self> {
        use windows_sys::Win32::System::Console::*;
        let handle = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        let mut original = 0;
        if unsafe { GetConsoleMode(handle, &mut original) } == 0
            || unsafe { SetConsoleMode(handle, original & !ENABLE_ECHO_INPUT) } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        Ok(Self(handle, original))
    }
}
#[cfg(windows)]
impl Drop for EchoGuard {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::System::Console::SetConsoleMode(self.0, self.1);
        }
    }
}
fn token(reference: &str) -> Result<String> {
    ensure!(
        reference.starts_with("keychain:") && !reference[9..].trim().is_empty(),
        "1Password tokens must use a keychain:<item> reference"
    );
    let value = orgs::reference("1Password service-account token", reference)?;
    ensure!(
        value.starts_with("ops_"),
        "the credential store entry must contain a 1Password service-account token"
    );
    Ok(value)
}
fn base_env() -> Environment {
    std::env::vars()
        .filter(|(k, _)| orgs::BASE_KEYS.contains(&k.as_str()) && k != "SHELL" && k != "TERM")
        .collect()
}
struct Client {
    settings: Value,
    role: String,
    vault: String,
    directory: PathBuf,
    env: Environment,
}
impl Client {
    fn new(h: &Path, role: &str, settings: Value, credential: Option<String>) -> Result<Self> {
        let entry = &settings[role];
        ensure!(
            entry.is_object(),
            "1Password writes need a separate writer: run reckon secrets setup --writer keychain:<item>"
        );
        let credential = match credential {
            Some(v) => v,
            None => token(text(&entry["reference"]))?,
        };
        ensure!(
            sha256(&credential) == text(&entry["sha256"]),
            "the 1Password token changed; run reckon secrets setup to validate its vault access again"
        );
        let directory = local_path(&root(h)?, format!("runtime/{role}"))?;
        let mut env = base_env();
        for (child, keys) in [
            ("home", vec!["HOME", "USERPROFILE"]),
            ("config", vec!["XDG_CONFIG_HOME"]),
            ("cache", vec!["XDG_CACHE_HOME", "LOCALAPPDATA"]),
            ("state", vec!["XDG_STATE_HOME", "APPDATA"]),
            ("tmp", vec!["TMPDIR", "TMP", "TEMP"]),
        ] {
            let path = local_path(&directory, child)?;
            private_dir(&path)?;
            for key in keys {
                env.insert(key.into(), path.display().to_string());
            }
        }
        env.insert("OP_SERVICE_ACCOUNT_TOKEN".into(), credential);
        env.insert("OP_BIOMETRIC_UNLOCK_ENABLED".into(), "false".into());
        env.insert("OP_DEBUG".into(), "false".into());
        Ok(Self {
            vault: text(&settings["vault"]).into(),
            settings,
            role: role.into(),
            directory,
            env,
        })
    }
    fn call(&self, args: &[String], payload: Option<&Value>, json_output: bool) -> Result<Value> {
        let pair = args.iter().take(2).map(String::as_str).collect::<Vec<_>>();
        let read = [
            vec!["vault", "list"],
            vec!["item", "get"],
            vec!["item", "list"],
        ]
        .contains(&pair);
        let derived = args.first().is_some_and(|s| s == "read");
        let write = [vec!["item", "create"], vec!["item", "edit"]].contains(&pair);
        ensure!(
            read || derived || write && self.role == "writer",
            "unsupported 1Password operation"
        );
        if derived {
            ensure!(
                args.last()
                    .is_some_and(|s| s.starts_with(&format!("op://{}/", self.vault))),
                "1Password operations must stay inside the configured Reckon vault"
            );
        } else if pair != ["vault", "list"] {
            ensure!(
                args.windows(2)
                    .any(|a| a[0] == "--vault" && a[1] == self.vault),
                "1Password operations must stay inside the configured Reckon vault"
            );
        }
        let mut argv = vec![
            text(&self.settings["binary"]).into(),
            "--config".into(),
            self.directory.join("config").display().to_string(),
        ];
        argv.extend_from_slice(args);
        let body = payload.map(serde_json::to_vec).transpose()?;
        let done = process::run(
            &argv,
            Some(&self.env),
            Some(&self.directory),
            30.0,
            1048576,
            body.as_deref(),
        )
        .map_err(|_| {
            anyhow!("1Password request could not be completed; its output was suppressed")
        })?;
        if done.status != process::Status::Ok {
            // op errors can include credentials and JSON templates. Return only a classification.
            let detail = String::from_utf8_lossy(&done.stderr).to_lowercase();
            let mut reason = "check the service-account token, permissions and network";
            for (patterns, message) in [
                (
                    &["rate limit", "too many requests", "429"][..],
                    "service-account rate limit reached; retry later",
                ),
                (
                    &[
                        "expired",
                        "unauthorized",
                        "401",
                        "invalid service account token",
                    ][..],
                    "service-account authentication was rejected; refresh the token and run setup again",
                ),
                (
                    &[
                        "403",
                        "not authorized",
                        "not sufficient permissions",
                        "don't have permission",
                    ][..],
                    "service-account permission denied",
                ),
                (
                    &[
                        "no such host",
                        "dial tcp",
                        "network is unreachable",
                        "connection refused",
                    ][..],
                    "1Password network access is unavailable",
                ),
                (
                    &["not found", "isn't an item", "isn't a vault"][..],
                    "the selected item or vault was not found",
                ),
            ] {
                if patterns.iter().any(|p| detail.contains(p)) {
                    reason = message;
                    break;
                }
            }
            bail!("1Password request {}; {reason}", done.status.name());
        }
        let output = String::from_utf8(done.stdout).map_err(|_| {
            anyhow!("1Password returned invalid data; its contents were suppressed")
        })?;
        if json_output {
            serde_json::from_str(&output).map_err(|_| {
                anyhow!("1Password returned invalid data; its contents were suppressed")
            })
        } else {
            Ok(json!(output))
        }
    }
    fn scope(&self) -> Result<String> {
        let vaults = self.call(&words(&["vault", "list", "--format", "json"]), None, true)?;
        ensure!(
            vaults.is_array()
                && array(&vaults).len() == 1
                && vaults[0].is_object()
                && text(&vaults[0]["id"]) == self.vault
                && vaults[0]["name"].is_string(),
            "the service account must have access to exactly the configured Reckon vault"
        );
        Ok(text(&vaults[0]["name"]).into())
    }
    fn item(&self, identifier: &str, fresh: bool) -> Result<Value> {
        let mut args = words(&["item", "get", "--vault", &self.vault, "--format", "json"]);
        if fresh {
            args.push("--cache=false".into());
        }
        args.extend(["--".into(), identifier.into()]);
        let data = self.call(&args, None, true)?;
        ensure!(
            data.is_object()
                && valid_id(text(&data["id"]))
                && text(&data["vault"]["id"]) == self.vault
                && text(&data["state"]) == "ACTIVE"
                && data["fields"].is_array()
                && if valid_id(identifier) {
                    text(&data["id"]) == identifier
                } else {
                    text(&data["title"]) == identifier
                },
            "1Password did not return the selected active item in the Reckon vault"
        );
        Ok(data)
    }
}
fn words(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| (*s).into()).collect()
}

pub fn setup(
    h: &Path,
    vault: &str,
    reader: &str,
    writer: Option<&str>,
    binary: &str,
) -> Result<Value> {
    operator()?;
    ensure!(
        valid_id(vault),
        "--vault must be the Reckon vault's 26-character ID"
    );
    let executable = which(binary)
        .ok_or_else(|| anyhow!("install 1Password CLI 2.41.0 or newer, or select it with --op"))?
        .canonicalize()?;
    let existing = config(h, false)?;
    ensure!(
        existing.as_ref().is_none_or(|s| text(&s["vault"]) == vault),
        "this Reckon home is already pinned to a different vault; migrate its references before changing stores"
    );
    ensure!(
        writer != Some(reader),
        "use separate reader and writer service-account entries"
    );
    let mut settings =
        json!({"schema_version":SCHEMA,"vault":vault,"vault_name":"","binary":executable});
    let mut credentials = BTreeMap::new();
    for (role, reference) in [("reader", Some(reader)), ("writer", writer)] {
        if let Some(reference) = reference {
            let credential = token(reference)?;
            settings[role] = json!({"reference":reference,"sha256":sha256(&credential)});
            credentials.insert(role, credential);
        }
    }
    ensure!(
        writer.is_none() || credentials.get("reader") != credentials.get("writer"),
        "reader and writer must be different service-account tokens"
    );
    let done = process::run(
        &words(&[text(&settings["binary"]), "--version"]),
        Some(&base_env()),
        None,
        10.0,
        4096,
        None,
    )?;
    let output = String::from_utf8_lossy(&done.stdout);
    let version = regex::Regex::new(r"^(\d+)\.(\d+)\.(\d+)\s*$")?
        .captures(&output)
        .and_then(|c| {
            Some((
                c[1].parse::<u32>().ok()?,
                c[2].parse::<u32>().ok()?,
                c[3].parse::<u32>().ok()?,
            ))
        });
    ensure!(
        done.status == process::Status::Ok && version.is_some_and(|v| v >= (2, 41, 0)),
        "1Password CLI 2.41.0 or newer (stable) is required"
    );
    // Service-account grants are immutable. Fingerprints make token replacement fail closed.
    for (role, credential) in credentials {
        let name = Client::new(h, role, settings.clone(), Some(credential))?.scope()?;
        ensure!(
            text(&settings["vault_name"]).is_empty() || text(&settings["vault_name"]) == name,
            "reader and writer did not return the same vault"
        );
        settings["vault_name"] = json!(name);
    }
    let _lock = lock(&root(h)?)?;
    ensure!(
        config(h, false)? == existing,
        "1Password setup changed concurrently; retry setup"
    );
    write_json(&local_path(&root(h)?, "store.json")?, &settings)?;
    Ok(
        json!({"provider":"1password","vault":vault,"vault_name":settings["vault_name"],"reader":"configured","writer":if writer.is_some(){"configured"}else{"not configured"}}),
    )
}

pub struct Reference {
    pub item: String,
    pub field: Vec<String>,
    pub derived: bool,
}
pub fn parse_reference(value: &str, settings: &Value) -> Result<Reference> {
    let raw = value
        .strip_prefix("op://")
        .ok_or_else(|| anyhow!("expected a 1Password op:// reference"))?;
    let (base, query) = raw
        .split_once('?')
        .map(|(b, q)| (b, Some(q)))
        .unwrap_or((raw, None));
    if let Some(query) = query {
        let mut seen = BTreeSet::new();
        ensure!(
            !query.is_empty(),
            "unsupported 1Password reference query parameters"
        );
        for part in query.split('&') {
            ensure!(
                part.contains('='),
                "unsupported 1Password reference query parameters"
            );
        }
        for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
            let allowed = match k.as_ref() {
                "attribute" | "attr" => [
                    "type", "value", "title", "id", "purpose", "otp", "content", "size", "name",
                ]
                .contains(&v.as_ref()),
                "ssh-format" => v == "openssh",
                _ => false,
            };
            ensure!(
                allowed && seen.insert(k.into_owned()),
                "unsupported 1Password reference query parameters"
            );
        }
    }
    let parts = base.split('/').collect::<Vec<_>>();
    ensure!(
        [3, 4].contains(&parts.len())
            && parts
                .iter()
                .all(|p| !p.is_empty() && !p.chars().any(char::is_control))
            && !value.contains(['#', '\u{7f}']),
        "use a plain 1Password field reference (vault/item/[section/]field)"
    );
    ensure!(
        [text(&settings["vault"]), text(&settings["vault_name"])].contains(&parts[0]),
        "a secret reference points outside the configured Reckon vault"
    );
    Ok(Reference {
        item: parts[1].into(),
        field: parts[2..].iter().map(|s| (*s).into()).collect(),
        derived: query.is_some(),
    })
}
pub fn field<'a>(data: &'a Value, path: &[String]) -> Result<&'a Value> {
    ensure!(
        [1, 2].contains(&path.len()),
        "invalid 1Password credential field path"
    );
    let section = if path.len() == 2 {
        ensure!(
            data.get("sections").is_none_or(Value::is_array),
            "1Password returned invalid section data"
        );
        let sections = array(&data["sections"]);
        let mut matches = sections
            .iter()
            .filter(|s| text(&s["id"]) == path[0])
            .collect::<Vec<_>>();
        if matches.is_empty() {
            matches = sections
                .iter()
                .filter(|s| text(&s["label"]) == path[0])
                .collect();
        }
        ensure!(
            matches.len() == 1 && component(text(&matches[0]["id"])),
            "1Password section is missing or ambiguous"
        );
        Some(text(&matches[0]["id"]))
    } else {
        None
    };
    let fields = array(&data["fields"])
        .iter()
        .filter(|f| {
            f.is_object()
                && match section {
                    Some(s) => text(&f["section"]["id"]) == s,
                    None => !truth(&f["section"]),
                }
        })
        .collect::<Vec<_>>();
    let selected = &path[path.len() - 1];
    let mut matches = fields
        .iter()
        .copied()
        .filter(|f| text(&f["id"]) == selected)
        .collect::<Vec<_>>();
    if matches.is_empty() {
        matches = fields
            .iter()
            .copied()
            .filter(|f| text(&f["label"]) == selected)
            .collect();
    }
    ensure!(
        matches.len() == 1
            && matches[0]["value"].is_string()
            && !text(&matches[0]["value"]).is_empty()
            && !text(&matches[0]["value"]).contains('\0'),
        "1Password credential field is missing, ambiguous, empty, or unsuitable for an environment variable"
    );
    ensure!(
        component(text(&matches[0]["id"])),
        "1Password returned an invalid field ID"
    );
    Ok(matches[0])
}
fn field_path(selected: &Value) -> Vec<String> {
    let mut path = vec![];
    if truth(&selected["section"]) {
        path.push(text(&selected["section"]["id"]).into());
    }
    path.push(text(&selected["id"]).into());
    path
}
fn reference_for(vault: &str, item: &str, selected: &Value) -> String {
    format!("op://{vault}/{item}/{}", field_path(selected).join("/"))
}
pub fn resolve(h: &Path, values: &Environment) -> Result<Environment> {
    let settings = required_config(h)?;
    // Check all destinations before obtaining a token or making any network request.
    let paths = values
        .iter()
        .map(|(k, v)| Ok((k, parse_reference(v, &settings)?)))
        .collect::<Result<Vec<_>>>()?;
    let client = Client::new(h, "reader", settings, None)?;
    let mut items = BTreeMap::new();
    let mut derived = BTreeMap::new();
    let mut result = Environment::new();
    for (key, reference) in paths {
        let value = if reference.derived {
            let suffix = values[key][5..]
                .split_once('/')
                .ok_or_else(|| anyhow!("invalid reference"))?
                .1;
            let canonical = format!("op://{}/{suffix}", client.vault);
            if !derived.contains_key(&canonical) {
                derived.insert(
                    canonical.clone(),
                    client.call(&words(&["read", "--no-newline", &canonical]), None, false)?,
                );
            }
            text(&derived[&canonical]).to_owned()
        } else {
            if !items.contains_key(&reference.item) {
                items.insert(reference.item.clone(), client.item(&reference.item, false)?);
            }
            text(
                &field(&items[&reference.item], &reference.field).map_err(|_| {
                    anyhow!("{key}: could not resolve its 1Password credential field")
                })?["value"],
            )
            .into()
        };
        ensure!(
            !value.is_empty() && !value.contains('\0'),
            "{key}: 1Password returned an empty or non-text credential"
        );
        result.insert(key.clone(), value);
    }
    Ok(result)
}

struct Connection {
    name: String,
    conn: Value,
    spec: Value,
    path: PathBuf,
    raw: Vec<u8>,
    values: Environment,
    directory: PathBuf,
}
fn connection(h: &Path, ctx: &Context, target: &str, registry: &Registry) -> Result<Connection> {
    let (name, conn) = orgs::resolve(ctx, target)?;
    let spec = registry[text(&conn["provider"])].clone();
    let path = orgs::secrets_path(h, &ctx.org, &name)?;
    let raw = if path.exists() {
        read_bytes(&path, 65536)?
    } else {
        vec![]
    };
    let values = orgs::secrets(h, &ctx.org, &name, &spec)?;
    let directory = local_path(&root(h)?, format!("connections/{}/{name}", ctx.org))?;
    Ok(Connection {
        name,
        conn,
        spec,
        path,
        raw,
        values,
        directory,
    })
}
fn check_key(key: &str, spec: &Value) -> Result<()> {
    ensure!(
        regex::Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$")?.is_match(key)
            && strings(&spec["prefixes"])
                .iter()
                .any(|p| key.starts_with(p)),
        "the credential key must be a setting for this connection's provider"
    );
    Ok(())
}
fn confirm(ctx: &Context, conn: &Connection, keys: &[String], verb: &str) -> Result<()> {
    let expected = if ctx.production || conn.conn.get("env").is_none() {
        &ctx.org
    } else {
        "yes"
    };
    eprintln!(
        "{verb} {} for {}/{} in the Reckon vault",
        keys.join(", "),
        ctx.org,
        conn.name
    );
    ensure!(
        ask(&format!("Type {expected} to continue: "))? == expected,
        "secret operation cancelled"
    );
    Ok(())
}
fn replace_settings(conn: &Connection, updates: &Environment) -> Result<()> {
    let current = if conn.path.exists() {
        read_bytes(&conn.path, 65536)?
    } else {
        vec![]
    };
    ensure!(
        current == conn.raw,
        "connection settings changed during the operation; remote credentials were retained; retry"
    );
    let mut lines = vec![];
    let mut seen = BTreeSet::new();
    let regex = regex::Regex::new(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")?;
    for line in std::str::from_utf8(&conn.raw)?.lines() {
        if let Some(c) = regex.captures(line.trim())
            && let Some(value) = updates.get(&c[1])
        {
            if seen.insert(c[1].to_owned()) {
                lines.push(format!("{}={value}", &c[1]));
            }
        } else {
            lines.push(line.into());
        }
    }
    for (key, value) in updates {
        if !seen.contains(key) {
            lines.push(format!("{key}={value}"));
        }
    }
    write(&conn.path, format!("{}\n", lines.join("\n")))
}
fn managed_item(directory: &Path) -> Result<Option<String>> {
    let path = local_path(directory, "item.json")?;
    if !path.exists() {
        return Ok(None);
    }
    let data = read_json(&path)?;
    ensure!(
        data.is_object() && data["schema_version"] == SCHEMA,
        "managed 1Password item state is malformed"
    );
    ensure!(
        !truth(&data["pending_create"]),
        "an earlier item create may have succeeded; run reckon secrets recover <connection> before retrying"
    );
    if data["ready_to_create"] == true && data.get("item").is_none() {
        return Ok(None);
    }
    ensure!(
        valid_id(text(&data["item"])),
        "managed 1Password item state is malformed"
    );
    Ok(Some(text(&data["item"]).into()))
}
fn put(
    h: &Path,
    ctx: &Context,
    conn: &Connection,
    changes: &Environment,
    update_linked: bool,
) -> Result<Environment> {
    let settings = required_config(h)?;
    let existing = managed_item(&conn.directory)?;
    let mut linked = BTreeMap::new();
    for key in changes.keys() {
        if let Some(value) = conn.values.get(key).filter(|s| s.starts_with("op://")) {
            let reference = parse_reference(value, &settings)?;
            ensure!(
                !reference.derived,
                "derived 1Password references must be edited in 1Password"
            );
            linked.insert(key, reference);
        }
    }
    ensure!(
        update_linked
            || linked
                .values()
                .all(|r| Some(r.item.as_str()) == existing.as_deref()),
        "this field belongs to a linked item; use --update-linked to explicitly update it"
    );
    let items = linked
        .values()
        .map(|r| r.item.as_str())
        .collect::<BTreeSet<_>>();
    ensure!(
        items.len() <= 1
            && (existing.is_none()
                || items.is_empty()
                || items.contains(existing.as_deref().unwrap_or(""))),
        "save fields in the same connection item; update separately linked items one at a time"
    );
    let identifier = items.first().copied().or(existing.as_deref());
    let client = Client::new(h, "writer", settings, None)?;
    let mut expected = BTreeMap::new();
    let item;
    if let Some(identifier) = identifier {
        let mut data = client.item(identifier, true)?;
        ensure!(
            text(&data["category"]) == "API_CREDENTIAL"
                && !array(&data["fields"])
                    .iter()
                    .any(|f| text(&f["type"]) == "PASSKEY"),
            "only API Credential items can be updated through Reckon; edit other linked item types in 1Password"
        );
        for (key, value) in changes {
            let selected = if let Some(reference) = linked.get(key) {
                Some(field(&data, &reference.field)?.clone())
            } else {
                let matches = array(&data["fields"])
                    .iter()
                    .filter(|f| text(&f["label"]) == key && !truth(&f["section"]))
                    .collect::<Vec<_>>();
                ensure!(
                    matches.len() <= 1,
                    "the destination item has ambiguous credential fields"
                );
                matches.first().map(|f| (*f).clone())
            };
            let mut selected = selected.unwrap_or_else(|| json!({"id":&uuid::Uuid::new_v4().simple().to_string()[..26],"label":key,"type":"CONCEALED"}));
            selected["value"] = json!(value);
            selected["type"] = json!("CONCEALED");
            let fields = data["fields"]
                .as_array_mut()
                .ok_or_else(|| anyhow!("invalid item fields"))?;
            if let Some(index) = fields
                .iter()
                .position(|f| f["id"] == selected["id"] && f["section"] == selected["section"])
            {
                fields[index] = selected.clone();
            } else {
                fields.push(selected.clone());
            }
            expected.insert(key.clone(), selected);
        }
        client.call(
            &words(&[
                "item",
                "edit",
                "--vault",
                &client.vault,
                "--format",
                "json",
                "--",
                text(&data["id"]),
            ]),
            Some(&data),
            true,
        )?;
        item = text(&data["id"]).to_owned();
    } else {
        let title = format!("{} / {}", ctx.org, conn.name);
        let tag = format!(
            "reckon/{}/{}",
            ctx.org,
            conn.conn["env"].as_str().unwrap_or("shared")
        );
        // Journal the intent first. An uncertain create must never be silently repeated.
        write_json(
            &local_path(&conn.directory, "item.json")?,
            &json!({"schema_version":SCHEMA,"pending_create":true,"title":title,"tag":tag}),
        )?;
        for (key, value) in changes {
            expected.insert(key.clone(), json!({"id":&uuid::Uuid::new_v4().simple().to_string()[..26],"label":key,"type":"CONCEALED","value":value}));
        }
        let data = json!({"title":title,"category":"API_CREDENTIAL","tags":[tag],"fields":expected.values().collect::<Vec<_>>()});
        let created = client.call(
            &words(&[
                "item",
                "create",
                "--vault",
                &client.vault,
                "--format",
                "json",
                "-",
            ]),
            Some(&data),
            true,
        )?;
        ensure!(
            created.is_object() && valid_id(text(&created["id"])),
            "1Password create returned no item ID; inspect the Reckon vault and link the item before retrying"
        );
        item = text(&created["id"]).into();
        write_json(&local_path(&conn.directory, "item.json")?, &json!({"schema_version":SCHEMA,"item":item}))
            .map_err(|_| anyhow!("remote item {item} was saved; use reckon secrets link --item {item} before retrying"))?;
    }
    let fresh = client.item(&item, true)?;
    let mut refs = Environment::new();
    for (key, selected) in expected {
        let checked = field(&fresh, &field_path(&selected))?;
        ensure!(
            text(&checked["value"]) == changes[&key],
            "remote credential verification failed; connection settings were not replaced"
        );
        refs.insert(key, reference_for(&client.vault, &item, checked));
    }
    Ok(refs)
}
pub fn set_secret(
    h: &Path,
    ctx: &Context,
    target: &str,
    key: &str,
    registry: &Registry,
    update_linked: bool,
) -> Result<Value> {
    operator()?;
    let conn = connection(h, ctx, target, registry)?;
    check_key(key, &conn.spec)?;
    let settings = required_config(h)?;
    let _lock = lock(&conn.directory)?;
    ensure!(
        settings["writer"].is_object(),
        "configure a separate 1Password writer before saving credentials"
    );
    confirm(ctx, &conn, &words(&[key]), "Save")?;
    let value = hidden(&format!("Value for {key} (hidden): "))?;
    let refs = put(
        h,
        ctx,
        &conn,
        &Environment::from([(key.into(), value)]),
        update_linked,
    )?;
    replace_settings(&conn, &refs)?;
    Ok(json!({"org":ctx.org,"connection":conn.name,"saved":[key],"provider":"1password"}))
}
pub fn link(
    h: &Path,
    ctx: &Context,
    target: &str,
    key: &str,
    item: &str,
    selected: &str,
    registry: &Registry,
) -> Result<Value> {
    operator()?;
    let conn = connection(h, ctx, target, registry)?;
    check_key(key, &conn.spec)?;
    let path = selected.split('/').map(str::to_owned).collect::<Vec<_>>();
    ensure!(
        valid_id(item) && !selected.is_empty() && [1, 2].contains(&path.len()),
        "link requires --item <item-id> and --field <field-id-or-label> (optionally section/field)"
    );
    let _lock = lock(&conn.directory)?;
    let client = Client::new(h, "reader", required_config(h)?, None)?;
    let data = client.item(item, false)?;
    let chosen = field(&data, &path)?;
    confirm(ctx, &conn, &words(&[key]), "Link")?;
    replace_settings(
        &conn,
        &Environment::from([(
            key.into(),
            reference_for(&client.vault, text(&data["id"]), chosen),
        )]),
    )?;
    let journal = local_path(&conn.directory, "item.json")?;
    if journal.exists() && truth(&read_json(&journal)?["pending_create"]) {
        write_json(
            &journal,
            &json!({"schema_version":SCHEMA,"item":data["id"]}),
        )?;
    }
    Ok(json!({"org":ctx.org,"connection":conn.name,"linked":[key],"provider":"1password"}))
}
pub fn import(
    h: &Path,
    ctx: &Context,
    target: &str,
    registry: &Registry,
    keys: &[String],
    dry_run: bool,
) -> Result<Value> {
    operator()?;
    let conn = connection(h, ctx, target, registry)?;
    let selected = if keys.is_empty() {
        conn.values
            .keys()
            .filter(|k| orgs::secret_key(k))
            .cloned()
            .collect::<BTreeSet<_>>()
    } else {
        keys.iter().cloned().collect()
    };
    let mut changes = Environment::new();
    for key in selected {
        check_key(&key, &conn.spec)?;
        let value = conn
            .values
            .get(&key)
            .ok_or_else(|| anyhow!("a selected credential key is not configured"))?;
        if !value.is_empty() && !value.starts_with("op://") && !value.starts_with("keychain:") {
            changes.insert(key, value.clone());
        }
    }
    let mut summary = json!({"org":ctx.org,"connection":conn.name,"fields":changes.keys().collect::<Vec<_>>(),"dry_run":dry_run});
    if dry_run || changes.is_empty() {
        return Ok(summary);
    }
    let _lock = lock(&conn.directory)?;
    confirm(
        ctx,
        &conn,
        &changes.keys().cloned().collect::<Vec<_>>(),
        "Import",
    )?;
    let refs = put(h, ctx, &conn, &changes, false)?;
    replace_settings(&conn, &refs)?;
    summary["imported"] = json!(changes.keys().collect::<Vec<_>>());
    Ok(summary)
}
pub fn recover(h: &Path, ctx: &Context, target: &str, registry: &Registry) -> Result<Value> {
    operator()?;
    let conn = connection(h, ctx, target, registry)?;
    let _lock = lock(&conn.directory)?;
    let journal = local_path(&conn.directory, "item.json")?;
    let pending = if journal.exists() {
        read_json(&journal)?
    } else {
        Value::Null
    };
    ensure!(
        pending.is_object() && truth(&pending["pending_create"]),
        "this connection has no uncertain item create to recover"
    );
    ensure!(
        pending["title"].is_string() && pending["tag"].is_string(),
        "pending creation has no destination metadata; recover it with secrets link --item <id>"
    );
    let client = Client::new(h, "reader", required_config(h)?, None)?;
    let entries = client.call(
        &words(&[
            "item",
            "list",
            "--vault",
            &client.vault,
            "--tags",
            text(&pending["tag"]),
            "--format",
            "json",
        ]),
        None,
        true,
    )?;
    ensure!(
        entries.is_array() && array(&entries).iter().all(Value::is_object),
        "1Password returned an invalid recovery item list"
    );
    let matches = array(&entries)
        .iter()
        .filter(|e| {
            e["title"] == pending["title"]
                && text(&e["category"]) == "API_CREDENTIAL"
                && text(&e["vault"]["id"]) == client.vault
                && array(&e["tags"]).contains(&pending["tag"])
        })
        .collect::<Vec<_>>();
    ensure!(
        matches.len() <= 1,
        "several items match the interrupted create; inspect 1Password and use secrets link --item <id>"
    );
    if let Some(found) = matches.first() {
        let item = text(&found["id"]);
        ensure!(
            valid_id(item),
            "1Password returned an invalid recovery item ID"
        );
        client.item(item, true)?;
        confirm(ctx, &conn, &words(&[&format!("item {item}")]), "Recover")?;
        write_json(&journal, &json!({"schema_version":SCHEMA,"item":item}))?;
        return Ok(
            json!({"connection":conn.name,"status":"recovered","item":item,"next":"retry the original set or import command"}),
        );
    }
    eprintln!("No matching item was found. An earlier request may still complete.");
    ensure!(
        ask("Type retry to permit another create, anything else to cancel: ")? == "retry",
        "secret recovery cancelled"
    );
    write_json(
        &journal,
        &json!({"schema_version":SCHEMA,"ready_to_create":true}),
    )?;
    Ok(
        json!({"connection":conn.name,"status":"retry_enabled","next":"retry the original set or import command"}),
    )
}
pub fn status(h: &Path, ctx: Option<&Context>, registry: &Registry) -> Result<Value> {
    let settings = config(h, false)?;
    let mut rows = vec![];
    if let Some(ctx) = ctx {
        for (name, conn) in object(&ctx.data["connections"]) {
            if !ctx.env.is_empty() && !orgs::applies(&conn, &ctx.env) {
                continue;
            }
            let values = orgs::secrets(h, &ctx.org, &name, &registry[text(&conn["provider"])])?;
            let keys = values
                .iter()
                .filter(|(_, v)| v.starts_with("op://"))
                .map(|(k, _)| k.clone())
                .collect::<Vec<_>>();
            let invalid = settings
                .as_ref()
                .is_some_and(|s| keys.iter().any(|k| parse_reference(&values[k], s).is_err()));
            let state = if invalid {
                "outside_store_or_invalid"
            } else if !keys.is_empty() && settings.is_none() {
                "store_not_configured"
            } else if keys.is_empty() {
                "no_references"
            } else {
                "referenced"
            };
            rows.push(json!({"connection":name,"fields":keys,"state":state}));
        }
    }
    Ok(
        json!({"provider":"1password","configured":settings.is_some(),"vault":settings.as_ref().map(|s| &s["vault"]),
        "vault_name":settings.as_ref().map(|s| &s["vault_name"]),"writer":settings.as_ref().is_some_and(|s| s["writer"].is_object()),
        "connections":rows,"live_verified":false}),
    )
}
pub fn check(h: &Path, ctx: &Context, target: &str, registry: &Registry) -> Result<Value> {
    let conn = connection(h, ctx, target, registry)?;
    let values = conn
        .values
        .into_iter()
        .filter(|(_, v)| v.starts_with("op://"))
        .collect::<Environment>();
    if values.is_empty() {
        return Ok(json!({"connection":conn.name,"status":"no_references","fields":[]}));
    }
    resolve(h, &values)?;
    Ok(json!({"connection":conn.name,"status":"ok","fields":values.keys().collect::<Vec<_>>()}))
}

pub fn benchmark(
    h: &Path,
    ctx: &Context,
    target: &str,
    registry: &Registry,
    samples: u64,
) -> Result<Value> {
    ensure!(
        (1..=50).contains(&samples),
        "--samples must be between 1 and 50"
    );
    let conn = connection(h, ctx, target, registry)?;
    let refs = conn
        .values
        .into_iter()
        .filter(|(_, v)| v.starts_with("op://"))
        .collect::<Environment>();
    ensure!(
        !refs.is_empty(),
        "this connection has no 1Password references to benchmark"
    );
    let settings = required_config(h)?;
    let mut items = BTreeSet::new();
    for reference in refs.values() {
        let parsed = parse_reference(reference, &settings)?;
        ensure!(
            !parsed.derived,
            "benchmark ordinary text or concealed fields without query parameters"
        );
        items.insert(parsed.item);
    }
    Client::new(h, "reader", settings.clone(), None)?.scope()?;
    let mut timings = [vec![], vec![]];
    let mut first = [0.0; 2];
    for sample in 0..=samples {
        let mut results = [Environment::new(), Environment::new()];
        for mode in if sample % 2 == 0 { [0, 1] } else { [1, 0] } {
            let start = std::time::Instant::now();
            results[mode] = if mode == 0 {
                let mut values = Environment::new();
                for (key, reference) in &refs {
                    let client = Client::new(h, "reader", settings.clone(), None)?;
                    let suffix = reference[5..]
                        .split_once('/')
                        .ok_or_else(|| anyhow!("invalid reference"))?
                        .1;
                    let value = client.call(
                        &words(&[
                            "read",
                            "--no-newline",
                            &format!("op://{}/{suffix}", client.vault),
                        ]),
                        None,
                        false,
                    )?;
                    values.insert(key.clone(), text(&value).into());
                }
                values
            } else {
                resolve(h, &refs)?
            };
            let elapsed = start.elapsed().as_secs_f64() * 1000.0;
            if sample == 0 {
                first[mode] = elapsed;
            } else {
                timings[mode].push(elapsed);
            }
        }
        ensure!(
            results[0] == results[1],
            "credentials changed during the benchmark; retry with a stable dummy item"
        );
    }
    fn summary(values: &mut [f64], calls: usize) -> Value {
        values.sort_by(f64::total_cmp);
        let middle = values.len() / 2;
        let median = if values.len().is_multiple_of(2) {
            (values[middle - 1] + values[middle]) / 2.0
        } else {
            values[middle]
        };
        json!({"median_ms":median,"p95_ms":values[(values.len()*95).div_ceil(100)-1],"op_invocations_per_sample":calls})
    }
    Ok(
        json!({"mode":"configured_store","connection":conn.name,"samples":samples,"fields":refs.len(),
        "includes_os_credential_lookup":true,"first":{"per_field_ms":first[0],"per_item_ms":first[1]},
        "subsequent":{"per_field":summary(&mut timings[0],refs.len()),"per_item":summary(&mut timings[1],items.len())}}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> Value {
        json!({"schema_version":SCHEMA,"vault":"vvvvvvvvvvvvvvvvvvvvvvvvvv","vault_name":"Reckon","binary":"/missing/op",
            "reader":{"reference":"keychain:reader","sha256":sha256("ops_reader")},
            "writer":{"reference":"keychain:writer","sha256":sha256("ops_writer")}})
    }
    #[test]
    fn references_cannot_escape_the_selected_vault() -> Result<()> {
        let settings = settings();
        for reference in [
            "op://Other/item/token",
            "op://Reckon/item/token#fragment",
            "op://Reckon/item/token?account=personal",
            "op://Reckon/item/token?attribute=value&attribute=id",
            "op://Reckon/item/token?ssh-format=pem",
            "op://Reckon//token",
            "op://Reckon/item/token?",
            "op://Reckon/item/token?attribute",
            "op://Reckon/item/to\0ken",
        ] {
            assert!(
                parse_reference(reference, &settings).is_err(),
                "{reference}"
            );
        }
        let parsed = parse_reference(
            "op://Reckon/Existing item/Authentication/token?attribute=otp",
            &settings,
        )?;
        assert_eq!(parsed.item, "Existing item");
        assert_eq!(parsed.field, words(&["Authentication", "token"]));
        assert!(parsed.derived);
        Ok(())
    }
    #[test]
    fn fields_are_selected_by_section_and_reject_ambiguity_or_nontext_values() -> Result<()> {
        let mut data = json!({"fields":[{"id":"field","label":"Token","value":"dummy","section":{"id":"auth"}}],
            "sections":[{"id":"auth","label":"Authentication"}]});
        assert!(field(&data, &words(&["Token"])).is_err());
        assert_eq!(
            field(&data, &words(&["Authentication", "Token"]))?["value"],
            "dummy"
        );
        let duplicate = data["fields"][0].clone();
        data["fields"]
            .as_array_mut()
            .expect("array")
            .push(duplicate);
        assert!(field(&data, &words(&["auth", "field"])).is_err());
        for value in [json!(""), json!("nul\0value"), json!(123), Value::Null] {
            let data = json!({"fields":[{"id":"field","label":"Token","value":value}]});
            assert!(field(&data, &words(&["field"])).is_err());
        }
        Ok(())
    }
    #[test]
    fn reader_cannot_write_and_no_role_can_manage_vaults_share_move_or_delete() -> Result<()> {
        let home = tempfile::tempdir()?;
        let reader = Client::new(home.path(), "reader", settings(), Some("ops_reader".into()))?;
        for args in [
            words(&["item", "edit", "--vault", &reader.vault, "--", "item"]),
            words(&["vault", "create", "Other"]),
            words(&["item", "delete", "item"]),
            words(&["item", "share", "item"]),
            words(&["read", "op://Other/item/field"]),
        ] {
            assert!(reader.call(&args, None, true).is_err());
        }
        let writer = Client::new(home.path(), "writer", settings(), Some("ops_writer".into()))?;
        for args in [
            words(&["item", "create", "--vault", "Other", "-"]),
            words(&["item", "move", "item"]),
            words(&["item", "delete", "item"]),
        ] {
            assert!(writer.call(&args, None, true).is_err());
        }
        assert!(
            Client::new(
                home.path(),
                "reader",
                settings(),
                Some("ops_replaced".into())
            )
            .is_err()
        );
        Ok(())
    }
    #[test]
    fn malformed_store_config_never_echoes_raw_contents() -> Result<()> {
        let home = tempfile::tempdir()?;
        let path = root(home.path())?.join("store.json");
        write(&path, "ops_sensitive_not_json")?;
        let error = config(home.path(), true)
            .err()
            .ok_or_else(|| anyhow!("expected malformed config"))?
            .to_string();
        assert_eq!(error, "1Password configuration is malformed");
        let mut data = settings();
        data["reader"]["reference"] = json!("op://Other/token/value");
        write_json(&path, &data)?;
        assert!(config(home.path(), true).is_err());
        Ok(())
    }
}
