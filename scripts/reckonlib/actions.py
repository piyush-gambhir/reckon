"""Approved writes: the agent prepares an exact action, the operator applies it.

prepare freezes the payload and its target and stores them with a hash. apply runs
in the operator's own terminal, shows the same preview, asks for confirmation (the
org name for production or org-wide connections), runs the command once, and keeps
a receipt: applied, failed, or unknown when the outcome cannot be established.
"""
from fnmatch import fnmatchcase
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

from . import orgs
from .process import run as bounded
from .store import identifier, local_path, lock, now, private_dir, read_json, write_json

# operation: provider, required params, optional params
WRITES = {
    "github.issue.create": ("github", ("repo", "title", "body"), ("labels",)),
}


def validate(operation, params):
    if operation not in WRITES:
        raise ValueError("unsupported write: " + operation + " (supported: " + ", ".join(sorted(WRITES)) + ")")
    provider, required, optional = WRITES[operation]
    if not isinstance(params, dict) or set(params) - set(required) - set(optional):
        raise ValueError("unknown parameters for " + operation)
    for key in required:
        if not isinstance(params.get(key), str) or not params[key].strip():
            raise ValueError(operation + " requires " + key)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", params["repo"]):
        raise ValueError("repo must be owner/name")
    if len(params["title"]) > 256 or len(params["body"]) > 65536 or "\x00" in params["title"] + params["body"]:
        raise ValueError("title or body is too long or contains NUL")
    labels = params.get("labels", "")
    if labels and not re.fullmatch(r"[\w .:/-]+(,[\w .:/-]+)*", labels):
        raise ValueError("labels must be a comma-separated list of plain names")
    return provider


def argv(operation, params):
    """The exact command; the body travels on standard input, never in argv."""
    args = ["gh", "issue", "create", "--repo", params["repo"], "--title", params["title"], "--body-file", "-"]
    if params.get("labels"):
        args += ["--label", params["labels"]]
    return args, params["body"].encode()


def digest(record):
    frozen = {k: record[k] for k in ("org", "environment", "connection", "operation", "params")}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def printable(text):
    """Show control characters instead of letting them redraw the approval screen."""
    return "".join(c if c in "\n\t" or (c >= " " and c != "\x7f") else "\\x%02x" % ord(c) for c in text)


def terminal():
    return sys.stdin.isatty()


