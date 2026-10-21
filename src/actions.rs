use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
use std::io::{IsTerminal, Write};
pub const WRITES: &[(&str, &str, &str, &str)] = &[
    ("github.issue.create", "github", "repo title body", "labels"),
    (
        "jira.issue.create",
        "jira",
        "project type summary",
        "description labels parent",
    ),
    ("gsc.sitemap.submit", "gsc", "site sitemap", ""),
];
pub fn validate<'a>(operation: &str, params: &Value) -> Result<&'a str> {
    let entry=WRITES.iter().find(|v|v.0==operation).ok_or_else(||anyhow!("unsupported write: {operation} (supported: github.issue.create, gsc.sitemap.submit, jira.issue.create)"))?;
    let (_, provider, required, optional) = *entry;
    let allowed = format!("{required} {optional}")
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    ensure!(
        params.is_object() && object(params).keys().all(|k| allowed.contains(k)),
        "unknown parameters for {operation}"
    );
    for k in required.split_whitespace() {
        ensure!(
            params[k].is_string() && !text(&params[k]).trim().is_empty(),
            "{operation} requires {k}"
        );
    }
    ensure!(
        object(params)
            .values()
            .all(|v| v.is_string() && !text(v).contains('\0')),
        "parameters are text without NUL characters"
    );
    let re = |pattern: &str, value: &str| {
        regex::Regex::new(&format!("^(?:{pattern})$")).is_ok_and(|r| r.is_match(value))
    };
    match operation {
        "gsc.sitemap.submit" => {
            let site = text(&params["site"]);
            ensure!(
                re(
                    r"sc-domain:[A-Za-z0-9.-]+|https?://[^\s/*?\[\]]+/[^\s*?\[\]]*",
                    site
                ) && connectors::site_allowed(site, site),
                "site is a Search Console property: sc-domain:example.com or https://www.example.com/"
            );
            ensure!(
                re(r"https?://[^\s*?\[\]]{1,2040}", text(&params["sitemap"]))
                    && connectors::site_allowed(text(&params["sitemap"]), site),
                "sitemap is the sitemap's full URL, inside the property {site}"
            );
        }
        "jira.issue.create" => {
            ensure!(
                re(r"[A-Z][A-Z0-9_]{0,49}", text(&params["project"])),
                "project is a Jira project key such as ABC"
            );
            ensure!(
                re(r"[A-Za-z][\w -]{0,59}", text(&params["type"])),
                "type is an issue type name such as Task, Bug or Story"
            );
            ensure!(
                text(&params["summary"]).chars().count() <= 255
                    && !text(&params["summary"]).contains(['\n', '\r']),
                "summary is one line of at most 255 characters"
            );
            ensure!(
                text(&params["description"]).chars().count() <= 30000,
                "description is at most 30000 characters"
            );
            ensure!(
                !truth(&params["labels"]) || re(r"[\w.:/-]+(,[\w.:/-]+)*", text(&params["labels"])),
                "labels must be a comma-separated list of names without spaces"
            );
            ensure!(
                !truth(&params["parent"])
                    || re(r"[A-Z][A-Z0-9_]{0,49}-[0-9]+", text(&params["parent"])),
                "parent is an issue key such as ABC-12"
            );
        }
        _ => {
            ensure!(
                re(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text(&params["repo"])),
                "repo must be owner/name"
            );
            ensure!(
                text(&params["title"]).chars().count() <= 256
                    && text(&params["body"]).chars().count() <= 65536,
                "title or body is too long"
            );
            ensure!(
                !truth(&params["labels"])
                    || re(r"[\w .:/-]+(,[\w .:/-]+)*", text(&params["labels"])),
                "labels must be a comma-separated list of plain names"
            );
        }
    }
    Ok(provider)
}
pub fn target_text(operation: &str, params: &Value) -> String {
    match operation {
        "jira.issue.create" => {
            format!("{}: {}", text(&params["project"]), text(&params["summary"]))
        }
        "gsc.sitemap.submit" => text(&params["sitemap"]).into(),
        _ => text(&params["repo"]).into(),
    }
}
pub fn marker(id: &str, operation: &str) -> String {
    if operation == "jira.issue.create" {
        format!("Reckon action: {id}")
    } else {
        format!("<!-- reckon-action: {id} -->")
    }
}
pub fn argv(operation: &str, p: &Value, id: &str) -> (Vec<String>, Option<Vec<u8>>) {
    match operation {
        "gsc.sitemap.submit" => (
            vec![
                "gsc".into(),
                "sitemaps".into(),
                "submit".into(),
                text(&p["sitemap"]).into(),
                format!("--site={}", text(&p["site"])),
                "-o".into(),
                "json".into(),
            ],
            None,
        ),
        "jira.issue.create" => {
            let d = text(&p["description"]).trim_end_matches('\n');
            let mut a = vec![
                "jira".into(),
                "issue".into(),
                "create".into(),
                "--allow-duplicate".into(),
                format!("--project={}", text(&p["project"])),
                format!("--type={}", text(&p["type"])),
                format!("--summary={}", text(&p["summary"])),
                format!(
                    "--description={}{}",
                    if d.is_empty() {
                        String::new()
                    } else {
                        format!("{d}\n\n")
                    },
                    marker(id, operation)
                ),
            ];
            a.extend(
                text(&p["labels"])
                    .split(',')
                    .filter(|l| !l.is_empty())
                    .map(|l| format!("--label={l}")),
            );
            if truth(&p["parent"]) {
                a.push(format!("--parent={}", text(&p["parent"])));
            }
            a.extend(["-o".into(), "json".into()]);
            (a, None)
        }
        _ => {
            let mut a = vec![
                "gh".into(),
                "issue".into(),
                "create".into(),
                "--repo".into(),
                text(&p["repo"]).into(),
                "--title".into(),
                text(&p["title"]).into(),
                "--body-file".into(),
                "-".into(),
            ];
            if truth(&p["labels"]) {
                a.extend(["--label".into(), text(&p["labels"]).into()]);
            }
            (
                a,
                Some(
                    format!(
                        "{}\n\n{}\n",
                        text(&p["body"]).trim_end_matches('\n'),
                        marker(id, operation)
                    )
                    .into_bytes(),
                ),
            )
        }
    }
}
pub fn printable(content: &str) -> String {
    content
        .chars()
        .map(|c| {
            if c == '\n' || c == '\t' || (c >= ' ' && c != '\u{7f}') {
                c.to_string()
            } else {
                format!("\\x{:02x}", c as u32)
            }
        })
        .collect()
}
fn targeting(key: &str) -> bool {
    let target=regex::Regex::new("(?i)HOST|URL|SERVER|SITE|DOMAIN|ENDPOINT|ORG|PROJECT|ACCOUNT|REGION|PROFILE|TENANT|WORKSPACE|EMAIL|USER").expect("constant regex");
    let secret = regex::Regex::new(
        "(?i)TOKEN|PASSWORD|SECRET|PASS$|_PWD$|API_KEY|APIKEY|COOKIE|CREDENTIALS|PRIVATE_KEY|_URI$",
    )
    .expect("constant regex");
    target.is_match(key) && !secret.is_match(key)
}
pub fn target(conn: &Value, values: &process::Environment) -> String {
    hash_json(
        &json!({"connection":conn,"settings":values.iter().filter(|(k,_)|targeting(k)).collect::<std::collections::BTreeMap<_,_>>()}),
    )
}
pub fn digest(record: &Value) -> String {
    let mut frozen = json!({});
    for k in ["org", "environment", "connection", "operation", "params"] {
        frozen[k] = record[k].clone();
    }
    hash_json(&frozen)
}
fn is_live(ctx: &Context, conn: &Value) -> bool {
    ctx.production || conn.get("env").is_none()
}
pub fn preview(record: &Value, live: bool, full: bool, account: Option<&str>) -> String {
    let mut lines = vec![
        format!(
            "Action {} ({})",
            text(&record["id"]),
            text(&record["status"])
        ),
        format!(
            "  {} on {}{} in {}/{}{}",
            text(&record["operation"]),
            text(&record["connection"]),
            account
                .map(|a| format!(" as {}", printable(a)))
                .unwrap_or_default(),
            text(&record["org"]),
            text(&record["environment"]),
            if live {
                "  [LIVE: production or org-wide account]"
            } else {
                ""
            }
        ),
    ];
    for (k, v) in object(&record["params"]) {
        let v = text(&v);
        let v = if full || v.chars().count() < 2000 {
            printable(v)
        } else {
            printable(&format!(
                "{}\n... ({} characters)",
                v.chars().take(2000).collect::<String>(),
                v.chars().count()
            ))
        };
        lines.push(format!(
            "  {k}:{}",
            if v.contains('\n') {
                format!("\n    {}", v.replace('\n', "\n    "))
            } else {
                format!(" {v}")
            }
        ));
    }
    lines.join("\n")
}
fn bound(conn: &Value, operation: &str, params: &Value) -> Result<()> {
    let rule = match operation {
        "github.issue.create" => Some(("repo", "repo")),
        "gsc.sitemap.submit" => Some(("site", "site")),
        _ => None,
    };
    if let Some((param, kind)) = rule {
        let allowed = strings(&conn["resources"][kind]);
        let value = text(&params[param]);
        ensure!(
            allowed.is_empty()
                || allowed.iter().any(|p| if kind == "site" {
                    connectors::site_allowed(value, p)
                } else {
                    glob_match(p, value)
                }),
            "{kind} {value} is outside this connection (allowed: {})",
            allowed.join(", ")
        );
    }
    Ok(())
}
pub fn prepare(
    h: &Path,
    ctx: &Context,
    root: &Path,
    operation: &str,
    params: Value,
    registry: &Registry,
    session: Option<&str>,
) -> Result<(Value, String)> {
    let provider = validate(operation, &params)?;
    let (cname, conn) = orgs::resolve(ctx, provider)?;
    bound(&conn, operation, &params)?;
    let id = format!(
        "a-{}-{}",
        chrono::Utc::now().format("%Y%m%dT%H%M%SZ"),
        &uuid::Uuid::new_v4().simple().to_string()[..8]
    );
    let values = orgs::secrets(h, &ctx.org, &cname, &registry[provider])?
        .into_iter()
        .filter(|(k, _)| targeting(k))
        .collect::<process::Environment>();
    let values = orgs::references(h, &values)?;
    let mut record = json!({"schema_version":SCHEMA,"id":id,"org":ctx.org,"environment":ctx.env,"connection":cname,"operation":operation,"params":params,"status":"prepared","prepared_at":now(),"receipt":null,"target_sha256":target(&conn,&values)});
    if let Some(session) = session {
        record["session"] = json!(identifier(session)?);
    }
    record["payload_sha256"] = json!(digest(&record));
    write_json(
        &local_path(root, format!("actions/{id}/action.json"))?,
        &record,
    )?;
    let preview = preview(
        &record,
        is_live(ctx, &conn),
        false,
        conn["account"].as_str(),
    );
    Ok((record, preview))
}
pub fn load(root: &Path, id: &str) -> Result<(PathBuf, Value)> {
    let path = local_path(root, Path::new("actions").join(identifier(id)?))?;
    let p = local_path(&path, "action.json")?;
    ensure!(p.is_file(), "unknown action: {id}");
    let record = read_json(&p)?;
    ensure!(
        record.is_object() && record["schema_version"] == SCHEMA && text(&record["id"]) == id,
        "unsupported or inconsistent action metadata"
    );
    Ok((path, record))
}
pub fn listing(root: &Path) -> Result<Vec<Value>> {
    let mut rows = vec![];
    for dir in entries(&local_path(root, "actions")?)?.into_iter().rev() {
        let p = local_path(&dir, "action.json")?;
        if p.is_file() {
            let r = read_json(&p)?;
            let mut row = json!({});
            for k in [
                "id",
                "status",
                "operation",
                "connection",
                "environment",
                "prepared_at",
                "session",
            ] {
                row[k] = r[k].clone();
            }
            rows.push(row);
        }
    }
    Ok(rows)
}
fn connection(ctx: &Context, record: &Value) -> Result<(String, Value)> {
    ensure!(
        text(&record["org"]) == ctx.org && text(&record["environment"]) == ctx.env,
        "action belongs to {}/{}; this context is {}/{}",
        text(&record["org"]),
        text(&record["environment"]),
        ctx.org,
        ctx.env
    );
    let cname = text(&record["connection"]);
    let conn = &ctx.data["connections"][cname];
    let provider = validate(text(&record["operation"]), &record["params"])?;
    ensure!(
        conn.is_object() && text(&conn["provider"]) == provider && orgs::applies(conn, &ctx.env),
        "connection {cname} changed or no longer serves {}; prepare the action again",
        ctx.env
    );
    bound(conn, text(&record["operation"]), &record["params"])?;
    Ok((cname.into(), conn.clone()))
}
pub fn console(site: &str) -> String {
    let encoded = url::form_urlencoded::byte_serialize(site.as_bytes())
        .collect::<String>()
        .replace('+', "%20");
    format!("https://search.google.com/search-console/sitemaps?resource_id={encoded}")
}
fn browse(issue: &Value) -> Option<String> {
    let api = text(&issue["self"]);
    let base = api.split("/rest/api/").next()?;
    let base = if base == api {
        let url = url::Url::parse(api).ok()?;
        format!(
            "{}://{}{}",
            url.scheme(),
            url.host_str()?,
            url.port().map(|p| format!(":{p}")).unwrap_or_default()
        )
    } else {
        base.into()
    };
    (!text(&issue["key"]).is_empty()).then(|| format!("{base}/browse/{}", text(&issue["key"])))
}
fn link(operation: &str, out: &str, params: &Value) -> Option<String> {
    match operation {
        "gsc.sitemap.submit" => Some(console(text(&params["site"]))),
        "jira.issue.create" => browse(&serde_json::from_str::<Value>(out).ok()?),
        _ => regex::Regex::new(r"https://\S+")
            .ok()?
            .find(out)
            .map(|m| m.as_str().into()),
    }
}
pub fn apply(h: &Path, ctx: &Context, root: &Path, id: &str, registry: &Registry) -> Result<Value> {
    ensure!(
        !activity::pinned(),
        "reckon act apply is for the operator, not a pinned agent"
    );
    ensure!(
        std::io::stdin().is_terminal(),
        "reckon act apply is for the operator's own terminal"
    );
    let mut ctx = ctx.clone();
    ctx.link = Value::Null;
    let (path, _) = load(root, id)?;
    let _lock = lock(&path)?;
    let (_, mut record) = load(root, id)?;
    ensure!(
        text(&record["status"]) == "prepared",
        "action {id} is {}; each action runs once",
        text(&record["status"])
    );
    if (chrono::Utc::now() - instant(text(&record["prepared_at"]))?).num_seconds() > 24 * 3600 {
        record["status"] = json!("expired");
        record["decided_at"] = json!(now());
        write_json(&path.join("action.json"), &record)?;
        bail!("action {id} was prepared over 24 hours ago; prepare it again");
    }
    ensure!(
        text(&record["payload_sha256"]) == digest(&record),
        "action {id} changed after it was prepared; prepare it again"
    );
    let (cname, conn) = connection(&ctx, &record)?;
    let spec = &registry[text(&conn["provider"])];
    let values = orgs::references(h, &orgs::secrets(h, &ctx.org, &cname, spec)?)?;
    ensure!(
        text(&record["target_sha256"]) == target(&conn, &values),
        "connection {cname} changed after action {id} was prepared (its environment, resource limits, account or target settings); prepare it again"
    );
    let account = connectors::whoami(h, &ctx, &cname, &conn, registry, Some(&values))?;
    let live = is_live(&ctx, &conn);
    eprintln!("{}", preview(&record, live, true, account.as_deref()));
    let expected = if live { ctx.org.as_str() } else { "yes" };
    eprint!("Type {expected} to apply, anything else to cancel: ");
    std::io::stderr().flush()?;
    let mut answer = String::new();
    std::io::stdin().read_line(&mut answer)?;
    if answer.trim() != expected {
        record["status"] = json!("cancelled");
        record["decided_at"] = json!(now());
        write_json(&path.join("action.json"), &record)?;
        return Ok(record);
    }
    let (mut env, values) = orgs::child_env(h, &ctx, &cname, &conn, spec, true, Some(&values))?;
    for (k, v) in [
        ("NO_COLOR", "1"),
        ("GH_PROMPT_DISABLED", "1"),
        ("JIRA_NO_INPUT", "true"),
    ] {
        env.insert(k.into(), v.into());
    }
    let (command, body) = argv(text(&record["operation"]), &record["params"], id);
    record["status"] = json!("running");
    record["approved_at"] = json!(now());
    write_json(&path.join("action.json"), &record)?;
    let result = process::run(
        &command,
        Some(&env),
        env.get("TMPDIR").map(Path::new),
        60.0,
        262144,
        body.as_deref(),
    )?;
    let out = orgs::redact(&String::from_utf8_lossy(&result.stdout), &values)
        .trim()
        .to_owned();
    let err = orgs::redact(&String::from_utf8_lossy(&result.stderr), &values)
        .trim()
        .to_owned();
    record["status"] = json!(match result.status {
        process::Status::Ok => "applied",
        process::Status::Failed => "failed",
        _ => "unknown",
    });
    record["completed_at"] = json!(now());
    record["receipt"] = json!({"exit_code":result.exit_code,"stdout":out.chars().rev().take(2000).collect::<String>().chars().rev().collect::<String>(),"stderr":err.chars().rev().take(2000).collect::<String>().chars().rev().collect::<String>(),"url":link(text(&record["operation"]),&out,&record["params"])});
    write_json(&path.join("action.json"), &record)?;
    Ok(record)
}
fn plain(value: &Value) -> String {
    if let Some(s) = value.as_str() {
        return s.into();
    }
    fn walk(n: &Value, out: &mut String) {
        if text(&n["type"]) == "text" {
            out.push_str(text(&n["text"]));
        } else if text(&n["type"]) == "hardBreak" {
            out.push('\n');
        }
        for child in array(&n["content"]) {
            walk(child, out);
        }
    }
    array(&value["content"])
        .iter()
        .map(|b| {
            let mut out = String::new();
            walk(b, &mut out);
            out
        })
        .collect::<Vec<_>>()
        .join("\n")
}
pub fn reconcile(
    h: &Path,
    ctx: &Context,
    root: &Path,
    id: &str,
    registry: &Registry,
) -> Result<Value> {
    let (path, _) = load(root, id)?;
    let _lock = lock(&path)?;
    let (_, mut record) = load(root, id)?;
    ensure!(
        ["unknown", "running"].contains(&text(&record["status"])),
        "action {id} is {}; only unknown outcomes need reconciling",
        text(&record["status"])
    );
    let (cname, conn) = connection(ctx, &record)?;
    let (env, _values) = orgs::child_env(
        h,
        ctx,
        &cname,
        &conn,
        &registry[text(&conn["provider"])],
        false,
        None,
    )?;
    let params = &record["params"];
    let op = text(&record["operation"]);
    let tag = marker(id, op);
    let started = instant(
        record["approved_at"]
            .as_str()
            .unwrap_or(text(&record["prepared_at"])),
    )?;
    let (command, where_) = match op {
        "gsc.sitemap.submit" => (
            vec![
                "gsc".into(),
                "sitemaps".into(),
                "get".into(),
                text(&params["sitemap"]).into(),
                format!("--site={}", text(&params["site"])),
                "-o".into(),
                "json".into(),
            ],
            text(&params["sitemap"]).to_owned(),
        ),
        "jira.issue.create" => {
            let days = (chrono::Utc::now() - started)
                .num_seconds()
                .div_euclid(86400)
                + 2;
            (
                vec![
                    "jira".into(),
                    "issue".into(),
                    "search".into(),
                    "--jql".into(),
                    format!(
                        "project = \"{}\" AND creator = currentUser() AND created >= -{days}d ORDER BY created DESC",
                        text(&params["project"])
                    ),
                    "--fields".into(),
                    "summary,description".into(),
                    "--limit".into(),
                    "100".into(),
                    "-o".into(),
                    "json".into(),
                ],
                format!("project {}", text(&params["project"])),
            )
        }
        _ => (
            vec![
                "gh".into(),
                "issue".into(),
                "list".into(),
                "--repo".into(),
                text(&params["repo"]).into(),
                "--state".into(),
                "all".into(),
                "--limit".into(),
                "100".into(),
                "--json".into(),
                "number,url,body,createdAt".into(),
            ],
            text(&params["repo"]).into(),
        ),
    };
    let result = process::run(
        &command,
        Some(&env),
        env.get("TMPDIR").map(Path::new),
        60.0,
        8 * 1048576,
        None,
    )?;
    ensure!(
        result.status == process::Status::Ok,
        "could not read {where_} ({}); the outcome stays unknown",
        result.status.name()
    );
    let data: Value = serde_json::from_slice(&result.stdout).map_err(|_| {
        anyhow!("could not read the listing of {where_}; the outcome stays unknown")
    })?;
    let found = match op {
        "gsc.sitemap.submit" => instant(text(&data["lastSubmitted"]))
            .is_ok_and(|t| t >= started)
            .then(|| console(text(&params["site"]))),
        "jira.issue.create" => array(&data)
            .iter()
            .find(|i| {
                i["fields"]["summary"] == params["summary"]
                    && plain(&i["fields"]["description"])
                        .trim_end()
                        .lines()
                        .last()
                        .unwrap_or("")
                        .trim()
                        == tag
            })
            .and_then(browse),
        _ => array(&data)
            .iter()
            .find(|i| {
                text(&i["body"])
                    .trim_end()
                    .lines()
                    .last()
                    .unwrap_or("")
                    .trim()
                    == tag
            })
            .and_then(|i| i["url"].as_str().map(str::to_owned)),
    };
    if let Some(url) = found {
        record["status"] = json!("applied");
        record["reconciled_at"] = json!(now());
        if !record["receipt"].is_object() {
            record["receipt"] = json!({});
        }
        record["receipt"]["url"] = json!(url);
        record["receipt"]["reconciled"] = json!(true);
        write_json(&path.join("action.json"), &record)?;
    } else {
        record["last_reconcile_at"] = json!(now());
        write_json(&path.join("action.json"), &record)?;
        record["note"] = json!(format!(
            "no proof that this action landed in {where_}; still unknown. Check it yourself before preparing the write again."
        ));
    }
    Ok(record)
}
