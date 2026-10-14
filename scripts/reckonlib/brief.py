"""A local digest of one org: what is open, what changed, what waits on the operator.

Built only from saved files; it contacts no tool and asks no model, so it is safe
to run on a schedule and its output is the same for the same state."""
from datetime import datetime, timedelta, timezone
import json

from . import activity, goals, orgs, spend
from .store import local_path, read_json


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def brief(h, ctx, registry, days=7, envs=None):
    """One org; envs (default: the context's) widens connections, knowledge and activity."""
    envs = envs or [ctx["env"]]
    root = orgs.workspace(h, ctx["org"])
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sessions, findings = [], []
    for path in sorted(local_path(root, "sessions").glob("*/session.json"), reverse=True) if (root / "sessions").is_dir() else []:
        data = read_json(path)
        if data.get("status") == "open":
            sessions.append({"id": data["id"], "environment": data["environment"], "question": data["question"], "since": data["created_at"]})
        for note in data.get("notes", []):
            if note["kind"] == "finding" and parse(note["recorded_at"]) >= since:
                findings.append({"session": data["id"], "environment": data["environment"], "text": note["text"], "at": note["recorded_at"]})
    pending, unknown = [], []
    for path in sorted(local_path(root, "actions").glob("*/action.json"), reverse=True) if (root / "actions").is_dir() else []:
        action = read_json(path)
        row = {"id": action["id"], "operation": action["operation"], "connection": action["connection"],
               "environment": action["environment"], "target": action["params"].get("repo")}
        if action["status"] == "prepared":
            pending.append(row)
        elif action["status"] in ("unknown", "running"):
            unknown.append(row)
    attention = []
    for env in envs:
        scoped = ctx if env == ctx["env"] else orgs.context(h, ctx["org"], env, registry)
        prefix = "[" + env + "] " if len(envs) > 1 else ""
        attention += [prefix + r["connection"] + ": " + r["state"] for r in orgs.describe(h, scoped, registry)
                      if r["applies"] and r["state"] != "ready" and prefix + r["connection"] + ": " + r["state"] not in attention]
    events = [e for e in activity.read(h, ctx["org"], days, limit=100000) if e.get("environment") in envs]
    refused = [{"at": e["at"], "environment": e["environment"], "actor": e["actor"], "target": e.get("target"),
                "command": " ".join(e.get("command", [])), "detail": e.get("detail", "")} for e in events if e["event"] == "refused"]
    targets = [dict(row, environment=env) for env in envs for row in goals.report(root, env, registry)]
    from .watches import inbox
    new = sorted((item for env in envs for item in inbox(h, ctx["org"], days, env)), key=lambda i: i["at"], reverse=True)
    stale = []
    for layer in ["_shared"] + list(envs):
        base = root / "knowledge" / layer
        for path in sorted(base.glob("*.md")) if base.is_dir() else []:
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).days
            if age > 90:
                stale.append(layer + "/" + path.name + " (" + str(age) + " days)")
    return {"org": ctx["org"], "environment": ctx["env"] if len(envs) == 1 else "all", "days": days, "open_sessions": sessions,
            "recent_findings": findings[:20], "writes_waiting_for_approval": pending,
            "writes_with_unknown_outcome": unknown, "connections_needing_attention": attention, "stale_knowledge": stale,
            "refused_runs": refused[:20], "runs": sum(e["event"] == "run" for e in events), "goals": targets,
            "inbox": new[:30]}


def all_orgs(h, registry, days=7):
    """Every org, every environment: the morning view for someone running several businesses."""
    if activity.pinned():
        raise ValueError("a pinned agent sees only its own org; run reckon brief --all-orgs in your own terminal")
    results = []
    for org in orgs.orgs(h):
        data = orgs.load(h, org, registry)
        ctx = orgs.context(h, org, data["environments"][0], registry)
        result = brief(h, ctx, registry, days, data["environments"])
        result["agent_tokens"] = spend.report(h, org, days)["tokens"]
        warning = spend.over(h, org)
        if warning:
            result["connections_needing_attention"].append(warning)
        results.append(result)
    return {"days": days, "orgs": results}


def render_all(result):
    lines = ["# Brief: all orgs (last " + str(result["days"]) + " days)", "",
             "| Org | Unknown writes | Waiting writes | Open sessions | Findings | Refused | Runs | Attention | Agent tokens |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in result["orgs"]:
        lines.append("| " + " | ".join([r["org"]] + [str(len(r[k])) for k in ("writes_with_unknown_outcome", "writes_waiting_for_approval",
                     "open_sessions", "recent_findings", "refused_runs")] + [str(r["runs"]), str(len(r["connections_needing_attention"])), format(r.get("agent_tokens", 0), ",")]) + " |")
    if not result["orgs"]:
        lines.append("| no orgs yet | | | | | | | | |")
    lines.append("")
    for r in result["orgs"]:
        for line in render(r).splitlines():
            # One level down: the org becomes a section and its sections become subsections.
            lines.append("## " + line[9:] if line.startswith("# Brief: ") else "#" + line if line.startswith("## ") else line)
        lines.append("")
    return "\n".join(lines)


def goal_line(r):
    where = "[" + r["environment"] + "] " + r["title"] + ": "
    if r["status"] in ("no reading", "unreadable", "invalid"):
        return where + r["status"] + " (" + r["next"] + ")"
    return (where + format(r["value"], ",") + " vs target " + format(r["target"], ",") + ", " + r["status"] +
            " (observed " + str(r["observed_at"])[:10] + ", " + r["evidence"] + ")")


def render(result):
    lines = ["# Brief: " + result["org"] + " / " + result["environment"] + " (last " + str(result["days"]) + " days)", ""]
    sections = (("New from watches (reckon inbox)", "inbox",
                 lambda r: "[" + r["environment"] + "] " + r["watch"] + ": " + r["title"] + "  (" + r["at"][:16] + ")"),
                ("Goals (from the newest saved read)", "goals", goal_line),
                ("Writes with an unknown outcome (run reckon act reconcile)", "writes_with_unknown_outcome",
                 lambda r: r["id"] + "  " + r["operation"] + " " + str(r["target"] or "") + " via " + r["connection"]),
                ("Writes waiting for approval (reckon act apply)", "writes_waiting_for_approval",
                 lambda r: r["id"] + "  " + r["operation"] + " " + str(r["target"] or "") + " via " + r["connection"]),
                ("Open sessions", "open_sessions", lambda r: r["id"] + "  [" + r["environment"] + "] " + r["question"]),
                ("Recent findings", "recent_findings", lambda r: "[" + r["environment"] + "] " + r["text"] + "  (" + r["session"] + ")"),
                ("Refused runs (wrong context, denied commands, budgets)", "refused_runs",
                 lambda r: r["at"] + "  [" + r["environment"] + "] " + r["actor"] + " " + str(r["target"]) + " " + r["command"] + ": " + r["detail"]),
                ("Connections needing attention", "connections_needing_attention", str),
                ("Knowledge not touched in 90 days", "stale_knowledge", str))
    for title, key, fmt in sections:
        lines += ["## " + title, ""] + (["- " + fmt(r) for r in result[key]] or ["None."]) + [""]
    return "\n".join(lines)
