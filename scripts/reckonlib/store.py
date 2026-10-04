"""Versioned local sessions and atomic evidence; no external storage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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


def local_path(root, *parts):
    """Reject redirects below a trusted workspace/tree root, including broken links."""
    path = root
    for part in Path(*parts).parts:
        if part == ".." or Path(part).is_absolute():
            raise ValueError("local state paths must stay inside their root")
        path = path / part
        if path.is_symlink() or (os.name == "nt" and path.exists() and
                getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("local state paths must not contain symlinks or junctions")
    return path


def validate_tree(root):
    """Validate a snapshot before/after copying without following extra links."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = local_path(root, Path(directory, name).relative_to(root))
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError("session snapshots require regular files and directories")


def private_dir(path):
    if path.is_symlink():
        raise ValueError("local state directories must not be symlinks")
    # mkdir(parents=True) uses the umask for intermediate directories, not mode.
    if not path.parent.exists():
        private_dir(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def write(path, text):
    local_path(path.parent, path.name)
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read_bytes(path, maximum=8 * 1048576):
    local_path(path.parent, path.name)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("local state must be a regular file within its size limit")
        content = stream.read(maximum + 1)
        if len(content) > maximum:
            raise ValueError("local state file exceeds its size limit")
        return content


def read_json(path):
    return json.loads(read_bytes(path).decode("utf-8"))


@contextmanager
def lock(path):
    """OS advisory lock releases automatically on interruption/process death."""
    private_dir(path)
    target = local_path(path, ".lock")
    fd = os.open(target, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    with os.fdopen(fd, "r+b") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("session lock must be a private regular file")
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
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
    path = local_path(root, "sessions", identifier(session_id))
    local_path(path, "session.json")
    if not (path / "session.json").is_file():
        raise ValueError("unknown session: " + session_id)
    data = read_json(path / "session.json")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or data.get("id") != session_id or data.get("environment") not in ("production", "staging", "uat"):
        raise ValueError("unsupported or inconsistent session metadata")
    return path, data


def services(root, env):
    path = local_path(root, "infra-knowledge", env, "services.json")
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
    private_dir(local_path(root, "sessions"))
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
    for directory in sorted(local_path(path, "evidence").iterdir()):
        local_path(path, "evidence", directory.name)
        if not directory.is_dir():
            raise ValueError("unexpected file in evidence directory")
        p = local_path(path, "evidence", directory.name, "record.json")
        # A process can stop between allocating a directory and starting a read.
        if not p.exists() and not any(directory.iterdir()):
            continue
        record = read_json(p)
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA:
            raise ValueError("unsupported evidence metadata")
        if record.get("id") != p.parent.name or record.get("session") != session["id"] or record.get("environment") != session["environment"]:
            raise ValueError("evidence identity/environment mismatch")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) - {"stdout", "stderr"}:
            raise ValueError("invalid evidence artifacts")
        if record.get("status") == "ok" and set(artifacts) != {"stdout", "stderr"}:
            raise ValueError("evidence integrity check failed: successful record lacks artifacts")
        for stream, artifact in artifacts.items():
            expected = "evidence/" + directory.name + "/" + stream + ".txt"
            if not isinstance(artifact, dict) or artifact.get("path") != expected:
                raise ValueError("invalid evidence artifact path")
            try:
                content = read_bytes(local_path(path, expected))
            except FileNotFoundError:
                raise ValueError("evidence integrity check failed: missing artifact") from None
            if len(content) != artifact.get("bytes") or hashlib.sha256(content).hexdigest() != artifact.get("sha256"):
                raise ValueError("evidence integrity check failed: " + directory.name + "/" + stream)
        items.append(record)
    return sorted(items, key=lambda item: (item["observed_at"], item["id"]))
