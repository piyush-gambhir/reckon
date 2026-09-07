#!/usr/bin/env python3
"""Deterministic local support for agent-driven investigations."""
import argparse
import json
from pathlib import Path
import sys

from reckonlib import environment as envs
from reckonlib import investigation as work
from reckonlib.operations import OPERATIONS, build, plan
from reckonlib.process import run
from reckonlib.store import create, lock, read_json, resolve_service, services, session_path, window, write_json


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent, help="workspace root (defaults to this clone)")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("debug", "investigate", "monitor", "analyze"):
        command = sub.add_parser(name, help="start a saved investigation; local until --collect")
        command.add_argument("--env", choices=envs.ENVIRONMENTS)
        command.add_argument("--service", required=True)
        command.add_argument("--question", required=True)
        command.add_argument("--from", dest="start", required=True, help="ISO time with timezone")
        command.add_argument("--to", dest="end", required=True, help="ISO time with timezone")
        command.add_argument("--playbook", choices=("latency", "errors", "workload", "changes"), default="latency")
        command.add_argument("--collect", action="store_true", help="perform the mapped live reads now")
        command.add_argument("--json", action="store_true")
    command = sub.add_parser("services", help="inspect/validate environment service mappings")
    command.add_argument("action", choices=("list", "show", "validate"))
    command.add_argument("name", nargs="?")
    command.add_argument("--env", choices=envs.ENVIRONMENTS)
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("capabilities", help="list supported collection operations and local readiness")
    command.add_argument("--env", choices=envs.ENVIRONMENTS)
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("collect", help="collect saved playbook or one typed operation (live reads)")
    command.add_argument("session")
    command.add_argument("--env", choices=envs.ENVIRONMENTS)
    command.add_argument("--operation", choices=tuple(OPERATIONS))
    command.add_argument("--params", default="{}", help="operation parameters as a JSON object")
    command.add_argument("--refresh", action="store_true", help="make new reads even when this query has saved successful evidence")
    command.add_argument("--timeout", type=float, default=15, help="per-command deadline, at most 120 seconds")
    command.add_argument("--max-bytes", type=int, default=1048576, help="per-command combined output limit, at most 8 MiB")
    command.add_argument("--json", action="store_true")
    for name in ("resume", "report"):
        command = sub.add_parser(name)
        command.add_argument("session")
        command.add_argument("--json", action="store_true")
    command = sub.add_parser("note", help="record findings, hypotheses and next checks")
    command.add_argument("session")
    command.add_argument("--kind", choices=("finding", "hypothesis", "rejected", "question"), required=True)
    command.add_argument("--text", required=True)
    command.add_argument("--evidence", nargs="*", default=[])
    command.add_argument("--next-check", default="")
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("history", help="search local sessions and legacy RCAs")
    command.add_argument("--env", choices=envs.ENVIRONMENTS)
    command.add_argument("--service")
    command.add_argument("--query")
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("promote", help="snapshot a debugging session into a preliminary incident folder")
    command.add_argument("session")
    command.add_argument("--slug", required=True)
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("close", help="mark investigation outcome; does not assert infrastructure recovery")
    command.add_argument("session")
    command.add_argument("--outcome", choices=("diagnosed", "inconclusive"), required=True)
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("demo", help="run a synthetic investigation in a separate local workspace; no network")
    command.add_argument("--json", action="store_true")
    command = sub.add_parser("probe", help="single live connectivity probe (used by PowerShell)")
    command.add_argument("name")
    command.add_argument("--env", choices=envs.ENVIRONMENTS)
    return p


