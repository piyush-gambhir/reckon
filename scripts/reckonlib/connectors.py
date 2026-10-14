"""Run one CLI for one connection: its credentials, its target, read-only forced."""
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

from . import orgs
from .process import run as bounded
from .store import local_path, lock, pick, read_json, write_json


def tokens(args, spec=None):
    """Leading subcommand words, up to the first flag, with command aliases resolved."""
    aliases = (spec or {}).get("aliases", {})
    words = []
    for arg in args:
        if arg.startswith("-"):
            break
        words.append(aliases.get(arg.lower(), arg.lower()))
    return words


def matches(pattern, words):
    return len(words) >= len(pattern) and all(fnmatchcase(w, p.lower()) for w, p in zip(words, pattern))


def long_name(arg, spec):
    """--flag[=value] as the CLI's parser reads it (mysql: --loose-x and --x_y mean --x / --x-y)."""
    name = arg.split("=", 1)[0]
    if spec.get("option_aliases"):
        name = "--" + name[2:].replace("_", "-")
        for prefix in ("loose-", "maximum-", "enable-", "disable-", "skip-"):
            if name.startswith("--" + prefix):
                name = "--" + name[2 + len(prefix):]
    return name


def expands(name, flag, spec):
    """Whether this parser reads name as flag: exactly, or as an abbreviation where it allows them."""
    return name == flag or bool(spec.get("abbreviations") and len(name) > 2 and flag.startswith(name))


def cluster(arg, spec):
    """Short flags set by one argument (-Asx), and the attached value of a value flag if any."""
    flags, takes_value = [], spec.get("short_values", "")
    for index, char in enumerate(arg[1:], 1):
        flags.append(char)
        if char in takes_value:
            return flags, arg[index + 1:] or None
    return flags, None


def flag_problem(arg, spec):
    """The denied flag an argument would set, in any spelling the CLI's parser accepts."""
    denied = spec.get("deny_args", [])
    if arg.startswith("--") and len(arg) > 2:
        name = long_name(arg, spec)
        for flag in denied:
            if flag.startswith("--") and expands(name, flag, spec):
                return flag
        for prefix in spec.get("deny_prefixes", []):
            if name.startswith(prefix):
                return prefix
    elif arg.startswith("-") and len(arg) > 1 and not arg.startswith("--"):
        shorts = {f[1] for f in denied if len(f) == 2 and f[1] != "-"}
        for char in cluster(arg, spec)[0]:
            if char in shorts:
                return "-" + char
    return None


def operands(args, spec, long_values=()):
    """(flag, value) pairs and positional words, following which argument each flag owns."""
    pairs, positional, index = [], [], 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--") and len(arg) > 2:
            name = long_name(arg, spec)
            owner = next((f for f in long_values if expands(name, f, spec)), None)
            if "=" in arg:
                pairs.append((owner or name, arg.split("=", 1)[1]))
            elif owner and index + 1 < len(args):
                pairs.append((owner, args[index + 1]))
                index += 1
        elif arg.startswith("-") and len(arg) > 1:
            flags, attached = cluster(arg, spec)
            if flags and flags[-1] in spec.get("short_values", ""):
                if attached is None and index + 1 < len(args):
                    attached = args[index + 1]
                    index += 1
                pairs.append(("-" + flags[-1], attached))
        else:
            positional.append(arg)
        index += 1
    return pairs, positional


def short_flags_set(args, spec):
    """Short flags actually set, skipping arguments that belong to a value flag (-t -C sets only -t)."""
    found, index = set(), 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-") and len(arg) > 1 and not arg.startswith("--"):
            flags, attached = cluster(arg, spec)
            found.update(flags)
            if flags and flags[-1] in spec.get("short_values", "") and attached is None:
                index += 1
        index += 1
    return found


