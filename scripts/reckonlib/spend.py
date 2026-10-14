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
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


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
    # One line per content block, each with the message's usage so far: keep the largest per message.
    messages = {}
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
                    model = message.get("model", "unknown")
                    if not when or when < since or model == "<synthetic>" or not inside(entry.get("cwd"), folders):
                        continue
                    row = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                           "cache_read": usage.get("cache_read_input_tokens") or 0,
                           "cache_write": usage.get("cache_creation_input_tokens") or 0}
                    key = message.get("id") or entry.get("uuid")
                    known = messages.setdefault(key, (model, dict.fromkeys(FIELDS, 0)))[1]
                    for f in FIELDS:
                        known[f] = max(known[f], row[f])
    totals = {}
    for model, row in messages.values():
        if any(row.values()):
            into = totals.setdefault(model, dict.fromkeys(FIELDS, 0))
            for f in FIELDS:
                into[f] += row[f]
    return totals


def codex(root, since):
    """Per-turn usage events inside the window, so resumed sessions count only new turns
    and an event replayed into a forked session counts once."""
    base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    folders, totals, seen = roots(root), {}, set()
    for path in sorted(base.rglob("*.jsonl")) if base.is_dir() else []:
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        model = "codex"
        with path.open(encoding="utf-8", errors="replace") as lines:
            try:
                meta = json.loads(next(lines, "{}")).get("payload", {})
            except ValueError:
                continue
            if not isinstance(meta, dict) or not inside(meta.get("cwd"), folders):
                continue
            for line in lines:
                if '"token_count"' not in line and '"turn_context"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                    payload = entry.get("payload", {})
                except ValueError:
                    continue
                if payload.get("type") != "token_count":
                    model = payload.get("model") or model
                    continue
                info = payload.get("info") or {}
                turn, total = info.get("last_token_usage"), info.get("total_token_usage")
                when = parse(entry.get("timestamp"))
                key = (entry.get("timestamp"), json.dumps(total, sort_keys=True))
                if not turn or not when or when < since or key in seen:
                    continue
                seen.add(key)
                row = totals.setdefault(model, dict.fromkeys(FIELDS, 0))
                cached = turn.get("cached_input_tokens") or 0
                row["input"] += (turn.get("input_tokens") or 0) - cached
                row["output"] += turn.get("output_tokens") or 0
                row["cache_read"] += cached
                row["cache_write"] += turn.get("cache_write_input_tokens") or 0
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
    table, rates, models = prices(h), [], []
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
