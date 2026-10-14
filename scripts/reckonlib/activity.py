"""An append-only log of what ran for each org: reads, refusals and write steps.

Kept whether or not a session was named, so wrong-context refusals and reads
outside a session leave a trace. Arguments are stored as a hash plus the leading
command words; values never are. Logging never makes a run fail."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

from . import orgs
from .store import local_path, now


def path(h, org):
    return local_path(orgs.org_path(h, org), "activity.jsonl")


def actor():
    return "agent" if os.environ.get("RECKON_ORG") else "operator"


def record(h, ctx, event, **fields):
    entry = {"at": now(), "org": ctx["org"], "environment": ctx["env"], "actor": actor(), "event": event}
    entry.update({k: v for k, v in fields.items() if v is not None})
    if "args" in entry:
        args = entry.pop("args")
        entry["args_sha256"] = hashlib.sha256(json.dumps(args).encode()).hexdigest()
    if "detail" in entry:
        entry["detail"] = str(entry["detail"])[:300]
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
            at = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            continue
        if at < since or env and entry.get("environment") != env or event and entry.get("event") != event:
            continue
        rows.append(entry)
    return rows[-limit:][::-1]
