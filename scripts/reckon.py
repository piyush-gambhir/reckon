#!/usr/bin/env python3
"""Reckon: one workspace for several businesses.

Orgs hold environments, tool connections, knowledge and saved work. The agent
works in one org and environment at a time and reaches every tool through
reckon run, which adds that connection's credentials and keeps reads read-only.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reckonlib import activity, connectors, orgs, workspace  # noqa: E402
from reckonlib import investigation as work  # noqa: E402
from reckonlib.operations import OPERATIONS, build, plan  # noqa: E402
from reckonlib.store import create, local_path, lock, now, read_json, resolve_service, services, session_path, window, write_json  # noqa: E402

INVESTIGATIONS = ("debug", "investigate", "monitor", "analyze")
EXIT = {"timeout": 124, "output_limit": 3, "unavailable": 127, "cancelled": 130}


def parser():
    p = argparse.ArgumentParser(prog="reckon", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    scope = argparse.ArgumentParser(add_help=False)
    scope.add_argument("--org", help="org (defaults to the agent's pinned org, then reckon use)")
    scope.add_argument("--env", help="environment within the org")
    scope.add_argument("--json", action="store_true", help="structured output")

    command = sub.add_parser("org", help="add, list or show orgs")
    actions = command.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="create an org")
    add.add_argument("org")
    add.add_argument("--name", help="display name")
    add.add_argument("--envs", help="comma-separated environments (default prod,uat,dev, or the --like org's)")
    add.add_argument("--production", help="comma-separated production environments (default: prod or production)")
    add.add_argument("--like", metavar="ORG", help="copy another org's environments and connection layout, without secrets or resource limits")
    add.add_argument("--json", action="store_true")
    for action in ("list", "show"):
        item = actions.add_parser(action)
        if action == "show":
            item.add_argument("org", nargs="?")
        item.add_argument("--json", action="store_true")

    command = sub.add_parser("use", help="set the default org and environment for the next launch")
    command.add_argument("target", help="<org>/<env>, or <env> to keep the current org")
    command.add_argument("--json", action="store_true")

    command = sub.add_parser("connect", parents=[scope], help="add a tool connection, or update its environment labels")
    command.add_argument("name", nargs="?", help="connection name, e.g. jenkins-prod")
    command.add_argument("--provider", help="tool (reckon connect --list shows them)")
    command.add_argument("--for-env", dest="bound", help="tie the connection to one environment; omit to share it across all")
    command.add_argument("--label", action="append", default=[], metavar="ENV=LABEL",
                         help="label that selects ENV inside a shared backend, e.g. prod=production (repeatable)")
    command.add_argument("--list", action="store_true", help="list supported tools")
    command.add_argument("--allow", action="append", default=[], metavar="KIND=PATTERN",
                         help="limit the connection to resources, e.g. repo=acme/* or site=sc-domain:acme.com (repeatable)")
    command.add_argument("--allow-any", action="store_true", help="remove the connection's resource limits")
    command.add_argument("--account", help="the login, email or account id this connection must use, checked by reckon verify "
                                           "and before every write (a * pattern is allowed; '' removes it)")
    command.add_argument("--remove", action="store_true", help="remove the connection; its secrets file is kept for you to delete")

    command = sub.add_parser("login", parents=[scope], help="interactive login for a connection's CLI (run it yourself)")
    command.add_argument("connection")

    command = sub.add_parser("migrate", help="copy this checkout's pre-org layout into a new org")
    command.add_argument("--org", required=True)
    command.add_argument("--name")
    command.add_argument("--from", dest="source", type=Path, default=orgs.code_root(), help="old checkout (default: this one)")
    command.add_argument("--rename", action="append", default=[], metavar="OLD=NEW", help="rename an environment, e.g. production=prod")
    command.add_argument("--share", action="append", default=[], metavar="TOOL",
                         help="make one connection for every environment, e.g. cubeapm when one server holds them all")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--json", action="store_true")

    command = sub.add_parser("open", help="start Claude Code or Codex in an org's folder, pinned to one environment")
    command.add_argument("target", nargs="?", help="<org>/<env> (default: reckon use)")
    command.add_argument("--runtime", choices=("claude", "codex"))

    for name, text in (("status", "context and connections"), ("preflight", "compact digest an agent reads first"),
                       ("doctor", "diagnose setup problems")):
        sub.add_parser(name, parents=[scope], help=text)
    command = sub.add_parser("verify", parents=[scope], help="one live read per connection (contacts your tools)")
    command.add_argument("connections", nargs="*")

    command = sub.add_parser("run", parents=[scope], help="run a connection's CLI read-only: reckon run <connection|tool> -- <args>")
    command.add_argument("target")
    command.add_argument("--session", help="save the output as evidence in this session")
    command.add_argument("--label", help="evidence label (with --session)")
    command.add_argument("--timeout", type=float, default=60, help="seconds, at most 120")
    command.add_argument("--max-bytes", type=int, default=8 * 1048576)
    command.add_argument("--stdin", action="store_true", help="pass this command's standard input to the CLI")
    command.add_argument("--cache", metavar="AGE", help="reuse an identical successful read younger than AGE (e.g. 15m, 2h, 1d)")

    for name in INVESTIGATIONS:
        command = sub.add_parser(name, parents=[scope], help="start an investigation session for a mapped service")
        command.add_argument("--service", required=True)
        command.add_argument("--question", required=True)
        command.add_argument("--from", dest="start", required=True, help="ISO time with timezone")
        command.add_argument("--to", dest="end", required=True, help="ISO time with timezone")
        command.add_argument("--playbook", choices=("latency", "errors", "workload", "changes"), default="latency")
        command.add_argument("--collect", action="store_true", help="perform the mapped live reads now")
    command = sub.add_parser("task", parents=[scope], help="start a session for any piece of work")
    command.add_argument("--question", help="what this work is about (a workflow supplies one)")
    command.add_argument("--workflow", help="start from a workflow recipe (reckon workflows lists them)")
    command.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="a workflow parameter; repeat for each")
    command.add_argument("--area", help="dev, product, gtm, sales, ops or your own word")
    command.add_argument("--budget", type=int, help="most reads this session may record (protects quotas and production)")
    command.add_argument("--from", dest="start", help="optional window start (ISO time with timezone)")
    command.add_argument("--to", dest="end", help="optional window end")

    sub.add_parser("workflows", parents=[scope], help="workflow recipes: parameters and the checks that make one done")
    command = sub.add_parser("watch", parents=[scope], help="poll saved reads for new items into the inbox (operator only)")
    command.add_argument("action", choices=("add", "list", "remove", "run"))
    command.add_argument("name", nargs="*")
    command.add_argument("--connection", help="connection or tool to read")
    command.add_argument("--items", default="", help="dotted path to the list of items in the JSON output (default: the top level)")
    command.add_argument("--key", default="id", help="field that identifies an item (default id)")
    command.add_argument("--title", help="field to show for an item")
    command.add_argument("--production", action="store_true", help="confirm a watch in a production environment")
    command = sub.add_parser("inbox", parents=[scope], help="new items found by watches")
    command.add_argument("--days", type=int, default=7)
    command.add_argument("--all-envs", action="store_true")
    command = sub.add_parser("spend", parents=[scope], help="agent tokens used in this org's workspace (Claude Code and Codex)")
    command.add_argument("--days", type=int, default=30)
    command.add_argument("--all-orgs", action="store_true", help="every org (operator only)")
    command.add_argument("--set-budget", type=int, metavar="TOKENS", help="warn when the org passes this many tokens (0 clears it)")
    command.add_argument("--budget-days", type=int, default=30, help="the budget's window in days (default 30)")
    command = sub.add_parser("routine", parents=[scope], help="run a workflow on a schedule with a headless agent (operator only)")
    command.add_argument("action", choices=("add", "list", "remove", "run", "install"))
    command.add_argument("name", nargs="?")
    command.add_argument("--workflow")
    command.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    command.add_argument("--at", help="daily HH:MM, weekdays HH:MM or mon HH:MM (local time)")
    command.add_argument("--production", action="store_true", help="confirm a routine in a production environment")
    command.add_argument("--timeout-minutes", type=int, default=45)
    command = sub.add_parser("services", parents=[scope], help="inspect or validate service mappings")
    command.add_argument("action", choices=("list", "show", "validate"))
    command.add_argument("name", nargs="?")
    sub.add_parser("capabilities", parents=[scope], help="typed operations and their readiness")
    command = sub.add_parser("collect", parents=[scope], help="collect a session's mapped evidence or one typed operation")
    command.add_argument("session")
    command.add_argument("--operation", choices=tuple(OPERATIONS))
    command.add_argument("--params", default="{}", help="operation parameters as a JSON object")
    command.add_argument("--refresh", action="store_true")
    command.add_argument("--timeout", type=float, default=15)
    command.add_argument("--max-bytes", type=int, default=1048576)
    command = sub.add_parser("check", parents=[scope], help="quality checks on a saved session (citations, integrity, completeness)")
    command.add_argument("session")
    for name in ("resume", "report"):
        command = sub.add_parser(name, parents=[scope])
        command.add_argument("session")
    command = sub.add_parser("note", parents=[scope], help="record findings, actions, hypotheses, rejected ideas and questions")
    command.add_argument("session")
    command.add_argument("--kind", choices=("finding", "action", "hypothesis", "rejected", "question"))
    command.add_argument("--text", required=True, help="the note, or with --withdraw why it no longer stands")
    command.add_argument("--withdraw", metavar="NOTE_ID", help="withdraw a note: it stays on record but no longer counts")
    command.add_argument("--evidence", nargs="*", default=[])
    command.add_argument("--next-check", default="")
    command.add_argument("--follows", action="append", default=[], metavar="NOTE_ID",
                         help="a finding that says what happened to an action from the last review (repeatable)")
    command = sub.add_parser("history", parents=[scope], help="search this org's sessions and RCAs")
    command.add_argument("--service")
    command.add_argument("--query")
    command.add_argument("--all-envs", action="store_true")
    command.add_argument("--limit", type=int, default=20)
    command = sub.add_parser("promote", parents=[scope], help="snapshot a session into a preliminary incident")
    command.add_argument("session")
    command.add_argument("--slug", required=True)
    command = sub.add_parser("close", parents=[scope], help="record a session's outcome")
    command.add_argument("session")
    command.add_argument("--outcome", choices=("diagnosed", "done", "inconclusive"), required=True)
    command = sub.add_parser("act", parents=[scope], help="prepare a write for the operator, or apply one (operator only)")
    command.add_argument("action", choices=("prepare", "apply", "list", "show", "reconcile"))
    command.add_argument("target", nargs="?", help="prepare: the write operation (github.issue.create, jira.issue.create); apply/show: the action ID")
    command.add_argument("--params", default="{}", help="prepare: parameters as a JSON object")
    command.add_argument("--body-stdin", action="store_true", help="prepare: read the long text (GitHub body, Jira description) from standard input")
    command.add_argument("--session", help="prepare: the session this write comes from, so its next review sees the receipt")
    command = sub.add_parser("brief", parents=[scope], help="local digest: open work, findings, writes waiting, gaps (no network)")
    command.add_argument("--days", type=int, default=7)
    command.add_argument("--save", action="store_true", help="also write it to the org folder's briefs/<date>-<env>.md")
    command.add_argument("--all-envs", action="store_true", help="every environment of the org")
    command.add_argument("--all-orgs", action="store_true", help="every org and environment, one digest (operator only)")
    command = sub.add_parser("log", parents=[scope], help="this org's activity: runs, refusals and write steps")
    command.add_argument("--days", type=int, default=7)
    command.add_argument("--event", choices=("run", "refused", "verify", "act.prepare", "act.apply", "act.reconcile", "routine", "watch"))
    command.add_argument("--all-envs", action="store_true")
    command.add_argument("--limit", type=int, default=50)
    command = sub.add_parser("demo", help="synthetic investigation in a throwaway org; no network")
    command.add_argument("--json", action="store_true")
    return p


def split(target):
    org, _, env = target.partition("/")
    return org, env or None


def labels(values, envs):
    result = {}
    for item in values:
        env, sep, label = item.partition("=")
        if not sep or env not in envs or not orgs.LABEL.fullmatch(label):
            raise ValueError("--label takes ENV=LABEL with one of: " + ", ".join(envs))
        result[env] = label
    return result


def connect(h, args, registry):
    if args.list:
        return [{"provider": k, "title": v.get("title", k), "binary": v["binary"], "mode": v.get("mode"),
                 "settings": v.get("required", [])} for k, v in sorted(registry.items())]
    if not args.name:
        raise ValueError("usage: reckon connect <name> --provider <tool> [--for-env <env> | --label <env>=<label>]")
    ctx = context(h, args, registry, need_env=False)
    data = ctx["data"]
    orgs.name(args.name, "connection")
    if args.remove:
        if args.name not in data["connections"]:
            raise ValueError("unknown connection: " + args.name)
        del data["connections"][args.name]
        orgs.save(h, data, registry)
        # A connection added later under this name starts unchecked.
        checks = checks_path(h, ctx["org"], args.name)
        if checks.is_file():
            checks.unlink()
        workspace.ensure(h, ctx, registry)
        return {"org": ctx["org"], "removed": args.name, "secrets_kept": str(orgs.secrets_path(h, ctx["org"], args.name))}
    conn = dict(data["connections"].get(args.name, {}))
    if conn:
        if args.provider and args.provider != conn["provider"]:
            raise ValueError(args.name + " already uses " + conn["provider"] + "; pick another name")
    else:
        if args.provider not in registry:
            raise ValueError("choose --provider from: " + ", ".join(sorted(registry)))
        conn["provider"] = args.provider
    if args.bound:
        if args.label:
            raise ValueError("a connection tied to one environment has no labels; --label is for shared backends")
        conn.pop("env_labels", None)
        conn["env"] = args.bound
    if args.label:
        conn.pop("env", None)
        conn["env_labels"] = dict(conn.get("env_labels", {}), **labels(args.label, data["environments"]))
    if args.allow_any:
        conn.pop("resources", None)
    for item in args.allow:
        kind, sep, pattern = item.partition("=")
        if not sep or not pattern:
            raise ValueError("--allow takes KIND=PATTERN")
        conn.setdefault("resources", {}).setdefault(kind, [])
        if pattern not in conn["resources"][kind]:
            conn["resources"][kind].append(pattern)
    if args.account is not None:
        if not args.account:
            conn.pop("account", None)
        elif not registry[conn["provider"]].get("identity"):
            raise ValueError(conn["provider"] + " does not report which account it uses, so --account cannot be checked")
        else:
            conn["account"] = args.account
    data["connections"][args.name] = conn
    orgs.save(h, data, registry)
    workspace.ensure(h, ctx, registry)
    path, created = connectors.template(h, ctx["org"], args.name, registry[conn["provider"]])
    spec = registry[conn["provider"]]
    try:
        ready = orgs.configured(h, ctx["org"], args.name, spec)
    except ValueError:
        ready = False
    return {"org": ctx["org"], "connection": args.name, "provider": conn["provider"], "environment": conn.get("env") or "all",
            "env_labels": conn.get("env_labels", {}), "resources": conn.get("resources", {}), "account": conn.get("account"),
            "secrets": str(path), "secrets_created": created,
            "next": "reckon verify " + args.name if ready else
                    "edit " + str(path) + (" or run: reckon login " + args.name if spec.get("login") else "") +
                    ", then: reckon verify " + args.name}


def context(h, args, registry, need_env=True):
    org, env = getattr(args, "org", None), getattr(args, "env", None)
    if not need_env and org and not env:
        data = orgs.load(h, org, registry)
        return {"org": org, "env": None, "production": False, "source": "arguments", "data": data}
    return orgs.context(h, org, env, registry)


def knowledge_count(root, layer):
    path = root / "knowledge" / layer
    return len(list(path.glob("*.md"))) + len(list(path.glob("*.json"))) if path.is_dir() else 0


def shape(conn):
    """A connection's configuration as a hash: a live check counts only for the setup it checked."""
    import hashlib
    return hashlib.sha256(json.dumps(conn, sort_keys=True).encode()).hexdigest()


