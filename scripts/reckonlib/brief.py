"""A local digest of one org: what is open, what changed, what waits on the operator.

Built only from saved files; it contacts no tool and asks no model, so it is safe
to run on a schedule and its output is the same for the same state."""
from datetime import datetime, timedelta, timezone
import json

from . import orgs
from .store import local_path, read_json


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def brief(h, ctx, registry, days=7):
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
    attention = [r["connection"] + ": " + r["state"] for r in orgs.describe(h, ctx, registry) if r["applies"] and r["state"] != "ready"]
    stale = []
    for layer in ("_shared", ctx["env"]):
        base = root / "knowledge" / layer
        for path in sorted(base.glob("*.md")) if base.is_dir() else []:
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).days
            if age > 90:
                stale.append(layer + "/" + path.name + " (" + str(age) + " days)")
    return {"org": ctx["org"], "environment": ctx["env"], "days": days, "open_sessions": sessions,
            "recent_findings": findings[:20], "writes_waiting_for_approval": pending,
            "writes_with_unknown_outcome": unknown, "connections_needing_attention": attention, "stale_knowledge": stale}


def render(result):
    lines = ["# Brief: " + result["org"] + " / " + result["environment"] + " (last " + str(result["days"]) + " days)", ""]
    sections = (("Writes with an unknown outcome (run reckon act reconcile)", "writes_with_unknown_outcome",
                 lambda r: r["id"] + "  " + r["operation"] + " " + str(r["target"] or "") + " via " + r["connection"]),
                ("Writes waiting for approval (reckon act apply)", "writes_waiting_for_approval",
                 lambda r: r["id"] + "  " + r["operation"] + " " + str(r["target"] or "") + " via " + r["connection"]),
                ("Open sessions", "open_sessions", lambda r: r["id"] + "  [" + r["environment"] + "] " + r["question"]),
                ("Recent findings", "recent_findings", lambda r: "[" + r["environment"] + "] " + r["text"] + "  (" + r["session"] + ")"),
                ("Connections needing attention", "connections_needing_attention", str),
                ("Knowledge not touched in 90 days", "stale_knowledge", str))
    for title, key, fmt in sections:
        lines += ["## " + title, ""] + (["- " + fmt(r) for r in result[key]] or ["None."]) + [""]
    return "\n".join(lines)
