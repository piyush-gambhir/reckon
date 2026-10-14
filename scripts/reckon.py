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
from reckonlib import connectors, orgs, workspace  # noqa: E402
from reckonlib import investigation as work  # noqa: E402
from reckonlib.operations import OPERATIONS, build, plan  # noqa: E402
from reckonlib.store import create, local_path, lock, resolve_service, services, session_path, window, write_json  # noqa: E402

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
    add.add_argument("--envs", default="prod,uat,dev", help="comma-separated environments (default prod,uat,dev)")
    add.add_argument("--production", help="comma-separated production environments (default: prod or production)")
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
    command.add_argument("--question", required=True, help="what this work is about")
    command.add_argument("--area", help="dev, product, gtm, sales, ops or your own word")
    command.add_argument("--budget", type=int, help="most reads this session may record (protects quotas and production)")
    command.add_argument("--from", dest="start", help="optional window start (ISO time with timezone)")
    command.add_argument("--to", dest="end", help="optional window end")

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
    command = sub.add_parser("note", parents=[scope], help="record findings, hypotheses, rejected ideas and questions")
    command.add_argument("session")
    command.add_argument("--kind", choices=("finding", "hypothesis", "rejected", "question"), required=True)
    command.add_argument("--text", required=True)
    command.add_argument("--evidence", nargs="*", default=[])
    command.add_argument("--next-check", default="")
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
    command.add_argument("target", nargs="?", help="prepare: the write operation (github.issue.create); apply/show: the action ID")
    command.add_argument("--params", default="{}", help="prepare: parameters as a JSON object")
    command.add_argument("--body-stdin", action="store_true", help="prepare: read the body parameter from standard input")
    command = sub.add_parser("brief", parents=[scope], help="local digest: open work, findings, writes waiting, gaps (no network)")
    command.add_argument("--days", type=int, default=7)
    command.add_argument("--save", action="store_true", help="also write it to the org folder's briefs/<date>-<env>.md")
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
    data["connections"][args.name] = conn
    orgs.save(h, data, registry)
    workspace.ensure(h, ctx, registry)
    path, created = connectors.template(h, ctx["org"], args.name, registry[conn["provider"]])
    spec = registry[conn["provider"]]
    return {"org": ctx["org"], "connection": args.name, "provider": conn["provider"], "environment": conn.get("env") or "all",
            "env_labels": conn.get("env_labels", {}), "resources": conn.get("resources", {}), "secrets": str(path), "secrets_created": created,
            "next": "edit " + str(path) + (" or run: reckon login " + args.name if spec.get("login") else "") +
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


def preflight(h, ctx, registry):
    root = orgs.workspace(h, ctx["org"])
    rows = orgs.describe(h, ctx, registry)
    sessions = work.history(root, ctx["env"], limit=200)
    open_sessions = [s for s in sessions if s.get("status") == "open"]
    incidents = sorted(p.name for p in (root / "incidents").iterdir() if p.is_dir() and not p.name.startswith(".")) if (root / "incidents").is_dir() else []
    warnings = []
    if ctx["production"]:
        warnings.append("PRODUCTION: prefer the cheapest signal and bound every query")
    if not any(r["applies"] for r in rows):
        warnings.append("no connections for " + ctx["env"] + "; ask the operator to run reckon connect")
    if not knowledge_count(root, ctx["env"]) and not knowledge_count(root, "_shared"):
        warnings.append("no knowledge for this org yet; do not invent facts")
    return {"org": ctx["org"], "name": ctx["data"]["name"], "environment": ctx["env"], "production": ctx["production"],
            "resolved_from": ctx["source"], "workspace": str(root),
            "connections": [{k: r[k] for k in ("connection", "provider", "state", "label")} for r in rows if r["applies"]],
            "other_environments": [r["connection"] + " (" + r["scope"] + ")" for r in rows if not r["applies"]],
            "knowledge": {"_shared": knowledge_count(root, "_shared"), ctx["env"]: knowledge_count(root, ctx["env"])},
            "open_sessions": [{"id": s["id"], "question": s["question"]} for s in open_sessions[:5]],
            "incidents": {"count": len(incidents), "latest": incidents[-1] if incidents else None},
            "warnings": warnings, "note": "ready means configured locally; reckon verify proves live access"}


def version_tuple(text):
    import re
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
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
                             " (run: " + row["binary"] + " update, or remove it and rerun bash scripts/setup.sh)")
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
    checks.append("reckon command: " + (str(link) if link.is_symlink() else "created on the first reckon open"))
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
    if os.environ.get("RECKON_ORG") and (name in ("use", "connect", "login", "migrate", "open") or name == "org" and args.action == "add"
                                         or name == "act" and args.action == "apply"):
        # An agent started by reckon open works only inside its pinned org and environment.
        raise ValueError("reckon " + name + " is for the operator's own terminal, not a pinned agent")
    if name == "org":
        if args.action == "add":
            envs = [e.strip() for e in args.envs.split(",") if e.strip()]
            production = [e.strip() for e in args.production.split(",")] if args.production else None
            data = orgs.create(h, args.org, args.name, envs, production, registry)
            workspace.ensure(h, {"org": args.org, "data": data}, registry)
            if not (h / "context").exists():
                orgs.set_context(h, args.org, envs[0], registry)
            return {"org": data["id"], "environments": data["environments"], "production": data["production"],
                    "next": "reckon connect <name> --org " + args.org + " --provider <tool> [--for-env <env>]"}
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
        return actions.apply(h, ctx, orgs.workspace(h, owner), args.target, registry)

    ctx = context(h, args, registry)
    root = orgs.workspace(h, ctx["org"])
    if name == "status":
        return {"org": ctx["org"], "name": ctx["data"]["name"], "environment": ctx["env"], "production": ctx["production"],
                "resolved_from": ctx["source"], "workspace": str(root), "connections": orgs.describe(h, ctx, registry)}
    if name == "preflight":
        return preflight(h, ctx, registry)
    if name == "verify":
        return connectors.verify(h, ctx, registry, tuple(args.connections))
    if name == "run":
        command = args.passthrough
        if not command:
            raise ValueError("usage: reckon run <connection|tool> -- <arguments>")
        if args.label and not args.session:
            raise ValueError("--label needs --session")
        if args.session:
            spath, sdata = session_path(root, args.session)
            work.matching(sdata, ctx)
            work.spend(spath, sdata, 1)
        stdin = sys.stdin.buffer.read(1048577) if args.stdin else None
        if stdin is not None and len(stdin) > 1048576:
            raise ValueError("standard input over 1 MiB; pass a file argument instead")
        cache_seconds = 0
        if args.cache:
            unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(args.cache[-1:])
            if not unit or not args.cache[:-1].isdigit():
                raise ValueError("--cache takes a number and s, m, h or d, e.g. 15m")
            cache_seconds = int(args.cache[:-1]) * unit
        outcome = connectors.run(h, ctx, args.target, command, registry, args.timeout, args.max_bytes, stdin, cache_seconds)
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
    if name == "task":
        question(args.question)
        if bool(args.start) != bool(args.end):
            raise ValueError("give both --from and --to, or neither")
        if args.area:
            orgs.name(args.area, "area")
        bounds = window(args.start, args.end) if args.start else None
        if args.budget is not None and not 1 <= args.budget <= 1000:
            raise ValueError("--budget must be 1-1000")
        path, data = create(root, ctx["org"], ctx["env"], args.question, "task", bounds=bounds, area=args.area, budget=args.budget)
        report = work.report(root, data["id"])
        return {"session": data["id"], "org": ctx["org"], "environment": ctx["env"], "path": str(path), "report": str(report),
                "next": "reckon run <connection> --session " + data["id"] + " -- <arguments>"}
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
        return work.check(root, args.session)
    if name == "report":
        return {"report": str(work.report(root, args.session))}
    if name == "note":
        return work.note(root, args.session, args.kind, args.text, args.evidence, args.next_check)
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
        result = brief(h, ctx, registry, args.days)
        if args.save:
            from datetime import datetime, timezone
            from reckonlib.brief import render
            from reckonlib.store import write as save
            target = local_path(root, "briefs", datetime.now(timezone.utc).strftime("%Y-%m-%d") + "-" + ctx["env"] + ".md")
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
            return actions.reconcile(h, ctx, root, args.target or "", registry)
        params = json.loads(args.params)
        if args.body_stdin:
            params["body"] = sys.stdin.read(65537)
        record, text = actions.prepare(h, ctx, root, args.target or "", params, registry)
        print(text, file=sys.stderr)
        return {"action": record["id"], "status": record["status"],
                "next": "the operator reviews and runs: reckon act apply " + record["id"]}
    if name == "close":
        path, _ = session_path(root, args.session)
        with lock(path):
            _, data = session_path(root, args.session)
            if args.outcome == "diagnosed" and not any(n["kind"] == "finding" for n in data["notes"]):
                raise ValueError("record an evidence-backed finding before marking the investigation diagnosed")
            data["status"] = args.outcome
            write_json(path / "session.json", data)
        return {"session": args.session, "status": args.outcome}


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
    if args.passthrough and args.command not in ("run", "open"):
        parser().error("only run and open take arguments after --")
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
                print("  " + row["connection"].ljust(22) + row["provider"].ljust(14) + row["state"] + (" label=" + row["label"] if row.get("label") else "") + where)
            else:
                print("  " + row["connection"].ljust(22) + row["provider"].ljust(14) + row["state"] + (" label=" + row["label"] if row.get("label") else ""))
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
    elif command == "brief":
        from reckonlib.brief import render
        print(render(result))
        if result.get("saved"):
            print("Saved: " + result["saved"])
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