def with_command_values(spec, args):
    """Value-taking short flags that only some subcommands define (jira issue search -j)."""
    extra = "".join(v for path, v in spec.get("command_short_values", {}).items() if matches(path.split(), tokens(args, spec)))
    return dict(spec, short_values=spec.get("short_values", "") + extra) if extra else spec


def check(provider, spec, args, label=None):
    """Refuse arguments that would change the target, credentials or safety mode."""
    spec = with_command_values(spec, args)
    if spec.get("mode") == "typed":
        raise ValueError(provider + " has no reckon run surface; use its typed operations (reckon capabilities)")
    for arg in args:
        if "\x00" in arg or len(arg) > 65536:
            raise ValueError("invalid argument")
        flag = flag_problem(arg, spec)
        if flag:
            raise ValueError(flag + " is fixed by the connection and cannot be passed through reckon run")
    if (spec.get("deny_commands") or spec.get("read_commands")) and args and args[0].startswith("-") and args not in (["--help"], ["-h"], ["--version"]):
        # Subcommand CLIs: the command comes first, so no flag can hide it from the checks below.
        raise ValueError("put the " + provider + " command before any flags, e.g. reckon run " + provider + " -- <command> <args> -o json")
    words = tokens(args, spec)
    allowed = spec.get("read_commands")
    if allowed and not any(matches(p, words) for p in allowed):
        raise ValueError(provider + " allows only these read commands through reckon run: " +
                         "; ".join(" ".join(p) for p in allowed) + " (put the command before any flags)")
    # Cobra resolves subcommands across flags (auth -o json token), so match on positional words too.
    aliases = spec.get("aliases", {})
    positional = [aliases.get(w.lower(), w.lower()) for w in operands(args, spec)[1]]
    for pattern in spec.get("deny_commands", []):
        if matches(pattern, words) or matches(pattern, positional):
            raise ValueError("'" + " ".join(pattern) + "' is not allowed through reckon run")
    required = spec.get("require_short")
    if required and not short_flags_set(args, spec) & set(required):
        # kcat picks producer mode by itself when stdin is not a terminal; insist on a read mode.
        raise ValueError(provider + " needs an explicit read mode: one of " + ", ".join("-" + c for c in required))
    if provider == "kubernetes":
        secrets_(args, spec)
    if provider == "postgres":
        conninfo(args, spec)
    if label:
        scope(spec, args, label, ())


KUBE_VALUES = ("--namespace", "--selector", "--output", "--sort-by", "--field-selector", "--label-columns", "--container",
               "--since", "--since-time", "--tail", "--template", "--chunk-size", "--request-timeout")


def secrets_(args, spec):
    """kubectl get/describe must not name the secrets resource, in any of its spellings."""
    words = tokens(args, spec)
    if not words or words[0] not in ("get", "describe"):
        return
    _, positional = operands(args, spec, KUBE_VALUES)
    for word in positional[1:]:
        for part in word.split(","):
            # secret, secrets, secret.v1., secrets.v1, secret/name: the resource is the part before the first dot.
            kind = part.split("/", 1)[0].lower().split(".", 1)[0]
            if kind in ("secret", "secrets"):
                raise ValueError("Kubernetes secrets are not readable through reckon run")


PSQL_VALUES = ("--command", "--dbname", "--file", "--set", "--variable", "--output", "--pset", "--table-attr",
               "--field-separator", "--record-separator", "--log-file")


def conninfo(args, spec):
    """psql reads a connection string wherever a database name goes; it would replace the connection."""
    pairs, positional = operands(args, spec, PSQL_VALUES)
    if len(positional) > 1:
        # psql [DBNAME [USERNAME]]: a second word would replace the connection's user.
        raise ValueError("psql takes at most a database name here; the user is fixed by the connection")
    names = positional + [v for f, v in pairs if f in ("-d", "--dbname") and v]
    for value in names:
        if "://" in value or "=" in value:
            raise ValueError("connection strings are fixed by the connection and cannot be passed through reckon run")


