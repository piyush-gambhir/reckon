"""Small read-shaped capability surface over existing CLIs.

Custom CLI argument contracts are checked against the releases pinned in
scripts/cli-releases.csv; recheck them whenever those pins change.
No arbitrary shell, database, mutation, or remote git operation is accepted.
"""
from datetime import timedelta
import json
import math
from pathlib import Path
import re

from .store import instant, window

# name: provider, capability, required parameters, optional parameters, output
OPERATIONS = {
    "cubeapm.metrics.range": ("cubeapm", "metrics.range", "query", "step", "json"),
    "cubeapm.logs.search": ("cubeapm", "logs.search", "query service", "limit", "ndjson"),
    "cubeapm.traces.search": ("cubeapm", "traces.search", "service", "query status env_label limit", "json"),
    "cubeapm.trace.get": ("cubeapm", "trace.get", "trace_id", "", "json"),
    "grafana.metrics.range": ("grafana", "metrics.range", "query datasource", "step", "json"),
    "grafana.annotations": ("grafana", "changes.list", "", "tags limit", "json"),
    "jenkins.builds": ("jenkins", "deployments.list", "job", "limit", "json"),
    "jenkins.build.get": ("jenkins", "deployment.get", "job number", "", "json"),
    "github.runs": ("github", "deployments.list", "repo", "workflow limit", "json"),
    "github.run.get": ("github", "deployment.get", "repo number", "", "json"),
    "git.show": ("git", "code.change", "path revision", "", "text"),
    "elasticsearch.logs.search": ("elasticsearch", "logs.search", "index query", "field limit", "json"),
    "kubernetes.pods": ("kubernetes", "workload.state", "namespace selector", "", "json"),
    "kubernetes.events": ("kubernetes", "workload.events", "namespace", "", "json"),
    "jira.search": ("jira", "history.tickets", "query", "limit", "json"),
    "nginxpm.proxy.get": ("nginxpm", "routing.config", "number", "", "json"),
    "nginxpm.cert.get": ("nginxpm", "certificate.config", "number", "", "json"),
    "aws.logs.search": ("aws", "logs.search", "group query", "limit", "json"),
}
SNAPSHOTS = {"kubernetes.pods", "kubernetes.events", "nginxpm.proxy.get", "nginxpm.cert.get", "jira.search"}
PLAYBOOKS = {
    "latency": ["Confirm latency and signal presence against the preceding equal-length baseline.",
                "Separate per-call latency from request-volume change; attribute to endpoints and dependencies.",
                "Establish the deployed SHA, inspect the relevant code, and test the causal mechanism."],
    "errors": ["Compare error counts AND error rates against request volume and the baseline.",
               "Group representative errors by endpoint/dependency; follow one concrete failing request.",
               "Distinguish a new failure from a long-standing error exposed by increased volume."],
    "workload": ["Check signal presence before accepting zero errors as health.",
                 "Inspect current pods/events for restarts, OOM kills, scheduling and rollout failures.",
                 "Current state cannot establish historical pod state; correlate event timestamps with the incident."],
    "changes": ["Establish onset independently of deployment timestamps.",
                "Read deployment/run details to identify the actual shipped revision, then inspect its relevant code.",
                "Test configuration, routing and traffic changes as alternatives; temporal correlation is insufficient."],
}


def positive(value, ceiling):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or not 1 <= int(value) <= ceiling:
        raise ValueError("expected an integer between 1 and " + str(ceiling))
    return str(value)


