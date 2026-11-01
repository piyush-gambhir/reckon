use crate::store::*;
use crate::*;
pub fn valid_path(path: &str) -> bool {
    regex::Regex::new(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$").is_ok_and(|r| r.is_match(path))
}
pub fn load(root: &Path, env: &str) -> Result<Vec<Value>> {
    let mut goals = serde_json::Map::new();
    for layer in ["_shared", env] {
        let path = local_path(root, format!("knowledge/{layer}/goals.json"))?;
        if !path.is_file() {
            continue;
        }
        let data = read_json(&path)?;
        ensure!(
            data.is_object() && data["goals"].is_array(),
            "{layer}/goals.json must contain a goals array"
        );
        for goal in array(&data["goals"]) {
            let words = &goal["command"];
            let matched = goal.get("match").cloned().unwrap_or(json!([]));
            ensure!(
                goal.is_object()
                    && orgs::valid_name(text(&goal["id"]))
                    && goal["title"].is_string()
                    && goal["provider"].is_string()
                    && words.is_array()
                    && matched.is_array()
                    && (!array(words).is_empty() || !array(&matched).is_empty())
                    && array(words)
                        .iter()
                        .chain(array(&matched))
                        .all(|w| w.is_string() && !text(w).is_empty())
                    && valid_path(text(&goal["path"]))
                    && goal["target"].is_number()
                    && goal
                        .get("direction")
                        .is_none_or(|value| ["up", "down"].contains(&text(value))),
                "{layer}/goals.json: each goal needs id, title, provider, command (words), path (a.b.0), a numeric target and direction up or down: {goal}"
            );
            goals.insert(text(&goal["id"]).into(), goal.clone());
        }
    }
    Ok(goals.into_values().collect())
}
fn latest(
    root: &Path,
    org: &str,
    env: &str,
    goal: &Value,
    registry: &orgs::Registry,
) -> Result<Option<Value>> {
    let mut best: Option<(
        chrono::DateTime<chrono::Utc>,
        std::time::SystemTime,
        Value,
        PathBuf,
    )> = None;
    for session in entries(&local_path(root, "sessions")?)? {
        let id = session.file_name().unwrap_or_default().to_string_lossy();
        let (session, metadata) = session_path(root, &id)?;
        if text(&metadata["environment"]) != env {
            continue;
        }
        ensure!(
            metadata["org"] == org,
            "goal evidence belongs to another org"
        );
        // Briefings use the same integrity checks as citations and session reports.
        for record in evidence(&session)? {
            let dir = local_path(&session, format!("evidence/{}", text(&record["id"])))?;
            let path = local_path(&dir, "record.json")?;
            let args = strings(&record["command"]);
            let args = args.get(1..).unwrap_or(&[]);
            let spec = registry
                .get(text(&goal["provider"]))
                .unwrap_or(&Value::Null);
            if text(&record["environment"]) != env
                || text(&record["status"]) != "ok"
                || record["provider"] != goal["provider"]
                || !connectors::matches(&strings(&goal["command"]), &connectors::tokens(args, spec))
                || strings(&goal["match"]).iter().any(|m| {
                    !args.iter().any(|a| {
                        a == m
                            || (m.starts_with('-') && a.starts_with(&format!("{m}=")))
                            || (a.starts_with('-')
                                && a.split_once('=').is_some_and(|(_, value)| value == m))
                    })
                })
            {
                continue;
            }
            let stamp = instant(text(&record["observed_at"])).map_err(|_| {
                anyhow!(
                    "invalid observation time in evidence {}",
                    text(&record["id"])
                )
            })?;
            let modified = path.metadata()?.modified()?;
            if best
                .as_ref()
                .is_none_or(|(s, t, _, _)| (stamp, modified) > (*s, *t))
            {
                best = Some((stamp, modified, record, dir));
            }
        }
    }
    let Some((_, _, record, dir)) = best else {
        return Ok(None);
    };
    let value = read_json(&local_path(&dir, "stdout.txt")?)
        .ok()
        .map(|v| pick(&v, text(&goal["path"])).clone())
        .filter(Value::is_number)
        .unwrap_or(Value::Null);
    let mut warnings = strings(&record["data_quality"]["warnings"]);
    if ["partial", "limit_reached"].contains(&text(&record["completeness"])) {
        warnings.insert(
            0,
            format!("saved read is {}", text(&record["completeness"])),
        );
    }
    Ok(Some(
        json!({"value":value,"observed_at":record["observed_at"],"evidence":record["id"],"session":record["session"],"completeness":record["completeness"],"data_quality":record["data_quality"],"warnings":warnings}),
    ))
}
fn compare(value: &Value, target: &Value) -> Option<std::cmp::Ordering> {
    let integer = |number: &Value| {
        number
            .as_i64()
            .map(i128::from)
            .or_else(|| number.as_u64().map(i128::from))
    };
    // Compare whole parts as integers so counters above 2^53 keep their precision.
    let float_integer = |float: f64, integer: i128| {
        let whole = (float.trunc() as i128).cmp(&integer);
        if whole == std::cmp::Ordering::Equal {
            float.fract().partial_cmp(&0.0)
        } else {
            Some(whole)
        }
    };
    match (integer(value), integer(target)) {
        (Some(value), Some(target)) => Some(value.cmp(&target)),
        (None, Some(target)) => float_integer(value.as_f64()?, target),
        (Some(value), None) => {
            float_integer(target.as_f64()?, value).map(std::cmp::Ordering::reverse)
        }
        (None, None) => value.as_f64()?.partial_cmp(&target.as_f64()?),
    }
}
pub fn report(root: &Path, org: &str, env: &str, registry: &orgs::Registry) -> Vec<Value> {
    let goals = match load(root, env) {
        Ok(g) => g,
        Err(e) => {
            return vec![
                json!({"goal":"goals.json","title":"goals file","status":"invalid","next":e.to_string().chars().take(300).collect::<String>()}),
            ];
        }
    };
    goals.into_iter().map(|g| {
        let mut row = json!({"goal":g["id"],"title":g["title"],"target":g["target"],"direction":g["direction"].as_str().unwrap_or("up")});
        match latest(root, org, env, &g, registry) {
            Ok(Some(seen)) => {
                for (k, v) in object(&seen) {
                    row[k] = v;
                }
                if let Some(order) = compare(&row["value"], &g["target"]) {
                    let hit = if text(&row["direction"]) == "up" { order.is_ge() } else { order.is_le() };
                    row["status"] = json!(if hit { "on track" } else { "behind" });
                } else {
                    row["status"] = json!("unreadable");
                    row["next"] = json!(format!("no number at {} in evidence {}",text(&g["path"]),text(&row["evidence"])));
                }
            }
            Ok(None) => {
                row["status"] = json!("no reading");
                row["next"] = json!(format!("reckon run {} --session <id> -- {} ... -o json",text(&g["provider"]),strings(&g["command"]).join(" ")));
            }
            Err(e) => {
                row["status"] = json!("invalid");
                row["next"] = json!(e.to_string().chars().take(300).collect::<String>());
            }
        }
        row
    }).collect()
}
