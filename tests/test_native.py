"""End-to-end checks of the native executable, using synthetic connections only."""
import json
import os
import select
import subprocess
import time
import unittest
from pathlib import Path

from helpers import OrgCase, SOURCE


@unittest.skipIf(os.name == "nt", "PTY and process-group checks require Unix")
class NativeApprovalTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"gh": {"provider": "github", "env": "staging"}},
                 secrets={"gh": ["GH_TOKEN=synthetic-token", "GH_HOST=github.fixture.invalid"]})
        self.receipt = self.dir / "created.json"
        self.fake("import json,os,sys\nfrom pathlib import Path\n"
                  "if sys.argv[1:3] == ['issue', 'create']:\n"
                  "    Path(" + repr(str(self.receipt)) + ").write_text(json.dumps({'args':sys.argv[1:], 'body':sys.stdin.read(), 'token':os.environ.get('GH_TOKEN'), 'host':os.environ.get('GH_HOST')}))\n"
                  "    print('https://github.fixture.invalid/acme/api/issues/42')\n"
                  "else: print('[]')\n", "gh")

    def prepare(self):
        result = self.cli("act", "prepare", "github.issue.create", "--org", "acme", "--env", "staging", "--json",
                          "--params", json.dumps({"repo": "acme/api", "title": "Fixture issue", "body": "Fixture body"}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["action"]

    def apply_in_terminal(self, action, answer="yes", before_confirm=None):
        import pty
        master, slave = pty.openpty()
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        child = subprocess.Popen([binary, "act", "apply", action, "--org", "acme", "--env", "staging", "--json"],
                                 stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        os.close(slave)
        preview = b""
        try:
            deadline = time.monotonic() + 10
            while b"anything else to cancel:" not in preview:
                self.assertLess(time.monotonic(), deadline, preview)
                ready, _, _ = select.select([child.stderr], [], [], 0.1)
                if ready:
                    chunk = os.read(child.stderr.fileno(), 65536)
                    if not chunk:
                        break
                    preview += chunk
            if b"anything else to cancel:" in preview:
                if before_confirm:
                    before_confirm()
                os.write(master, (answer + "\n").encode())
            out, err = child.communicate(timeout=10)
            return child.returncode, out.decode(), (preview + err).decode()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            os.close(master)

    def test_native_approval_runs_once_and_records_the_receipt(self):
        action = self.prepare()
        code, out, err = self.apply_in_terminal(action)
        self.assertEqual(code, 0, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "applied")
        self.assertEqual(record["receipt"]["url"], "https://github.fixture.invalid/acme/api/issues/42")
        called = json.loads(self.receipt.read_text())
        self.assertIn("<!-- reckon-action: " + action + " -->", called["body"])
        self.assertEqual(called["token"], "synthetic-token")
        self.assertNotIn("synthetic-token", out + err)
        code, _, err = self.apply_in_terminal(action)
        self.assertEqual(code, 1)
        self.assertIn("each action runs once", err)

    def test_native_approval_keeps_the_settings_shown_to_the_operator(self):
        action = self.prepare()
        code, _, err = self.apply_in_terminal(action, before_confirm=lambda: self.secret(
            "acme", "gh", ["GH_TOKEN=rotated-fixture-token", "GH_HOST=other.fixture.invalid"]))
        self.assertEqual(code, 0, err)
        called = json.loads(self.receipt.read_text())
        self.assertEqual(called["token"], "synthetic-token")
        self.assertEqual(called["host"], "github.fixture.invalid")

    def test_changed_targets_and_nonterminal_apply_are_refused(self):
        action = self.prepare()
        result = self.cli("act", "apply", action, "--org", "acme", "--env", "staging", stdin="yes\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("own terminal", result.stderr)
        self.secret("acme", "gh", ["GH_TOKEN=synthetic-token", "GH_HOST=other.fixture.invalid"])
        code, _, err = self.apply_in_terminal(action)
        self.assertEqual(code, 1)
        self.assertIn("changed after action", err)
        self.assertFalse(self.receipt.exists())

    def test_native_cancellation_terminates_descendants(self):
        pidfile = self.dir / "child.pid"
        self.fake("import subprocess,sys,time\nfrom pathlib import Path\n"
                  "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                  "Path(" + repr(str(pidfile)) + ").write_text(str(child.pid))\n"
                  "time.sleep(60)\n", "gh")
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        child = subprocess.Popen([binary, "run", "gh", "--org", "acme", "--env", "staging", "--", "issue", "list"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        try:
            deadline = time.monotonic() + 10
            while not pidfile.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            child.terminate()
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 130, (out, err))
            pid = int(pidfile.read_text())
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                if time.monotonic() >= deadline:
                    # Linux can retain an already killed descendant as a zombie until init reaps it.
                    status = Path('/proc') / str(pid) / 'stat'
                    self.assertTrue(status.exists() and status.read_text().split()[2] == 'Z', pid)
                    break
                time.sleep(0.02)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


class NativeRoutineTests(OrgCase):
    def test_native_routine_preserves_parameters_and_scrubs_credentials(self):
        self.org(connections={"gsc": {"provider": "gsc"}}, secrets={"gsc": ["GSC_ACCESS_TOKEN=fixture-token"]})
        self.fake("import json,os,sys\nprint(json.dumps({'args':sys.argv[1:], 'org':os.environ.get('RECKON_ORG'), 'env':os.environ.get('RECKON_ENV'), 'token':os.environ.get('GSC_ACCESS_TOKEN')}))", "claude")
        added = self.cli("routine", "add", "weekly", "--org", "acme", "--env", "staging", "--workflow", "growth-review",
                         "--param", "site=https://acme.com/a%20b/", "--at", "mon 08:30", "--json")
        self.assertEqual(added.returncode, 0, added.stderr)
        result = self.cli("routine", "run", "weekly", "--org", "acme", "--json", GSC_ACCESS_TOKEN="ambient-fixture-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "ok")
        seen = json.loads(Path(data["log"]).read_text())
        self.assertEqual((seen["org"], seen["env"], seen["token"]), ("acme", "staging", None))
        self.assertIn("--param site=https://acme.com/a%20b/", seen["args"][1])
        self.assertEqual(seen["args"][2:], ["--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config"])
