"""An append-only log of what ran for each org: reads, refusals and write steps.

Kept whether or not a session was named, so reads outside a session and refusals
by reckon run (another environment's connection, denied commands, spent budgets)
leave a trace. Arguments are stored as a hash plus the command words the registry
knows; values never are. Logging never makes a run fail."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

from . import connectors, orgs
from .store import local_path, now


def path(h, org):
    return local_path(orgs.org_path(h, org), "activity.jsonl")


def pinned():
    """Set by reckon open; an empty value still counts, so clearing it does not unpin."""
    return "RECKON_ORG" in os.environ or "RECKON_ENV" in os.environ


def actor():
    return "agent" if pinned() else "operator"


def words(ctx, target, args, registry):
    """The command words worth keeping. Leading words of database clients are values
    (connection URLs, passwords), so only words matching a registry pattern are kept,
    and for our own CLIs the first two."""
    try:
        spec = registry[orgs.resolve(ctx, target)[1]["provider"]]
    except (ValueError, KeyError):
        return []
    found = connectors.tokens(args, spec)
    if spec.get("mode") == "own":
        return [w[:32] for w in found[:2]]
    patterns = [p for p in spec.get("read_commands", []) + spec.get("deny_commands", []) if connectors.matches(p, found)]
    return [w[:32] for w in found[:max(map(len, patterns))]] if patterns else []


def record(h, ctx, event, **fields):
    entry = {"at": now(), "org": ctx["org"], "environment": ctx["env"], "actor": actor(), "event": event}
    entry.update({k: v for k, v in fields.items() if v is not None})
    if "args" in entry:
        args = entry.pop("args")
        entry["args_sha256"] = hashlib.sha256(json.dumps(args).encode()).hexdigest()
    if "detail" in entry:
        entry["detail"] = orgs.redact(str(entry["detail"]), {})[:300]
    try:
        target = path(h, ctx["org"])
        orgs.private_dir(target.parent)
        fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def read(h, org, days=7, env=None, event=None, limit=50):
    target = path(h, org)
    if not target.is_file():
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
            if datetime.fromisoformat(entry["at"].replace("Z", "+00:00")) < since:
                continue
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if env and entry.get("environment") != env or event and entry.get("event") != event:
            continue
        rows.append(entry)
    return rows[-limit:][::-1]