def build(operation, params, bounds, root):
    if operation not in OPERATIONS:
        raise ValueError("unsupported operation: " + operation)
    provider, capability, required, optional, output = OPERATIONS[operation]
    if not isinstance(params, dict) or set(params) - set((required + " " + optional).split()):
        raise ValueError("unknown parameters for " + operation)
    for key in required.split():
        if key not in params or params[key] in ("", None):
            raise ValueError(operation + " requires " + key)
    p = dict(params)
    for key, value in p.items():
        if key in ("limit", "step", "number"):
            continue
        if not isinstance(value, str) or len(value) > 8192 or "\x00" in value or value.startswith("-"):
            raise ValueError("invalid argument: " + key)
    bounds = window(bounds["from"], bounds["to"])
    start, end = bounds["from"], bounds["to"]
    length = (instant(end) - instant(start)).total_seconds()
    step = positive(p.get("step", max(1, math.ceil(length / 250))), 86400)
    if operation.endswith("metrics.range") and length / int(step) > 1000:
        raise ValueError("query exceeds 1000 time steps; increase step")
    limit = positive(p.get("limit", 100 if "logs" in operation else 20), 200)
    body = None
    if operation == "cubeapm.metrics.range":
        args = ["cubeapm", "metrics", "query-range", p["query"], "--from", start, "--to", end, "--step", step + "s", "-o", "json"]
    elif operation == "cubeapm.logs.search":
        # Quote the service value ourselves; the CLI's --service concatenates raw LogsQL.
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", p["service"]):
            raise ValueError("use a literal mapped service label for CubeAPM logs")
        args = ["cubeapm", "logs", "query", p["query"], "--service", p["service"], "--from", start, "--to", end, "--limit", limit, "-o", "json"]
    elif operation == "cubeapm.traces.search":
        args = ["cubeapm", "traces", "search", "--service", p["service"], "--from", start, "--to", end, "--limit", limit, "-o", "json"]
        for key, flag in (("query", "--query"), ("status", "--status"), ("env_label", "--env")):
            if p.get(key):
                args += [flag, p[key]]
        if p.get("status") and p["status"] not in ("error", "ok"):
            raise ValueError("trace status must be error or ok")
    elif operation == "cubeapm.trace.get":
        args = ["cubeapm", "traces", "get", p["trace_id"], "--from", start, "--to", end, "-o", "json"]
    elif operation == "grafana.metrics.range":
        args = ["grafana", "datasource", "query", p["datasource"], "--expr", p["query"], "--query-type", "range", "--from", start, "--to", end, "--step", step + "s", "-o", "json"]
    elif operation == "grafana.annotations":
        args = ["grafana", "annotation", "list", "--from", str(int(instant(start).timestamp() * 1000)), "--to", str(int(instant(end).timestamp() * 1000)), "--limit", limit, "-o", "json"]
        if p.get("tags"):
            args += ["--tags", p["tags"]]
    elif operation.startswith("jenkins."):
        args = ["jenkins", "build", "get" if operation.endswith(".get") else "list", p["job"]]
        args += [positive(p["number"], 2147483647)] if operation.endswith(".get") else ["--limit", limit]
        args += ["-o", "json"]
    elif operation.startswith("github."):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", p["repo"]):
            raise ValueError("GitHub repo must be owner/name; host comes from the selected profile")
        if operation == "github.runs":
            args = ["gh", "run", "list", "--repo", p["repo"], "--created", start + ".." + end, "--limit", limit,
                    "--json", "databaseId,headSha,displayTitle,status,conclusion,createdAt,updatedAt,url"]
            if p.get("workflow"):
                args += ["--workflow", p["workflow"]]
        else:
            args = ["gh", "run", "view", positive(p["number"], 999999999999), "--repo", p["repo"],
                    "--json", "databaseId,headSha,displayTitle,status,conclusion,createdAt,updatedAt,url,jobs"]
    elif operation == "git.show":
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", p["revision"]):
            raise ValueError("git.show requires an explicit commit SHA established from deployment evidence")
        repo = Path(p["path"]).expanduser()
        if not repo.is_absolute():
            repo = root / repo
        if not repo.is_dir():
            raise ValueError("local source repository is unavailable")
        args = ["git", "-C", str(repo.resolve()), "-c", "core.fsmonitor=false", "show", "--no-ext-diff", "--no-textconv", "--format=fuller", p["revision"], "--"]
    elif operation == "elasticsearch.logs.search":
        field = p.get("field", "@timestamp")
        body = json.dumps({"query": {"bool": {"filter": [{"range": {field: {"gte": start, "lte": end}}}],
                                               "must": [{"simple_query_string": {"query": p["query"]}}]}},
                           "sort": [{field: "desc"}]}).encode()
        args = ["es", "search", "query", p["index"], "-f", "-", "--size", limit, "-o", "json"]
    elif operation.startswith("kubernetes."):
        args = ["kubectl", "get", "pods" if operation.endswith("pods") else "events", "--namespace", p["namespace"], "--request-timeout=15s", "-o", "json"]
        if p.get("selector"):
            args += ["--selector", p["selector"]]
    elif operation == "jira.search":
        args = ["jira", "issue", "search", "--jql", p["query"], "--limit", limit, "--fields", "summary,status,updated,labels", "-o", "json"]
    elif operation.startswith("nginxpm."):
        args = ["nginxpm", "proxy" if ".proxy." in operation else "cert", "get", positive(p["number"], 2147483647), "-o", "json"]
    elif operation == "aws.logs.search":
        args = ["aws", "logs", "filter-log-events", "--log-group-name", p["group"], "--filter-pattern", p["query"],
                "--start-time", str(int(instant(start).timestamp() * 1000)), "--end-time", str(int(instant(end).timestamp() * 1000)),
                "--limit", limit, "--no-paginate", "--output", "json"]
    return {"argv": args, "stdin": body, "provider": provider, "capability": capability, "format": output,
            "window": bounds, "limit": int(limit),
            "temporal_scope": "current_snapshot" if operation in SNAPSHOTS else "recent_builds_not_window_filtered" if operation == "jenkins.builds" else "commit" if operation == "git.show" else "requested_window"}


