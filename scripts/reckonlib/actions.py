"""Approved writes: the agent prepares an exact action, the operator applies it.

prepare freezes the payload and its target and stores them with a hash. apply runs
in the operator's own terminal, refuses if the connection changed since (its
environment, resource limits, expected account or target settings) or is logged in
to another account, shows the same preview, asks for confirmation (the org name for
production or org-wide connections), runs the command once, and keeps a receipt:
applied, failed, or unknown when the outcome cannot be established.
"""
from fnmatch import fnmatchcase
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

from . import connectors, orgs
from .process import run as bounded
from .store import identifier, local_path, lock, now, private_dir, read_json, write_json

EXPIRY_HOURS = 24

# operation: provider, required params, optional params, (param, resource kind) bound by the connection's limits
WRITES = {
    "github.issue.create": ("github", ("repo", "title", "body"), ("labels",), ("repo", "repo")),
    "jira.issue.create": ("jira", ("project", "type", "summary"), ("description", "labels", "parent"), None),
    "gsc.sitemap.submit": ("gsc", ("site", "sitemap"), (), ("site", "site")),
}
PROPERTY = re.compile(r"sc-domain:[A-Za-z0-9.-]+|https?://[^\s/*?\[\]]+/[^\s*?\[\]]*")


def validate(operation, params):
    if operation not in WRITES:
        raise ValueError("unsupported write: " + operation + " (supported: " + ", ".join(sorted(WRITES)) + ")")
    provider, required, optional, _ = WRITES[operation]
    if not isinstance(params, dict) or set(params) - set(required) - set(optional):
        raise ValueError("unknown parameters for " + operation)
    for key in required:
        if not isinstance(params.get(key), str) or not params[key].strip():
            raise ValueError(operation + " requires " + key)
    if any(not isinstance(v, str) or "\x00" in v for v in params.values()):
        raise ValueError("parameters are text without NUL characters")
    if operation == "gsc.sitemap.submit":
        if not PROPERTY.fullmatch(params["site"]) or (connectors.site_parts(params["site"]) or ("host",))[0] == "host":
            raise ValueError("site is a Search Console property: sc-domain:example.com or https://www.example.com/")
        if (not re.fullmatch(r"https?://[^\s*?\[\]]{1,2040}", params["sitemap"]) or (connectors.site_parts(params["sitemap"]) or ("",))[0] != "url"
                or not connectors.site_allowed(params["sitemap"], params["site"])):
            raise ValueError("sitemap is the sitemap's full URL, inside the property " + params["site"])
        return provider
    if operation == "jira.issue.create":
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}", params["project"]):
            raise ValueError("project is a Jira project key such as ABC")
        if not re.fullmatch(r"[A-Za-z][\w -]{0,59}", params["type"]):
            raise ValueError("type is an issue type name such as Task, Bug or Story")
        if len(params["summary"]) > 255 or "\n" in params["summary"] or "\r" in params["summary"]:
            raise ValueError("summary is one line of at most 255 characters")
        if len(params.get("description", "")) > 30000:
            raise ValueError("description is at most 30000 characters")
        # Jira labels cannot contain spaces.
        if params.get("labels") and not re.fullmatch(r"[\w.:/-]+(,[\w.:/-]+)*", params["labels"]):
            raise ValueError("labels must be a comma-separated list of names without spaces")
        if params.get("parent") and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,49}-[0-9]+", params["parent"]):
            raise ValueError("parent is an issue key such as ABC-12")
        return provider
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", params["repo"]):
        raise ValueError("repo must be owner/name")
    if len(params["title"]) > 256 or len(params["body"]) > 65536:
        raise ValueError("title or body is too long")
    labels = params.get("labels", "")
    if labels and not re.fullmatch(r"[\w .:/-]+(,[\w .:/-]+)*", labels):
        raise ValueError("labels must be a comma-separated list of plain names")
    return provider


def target_text(operation, params):
    """A short name for what a write touches, for lists and briefs."""
    if operation == "jira.issue.create":
        return str(params.get("project")) + ": " + str(params.get("summary"))
    if operation == "gsc.sitemap.submit":
        return params.get("sitemap")
    return params.get("repo")


def marker(action_id, operation="github.issue.create"):
    """Left in the created issue so reconcile can prove which action made it (hidden on GitHub)."""
    if operation == "jira.issue.create":
        return "Reckon action: " + action_id
    return "<!-- reckon-action: " + action_id + " -->"