SELECTOR = re.compile(r'([A-Za-z_][\w.]*)\s*(=~|!~|!=|=|:)\s*"?([^",}\s)]*)"?')


def scope(spec, args, label, others):
    """Keep reads on a shared backend inside the active environment's label.

    Trace commands get --env added when missing. Metric and log queries must select
    the label with a positive selector; any selector naming another value, or
    excluding the label, is refused. This prevents mistakes; it is not a query
    parser, so keep queries simple."""
    if not label:
        return list(args)
    if "--" in args:
        # After --, cobra stops reading flags: an added --env would be ignored.
        raise ValueError("-- is not allowed on a shared backend; pass every flag before it")
    words = tokens(args, spec)
    family = spec.get("env_label_families", [])
    if words and words[0] in family and len(words) < 2 and args not in ([words[0]],):
        raise ValueError("put the full command first on this connection, e.g. " + words[0] + " query ... -o json")
    keys = spec.get("env_label_keys", [])
    flag = spec.get("env_label_flag")
    given = positive = False
    for index, arg in enumerate(args):
        value = args[index + 1] if index + 1 < len(args) else ""
        if flag and (arg == flag or arg.startswith(flag + "=")):
            given = True
            if (arg.split("=", 1)[1] if "=" in arg else value) != label:
                raise ValueError(flag + " must be " + label + " on this connection")
        if arg == "--tags" or arg.startswith("--tags="):
            # One key=value per --tags (a repeatable string array).
            key, _, tag_value = (arg.split("=", 1)[1] if arg.startswith("--tags=") else value).partition("=")
            if key.strip() in spec.get("env_label_tags", []):
                given = True
                if tag_value != label:
                    raise ValueError("--tags " + key + "=" + tag_value + " must use " + label + " on this connection")
        for key, op, selected in SELECTOR.findall(arg):
            if key not in keys:
                continue
            if selected != label or op in ("!=", "!~"):
                raise ValueError("selector " + key + op + selected + " reaches outside " + label + "; this connection is on " + label)
            positive = True
    if not given and any(matches(p, words) for p in spec.get("env_injected_commands", [])):
        return list(args) + [flag, label]
    if any(matches(p, words) for p in spec.get("env_scoped_commands", [])):
        if not positive:
            raise ValueError("this backend holds every environment: select " + label + " in the query, e.g. {env=\"" + label +
                             "\"} in PromQL or --stream '{env=\"" + label + "\"}' for logs")
        streams = [args[i + 1] if a == "--stream" and i + 1 < len(args) else a.split("=", 1)[1]
                   for i, a in enumerate(args) if a == "--stream" or a.startswith("--stream=")]
        # The CLI wraps the query in parentheses after the stream filter, except around pipes.
        streamed = any(any(k in keys and v == label for k, _, v in SELECTOR.findall(st)) for st in streams) and not any("|" in a for a in args)
        if any(re.search(r"(?i)(?<![\w-])(or|unless)(?![\w-])", a) for a in args) and not streamed:
            raise ValueError("or/unless can read past the " + label + " selector; for logs put it in --stream '{env=\"" + label +
                             "\"}', which applies to the whole query")
    return list(args)


HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


def ascii_host(host):
    """A host name in its ASCII (punycode) form, lower case, or None if it is not a plain host name.
    Python's codec is IDNA2003, which maps some names onto others (faß.de becomes fass.de), so a
    non-ASCII label counts only when it converts back to exactly itself."""
    labels = []
    for label in host.rstrip(".").lower().split("."):
        if not label.isascii():
            try:
                converted = label.encode("idna").decode("ascii")
                if converted.encode("ascii").decode("idna") != label:
                    return None
            except UnicodeError:
                return None
            label = converted
        labels.append(label)
    host = ".".join(labels)
    return host if HOST.fullmatch(host) else None


