"""Versioned local sessions and atomic evidence; no external storage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

SCHEMA = 1


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("times must be ISO 8601 with an explicit timezone") from None
    if parsed.tzinfo is None:
        raise ValueError("times need an explicit timezone (Z or +HH:MM)")
    return parsed.astimezone(timezone.utc)


def window(start, end):
    a, b = instant(start), instant(end)
    if not 0 < (b - a).total_seconds() <= 31 * 86400:
        raise ValueError("window must be positive and at most 31 days")
    return {"from": a.isoformat().replace("+00:00", "Z"), "to": b.isoformat().replace("+00:00", "Z")}


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", value) or ".." in value:
        raise ValueError("invalid identifier")
    return value


def private_dir(path):
    if path.is_symlink():
        raise ValueError("local state directories must not be symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def write(path, text):
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_json(path):
    if path.stat().st_size > 8 * 1048576:
        raise ValueError("metadata file exceeds 8 MiB")
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def lock(path):
    """OS advisory lock releases automatically on interruption/process death."""
    private_dir(path)
    with open(path / ".lock", "a+b") as stream:
        os.chmod(path / ".lock", 0o600)
        if os.name == "nt":
            import msvcrt
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def session_path(root, session_id):
    base = root / "sessions"
    path = base / identifier(session_id)
    if base.is_symlink() or path.is_symlink():
        raise ValueError("sessions must not be symlinks")
    if not (path / "session.json").is_file():
        raise ValueError("unknown session: " + session_id)
    data = read_json(path / "session.json")
    if data.get("schema_version") != SCHEMA or data.get("id") != session_id:
        raise ValueError("unsupported or inconsistent session metadata")
    return path, data


def services(root, env):
    path = root / "infra-knowledge" / env / "services.json"
    if not path.exists():
        return []
    data = read_json(path)
    if data.get("schema_version") != SCHEMA or not isinstance(data.get("services"), list):
        raise ValueError("services.json needs schema_version: 1 and a services array")
    ids = set()
    for service in data["services"]:
        if not isinstance(service, dict):
            raise ValueError("each service must be an object")
        sid = identifier(service.get("id", ""))
        if sid in ids:
            raise ValueError("duplicate service ID: " + sid)
        ids.add(sid)
        if not isinstance(service.get("aliases", []), list) or not all(isinstance(x, str) for x in service.get("aliases", [])):
            raise ValueError("service aliases must be an array of strings")
        if not isinstance(service.get("sources", {}), dict):
            raise ValueError("service sources must be an object")
        supported = {"cubeapm", "grafana", "jenkins", "github", "git", "elasticsearch", "kubernetes", "aws", "jira", "nginxpm"}
        for provider, config in service.get("sources", {}).items():
            if provider not in supported or not isinstance(config, dict):
                raise ValueError("unsupported or invalid service source: " + provider)
            if "metrics" in config and (not isinstance(config["metrics"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in config["metrics"].items())):
                raise ValueError("metrics must map labels to query strings")
        if not isinstance(service.get("provenance"), dict):
            raise ValueError("service provenance must be an object")
        if not service.get("provenance", {}).get("source") or not service.get("provenance", {}).get("observed_at"):
            raise ValueError("every service needs provenance.source and provenance.observed_at")
        instant(service["provenance"]["observed_at"])
    return data["services"]


def resolve_service(root, env, name):
    matches = [s for s in services(root, env) if name == s["id"] or name in s.get("aliases", [])]
    if len(matches) > 1:
        raise ValueError("ambiguous service alias: " + name)
    if not matches:
        raise ValueError("service is unmapped; add it to infra-knowledge/" + env + "/services.json (see examples/services.json)")
    return matches[0]


def create(root, env, service, question, bounds, playbook, mode):
    private_dir(root / "sessions")
    sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:10]
    path = root / "sessions" / sid
    private_dir(path / "evidence")
    data = {"schema_version": SCHEMA, "id": sid, "environment": env, "mode": mode,
            "service": service["id"], "service_context": service, "question": question,
            "window": bounds, "playbook": playbook, "created_at": now(), "status": "open", "notes": []}
    write_json(path / "session.json", data)
    return path, data


def evidence(path):
    items = []
    session = read_json(path / "session.json")
    for p in sorted((path / "evidence").glob("*/record.json")):
        if p.parent.is_symlink():
            raise ValueError("evidence directories must not be symlinks")
        record = read_json(p)
        if record.get("id") != p.parent.name or record.get("session") != session["id"] or record.get("environment") != session["environment"]:
            raise ValueError("evidence identity/environment mismatch")
        items.append(record)
    return sorted(items, key=lambda item: (item["observed_at"], item["id"]))