def argv(operation, params, action_id):
    """The exact command. A GitHub body travels on standard input, never in argv; jira-cli
    takes its description only as a flag, given as one --flag=value argument so text that
    starts with a dash stays a value."""
    if operation == "gsc.sitemap.submit":
        # Submitting is idempotent (an HTTP PUT); it asks Google to fetch the sitemap again.
        return ["gsc", "sitemaps", "submit", params["sitemap"], "--site=" + params["site"], "-o", "json"], None
    if operation == "jira.issue.create":
        text = params.get("description", "").rstrip("\n")
        # --allow-duplicate: jira-cli would otherwise reuse a recent issue with the same summary, without this
        # description or marker. Reckon already runs each action once.
        args = ["jira", "issue", "create", "--allow-duplicate", "--project=" + params["project"], "--type=" + params["type"],
                "--summary=" + params["summary"], "--description=" + (text + "\n\n" if text else "") + marker(action_id, operation)]
        args += ["--label=" + label for label in params.get("labels", "").split(",") if label]
        if params.get("parent"):
            args.append("--parent=" + params["parent"])
        return args + ["-o", "json"], None
    args = ["gh", "issue", "create", "--repo", params["repo"], "--title", params["title"], "--body-file", "-"]
    if params.get("labels"):
        args += ["--label", params["labels"]]
    return args, (params["body"].rstrip("\n") + "\n\n" + marker(action_id) + "\n").encode()


def browse(issue):
    """A Jira issue's web address, from the API address jira-cli prints with it (keeping a
    Server context path such as /jira)."""
    api = str(issue.get("self", ""))
    base = re.match(r"(https?://\S+?)/rest/api/", api) or re.match(r"(https?://[^/\s]+)", api)
    return base.group(1) + "/browse/" + issue["key"] if base and issue.get("key") else None


def plain(description):
    """A Jira description as text: wiki markup on Server, an ADF document on Cloud (one line per block)."""
    if isinstance(description, str):
        return description
    def walk(node, out):
        if isinstance(node, dict):
            out.append(str(node.get("text", "")) if node.get("type") == "text" else "\n" if node.get("type") == "hardBreak" else "")
            for child in node.get("content") if isinstance(node.get("content"), list) else []:
                walk(child, out)
    lines = []
    for block in description.get("content") if isinstance(description, dict) and isinstance(description.get("content"), list) else []:
        parts = []
        walk(block, parts)
        lines.append("".join(parts))
    return "\n".join(lines)


def last_line(text):
    """Proof sits on the last line: a note quoting a marker elsewhere does not count."""
    return str(text or "").rstrip().rsplit("\n", 1)[-1].strip()


def console(site):
    """Search Console's sitemaps page for a property."""
    from urllib.parse import quote
    return "https://search.google.com/search-console/sitemaps?resource_id=" + quote(site, safe="")


def link(operation, out, params=None):
    """The created item's address in a write's output."""
    if operation == "gsc.sitemap.submit":
        return console(params["site"]) if params else None
    if operation == "jira.issue.create":
        try:
            created = json.loads(out)
        except ValueError:
            return None
        return browse(created) if isinstance(created, dict) else None
    return next(iter(re.findall(r"https://\S+", out)), None)


def submitted(stamp, started):
    """Whether Google's lastSubmitted time is at or after the apply began."""
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= started


def bound(conn, operation, params):
    """(kind, target, allowed) when the write's target is outside the connection's resource limit, else None."""
    rule = WRITES[operation][3]
    allowed = conn.get("resources", {}).get(rule[1]) if rule else None
    if not allowed:
        return None
    value = params[rule[0]]
    test = connectors.site_allowed if rule[1] == "site" else fnmatchcase
    return None if any(test(value, p) for p in allowed) else (rule[1], value, allowed)


# Settings that choose where a write lands (GH_HOST, JIRA_URL); secrets are never part of the fingerprint.
TARGET_KEY = re.compile(r"HOST|URL|SERVER|SITE|DOMAIN|ENDPOINT|ORG|PROJECT|ACCOUNT|REGION|PROFILE|TENANT|WORKSPACE|EMAIL|USER", re.I)


def targeting(key):
    return bool(TARGET_KEY.search(key)) and not orgs.SECRET_KEY.search(key)