def plan(session):
    """Recipes use curated queries, never invented metric names or labels."""
    sources = session["service_context"].get("sources", {})
    bounds = session["window"]
    delta = instant(bounds["to"]) - instant(bounds["from"])
    baseline = {"from": (instant(bounds["from"]) - delta).isoformat(), "to": bounds["from"]}
    tasks, gaps = [], []

    def add(label, operation, params, target=None):
        tasks.append({"label": label, "operation": operation, "params": params, "window": target or bounds})

    metric_source = False
    for provider in ("cubeapm", "grafana"):
        source = sources.get(provider, {})
        for metric, query in source.get("metrics", {}).items():
            if not isinstance(query, str):
                raise ValueError("mapped metric queries must be strings")
            metric_source = True
            params = {"query": query.replace("$service", json.dumps(source.get("service", session["service"]))) }
            if provider == "grafana":
                params["datasource"] = source.get("datasource", "")
            for title, target in (("baseline", baseline), ("incident", bounds)):
                add(metric + " / " + title, provider + ".metrics.range", params, target)
    if not metric_source:
        gaps.append("No mapped metric queries: symptom magnitude, baseline and signal presence need another source.")
    source = sources.get("cubeapm", {})
    if source.get("logs_query"):
        add("error examples", "cubeapm.logs.search", {"query": source["logs_query"], "service": source.get("service", session["service"])})
    source = sources.get("grafana", {})
    if source.get("annotation_tags"):
        add("annotations", "grafana.annotations", {"tags": source["annotation_tags"]})
    source = sources.get("jenkins", {})
    if source.get("job"):
        add("recent builds (inspect timestamps)", "jenkins.builds", {"job": source["job"]})
    source = sources.get("github", {})
    if source.get("repo"):
        add("workflow runs", "github.runs", {k: source[k] for k in ("repo", "workflow") if k in source})
    source = sources.get("git", {})
    if source.get("path") and source.get("revision"):
        add("mapped commit (verify deployment attribution)", "git.show", {"path": source["path"], "revision": source["revision"]})
    else:
        gaps.append("Deployed code is not captured. Establish the deployed SHA from build/run evidence, then collect git.show against a local repository.")
    source = sources.get("elasticsearch", {})
    if source.get("index"):
        add("ES log examples", "elasticsearch.logs.search", {"index": source["index"], "query": source.get("query", "error"), "field": source.get("field", "@timestamp")})
    source = sources.get("kubernetes", {})
    if source.get("namespace"):
        add("current workload events", "kubernetes.events", {"namespace": source["namespace"]})
        if source.get("selector"):
            add("current pods", "kubernetes.pods", {"namespace": source["namespace"], "selector": source["selector"]})
    source = sources.get("aws", {})
    if source.get("group"):
        add("CloudWatch log examples", "aws.logs.search", {"group": source["group"], "query": source.get("query", "ERROR")})
    source = sources.get("jira", {})
    if source.get("query"):
        add("related tickets (current status)", "jira.search", {"query": source["query"]})
    source = sources.get("nginxpm", {})
    for key, operation in (("proxy_id", "nginxpm.proxy.get"), ("certificate_id", "nginxpm.cert.get")):
        if source.get(key):
            add("current " + key, operation, {"number": source[key]})
    if len(tasks) > 24:
        raise ValueError("playbook exceeds 24 operations; narrow the service's mapped sources/metrics")
    return tasks, gaps
