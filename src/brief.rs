use crate::*;
use crate::{
    orgs::{Context, Registry},
    store::*,
};
use std::collections::BTreeMap;
pub fn brief(
    h: &Path,
    ctx: &Context,
    registry: &Registry,
    days: i64,
    envs: &[String],
) -> Result<Value> {
    let envs = if envs.is_empty() {
        vec![ctx.env.clone()]
    } else {
        envs.to_vec()
    };
    let root = orgs::workspace(h, &ctx.org)?;
    let since = chrono::Utc::now() - chrono::Duration::days(days);
    let (mut sessions, mut findings, mut pending, mut unknown, mut attention, mut stale) =
        (vec![], vec![], vec![], vec![], vec![], vec![]);
    let mut series: BTreeMap<String, Value> = BTreeMap::new();
    for folder in entries(&local_path(&root, "sessions")?)?.into_iter().rev() {
        let Ok(data) = read_json(&folder.join("session.json")) else {
            continue;
        };
        if !data.is_object()
            || !data["notes"].is_array()
            || !envs.contains(&text(&data["environment"]).to_owned())
        {
            continue;
        }
        let flow = &data["workflow"];
        if flow.is_object()
            && flow["id"].is_string()
            && flow["params"].is_object()
            && flow
                .get("series")
                .is_none_or(|v| v.is_array() && array(v).iter().all(Value::is_string))
        {
            let keys = strings(&flow["series"])
                .into_iter()
                .collect::<std::collections::BTreeSet<_>>();
            let identity = canonical(&json!([
                flow["id"],
                data["environment"],
                keys.iter()
                    .map(|k| json!([k, flow["params"][k]]))
                    .collect::<Vec<_>>()
            ]));
            let row=series.entry(identity).or_insert_with(||json!({"workflow":flow["id"],"environment":data["environment"],"series":keys.iter().map(|k|format!("{k}={}",text(&flow["params"][k]))).collect::<Vec<_>>().join(" "),"last_done":null,"session":null,"open":[]}));
            if text(&data["status"]) == "done" {
                let stamp = data["closed_at"]
                    .as_str()
                    .unwrap_or(text(&data["created_at"]));
                let parsed = instant(stamp).ok().or_else(|| {
                    chrono::NaiveDate::parse_from_str(stamp, "%Y-%m-%d")
                        .ok()
                        .and_then(|d| d.and_hms_opt(0, 0, 0))
                        .map(|d| d.and_utc())
                });
                if let Some(done) = parsed
                    && (row["last_done"].is_null()
                        || instant(text(&row["last_done"])).is_ok_and(|t| done > t))
                {
                    row["last_done"] =
                        json!(done.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, true));
                    row["session"] = data["id"].clone();
                }
            } else if text(&data["status"]) == "open" {
                row["open"]
                    .as_array_mut()
                    .expect("array")
                    .push(data["id"].clone());
            }
        }
        if text(&data["status"]) == "open" {
            sessions.push(json!({"id":data["id"],"environment":data["environment"],"question":data["question"],"since":data["created_at"]}));
        }
        for n in array(&data["notes"]) {
            if text(&n["kind"]) == "finding"
                && !truth(&n["withdrawn_at"])
                && instant(text(&n["recorded_at"])).is_ok_and(|t| t >= since)
            {
                findings.push(json!({"session":data["id"],"environment":data["environment"],"text":n["text"],"at":n["recorded_at"]}));
            }
        }
    }
    for folder in entries(&local_path(&root, "actions")?)?.into_iter().rev() {
        let Ok(action) = read_json(&folder.join("action.json")) else {
            continue;
        };
        if !action.is_object()
            || !action["id"].is_string()
            || !envs.contains(&text(&action["environment"]).to_owned())
        {
            continue;
        }
        let row = json!({"id":action["id"],"operation":action["operation"],"connection":action["connection"],"environment":action["environment"],"target":actions::target_text(text(&action["operation"]),&action["params"])});
        if text(&action["status"]) == "prepared" {
            pending.push(row);
        } else if ["unknown", "running"].contains(&text(&action["status"])) {
            unknown.push(row);
        }
    }
    for env in &envs {
        let scoped = Context::from_data(ctx.data.clone(), env);
        let prefix = if envs.len() > 1 {
            format!("[{env}] ")
        } else {
            String::new()
        };
        for r in orgs::describe(h, &scoped, registry) {
            if truth(&r["applies"]) && text(&r["state"]) != "ready" {
                let message = format!("{prefix}{}: {}", text(&r["connection"]), text(&r["state"]));
                if !attention.contains(&message) {
                    attention.push(message);
                }
            }
        }
    }
    let events = activity::read(h, &ctx.org, days, None, None, 100000)?
        .into_iter()
        .filter(|e| envs.contains(&text(&e["environment"]).to_owned()))
        .collect::<Vec<_>>();
    let refused=events.iter().filter(|e|text(&e["event"])=="refused").take(20).map(|e|json!({"at":e["at"],"environment":e["environment"],"actor":e["actor"],"target":e["target"],"command":strings(&e["command"]).join(" "),"detail":e["detail"].as_str().unwrap_or("")})).collect::<Vec<_>>();
    let mut targets = vec![];
    let mut new = vec![];
    for env in &envs {
        for mut row in goals::report(&root, env, registry) {
            row["environment"] = json!(env);
            targets.push(row);
        }
        new.extend(watches::inbox(h, &ctx.org, days, Some(env))?);
    }
    new.sort_by_key(|r| std::cmp::Reverse(text(&r["at"]).to_owned()));
    new.truncate(30);
    for layer in std::iter::once("_shared".to_owned()).chain(envs.clone()) {
        for path in entries(&local_path(&root, format!("knowledge/{layer}"))?)? {
            if path.extension().is_some_and(|e| e == "md")
                && let Ok(modified) = path.metadata().and_then(|m| m.modified())
            {
                let age = (chrono::Utc::now() - chrono::DateTime::<chrono::Utc>::from(modified))
                    .num_days();
                if age > 90 {
                    stale.push(format!(
                        "{layer}/{} ({age} days)",
                        path.file_name().unwrap_or_default().to_string_lossy()
                    ));
                }
            }
        }
    }
    let mut reviews = series.into_values().collect::<Vec<_>>();
    reviews.sort_by_key(|r| {
        (
            text(&r["last_done"]).to_owned(),
            text(&r["workflow"]).to_owned(),
        )
    });
    let warning = match spend::over(h, &ctx.org) {
        Ok(v) => v,
        Err(e) => Some(format!("could not check the agent budget: {e}")),
    };
    Ok(
        json!({"org":ctx.org,"environment":if envs.len()==1{ctx.env.as_str()}else{"all"},"days":days,"open_sessions":sessions,"recent_findings":findings.into_iter().take(20).collect::<Vec<_>>(),"writes_waiting_for_approval":pending,"writes_with_unknown_outcome":unknown,"connections_needing_attention":attention,"stale_knowledge":stale,"refused_runs":refused,"runs":events.iter().filter(|e|text(&e["event"])=="run").count(),"goals":targets,"inbox":new,"agent_budget":warning.into_iter().collect::<Vec<_>>(),"reviews":reviews}),
    )
}
pub fn all_orgs(h: &Path, registry: &Registry, days: i64) -> Result<Value> {
    ensure!(
        !activity::pinned(),
        "a pinned agent sees only its own org; run reckon brief --all-orgs in your own terminal"
    );
    let mut results = vec![];
    for org in orgs::orgs(h)? {
        let data = orgs::load(h, &org, registry)?;
        let envs = strings(&data["environments"]);
        let ctx = Context::from_data(data, &envs[0]);
        let mut r = brief(h, &ctx, registry, days, &envs)?;
        r["agent_tokens"] = spend::report(h, &org, days)
            .map(|r| r["tokens"].clone())
            .unwrap_or(json!(0));
        results.push(r);
    }
    Ok(json!({"days":days,"orgs":results}))
}
pub fn review_line(r: &Value) -> String {
    let mut line = format!(
        "[{}] {}{}: ",
        text(&r["environment"]),
        text(&r["workflow"]),
        if truth(&r["series"]) {
            format!(" {}", text(&r["series"]))
        } else {
            String::new()
        }
    );
    if truth(&r["last_done"]) {
        line.push_str(&format!(
            "last done {} ({} days ago, {})",
            text(&r["last_done"]).get(..10).unwrap_or(""),
            instant(text(&r["last_done"]))
                .map(|t| (chrono::Utc::now() - t).num_days())
                .unwrap_or(0),
            text(&r["session"])
        ));
    } else {
        line.push_str("never finished");
    }
    if truth(&r["open"]) {
        line.push_str(&format!("; open: {}", strings(&r["open"]).join(", ")));
    }
    line
}
fn goal_line(r: &Value) -> String {
    let where_ = format!("[{}] {}: ", text(&r["environment"]), text(&r["title"]));
    if ["no reading", "unreadable", "invalid"].contains(&text(&r["status"])) {
        return format!("{where_}{} ({})", text(&r["status"]), text(&r["next"]));
    }
    format!(
        "{where_}{} vs target {}, {} (observed {}, {})",
        number(&r["value"]),
        number(&r["target"]),
        text(&r["status"]),
        text(&r["observed_at"]).get(..10).unwrap_or(""),
        text(&r["evidence"])
    )
}
pub fn render(result: &Value) -> String {
    let mut lines = vec![
        format!(
            "# Brief: {} / {} (last {} days)",
            text(&result["org"]),
            text(&result["environment"]),
            result["days"]
        ),
        String::new(),
    ];
    for (title, key) in [
        ("Agent budget", "agent_budget"),
        ("New from watches (reckon inbox)", "inbox"),
        ("Goals (from the newest saved read)", "goals"),
        (
            "Reviews (each series' last finished run; the next one follows it up)",
            "reviews",
        ),
        (
            "Writes with an unknown outcome (run reckon act reconcile)",
            "writes_with_unknown_outcome",
        ),
        (
            "Writes waiting for approval (reckon act apply)",
            "writes_waiting_for_approval",
        ),
        ("Open sessions", "open_sessions"),
        ("Recent findings", "recent_findings"),
        (
            "Refused runs (wrong context, denied commands, budgets)",
            "refused_runs",
        ),
        (
            "Connections needing attention",
            "connections_needing_attention",
        ),
        ("Knowledge not touched in 90 days", "stale_knowledge"),
    ] {
        lines.extend([format!("## {title}"), String::new()]);
        if array(&result[key]).is_empty() {
            lines.push("None.".into());
        }
        for r in array(&result[key]) {
            let line = match key {
                "agent_budget" | "connections_needing_attention" | "stale_knowledge" => {
                    text(r).into()
                }
                "inbox" => format!(
                    "[{}] {}: {}  ({})",
                    text(&r["environment"]),
                    text(&r["watch"]),
                    text(&r["title"]),
                    text(&r["at"]).get(..16).unwrap_or("")
                ),
                "goals" => goal_line(r),
                "reviews" => review_line(r),
                "writes_with_unknown_outcome" | "writes_waiting_for_approval" => format!(
                    "{}  {} {} via {}",
                    text(&r["id"]),
                    text(&r["operation"]),
                    text(&r["target"]),
                    text(&r["connection"])
                ),
                "open_sessions" => format!(
                    "{}  [{}] {}",
                    text(&r["id"]),
                    text(&r["environment"]),
                    text(&r["question"])
                ),
                "recent_findings" => format!(
                    "[{}] {}  ({})",
                    text(&r["environment"]),
                    text(&r["text"]),
                    text(&r["session"])
                ),
                _ => format!(
                    "{}  [{}] {} {} {}: {}",
                    text(&r["at"]),
                    text(&r["environment"]),
                    text(&r["actor"]),
                    text(&r["target"]),
                    text(&r["command"]),
                    text(&r["detail"])
                ),
            };
            lines.push(format!("- {line}"));
        }
        lines.push(String::new());
    }
    lines.join("\n")
}
pub fn render_all(result: &Value) -> String {
    let mut lines=vec![format!("# Brief: all orgs (last {} days)",result["days"]),String::new(),"| Org | Unknown writes | Waiting writes | Open sessions | Findings | Refused | Runs | Attention | Agent tokens |".into(),"| --- | --- | --- | --- | --- | --- | --- | --- | --- |".into()];
    for r in array(&result["orgs"]) {
        lines.push(format!(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} |",
            text(&r["org"]),
            array(&r["writes_with_unknown_outcome"]).len(),
            array(&r["writes_waiting_for_approval"]).len(),
            array(&r["open_sessions"]).len(),
            array(&r["recent_findings"]).len(),
            array(&r["refused_runs"]).len(),
            r["runs"],
            array(&r["connections_needing_attention"]).len(),
            r["agent_tokens"]
        ));
    }
    if array(&result["orgs"]).is_empty() {
        lines.push("| no orgs yet | | | | | | | | |".into());
    }
    lines.push(String::new());
    for r in array(&result["orgs"]) {
        for line in render(r).lines() {
            lines.push(if let Some(rest) = line.strip_prefix("# Brief: ") {
                format!("## {rest}")
            } else if line.starts_with("## ") {
                format!("#{line}")
            } else {
                line.into()
            });
        }
        lines.push(String::new());
    }
    lines.join("\n")
}