def probe(root, name, explicit):
    active = envs.selected(root, explicit)
    values = envs.load(root, active)
    if name not in envs.readiness(root, values):
        raise ValueError("unknown integration")
    if envs.readiness(root, values)[name]["state"] != "configured":
        raise ValueError("integration is not locally configured")
    commands = {
        "grafana": ["grafana", "user", "current", "-o", "json"],
        "jenkins": ["jenkins", "status", "-o", "json"],
        "cubeapm": ["cubeapm", "metrics", "label-values", "service", "-o", "json"],
        "github": ["gh", "auth", "status"], "aws": ["aws", "sts", "get-caller-identity", "--output", "json"],
        "kubernetes": ["kubectl", "get", "ns", "-o", "name"],
        "kafka-rpk": ["rpk", "cluster", "info"],
        "redis": ["redis-cli", "-u", values.get("REDIS_URL", ""), "PING"],
        "mongodb": ["mongosh", values.get("MONGODB_URI", ""), "--quiet", "--eval", "db.runCommand({ping:1})"],
        "postgres": ["psql", "-c", "SELECT 1"],
        "mysql": ["mysql", "--defaults-extra-file=" + str(Path(values["XDG_CONFIG_HOME"]) / "mysql/my.cnf"), "-e", "SELECT 1"],
        "elasticsearch": ["es", "cluster", "health", "-o", "json"],
        "jira": ["jira", "whoami", "-o", "json"], "nginxpm": ["nginxpm", "proxy", "list", "-o", "json"],
    }
    commands["kafka"] = ["kcat", "-L", "-b", values.get("KAFKA_BOOTSTRAP_SERVERS", ""), "-J"]
    for key, field in (("SECURITY_PROTOCOL", "security.protocol"), ("SASL_MECHANISM", "sasl.mechanism"), ("SASL_USERNAME", "sasl.username"), ("SASL_PASSWORD", "sasl.password")):
        if values.get("KAFKA_" + key):
            commands["kafka"] += ["-X", field + "=" + values["KAFKA_" + key]]
    commands["clickhouse"] = ["clickhouse", "client", "--host", values.get("CLICKHOUSE_HOST", ""), "--port", values.get("CLICKHOUSE_PORT", "9440"),
                              "--user", values.get("CLICKHOUSE_USER", ""), "--password", values.get("CLICKHOUSE_PASSWORD", ""), "--readonly=1", "--query", "SELECT 1"]
    if values.get("CLICKHOUSE_SECURE", "1") != "0":
        commands["clickhouse"] += ["--secure"]
    if values.get("CLICKHOUSE_DATABASE"):
        commands["clickhouse"] += ["--database", values["CLICKHOUSE_DATABASE"]]
    result = run(commands[name], env=envs.scope(values, name), cwd=root)
    if result["status"] != "ok":
        print(envs.redact(result["stderr"].decode(errors="replace"), values), file=sys.stderr)
    return {"integration": name, "environment": active, "status": result["status"]}


