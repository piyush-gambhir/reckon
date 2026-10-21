use crate::store::*;
use crate::*;
use chrono::{DateTime, Utc};
use std::collections::{BTreeMap, BTreeSet};
const FIELDS: [&str; 4] = ["input", "output", "cache_read", "cache_write"];
type Totals = BTreeMap<String, [i64; 4]>;
fn inside(cwd: &str, root: &Path) -> bool {
    let cwd = Path::new(cwd);
    cwd.starts_with(root) || root.canonicalize().is_ok_and(|p| cwd.starts_with(p))
}
fn recent(path: &Path, since: DateTime<Utc>) -> bool {
    path.metadata()
        .and_then(|m| m.modified())
        .is_ok_and(|t| DateTime::<Utc>::from(t) >= since)
}
fn lines(path: &Path) -> impl Iterator<Item = String> {
    use std::io::{BufRead, BufReader};
    std::fs::File::open(path).ok().into_iter().flat_map(|file| {
        BufReader::new(file)
            .split(b'\n')
            .map_while(std::result::Result::ok)
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
    })
}

fn parse(stamp: &str) -> Option<DateTime<Utc>> {
    instant(stamp).ok().or_else(|| {
        chrono::NaiveDateTime::parse_from_str(stamp, "%Y-%m-%dT%H:%M:%S%.f")
            .ok()
            .map(|t| t.and_utc())
    })
}
fn claude(root: &Path, since: DateTime<Utc>) -> Totals {
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
    for project in entries(&base).unwrap_or_default() {
        if !prefixes.iter().any(|p| {
            project
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .starts_with(p)
        }) {
            continue;
        }
        for path in walkdir::WalkDir::new(project)
            .into_iter()
            .filter_map(std::result::Result::ok)
            .map(|e| e.into_path())
            .filter(|p| p.extension().is_some_and(|e| e == "jsonl") && recent(p, since))
        {
            for line in lines(&path) {
                if !line.contains("\"usage\"") || !line.contains("\"assistant\"") {
                    continue;
                }
                let Ok(e) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                let msg = &e["message"];
                if !msg["usage"].is_object()
                    || parse(text(&e["timestamp"])).is_none_or(|t| t < since)
                    || text(&msg["model"]) == "<synthetic>"
                    || !inside(text(&e["cwd"]), root)
                {
                    continue;
                }
                let key = msg["id"]
                    .as_str()
                    .or_else(|| e["uuid"].as_str())
                    .unwrap_or("")
                    .to_owned();
                let row = [
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ]
                .map(|k| msg["usage"][k].as_i64().unwrap_or(0));
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
            for i in 0..4 {
                into[i] += row[i];
            }
        }
    }
    totals
}
fn codex(root: &Path, since: DateTime<Utc>) -> Totals {
    let base = std::env::var("CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| orgs::expand_home("~/.codex"))
        .join("sessions");
    let mut totals = Totals::new();
    let mut seen = BTreeSet::new();
    if !base.is_dir() {
        return totals;
    }
    for path in walkdir::WalkDir::new(base)
        .sort_by_file_name()
        .into_iter()
        .filter_map(std::result::Result::ok)
        .map(|e| e.into_path())
        .filter(|p| p.extension().is_some_and(|e| e == "jsonl") && recent(p, since))
    {
        let mut rows = lines(&path);
        let metadata = rows
            .next()
            .and_then(|line| serde_json::from_str::<Value>(&line).ok());
        if !metadata
            .as_ref()
            .is_some_and(|e| inside(text(&e["payload"]["cwd"]), root))
        {
            continue;
        }
        let mut model = "codex".to_owned();
        for line in rows {
            if !line.contains("\"token_count\"") && !line.contains("\"turn_context\"") {
                continue;
            }
            let Ok(e) = serde_json::from_str::<Value>(&line) else {
                continue;
            };
            let p = &e["payload"];
            if text(&p["type"]) != "token_count" {
                if let Some(m) = p["model"].as_str()
                    && !m.is_empty()
                {
                    model = m.into();
                }
                continue;
            }
            let turn = &p["info"]["last_token_usage"];
            let key = format!(
                "{}:{}",
                text(&e["timestamp"]),
                canonical(&p["info"]["total_token_usage"])
            );
            if !truth(turn)
                || parse(text(&e["timestamp"])).is_none_or(|t| t < since)
                || !seen.insert(key)
            {
                continue;
            }
            let cached = turn["cached_input_tokens"].as_i64().unwrap_or(0);
            let row = totals.entry(model.clone()).or_default();
            row[0] += turn["input_tokens"].as_i64().unwrap_or(0) - cached;
            row[1] += turn["output_tokens"].as_i64().unwrap_or(0);
            row[2] += cached;
            row[3] += turn["cache_write_input_tokens"].as_i64().unwrap_or(0);
        }
    }
    totals
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
    let since = Utc::now() - chrono::Duration::days(days);
    let root = orgs::workspace(h, org)?;
    let path = local_path(h, "prices.json")?;
    let table = if path.is_file() {
        read_json(&path)?
    } else {
        json!({})
    };
    ensure!(
        table.is_object()
            && object(&table).values().all(|v| v.is_object()
                && FIELDS
                    .iter()
                    .all(|f| v.get(*f).is_none_or(Value::is_number))),
        "{} must map each model to USD per million tokens for {}",
        path.display(),
        FIELDS.join(", ")
    );
    let (mut models, mut unpriced, mut tokens, mut cost) = (vec![], BTreeSet::new(), 0i64, 0.0f64);
    let mut priced = false;
    for (runtime, totals) in [
        ("claude", claude(&root, since)),
        ("codex", codex(&root, since)),
    ] {
        for (model, row) in totals {
            let rate = table.get(&model);
            let usd = rate.map(|rate| {
                (row.iter()
                    .enumerate()
                    .map(|(i, n)| *n as f64 * rate[FIELDS[i]].as_f64().unwrap_or(0.0))
                    .sum::<f64>()
                    / 1e6
                    * 100.0)
                    .round()
                    / 100.0
            });
            if let Some(usd) = usd {
                priced = true;
                cost += usd;
            } else {
                unpriced.insert(model.clone());
            }
            tokens += row.iter().sum::<i64>();
            let mut r = json!({"runtime":runtime,"model":model,"usd":usd});
            for (i, f) in FIELDS.iter().enumerate() {
                r[*f] = json!(row[i]);
            }
            models.push(r);
        }
    }
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
    let used = report["tokens"].as_i64().unwrap_or(0);
    Ok((used > limit["tokens"].as_i64().unwrap_or(0)).then(|| {
        format!(
            "{org} has used {used} agent tokens in {} days, over its budget of {}",
            limit["days"], limit["tokens"]
        )
    }))
}
