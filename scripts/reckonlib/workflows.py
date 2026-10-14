"""Workflow recipes: parameters and required checks for repeatable work.

A recipe is a JSON file next to its playbook in skills/reckon/workflows/, or an
org's own in its workspace's workflows/ folder (same id replaces the built-in).
reckon task --workflow starts a session from it and keeps a copy of the recipe in
the session, so reckon check and reckon close judge completion from the evidence
that was saved, not from closing prose."""
from datetime import datetime, timezone
from pathlib import Path
import re

from . import connectors, orgs
from .store import read_json

BUILTIN = Path(__file__).resolve().parents[2] / "skills" / "reckon" / "workflows"
SKIP = re.compile(r"skip(?:ped)? ([a-z0-9][a-z0-9-]*):\s*\S", re.I)


def validate(data, name):
    def fail(why):
        raise ValueError("workflow " + name + ": " + why)
    if not isinstance(data, dict) or not orgs.NAME.fullmatch(str(data.get("id", ""))):
        fail("needs an id of lowercase letters, digits and hyphens")
    for key in ("title", "question"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            fail("needs a " + key)
    for key, spec in data.get("params", {}).items():
        if not orgs.NAME.fullmatch(key) or not isinstance(spec, dict):
            fail("parameter " + repr(key) + " is not valid")
    ids = set()
    for check in data.get("checks", []):
        if not orgs.NAME.fullmatch(str(check.get("id", ""))) or check["id"] in ids or not check.get("text"):
            fail("each check needs a unique id and a text")
        ids.add(check["id"])
        ways = check.get("any")
        if check.get("findings") is None and not (isinstance(ways, list) and ways and all(
                isinstance(w, dict) and isinstance(w.get("provider"), str) and isinstance(w.get("command"), list) for w in ways)):
            fail("check " + check["id"] + " needs any: [{provider, command}] or findings: N")
    if not data.get("checks"):
        fail("needs at least one check")


def recipes(root):
    found = {}
    for folder in (BUILTIN, root / "workflows"):
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            data = read_json(path)
            validate(data, path.name)
            found[data["id"]] = dict(data, source=str(path))
    return found


def start(root, recipe_id, given):
    """The session question, area, budget and recipe copy for reckon task --workflow."""
    found = recipes(root)
    if recipe_id not in found:
        raise ValueError("unknown workflow " + repr(recipe_id) + "; reckon workflows lists them: " + ", ".join(sorted(found)))
    recipe, given, params = found[recipe_id], dict(given), {}
    for key, spec in recipe.get("params", {}).items():
        value = given.pop(key, spec.get("default"))
        if value == "today":
            value = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if value is None:
            raise ValueError(recipe_id + " needs --param " + key + "=<" + spec.get("help", "value") + ">")
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
            raise ValueError("--param " + key + " must match " + spec["pattern"])
        params[key] = value
    if given:
        raise ValueError(recipe_id + " has no parameter " + ", ".join(sorted(given)) + "; it takes: " + ", ".join(recipe.get("params", {})))
    question = recipe["question"]
    for key, value in params.items():
        question = question.replace("{" + key + "}", value)
    snapshot = {k: recipe[k] for k in ("id", "title", "checks", "source") if k in recipe}
    snapshot["params"] = params
    playbook = recipe.get("playbook")
    return question, recipe.get("area"), recipe.get("budget"), snapshot, playbook


def met(way, record):
    if record.get("status") != "ok" or record.get("provider") != way["provider"]:
        return False
    return connectors.matches(way["command"], connectors.tokens(record.get("command", [])[1:]))


def progress(session, records, available):
    """Each check: met (evidence ids), skipped (a noted reason), unavailable (no such
    connection here) or todo. available is the set of providers this env can reach."""
    recipe = session.get("workflow")
    if not recipe:
        return None
    skipped = {}
    for note in session.get("notes", []):
        match = SKIP.match(note["text"]) if note["kind"] == "question" else None
        if match:
            skipped[match.group(1).lower()] = note["text"]
    findings = sum(n["kind"] == "finding" for n in session.get("notes", []))
    rows = []
    for check in recipe["checks"]:
        row = {"id": check["id"], "text": check["text"]}
        if check.get("findings") is not None:
            row["status"] = "met" if findings >= check["findings"] else "skipped" if check["id"] in skipped else "todo"
        else:
            hits = [r["id"] for r in records if any(met(w, r) for w in check["any"])]
            if hits:
                row.update(status="met", evidence=hits)
            elif check["id"] in skipped:
                row.update(status="skipped", reason=skipped[check["id"]])
            elif not any(w["provider"] in available for w in check["any"]):
                row["status"] = "unavailable"
            else:
                row["status"] = "todo"
        rows.append(row)
    return {"workflow": recipe["id"], "params": recipe.get("params", {}), "checks": rows,
            "todo": [r["id"] for r in rows if r["status"] == "todo"]}