def execute(args):
    root = args.root.resolve()
    name = args.command
    if name in ("debug", "investigate", "monitor", "analyze"):
        active = envs.selected(root, args.env)
        service = resolve_service(root, active, args.service)
        if not args.question.strip() or len(args.question) > 16000:
            raise ValueError("question must contain 1–16000 characters")
        bounds = window(args.start, args.end)
        draft = {"service_context": service, "service": service["id"], "window": bounds, "playbook": args.playbook}
        tasks, gaps = plan(draft)
        for task in tasks:
            build(task["operation"], task["params"], task["window"], root)
        path, data = create(root, active, service, args.question, bounds, args.playbook, name)
        collection = None
        if args.collect:
            collected = work.collect(root, data["id"], active)
            collection = {"successful": sum(r["status"] in ("ok", "reused") for r in collected), "attempted": len(collected),
                          "partial": not collected or any(r["status"] not in ("ok", "reused") for r in collected)}
        report = work.report(root, data["id"])
        return {"session": data["id"], "environment": active, "path": str(path), "report": str(report),
                "planned_operations": len(tasks), "gaps": gaps, "collection": collection,
                "next": "scripts/reckon resume " + data["id"]}
    if name == "services":
        active = envs.selected(root, args.env)
        if args.action == "show":
            if not args.name:
                raise ValueError("services show requires a name")
            return resolve_service(root, active, args.name)
        entries = services(root, active)
        if args.action == "validate":
            # Validate operation contracts without touching infrastructure.
            for service in entries:
                tasks, _ = plan({"service_context": service, "service": service["id"], "playbook": "latency",
                                 "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:30:00Z"}})
                for task in tasks:
                    build(task["operation"], task["params"], task["window"], root)
            return {"environment": active, "valid_services": len(entries)}
        return entries
    if name == "capabilities":
        active = envs.selected(root, args.env)
        ready = envs.readiness(root, envs.load(root, active))
        return {"environment": active, "live_verified": False, "operations": [
            {"operation": operation, "provider": values[0], "capability": values[1], "required": values[2].split(),
             "optional": values[3].split(), "readiness": ready[values[0]]["state"]} for operation, values in OPERATIONS.items()]}
    if name == "collect":
        if not 0 < args.timeout <= 120 or not 0 < args.max_bytes <= 8388608:
            raise ValueError("timeout must be 0–120 seconds; output limit 1–8388608 bytes")
        if args.params != "{}" and not args.operation:
            raise ValueError("--params requires --operation")
        result = work.collect(root, args.session, args.env, args.operation, json.loads(args.params), args.refresh, args.timeout, args.max_bytes)
        work.report(root, args.session)
        return result
    if name == "resume":
        return work.resume(root, args.session)
    if name == "report":
        return {"report": str(work.report(root, args.session))}
    if name == "note":
        return work.note(root, args.session, args.kind, args.text, args.evidence, args.next_check)
    if name == "history":
        if not 1 <= args.limit <= 200:
            raise ValueError("history limit must be 1–200")
        return work.history(root, args.env, args.service, args.query, args.limit)
    if name == "promote":
        return {"incident": str(work.promote(root, args.session, args.slug)), "note": "Preliminary snapshot; complete RCA.md using the RCA template."}
    if name == "close":
        path, _ = session_path(root, args.session)
        with lock(path):
            _, data = session_path(root, args.session)
            if args.outcome == "diagnosed" and not any(n["kind"] == "finding" for n in data["notes"]):
                raise ValueError("record an evidence-backed finding before marking the investigation diagnosed")
            data["status"] = args.outcome
            write_json(path / "session.json", data)
        return {"session": args.session, "status": args.outcome}
    if name == "probe":
        return probe(root, args.name, args.env)
    if name == "demo":
        from reckonlib.demo import demo
        return demo(root)


def main():
    if sys.version_info < (3, 9):
        print("reckon requires Python 3.9 or newer", file=sys.stderr)
        return 1
    args = parser().parse_args()
    try:
        result = execute(args)
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            display(args.command, result)
        if args.command == "probe" and result["status"] != "ok":
            return 1
        if args.command == "collect" and any(r["status"] not in ("ok", "reused") for r in result):
            return 2  # explicit partial collection; successful artifacts remain available
        if isinstance(result, dict) and result.get("collection") and result["collection"]["partial"]:
            return 2
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("reckon: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("reckon: interrupted; saved evidence remains resumable", file=sys.stderr)
        return 130


def display(command, result):
    if command == "resume":
        session = result["session"]
        print(session["id"] + " · " + session["environment"] + " · " + session["service"] + " · " + session["status"])
        print(session["question"])
        print(session["window"]["from"] + " → " + session["window"]["to"])
        for item in result["evidence"]:
            print("  " + item["id"] + "  " + item["status"] + "  " + item["label"] + " — " + item["summary"])
        for item in session["notes"]:
            print("  " + item["kind"] + ": " + item["text"])
            if item["next_check"]:
                print("    Next: " + item["next_check"])
        for gap in result["gaps"]:
            print("  Gap: " + gap)
        print("Use --json for the full context, evidence paths, operation plan and investigation instructions.")
    elif command == "collect":
        for item in result:
            print(item["id"] + "  " + item["status"] + "  " + item["label"])
    elif command == "history":
        for item in result:
            print(item["id"] + "  " + item["environment"] + "  " + item["question"])
        if not result:
            print("No matching saved investigations.")
    elif command == "capabilities":
        print("Environment: " + result["environment"] + " (local readiness; connectivity not verified)")
        for item in result["operations"]:
            print("  " + item["operation"].ljust(28) + item["readiness"])
    elif isinstance(result, dict) and "report" in result:
        if result.get("synthetic"):
            print("Synthetic demo completed — no provider connections.")
        if "session" in result:
            print("Session: " + result["session"])
        if "environment" in result:
            print("Environment: " + result["environment"])
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
