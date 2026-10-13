"""Copy a pre-org checkout's single-business layout into one org under $RECKON_HOME.

Nothing in the source is changed or deleted. Credential files are parsed as
literal KEY=value lines; anything that needs shell expansion is reported by
name and left for the operator to copy by hand.
"""
import hashlib
import os
from pathlib import Path
import shutil

from . import orgs
from .store import local_path, private_dir, read_bytes, read_json, write, write_json

LEGACY_ENVS = ("production", "staging", "uat")
# Names the old .envrc translated before the CLIs saw them.
ALIASES = {"CUBEAPM_HOST": "CUBEAPM_SERVER", "JENKINS_USERNAME": "JENKINS_USER"}


def parse(path):
    values, skipped = {}, []
    if not path.is_file():
        return values, skipped
    for number, raw in enumerate(read_bytes(path, 1048576).decode("utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = orgs.LINE.fullmatch(line)
        if not match:
            skipped.append(path.name + " line " + str(number) + " (not KEY=value)")
            continue
        key, value = match.group(1), match.group(2).strip()
        # The old loader sourced these files with a shell: single quotes are literal,
        # anything else may expand.
        quote = value[0] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else ""
        if quote:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if quote != "'" and ("$" in value or "`" in value or "\\" in value):
            skipped.append(key + " in " + path.name + " (uses shell expansion; copy it by hand)")
            continue
        values[key] = value
    return values, skipped


def literal(value):
    """Quote a value so orgs.secrets reads back exactly these characters."""
    if value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""):
        return "'" + value + "'"
    return value


def config_dirs(spec):
    return sorted({Path(p).parts[0] for p in spec.get("config_files", []) + list(spec.get("paths", {}).values())})


def digest(directory):
    if not directory.is_dir():
        return None
    hasher = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()):
        hasher.update(str(path.relative_to(directory)).encode() + b"\0" + path.read_bytes() + b"\0")
    return hasher.hexdigest()


def plan(source, registry, share=()):
    envs = [e for e in LEGACY_ENVS if (source / (".env." + e)).is_file() or (source / ".config" / e).is_dir()
            or (source / "infra-knowledge" / e).is_dir()]
    if not envs:
        raise ValueError("no pre-org layout found in " + str(source) + " (.env.<env>, .config/<env> or infra-knowledge/<env>)")
    common, skipped = parse(source / ".env.common")
    settings, present = {}, {}
    for env in envs:
        values = dict(common)
        for suffix in ("", ".local"):
            more, notes = parse(source / (".env." + env + suffix))
            values.update(more)
            skipped += notes
        for old, new in ALIASES.items():
            if old in values:
                value = values.pop(old)
                values.setdefault(new, value)
        settings[env] = values
        for provider, spec in registry.items():
            has_keys = any(any(values.get(k) for k in group.split("/")) for group in spec.get("required", []))
            has_config = any((source / ".config" / env / p).is_file() for p in spec.get("config_files", []) + list(spec.get("paths", {}).values()))
            if has_keys or has_config:
                present.setdefault(provider, []).append(env)
    claimed = set()
    connections = {}
    for provider, where in sorted(present.items()):
        spec = registry[provider]
        per_env = {e: {k: v for k, v in settings[e].items() if k.startswith(tuple(spec["prefixes"]))} for e in where}
        claimed |= {k for values in per_env.values() for k in values}
        configs = {e: tuple(digest(source / ".config" / e / d) for d in config_dirs(spec)) for e in where}
        same = all(per_env[e] == per_env[where[0]] and configs[e] == configs[where[0]] for e in where)
        if provider in share and not same:
            raise ValueError(provider + " has different settings per environment; it cannot become one shared connection")
        shared = provider in share or (where == envs and len(envs) > 1 and same)
        for env in ([where[0]] if shared else where):
            cname = provider if shared else provider + "-" + env
            connections[cname] = {"provider": provider, "env": None if shared else env, "values": per_env[env],
                                  "config": [d for d in config_dirs(spec) if (source / ".config" / env / d).is_dir()], "from_env": env}
    for env in envs:
        for key in sorted(set(settings[env]) - claimed):
            if key not in ("PATH",):
                skipped.append(key + " (no matching tool)")
    return {"environments": envs, "connections": connections, "skipped": sorted(set(skipped)),
            "knowledge": (source / "infra-knowledge").is_dir(),
            "sessions": sorted(p.parent.name for p in (source / "sessions").glob("*/session.json")),
            "incidents": sorted(p.name for p in (source / "incidents").iterdir() if p.is_dir() and not p.name.startswith(".")) if (source / "incidents").is_dir() else []}