def checks_path(h, org, cname):
    return local_path(orgs.org_path(h, org), "runtime", cname) / "verify.json"


def remember(h, ctx, rows):
    """Log each live check, and keep the newest per connection and environment for status and preflight."""
    for row in rows:
        if row["status"] == "skipped":
            continue
        entry = activity.record(h, ctx, "verify", target=row["connection"], status=row["status"], account=row.get("account"),
                                detail=row.get("detail") or None)
        path = checks_path(h, ctx["org"], row["connection"])
        try:
            with lock(path.parent):
                saved = read_json(path) if path.is_file() else {}
                saved = saved if isinstance(saved, dict) else {}
                saved[ctx["env"]] = {"at": entry["at"], "status": row["status"], "detail": entry.get("detail"),
                                     "connection_sha256": shape(ctx["data"]["connections"][row["connection"]])}
                write_json(path, saved)
        except (OSError, ValueError):
            pass


def described(h, ctx, registry):
    """orgs.describe plus each connection's last live check here, while its configuration is
    unchanged: configured locally ("ready") is not the same as working."""
    rows = orgs.describe(h, ctx, registry)
    for row in rows:
        if not row["applies"]:
            continue
        try:
            path = checks_path(h, ctx["org"], row["connection"])
            found = (read_json(path) if path.is_file() else {}).get(ctx["env"])
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(found, dict) and found.get("connection_sha256") == shape(ctx["data"]["connections"][row["connection"]]):
            row["verified"] = {k: found.get(k) for k in ("at", "status", "detail")}
    return rows


