"""Orgs, environments and connections kept under $RECKON_HOME.

Credentials live in one file per connection and are passed only to the child
process that needs them. They are never exported to the agent, written back,
or shown in output.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .store import local_path, now, private_dir, read_bytes, read_json, write, write_json

SCHEMA = 1
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
LABEL = re.compile(r"[A-Za-z0-9_.:/-]{1,80}")
# Some tools report a name with spaces (a Grafana org), so only edge spaces and control characters are refused.
ACCOUNT = re.compile(r"(?=\S)[^\x00-\x1f\x7f]{1,200}(?<=\S)")
LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)")
PRODUCTION_NAMES = ("prod", "production")
# Inherited from the operator's shell by every child; everything else is dropped.
BASE_KEYS = {"PATH", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
             "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
             "https_proxy", "http_proxy", "no_proxy"}
SECRET_KEY = re.compile(r"TOKEN|PASSWORD|SECRET|PASS$|_PWD$|API_KEY|APIKEY|COOKIE|CREDENTIALS|PRIVATE_KEY|_URI$", re.I)


def home():
    raw = os.environ.get("RECKON_HOME") or os.path.join(os.path.expanduser("~"), ".reckon")
    return Path(os.path.abspath(os.path.expanduser(raw)))


def code_root():
    return Path(__file__).resolve().parents[3]


def name(value, kind):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(kind + " names use lowercase letters, digits and hyphens (at most 40): " + repr(value))
    return value


def providers(root=None):
    data = read_json((root or code_root()) / "scripts" / "providers.json")
    if data.get("schema_version") != SCHEMA or not isinstance(data.get("providers"), dict):
        raise ValueError("scripts/providers.json needs schema_version 1 and a providers object")
    return data["providers"]


def org_path(h, org):
    return local_path(h, "orgs", name(org, "org"))


def workspace(h, org):
    return local_path(org_path(h, org), "workspace")


def validate(data, org, registry):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or data.get("id") != org:
        raise ValueError("org.json for " + org + " is missing fields or uses another schema")
    envs = data.get("environments")
    if not isinstance(envs, list) or not envs or len(set(envs)) != len(envs):
        raise ValueError(org + ": environments must be a non-empty list of unique names")
    for env in envs:
        name(env, "environment")
    if not isinstance(data.get("production"), list) or set(data["production"]) - set(envs):
        raise ValueError(org + ": production must list some of the org's environments")
    if not isinstance(data.get("connections"), dict):
        raise ValueError(org + ": connections must be an object")
    for cname, conn in data["connections"].items():
        name(cname, "connection")
        if not isinstance(conn, dict) or set(conn) - {"provider", "env", "env_labels", "resources", "account", "note"}:
            raise ValueError(org + "/" + cname + ": allowed fields are provider, env, env_labels, resources, account, note")
        if "account" in conn and not (isinstance(conn["account"], str) and ACCOUNT.fullmatch(conn["account"])):
            raise ValueError(org + "/" + cname + ": account is the login, email or account id the tool reports (a * pattern is allowed)")
        kinds = registry.get(conn.get("provider"), {}).get("resources", {})
        bound = conn.get("resources", {})
        if not isinstance(bound, dict) or set(bound) - set(kinds) or not all(
                isinstance(v, list) and v and all(isinstance(x, str) and x for x in v) for v in bound.values()):
            raise ValueError(org + "/" + cname + ": resources must map " + (", ".join(sorted(kinds)) or "nothing for this tool") +
                             " to non-empty lists of names or patterns")
        if conn.get("provider") not in registry:
            raise ValueError(org + "/" + cname + ": unknown provider " + repr(conn.get("provider")))
        if "env" in conn and "env_labels" in conn:
            raise ValueError(org + "/" + cname + ": use env for one environment or env_labels for a shared one, not both")
        if "env" in conn and conn["env"] not in envs:
            raise ValueError(org + "/" + cname + ": environment " + repr(conn["env"]) + " is not one of " + ", ".join(envs))
        labels = conn.get("env_labels", {})
        if not isinstance(labels, dict) or set(labels) - set(envs) or not all(isinstance(v, str) and LABEL.fullmatch(v) for v in labels.values()):
            raise ValueError(org + "/" + cname + ": env_labels must map the org's environments to plain labels")
    return data


def budget_problem(data):
    """Why the org's agent token budget is invalid, or None. Checked on save and when
    read, never on load, so a bad value cannot lock the org (or its repair) out."""
    limit = data.get("agent_token_budget")
    if limit is None or (isinstance(limit, dict) and set(limit) == {"tokens", "days"}
                         and all(isinstance(v, int) and not isinstance(v, bool) for v in limit.values())
                         and limit["tokens"] >= 1 and 1 <= limit["days"] <= 365):
        return None
    return data.get("id", "org") + ": agent_token_budget needs whole numbers tokens >= 1 and days 1-365 (reckon spend --set-budget 0 clears it)"


def load(h, org, registry=None):
    path = local_path(org_path(h, org), "org.json")
    if not path.is_file():
        raise ValueError("unknown org: " + org + " (create it with: reckon org add " + org + ")")
    return validate(read_json(path), org, registry or providers())


def save(h, data, registry=None):
    validate(data, data.get("id"), registry or providers())
    if budget_problem(data):
        raise ValueError(budget_problem(data))
    write_json(local_path(org_path(h, data["id"]), "org.json"), data)


def orgs(h):
    base = local_path(h, "orgs")
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if NAME.fullmatch(p.name) and (p / "org.json").is_file())


def create(h, org, display=None, envs=("prod", "uat", "dev"), production=None, registry=None):
    path = org_path(h, org)
    if (path / "org.json").exists():
        raise ValueError("org already exists: " + org)
    envs = list(envs)
    if production is None:
        production = [env for env in envs if env in PRODUCTION_NAMES]
    data = {"schema_version": SCHEMA, "id": org, "name": display or org, "environments": envs,
            "production": list(production), "connections": {}, "created_at": now()}
    validate(data, org, registry or providers())
    for directory in ("secrets", "config", "runtime", "workspace"):
        private_dir(local_path(path, directory))
    save(h, data, registry)
    return data


def context(h, org=None, env=None, registry=None, cwd=None):
    """Resolve the org and environment: arguments, then the agent's pinned
    RECKON_ORG/RECKON_ENV, then the link of the folder the command runs in, then
    the saved default. Missing or unknown values fail closed."""
    pinned = (os.environ.get("RECKON_ORG"), os.environ.get("RECKON_ENV"))
    # reckon open pins an agent to one org and environment; it cannot step outside them.
    if pinned[0] and org and org != pinned[0] or pinned[1] and env and env != pinned[1]:
        raise ValueError("this agent is pinned to " + str(pinned[0]) + "/" + str(pinned[1]) +
                         "; another org or environment needs its own agent (reckon open <org>/<env>)")
    saved_path = local_path(h, "context")
    saved = read_bytes(saved_path, 4096).decode().strip().partition("/")[::2] if saved_path.is_file() else (None, None)
    # An agent started by reckon open follows its pin, never the folder it wanders into.
    link = None if "RECKON_ORG" in os.environ or "RECKON_ENV" in os.environ else linked(h, cwd)
    source = "arguments"
    if not org:
        if pinned[0]:
            org, source = pinned[0], "RECKON_ORG/RECKON_ENV"
        elif link:
            org, source = link["org"], "folder link " + link["path"]
        else:
            org, source = saved[0], str(saved_path)
    # A link is a default, not a lock: it applies only when the chosen org is the linked one.
    link = link if link and link["org"] == org else None
    if not env:
        # An org named explicitly keeps the environment pinned, linked or saved for that same org.
        env = pinned[1] if pinned[0] == org else link["env"] if link else saved[1] if saved[0] == org else None
    if not org:
        raise ValueError("no org selected: run reckon use <org>/<env> (reckon org list shows your orgs)")
    data = load(h, org, registry)
    if not env:
        raise ValueError("no environment selected for " + org + "; choose one of: " + ", ".join(data["environments"]))
    if env not in data["environments"]:
        raise ValueError(org + " has no environment " + repr(env) + "; choose one of: " + ", ".join(data["environments"]))
    # An agent started in one org's folder must not act for another org.
    here = Path(os.path.abspath(cwd or os.getcwd()))
    base = Path(os.path.abspath(h / "orgs"))
    if base in here.parents and len(here.relative_to(base).parts) >= 2 and here.relative_to(base).parts[1] == "workspace":
        owner = here.relative_to(base).parts[0]
        if owner != org:
            raise ValueError("this folder belongs to org " + owner + ", but the selected org is " + org)
    return {"org": org, "env": env, "production": env in data["production"], "source": source, "data": data, "link": link}


def set_context(h, org, env, registry=None):
    data = load(h, org, registry)
    if env not in data["environments"]:
        raise ValueError(org + " has no environment " + repr(env) + "; choose one of: " + ", ".join(data["environments"]))
    private_dir(h)
    write(local_path(h, "context"), org + "/" + env + "\n")


def folder(path):
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def links(h):
    """Folder links from $RECKON_HOME/links.json: {folder: {org, env, use?}}. A
    malformed file is an error, never read as "no links"."""
    path = local_path(h, "links.json")
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except ValueError:
        data = None
    entries = data.get("links") if isinstance(data, dict) and data.get("schema_version") == SCHEMA else None
    if not isinstance(entries, dict) or not all(
            isinstance(v, dict) and not set(v) - {"org", "env", "use"} and isinstance(v.get("org"), str) and NAME.fullmatch(v["org"])
            and isinstance(v.get("env"), str) and NAME.fullmatch(v["env"])
            and isinstance(v.get("use", []), list) and all(isinstance(c, str) and NAME.fullmatch(c) for c in v.get("use", []))
            for v in entries.values()):
        raise ValueError(str(path) + " is malformed; fix or delete it")
    return entries


def linked(h, cwd=None):
    """The deepest link covering a folder, with its path, or None. Folders under
    $RECKON_HOME belong to their org and never follow a link."""
    here, root = folder(cwd or os.getcwd()), folder(h)
    if here == root or root in here.parents:
        return None
    entries = links(h)
    found = [Path(p) for p in entries if Path(p) == here or Path(p) in here.parents]
    if not found:
        return None
    best = max(found, key=lambda p: len(p.parts))
    return dict(entries[str(best)], path=str(best))


def link(h, path, org, env, use=(), registry=None):
    """Make a folder and everything below it default to an org and environment, and
    optionally prefer connections for their tools. Replaces a link at the same folder."""
    where, root = folder(path), folder(h)
    if not where.is_dir():
        raise ValueError("not a folder: " + str(where))
    if where == root or root in where.parents:
        raise ValueError("folders under " + str(h) + " already belong to their org")
    data = load(h, org, registry)
    if env not in data["environments"]:
        raise ValueError(org + " has no environment " + repr(env) + "; choose one of: " + ", ".join(data["environments"]))
    conns, use = data["connections"], sorted(set(use))
    for cname in use:
        if cname not in conns:
            raise ValueError(org + " has no connection " + cname + " (reckon status --org " + org + " lists them)")
    for provider in sorted({conns[c]["provider"] for c in use}):
        # reckon run <name> means the connection of that name first, so the preference would never apply.
        if provider in conns and provider not in use:
            raise ValueError(org + " has a connection named " + provider + ", so reckon run " + provider + " always means it; prefer it or rename it")
        # One preferred connection per tool and environment, or the preference would still be a guess.
        for each in data["environments"]:
            fit = [c for c in use if conns[c]["provider"] == provider and applies(conns[c], each)]
            if len(fit) > 1:
                raise ValueError(", ".join(fit) + " all serve " + org + "/" + each + "; prefer one " + provider + " connection per environment")
    entries = links(h)
    entry = dict({"org": org, "env": env}, **({"use": use} if use else {}))
    replaced = entries.get(str(where))
    entries[str(where)] = entry
    write_json(local_path(h, "links.json"), {"schema_version": SCHEMA, "links": entries})
    return dict(entry, path=str(where), replaced=replaced)


def unlink(h, path):
    where, entries = folder(path), links(h)
    if str(where) not in entries:
        covering = linked(h, where)
        raise ValueError("no link at " + str(where) + ("; it follows the link at " + covering["path"] + ", unlink that folder instead" if covering else ""))
    entry = entries.pop(str(where))
    write_json(local_path(h, "links.json"), {"schema_version": SCHEMA, "links": entries})
    return dict(entry, path=str(where))


def applies(conn, env):
    """A labelled shared connection serves only the environments it has a label for."""
    if "env_labels" in conn:
        return env in conn["env_labels"]
    return conn.get("env") in (None, env)


def resolve(ctx, target):
    """A connection name, the connection the folder link prefers for a provider, or a
    provider with exactly one connection for this environment. Saved work that must
    not depend on the folder it runs in clears ctx["link"] first."""
    conns = ctx["data"]["connections"]
    link = ctx.get("link") or {}
    use = link.get("use", [])
    # Checked before any name: a replacement named like the tool must not stand in for a removed preference.
    missing = [c for c in use if c not in conns]
    if missing:
        raise ValueError("the folder link at " + link["path"] + " prefers " + ", ".join(missing) + ", which " + ctx["org"] +
                         " no longer has; link the folder again (reckon link)")
    preferred = [c for c in use if conns[c]["provider"] == target]
    if target in conns:
        if preferred and target not in preferred:
            raise ValueError(target + " names a connection, but the folder link at " + link["path"] + " prefers " + ", ".join(preferred) +
                             " for that tool; name the connection you mean")
        if not applies(conns[target], ctx["env"]):
            if "env_labels" in conns[target]:
                raise ValueError("connection " + target + " has no label for " + ctx["env"] + "; add one: reckon connect " + target +
                                 " --label " + ctx["env"] + "=<label>")
            raise ValueError("connection " + target + " belongs to " + conns[target]["env"] + "; this context is " + ctx["org"] + "/" + ctx["env"])
        return target, conns[target]
    if preferred:
        fit = [c for c in preferred if applies(conns[c], ctx["env"])]
        if len(fit) == 1:
            return fit[0], conns[fit[0]]
        raise ValueError("the folder link at " + link["path"] + " prefers " + ", ".join(preferred) + " for " + target + ", and " +
                         ("none of them serves " if not fit else "several serve ") + ctx["env"] + "; name a connection or link the folder again")
    matches = [(n, c) for n, c in sorted(conns.items()) if c["provider"] == target and applies(c, ctx["env"])]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(ctx["org"] + " has no connection named or providing " + repr(target) + " for " + ctx["env"] +
                         " (reckon status lists connections; reckon connect adds one)")
    raise ValueError("several " + target + " connections fit " + ctx["env"] + ": " + ", ".join(n for n, _ in matches) + "; name one")


def secrets_path(h, org, conn):
    return local_path(org_path(h, org), "secrets", name(conn, "connection") + ".env")


def secrets(h, org, conn, spec):
    """Parse KEY=value lines without shell evaluation. Messages never echo values."""
    path = secrets_path(h, org, conn)
    if not path.exists():
        return {}
    if os.name != "nt" and path.lstat().st_mode & 0o077:
        raise ValueError(str(path) + " is readable by other users; run: chmod 600 " + str(path))
    values = {}
    for number, raw in enumerate(read_bytes(path, 65536).decode("utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError(path.name + " line " + str(number) + ": expected KEY=value")
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not key.startswith(tuple(spec["prefixes"])):
            raise ValueError(path.name + " line " + str(number) + ": " + key + " is not a setting for this provider (expected " +
                             " or ".join(p + "*" for p in spec["prefixes"]) + ")")
        values[key] = value
    return values


def reference(key, value):
    """A value may name a secret kept elsewhere: keychain:<item> (macOS Keychain or
    Linux secret-tool) or op://vault/item/field (1Password). Read only when a run needs it."""
    if value.startswith("keychain:"):
        item = value[9:].strip()
        if not item:
            raise ValueError(key + ": keychain: needs an item name")
        argv = (["security", "find-generic-password", "-s", item, "-w"] if sys.platform == "darwin"
                else ["secret-tool", "lookup", "service", item])
    elif value.startswith("op://"):
        argv = ["op", "read", "--no-newline", value]
    else:
        return value
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(key + ": could not read its secret reference with " + argv[0] + " (" + type(exc).__name__ + ")")
    secret = done.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
    if done.returncode != 0 or not secret:
        raise ValueError(key + ": " + argv[0] + " found nothing for " + value)
    return secret


