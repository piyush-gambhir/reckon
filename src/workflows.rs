use crate::*;
use crate::{orgs::Registry, store::*};
use std::collections::BTreeSet;
pub fn validate(data: &Value, name: &str) -> Result<()> {
    let fail = |why: &str| anyhow!("workflow {name}: {why}");
    if !data.is_object() || !orgs::valid_name(text(&data["id"])) {
        return Err(fail("needs an id of lowercase letters, digits and hyphens"));
    }
    for k in ["title", "question"] {
        if !data[k].is_string() || text(&data[k]).trim().is_empty() {
            return Err(fail(&format!("needs a {k}")));
        }
    }
    if data
        .get("budget")
        .is_some_and(|v| !v.is_null() && !v.as_u64().is_some_and(|n| (1..=1000).contains(&n)))
    {
        return Err(fail("budget must be a whole number 1-1000"));
    }
    if data.get("params").is_some_and(|v| !v.is_object()) {
        return Err(fail("params must be an object"));
    }
    if data.get("series").is_some_and(|v| {
        !v.is_array()
            || array(v)
                .iter()
                .any(|k| !k.is_string() || data["params"].get(text(k)).is_none())
    }) {
        return Err(fail(
            "series lists parameter names that identify one series of runs, e.g. [\"site\"]",
        ));
    }
    for (k, spec) in object(&data["params"]) {
        if !orgs::valid_name(&k) || !spec.is_object() {
            return Err(fail(&format!("parameter '{k}' is not valid")));
        }
        for p in ["default", "pattern", "help"] {
            if spec.get(p).is_some_and(|v| !v.is_string()) {
                return Err(fail(&format!(
                    "parameter {k}: default, pattern and help are strings"
                )));
            }
        }
        regex::Regex::new(text(&spec["pattern"]))
            .map_err(|e| fail(&format!("parameter {k} pattern: {e}")))?;
    }
    let checks = array(&data["checks"]);
    if !data["checks"].is_array() || checks.is_empty() {
        return Err(fail("needs at least one check"));
    }
    let report = &data["report"];
    if data.get("report").is_some()
        && (!report.is_object()
            || object(report).iter().any(|(k, v)| {
                !["min_actions", "max_actions"].contains(&k.as_str()) || v.as_u64().is_none()
            })
            || report["min_actions"].as_u64().unwrap_or(0)
                > report["max_actions"].as_u64().unwrap_or(1000000))
    {
        return Err(fail(
            "report takes min_actions and max_actions, whole numbers with min <= max",
        ));
    }
    let mut ids = BTreeSet::new();
    for c in checks {
        let id = text(&c["id"]);
        if !c.is_object() || !orgs::valid_name(id) || !ids.insert(id) || !truth(&c["text"]) {
            return Err(fail("each check needs a unique id and a text"));
        }
        if ["report-actions", "follow-up"].contains(&id) {
            return Err(fail(&format!("{id} is reserved")));
        }
        if let Some(n) = c.get("findings") {
            if !n.as_u64().is_some_and(|n| n >= 1) {
                return Err(fail(&format!(
                    "check {id}: findings must be a whole number of at least 1"
                )));
            }
        } else if !c["any"].is_array()
            || array(&c["any"]).is_empty()
            || !array(&c["any"]).iter().all(|w| {
                w.is_object()
                    && w["provider"].is_string()
                    && w["command"].is_array()
                    && !array(&w["command"]).is_empty()
                    && array(&w["command"])
                        .iter()
                        .all(|v| v.is_string() && !text(v).is_empty())
                    && w.get("flag").is_none_or(Value::is_string)
            })
        {
            return Err(fail(&format!(
                "check {id} needs any: [{{provider, command: [words], flag?}}] or findings: N"
            )));
        }
    }
    Ok(())
}
pub fn recipes(root: &Path) -> Result<Value> {
    let mut found = json!({});
    let builtin = assets::root(&orgs::home()?)?.join("skills/reckon/workflows");
    for folder in [builtin, root.join("workflows")] {
        for path in entries(&folder)? {
            if path.extension().is_some_and(|v| v == "json") {
                let mut data = read_json(&path)?;
                validate(
                    &data,
                    &path.file_name().unwrap_or_default().to_string_lossy(),
                )?;
                data["source"] = json!(path);
                let id = text(&data["id"]).to_owned();
                found[id] = data;
            }
        }
    }
    Ok(found)
}
pub struct Started {
    pub question: String,
    pub area: Value,
    pub budget: Value,
    pub snapshot: Value,
    pub playbook: Value,
}
pub fn start(root: &Path, id: &str, given: &Value) -> Result<Started> {
    let recipes = recipes(root)?;
    let recipe = recipes.get(id).ok_or_else(|| {
        anyhow!(
            "unknown workflow '{id}'; reckon workflows lists them: {}",
            object(&recipes)
                .keys()
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        )
    })?;
    let mut given = object(given);
    let mut params = json!({});
    for (k, spec) in object(&recipe["params"]) {
        let mut v = given
            .remove(&k)
            .or_else(|| spec.get("default").cloned())
            .ok_or_else(|| {
                anyhow!(
                    "{id} needs --param {k}=<{}>",
                    spec["help"].as_str().unwrap_or("value")
                )
            })?;
        if text(&v) == "today" {
            v = json!(chrono::Utc::now().format("%Y-%m-%d").to_string());
        }
        if truth(&spec["pattern"]) {
            let pattern = text(&spec["pattern"]);
            ensure!(
                regex::Regex::new(&format!("^(?:{pattern})$"))?.is_match(text(&v)),
                "--param {k} must match {pattern}"
            );
        }
        params[k] = v;
    }
    ensure!(
        given.is_empty(),
        "{id} has no parameter {}; it takes: {}",
        given.keys().cloned().collect::<Vec<_>>().join(", "),
        object(&recipe["params"])
            .keys()
            .cloned()
            .collect::<Vec<_>>()
            .join(", ")
    );
    let re = regex::Regex::new(r"\{([a-z0-9][a-z0-9-]*)\}")?;
    let question = re
        .replace_all(text(&recipe["question"]), |c: &regex::Captures<'_>| {
            params[&c[1]].as_str().unwrap_or(&c[0]).to_owned()
        })
        .into_owned();
    let mut snapshot = json!({"params":params});
    for k in ["id", "title", "checks", "report", "series", "source"] {
        if let Some(v) = recipe.get(k) {
            snapshot[k] = v.clone();
        }
    }
    Ok(Started {
        question,
        area: recipe["area"].clone(),
        budget: recipe["budget"].clone(),
        snapshot,
        playbook: recipe["playbook"].clone(),
    })
}
pub fn previous(root: &Path, env: &str, snapshot: &Value) -> Value {
    let mut best: Option<(String, Value)> = None;
    for folder in entries(&root.join("sessions")).unwrap_or_default() {
        let id = folder.file_name().unwrap_or_default().to_string_lossy();
        let Ok((_, data)) = session_path(root, &id) else {
            continue;
        };
        let flow = &data["workflow"];
        if !flow.is_object()
            || !flow["params"].is_object()
            || !data["notes"].is_array()
            || text(&data["environment"]) != env
            || text(&data["status"]) != "done"
            || flow["id"] != snapshot["id"]
            || strings(&snapshot["series"])
                .iter()
                .any(|k| flow["params"][k] != snapshot["params"][k])
        {
            continue;
        }
        let created = text(&data["created_at"]).to_owned();
        if best.as_ref().is_none_or(|(at, _)| &created > at) {
            best = Some((
                created,
                json!({"session":data["id"],"actions":investigation::live(&data["notes"]).iter().filter(|n|text(&n["kind"])=="action").map(|n|json!({"id":n["id"],"text":n["text"]})).collect::<Vec<_>>()}),
            ));
        }
    }
    best.map(|(_, v)| v).unwrap_or(Value::Null)
}
pub fn met(way: &Value, record: &Value, registry: &Registry) -> bool {
    if text(&record["status"]) != "ok" || record["provider"] != way["provider"] {
        return false;
    }
    let args = strings(&record["command"]);
    let args = args.get(1..).unwrap_or(&[]);
    if args
        .iter()
        .any(|a| ["-h", "--help", "help"].contains(&a.as_str()))
    {
        return false;
    }
    let flag = text(&way["flag"]);
    if !flag.is_empty()
        && !args
            .iter()
            .any(|a| a == flag || a.starts_with(&format!("{flag}=")))
    {
        return false;
    }
    connectors::matches(
        &strings(&way["command"]),
        &connectors::tokens(
            args,
            registry.get(text(&way["provider"])).unwrap_or(&Value::Null),
        ),
    )
}
pub fn progress(
    session: &Value,
    records: &[Value],
    available: &BTreeSet<String>,
    registry: &Registry,
) -> Result<Value> {
    let recipe = &session["workflow"];
    if !truth(recipe) {
        return Ok(Value::Null);
    }
    let notes = investigation::live(&session["notes"]);
    let mut skipped = serde_json::Map::new();
    let re = regex::Regex::new(r"(?i)^skip(?:ped)? ([a-z0-9][a-z0-9-]*):\s*\S")?;
    for n in &notes {
        if text(&n["kind"]) == "question"
            && let Some(c) = re.captures(text(&n["text"]))
        {
            skipped.insert(c[1].to_lowercase(), n["text"].clone());
        }
    }
    let findings = notes
        .iter()
        .filter(|n| text(&n["kind"]) == "finding")
        .count();
    let mut rows = vec![];
    for c in array(&recipe["checks"]) {
        let id = text(&c["id"]);
        let mut row = json!({"id":id,"text":c["text"]});
        if let Some(n) = c["findings"].as_u64() {
            row["status"] = json!(if findings as u64 >= n { "met" } else { "todo" });
        } else {
            let hits = records
                .iter()
                .filter(|r| array(&c["any"]).iter().any(|w| met(w, r, registry)))
                .map(|r| r["id"].clone())
                .collect::<Vec<_>>();
            if !hits.is_empty() {
                row["status"] = json!("met");
                row["evidence"] = json!(hits);
            } else if let Some(reason) = skipped.get(id) {
                row["status"] = json!("skipped");
                row["reason"] = reason.clone();
            } else {
                row["status"] = json!(if array(&c["any"])
                    .iter()
                    .any(|w| available.contains(text(&w["provider"])))
                {
                    "todo"
                } else {
                    "unavailable"
                });
            }
        }
        rows.push(row);
    }
    let carried = array(&session["previous"]["actions"]);
    if !carried.is_empty() {
        let followed = notes
            .iter()
            .flat_map(|n| strings(&n["follows"]))
            .collect::<BTreeSet<_>>();
        let missing = carried
            .iter()
            .filter(|a| !followed.contains(text(&a["id"])))
            .map(|a| a["id"].clone())
            .collect::<Vec<_>>();
        let mut row = json!({"id":"follow-up","text":format!("Each action from the last review ({}) followed up with reckon note --follows (now {} of {})",text(&session["previous"]["session"]),carried.len()-missing.len(),carried.len())});
        if missing.is_empty() {
            row["status"] = json!("met");
        } else if let Some(reason) = skipped.get("follow-up") {
            row["status"] = json!("skipped");
            row["reason"] = reason.clone();
        } else {
            row["status"] = json!("todo");
            row["missing"] = json!(missing);
        }
        rows.push(row);
    }
    let rule = &recipe["report"];
    if truth(rule) {
        let count = notes
            .iter()
            .filter(|n| text(&n["kind"]) == "action")
            .count() as u64;
        let low = rule["min_actions"].as_u64().unwrap_or(0);
        let high = rule["max_actions"].as_u64();
        let fits = count >= low && high.is_none_or(|h| count <= h);
        rows.push(json!({"id":"report-actions","text":format!("Actions recorded with evidence: {low}{} (now {count})",high.map(|h|format!(" to {h}")).unwrap_or_else(||" or more".into())),"status":if fits{"met"}else{"todo"}}));
    }
    let mut known = array(&recipe["checks"])
        .iter()
        .map(|c| text(&c["id"]).to_owned())
        .collect::<BTreeSet<_>>();
    if !carried.is_empty() {
        known.insert("follow-up".into());
    }
    Ok(
        json!({"workflow":recipe["id"],"params":recipe.get("params").unwrap_or(&json!({})),"todo":rows.iter().filter(|r|text(&r["status"])=="todo").map(|r|r["id"].clone()).collect::<Vec<_>>(),"checks":rows,"source":recipe["source"],"unknown_skips":skipped.keys().filter(|k|!known.contains(*k)).collect::<Vec<_>>()}),
    )
}
