use crate::*;

fn named(value: &Value, key: &str) -> bool {
    value[key].as_str().is_some_and(|v| !v.is_empty())
}
fn numbered(value: &Value, key: &str) -> bool {
    value[key].as_u64().is_some_and(|v| v > 0)
}
fn rows(value: &Value, row: impl Fn(&Value) -> bool) -> bool {
    value
        .as_array()
        .is_some_and(|values| values.iter().all(|v| v.is_object() && row(v)))
}

fn requested(record: &Value, index: usize) -> Result<&str> {
    record["command"][index]
        .as_str()
        .filter(|value| !value.is_empty() && !value.starts_with('-'))
        .ok_or_else(|| anyhow!("missing requested resource identity"))
}
fn same_trace_id(requested: &str, returned: &str) -> bool {
    let hex = |value: &str| {
        !value.is_empty() && value.len() <= 32 && value.bytes().all(|c| c.is_ascii_hexdigit())
    };
    requested == returned
        || (hex(requested)
            && hex(returned)
            && requested
                .trim_start_matches('0')
                .eq_ignore_ascii_case(returned.trim_start_matches('0')))
}
fn identity(record: &Value, data: &Value) -> Result<()> {
    let number = match text(&record["operation"]) {
        "jenkins.build.get" => Some(("number", requested(record, 4)?)),
        "github.run.get" => Some(("databaseId", requested(record, 3)?)),
        "nginxpm.proxy.get" => Some(("id", requested(record, 3)?)),
        "nginxpm.cert.get" => Some(("id", requested(record, 3)?)),
        "cubeapm.trace.get" => {
            let expected = requested(record, 3)?;
            ensure!(
                same_trace_id(expected, text(&data["traceID"])),
                "returned trace does not match the request"
            );
            for span in array(&data["spans"]) {
                if let Some(trace) = span["traceID"].as_str().filter(|trace| !trace.is_empty()) {
                    ensure!(
                        same_trace_id(expected, trace),
                        "returned span belongs to another trace"
                    );
                }
            }
            None
        }
        _ => None,
    };
    if let Some((field, expected)) = number {
        ensure!(
            expected.parse::<u64>().ok().filter(|n| *n > 0) == data[field].as_u64(),
            "returned resource does not match the request"
        );
    }
    Ok(())
}

fn command_matches(operation: &str, command: &[Value]) -> bool {
    let prefix: &[&str] = match operation {
        "cubeapm.metrics.range" => &["metrics", "query-range"],
        "cubeapm.logs.search" => &["logs", "query"],
        "cubeapm.traces.search" => &["traces", "search"],
        "cubeapm.trace.get" => &["traces", "get"],
        "grafana.metrics.range" => &["datasource", "query"],
        "grafana.annotations" => &["annotation", "list"],
        "jenkins.builds" => &["build", "list"],
        "jenkins.build.get" => &["build", "get"],
        "github.runs" => &["run", "list"],
        "github.run.get" => &["run", "view"],
        "elasticsearch.logs.search" => &["search", "query"],
        "kubernetes.pods" => &["get", "pods"],
        "kubernetes.events" => &["get", "events"],
        "jira.search" => &["issue", "search"],
        "nginxpm.proxy.get" => &["proxy", "get"],
        "nginxpm.cert.get" => &["cert", "get"],
        "aws.logs.search" => &["logs", "filter-log-events"],
        "git.show" => {
            let mut index = 1;
            while command
                .get(index)
                .is_some_and(|arg| ["-C", "-c"].contains(&text(arg)))
            {
                index += 2;
            }
            return command.get(index).is_some_and(|arg| arg == "show");
        }
        _ => return false,
    };
    prefix
        .iter()
        .enumerate()
        .all(|(i, word)| command.get(i + 1).and_then(Value::as_str) == Some(*word))
}

