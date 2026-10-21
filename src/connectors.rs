use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
use process::{Environment, Status};
pub fn tokens(args: &[String], spec: &Value) -> Vec<String> {
    args.iter()
        .take_while(|a| !a.starts_with('-'))
        .map(|a| {
            let a = a.to_lowercase();
            spec["aliases"][&a].as_str().unwrap_or(&a).to_owned()
        })
        .collect()
}
pub fn matches(pattern: &[String], words: &[String]) -> bool {
    words.len() >= pattern.len()
        && pattern
            .iter()
            .zip(words)
            .all(|(p, w)| glob_match(&p.to_lowercase(), w))
}
fn patterns(spec: &Value, key: &str, words: &[String]) -> bool {
    array(&spec[key])
        .iter()
        .any(|p| matches(&strings(p), words))
}
fn long_name(arg: &str, spec: &Value) -> String {
    let mut name = arg.split('=').next().unwrap_or("").to_owned();
    if truth(&spec["option_aliases"]) {
        name = name.replace('_', "-");
        for prefix in ["loose-", "maximum-", "enable-", "disable-", "skip-"] {
            if let Some(rest) = name.strip_prefix(&format!("--{prefix}")) {
                name = format!("--{rest}");
            }
        }
    }
    name
}
fn expands(name: &str, flag: &str, spec: &Value) -> bool {
    name == flag || (truth(&spec["abbreviations"]) && name.len() > 2 && flag.starts_with(name))
}
fn cluster(arg: &str, spec: &Value) -> (Vec<char>, Option<String>) {
    let mut flags = vec![];
    for (i, c) in arg.char_indices().skip(1) {
        flags.push(c);
        if text(&spec["short_values"]).contains(c) {
            let rest = &arg[i + c.len_utf8()..];
            return (flags, (!rest.is_empty()).then(|| rest.to_owned()));
        }
    }
    (flags, None)
}
pub fn flag_problem(arg: &str, spec: &Value) -> Option<String> {
    if arg.starts_with("--") && arg.len() > 2 {
        let name = long_name(arg, spec);
        for flag in strings(&spec["deny_args"]) {
            if flag.starts_with("--") && expands(&name, &flag, spec) {
                return Some(flag);
            }
        }
        for prefix in strings(&spec["deny_prefixes"]) {
            if name.starts_with(&prefix) {
                return Some(prefix);
            }
        }
    } else if arg.starts_with('-') && arg.len() > 1 {
        let shorts = strings(&spec["deny_args"])
            .into_iter()
            .filter(|f| f.len() == 2 && f != "--")
            .collect::<Vec<_>>();
        for c in cluster(arg, spec).0 {
            let f = format!("-{c}");
            if shorts.contains(&f) {
                return Some(f);
            }
        }
    }
    None
}
pub fn operands(
    args: &[String],
    spec: &Value,
    long_values: &[String],
) -> (Vec<(String, Option<String>)>, Vec<String>) {
    let (mut pairs, mut positional, mut i) = (vec![], vec![], 0);
    while i < args.len() {
        let arg = &args[i];
        if arg.starts_with("--") && arg.len() > 2 {
            let name = long_name(arg, spec);
            let owner = long_values.iter().find(|f| expands(&name, f, spec));
            if let Some((_, v)) = arg.split_once('=') {
                pairs.push((owner.cloned().unwrap_or(name), Some(v.into())));
            } else if let Some(owner) = owner
                && i + 1 < args.len()
            {
                i += 1;
                pairs.push((owner.clone(), Some(args[i].clone())));
            }
        } else if arg.starts_with('-') && arg.len() > 1 {
            let (flags, mut attached) = cluster(arg, spec);
            if let Some(c) = flags.last()
                && text(&spec["short_values"]).contains(*c)
            {
                if attached.is_none() && i + 1 < args.len() {
                    i += 1;
                    attached = Some(args[i].clone());
                }
                pairs.push((format!("-{c}"), attached));
            }
        } else {
            positional.push(arg.clone());
        }
        i += 1;
    }
    (pairs, positional)
}
fn with_command_values(spec: &Value, args: &[String]) -> Value {
    let mut spec = spec.clone();
    let words = tokens(args, &spec);
    let extra = object(&spec["command_short_values"])
        .into_iter()
        .filter(|(path, _)| {
            matches(
                &path
                    .split_whitespace()
                    .map(str::to_owned)
                    .collect::<Vec<_>>(),
                &words,
            )
        })
        .map(|(_, v)| text(&v).to_owned())
        .collect::<String>();
    spec["short_values"] = json!(format!("{}{extra}", text(&spec["short_values"])));
    spec
}
pub fn check(provider: &str, spec: &Value, args: &[String]) -> Result<()> {
    let spec = with_command_values(spec, args);
    ensure!(
        text(&spec["mode"]) != "typed",
        "{provider} has no reckon run surface; use its typed operations (reckon capabilities)"
    );
    for arg in args {
        ensure!(
            !arg.contains('\0') && arg.len() <= 65536,
            "invalid argument"
        );
        if let Some(flag) = flag_problem(arg, &spec) {
            bail!("{flag} is fixed by the connection and cannot be passed through reckon run");
        }
    }
    let help = args.len() == 1 && ["--help", "-h", "--version"].contains(&args[0].as_str());
    if (truth(&spec["deny_commands"]) || truth(&spec["read_commands"]))
        && args.first().is_some_and(|s| s.starts_with('-'))
        && !help
    {
        bail!(
            "put the {provider} command before any flags, e.g. reckon run {provider} -- <command> <args> -o json"
        );
    }
    let words = tokens(args, &spec);
    if truth(&spec["read_commands"]) {
        ensure!(
            patterns(&spec, "read_commands", &words),
            "{provider} allows only these read commands through reckon run: {} (put the command before any flags)",
            array(&spec["read_commands"])
                .iter()
                .map(|p| strings(p).join(" "))
                .collect::<Vec<_>>()
                .join("; ")
        );
    }
    let positional = operands(args, &spec, &[])
        .1
        .into_iter()
        .map(|s| {
            let lower = s.to_lowercase();
            spec["aliases"][&lower]
                .as_str()
                .unwrap_or(&lower)
                .to_owned()
        })
        .collect::<Vec<_>>();
    for p in array(&spec["deny_commands"]) {
        let p = strings(p);
        ensure!(
            !matches(&p, &words) && !matches(&p, &positional),
            "'{}' is not allowed through reckon run",
            p.join(" ")
        );
    }
    let required = text(&spec["require_short"]);
    if !required.is_empty() {
        let mut found = false;
        let mut i = 0;
        while i < args.len() {
            let a = &args[i];
            if a.starts_with('-') && !a.starts_with("--") {
                let (flags, attached) = cluster(a, &spec);
                found |= flags.iter().any(|c| required.contains(*c));
                if flags
                    .last()
                    .is_some_and(|c| text(&spec["short_values"]).contains(*c))
                    && attached.is_none()
                {
                    i += 1;
                }
            }
            i += 1;
        }
        ensure!(
            found,
            "{provider} needs an explicit read mode: one of {}",
            required
                .chars()
                .map(|c| format!("-{c}"))
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    if provider == "kubernetes" && words.first().is_some_and(|w| w == "get" || w == "describe") {
        let long="--namespace --selector --output --sort-by --field-selector --label-columns --container --since --since-time --tail --template --chunk-size --request-timeout".split_whitespace().map(str::to_owned).collect::<Vec<_>>();
        for w in operands(args, &spec, &long).1.iter().skip(1) {
            for p in w.split(',') {
                let kind = p.split('/').next().unwrap_or("").to_lowercase();
                ensure!(
                    !["secret", "secrets"].contains(&kind.split('.').next().unwrap_or("")),
                    "Kubernetes secrets are not readable through reckon run"
                );
            }
        }
    }
    if provider == "postgres" {
        let long="--command --dbname --file --set --variable --output --pset --table-attr --field-separator --record-separator --log-file".split_whitespace().map(str::to_owned).collect::<Vec<_>>();
        let (pairs, pos) = operands(args, &spec, &long);
        ensure!(
            pos.len() <= 1,
            "psql takes at most a database name here; the user is fixed by the connection"
        );
        for v in pos.into_iter().chain(
            pairs
                .into_iter()
                .filter(|(f, _)| f == "-d" || f == "--dbname")
                .filter_map(|(_, v)| v),
        ) {
            ensure!(
                !v.contains("://") && !v.contains('='),
                "connection strings are fixed by the connection and cannot be passed through reckon run"
            );
        }
    }
    Ok(())
}
fn selectors(arg: &str) -> Vec<(String, String, String)> {
    let re = regex::Regex::new(r#"([A-Za-z_][\w.]*)\s*(=~|!~|!=|=|:)\s*"?([^",}\s)]*)"?"#)
        .expect("constant regex");
    re.captures_iter(arg)
        .map(|c| (c[1].into(), c[2].into(), c[3].into()))
        .collect()
}
pub fn scope(spec: &Value, args: &[String], label: &str) -> Result<Vec<String>> {
    if label.is_empty() {
        return Ok(args.to_vec());
    }
    ensure!(
        !args.iter().any(|a| a == "--"),
        "-- is not allowed on a shared backend; pass every flag before it"
    );
    let words = tokens(args, spec);
    let keys = strings(&spec["env_label_keys"]);
    let flag = text(&spec["env_label_flag"]);
    let (mut given, mut positive) = (false, false);
    if words
        .first()
        .is_some_and(|w| strings(&spec["env_label_families"]).contains(w))
        && words.len() < 2
        && args.len() > 1
    {
        bail!(
            "put the full command first on this connection, e.g. {} query ... -o json",
            words[0]
        );
    }
    for (i, arg) in args.iter().enumerate() {
        let next = args.get(i + 1).map(String::as_str).unwrap_or("");
        if !flag.is_empty() && (arg == flag || arg.starts_with(&format!("{flag}="))) {
            given = true;
            ensure!(
                arg.split_once('=').map(|(_, v)| v).unwrap_or(next) == label,
                "{flag} must be {label} on this connection"
            );
        }
        if arg == "--tags" || arg.starts_with("--tags=") {
            let v = arg.strip_prefix("--tags=").unwrap_or(next);
            let (k, v) = v.split_once('=').unwrap_or((v, ""));
            if strings(&spec["env_label_tags"]).contains(&k.trim().to_owned()) {
                given = true;
                ensure!(
                    v == label,
                    "--tags {k}={v} must use {label} on this connection"
                );
            }
        }
        for (k, op, v) in selectors(arg) {
            if keys.contains(&k) {
                ensure!(
                    v == label && op != "!=" && op != "!~",
                    "selector {k}{op}{v} reaches outside {label}; this connection is on {label}"
                );
                positive = true;
            }
        }
    }
    let mut out = args.to_vec();
    if !given && patterns(spec, "env_injected_commands", &words) {
        out.extend([flag.into(), label.into()]);
        return Ok(out);
    }
    if patterns(spec, "env_scoped_commands", &words) {
        ensure!(
            positive,
            "this backend holds every environment: select {label} in the query, e.g. {{env=\"{label}\"}} in PromQL or --stream '{{env=\"{label}\"}}' for logs"
        );
        let streamed = args
            .iter()
            .enumerate()
            .filter_map(|(i, a)| {
                if a == "--stream" {
                    args.get(i + 1).map(String::as_str)
                } else {
                    a.strip_prefix("--stream=")
                }
            })
            .any(|s| {
                selectors(s)
                    .iter()
                    .any(|(k, _, v)| keys.contains(k) && v == label)
            })
            && !args.iter().any(|a| a.contains('|'));
        let re = regex::Regex::new(r"(?i)(?:^|[^\w-])(or|unless)(?:$|[^\w-])")?;
        ensure!(
            streamed || !args.iter().any(|a| re.is_match(a)),
            "or/unless can read past the {label} selector; for logs put it in --stream '{{env=\"{label}\"}}', which applies to the whole query"
        );
    }
    Ok(out)
}
fn ascii_host(host: &str) -> Option<String> {
    let host = host.trim_end_matches('.').to_lowercase();
    if host
        .chars()
        .any(|c| ['ß', 'ς', '\u{200c}', '\u{200d}'].contains(&c))
    {
        return None;
    }
    let mut labels = vec![];
    for label in host.split('.') {
        let ascii = idna::domain_to_ascii_strict(label).ok()?;
        if !label.is_ascii() && idna::domain_to_unicode(&ascii).0 != label {
            return None;
        }
        labels.push(ascii);
    }
    let ascii = labels.join(".");
    regex::Regex::new(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
        .ok()?
        .is_match(&ascii)
        .then_some(ascii)
}
fn site_parts(value: &str) -> Option<(String, String, String)> {
    if value.contains('\\') || value.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return None;
    }
    if let Some(v) = value.strip_prefix("sc-domain:") {
        return Some(("domain".into(), ascii_host(v)?, String::new()));
    }
    if !value.contains("://") {
        return Some(("host".into(), ascii_host(value)?, String::new()));
    }
    let url = url::Url::parse(value).ok()?;
    if !["http", "https"].contains(&url.scheme())
        || value.contains('#')
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port() == Some(0)
    {
        return None;
    }
    // Validate the original authority before URL normalization can map it to another host.
    let authority = value.split_once("://")?.1.split(['/', '?', '#']).next()?;
    if authority.contains('@') {
        return None;
    }
    let raw_host = authority
        .rsplit_once(':')
        .map(|(host, _)| host)
        .unwrap_or(authority);
    let host = ascii_host(raw_host)?;
    let port = url.port().map(|p| format!(":{p}")).unwrap_or_default();
    let query = url.query().map(|q| format!("?{q}")).unwrap_or_default();
    let prefix = format!("{}://{host}{port}{}{query}", url.scheme(), url.path());
    Some(("url".into(), host, prefix))
}
pub fn site_allowed(value: &str, pattern: &str) -> bool {
    let Some(got) = site_parts(value) else {
        return false;
    };
    if pattern.contains(['*', '?', '[']) {
        return glob_match(pattern, value);
    }
    let Some(allowed) = site_parts(pattern) else {
        return false;
    };
    match allowed.0.as_str() {
        "domain" => got.1 == allowed.1 || got.1.ends_with(&format!(".{}", allowed.1)),
        "url" => got.0 == "url" && got.2.starts_with(&allowed.2),
        _ => got == allowed,
    }
}
pub fn bind(spec: &Value, args: &[String], conn: &Value, values: &Environment) -> Result<()> {
    let words = tokens(args, spec);
    for (kind, allowed) in object(&conn["resources"]) {
        let allowed = strings(&allowed);
        let rule = &spec["resources"][&kind];
        for p in array(&rule["refuse_commands"]) {
            ensure!(
                !matches(&strings(p), &words),
                "'{}' cannot be limited to {kind} {} on this connection",
                strings(p).join(" "),
                allowed.join(", ")
            );
        }
        for value in strings(&rule["refuse_text"]) {
            ensure!(
                !args.iter().any(|a| a.contains(&value)),
                "'{value}' can select another {kind}; name the {kind} with {}",
                text(&rule["flags"][0])
            );
        }
        for flag in strings(&rule["refuse_flags"]) {
            ensure!(
                !args
                    .iter()
                    .filter(|a| a.starts_with('-') && (flag.len() == 2) == !a.starts_with("--"))
                    .any(|a| a == &flag
                        || a.starts_with(&format!("{flag}="))
                        || (flag.len() == 2
                            && cluster(a, spec)
                                .0
                                .contains(&flag.chars().nth(1).unwrap_or('\0')))),
                "{flag} reaches beyond {kind} {}",
                allowed.join(", ")
            );
        }
        let flags = strings(&rule["flags"]);
        let (pairs, pos) = operands(
            args,
            spec,
            &flags
                .iter()
                .filter(|f| f.starts_with("--"))
                .cloned()
                .collect::<Vec<_>>(),
        );
        let mut chosen = pairs
            .into_iter()
            .filter(|(f, _)| flags.contains(f))
            .filter_map(|(_, v)| v)
            .flat_map(|v| {
                v.split(',')
                    .map(|p| p.trim().to_owned())
                    .collect::<Vec<_>>()
            })
            .collect::<Vec<_>>();
        for key in strings(&rule["default_env"]) {
            if let Some(v) = values.get(&key).filter(|v| !v.is_empty()) {
                chosen.push(v.clone());
            }
        }
        if let Some(pattern) = rule["positional"].as_str() {
            let re = regex::Regex::new(pattern)?;
            chosen.extend(pos.iter().filter(|p| re.is_match(p)).cloned());
        }
        let kinds = strings(&rule["refuse_kinds"]);
        for w in pos.iter().skip(1) {
            for p in w.split(',') {
                let kind_ = p.split('/').next().unwrap_or("").to_lowercase();
                ensure!(
                    !kinds.contains(&kind_.split('.').next().unwrap_or("").to_owned()),
                    "{p} is cluster-wide and cannot be limited to {kind} {}",
                    allowed.join(", ")
                );
            }
        }
        ensure!(
            !chosen.is_empty(),
            "this connection is limited to {kind} {}; name it with {}",
            allowed.join(", "),
            text(&rule["flags"][0])
        );
        for value in chosen {
            ensure!(
                allowed.iter().any(|p| if kind == "site" {
                    site_allowed(&value, p)
                } else {
                    glob_match(p, &value)
                }),
                "{kind} {value} is outside this connection (allowed: {})",
                allowed.join(", ")
            );
        }
    }
    Ok(())
}
pub fn extra_args(provider: &str, values: &Environment, config: &Path) -> Result<Vec<String>> {
    let get = |k: &str| values.get(k).cloned().unwrap_or_default();
    let default = |k: &str, d: &str| values.get(k).cloned().unwrap_or_else(|| d.into());
    let mut args = vec![];
    match provider {
        "mysql" => {
            let p = local_path(config, "mysql/my.cnf")?;
            let mut lines = vec![
                "[client]".into(),
                "init-command=SET SESSION TRANSACTION READ ONLY".into(),
            ];
            for (k, v) in [("user", "MYSQL_USER"), ("database", "MYSQL_DATABASE")] {
                if !get(v).is_empty() {
                    lines.push(format!("{k}={}", get(v)));
                }
            }
            write(&p, format!("{}\n", lines.join("\n")))?;
            args.push(format!("--defaults-extra-file={}", p.display()));
        }
        "clickhouse" => {
            args = vec![
                "--host".into(),
                get("CLICKHOUSE_HOST"),
                "--port".into(),
                default("CLICKHOUSE_PORT", "9440"),
                "--user".into(),
                get("CLICKHOUSE_USER"),
                "--password".into(),
                get("CLICKHOUSE_PASSWORD"),
                "--readonly=1".into(),
            ];
            if default("CLICKHOUSE_SECURE", "1") != "0" {
                args.push("--secure".into());
            }
            if !get("CLICKHOUSE_DATABASE").is_empty() {
                args.extend(["--database".into(), get("CLICKHOUSE_DATABASE")]);
            }
        }
        "kafka" => {
            args = vec!["-b".into(), get("KAFKA_BOOTSTRAP_SERVERS")];
            for (key, option) in [
                ("SECURITY_PROTOCOL", "security.protocol"),
                ("SASL_MECHANISM", "sasl.mechanism"),
                ("SASL_USERNAME", "sasl.username"),
                ("SASL_PASSWORD", "sasl.password"),
            ] {
                let v = get(&format!("KAFKA_{key}"));
                if !v.is_empty() {
                    args.extend(["-X".into(), format!("{option}={v}")]);
                }
            }
        }
        "redis" => args = vec!["-u".into(), get("REDIS_URL")],
        "mongodb" => args = vec![get("MONGODB_URI")],
        _ => {}
    }
    Ok(args)
}
pub struct Prepared {
    pub connection: String,
    pub conn: Value,
    pub spec: Value,
    pub env: Environment,
    pub values: orgs::ResolvedSettings,
}
pub fn prepare(
    h: &Path,
    ctx: &Context,
    target: &str,
    registry: &Registry,
    interactive: bool,
    values: Option<&orgs::ResolvedSettings>,
) -> Result<Prepared> {
    let (connection, conn) = orgs::resolve(ctx, target)?;
    let spec = registry[text(&conn["provider"])].clone();
    let (mut env, values) =
        orgs::child_env(h, ctx, &connection, &conn, &spec, interactive, values)?;
    if text(&conn["provider"]) == "kafka-rpk" {
        for (t, s) in [
            ("RPK_BROKERS", "KAFKA_BOOTSTRAP_SERVERS"),
            ("RPK_USER", "KAFKA_SASL_USERNAME"),
            ("RPK_PASS", "KAFKA_SASL_PASSWORD"),
            ("RPK_SASL_MECHANISM", "KAFKA_SASL_MECHANISM"),
        ] {
            if let Some(v) = values.get(s) {
                env.entry(t.into()).or_insert_with(|| v.clone());
            }
        }
        if values
            .get("KAFKA_SECURITY_PROTOCOL")
            .is_some_and(|v| v == "SSL" || v == "SASL_SSL")
        {
            env.entry("RPK_TLS_ENABLED".into())
                .or_insert_with(|| "true".into());
        }
    }
    ensure!(
        which(text(&spec["binary"])).is_some(),
        "{} is not installed or not on PATH (needed by {connection})",
        text(&spec["binary"])
    );
    Ok(Prepared {
        connection,
        conn,
        spec,
        env,
        values,
    })
}
pub fn command(h: &Path, ctx: &Context, p: &Prepared, args: &[String]) -> Result<Vec<String>> {
    let mut argv = vec![text(&p.spec["binary"]).into()];
    argv.extend(strings(&p.spec["subcommand"]));
    argv.extend(extra_args(
        text(&p.conn["provider"]),
        &p.values,
        &local_path(
            &orgs::org_path(h, &ctx.org)?,
            format!("config/{}", p.connection),
        )?,
    )?);
    argv.extend_from_slice(args);
    Ok(argv)
}
pub struct Outcome {
    pub connection: String,
    pub provider: String,
    pub argv: Vec<String>,
    pub values: orgs::ResolvedSettings,
    pub result: process::Output,
    pub spec: Value,
    pub cached_at: Option<String>,
    pub quota_note: Option<String>,
}
pub fn resolved(spec: &Value, env: &Environment, args: &[String]) -> Result<Value> {
    let mut argv = vec![text(&spec["binary"]).into()];
    argv.extend(strings(&spec["resolver"]));
    argv.extend_from_slice(args);
    let result = process::run(
        &argv,
        Some(env),
        env.get("TMPDIR").map(Path::new),
        10.0,
        262144,
        None,
    )?;
    let combined = format!(
        "{}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    let answer: Value = serde_json::from_slice(if result.stdout.is_empty() {
        &result.stderr
    } else {
        &result.stdout
    })
    .unwrap_or(Value::Null);
    if result.status != Status::Ok {
        if combined.contains("unknown command")
            && (combined.contains("commands") || combined.contains("resolve"))
        {
            return Ok(Value::Null);
        }
        bail!(
            "{} could not resolve these arguments: {}",
            text(&spec["binary"]),
            answer["error"].as_str().unwrap_or(result.status.name())
        );
    }
    if !answer.is_object() || answer.get("effect").is_none() {
        return Ok(Value::Null);
    }
    ensure!(
        !truth(&answer["secret_output"]),
        "{} prints a credential and is not allowed through reckon run",
        answer["command"].as_str().unwrap_or("this command")
    );
    ensure!(
        text(&answer["effect"]) == "read",
        "{} has effect {}; reckon run only reads",
        answer["command"].as_str().unwrap_or("this command"),
        text(&answer["effect"])
    );
    Ok(answer)
}
pub fn quota(
    h: &Path,
    ctx: &Context,
    cname: &str,
    spec: &Value,
    args: &[String],
) -> Result<Option<String>> {
    let rule = &spec["quota"];
    let words = tokens(args, spec);
    if !truth(rule)
        || !array(&rule["commands"]).iter().any(|c| {
            matches(&strings(&c["command"]), &words)
                && (!truth(&c["exact"]) || words.len() == array(&c["command"]).len())
                && (!truth(&c["flag"])
                    || args.iter().any(|a| {
                        a == text(&c["flag"]) || a.starts_with(&format!("{}=", text(&c["flag"])))
                    }))
        })
    {
        return Ok(None);
    }
    let folder = local_path(&orgs::org_path(h, &ctx.org)?, format!("runtime/{cname}"))?;
    let _lock = lock(&folder)?;
    let path = folder.join("quota.json");
    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let data = if path.is_file() {
        read_json(&path)?
    } else {
        json!({})
    };
    let used = if text(&data["day"]) == today {
        data["used"].as_u64().unwrap_or(0)
    } else {
        0
    };
    let limit = rule["per_day"].as_u64().unwrap_or(0);
    ensure!(
        used < limit,
        "{cname} has used its {limit} {} for {today} (UTC); reuse saved evidence or a --cache result, or wait for 00:00 UTC",
        text(&rule["unit"])
    );
    write_json(&path, &json!({"day":today,"used":used+1,"per_day":limit}))?;
    let left = limit - used - 1;
    Ok((left <= 2).then(|| {
        format!(
            "{cname}: {left} of {limit} {} left today (UTC)",
            text(&rule["unit"])
        )
    }))
}
pub struct RunOptions<'a> {
    pub timeout: f64,
    pub max_bytes: usize,
    pub stdin: Option<&'a [u8]>,
    pub cache_seconds: u64,
}
pub fn run(
    h: &Path,
    ctx: &Context,
    target: &str,
    args: &[String],
    registry: &Registry,
    options: RunOptions<'_>,
) -> Result<Outcome> {
    let RunOptions {
        timeout,
        max_bytes,
        stdin,
        cache_seconds,
    } = options;

    let (_, selected) = orgs::resolve(ctx, target)?;
    check(
        text(&selected["provider"]),
        &registry[text(&selected["provider"])],
        args,
    )?;
    let p = prepare(h, ctx, target, registry, false, None)?;
    ensure!(
        orgs::configured(h, &ctx.org, &p.connection, &p.spec, Some(&p.values))?,
        "{} has no credentials yet: edit {}{}",
        p.connection,
        orgs::secrets_path(h, &ctx.org, &p.connection)?.display(),
        if truth(&p.spec["login"]) {
            format!(" or run reckon login {} in your own terminal", p.connection)
        } else {
            String::new()
        }
    );
    let (args, argv) = (|| -> Result<_> {
        if truth(&p.spec["resolver"]) {
            resolved(&p.spec, &p.env, args)?;
        }
        bind(&p.spec, args, &p.conn, &p.values)?;
        let args = scope(&p.spec, args, text(&p.conn["env_labels"][&ctx.env]))?;
        let argv = command(h, ctx, &p, &args)?;
        Ok((args, argv))
    })()
    .map_err(|e| anyhow!(orgs::redact(&e.to_string(), &p.values)))?;
    let key = hash_json(&json!([
        ctx.org,
        ctx.env,
        p.connection,
        p.conn,
        p.values
            .iter()
            .map(|(k, v)| json!([k, v]))
            .collect::<Vec<_>>(),
        argv,
        String::from_utf8_lossy(stdin.unwrap_or(&[]))
    ]));
    let cache = local_path(&orgs::org_path(h, &ctx.org)?, format!("cache/{key}.json"))?;
    let mut cached_at = None;
    let mut quota_note = None;
    let hit = if cache_seconds > 0 && cache.is_file() {
        let entry = read_json(&cache)?;
        let age = chrono::Utc::now()
            .signed_duration_since(instant(text(&entry["observed_at"]))?)
            .num_milliseconds();
        (age >= 0 && age as u64 <= cache_seconds.saturating_mul(1000)).then_some(entry)
    } else {
        None
    };
    let result = if let Some(hit) = hit {
        cached_at = Some(text(&hit["observed_at"]).into());
        process::Output {
            status: Status::Ok,
            exit_code: hit["exit_code"].as_i64().map(|v| v as i32),
            duration_ms: hit["duration_ms"].as_u64().unwrap_or(0) as u128,
            stdout: text(&hit["stdout"]).as_bytes().to_vec(),
            stderr: text(&hit["stderr"]).as_bytes().to_vec(),
        }
    } else {
        quota_note = quota(h, ctx, &p.connection, &p.spec, &args)?;
        let cwd = if truth(&p.spec["isolated_cwd"]) {
            PathBuf::from(&p.env["TMPDIR"])
        } else {
            std::env::current_dir()?
        };
        let result = process::run(&argv, Some(&p.env), Some(&cwd), timeout, max_bytes, stdin)?;
        if result.status == Status::Ok {
            write_json(
                &cache,
                &json!({"observed_at":now(),"exit_code":result.exit_code,"duration_ms":result.duration_ms as u64,"stdout":orgs::redact(&String::from_utf8_lossy(&result.stdout),&p.values),"stderr":orgs::redact(&String::from_utf8_lossy(&result.stderr),&p.values)}),
            )?;
        }
        result
    };
    Ok(Outcome {
        connection: p.connection,
        provider: text(&p.conn["provider"]).into(),
        argv,
        values: p.values,
        result,
        spec: p.spec,
        cached_at,
        quota_note,
    })
}
pub fn probe(h: &Path, ctx: &Context, p: &Prepared) -> Result<process::Output> {
    process::run(
        &command(h, ctx, p, &strings(&p.spec["verify"]))?,
        Some(&p.env),
        p.env.get("TMPDIR").map(Path::new),
        20.0,
        262144,
        None,
    )
}
pub fn account(spec: &Value, result: &process::Output) -> Option<String> {
    if !truth(&spec["identity"]) || result.status != Status::Ok {
        return None;
    }
    let data: Value = serde_json::from_slice(&result.stdout).ok()?;
    let paths = if spec["identity"].is_array() {
        strings(&spec["identity"])
    } else {
        vec![text(&spec["identity"]).into()]
    };
    for path in paths {
        let v = pick(&data, &path);
        let value = if v.is_string() {
            text(v).to_owned()
        } else if v.is_i64() || v.is_u64() {
            v.to_string()
        } else {
            continue;
        };
        if !value.trim().is_empty() {
            return Some(value.trim().into());
        }
    }
    None
}
pub fn shown(seen: Option<&str>, values: &impl orgs::Redactable) -> Option<String> {
    seen.map(|s| orgs::redact(s, values).chars().take(200).collect())
}
pub fn account_problem(
    cname: &str,
    conn: &Value,
    seen: Option<&str>,
    values: &impl orgs::Redactable,
) -> Option<String> {
    let expected = text(&conn["account"]);
    if expected.is_empty() {
        return None;
    }
    let Some(seen) = seen else {
        return Some(format!(
            "{cname} did not report which account it uses, so it cannot be confirmed as {expected}"
        ));
    };
    if !glob_match(&expected.to_lowercase(), &seen.to_lowercase()) {
        let shown = shown(Some(seen), values).unwrap_or_default();
        return Some(format!(
            "{cname} is logged in as {shown}, not {expected} (if {shown} is right: reckon connect {cname} --account {shown})"
        ));
    }
    None
}
pub fn whoami(
    h: &Path,
    ctx: &Context,
    cname: &str,
    conn: &Value,
    registry: &Registry,
    values: Option<&orgs::ResolvedSettings>,
) -> Result<Option<String>> {
    if !truth(&conn["account"]) {
        return Ok(None);
    }
    let p = prepare(h, ctx, cname, registry, false, values)?;
    ensure!(
        truth(&p.spec["verify"]),
        "{cname} has no live check, so its account cannot be confirmed as {}",
        text(&conn["account"])
    );
    let result = probe(h, ctx, &p)?;
    ensure!(
        result.status == Status::Ok,
        "could not confirm {cname}'s account (its check read {}); run reckon verify {cname}",
        result.status.name()
    );
    let seen = account(&p.spec, &result);
    if let Some(problem) = account_problem(cname, conn, seen.as_deref(), &p.values) {
        bail!(problem);
    }
    Ok(shown(seen.as_deref(), &p.values))
}
pub fn verify(
    h: &Path,
    ctx: &Context,
    registry: &Registry,
    names: &[String],
) -> Result<Vec<Value>> {
    for n in names {
        ensure!(
            ctx.data["connections"].get(n).is_some(),
            "unknown connection: {n}"
        );
    }
    let mut rows = vec![];
    for (cname, conn) in object(&ctx.data["connections"]) {
        if (!names.is_empty() && !names.contains(&cname)) || !orgs::applies(&conn, &ctx.env) {
            continue;
        }
        let mut row = json!({"connection":cname,"provider":conn["provider"]});
        let p = match prepare(h, ctx, &cname, registry, false, None) {
            Ok(p) => p,
            Err(e) => {
                row["status"] = json!("skipped");
                row["detail"] = json!(e.to_string());
                rows.push(row);
                continue;
            }
        };
        let reason = if !truth(&p.spec["verify"]) {
            Some("no live check defined".into())
        } else if !orgs::configured(h, &ctx.org, &cname, &p.spec, Some(&p.values))? {
            Some("no credentials".into())
        } else {
            quota(h, ctx, &cname, &p.spec, &strings(&p.spec["verify"]))
                .err()
                .map(|e| e.to_string())
        };
        if let Some(reason) = reason {
            row["status"] = json!("skipped");
            row["detail"] = json!(reason);
        } else {
            let result = probe(h, ctx, &p)?;
            row["status"] = json!(result.status.name());
            let detail = if result.status == Status::Ok {
                String::new()
            } else {
                let s = orgs::redact(&String::from_utf8_lossy(&result.stderr), &p.values);
                s.trim()
                    .chars()
                    .rev()
                    .take(400)
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect()
            };
            row["detail"] = json!(detail);
            if result.status == Status::Ok
                && (truth(&p.spec["identity"]) || truth(&conn["account"]))
            {
                let seen = account(&p.spec, &result);
                let problem = account_problem(&cname, &conn, seen.as_deref(), &p.values);
                let shown = shown(seen.as_deref(), &p.values);
                row["account"] = json!(shown);
                if let Some(problem) = problem {
                    row["status"] = json!("wrong_account");
                    row["detail"] = json!(problem);
                } else if let Some(shown) = shown {
                    row["detail"] = json!(if truth(&conn["account"]) {
                        format!("account {shown}")
                    } else {
                        format!(
                            "account {shown} (pin it: reckon connect {cname} --account {shown})"
                        )
                    });
                }
            }
        }
        rows.push(row);
    }
    Ok(rows)
}
pub fn template(h: &Path, org: &str, cname: &str, spec: &Value) -> Result<(PathBuf, bool)> {
    let path = orgs::secrets_path(h, org, cname)?;
    if path.exists() {
        return Ok((path, false));
    }
    let mut lines=vec![format!("# Settings for the {cname} connection. One KEY=value per line; no shell syntax."),format!("# Only {} keys are accepted. This file is never shown to the agent.",strings(&spec["prefixes"]).iter().map(|p|format!("{p}*")).collect::<Vec<_>>().join(" / ")),"# A value may be a reference instead of the secret: keychain:<item> or op://vault/item/field.".into(),format!("# 1Password setup: reckon secrets setup. Save credentials: reckon secrets set {cname} <KEY>.")];
    if truth(&spec["login"]) {
        lines.push(format!(
            "# Or leave the keys empty and run: reckon login {cname}"
        ));
    }
    for group in strings(&spec["required"]) {
        if group.contains('/') {
            lines.push(format!("# Set one of: {}", group.replace('/', ", ")));
        }
        lines.extend(group.split('/').map(|k| format!("{k}=")));
    }
    lines.extend(strings(&spec["optional"]).iter().map(|k| format!("# {k}=")));
    write(&path, format!("{}\n", lines.join("\n")))?;
    Ok((path, true))
}