def checked(row):
    found = row.get("verified")
    if not found:
        return ""
    return ("  last verify: " + str(found["status"]) + " " + str(found["at"])[:16].replace("T", " ") + "Z" +
            (": " + str(found["detail"])[:80] if found.get("detail") and found["status"] != "ok" else ""))


def preflight(h, ctx, registry):
    root = orgs.workspace(h, ctx["org"])
    rows = described(h, ctx, registry)
    sessions = work.history(root, ctx["env"], limit=200)
    open_sessions = [s for s in sessions if s.get("status") == "open"]
    incidents = sorted(p.name for p in (root / "incidents").iterdir() if p.is_dir() and not p.name.startswith(".")) if (root / "incidents").is_dir() else []
    warnings = []
    if ctx["production"]:
        warnings.append("PRODUCTION: prefer the cheapest signal and bound every query")
    if not any(r["applies"] for r in rows):
        warnings.append("no connections for " + ctx["env"] + "; ask the operator to run reckon connect")
    for r in rows:
        if r["applies"] and r.get("verified", {}).get("status") not in (None, "ok"):
            warnings.append(r["connection"] + " failed its last live check (" + str(r["verified"]["status"]) +
                            "); ask the operator to fix it and run reckon verify " + r["connection"])
    if not knowledge_count(root, ctx["env"]) and not knowledge_count(root, "_shared"):
        warnings.append("no knowledge for this org yet; do not invent facts")
    return {"org": ctx["org"], "name": ctx["data"]["name"], "environment": ctx["env"], "production": ctx["production"],
            "resolved_from": ctx["source"], "workspace": str(root),
            "connections": [{k: r.get(k) for k in ("connection", "provider", "state", "label", "verified")} for r in rows if r["applies"]],
            "other_environments": [r["connection"] + " (" + r["scope"] + ")" for r in rows if not r["applies"]],
            "knowledge": {"_shared": knowledge_count(root, "_shared"), ctx["env"]: knowledge_count(root, ctx["env"])},
            "open_sessions": [{"id": s["id"], "question": s["question"]} for s in open_sessions[:5]],
            "incidents": {"count": len(incidents), "latest": incidents[-1] if incidents else None},
            "warnings": warnings, "note": "ready means configured locally; reckon verify proves live access"}


