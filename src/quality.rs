use crate::*;

fn bounded(value: &Value) -> Option<String> {
    value.as_str().map(|value| {
        value
            .chars()
            .take(200)
            .map(|c| if c.is_control() { ' ' } else { c })
            .collect()
    })
}

pub fn summarize(record: &Value, data: Option<&Value>) -> Result<Value> {
    let provider = text(&record["provider"]);
    let data = data.unwrap_or(&Value::Null);
    if provider == "gsc" {
        return Ok(gsc_quality(data));
    }
    if !["bing", "clarity", "jenkins", "github", "cubeapm", "grafana"].contains(&provider) {
        return Ok(json!({}));
    }
    let registry = orgs::providers()?;
    let spec = &registry[provider];
    let argv = strings(&record["command"]);
    let args = argv.get(1..).unwrap_or(&[]);
    let words = connectors::tokens(args, spec);
    let command = words
        .iter()
        .take(2)
        .map(String::as_str)
        .collect::<Vec<_>>()
        .join(" ");
    let options = if provider == "github" {
        connectors::github_format_options(args, spec)
    } else {
        connectors::operands(
            args,
            spec,
            &strings(&json!([
                "--limit",
                "--count",
                "--days",
                "--json",
                "--status",
                "--jq",
                "--template"
            ])),
        )
        .0
    };
    let limit = |default: u64| match options
        .iter()
        .rev()
        .find(|(key, _)| ["--limit", "-L"].contains(&key.as_str()))
    {
        Some((_, value)) => value
            .as_deref()
            .and_then(|s| s.parse::<u64>().ok())
            .filter(|n| *n > 0),
        None => Some(default),
    };
    let mut sources = vec![];
    let mut warnings = vec![];
    let mut limited = false;
    match (provider, command.as_str()) {
        ("bing", "stats summary") => {
            warnings.push("Bing summary periods end at the latest day returned by Bing, which may precede the requested review window.".into());
            let expected = data["window_days"]
                .as_u64()
                .filter(|n| (1..=1000).contains(n));
            for scope in ["current", "previous"] {
                let period = &data[scope];
                if !period.is_object() {
                    if scope == "current" && data.get("current").is_some_and(Value::is_null) {
                        warnings.push("Bing returned no dated current-period data; this does not establish zero traffic.".into());
                        limited = true;
                    }
                    continue;
                }
                let mut source = json!({"scope":scope});
                for key in ["start", "end"] {
                    if let Some(value) = bounded(&period[key]) {
                        source[key] = json!(value);
                    }
                }
                if let Some(days) = period["days_with_data"].as_u64() {
                    source["days_with_data"] = json!(days);
                    if let Some(expected) = expected {
                        source["expected_days"] = json!(expected);
                        if days < expected {
                            limited = true;
                            warnings.push(format!("{scope}: {days} of {expected} days have data; missing days are unknown, not zero."));
                        }
                    }
                }
                sources.push(source);
            }
        }
        ("bing", "crawl issues") => {
            sources.push(json!({"scope":"result","coverage":"legacy_crawl_issue_listing"}));
            warnings.push("Bing crawl issues can be delayed or absent from the legacy endpoint; an empty result does not establish that the site has no crawl issues.".into());
        }
        ("clarity", "recordings list" | "sessions list") => {
            sources.push(json!({"scope":"recordings","timezone":"UTC","sampling":"sampled"}));
            limited = true;
            warnings.push("Clarity recordings are a sample, not a complete recording export; an empty sample does not establish that no matching sessions exist.".into());
        }
        ("clarity", "export") if words.len() == 1 => {
            let rows = data.as_array().map(|metrics| {
                metrics
                    .iter()
                    .filter_map(|metric| metric["information"].as_array().map(Vec::len))
                    .max()
                    .unwrap_or(metrics.len())
            });
            let mut source = json!({"scope":"export","timezone":"UTC","row_limit":1000});
            if let Some(rows) = rows {
                source["row_count"] = json!(rows);
                limited = rows >= 1000;
            }
            sources.push(source);
            warnings.push("Clarity export is limited to 1,000 rows per query with no pagination; smaller results alone do not prove complete coverage.".into());
            if limited {
                warnings.push(
                    "Clarity export reached its row limit; additional rows may be omitted.".into(),
                );
            }
        }
        ("jenkins", "build list" | "builds list")
        | ("github", "run list" | "run ls" | "pr list" | "pr ls") => {
            let default = if provider == "jenkins" {
                25
            } else if command.starts_with("run ") {
                20
            } else {
                30
            };
            let mut source = json!({"scope":"result"});
            let projected = provider == "github"
                && options
                    .iter()
                    .any(|(key, _)| ["--jq", "-q", "--template", "-t"].contains(&key.as_str()));
            if projected {
                limited = true;
                warnings.push("GitHub output was projected; returned row counts cannot establish source coverage.".into());
            } else if let Some(rows) = data.as_array().map(Vec::len) {
                source["row_count"] = json!(rows);
                if let Some(limit) = limit(default) {
                    source["row_limit"] = json!(limit);
                    if rows as u64 >= limit {
                        limited = true;
                        warnings.push(format!("The {provider} list reached its limit of {limit}; additional matching records may be omitted."));
                    }
                }
            }
            if provider == "jenkins" {
                source["coverage"] = json!("recent_builds_not_window_filtered");
                warnings.push("Jenkins returns recent builds, not filtered to the review window; inspect timestamps before making period-wide claims.".into());
                if options.iter().any(|(key, _)| key == "--status") {
                    warnings.push("Jenkins applies the status filter after selecting recent builds; older matching builds may be omitted.".into());
                }
            }
            sources.push(source);
        }
        ("jenkins", "build get" | "builds get") if data["building"].is_boolean() => {
            let state = if data["building"] == true {
                "in_progress".to_owned()
            } else {
                bounded(&data["result"]).unwrap_or_default()
            };
            sources.push(json!({"scope":"build","data_state":state}));
            if data["building"] == true {
                warnings.push(
                    "Jenkins build is still running; its result and duration may change.".into(),
                );
            }
        }
        ("github", "run view") if data["status"].is_string() => {
            let state = bounded(&data["status"]).unwrap_or_default();
            if state != "completed" && !state.is_empty() {
                warnings.push(
                    "GitHub run was not completed when captured; its outcome may change.".into(),
                );
            }
            sources.push(json!({"scope":"run","data_state":state}));
        }
        ("cubeapm" | "grafana", _) => {
            for key in ["warnings", "infos"] {
                for warning in array(&data[key]).iter().take(20).filter_map(bounded) {
                    warnings.push(format!("Provider {key}: {warning}"));
                }
                if array(&data[key]).len() > 20 {
                    warnings.push(format!("Only the first 20 provider {key} are summarized; inspect the output artifact for the rest."));
                }
            }
        }
        _ => {}
    }
    Ok(json!({"sources":sources,"warnings":warnings,"limited":limited}))
}

