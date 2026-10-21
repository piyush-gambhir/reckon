"""Watches: saved reads polled for new items (a new dispute, a new Sentry issue, a
failed deploy), which land in the org's inbox for the brief and the agent.

A watch names a connection, an environment, the read's arguments, where the list
of items is in its JSON output and which field identifies an item. `reckon watch
run` (by hand, cron or launchd) runs each read through the same checks as
`reckon run`, remembers the ids it has seen, and appends new items to
workspace/inbox.jsonl. The first run only records what exists, so nothing floods in."""
import hashlib
import json
import os
import shutil

from . import actions, activity, connectors, orgs
from .goals import PATH
from .store import local_path, lock, now, pick, read_json, write_json

SEEN_LIMIT = 5000


def path(h, org):
    return local_path(orgs.org_path(h, org), "watches.json")


def load(h, org):
    target = path(h, org)
    return read_json(target) if target.is_file() else {}


def add(h, ctx, name, target, args, items, key, title=None, registry=None, production=False):
    orgs.name(name, "watch")
    if ctx["production"] and not production:
        raise ValueError(ctx["org"] + "/" + ctx["env"] + " is production; a watch there runs unattended, so pass --production to confirm")
    for label, value in (("--items", items), ("--key", key), ("--title", title)):
        if value and not PATH.fullmatch(value):
            raise ValueError(label + " is a dotted path such as data or result.items")
    if not args:
        raise ValueError("give the read after --, e.g. -- disputes list --limit 20")
    cname, conn = orgs.resolve(ctx, target)
    spec = (registry or orgs.providers())[conn["provider"]]
    if spec.get("mode") == "approval":
        raise ValueError(conn["provider"] + " asks before every run; it cannot be watched unattended")
    # The read must pass reckon run's checks now, not first at 3 a.m.
    connectors.check(conn["provider"], spec, args)
    watches = load(h, ctx["org"])
    if name in watches:
        raise ValueError("watch " + name + " exists; remove it first")
    watches[name] = {"connection": cname, "environment": ctx["env"], "args": list(args), "items": items or "", "key": key,
                     "title": title, "created_at": now()}
    write_json(path(h, ctx["org"]), watches)
    return dict(watches[name], watch=name)


def remove(h, org, name):
    watches = load(h, org)
    if name not in watches:
        raise ValueError("unknown watch: " + name)
    del watches[name]
    write_json(path(h, org), watches)
    shutil.rmtree(str(local_path(orgs.org_path(h, org), "runtime", "watches", name)), ignore_errors=True)
    return {"org": org, "removed": name}


def clean(data, values):
    """Redact each string after parsing, so redaction cannot rewrite the JSON itself."""
    if isinstance(data, dict):
        return {k: clean(v, values) for k, v in data.items()}
    if isinstance(data, list):
        return [clean(v, values) for v in data]
    return orgs.redact(data, values) if isinstance(data, str) else data


def fingerprint(watch):
    return hashlib.sha256(json.dumps([watch[k] for k in ("connection", "environment", "args", "items", "key")]).encode()).hexdigest()


def poll(h, org, name, watch, registry):
    # A saved watch reads its own connection from any folder: no folder link preferences.
    ctx = dict(orgs.context(h, org, watch["environment"], registry), link=None)
    folder = local_path(orgs.org_path(h, org), "runtime", "watches", name)
    try:
        held = lock(folder)
        held.__enter__()
    except BlockingIOError:
        return ctx, {"watch": name, "status": "skipped", "items": 0, "new": 0, "detail": "another run of this watch is going"}
    try:
        return ctx, check(h, ctx, folder, name, watch, registry)
    finally:
        held.__exit__(None, None, None)


def check(h, ctx, folder, name, watch, registry):
    # The saved connection itself, or nothing: a removed one must not turn into a tool name that resolves elsewhere.
    if watch["connection"] not in ctx["data"]["connections"]:
        raise ValueError(name + ": connection " + watch["connection"] + " no longer exists; remove the watch and add it again")
    outcome = connectors.run(h, ctx, watch["connection"], watch["args"], registry, timeout=60)
    result = outcome["result"]
    if result["status"] != "ok":
        raise ValueError(name + ": the read " + result["status"] + " (exit " + str(result["exit_code"]) + ")")
    try:
        data = clean(json.loads(result["stdout"].decode("utf-8", errors="replace")), outcome["values"])
    except ValueError:
        raise ValueError(name + ": the read did not return JSON; add the tool's JSON flag to the watch")
    found = pick(data, watch["items"])
    if not isinstance(found, list):
        raise ValueError(name + ": no list at " + (watch["items"] or "the top level") + " in the output")
    keyed = [(pick(item, watch["key"]), item) for item in found]
    if found and all(ident is None for ident, _ in keyed):
        raise ValueError(name + ": no item has a " + watch["key"] + " field; set --key")
    state_path = folder / "state.json"
    state = read_json(state_path) if state_path.is_file() else None
    # A changed watch (other connection, environment or read) starts again from a baseline.
    if state and state.get("fingerprint") != fingerprint(watch):
        state = None
    order = list(state["seen"]) if state else []
    seen = set(order)
    fresh = []
    for ident, item in keyed:
        if ident is None or str(ident) in seen:
            continue
        seen.add(str(ident))
        order.append(str(ident))
        title = pick(item, watch["title"]) if watch.get("title") else None
        text = str(title) if title is not None else str(ident)
        fresh.append({"id": actions.printable(str(ident))[:200], "title": actions.printable(text.replace("\n", " "))[:200]})
    if state is not None and fresh:
        # Deliver first, then remember: a failed write leaves the items to be found again.
        inbox = local_path(orgs.workspace(h, ctx["org"]), "inbox.jsonl")
        fd = os.open(str(inbox), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            for item in fresh:
                out.write(json.dumps({"at": now(), "watch": name, "environment": ctx["env"], "connection": watch["connection"],
                                      "id": item["id"], "title": item["title"]}, ensure_ascii=False) + "\n")
    # Oldest first, so the cap forgets the oldest ids.
    write_json(state_path, {"fingerprint": fingerprint(watch), "seen": order[-SEEN_LIMIT:], "checked_at": now()})
    if state is None:
        return {"watch": name, "status": "baseline", "items": len(found), "new": 0}
    return {"watch": name, "status": "ok", "items": len(found), "new": len(fresh)}


def run(h, org, registry, names=()):
    watches = load(h, org)
    for unknown in set(names) - set(watches):
        raise ValueError("unknown watch: " + unknown)
    rows = []
    for name, watch in sorted(watches.items()):
        if names and name not in names:
            continue
        try:
            ctx, row = poll(h, org, name, watch, registry)
            activity.record(h, ctx, "watch", target=name, status=row["status"], new=row["new"])
        except (ValueError, OSError) as exc:
            row = {"watch": name, "status": "error", "detail": str(exc)}
            try:
                activity.record(h, orgs.context(h, org, watch["environment"], registry), "watch", target=name, status="error", detail=exc)
            except ValueError:
                pass
        rows.append(row)
    return rows


def inbox(h, org, days=7, env=None):
    from datetime import datetime, timedelta, timezone
    target = local_path(orgs.workspace(h, org), "inbox.jsonl")
    if not target.is_file():
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
            if datetime.fromisoformat(item["at"].replace("Z", "+00:00")) < since:
                continue
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if env is None or item.get("environment") == env:
            rows.append(item)
    return rows[::-1]