def version_tuple(text):
    """The x.y.z a CLI reports: the number after "version" (gsc also prints the latest
    release it knows of), else the first one in its output."""
    import re
    match = re.search(r"version[\s:=]+v?(\d+)\.(\d+)\.(\d+)", text, re.I) or re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in match.groups()) if match else None


def outdated():
    """Installed sibling CLIs older than the release this checkout pins."""
    import csv
    from reckonlib.process import run as bounded
    notes = []
    with open(orgs.code_root() / "scripts" / "cli-releases.csv") as stream:
        for row in csv.DictReader(stream):
            if not shutil.which(row["binary"]):
                continue
            env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "NO_UPDATE_NOTIFIER": "1", "CI": "1"}
            result = bounded([row["binary"], "version"], env=env, timeout=5, max_bytes=65536)
            have = version_tuple((result["stdout"] + result["stderr"]).decode(errors="replace"))
            want = version_tuple(row["release"])
            if have and want and have < want:
                notes.append(row["binary"] + " " + ".".join(map(str, have)) + " is older than the pinned " + row["release"] +
                             " (rerun scripts/setup.sh or setup.ps1 to upgrade it)")
    return notes


def doctor(h, registry):
    problems, checks = [], []
    checks.append("Python " + ".".join(map(str, sys.version_info[:3])))
    if h.exists() and os.name != "nt" and h.stat().st_mode & 0o077:
        problems.append(str(h) + " is readable by other users; run: chmod 700 " + str(h))
    found = orgs.orgs(h)
    checks.append("orgs: " + (", ".join(found) or "none (reckon org add <org> or reckon migrate --org <org>)"))
    for org in found:
        try:
            data = orgs.load(h, org, registry)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        for cname, conn in sorted(data["connections"].items()):
            spec = registry[conn["provider"]]
            try:
                values = orgs.secrets(h, org, cname, spec)
                if not orgs.configured(h, org, cname, spec, values):
                    checks.append(org + "/" + cname + ": no credentials yet")
            except ValueError as exc:
                problems.append(org + "/" + cname + ": " + str(exc))
            if not shutil.which(spec["binary"]):
                checks.append(org + "/" + cname + ": " + spec["binary"] + " is not installed")
    checks += outdated()
    link = h / "bin" / "reckon"
    if not link.is_symlink():
        checks.append("reckon command: created on the first reckon open")
    elif str(link.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        checks.append("reckon command: " + str(link) + " is not on PATH; add to your shell rc: export PATH=\"" + str(link.parent) + ":$PATH\"")
    else:
        checks.append("reckon command: " + str(link))
    try:
        ctx = orgs.context(h, registry=registry)
        checks.append("default context: " + ctx["org"] + "/" + ctx["env"] + " (from " + ctx["source"] + ")")
    except ValueError as exc:
        checks.append("default context: " + str(exc))
    return {"home": str(h), "checks": checks, "problems": problems}


def execute(args):
    h = orgs.home()
    registry = orgs.providers()
    name = args.command
    if activity.pinned() and (name in ("use", "connect", "login", "migrate", "open", "routine", "watch") or name == "org" and args.action == "add"
                                         or name == "act" and args.action == "apply"):
        # An agent started by reckon open works only inside its pinned org and environment.
        raise ValueError("reckon " + name + " is for the operator's own terminal, not a pinned agent")
    if name == "org":
        if args.action == "add":
            source = orgs.load(h, args.like, registry) if args.like else None
            envs = [e.strip() for e in args.envs.split(",") if e.strip()] if args.envs else source["environments"] if source else ["prod", "uat", "dev"]
            production = [e.strip() for e in args.production.split(",")] if args.production else source["production"] if source and not args.envs else None
            data = orgs.create(h, args.org, args.name, envs, production, registry)
            templates = []
            if source:
                # Same tools, new accounts: the layout carries over, credentials and resource limits never do.
                for cname, conn in source["connections"].items():
                    copied = {k: conn[k] for k in ("provider", "env", "env_labels") if k in conn}
                    if copied.get("env", envs[0]) in envs and all(e in envs for e in copied.get("env_labels", {})):
                        data["connections"][cname] = copied
                orgs.save(h, data, registry)
                templates = [str(connectors.template(h, args.org, c, registry[v["provider"]])[0]) for c, v in sorted(data["connections"].items())]
            workspace.ensure(h, {"org": args.org, "data": data}, registry)
            if not (h / "context").exists():
                orgs.set_context(h, args.org, envs[0], registry)
            return {"org": data["id"], "environments": data["environments"], "production": data["production"],
                    "connections": sorted(data["connections"]), "fill_in": templates,
                    "next": "fill in each credentials file, then reckon verify --org " + args.org + " --env " + envs[0] if templates else
                            "reckon connect <name> --org " + args.org + " --provider <tool> [--for-env <env>]"}
        if args.action == "list":
            return [{"org": o, "name": orgs.load(h, o, registry)["name"], "environments": orgs.load(h, o, registry)["environments"]} for o in orgs.orgs(h)]
        org = args.org or orgs.context(h, registry=registry)["org"]
        data = orgs.load(h, org, registry)
        return dict(data, workspace=str(orgs.workspace(h, org)))
    if name == "use":
        org, env = split(args.target)
        if env is None and org not in orgs.orgs(h):
            env, org = org, orgs.context(h, registry=registry)["org"]
        orgs.set_context(h, org, env, registry)
        result = {"org": org, "environment": env, "note": "used by the next reckon open and by shells without a pinned context"}
        if os.environ.get("RECKON_ORG"):
            result["warning"] = "this shell is pinned to " + os.environ["RECKON_ORG"] + "/" + os.environ.get("RECKON_ENV", "") + " and keeps it"
        return result
    if name == "connect":
        return connect(h, args, registry)
    if name == "login":
        ctx = context(h, args, registry)
        cname, code = connectors.login(h, ctx, args.connection, registry)
        return {"connection": cname, "exit_code": code}
    if name == "migrate":
        from reckonlib.migrate import migrate
        rename = dict(item.split("=", 1) for item in args.rename if "=" in item)
        if len(rename) != len(args.rename):
            raise ValueError("--rename takes OLD=NEW")
        summary = migrate(h, args.org, args.source, registry, args.name, args.dry_run, rename, args.share)
        if not args.dry_run:
            workspace.ensure(h, {"org": args.org, "data": orgs.load(h, args.org, registry)}, registry)
            summary["next"] = "reckon use " + args.org + "/<env>, reckon verify, then reckon open"
        return summary
    if name == "open":
        org, env = split(args.target) if args.target else (None, None)
        if org and not env:
            raise ValueError("name the environment too: reckon open " + org + "/<env>")
        ctx = orgs.context(h, org, env, registry) if org else saved_context(h, registry)
        from reckonlib import spend
        try:
            warning = spend.over(h, ctx["org"])
        except (ValueError, OSError) as exc:
            warning = "could not check the agent budget: " + str(exc)
        if warning:
            print("warning: " + warning + " (reckon spend)", file=sys.stderr)
        workspace.launch(h, ctx, registry, args.runtime, args.passthrough)
    if name == "demo":
        from reckonlib.demo import demo
        return demo(h)
    if name == "doctor":
        return doctor(h, registry)
    if name == "act" and args.action == "apply":
        from reckonlib import actions
        if not args.target:
            raise ValueError("usage: reckon act apply <action-id>")
        owner = next((o for o in orgs.orgs(h) if (orgs.workspace(h, o) / "actions" / args.target / "action.json").is_file()), None)
        if not owner:
            raise ValueError("unknown action: " + args.target)
        record = actions.load(orgs.workspace(h, owner), args.target)[1]
        ctx = orgs.context(h, owner, record["environment"], registry)
        done = actions.apply(h, ctx, orgs.workspace(h, owner), args.target, registry)
        activity.record(h, ctx, "act.apply", target=done["connection"], action=done["id"], operation=done["operation"], status=done["status"])
        return done

    if name == "routine" and args.action != "add":
        # The routine names its environment; only the org is needed here.
        from reckonlib import routines
        org = args.org or orgs.context(h, registry=registry)["org"]
        orgs.load(h, org, registry)
        if args.action == "list":
            return [dict(r, routine=n) for n, r in sorted(routines.load(h, org).items())]
        if not args.name:
            raise ValueError("usage: reckon routine " + args.action + " <name>")
        if args.action == "remove":
            return routines.remove(h, org, args.name)
        if args.action == "install":
            return routines.install(h, org, args.name)
        return routines.run(h, org, args.name, registry)
    if name == "watch" and args.action != "add":
        from reckonlib import watches
        if args.passthrough:
            raise ValueError("only reckon watch add takes a read after --")
        org = args.org or orgs.context(h, registry=registry)["org"]
        orgs.load(h, org, registry)
        if args.action == "list":
            return [dict(w, watch=n) for n, w in sorted(watches.load(h, org).items())]
        if args.action == "remove":
            if len(args.name) != 1:
                raise ValueError("usage: reckon watch remove <name>")
            return watches.remove(h, org, args.name[0])
        return watches.run(h, org, registry, tuple(args.name))
    if name == "spend":
        from reckonlib import spend
        if not 1 <= args.days <= 365:
            raise ValueError("--days must be 1-365")
        if args.set_budget is None and "--budget-days" in sys.argv:
            raise ValueError("--budget-days goes with --set-budget")
        if args.set_budget is not None:
            if args.all_orgs:
                raise ValueError("set a budget for one org at a time (--org)")
            if activity.pinned():
                raise ValueError("budgets are for the operator's own terminal, not a pinned agent")
            if args.set_budget < 0 or not 1 <= args.budget_days <= 365:
                raise ValueError("--set-budget takes 0 or more tokens and --budget-days 1-365")
            return spend.set_budget(h, args.org or orgs.context(h, registry=registry)["org"], args.set_budget, args.budget_days)
        if args.all_orgs:
            if activity.pinned():
                raise ValueError("a pinned agent sees only its own org; run reckon spend --all-orgs in your own terminal")
            return [spend.report(h, org, args.days) for org in orgs.orgs(h)]
        # A pinned agent sees only its own org's spend.
        org = orgs.context(h, args.org, os.environ.get("RECKON_ENV"), registry)["org"] if activity.pinned() else \
            args.org or orgs.context(h, registry=registry)["org"]
        orgs.load(h, org, registry)
        return [spend.report(h, org, args.days)]
    if name == "brief" and args.all_orgs:
        from reckonlib.brief import all_orgs, render_all
        if not 1 <= args.days <= 365:
            raise ValueError("--days must be 1-365")
        if args.org or args.env:
            raise ValueError("--all-orgs covers every org; drop --org and --env")
        result = all_orgs(h, registry, args.days)
        if args.save:
            from datetime import datetime, timezone
            from reckonlib.store import write as save
            target = local_path(h, "briefs", datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-all.md")
            save(target, render_all(result) + "\n")
            result["saved"] = str(target)
        return result

    ctx = context(h, args, registry)
    root = orgs.workspace(h, ctx["org"])
    if name == "log":
        if not 1 <= args.days <= 365 or not 1 <= args.limit <= 1000:
            raise ValueError("--days must be 1-365 and --limit 1-1000")
        if args.all_envs and activity.pinned():
            raise ValueError("a pinned agent sees only its own environment's activity")
        return activity.read(h, ctx["org"], args.days, None if args.all_envs else ctx["env"], args.event, args.limit)
    if name == "status":
        return {"org": ctx["org"], "name": ctx["data"]["name"], "environment": ctx["env"], "production": ctx["production"],
                "resolved_from": ctx["source"], "workspace": str(root), "connections": described(h, ctx, registry)}
    if name == "preflight":
        return preflight(h, ctx, registry)
    if name == "verify":
        rows = connectors.verify(h, ctx, registry, tuple(args.connections))
        remember(h, ctx, rows)
        return rows
    if name == "run":
        command = args.passthrough
        if not command:
            raise ValueError("usage: reckon run <connection|tool> -- <arguments>")
        if args.label and not args.session:
            raise ValueError("--label needs --session")
        words = activity.words(ctx, args.target, command, registry)
        if args.session:
            spath, sdata = session_path(root, args.session)
            try:
                work.matching(sdata, ctx)
                work.spend(spath, sdata, 1)
            except ValueError as exc:
                activity.record(h, ctx, "refused", target=args.target, command=words, args=command, session=args.session, detail=exc)
                raise
        stdin = sys.stdin.buffer.read(1048577) if args.stdin else None
        if stdin is not None and len(stdin) > 1048576:
            raise ValueError("standard input over 1 MiB; pass a file argument instead")
        cache_seconds = 0
        if args.cache:
            unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(args.cache[-1:])
            if not unit or not args.cache[:-1].isdigit():
                raise ValueError("--cache takes a number and s, m, h or d, e.g. 15m")
            cache_seconds = int(args.cache[:-1]) * unit
        try:
            outcome = connectors.run(h, ctx, args.target, command, registry, args.timeout, args.max_bytes, stdin, cache_seconds)
        except ValueError as exc:
            activity.record(h, ctx, "refused", target=args.target, command=words, args=command, session=args.session, detail=exc)
            raise
        if outcome.get("quota_note"):
            print("reckon: " + outcome["quota_note"], file=sys.stderr)
        if outcome.get("cached_at"):
            print("reckon: cached result observed at " + outcome["cached_at"] + "; drop --cache for a fresh read", file=sys.stderr)
        result = outcome["result"]
        sys.stdout.write(orgs.redact(result["stdout"].decode("utf-8", errors="replace"), outcome["values"]))
        sys.stderr.write(orgs.redact(result["stderr"].decode("utf-8", errors="replace"), outcome["values"]))
        if args.session:
            record = work.record_run(root, args.session, ctx, outcome, args.label, args.timeout, args.max_bytes)
            print("reckon: saved as evidence " + record["id"] + " in " + args.session, file=sys.stderr)
        if result["status"] != "ok":
            print("reckon: " + outcome["connection"] + " " + result["status"], file=sys.stderr)
        activity.record(h, ctx, "run", target=outcome["connection"], command=words, args=command, session=args.session,
                        status=result["status"], exit_code=result["exit_code"], cached=bool(outcome.get("cached_at")) or None)
        return {"_exit": result["exit_code"] if result["status"] == "failed" else EXIT.get(result["status"], 0)}
    if name in INVESTIGATIONS:
        service = resolve_service(root, ctx["env"], args.service)
        question(args.question)
        bounds = window(args.start, args.end)
        draft = {"service_context": service, "service": service["id"], "window": bounds, "playbook": args.playbook}
        tasks, gaps = plan(draft)
        for task in tasks:
            build(task["operation"], task["params"], task["window"], root)
        path, data = create(root, ctx["org"], ctx["env"], args.question, name, service, bounds, args.playbook)
        collection = None
        if args.collect:
            collected = work.collect(h, ctx, root, data["id"], registry=registry)
            collection = {"successful": sum(r["status"] in ("ok", "reused") for r in collected), "attempted": len(collected),
                          "partial": not collected or any(r["status"] not in ("ok", "reused") for r in collected)}
        report = work.report(root, data["id"])
        return {"session": data["id"], "org": ctx["org"], "environment": ctx["env"], "path": str(path), "report": str(report),
                "planned_operations": len(tasks), "gaps": gaps, "collection": collection, "next": "reckon resume " + data["id"]}
    if name == "watch" and args.action == "add":
        from reckonlib import watches
        if len(args.name) != 1 or not args.connection:
            raise ValueError("reckon watch add <name> --connection <conn> --items <path> --key <field> -- <read>")
        return watches.add(h, ctx, args.name[0], args.connection, args.passthrough, args.items, args.key, args.title, registry,
                           args.production)
    if name == "inbox":
        from reckonlib import watches
        if args.all_envs and activity.pinned():
            raise ValueError("a pinned agent sees only its own environment's inbox")
        if not 1 <= args.days <= 365:
            raise ValueError("--days must be 1-365")
        return watches.inbox(h, ctx["org"], args.days, None if args.all_envs else ctx["env"])
    if name == "routine" and args.action == "add":
        from reckonlib import routines
        if not args.name or not args.workflow or not args.at:
            raise ValueError("reckon routine add <name> --workflow <id> --at <when> [--param k=v]")
        return routines.add(h, ctx, args.name, args.workflow, parse_params(args.param), args.at, args.timeout_minutes, args.production)
    if name == "workflows":
        from reckonlib import workflows
        return [{"workflow": r["id"], "title": r["title"], "area": r.get("area"), "budget": r.get("budget"),
                 "params": {k: v.get("help", "") + (" (default " + v["default"] + ")" if v.get("default") else "") for k, v in r.get("params", {}).items()},
                 "checks": [c["id"] for c in r["checks"]], "source": r["source"]} for r in workflows.recipes(root).values()]
    if name == "task":
        recipe = playbook = None
        if args.workflow:
            from reckonlib import workflows
            text, area, budget, recipe, playbook = workflows.start(root, args.workflow, parse_params(args.param))
            args.question = args.question or text
            args.area = args.area or area
            args.budget = args.budget if args.budget is not None else budget
        elif args.param:
            raise ValueError("--param needs --workflow")
        if args.question is None:
            raise ValueError("task needs --question or --workflow")
        question(args.question)
        if bool(args.start) != bool(args.end):
            raise ValueError("give both --from and --to, or neither")
        if args.area:
            orgs.name(args.area, "area")
        bounds = window(args.start, args.end) if args.start else None
        if args.budget is not None and not 1 <= args.budget <= 1000:
            raise ValueError("--budget must be 1-1000")
        last = workflows.previous(root, ctx["env"], recipe) if recipe else None
        path, data = create(root, ctx["org"], ctx["env"], args.question, "task", bounds=bounds, playbook=playbook, area=args.area,
                            budget=args.budget, workflow=recipe, previous=last)
        report = work.report(root, data["id"])
        result = {"session": data["id"], "org": ctx["org"], "environment": ctx["env"], "path": str(path), "report": str(report),
                  "next": "reckon run <connection> --session " + data["id"] + " -- <arguments>"}
        if last:
            result["previous"] = work.summary(root, last)
            result["start_with"] = ("the last review's actions: for each, record what happened with reckon note " + data["id"] +
                                    " --kind finding --follows <action-note-id> --evidence <ids>")
        if recipe:
            result["checks"] = [c["id"] + ": " + c["text"] for c in recipe["checks"]] + (["follow-up: each action from the last review"]
                                                                                          if last and last["actions"] else [])
            result["done_when"] = "reckon check " + data["id"] + " shows no check left to do; skip one with reckon note " + data["id"] + \
                " --kind question --text \"skip <check>: <reason>\""
        return result
    if name == "services":
        if args.action == "show":
            if not args.name:
                raise ValueError("services show requires a name")
            return resolve_service(root, ctx["env"], args.name)
        entries = services(root, ctx["env"])
        if args.action == "validate":
            for service in entries:
                tasks, _ = plan({"service_context": service, "service": service["id"], "playbook": "latency",
                                 "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:30:00Z"}})
                for task in tasks:
                    build(task["operation"], task["params"], task["window"], root)
            return {"org": ctx["org"], "environment": ctx["env"], "valid_services": len(entries)}
        return entries
    if name == "capabilities":
        readiness = {}
        for provider in sorted({v[0] for v in OPERATIONS.values()}):
            readiness[provider] = (work.source(h, ctx, provider, registry)[3] or "ready") if provider != "git" else "local"
        return {"org": ctx["org"], "environment": ctx["env"], "live_verified": False, "operations": [
            {"operation": op, "provider": v[0], "capability": v[1], "required": v[2].split(), "optional": v[3].split(),
             "readiness": readiness[v[0]]} for op, v in OPERATIONS.items()]}
    if name == "collect":
        if args.params != "{}" and not args.operation:
            raise ValueError("--params requires --operation")
        result = work.collect(h, ctx, root, args.session, args.operation, json.loads(args.params), args.refresh,
                              args.timeout, args.max_bytes, registry)
        work.report(root, args.session)
        return result
    if name == "resume":
        return work.resume(root, args.session)
    if name == "check":
        # Checks are judged in the session's own environment, never another one where tools look unavailable.
        work.matching(session_path(root, args.session)[1], ctx)
        return work.check(root, args.session, available(h, ctx, registry), registry)
    if name == "report":
        return {"report": str(work.report(root, args.session))}
    if name == "note":
        if args.withdraw:
            return work.withdraw(root, args.session, args.withdraw, args.text)
        if not args.kind:
            raise ValueError("note needs --kind, or --withdraw <note-id>")
        return work.note(root, args.session, args.kind, args.text, args.evidence, args.next_check, args.follows)
    if name == "history":
        if not 1 <= args.limit <= 200:
            raise ValueError("history limit must be 1–200")
        return work.history(root, None if args.all_envs else ctx["env"], args.service, args.query, args.limit)
    if name == "promote":
        return {"incident": str(work.promote(root, args.session, args.slug)), "note": "Preliminary snapshot; complete RCA.md using the RCA template."}
    if name == "brief":
        from reckonlib.brief import brief
        if not 1 <= args.days <= 365:
            raise ValueError("--days must be 1-365")
        if args.all_envs and activity.pinned():
            raise ValueError("a pinned agent sees only its own environment; run reckon brief --all-envs in your own terminal")
        result = brief(h, ctx, registry, args.days, ctx["data"]["environments"] if args.all_envs else None)
        if args.save:
            from datetime import datetime, timezone
            from reckonlib.brief import render
            from reckonlib.store import write as save
            target = local_path(root, "briefs", datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-" + result["environment"] + ".md")
            save(target, render(result) + "\n")
            result["saved"] = str(target)
        return result
    if name == "act":
        from reckonlib import actions
        if args.action == "list":
            return actions.listing(root)
        if args.action == "show":
            return actions.load(root, args.target or "")[1]
        if args.action == "reconcile":
            done = actions.reconcile(h, ctx, root, args.target or "", registry)
            activity.record(h, ctx, "act.reconcile", target=done["connection"], action=done["id"], status=done["status"])
            return done
        params = json.loads(args.params)
        if args.body_stdin:
            params["description" if (args.target or "").startswith("jira.") else "body"] = sys.stdin.read(65537)
        if args.session:
            work.matching(session_path(root, args.session)[1], ctx)
        record, text = actions.prepare(h, ctx, root, args.target or "", params, registry, session=args.session)
        activity.record(h, ctx, "act.prepare", target=record["connection"], action=record["id"], operation=record["operation"])
        print(text, file=sys.stderr)
        return {"action": record["id"], "status": record["status"],
                "next": "the operator reviews and runs: reckon act apply " + record["id"]}
    if name == "close":
        path, _ = session_path(root, args.session)
        with lock(path):
            _, data = session_path(root, args.session)
            if args.outcome == "diagnosed" and not any(n["kind"] == "finding" for n in data["notes"]):
                raise ValueError("record an evidence-backed finding before marking the investigation diagnosed")
            if args.outcome == "done" and data.get("workflow"):
                work.matching(data, ctx)
                checked = work.check(root, args.session, available(h, ctx, registry), registry)
                if "workflow" not in checked:
                    raise ValueError("cannot close as done: " + "; ".join(checked["problems"]))
                todo = checked["workflow"]["todo"]
                if "report-actions" in todo:
                    raise ValueError("report-actions: the recorded actions are outside the workflow's limit; merge or withdraw some with reckon note " +
                                     args.session + " --withdraw <note-id> --text \"<why>\"")
                if todo:
                    raise ValueError("workflow checks still to do: " + ", ".join(todo) + "; read them, or record why with reckon note " +
                                     args.session + " --kind question --text \"skip <check>: <reason>\"")
            data.update(status=args.outcome, closed_at=now())
            write_json(path / "session.json", data)
        return {"session": args.session, "status": args.outcome}


def parse_params(items):
    given = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or key in given:
            raise ValueError("--param takes KEY=VALUE, once per key")
        given[key] = value
    return given


def available(h, ctx, registry):
    """Providers this org can reach in this environment, for workflow checks."""
    return {r["provider"] for r in orgs.describe(h, ctx, registry) if r["applies"]}


def question(text):
    if not text.strip() or len(text) > 16000:
        raise ValueError("question must contain 1–16000 characters")


def saved_context(h, registry):
    """reckon open ignores a pinned shell and uses the saved default."""
    pinned = {k: os.environ.pop(k) for k in ("RECKON_ORG", "RECKON_ENV") if k in os.environ}
    try:
        return orgs.context(h, registry=registry)
    finally:
        os.environ.update(pinned)


def main():
    if sys.version_info < (3, 9):
        print("reckon requires Python 3.9 or newer", file=sys.stderr)
        return 1
    # Everything after the first "--" belongs to the tool (run) or the agent runtime (open).
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)
    args = parser().parse_args(argv[:cut])
    args.passthrough = argv[cut + 1:]
    if args.passthrough and args.command not in ("run", "open", "watch"):
        parser().error("only run, open and watch add take arguments after --")
    try:
        result = execute(args)
        if isinstance(result, dict) and "_exit" in result:
            return result["_exit"]
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            display(args.command, result)
        if args.command == "verify" and any(r["status"] not in ("ok", "skipped") for r in result):
            return 1
        if args.command == "collect" and any(r["status"] not in ("ok", "reused") for r in result):
            return 2  # explicit partial collection; successful artifacts remain available
        if isinstance(result, dict) and result.get("collection") and result["collection"]["partial"]:
            return 2
        if args.command in ("doctor", "check") and result["problems"]:
            return 1
        if args.command == "act" and isinstance(result, dict) and result.get("status") in ("failed", "unknown", "cancelled"):
            return 2 if result["status"] == "unknown" else 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("reckon: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("reckon: interrupted; saved evidence remains resumable", file=sys.stderr)
        return 130


def display(command, result):
    if command in ("status", "preflight"):
        tag = " (PRODUCTION)" if result["production"] else ""
        print(result["org"] + " · " + result["environment"] + tag + "  (from " + result["resolved_from"] + ")")
        print("workspace: " + result["workspace"])
        for row in result["connections"]:
            if command == "status":
                where = "" if row["applies"] else "  [" + row["scope"] + " only]"
                print("  " + row["connection"].ljust(22) + row["provider"].ljust(14) + row["state"] + (" label=" + row["label"] if row.get("label") else "") +
                      (" account=" + row["account"] if row.get("account") else "") + where + checked(row))
            else:
                print("  " + row["connection"].ljust(22) + row["provider"].ljust(14) + row["state"] + (" label=" + row["label"] if row.get("label") else "") +
                      checked(row))
        if not result["connections"]:
            print("  no connections yet: reckon connect <name> --provider <tool>")
        if command == "preflight":
            if result["other_environments"]:
                print("other environments: " + ", ".join(result["other_environments"]))
            print("knowledge: " + " · ".join(k + " " + str(v) + " files" for k, v in result["knowledge"].items()))
            for item in result["open_sessions"]:
                print("open session: " + item["id"] + "  " + item["question"])
            print("incidents: " + str(result["incidents"]["count"]) + (" · latest " + result["incidents"]["latest"] if result["incidents"]["latest"] else ""))
            for warning in result["warnings"]:
                print("warning: " + warning)
            print("note: " + result["note"])
    elif command == "connect" and isinstance(result, list):
        for row in result:
            print(row["provider"].ljust(15) + row["title"].ljust(24) + row["mode"].ljust(10) + ", ".join(row["settings"]))
    elif command == "check":
        print(("ok" if result["ok"] else "problems") + "  " + result["session"])
        for item in result["problems"]:
            print("  problem: " + item)
        for item in result["warnings"]:
            print("  warning: " + item)
        if result.get("workflow"):
            print("workflow " + result["workflow"]["workflow"] + ":")
            for row in result["workflow"]["checks"]:
                print("  " + row["status"].ljust(12) + row["id"].ljust(16) + row["text"])
    elif command == "inbox":
        for row in result:
            print(row["at"][:16] + "  [" + row["environment"] + "] " + row["watch"].ljust(18) + row["title"])
        if not result:
            print("nothing new")
    elif command == "watch" and isinstance(result, list) and result and "status" in result[0]:
        for row in result:
            print(row["status"].ljust(9) + row["watch"].ljust(20) + (str(row["new"]) + " new of " + str(row["items"]) if "new" in row else row.get("detail", "")))
    elif command == "spend":
        for row in result:
            if "models" not in row:
                print(row["org"] + ": budget " + (format(row["agent_token_budget"]["tokens"], ",") + " tokens per " +
                                                  str(row["agent_token_budget"]["days"]) + " days" if row["agent_token_budget"] else "cleared"))
                continue
            cost = "  ~$" + format(row["usd"], ".2f") if row["usd"] is not None else ""
            if row.get("budget"):
                cost += "  (budget " + format(row["budget"]["tokens"], ",") + " per " + str(row["budget"]["days"]) + " days)"
            print(row["org"].ljust(18) + format(row["tokens"], ",").rjust(16) + " tokens in " + str(row["days"]) + " days" + cost)
            for m in row["models"]:
                print("  " + (m["runtime"] + " " + m["model"]).ljust(34) + "in " + format(m["input"], ",") + "  out " + format(m["output"], ",") +
                      "  cache read " + format(m["cache_read"], ",") + "  cache write " + format(m["cache_write"], ","))
            if row["unpriced_models"] and row["models"]:
                print("  no price for " + ", ".join(row["unpriced_models"]) + " (add it to $RECKON_HOME/prices.json for an estimate)")
    elif command == "workflows":
        for row in result:
            print(row["workflow"].ljust(18) + row["title"])
            for key, text in row["params"].items():
                print("  --param " + key + "=  " + text)
            print("  checks: " + ", ".join(row["checks"]))
    elif command == "brief":
        from reckonlib.brief import render, render_all
        print(render_all(result) if "orgs" in result else render(result))
        if result.get("saved"):
            print("Saved: " + result["saved"])
    elif command == "log":
        for row in result:
            what = row.get("operation") or " ".join(row.get("command", []))
            state = row.get("status") or row.get("detail", "")
            print(row["at"] + "  " + row["environment"].ljust(6) + " " + row["actor"].ljust(8) + " " + row["event"].ljust(13) +
                  str(row.get("target") or "").ljust(16) + " " + what + ("  " + state if state else ""))
        if not result:
            print("no activity in this period")
    elif command == "verify":
        for row in result:
            print(("ok      " if row["status"] == "ok" else row["status"].ljust(8)) + row["connection"].ljust(22) + row.get("detail", "")[:200])
        if not result:
            print("no connections apply to this environment")
    elif command == "doctor":
        print("home: " + result["home"])
        for check in result["checks"]:
            print("  " + check)
        for problem in result["problems"]:
            print("  problem: " + problem)
        print("healthy" if not result["problems"] else str(len(result["problems"])) + " problem(s)")
    elif command == "resume":
        session = result["session"]
        print(session["id"] + " · " + str(session.get("org")) + "/" + session["environment"] + " · " + (session.get("service") or session["mode"]) + " · " + session["status"])
        print(session["question"])
        if session.get("window"):
            print(session["window"]["from"] + " → " + session["window"]["to"])
        for item in result["evidence"]:
            print("  " + item["id"] + "  " + item["status"] + "  " + item["label"] + " — " + item["summary"])
        for item in session["notes"]:
            print("  " + item["kind"] + ": " + item["text"])
            if item["next_check"]:
                print("    Next: " + item["next_check"])
        for gap in result["gaps"]:
            print("  Gap: " + gap)
        if result.get("previous"):
            last = result["previous"]
            followed = {f for n in session["notes"] if not n.get("withdrawn_at") for f in n.get("follows", [])}
            print("Last review " + last["session"] + " (" + last["status"] + "):")
            for action in last["actions"]:
                print("  action " + action["id"] + ("  followed up" if action["id"] in followed else "  to follow up") + ": " + action["text"])
            for done in last["writes"]:
                print("  write " + str(done["action"]) + "  " + str(done["status"]) + (" " + done["url"] if done.get("url") else ""))
        print("Use --json for the full context, evidence paths, operation plan and instructions.")
    elif command == "collect":
        for item in result:
            print(item["id"] + "  " + item["status"] + "  " + item["label"])
    elif command == "history":
        for item in result:
            print(item["id"] + "  " + item["environment"] + "  " + item["question"])
        if not result:
            print("No matching saved sessions.")
    elif command == "capabilities":
        print(result["org"] + " · " + result["environment"] + " (local readiness; connectivity not verified)")
        for item in result["operations"]:
            print("  " + item["operation"].ljust(28) + item["readiness"])
    elif isinstance(result, dict) and "report" in result:
        if result.get("synthetic"):
            print("Synthetic demo completed: no provider connections.")
        for key in ("session", "org", "environment"):
            if key in result:
                print(key.capitalize() + ": " + result[key])
        if result.get("collection"):
            info = result["collection"]
            print(f"Collection: {info['successful']}/{info['attempted']} successful" + (" (partial; inspect report gaps)" if info["partial"] else ""))
        print("Report: " + result["report"])
        if result.get("next") or result.get("resume"):
            print("Next: " + result.get("next", result.get("resume")))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