def site_parts(value):
    """(kind, host, prefix) of a Search Console or Bing target: a domain property
    (sc-domain:), a URL, or a bare host. None when it cannot be read safely: user
    info, a fragment, port 0, or anything but a plain host name, so text like
    https://evil.net#.acme.com/ is never taken for acme.com. Internationalized
    names compare in their punycode form."""
    from urllib.parse import urlsplit
    if value.startswith("sc-domain:"):
        host = ascii_host(value[len("sc-domain:"):])
        return ("domain", host, None) if host else None
    if "://" not in value:
        host = ascii_host(value)
        return ("host", host, None) if host else None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme, host = parts.scheme.lower(), ascii_host(parts.hostname or "")
    if scheme not in ("http", "https") or not host or port == 0 or "@" in parts.netloc or "#" in value:
        return None
    netloc = host + (":" + str(port) if port is not None and port != (443 if scheme == "https" else 80) else "")
    return ("url", host, scheme + "://" + netloc + (parts.path or "/") + ("?" + parts.query if parts.query else ""))


def site_allowed(value, pattern):
    """A Search Console or Bing target: the property itself, or a page URL inside it."""
    got = site_parts(value)
    if not got:
        return False
    if any(c in pattern for c in "*?["):
        # An operator's wildcard pattern matches the text, once the value is known to be a plain target.
        return fnmatchcase(value, pattern)
    allowed = site_parts(pattern)
    if not allowed:
        return False
    if allowed[0] == "domain":
        return got[1] == allowed[1] or got[1].endswith("." + allowed[1])
    if allowed[0] == "url":
        return got[0] == "url" and got[2].startswith(allowed[2])
    return got == allowed


def bind(spec, args, conn, values):
    """Keep a bound connection on its allowed resources (repositories, sites, namespaces, regions).

    Fails closed: every target source the CLI could use must be allowed, and commands
    whose target cannot be established (search, raw API, account-wide listings) are refused."""
    words = tokens(args, spec)
    for kind, allowed in conn.get("resources", {}).items():
        rule = spec["resources"][kind]
        for pattern in rule.get("refuse_commands", []):
            if matches(pattern, words):
                raise ValueError("'" + " ".join(pattern) + "' cannot be limited to " + kind + " " + ", ".join(allowed) + " on this connection")
        for text in rule.get("refuse_text", []):
            if any(text in arg for arg in args):
                raise ValueError("'" + text + "' can select another " + kind + "; name the " + kind + " with " + rule["flags"][0])
        for flag in rule.get("refuse_flags", []):
            if any(arg == flag or arg.startswith(flag + "=") or (len(flag) == 2 and flag[1] in cluster(arg, spec)[0])
                   for arg in args if arg.startswith("-") and (len(flag) == 2) == (not arg.startswith("--"))):
                raise ValueError(flag + " reaches beyond " + kind + " " + ", ".join(allowed))
        pairs, positional = operands(args, spec, tuple(f for f in rule["flags"] if f.startswith("--")))
        chosen = [part.strip() for f, v in pairs if f in rule["flags"] and v is not None for part in v.split(",")]
        defaults = [values[e] for e in rule.get("default_env", []) if values.get(e)]
        if rule.get("positional"):
            chosen += [p for p in positional if re.search(rule["positional"], p)]
        kinds = rule.get("refuse_kinds")
        if kinds:
            for word in positional[1:]:
                for part in word.split(","):
                    if part.split("/", 1)[0].lower().split(".", 1)[0] in kinds:
                        raise ValueError(part + " is cluster-wide and cannot be limited to " + kind + " " + ", ".join(allowed))
        if not chosen and not defaults:
            raise ValueError("this connection is limited to " + kind + " " + ", ".join(allowed) + "; name it with " + rule["flags"][0])
        test = site_allowed if kind == "site" else fnmatchcase
        # Defaults count too: the CLI may use them whatever the command line says.
        for value in chosen + defaults:
            if not any(test(value, pattern) for pattern in allowed):
                raise ValueError(kind + " " + value + " is outside this connection (allowed: " + ", ".join(allowed) + ")")


