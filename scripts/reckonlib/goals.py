"""Goals: an org's targets, each bound to a read, reported from saved evidence.

knowledge/_shared/goals.json (all environments) and knowledge/<env>/goals.json hold
{"goals": [{"id", "title", "provider", "command": [words], "path": "current.clicks",
"target": 5000, "direction": "up" | "down"}]}. The brief takes the newest ok
evidence of that read in this environment and reads the number at path, so it
contacts no tool; the value is as fresh as the last saved read."""
import json
import re

from . import connectors, orgs
from .store import local_path, read_json

PATH = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")


def load(root, env):
    goals = {}
    for layer in ("_shared", env):
        path = root / "knowledge" / layer / "goals.json"
        if not path.is_file():
            continue
        data = read_json(path)
        for goal in data.get("goals", []) if isinstance(data, dict) else []:
            validate(goal, layer + "/goals.json")
            goals[goal["id"]] = goal
    return list(goals.values())


def validate(goal, name):
    ok = (isinstance(goal, dict) and orgs.NAME.fullmatch(str(goal.get("id", ""))) and isinstance(goal.get("title"), str)
          and isinstance(goal.get("provider"), str) and isinstance(goal.get("command"), list) and goal["command"]
          and all(isinstance(w, str) and w for w in goal["command"]) and PATH.fullmatch(str(goal.get("path", "")))
          and isinstance(goal.get("target"), (int, float)) and not isinstance(goal.get("target"), bool)
          and goal.get("direction", "up") in ("up", "down"))
    if not ok:
        raise ValueError(name + ": each goal needs id, title, provider, command (words), path (a.b.0), a numeric target "
                         "and direction up or down: " + json.dumps(goal)[:200])


def pick(data, path):
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data if isinstance(data, (int, float)) and not isinstance(data, bool) else None


def latest(root, env, goal, registry=None):
    """The newest ok evidence of the goal's read in this environment, with the value at its path."""
    spec = (registry or {}).get(goal["provider"])
    best = None
    sessions = root / "sessions"
    for record_path in sessions.glob("*/evidence/*/record.json") if sessions.is_dir() else []:
        try:
            record = read_json(record_path)
        except ValueError:
            continue
        if (record.get("environment") != env or record.get("status") != "ok" or record.get("provider") != goal["provider"]
                or not connectors.matches(goal["command"], connectors.tokens(record.get("command", [])[1:], spec))):
            continue
        # Observation time first; the record's write time breaks ties within a second.
        key = (record.get("observed_at", ""), record_path.stat().st_mtime_ns)
        if best is None or key > best[0]:
            best = (key, record, record_path.parent)
    if not best:
        return None
    _, record, folder = best
    try:
        value = pick(json.loads(local_path(folder, "stdout.txt").read_text(encoding="utf-8")), goal["path"])
    except (ValueError, OSError):
        value = None
    return {"value": value, "observed_at": record.get("observed_at"), "evidence": record["id"], "session": record.get("session")}


def report(root, env, registry=None):
    rows = []
    for goal in load(root, env):
        row = {"goal": goal["id"], "title": goal["title"], "target": goal["target"], "direction": goal.get("direction", "up")}
        seen = latest(root, env, goal, registry)
        if not seen:
            row.update(status="no reading", next="reckon run " + goal["provider"] + " --session <id> -- " + " ".join(goal["command"]) + " ... -o json")
        elif seen["value"] is None:
            row.update(seen, status="unreadable", next="no number at " + goal["path"] + " in evidence " + seen["evidence"])
        else:
            hit = seen["value"] >= goal["target"] if row["direction"] == "up" else seen["value"] <= goal["target"]
            row.update(seen, status="on track" if hit else "behind")
        rows.append(row)
    return rows