def placeholder(value):
    return not value or bool(re.search(r"replace_me|example\.com|\.example\.|@example|xxxx|changeme|your-|^<.*>$", value, re.I))


def configured(h, org, conn, spec, values=None):
    """Local configuration only: required settings present, or a saved CLI profile."""
    values = secrets(h, org, conn, spec) if values is None else values
    if spec.get("required") and all(any(not placeholder(values.get(k, "")) for k in group.split("/")) for group in spec["required"]):
        return True
    config = local_path(org_path(h, org), "config", conn)
    return any((config / p).is_file() and (config / p).stat().st_size > 0 for p in spec.get("config_files", []))


def child_env(h, ctx, conn_name, conn, spec, interactive=False, values=None):
    """A fresh environment for one CLI: its secrets, private config/home/cache, safety switches.
    values: settings already read and resolved, so a check and the run it guards use the same ones."""
    org_dir = org_path(h, ctx["org"])
    if values is None:
        values = {k: reference(k, v) for k, v in secrets(h, ctx["org"], conn_name, spec).items()}
    env = {k: v for k, v in os.environ.items() if k in BASE_KEYS}
    env.update(values)
    config = local_path(org_dir, "config", conn_name)
    private_dir(config)
    env["XDG_CONFIG_HOME"] = str(config)
    runtime = local_path(org_dir, "runtime", conn_name)
    for directory, keys in (("home", ("HOME", "USERPROFILE")), ("cache", ("XDG_CACHE_HOME", "LOCALAPPDATA")),
                            ("state", ("XDG_STATE_HOME", "APPDATA")), ("tmp", ("TMPDIR", "TMP", "TEMP"))):
        target = local_path(runtime, directory)
        private_dir(target)
        for key in keys:
            env[key] = str(target)
    if spec.get("real_home") and os.environ.get("HOME"):
        # These CLIs keep OAuth tokens in the OS keychain, which macOS finds through
        # the real home. Their config (and keychain entries) stay per connection.
        env["HOME"] = os.environ["HOME"]
    for key, relative in spec.get("paths", {}).items():
        env[key] = str(local_path(config, relative))
    env.update(spec.get("defaults", {}))
    env.update(spec.get("always", {}))
    if not interactive:
        env.update(spec.get("read_only", {}))
        env.update(NO_COLOR="1", CI="1", PAGER="cat", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0")
    label = conn.get("env_labels", {}).get(ctx["env"])
    if label and spec.get("env_label_var"):
        env[spec["env_label_var"]] = label
    env.update(RECKON_HOME=str(h), RECKON_ORG=ctx["org"], RECKON_ENV=ctx["env"], RECKON_CONNECTION=conn_name)
    if label:
        env["RECKON_ENV_LABEL"] = label
    return env, values


def redact(text, values):
    secrets_ = {v for k, v in values.items() if len(v) >= 4 and SECRET_KEY.search(k)}
    for secret in sorted(secrets_, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    # user:password@ and the password-only :password@ form (redis://:secret@host).
    text = re.sub(r"(://)[^\s/@:]*:[^\s/@]+@", r"\1[REDACTED]@", text)
    return re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)


def describe(h, ctx, registry):
    """Connections visible in this context with local readiness; no network."""
    rows = []
    for cname, conn in sorted(ctx["data"]["connections"].items()):
        spec = registry[conn["provider"]]
        scope = conn.get("env") or (", ".join(sorted(conn["env_labels"])) if "env_labels" in conn else "all environments")
        row = {"connection": cname, "provider": conn["provider"], "scope": scope,
               "applies": applies(conn, ctx["env"]), "label": conn.get("env_labels", {}).get(ctx["env"]), "account": conn.get("account")}
        try:
            row["installed"] = bool(shutil.which(spec["binary"]))
            row["configured"] = configured(h, ctx["org"], cname, spec)
            row["state"] = "ready" if row["installed"] and row["configured"] else "no_cli" if not row["installed"] else "no_credentials"
        except ValueError as exc:
            row.update(installed=False, configured=False, state="invalid: " + str(exc))
        rows.append(row)
    return rows


def dump(value):
    return json.dumps(value, indent=2, ensure_ascii=False)