def extra_args(provider, values, config):
    """Connection details some database and queue clients only take as arguments."""
    if provider == "mysql":
        option_file = local_path(config, "mysql", "my.cnf")
        orgs.private_dir(option_file.parent)
        lines = ["[client]", "init-command=SET SESSION TRANSACTION READ ONLY"]
        lines += [key + "=" + values[var] for key, var in (("user", "MYSQL_USER"), ("database", "MYSQL_DATABASE")) if values.get(var)]
        orgs.write(option_file, "\n".join(lines) + "\n")
        return ["--defaults-extra-file=" + str(option_file)]
    if provider == "clickhouse":
        args = ["--host", values.get("CLICKHOUSE_HOST", ""), "--port", values.get("CLICKHOUSE_PORT", "9440"),
                "--user", values.get("CLICKHOUSE_USER", ""), "--password", values.get("CLICKHOUSE_PASSWORD", ""), "--readonly=1"]
        if values.get("CLICKHOUSE_SECURE", "1") != "0":
            args.append("--secure")
        if values.get("CLICKHOUSE_DATABASE"):
            args += ["--database", values["CLICKHOUSE_DATABASE"]]
        return args
    if provider == "kafka":
        args = ["-b", values.get("KAFKA_BOOTSTRAP_SERVERS", "")]
        for key, option in (("SECURITY_PROTOCOL", "security.protocol"), ("SASL_MECHANISM", "sasl.mechanism"),
                            ("SASL_USERNAME", "sasl.username"), ("SASL_PASSWORD", "sasl.password")):
            if values.get("KAFKA_" + key):
                args += ["-X", option + "=" + values["KAFKA_" + key]]
        return args
    if provider == "redis":
        return ["-u", values.get("REDIS_URL", "")]
    if provider == "mongodb":
        return [values.get("MONGODB_URI", "")]
    return []


def prepare(h, ctx, target, registry, interactive=False, values=None):
    cname, conn = orgs.resolve(ctx, target)
    spec = registry[conn["provider"]]
    env, values = orgs.child_env(h, ctx, cname, conn, spec, interactive=interactive, values=values)
    if conn["provider"] == "kafka-rpk":
        # rpk reads RPK_* settings; derive them from the shared KAFKA_* names.
        for target_key, source_key in (("RPK_BROKERS", "KAFKA_BOOTSTRAP_SERVERS"), ("RPK_USER", "KAFKA_SASL_USERNAME"),
                                       ("RPK_PASS", "KAFKA_SASL_PASSWORD"), ("RPK_SASL_MECHANISM", "KAFKA_SASL_MECHANISM")):
            if values.get(source_key) and not env.get(target_key):
                env[target_key] = values[source_key]
        if values.get("KAFKA_SECURITY_PROTOCOL") in ("SASL_SSL", "SSL"):
            env.setdefault("RPK_TLS_ENABLED", "true")
    if not shutil.which(spec["binary"], path=env.get("PATH")):
        raise ValueError(spec["binary"] + " is not installed or not on PATH (needed by " + cname + ")")
    return cname, conn, spec, env, values


def command(h, ctx, cname, conn, spec, values, args):
    config = local_path(orgs.org_path(h, ctx["org"]), "config", cname)
    argv = [spec["binary"]] + spec.get("subcommand", []) + extra_args(conn["provider"], values, config) + list(args)
    return argv


def cache_key(ctx, cname, argv, stdin):
    import hashlib
    material = json.dumps([ctx["org"], ctx["env"], cname, argv, (stdin or b"").decode("utf-8", "replace")])
    return hashlib.sha256(material.encode()).hexdigest()


def cached(h, ctx, key, max_age):
    """A successful identical read from the last max_age seconds, or None."""
    from datetime import datetime, timezone
    from .store import read_json
    path = local_path(orgs.org_path(h, ctx["org"]), "cache", key + ".json")
    if max_age <= 0 or not path.is_file():
        return None
    entry = read_json(path)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["observed_at"].replace("Z", "+00:00"))).total_seconds()
    return entry if 0 <= age <= max_age else None