def ask(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline()


def preview(record, live, full=False):
    lines = ["Action " + record["id"] + " (" + record["status"] + ")",
             "  " + record["operation"] + " on " + record["connection"] + " in " + record["org"] + "/" + record["environment"] +
             ("  [LIVE: production or org-wide account]" if live else "")]
    for key, value in sorted(record["params"].items()):
        # Approval shows every character; the agent-facing summary may abbreviate.
        text = printable(value if full or len(value) < 2000 else value[:2000] + "\n... (" + str(len(value)) + " characters)")
        lines.append("  " + key + ":" + ("\n    " + text.replace("\n", "\n    ") if "\n" in text else " " + text))
    return "\n".join(lines)


def is_live(ctx, conn):
    """Production environments and connections shared by every environment act on real data."""
    return ctx["production"] or "env" not in conn


def prepare(h, ctx, root, operation, params, registry):
    provider = validate(operation, params)
    cname, conn = orgs.resolve(ctx, provider)
    repos = conn.get("resources", {}).get("repo")
    if repos and not any(fnmatchcase(params["repo"], p) for p in repos):
        raise ValueError("repo " + params["repo"] + " is outside " + cname + " (allowed: " + ", ".join(repos) + ")")
    action_id = datetime.now(timezone.utc).strftime("a-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    record = {"schema_version": 1, "id": action_id, "org": ctx["org"], "environment": ctx["env"], "connection": cname,
              "operation": operation, "params": params, "status": "prepared", "prepared_at": now(), "receipt": None}
    record["payload_sha256"] = digest(record)
    path = local_path(root, "actions", action_id)
    private_dir(path)
    write_json(path / "action.json", record)
    return record, preview(record, is_live(ctx, conn))


def load(root, action_id):
    path = local_path(root, "actions", identifier(action_id))
    if not (path / "action.json").is_file():
        raise ValueError("unknown action: " + action_id)
    return path, read_json(path / "action.json")


def listing(root):
    base = local_path(root, "actions")
    if not base.is_dir():
        return []
    rows = [read_json(p) for p in sorted(base.glob("*/action.json"), reverse=True)]
    return [{k: r.get(k) for k in ("id", "status", "operation", "connection", "environment", "prepared_at")} for r in rows]


def apply(h, ctx, root, action_id, registry, confirm=None):
    """Run one prepared action once. confirm(prompt) returns what the operator typed."""
    # Enforced here, not only in the CLI, so no caller inside an agent can apply.
    if os.environ.get("RECKON_ORG"):
        raise ValueError("reckon act apply is for the operator, not a pinned agent")
    if not terminal():
        raise ValueError("reckon act apply is for the operator's own terminal")
    confirm = confirm or ask
    path, _ = load(root, action_id)
    with lock(path):
        _, record = load(root, action_id)
        if record["status"] != "prepared":
            raise ValueError("action " + action_id + " is " + record["status"] + "; each action runs once")
        if record["org"] != ctx["org"] or record["environment"] != ctx["env"]:
            raise ValueError("action belongs to " + record["org"] + "/" + record["environment"] + "; this context is " + ctx["org"] + "/" + ctx["env"])
        if record["payload_sha256"] != digest(record):
            raise ValueError("action " + action_id + " changed after it was prepared; prepare it again")
        validate(record["operation"], record["params"])
        # The exact connection that was prepared; never a provider lookup that could pick another.
        cname, conn = record["connection"], ctx["data"]["connections"].get(record["connection"])
        if not conn or conn["provider"] != WRITES[record["operation"]][0] or not orgs.applies(conn, ctx["env"]):
            raise ValueError("connection " + cname + " changed or no longer serves " + ctx["env"] + "; prepare the action again")
        repos = conn.get("resources", {}).get("repo")
        if repos and not any(fnmatchcase(record["params"]["repo"], p) for p in repos):
            raise ValueError("repo " + record["params"]["repo"] + " is now outside " + cname + "; prepare the action again")
        live = is_live(ctx, conn)
        print(preview(record, live, full=True), file=sys.stderr)
        expected = ctx["org"] if live else "yes"
        if confirm("Type " + expected + " to apply, anything else to cancel: ").strip() != expected:
            record.update(status="cancelled", decided_at=now())
            write_json(path / "action.json", record)
            return record
        spec = registry[conn["provider"]]
        env, values = orgs.child_env(h, ctx, cname, conn, spec, interactive=True)
        env.update(NO_COLOR="1", GH_PROMPT_DISABLED="1")
        command, body = argv(record["operation"], record["params"])
        # Record the attempt before running, so a crash cannot hide that it may have happened.
        record.update(status="running", approved_at=now())
        write_json(path / "action.json", record)
        result = bounded(command, env=env, cwd=str(local_path(orgs.org_path(h, ctx["org"]), "runtime", cname, "tmp")),
                         timeout=60, max_bytes=262144, stdin=body)
        out = orgs.redact(result["stdout"].decode(errors="replace"), values).strip()
        err = orgs.redact(result["stderr"].decode(errors="replace"), values).strip()
        if result["status"] == "ok":
            status = "applied"
        elif result["status"] == "failed":
            status = "failed"
        else:
            # Timed out or cancelled: the server may have accepted it. Check before retrying.
            status = "unknown"
        record.update(status=status, completed_at=now(), receipt={"exit_code": result["exit_code"], "stdout": out[-2000:], "stderr": err[-2000:],
                                                                  "url": next(iter(re.findall(r"https://\S+", out)), None)})
        write_json(path / "action.json", record)
        return record
