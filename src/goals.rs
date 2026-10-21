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
                    && ["up", "down"].contains(&goal["direction"].as_str().unwrap_or("up")),
                "{layer}/goals.json: each goal needs id, title, provider, command (words), path (a.b.0), a numeric target and direction up or down: {goal}"
            );
            goals.insert(text(&goal["id"]).into(), goal.clone());
        }
    }
    Ok(goals.into_values().collect())
}
fn latest(root: &Path, env: &str, goal: &Value, registry: &orgs::Registry) -> Option<Value> {
    let mut best: Option<(String, std::time::SystemTime, Value, PathBuf)> = None;
    for session in entries(&root.join("sessions")).ok()? {
        for dir in entries(&session.join("evidence")).unwrap_or_default() {
            let path = local_path(&dir, "record.json").ok()?;
            let Ok(record) = read_json(&path) else {
                continue;
            };
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
                        a == m || a.starts_with(&format!("{m}=")) || a.ends_with(&format!("={m}"))
                    })
                })
            {
                continue;
            }
            let stamp = text(&record["observed_at"]).to_owned();
            let modified = path.metadata().ok()?.modified().ok()?;
            if best
                .as_ref()
                .is_none_or(|(s, t, _, _)| (&stamp, modified) > (s, *t))
            {
                best = Some((stamp, modified, record, dir));
            }
        }
    }
    let (_, _, record, dir) = best?;
    let value = read_json(&local_path(&dir, "stdout.txt").ok()?)
        .ok()
        .map(|v| pick(&v, text(&goal["path"])).clone())
        .filter(Value::is_number)
        .unwrap_or(Value::Null);
    Some(
        json!({"value":value,"observed_at":record["observed_at"],"evidence":record["id"],"session":record["session"]}),
    )
}
pub fn report(root: &Path, env: &str, registry: &orgs::Registry) -> Vec<Value> {
    let goals = match load(root, env) {
        Ok(g) => g,
        Err(e) => {
            return vec![
                json!({"goal":"goals.json","title":"goals file","status":"invalid","next":e.to_string().chars().take(300).collect::<String>()}),
            ];
        }
    };
    goals.into_iter().map(|g|{let mut row=json!({"goal":g["id"],"title":g["title"],"target":g["target"],"direction":g["direction"].as_str().unwrap_or("up")});if let Some(seen)=latest(root,env,&g,registry){for(k,v)in object(&seen){row[k]=v;}if let Some(v)=row["value"].as_f64(){let target=g["target"].as_f64().unwrap_or(0.0);let hit=if text(&row["direction"])=="up"{v>=target}else{v<=target};row["status"]=json!(if hit{"on track"}else{"behind"});}else{row["status"]=json!("unreadable");row["next"]=json!(format!("no number at {} in evidence {}",text(&g["path"]),text(&row["evidence"])));}}else{row["status"]=json!("no reading");row["next"]=json!(format!("reckon run {} --session <id> -- {} ... -o json",text(&g["provider"]),strings(&g["command"]).join(" ")));}row}).collect()
}