def remember(h, ctx, key, result, values):
    from .store import now, write_json
    path = local_path(orgs.org_path(h, ctx["org"]), "cache", key + ".json")
    orgs.private_dir(path.parent)
    # Stored redacted, like evidence: no injected secret is ever written to disk.
    write_json(path, {"observed_at": now(), "exit_code": result["exit_code"], "duration_ms": result.get("duration_ms", 0),
                      "stdout": orgs.redact(result["stdout"].decode("utf-8", "replace"), values),
                      "stderr": orgs.redact(result["stderr"].decode("utf-8", "replace"), values)})


def resolved(spec, env, args):
    """Ask a CLI that implements the agent contract what these arguments would run.

    Returns None when the installed version has no resolver (the registry checks
    still apply); refuses anything that is not a plain read."""
    asked = bounded([spec["binary"]] + spec["resolver"] + list(args), env=env, cwd=env["TMPDIR"], timeout=10, max_bytes=262144)
    try:
        answer = json.loads(asked["stdout"].decode() or asked["stderr"].decode() or "{}")
    except ValueError:
        answer = {}
    text = asked["stdout"].decode(errors="replace") + asked["stderr"].decode(errors="replace")
    if asked["status"] != "ok":
        if "unknown command" in text and ("commands" in text or "resolve" in text):
            return None  # an older release without the resolver: the registry checks still apply
        raise ValueError(spec["binary"] + " could not resolve these arguments: " + str(answer.get("error", asked["status"])))
    if not isinstance(answer, dict) or "effect" not in answer:
        return None  # not a resolver answer (an older release printed something else)
    if answer.get("secret_output"):
        raise ValueError(answer.get("command", "this command") + " prints a credential and is not allowed through reckon run")
    if answer.get("effect") != "read":
        raise ValueError(answer.get("command", "this command") + " has effect " + str(answer.get("effect")) + "; reckon run only reads")
    return answer


def run(h, ctx, target, args, registry, timeout=60, max_bytes=8 * 1048576, stdin=None, cache_seconds=0):
    cname, conn, spec, env, values = prepare(h, ctx, target, registry)
    if not orgs.configured(h, ctx["org"], cname, spec, values):
        raise ValueError(cname + " has no credentials yet: edit " + str(orgs.secrets_path(h, ctx["org"], cname)) +
                         (" or run reckon login " + cname + " in your own terminal" if spec.get("login") else ""))
    label = conn.get("env_labels", {}).get(ctx["env"])
    check(conn["provider"], spec, args)
    if spec.get("resolver"):
        resolved(spec, env, args)
    bind(spec, args, conn, values)
    args = scope(spec, args, label, conn.get("env_labels", {}).values())
    argv = command(h, ctx, cname, conn, spec, values, args)
    key = cache_key(ctx, cname, argv, stdin)
    hit = cached(h, ctx, key, cache_seconds)
    if hit:
        result = {"status": "ok", "exit_code": hit["exit_code"], "duration_ms": hit["duration_ms"],
                  "stdout": hit["stdout"].encode(), "stderr": hit["stderr"].encode()}
        return {"connection": cname, "provider": conn["provider"], "argv": argv, "values": values, "result": result, "spec": spec,
                "cached_at": hit["observed_at"]}
    note = quota(h, ctx, cname, spec, args)
    # Some CLIs pick a target from the working directory (gh: the git remote); give them none.
    cwd = env["TMPDIR"] if spec.get("isolated_cwd") else os.getcwd()
    result = bounded(argv, env=env, cwd=cwd, timeout=timeout, max_bytes=max_bytes, stdin=stdin)
    if result["status"] == "ok":
        remember(h, ctx, key, result, values)
    return {"connection": cname, "provider": conn["provider"], "argv": argv, "values": values, "result": result, "spec": spec,
            "quota_note": note}


