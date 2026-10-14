"""Routines: a workflow run on a schedule by a headless agent pinned to one org and env.

A routine is saved in the org folder (outside the agent's workspace) and run by
launchd or cron through `reckon routine run`. Each run starts Claude Code
(`claude -p`) or Codex (`codex exec`) in the org's workspace with the same pinned,
credential-free environment as `reckon open`, so it reads through reckon run and can
prepare writes but never apply them. A run still going makes the next one skip."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from . import activity, orgs, workflows, workspace
from .store import local_path, lock, read_json, write_json

DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
AT = re.compile(r"(daily|weekdays|sun|mon|tue|wed|thu|fri|sat) ([01]\d|2[0-3]):([0-5]\d)")


def path(h, org):
    return local_path(orgs.org_path(h, org), "routines.json")


def load(h, org):
    target = path(h, org)
    return read_json(target) if target.is_file() else {}


def schedule(text):
    match = AT.fullmatch(text.strip().lower())
    if not match:
        raise ValueError("--at takes daily HH:MM, weekdays HH:MM or a day like mon HH:MM (24-hour, local time)")
    when, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
    days = list(range(7)) if when == "daily" else list(range(1, 6)) if when == "weekdays" else [DAYS.index(when)]
    return {"at": text.strip().lower(), "days": days, "hour": hour, "minute": minute}


def add(h, ctx, name, recipe_id, params, at, runtime="claude", timeout_minutes=45):
    orgs.name(name, "routine")
    if runtime not in ("claude", "codex"):
        raise ValueError("--runtime is claude or codex")
    if not 5 <= timeout_minutes <= 240:
        raise ValueError("--timeout-minutes must be 5-240")
    # Fail now, not at 8 in the morning: the recipe and its parameters must start a session.
    workflows.start(orgs.workspace(h, ctx["org"]), recipe_id, params)
    routines = load(h, ctx["org"])
    routines[name] = {"environment": ctx["env"], "workflow": recipe_id, "params": params, "runtime": runtime,
                      "schedule": schedule(at), "timeout_minutes": timeout_minutes}
    write_json(path(h, ctx["org"]), routines)
    return dict(routines[name], routine=name, org=ctx["org"])


def remove(h, org, name):
    routines = load(h, org)
    if name not in routines:
        raise ValueError("unknown routine: " + name)
    del routines[name]
    write_json(path(h, org), routines)
    return {"org": org, "removed": name, "note": "remove its launchd job or crontab line too (reckon routine install shows where)"}


def prompt(routine):
    params = " ".join("--param " + k + "=" + v for k, v in sorted(routine["params"].items()))
    return ("Scheduled routine. Run the " + routine["workflow"] + " workflow for this org and environment.\n"
            "1. Start it: reckon task --workflow " + routine["workflow"] + (" " + params if params else "") + "\n"
            "2. Follow its playbook in the reckon skill (workflows/" + routine["workflow"] + ".md). Save every read with "
            "--session <id>; use --cache 6h for repeated reads.\n"
            "3. Record findings with evidence (reckon note). Writes: only reckon act prepare, for the operator to apply.\n"
            "4. For a check you cannot do, record why: reckon note <id> --kind question --text \"skip <check>: <reason>\".\n"
            "5. Finish with reckon check <id> and reckon report <id>; close it as done only if check shows nothing to do.\n"
            "Nobody is watching: do not ask questions, and stop rather than guess.")


def argv(routine):
    text = prompt(routine)
    return ["claude", "-p", text] if routine["runtime"] == "claude" else ["codex", "exec", text]


def run(h, org, name, registry):
    routines = load(h, org)
    if name not in routines:
        raise ValueError("unknown routine: " + name)
    routine = routines[name]
    ctx = orgs.context(h, org, routine["environment"], registry)
    folder = local_path(orgs.org_path(h, org), "routines", name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        with lock(folder):
            root = workspace.ensure(h, ctx, registry)
            command = argv(routine)
            if not shutil.which(command[0]):
                raise ValueError(command[0] + " is not on PATH for scheduled runs; reinstall with reckon routine install")
            log = folder / (stamp + ".log")
            with open(os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as out:
                try:
                    code = subprocess.run(command, cwd=str(root), env=workspace.agent_env(h, ctx, registry), stdin=subprocess.DEVNULL,
                                          stdout=out, stderr=subprocess.STDOUT, timeout=routine["timeout_minutes"] * 60).returncode
                    status = "ok" if code == 0 else "failed"
                except subprocess.TimeoutExpired:
                    code, status = None, "timeout"
    except BlockingIOError:
        activity.record(h, ctx, "routine", target=name, status="skipped", detail="the previous run is still going")
        return {"routine": name, "status": "skipped", "detail": "the previous run is still going"}
    activity.record(h, ctx, "routine", target=name, status=status, exit_code=code)
    return {"routine": name, "org": org, "environment": ctx["env"], "status": status, "exit_code": code, "log": str(log)}


def install(h, org, name, routines=None):
    """Write the launchd job (macOS) or print the crontab line (elsewhere). Loading it
    stays a command for the operator to run."""
    routine = (routines or load(h, org)).get(name)
    if not routine:
        raise ValueError("unknown routine: " + name)
    reckon = str(h / "bin" / "reckon") if (h / "bin" / "reckon").exists() else str(Path(sys.argv[0]).resolve())
    command = [reckon, "routine", "run", name, "--org", org]
    # Schedulers start with a bare PATH; keep the one claude, codex and the CLIs were found on.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RECKON_HOME": str(h)}
    label = "com.reckon." + org + "." + name
    sched = routine["schedule"]
    if sys.platform == "darwin":
        target = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
        # A plain file in the operator's LaunchAgents; its folder's permissions are not Reckon's to change.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(plist(label, command, env, sched, local_path(orgs.org_path(h, org), "routines", name)), encoding="utf-8")
        target.chmod(0o644)
        return {"routine": name, "file": str(target),
                "next": "launchctl bootstrap gui/" + str(os.getuid()) + " " + str(target) + "   # remove: launchctl bootout gui/" +
                        str(os.getuid()) + "/" + label + " and delete the file"}
    days = ",".join(str(d) for d in sched["days"])
    line = (str(sched["minute"]) + " " + str(sched["hour"]) + " * * " + days + " " +
            " ".join(k + "=" + quote(v) for k, v in env.items()) + " " + " ".join(quote(a) for a in command))
    return {"routine": name, "crontab": line, "next": "add the crontab line with: crontab -e"}


def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def plist(label, command, env, sched, folder):
    from xml.sax.saxutils import escape
    intervals = "".join("<dict><key>Weekday</key><integer>" + str(d) + "</integer><key>Hour</key><integer>" + str(sched["hour"]) +
                        "</integer><key>Minute</key><integer>" + str(sched["minute"]) + "</integer></dict>" for d in sched["days"])
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>" + escape(label) + "</string>"
            "<key>ProgramArguments</key><array>" + "".join("<string>" + escape(a) + "</string>" for a in command) + "</array>"
            "<key>EnvironmentVariables</key><dict>" + "".join("<key>" + escape(k) + "</key><string>" + escape(v) + "</string>"
                                                             for k, v in env.items()) + "</dict>"
            "<key>StartCalendarInterval</key><array>" + intervals + "</array>"
            "<key>StandardOutPath</key><string>" + escape(str(folder / "launchd.log")) + "</string>"
            "<key>StandardErrorPath</key><string>" + escape(str(folder / "launchd.log")) + "</string>"
            "</dict></plist>\n")
