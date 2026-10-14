"""Shared fixtures for the offline tests: a temporary RECKON_HOME, fake CLIs, a CLI runner."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
from reckonlib import orgs  # noqa: E402

REGISTRY = orgs.providers()
BOUNDS = {"from": "2026-09-08T14:00:00Z", "to": "2026-09-08T14:30:00Z"}
CUBEAPM = ["CUBEAPM_SERVER=fixture.invalid", "CUBEAPM_EMAIL=test@fixture.invalid", "CUBEAPM_PASSWORD=secret-for-test"]
JENKINS = ["JENKINS_URL=https://jenkins.fixture.invalid", "JENKINS_USER=tester", "JENKINS_TOKEN=jenkins-token-123"]


class OrgCase(unittest.TestCase):
    """Each test gets its own RECKON_HOME, a fake-CLI bin directory and a minimal PATH."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reckon-test-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.home = self.dir / "home"
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        # Agent transcripts are read by reckon spend; never the real ones.
        self.agents = {"CLAUDE_CONFIG_DIR": str(self.dir / "claude"), "CODEX_HOME": str(self.dir / "codex")}
        patcher = mock.patch.dict(os.environ, dict(self.agents, RECKON_HOME=str(self.home), PATH=str(self.bin) + ":/usr/bin:/bin"))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("RECKON_ORG", None)
        os.environ.pop("RECKON_ENV", None)
        self.registry = REGISTRY

    def fake(self, source, binary="cubeapm"):
        path = self.bin / binary
        # Fakes behave like releases without the agent-contract resolver unless a test
        # implements "commands resolve" itself.
        guard = ("import sys as _sys\nif _sys.argv[1:3] == ['commands', 'resolve'] and 'resolve' not in " + repr(source) +
                 ":\n    _sys.stderr.write('Error: unknown command \"commands\"\\n'); _sys.exit(1)\n")
        path.write_text("#!" + sys.executable + "\n" + guard + source)
        path.chmod(0o700)
        return path

    def org(self, name="acme", envs=("staging", "uat"), connections=None, secrets=None, production=None):
        """Create an org with connections ({name: conn}) and secrets ({connection: [lines]})."""
        data = orgs.create(self.home, name, None, list(envs), production if production is not None else [], self.registry)
        data["connections"].update(connections or {})
        orgs.save(self.home, data, self.registry)
        for cname, lines in (secrets or {}).items():
            self.secret(name, cname, lines)
        return data

    def secret(self, org, cname, lines, mode=0o600):
        path = orgs.secrets_path(self.home, org, cname)
        orgs.write(path, lines if isinstance(lines, str) else "\n".join(lines) + "\n")
        path.chmod(mode)
        return path

    def ctx(self, org="acme", env="staging"):
        return orgs.context(self.home, org, env, self.registry)

    def cli_env(self, **extra):
        env = dict(self.agents, RECKON_HOME=str(self.home), PATH=os.environ["PATH"], HOME=os.environ.get("HOME", str(self.dir)))
        env.update(extra)
        return env

    def cli(self, *args, stdin=None, **extra):
        return subprocess.run([sys.executable, "-B", str(SOURCE / "scripts/reckon.py"), *args], capture_output=True, text=True,
                              input=stdin, env=self.cli_env(**extra))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(root):
    """Relative path -> content hash for every file, to prove a tree did not change."""
    return {str(p.relative_to(root)): sha256(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}