def target(conn, values):
    """Where a write would land, as a hash: the connection as configured (environment,
    resource limits, expected account) and its non-secret target settings, resolved
    (a keychain: or op:// reference counts by the value it gives today)."""
    settings = {k: v for k, v in values.items() if targeting(k)}
    return hashlib.sha256(json.dumps({"connection": conn, "settings": settings}, sort_keys=True).encode()).hexdigest()


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


def preview(record, live, full=False, account=None):
    lines = ["Action " + record["id"] + " (" + record["status"] + ")",
             "  " + record["operation"] + " on " + record["connection"] + (" as " + printable(account) if account else "") +
             " in " + record["org"] + "/" + record["environment"] + ("  [LIVE: production or org-wide account]" if live else "")]
    for key, value in sorted(record["params"].items()):
        # Approval shows every character; the agent-facing summary may abbreviate.
        text = printable(value if full or len(value) < 2000 else value[:2000] + "\n... (" + str(len(value)) + " characters)")
        lines.append("  " + key + ":" + ("\n    " + text.replace("\n", "\n    ") if "\n" in text else " " + text))
    return "\n".join(lines)


def is_live(ctx, conn):
    """Production environments and connections shared by every environment act on real data."""
    return ctx["production"] or "env" not in conn


def prepare(h, ctx, root, operation, params, registry, session=None):
    provider = validate(operation, params)
    cname, conn = orgs.resolve(ctx, provider)
    outside = bound(conn, operation, params)
    if outside:
        raise ValueError(outside[0] + " " + outside[1] + " is outside " + cname + " (allowed: " + ", ".join(outside[2]) + ")")
    action_id = datetime.now(timezone.utc).strftime("a-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    record = {"schema_version": 1, "id": action_id, "org": ctx["org"], "environment": ctx["env"], "connection": cname,
              "operation": operation, "params": params, "status": "prepared", "prepared_at": now(), "receipt": None,
              "target_sha256": target(conn, {k: orgs.reference(k, v) for k, v in
                                             orgs.secrets(h, ctx["org"], cname, registry[provider]).items() if targeting(k)})}
    if session:
        # The session's next review lists this write and its receipt.
        record["session"] = identifier(session)
    record["payload_sha256"] = digest(record)
    path = local_path(root, "actions", action_id)
    private_dir(path)
    write_json(path / "action.json", record)
    return record, preview(record, is_live(ctx, conn), account=conn.get("account"))


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
    return [{k: r.get(k) for k in ("id", "status", "operation", "connection", "environment", "prepared_at", "session")} for r in rows]


def connection(ctx, record):
    """The exact connection the action was prepared for, still serving this environment and target."""
    cname, conn = record["connection"], ctx["data"]["connections"].get(record["connection"])
    if record["org"] != ctx["org"] or record["environment"] != ctx["env"]:
        raise ValueError("action belongs to " + record["org"] + "/" + record["environment"] + "; this context is " + ctx["org"] + "/" + ctx["env"])
    if not conn or conn["provider"] != WRITES[record["operation"]][0] or not orgs.applies(conn, ctx["env"]):
        raise ValueError("connection " + cname + " changed or no longer serves " + ctx["env"] + "; prepare the action again")
    outside = bound(conn, record["operation"], record["params"])
    if outside:
        raise ValueError(outside[0] + " " + outside[1] + " is now outside " + cname + "; prepare the action again")
    return cname, conn


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
        prepared = datetime.fromisoformat(record["prepared_at"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - prepared).total_seconds() > EXPIRY_HOURS * 3600:
            record.update(status="expired", decided_at=now())
            write_json(path / "action.json", record)
            raise ValueError("action " + action_id + " was prepared over " + str(EXPIRY_HOURS) + " hours ago; prepare it again")
        if record["org"] != ctx["org"] or record["environment"] != ctx["env"]:
            raise ValueError("action belongs to " + record["org"] + "/" + record["environment"] + "; this context is " + ctx["org"] + "/" + ctx["env"])
        if record["payload_sha256"] != digest(record):
            raise ValueError("action " + action_id + " changed after it was prepared; prepare it again")
        validate(record["operation"], record["params"])
        cname, conn = connection(ctx, record)
        spec = registry[conn["provider"]]
        # Read the settings once: the fingerprint, the account check and the write all use these.
        values = {k: orgs.reference(k, v) for k, v in orgs.secrets(h, ctx["org"], cname, spec).items()}
        if record.get("target_sha256") != target(conn, values):
            raise ValueError("connection " + cname + " changed after action " + action_id + " was prepared (its environment, resource "
                             "limits, account or target settings); prepare it again")
        # One live read proves the connection is still logged in to the account it expects.
        account = connectors.whoami(h, ctx, cname, conn, registry, values)
        live = is_live(ctx, conn)
        print(preview(record, live, full=True, account=account), file=sys.stderr)
        expected = ctx["org"] if live else "yes"
        if confirm("Type " + expected + " to apply, anything else to cancel: ").strip() != expected:
            record.update(status="cancelled", decided_at=now())
            write_json(path / "action.json", record)
            return record
        env, values = orgs.child_env(h, ctx, cname, conn, spec, interactive=True, values=values)
        env.update(NO_COLOR="1", GH_PROMPT_DISABLED="1", JIRA_NO_INPUT="true")
        command, body = argv(record["operation"], record["params"], record["id"])
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
                                                                  "url": link(record["operation"], out, record["params"])})
        write_json(path / "action.json", record)
        return record