def migrate(h, org, source, registry, display=None, dry_run=False, rename=None, share=()):
    """rename maps old environment names to new ones (production=prod); share
    turns a provider into one connection for every environment."""
    source = Path(os.path.abspath(source))
    for provider in share:
        if provider not in registry:
            raise ValueError("unknown provider for --share: " + provider)
    steps = plan(source, registry, tuple(share))
    names = {e: (rename or {}).get(e, e) for e in steps["environments"]}
    for old, new in (rename or {}).items():
        if old not in names:
            raise ValueError("--rename " + old + "=" + new + ": the old checkout has no " + old + " environment")
        orgs.name(new, "environment")
    if len(set(names.values())) != len(names):
        raise ValueError("--rename would merge two environments")
    connections = {}
    for cname, conn in steps["connections"].items():
        if conn["env"] is not None:
            cname = conn["provider"] + "-" + names[conn["env"]]
        connections[cname] = dict(conn, env=names[conn["env"]] if conn["env"] else None)
    steps["connections"] = connections
    summary = {"org": org, "source": str(source), "environments": [names[e] for e in steps["environments"]],
               "connections": {n: {"provider": c["provider"], "environment": c["env"] or "all", "settings": sorted(c["values"]), "config": c["config"]}
                               for n, c in steps["connections"].items()},
               "skipped": steps["skipped"], "sessions": len(steps["sessions"]), "incidents": len(steps["incidents"]),
               "knowledge": steps["knowledge"], "dry_run": dry_run,
               # Their logins live in the OS keychain under the old config path, which does not move.
               "login_again": sorted(n for n, c in steps["connections"].items() if c["config"] and registry[c["provider"]].get("real_home"))}
    if dry_run:
        return summary
    production = [names[e] for e in steps["environments"] if e == "production"]
    if orgs.org_path(h, org).exists():
        # Rollback deletes the destination, so it must be entirely new.
        raise ValueError("org already exists (or its folder does): " + org)
    try:
        data = orgs.create(h, org, display, [names[e] for e in steps["environments"]], production, registry)
        copy(h, org, data, source, steps, names, registry)
    except BaseException:
        # The org did not exist before this call; leave nothing half-copied behind.
        shutil.rmtree(orgs.org_path(h, org), ignore_errors=True)
        raise
    return summary


def copy(h, org, data, source, steps, names, registry):
    org_dir = orgs.org_path(h, org)
    for cname, conn in steps["connections"].items():
        data["connections"][cname] = {"provider": conn["provider"]} if conn["env"] is None else {"provider": conn["provider"], "env": conn["env"]}
        path = orgs.secrets_path(h, org, cname)
        write(path, "# Migrated from " + str(source) + " (" + conn["from_env"] + ")\n" + "".join(k + "=" + literal(v) + "\n" for k, v in sorted(conn["values"].items())))
        os.chmod(path, 0o600)
        for directory in conn["config"]:
            target = local_path(org_dir, "config", cname, directory)
            private_dir(target.parent)
            shutil.copytree(source / ".config" / conn["from_env"] / directory, target, symlinks=True)
    orgs.save(h, data, registry)
    root = orgs.workspace(h, org)
    if steps["knowledge"]:
        for entry in sorted((source / "infra-knowledge").iterdir()):
            target = local_path(root, "knowledge", names.get(entry.name, entry.name))
            if entry.is_dir() and not entry.is_symlink():
                shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
            elif entry.is_file():
                shutil.copyfile(entry, target)
    for name in steps["sessions"]:
        target = local_path(root, "sessions", name)
        private_dir(target.parent)
        shutil.copytree(source / "sessions" / name, target, symlinks=True, ignore=shutil.ignore_patterns(".lock"))
        session = read_json(target / "session.json")
        session.update(org=org, environment=names.get(session.get("environment"), session.get("environment")))
        write_json(target / "session.json", session)
        for record_path in sorted((target / "evidence").glob("*/record.json")):
            record = read_json(record_path)
            record.update(org=org, environment=session["environment"])
            write_json(record_path, record)
    for name in steps["incidents"]:
        target = local_path(root, "incidents", name)
        private_dir(target.parent)
        shutil.copytree(source / "incidents" / name, target, symlinks=True)
