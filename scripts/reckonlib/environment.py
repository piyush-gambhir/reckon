"""Resolve credentials in a fresh subprocess; never serialize them to disk."""
import json
import os
from pathlib import Path
import re
import shutil

from .process import run
from .store import local_path, private_dir

ENVIRONMENTS = ("production", "staging", "uat")
BASE_KEYS = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
             "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE",
             "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE",
             "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"}
PREFIXES = {
    "grafana": ("GRAFANA_",), "jenkins": ("JENKINS_",), "cubeapm": ("CUBEAPM_",),
    "github": ("GH_", "GITHUB_"), "aws": ("AWS_",), "kubernetes": ("KUBE", "AWS_"),
    "kafka": ("KAFKA_",), "kafka-rpk": ("RPK_", "KAFKA_"), "redis": ("REDIS_",),
    "mongodb": ("MONGODB_",), "postgres": ("PG",), "mysql": ("MYSQL_",),
    "clickhouse": ("CLICKHOUSE_",), "elasticsearch": ("ES_",), "jira": ("JIRA_",),
    "nginxpm": ("NGINXPM_",), "git": (),
}


def selected(root, explicit=None):
    value = explicit or os.environ.get("RECKON_ENV")
    selection = root / ".reckon-env"
    if not value and selection.is_file():
        value = selection.read_text().strip()
    if value not in ENVIRONMENTS:
        raise ValueError("select an environment with --env production|staging|uat or scripts/reckon use <env>")
    return value


def load(root, name):
    if name not in ENVIRONMENTS:
        raise ValueError("invalid environment")
    local_path(root, ".config", name)
    clean = {k: v for k, v in os.environ.items() if k in BASE_KEYS}
    clean.update(RECKON_ROOT=str(root), RECKON_ENV=name, NO_COLOR="1")
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   ". (Join-Path $env:RECKON_ROOT 'scripts/activate.ps1') *> $null; "
                   "$result=@{}; Get-ChildItem Env: | ForEach-Object {$result[$_.Name]=$_.Value}; "
                   "$result | ConvertTo-Json -Compress"]
    else:
        command = ["bash", "-c", '. "$RECKON_ROOT/.envrc" >/dev/null && env -0']
    result = run(command, env=clean, cwd=root, timeout=10, max_bytes=262144)
    if result["status"] != "ok":
        # A malformed shell env file can echo secrets in its error. Do not forward it.
        raise ValueError("environment activation failed; check the selected environment files locally")
    if os.name == "nt":
        values = json.loads(result["stdout"].decode("utf-8-sig"))
    else:
        values = dict(item.decode().split("=", 1) for item in result["stdout"].split(b"\0") if b"=" in item)
    if values.get("RECKON_ENV") != name or values.get("XDG_CONFIG_HOME") != str(root / ".config" / name):
        raise ValueError("activation changed the selected environment or config directory")
    return values


def scope(values, provider):
    keys = BASE_KEYS | {"XDG_CONFIG_HOME", "RECKON_ROOT", "RECKON_ENV"}
    result = {k: v for k, v in values.items() if k in keys or k.startswith(PREFIXES[provider])}
    # XDG_CONFIG_HOME alone does not cover HOME-based profiles, SSO caches,
    # startup files or temporary data. Keep those defaults private to this
    # provider/environment. This is path scoping, not an OS filesystem sandbox.
    root = Path(values["RECKON_ROOT"])
    runtime = local_path(root, ".config", values["RECKON_ENV"], "runtime", provider)
    for directory, keys in (("home", ("HOME", "USERPROFILE")),
                            ("cache", ("XDG_CACHE_HOME", "LOCALAPPDATA")),
                            ("state", ("XDG_STATE_HOME", "APPDATA")),
                            ("tmp", ("TMPDIR", "TMP", "TEMP"))):
        target = local_path(root, runtime.relative_to(root), directory)
        private_dir(target)
        for key in keys:
            result[key] = str(target)
    result.update(NO_COLOR="1", CI="1", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", PAGER="cat")
    # CLI-level restrictions supplement server-side read-only roles.
    for prefix in ("GRAFANA", "JENKINS", "ES", "JIRA", "NGINXPM"):
        result[prefix + "_READ_ONLY"] = "true"
        result[prefix + "_NO_INPUT"] = "true"
    result["AWS_PAGER"] = ""
    return result


def registry(root):
    return [dict(zip(("name", "binary", "required", "config"), line.split("|")))
            for line in (root / "scripts/integrations.tsv").read_text().splitlines() if line]


def placeholder(value):
    return not value or bool(re.search(r"replace_me|example\.com|\.example\.|@example|xxxx|changeme|your-|^<.*>$", value, re.I))


def readiness(root, values):
    results = {}
    for entry in registry(root):
        installed = bool(shutil.which(entry["binary"], path=values.get("PATH")))
        configured = entry["required"] != "-" and all(
            any(not placeholder(values.get(key, "")) for key in group.split("/"))
            for group in entry["required"].split(","))
        if entry["config"] != "-":
            configured |= any((Path(values["XDG_CONFIG_HOME"]) / p).is_file()
                              and (Path(values["XDG_CONFIG_HOME"]) / p).stat().st_size > 0
                              for p in entry["config"].split(";"))
        results[entry["name"]] = {"installed": installed, "configured": bool(configured),
                                  "state": "configured" if installed and configured else "no_cli" if not installed else "no_credentials"}
    results["git"] = {"installed": bool(shutil.which("git", path=values.get("PATH"))), "configured": True}
    results["git"]["state"] = "configured" if results["git"]["installed"] else "no_cli"
    return results


def redact(text, values):
    secrets = {v for k, v in values.items() if len(v) >= 4 and re.search(r"TOKEN|PASSWORD|SECRET|PASS$|_PWD$|API_KEY|COOKIE", k, re.I)}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    return text
