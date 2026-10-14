"""Agent usage per org, read from Claude Code and Codex session files.

Each org has its own workspace folder, and both agents record the folder a
session ran in, so usage is attributed without any tracking of our own. Only
usage counters are read, never message content. Prices change, so estimates come
only from an operator-maintained $RECKON_HOME/prices.json (USD per million tokens):
{"claude-opus-5-5": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}}."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

from . import orgs
from .store import local_path, read_json

FIELDS = ("input", "output", "cache_read", "cache_write")


def parse(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def roots(root):
    """The workspace as both agents may have recorded it (macOS adds /private to /var)."""
    path = str(root)
    return {path, os.path.realpath(path)}


def inside(cwd, folders):
    return isinstance(cwd, str) and any(cwd == f or cwd.startswith(f + os.sep) for f in folders)


def claude(root, since):
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    folders = roots(root)
    # Claude Code names a project folder after its path with every other character replaced by "-".
    prefixes = {re.sub(r"[^A-Za-z0-9]", "-", f) for f in folders}
    totals, seen = {}, set()
    for project in sorted(base.iterdir()) if base.is_dir() else []:
        if not any(project.name.startswith(p) for p in prefixes):
            continue
        for path in project.rglob("*.jsonl"):
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
                continue
            with path.open(encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                        message, usage = entry["message"], entry["message"]["usage"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    when = parse(entry.get("timestamp"))
                    key = message.get("id") or entry.get("uuid")
                    if not when or when < since or key in seen or not inside(entry.get("cwd"), folders):
                        continue
                    seen.add(key)
                    row = totals.setdefault(message.get("model", "unknown"), dict.fromkeys(FIELDS, 0))
                    row["input"] += usage.get("input_tokens") or 0
                    row["output"] += usage.get("output_tokens") or 0
                    row["cache_read"] += usage.get("cache_read_input_tokens") or 0
                    row["cache_write"] += usage.get("cache_creation_input_tokens") or 0
    return totals


def codex(root, since):
    base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    folders, totals = roots(root), {}
    for path in sorted(base.rglob("*.jsonl")) if base.is_dir() else []:
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        meta, last, model = None, None, "codex"
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if meta is None:
                    try:
                        meta = json.loads(line).get("payload", {})
                    except ValueError:
                        break
                    if not inside(meta.get("cwd"), folders) or (parse(meta.get("timestamp")) or since) < since:
                        break
                    continue
                if '"token_count"' in line or '"turn_context"' in line:
                    try:
                        payload = json.loads(line).get("payload", {})
                    except ValueError:
                        continue
                    if payload.get("type") == "token_count" and (payload.get("info") or {}).get("total_token_usage"):
                        last = payload["info"]["total_token_usage"]
                    elif payload.get("model"):
                        model = payload["model"]
        if last:
            row = totals.setdefault(model, dict.fromkeys(FIELDS, 0))
            cached = last.get("cached_input_tokens") or 0
            row["input"] += (last.get("input_tokens") or 0) - cached
            row["output"] += last.get("output_tokens") or 0
            row["cache_read"] += cached
            row["cache_write"] += last.get("cache_write_input_tokens") or 0
    return totals


def prices(h):
    path = local_path(h, "prices.json")
    if not path.is_file():
        return {}
    data = read_json(path)
    if not isinstance(data, dict) or not all(isinstance(v, dict) and all(isinstance(v.get(f, 0), (int, float)) for f in FIELDS)
                                             for v in data.values()):
        raise ValueError(str(path) + " must map each model to USD per million tokens for " + ", ".join(FIELDS))
    return data


def report(h, org, days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    root = orgs.workspace(h, org)
    table, rates = prices(h), []
    models = []
    for runtime, totals in (("claude", claude(root, since)), ("codex", codex(root, since))):
        for model, row in sorted(totals.items()):
            rate = table.get(model)
            cost = round(sum(row[f] * rate.get(f, 0) for f in FIELDS) / 1e6, 2) if rate else None
            models.append(dict(row, runtime=runtime, model=model, usd=cost))
            if rate is None:
                rates.append(model)
    tokens = sum(sum(m[f] for f in FIELDS) for m in models)
    priced = [m["usd"] for m in models if m["usd"] is not None]
    return {"org": org, "days": days, "tokens": tokens, "models": models,
            "usd": round(sum(priced), 2) if priced and not rates else None,
            "unpriced_models": sorted(set(rates))}