def counts(rule, words, args):
    """Whether one quota rule covers this call: its command (exactly, if marked) and its flag."""
    if not matches(rule["command"], words) or rule.get("exact") and len(words) != len(rule["command"]):
        return False
    return not rule.get("flag") or any(a == rule["flag"] or a.startswith(rule["flag"] + "=") for a in args)


def quota(h, ctx, cname, spec, args):
    """Spend one call of a daily API quota (Clarity: 10 export requests per project per
    UTC day), counted per connection so retries cannot burn it unseen. Refuses once it
    is used up; returns a warning when two or fewer remain."""
    rule = spec.get("quota")
    if not rule or not any(counts(c, tokens(args, spec), args) for c in rule["commands"]):
        return None
    folder = local_path(orgs.org_path(h, ctx["org"]), "runtime", cname)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with lock(folder):
            path = folder / "quota.json"
            data = read_json(path) if path.is_file() else {}
            used = data.get("used", 0) if data.get("day") == today else 0
            if used >= rule["per_day"]:
                raise ValueError(cname + " has used its " + str(rule["per_day"]) + " " + rule["unit"] + " for " + today +
                                 " (UTC); reuse saved evidence or a --cache result, or wait for 00:00 UTC")
            write_json(path, {"day": today, "used": used + 1, "per_day": rule["per_day"]})
    except BlockingIOError:
        raise ValueError("another run is counting " + cname + "'s quota right now; run one at a time")
    left = rule["per_day"] - used - 1
    return cname + ": " + str(left) + " of " + str(rule["per_day"]) + " " + rule["unit"] + " left today (UTC)" if left <= 2 else None


def probe(h, ctx, cname, conn, spec, env, values):
    """The provider's cheap live read (its verify command), in the connection's runtime folder."""
    argv = command(h, ctx, cname, conn, spec, values, spec["verify"])
    return bounded(argv, env=env, cwd=str(orgs.org_path(h, ctx["org"]) / "runtime" / cname / "tmp"), timeout=20, max_bytes=262144)


def account(spec, result):
    """The account the verify read reports at the provider's identity path, or None.
    The full value, for comparison only; show it through shown()."""
    if not spec.get("identity") or result["status"] != "ok":
        return None
    try:
        data = json.loads(result["stdout"].decode("utf-8", errors="replace"))
    except ValueError:
        return None
    # A list names fallbacks: Jira hides emailAddress for some users, so name or accountId comes next.
    for path in spec["identity"] if isinstance(spec["identity"], list) else [spec["identity"]]:
        found = pick(data, path)
        if not isinstance(found, bool) and isinstance(found, (str, int)) and str(found).strip():
            return str(found).strip()
    return None


def shown(seen, values):
    return None if seen is None else orgs.redact(seen, values)[:200]


def account_problem(cname, conn, seen, values):
    """Why the account a tool reports is not the one the connection expects, or None."""
    expected = conn.get("account")
    if not expected:
        return None
    if seen is None:
        return cname + " did not report which account it uses, so it cannot be confirmed as " + expected
    # Logins and emails are case-insensitive; a pattern such as *@acme.com allows any account of a domain.
    if not fnmatchcase(seen.casefold(), expected.casefold()):
        text = shown(seen, values)
        return cname + " is logged in as " + text + ", not " + expected + " (if " + text + " is right: reckon connect " + cname + " --account " + text + ")"
    return None


def whoami(h, ctx, cname, conn, registry, values=None):
    """Confirm a connection still uses its expected account, with one live read; raises otherwise.
    Pass the settings the guarded command will run with, so both see the same credentials."""
    spec = registry[conn["provider"]]
    if not conn.get("account"):
        return None
    if not spec.get("verify"):
        raise ValueError(cname + " has no live check, so its account cannot be confirmed as " + conn["account"])
    _, _, _, env, values = prepare(h, ctx, cname, registry, values=values)
    result = probe(h, ctx, cname, conn, spec, env, values)
    if result["status"] != "ok":
        raise ValueError("could not confirm " + cname + "'s account (its check read " + result["status"] + "); run reckon verify " + cname)
    seen = account(spec, result)
    problem = account_problem(cname, conn, seen, values)
    if problem:
        raise ValueError(problem)
    return shown(seen, values)