fn gsc_quality(data: &Value) -> Value {
    let mut candidates = vec![("result".to_owned(), data)];
    for key in ["current", "previous"] {
        if let Some(period) = data.get(key) {
            candidates.push((key.to_owned(), period));
        }
    }
    for (i, period) in array(&data["sources"]).iter().take(20).enumerate() {
        candidates.push((format!("sources[{i}]"), period));
    }
    let mut sources = vec![];
    let mut warnings = vec![];
    for (scope, period) in candidates {
        if !period.is_object()
            || ![
                "complete",
                "data_state",
                "first_incomplete_date",
                "first_incomplete_hour",
            ]
            .iter()
            .any(|key| period.get(key).is_some())
        {
            continue;
        }
        // Keep source headers bounded; rows remain in the hashed artifact.
        let mut source = json!({"scope":scope,"timezone":"America/Los_Angeles"});
        for key in [
            "start",
            "end",
            "data_state",
            "first_incomplete_date",
            "first_incomplete_hour",
        ] {
            if let Some(value) = period[key].as_str() {
                source[key] = json!(
                    value
                        .chars()
                        .take(80)
                        .map(|c| if c.is_control() { ' ' } else { c })
                        .collect::<String>()
                );
            }
        }
        for key in ["complete", "metadata_returned"] {
            if let Some(value) = period[key].as_bool() {
                source[key] = json!(value);
            }
        }
        for key in ["row_count", "requests"] {
            if let Some(value) = period[key].as_u64() {
                source[key] = json!(value);
            }
        }
        if period["complete"] == false {
            warnings.push(format!(
                "{scope}: pagination is incomplete; omitted rows are unknown, not zero."
            ));
        }
        if truth(&source["first_incomplete_date"]) || truth(&source["first_incomplete_hour"]) {
            warnings.push(format!(
                "{scope}: preliminary data starts at {} and may change.",
                [
                    text(&source["first_incomplete_date"]),
                    text(&source["first_incomplete_hour"])
                ]
                .into_iter()
                .filter(|at| !at.is_empty())
                .collect::<Vec<_>>()
                .join(" / ")
            ));
        } else if ["all", "hourly_all"].contains(&text(&period["data_state"])) {
            warnings.push(format!(
                "{scope}: the requested data state may include preliminary data that can change."
            ));
        }
        sources.push(source);
    }
    if array(&data["sources"]).len() > 20 {
        warnings.push("Only the first 20 source headers are summarized; inspect the output artifact for the remaining sources.".into());
    }
    json!({"sources":sources,"warnings":warnings,"sources_truncated":array(&data["sources"]).len()>20})
}