def reconcile(h, ctx, root, action_id, registry):
    """Find out whether an action with an unknown outcome landed, by reading the target.

    Only proof upgrades the receipt: an issue whose body carries this action's marker,
    or a sitemap Google records as submitted after the apply began. Not finding it is
    not proof of failure (search and listings lag), so the action stays unknown and
    must not be prepared again until someone checks by hand."""
    path, _ = load(root, action_id)
    with lock(path):
        _, record = load(root, action_id)
        if record["status"] not in ("unknown", "running"):
            raise ValueError("action " + action_id + " is " + record["status"] + "; only unknown outcomes need reconciling")
        cname, conn = connection(ctx, record)
        env, values = orgs.child_env(h, ctx, cname, conn, registry[conn["provider"]])
        params, operation = record["params"], record["operation"]
        tag = marker(action_id, operation)
        started = datetime.fromisoformat(record.get("approved_at", record["prepared_at"]).replace("Z", "+00:00"))
        if operation == "gsc.sitemap.submit":
            where = params["sitemap"]
            command = ["gsc", "sitemaps", "get", params["sitemap"], "--site=" + params["site"], "-o", "json"]
            # Only a submission at or after the apply began counts; an earlier one (even a minute before)
            # proves nothing, and a clock difference leaves the outcome unknown rather than wrong.
            find = (lambda data: [console(params["site"])] if isinstance(data, dict) and submitted(data.get("lastSubmitted"), started) else [])
        elif operation == "jira.issue.create":
            where = "project " + params["project"]
            # JQL reads dates in the user's time zone, so look back in whole days, not from a UTC time.
            days = int((datetime.now(timezone.utc) - started).total_seconds() // 86400) + 2
            command = ["jira", "issue", "search", "--jql", 'project = "' + params["project"] + '" AND creator = currentUser() AND created >= -' +
                       str(days) + "d ORDER BY created DESC", "--fields", "summary,description", "--limit", "100", "-o", "json"]
            find = (lambda items: [browse(i) for i in items if isinstance(i, dict) and isinstance(i.get("fields"), dict)
                                   and i["fields"].get("summary") == params["summary"] and last_line(plain(i["fields"].get("description"))) == tag]
                    if isinstance(items, list) else [])
        else:
            where = params["repo"]
            command = ["gh", "issue", "list", "--repo", params["repo"], "--state", "all", "--limit", "100",
                       "--json", "number,url,body,createdAt"]
            find = (lambda items: [i.get("url") for i in items if isinstance(i, dict) and last_line(i.get("body")) == tag]
                    if isinstance(items, list) else [])
        result = bounded(command, env=env, cwd=env["TMPDIR"], timeout=60, max_bytes=8 * 1048576)
        if result["status"] != "ok":
            raise ValueError("could not read " + where + " (" + result["status"] + "); the outcome stays unknown")
        try:
            found = find(json.loads(result["stdout"].decode() or "[]"))
        except ValueError:
            raise ValueError("could not read the listing of " + where + "; the outcome stays unknown")
        if found:
            record.update(status="applied", reconciled_at=now(),
                          receipt=dict(record.get("receipt") or {}, url=found[0], reconciled=True))
            write_json(path / "action.json", record)
            return record
        record.update(last_reconcile_at=now())
        write_json(path / "action.json", record)
        return dict(record, note="no proof that this action landed in " + where +
                    "; still unknown. Check it yourself before preparing the write again.")