def verify(h, ctx, registry, names=()):
    """One cheap read per connection that applies to this environment, and the account it uses."""
    rows = []
    conns = ctx["data"]["connections"]
    for unknown in set(names) - set(conns):
        raise ValueError("unknown connection: " + unknown)
    for cname, conn in sorted(conns.items()):
        if names and cname not in names or not orgs.applies(conn, ctx["env"]):
            continue
        spec = registry[conn["provider"]]
        row = {"connection": cname, "provider": conn["provider"]}
        try:
            _, _, _, env, values = prepare(h, ctx, cname, registry)
        except ValueError as exc:
            rows.append(dict(row, status="skipped", detail=str(exc)))
            continue
        if not spec.get("verify"):
            rows.append(dict(row, status="skipped", detail="no live check defined"))
        elif not orgs.configured(h, ctx["org"], cname, spec, values):
            rows.append(dict(row, status="skipped", detail="no credentials"))
        else:
            try:
                quota(h, ctx, cname, spec, spec["verify"])
            except ValueError as exc:
                rows.append(dict(row, status="skipped", detail=str(exc)))
                continue
            result = probe(h, ctx, cname, conn, spec, env, values)
            detail = "" if result["status"] == "ok" else orgs.redact(result["stderr"].decode(errors="replace"), values).strip()[-400:]
            row.update(status=result["status"], detail=detail)
            # A pinned account is checked even where the tool reports none: unconfirmed is not ok.
            if result["status"] == "ok" and (spec.get("identity") or conn.get("account")):
                seen = account(spec, result)
                problem = account_problem(cname, conn, seen, values)
                row["account"] = text = shown(seen, values)
                if problem:
                    row.update(status="wrong_account", detail=problem)
                elif text and not conn.get("account"):
                    row["detail"] = "account " + text + " (pin it: reckon connect " + cname + " --account " + text + ")"
                elif text:
                    row["detail"] = "account " + text
            rows.append(row)
    return rows


def login(h, ctx, target, registry):
    """Interactive login for CLIs that keep their own tokens; for a person, not the agent."""
    if not sys.stdin.isatty():
        raise ValueError("reckon login is interactive; run it yourself in a terminal")
    cname, conn, spec, env, values = prepare(h, ctx, target, registry, interactive=True)
    if not spec.get("login"):
        raise ValueError(conn["provider"] + " has no login command; put its settings in " + str(orgs.secrets_path(h, ctx["org"], cname)))
    argv = command(h, ctx, cname, conn, spec, values, spec["login"])
    return cname, subprocess.call(argv, env=env, cwd=str(Path(env["TMPDIR"])))


def template(h, org, cname, spec):
    """Create a commented secrets file listing the provider's settings, if missing."""
    path = orgs.secrets_path(h, org, cname)
    if path.exists():
        return path, False
    orgs.private_dir(path.parent)
    lines = ["# Settings for the " + cname + " connection. One KEY=value per line; no shell syntax.",
             "# Only " + " / ".join(p + "*" for p in spec["prefixes"]) + " keys are accepted. This file is never shown to the agent.",
             "# A value may be a reference instead of the secret: keychain:<item> or op://vault/item/field."]
    if spec.get("login"):
        lines.append("# Or leave the keys empty and run: reckon login " + cname)
    for group in spec.get("required", []):
        if "/" in group:
            lines.append("# Set one of: " + group.replace("/", ", "))
        lines += [key + "=" for key in group.split("/")]
    for key in spec.get("optional", []):
        lines.append("# " + key + "=")
    orgs.write(path, "\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path, True
