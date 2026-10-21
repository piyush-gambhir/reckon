use crate::store::*;
use crate::*;
pub const OPERATIONS: &[(&str, &str, &str, &str, &str, &str)] = &[
    (
        "cubeapm.metrics.range",
        "cubeapm",
        "metrics.range",
        "query",
        "step",
        "json",
    ),
    (
        "cubeapm.logs.search",
        "cubeapm",
        "logs.search",
        "query service",
        "limit",
        "ndjson",
    ),
    (
        "cubeapm.traces.search",
        "cubeapm",
        "traces.search",
        "service",
        "query status env_label limit",
        "json",
    ),
    (
        "cubeapm.trace.get",
        "cubeapm",
        "trace.get",
        "trace_id",
        "",
        "json",
    ),
    (
        "grafana.metrics.range",
        "grafana",
        "metrics.range",
        "query datasource",
        "step",
        "json",
    ),
    (
        "grafana.annotations",
        "grafana",
        "changes.list",
        "",
        "tags limit",
        "json",
    ),
    (
        "jenkins.builds",
        "jenkins",
        "deployments.list",
        "job",
        "limit",
        "json",
    ),
    (
        "jenkins.build.get",
        "jenkins",
        "deployment.get",
        "job number",
        "",
        "json",
    ),
    (
        "github.runs",
        "github",
        "deployments.list",
        "repo",
        "workflow limit",
        "json",
    ),
    (
        "github.run.get",
        "github",
        "deployment.get",
        "repo number",
        "",
        "json",
    ),
    (
        "git.show",
        "git",
        "code.change",
        "path revision",
        "",
        "text",
    ),
    (
        "elasticsearch.logs.search",
        "elasticsearch",
        "logs.search",
        "index query",
        "field limit",
        "json",
    ),
    (
        "kubernetes.pods",
        "kubernetes",
        "workload.state",
        "namespace selector",
        "",
        "json",
    ),
    (
        "kubernetes.events",
        "kubernetes",
        "workload.events",
        "namespace",
        "",
        "json",
    ),
    (
        "jira.search",
        "jira",
        "history.tickets",
        "query",
        "limit",
        "json",
    ),
    (
        "nginxpm.proxy.get",
        "nginxpm",
        "routing.config",
        "number",
        "",
        "json",
    ),
    (
        "nginxpm.cert.get",
        "nginxpm",
        "certificate.config",
        "number",
        "",
        "json",
    ),
    (
        "aws.logs.search",
        "aws",
        "logs.search",
        "group query",
        "limit",
        "json",
    ),
];
pub fn positive(value: &Value, ceiling: u64) -> Result<String> {
    let v = if value.is_string() {
        text(value).to_owned()
    } else {
        value.to_string()
    };
    ensure!(
        !v.is_empty()
            && v.bytes().all(|c| c.is_ascii_digit())
            && v.parse::<u64>().is_ok_and(|n| (1..=ceiling).contains(&n)),
        "expected an integer between 1 and {ceiling}"
    );
    Ok(v)
}
pub struct Built {
    pub argv: Vec<String>,
    pub stdin: Option<Vec<u8>>,
    pub provider: String,
    pub capability: String,
    pub format: String,
    pub window: Value,
    pub limit: u64,
    pub temporal_scope: String,
}
pub fn build(operation: &str, p: &Value, bounds: &Value, root: &Path) -> Result<Built> {
    let entry = OPERATIONS
        .iter()
        .find(|v| v.0 == operation)
        .ok_or_else(|| anyhow!("unsupported operation: {operation}"))?;
    let (_, provider, capability, required, optional, output) = *entry;
    let allowed = format!("{required} {optional}")
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    ensure!(
        p.is_object() && object(p).keys().all(|k| allowed.contains(k)),
        "unknown parameters for {operation}"
    );
    for key in required.split_whitespace() {
        ensure!(
            p.get(key).is_some() && !p[key].is_null() && p[key] != json!(""),
            "{operation} requires {key}"
        );
    }
    for (k, v) in object(p) {
        if !["limit", "step", "number"].contains(&k.as_str()) {
            ensure!(
                v.is_string()
                    && text(&v).len() <= 8192
                    && !text(&v).contains('\0')
                    && !text(&v).starts_with('-'),
                "invalid argument: {k}"
            );
        }
    }
    let bounds = window(text(&bounds["from"]), text(&bounds["to"]))?;
    let start = text(&bounds["from"]);
    let end = text(&bounds["to"]);
    let length = (instant(end)? - instant(start)?).num_milliseconds() as f64 / 1000.0;
    let step = positive(
        p.get("step")
            .unwrap_or(&json!((length / 250.0).ceil().max(1.0) as u64)),
        86400,
    )?;
    if operation.ends_with("metrics.range") {
        ensure!(
            length / step.parse::<f64>()? <= 1000.0,
            "query exceeds 1000 time steps; increase step"
        );
    }
    let limit = positive(
        p.get("limit")
            .unwrap_or(&json!(if operation.contains("logs") { 100 } else { 20 })),
        200,
    )?;
    let get = |k: &str| text(&p[k]).to_owned();
    let default = |k: &str, d: &str| p[k].as_str().unwrap_or(d).to_owned();
    let mut body = None;
    let mut args: Vec<String> = match operation {
        "cubeapm.metrics.range" => vec![
            "cubeapm".into(),
            "metrics".into(),
            "query-range".into(),
            get("query"),
            "--from".into(),
            start.into(),
            "--to".into(),
            end.into(),
            "--step".into(),
            format!("{step}s"),
            "-o".into(),
            "json".into(),
        ],
        "cubeapm.logs.search" => {
            ensure!(
                orgs::valid_label(text(&p["service"])),
                "use a literal mapped service label for CubeAPM logs"
            );
            vec![
                "cubeapm".into(),
                "logs".into(),
                "query".into(),
                get("query"),
                "--service".into(),
                get("service"),
                "--from".into(),
                start.into(),
                "--to".into(),
                end.into(),
                "--limit".into(),
                limit.clone(),
                "-o".into(),
                "json".into(),
            ]
        }
        "cubeapm.traces.search" => {
            ensure!(
                !truth(&p["status"]) || ["error", "ok"].contains(&text(&p["status"])),
                "trace status must be error or ok"
            );
            vec![
                "cubeapm".into(),
                "traces".into(),
                "search".into(),
                "--service".into(),
                get("service"),
                "--from".into(),
                start.into(),
                "--to".into(),
                end.into(),
                "--limit".into(),
                limit.clone(),
                "-o".into(),
                "json".into(),
            ]
        }
        "cubeapm.trace.get" => vec![
            "cubeapm".into(),
            "traces".into(),
            "get".into(),
            get("trace_id"),
            "--from".into(),
            start.into(),
            "--to".into(),
            end.into(),
            "-o".into(),
            "json".into(),
        ],
        "grafana.metrics.range" => vec![
            "grafana".into(),
            "datasource".into(),
            "query".into(),
            get("datasource"),
            "--expr".into(),
            get("query"),
            "--query-type".into(),
            "range".into(),
            "--from".into(),
            start.into(),
            "--to".into(),
            end.into(),
            "--step".into(),
            format!("{step}s"),
            "-o".into(),
            "json".into(),
        ],
        "grafana.annotations" => vec![
            "grafana".into(),
            "annotation".into(),
            "list".into(),
            "--from".into(),
            instant(start)?.timestamp_millis().to_string(),
            "--to".into(),
            instant(end)?.timestamp_millis().to_string(),
            "--limit".into(),
            limit.clone(),
            "-o".into(),
            "json".into(),
        ],
        "jenkins.builds" | "jenkins.build.get" => {
            let mut a = vec![
                "jenkins".into(),
                "build".into(),
                if operation.ends_with("get") {
                    "get".into()
                } else {
                    "list".into()
                },
                get("job"),
            ];
            if operation.ends_with("get") {
                a.push(positive(&p["number"], 2147483647)?);
            } else {
                a.extend(["--limit".into(), limit.clone()]);
            }
            a.extend(["-o".into(), "json".into()]);
            a
        }
        "github.runs" | "github.run.get" => {
            ensure!(
                regex::Regex::new(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")?.is_match(text(&p["repo"])),
                "GitHub repo must be owner/name; host comes from the selected profile"
            );
            let fields =
                "databaseId,headSha,displayTitle,status,conclusion,createdAt,updatedAt,url";
            if operation == "github.runs" {
                vec![
                    "gh".into(),
                    "run".into(),
                    "list".into(),
                    "--repo".into(),
                    get("repo"),
                    "--created".into(),
                    format!("{start}..{end}"),
                    "--limit".into(),
                    limit.clone(),
                    "--json".into(),
                    fields.into(),
                ]
            } else {
                vec![
                    "gh".into(),
                    "run".into(),
                    "view".into(),
                    positive(&p["number"], 999999999999)?,
                    "--repo".into(),
                    get("repo"),
                    "--json".into(),
                    format!("{fields},jobs"),
                ]
            }
        }
        "git.show" => {
            ensure!(
                regex::Regex::new(r"^[0-9a-fA-F]{7,40}$")?.is_match(text(&p["revision"])),
                "git.show requires an explicit commit SHA established from deployment evidence"
            );
            let mut repo = orgs::expand_home(text(&p["path"]));
            if !repo.is_absolute() {
                repo = root.join(repo);
            }
            ensure!(repo.is_dir(), "local source repository is unavailable");
            vec![
                "git".into(),
                "-C".into(),
                repo.canonicalize()?.display().to_string(),
                "-c".into(),
                "core.fsmonitor=false".into(),
                "show".into(),
                "--no-ext-diff".into(),
                "--no-textconv".into(),
                "--format=fuller".into(),
                get("revision"),
                "--".into(),
            ]
        }
        "elasticsearch.logs.search" => {
            let field = default("field", "@timestamp");
            body = Some(serde_json::to_vec(
                &json!({"query":{"bool":{"filter":[{"range":{&field:{"gte":start,"lte":end}}}],"must":[{"simple_query_string":{"query":p["query"]}}]}},"sort":[{field:"desc"}]}),
            )?);
            vec![
                "es".into(),
                "search".into(),
                "query".into(),
                get("index"),
                "-f".into(),
                "-".into(),
                "--size".into(),
                limit.clone(),
                "-o".into(),
                "json".into(),
            ]
        }
        "kubernetes.pods" | "kubernetes.events" => vec![
            "kubectl".into(),
            "get".into(),
            if operation.ends_with("pods") {
                "pods".into()
            } else {
                "events".into()
            },
            "--namespace".into(),
            get("namespace"),
            "--request-timeout=15s".into(),
            "-o".into(),
            "json".into(),
        ],
        "jira.search" => vec![
            "jira".into(),
            "issue".into(),
            "search".into(),
            "--jql".into(),
            get("query"),
            "--limit".into(),
            limit.clone(),
            "--fields".into(),
            "summary,status,updated,labels".into(),
            "-o".into(),
            "json".into(),
        ],
        "nginxpm.proxy.get" | "nginxpm.cert.get" => vec![
            "nginxpm".into(),
            if operation.contains(".proxy.") {
                "proxy".into()
            } else {
                "cert".into()
            },
            "get".into(),
            positive(&p["number"], 2147483647)?,
            "-o".into(),
            "json".into(),
        ],
        "aws.logs.search" => vec![
            "aws".into(),
            "logs".into(),
            "filter-log-events".into(),
            "--log-group-name".into(),
            get("group"),
            "--filter-pattern".into(),
            get("query"),
            "--start-time".into(),
            instant(start)?.timestamp_millis().to_string(),
            "--end-time".into(),
            instant(end)?.timestamp_millis().to_string(),
            "--limit".into(),
            limit.clone(),
            "--no-paginate".into(),
            "--output".into(),
            "json".into(),
        ],
        _ => bail!("unsupported operation: {operation}"),
    };
    let extras: &[(&str, &str)] = match operation {
        "cubeapm.traces.search" => &[
            ("query", "--query"),
            ("status", "--status"),
            ("env_label", "--env"),
        ],
        "grafana.annotations" => &[("tags", "--tags")],
        "github.runs" => &[("workflow", "--workflow")],
        "kubernetes.pods" | "kubernetes.events" => &[("selector", "--selector")],
        _ => &[],
    };
    for (k, flag) in extras {
        if truth(&p[*k]) {
            args.extend([(*flag).into(), get(k)]);
        }
    }
    let scope = if [
        "kubernetes.pods",
        "kubernetes.events",
        "nginxpm.proxy.get",
        "nginxpm.cert.get",
        "jira.search",
    ]
    .contains(&operation)
    {
        "current_snapshot"
    } else if operation == "jenkins.builds" {
        "recent_builds_not_window_filtered"
    } else if operation == "git.show" {
        "commit"
    } else {
        "requested_window"
    };
    Ok(Built {
        argv: args,
        stdin: body,
        provider: provider.into(),
        capability: capability.into(),
        format: output.into(),
        window: bounds,
        limit: limit.parse()?,
        temporal_scope: scope.into(),
    })
}
pub fn playbook(name: &str) -> Vec<String> {
    match name{"errors"=>vec!["Compare error counts AND error rates against request volume and the baseline.","Group representative errors by endpoint/dependency; follow one concrete failing request.","Distinguish a new failure from a long-standing error exposed by increased volume."],"workload"=>vec!["Check signal presence before accepting zero errors as health.","Inspect current pods/events for restarts, OOM kills, scheduling and rollout failures.","Current state cannot establish historical pod state; correlate event timestamps with the incident."],"changes"=>vec!["Establish onset independently of deployment timestamps.","Read deployment/run details to identify the actual shipped revision, then inspect its relevant code.","Test configuration, routing and traffic changes as alternatives; temporal correlation is insufficient."],_=>vec!["Confirm latency and signal presence against the preceding equal-length baseline.","Separate per-call latency from request-volume change; attribute to endpoints and dependencies.","Establish the deployed SHA, inspect the relevant code, and test the causal mechanism."]}.into_iter().map(str::to_owned).collect()
}
pub fn plan(session: &Value) -> Result<(Vec<Value>, Vec<String>)> {
    if !truth(&session["service_context"]) || !truth(&session["window"]) {
        return Ok((vec![], vec![]));
    }
    let sources = &session["service_context"]["sources"];
    let bounds = &session["window"];
    let start = instant(text(&bounds["from"]))?;
    let baseline = json!({"from":(start-(instant(text(&bounds["to"]))?-start)).to_rfc3339(),"to":bounds["from"]});
    let mut tasks = vec![];
    let mut gaps = vec![];
    let mut metric_source = false;
    for provider in ["cubeapm", "grafana"] {
        let source = &sources[provider];
        for (metric, query) in object(&source["metrics"]) {
            ensure!(query.is_string(), "mapped metric queries must be strings");
            metric_source = true;
            let service = source.get("service").unwrap_or(&session["service"]);
            let mut params =
                json!({"query":text(&query).replace("$service",&serde_json::to_string(service)?)});
            if provider == "grafana" {
                params["datasource"] = source.get("datasource").cloned().unwrap_or(json!(""));
            }
            for (title, target) in [("baseline", &baseline), ("incident", bounds)] {
                tasks.push(json!({"label":format!("{metric} / {title}"),"operation":format!("{provider}.metrics.range"),"params":params,"window":target}));
            }
        }
    }
    if !metric_source {
        gaps.push("No mapped metric queries: symptom magnitude, baseline and signal presence need another source.".into());
    }
    let mut add = |label: &str, operation: &str, params: Value| {
        tasks.push(json!({"label":label,"operation":operation,"params":params,"window":bounds}));
    };
    let s = &sources["cubeapm"];
    if truth(&s["logs_query"]) {
        add(
            "error examples",
            "cubeapm.logs.search",
            json!({"query":s["logs_query"],"service":s.get("service").unwrap_or(&session["service"])}),
        );
    }
    let s = &sources["grafana"];
    if truth(&s["annotation_tags"]) {
        add(
            "annotations",
            "grafana.annotations",
            json!({"tags":s["annotation_tags"]}),
        );
    }
    let s = &sources["jenkins"];
    if truth(&s["job"]) {
        add(
            "recent builds (inspect timestamps)",
            "jenkins.builds",
            json!({"job":s["job"]}),
        );
    }
    let s = &sources["github"];
    if truth(&s["repo"]) {
        let mut p = json!({"repo":s["repo"]});
        if let Some(w) = s.get("workflow") {
            p["workflow"] = w.clone();
        }
        add("workflow runs", "github.runs", p);
    }
    let s = &sources["git"];
    if truth(&s["path"]) && truth(&s["revision"]) {
        add(
            "mapped commit (verify deployment attribution)",
            "git.show",
            json!({"path":s["path"],"revision":s["revision"]}),
        );
    } else {
        gaps.push("Deployed code is not captured. Establish the deployed SHA from build/run evidence, then collect git.show against a local repository.".into());
    }
    let s = &sources["elasticsearch"];
    if truth(&s["index"]) {
        add(
            "ES log examples",
            "elasticsearch.logs.search",
            json!({"index":s["index"],"query":s.get("query").unwrap_or(&json!("error")),"field":s.get("field").unwrap_or(&json!("@timestamp"))}),
        );
    }
    let s = &sources["kubernetes"];
    if truth(&s["namespace"]) {
        add(
            "current workload events",
            "kubernetes.events",
            json!({"namespace":s["namespace"]}),
        );
        if truth(&s["selector"]) {
            add(
                "current pods",
                "kubernetes.pods",
                json!({"namespace":s["namespace"],"selector":s["selector"]}),
            );
        }
    }
    let s = &sources["aws"];
    if truth(&s["group"]) {
        add(
            "CloudWatch log examples",
            "aws.logs.search",
            json!({"group":s["group"],"query":s.get("query").unwrap_or(&json!("ERROR"))}),
        );
    }
    let s = &sources["jira"];
    if truth(&s["query"]) {
        add(
            "related tickets (current status)",
            "jira.search",
            json!({"query":s["query"]}),
        );
    }
    let s = &sources["nginxpm"];
    for (k, op) in [
        ("proxy_id", "nginxpm.proxy.get"),
        ("certificate_id", "nginxpm.cert.get"),
    ] {
        if truth(&s[k]) {
            add(&format!("current {k}"), op, json!({"number":s[k]}));
        }
    }
    ensure!(
        tasks.len() <= 24,
        "playbook exceeds 24 operations; narrow the service's mapped sources/metrics"
    );
    Ok((tasks, gaps))
}