fn inspect(record: &Value, content: &[u8]) -> Result<Option<Value>> {
    let operation = text(&record["operation"]);
    let command = record["command"]
        .as_array()
        .ok_or_else(|| anyhow!("invalid evidence command metadata"))?;
    ensure!(
        command.len() >= 2
            && command
                .iter()
                .all(|arg| arg.as_str().is_some_and(|arg| !arg.contains('\0')))
            && command[0]
                .as_str()
                .is_some_and(|program| !program.is_empty()),
        "invalid evidence command metadata"
    );
    if let Some(provider) = operation.strip_prefix("run:") {
        ensure!(
            !provider.is_empty() && record["provider"] == provider && record["capability"] == "run",
            "inconsistent read operation metadata"
        );
        return Ok(None);
    }
    let Some(spec) = operations::OPERATIONS
        .iter()
        .find(|spec| spec.0 == operation)
    else {
        bail!("unknown evidence operation");
    };
    ensure!(
        record["provider"] == spec.1 && record["capability"] == spec.2,
        "inconsistent typed operation metadata"
    );
    ensure!(
        command_matches(operation, command),
        "command does not match the typed operation"
    );
    if spec.5 == "text" {
        return Ok(None);
    }
    if spec.5 == "ndjson" {
        for value in serde_json::Deserializer::from_slice(content).into_iter::<Value>() {
            ensure!(
                value.is_ok_and(|value| value.is_object()),
                "expected a stream of JSON log objects"
            );
        }
        return Ok(None);
    }
    let data: Value =
        serde_json::from_slice(content).map_err(|_| anyhow!("expected JSON output"))?;
    ensure!(
        !truth(&data["error"]) && text(&data["status"]) != "error",
        "provider returned an error payload"
    );
    if ["cubeapm.metrics.range", "grafana.metrics.range"].contains(&operation) {
        ensure!(
            data["status"] == "success" && data["data"]["resultType"] == "matrix",
            "expected a successful metric range envelope"
        );
        return crate::metrics::matrix_summary(&data["data"]["result"]).map(Some);
    }
    let valid = match operation {
        "cubeapm.traces.search" => rows(&data, |row| named(row, "TRACE_ID")),
        "cubeapm.trace.get" => {
            named(&data, "traceID")
                && data
                    .get("spans")
                    .is_some_and(|spans| spans.is_null() || rows(spans, |_| true))
        }
        // The Go renderers can serialize an empty nil slice as JSON null.
        "grafana.annotations" => {
            data.is_null() || rows(&data, |row| row["time"].as_i64().is_some())
        }
        "jenkins.builds" => data.is_null() || rows(&data, |row| numbered(row, "number")),
        "jenkins.build.get" => data.is_object() && numbered(&data, "number"),
        "github.runs" => data.is_null() || rows(&data, |row| numbered(row, "databaseId")),
        "github.run.get" => data.is_object() && numbered(&data, "databaseId"),
        "elasticsearch.logs.search" => {
            data["hits"].is_object() && rows(&data["hits"]["hits"], |_| true)
        }
        "kubernetes.pods" | "kubernetes.events" => {
            data.is_object() && rows(&data["items"], |row| named(&row["metadata"], "name"))
        }
        "jira.search" => data.is_null() || rows(&data, |row| named(row, "key")),
        "nginxpm.proxy.get" | "nginxpm.cert.get" => data.is_object() && numbered(&data, "id"),
        "aws.logs.search" => {
            data.is_object()
                && data
                    .get("events")
                    .is_none_or(|events| rows(events, |_| true))
        }
        _ => false,
    };
    ensure!(
        valid,
        "output does not match the typed operation's structure"
    );
    identity(record, &data)?;
    Ok(None)
}

// Reinterpret successful historical records without rewriting their provenance.
pub fn apply(record: &mut Value, content: &[u8]) {
    if text(&record["status"]) != "ok" {
        return;
    }
    if ["provider_error", "invalid_output"].contains(&text(&record["completeness"])) {
        record["status"] = json!("failed");
        return;
    }
    match inspect(record, content) {
        Err(error) => {
            record["status"] = json!("failed");
            record["completeness"] = json!("invalid_output");
            record["summary"] = json!(format!(
                "Evidence validation failed: {error}; inspect artifact and provenance."
            ));
        }
        Ok(Some(summary)) => {
            let retain_partial = ["partial", "limit_reached"]
                .contains(&text(&record["completeness"]))
                && ["bounded", "empty"].contains(&text(&summary["completeness"]));
            for (key, value) in object(&summary) {
                if !retain_partial || !["summary", "completeness"].contains(&key.as_str()) {
                    record[key] = value;
                }
            }
        }
        Ok(None) => {}
    }
}
