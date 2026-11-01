"""End-to-end checks of the native executable, using synthetic connections only."""
from datetime import datetime, timedelta, timezone
import json
import os
import select
import subprocess
import time
import unittest
from unittest import mock
from pathlib import Path

from helpers import BOUNDS, CUBEAPM, OrgCase, SOURCE


class NativeScopeTests(OrgCase):
    def test_complex_shared_metrics_queries_fail_cleanly_before_execution(self):
        self.org(connections={"cube": {"provider": "cubeapm", "env_labels": {"staging": "STAGING"}}},
                 secrets={"cube": CUBEAPM})
        calls = self.dir / "calls"
        self.fake("from pathlib import Path\nPath(" + repr(str(calls)) + ").touch()\nprint('{}')\n")
        selector = 'up{env="STAGING"}'
        queries = ["(" * 24000 + selector + ")" * 24000,
                   "sum(" * 12000 + selector + ")" * 12000,
                   " + ".join([selector] * 3000),
                   "(" * 128 + selector + ")" * 128, selector + " + 0" * 510]
        made = self.cli("task", "--question", "metrics", "--org", "acme", "--env", "staging",
                        "--from", BOUNDS["from"], "--to", BOUNDS["to"], "--json")
        self.assertEqual(made.returncode, 0, made.stderr)
        sid = json.loads(made.stdout)["session"]
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        for index, query in enumerate(queries):
            with self.subTest(shape=index):
                for args in (("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query),
                             ("collect", sid, "--org", "acme", "--env", "staging", "--operation", "cubeapm.metrics.range",
                              "--params", json.dumps({"query": query}))):
                    result = subprocess.run([binary, *args], capture_output=True, text=True, env=self.cli_env(), timeout=10)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    if args[0] == "run" or len(query) <= 8192:
                        self.assertIn("complex", result.stderr)
                    self.assertNotIn("overflow", result.stderr)
                    self.assertFalse(calls.exists())

    def test_shared_metric_complexity_limits_preserve_bounded_queries(self):
        self.org(connections={"cube": {"provider": "cubeapm", "env_labels": {"staging": "STAGING"}}},
                 secrets={"cube": CUBEAPM})
        self.fake("print('{}')\n")
        selector = 'up{env="STAGING"}'
        for query in ("(" * 127 + selector + ")" * 127, selector + " + 0" * 509):
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 0, result.stderr)
        for query in ("(" * 128 + selector + ")" * 128, selector + " + 0" * 510):
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("complex", result.stderr)

    def test_quoted_strings_and_comments_do_not_spend_metric_complexity(self):
        self.org(connections={"cube": {"provider": "cubeapm", "env_labels": {"staging": "STAGING"}}},
                 secrets={"cube": CUBEAPM})
        self.fake("print('{}')\n")
        for quote in ('"', "'", '`'):
            query = 'up{env="STAGING",note=' + quote + "(" * 4096 + "#" + ")" * 4096 + quote + '} # ' + "(" * 4096
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_metric_strings_keep_literal_backslashes_and_source_bytes(self):
        self.org(connections={"cube": {"provider": "cubeapm", "env_labels": {"staging": "STAGING"}}},
                 secrets={"cube": CUBEAPM})
        self.fake("import json,sys\nprint(json.dumps({'args':sys.argv[1:]}))\n")
        for query in (r'up{env=`STAGING`,note=`raw\q\`}', r'up{env=`STAGING`,note=`\n`}',
                      'up{env=`STAGING`,note=`line1\nline2`}'):
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(query, json.loads(result.stdout)["args"])
        for query in (r'up{env=`OTHER`}', r'up{env=`STAGING`,note=`raw\`} + unscoped # `}'):
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 1, result.stderr)
        for literal in (json.dumps('escaped " # ' + "(" * 4096 + "\\ end"),
                        "'escaped \\' # " + "(" * 4096 + "\\\\ end'", '`raw # ' + "(" * 4096 + '\\`'):
            query = 'up{env="STAGING",note=' + literal + '}'
            result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_metric_queries_refuse_unscoped_subexpressions_before_execution(self):
        self.org(connections={"cube": {"provider": "cubeapm", "env_labels": {"staging": "STAGING"}}},
                 secrets={"cube": CUBEAPM})
        calls = self.dir / "calls"
        self.fake("from pathlib import Path\nPath(" + repr(str(calls)) + ").write_text('called')\nprint('{}')\n")
        query = 'sum(up{env="STAGING"}) + sum(up)'
        result = self.cli("run", "cube", "--org", "acme", "--env", "staging", "--", "metrics", "query", query)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(calls.exists())
        made = self.cli("task", "--question", "metrics", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        path = self.home / "orgs/acme/workspace/sessions" / sid / "session.json"
        data = json.loads(path.read_text())
        data["window"] = BOUNDS
        path.write_text(json.dumps(data))
        result = self.cli("collect", sid, "--org", "acme", "--env", "staging", "--operation", "cubeapm.metrics.range",
                          "--params", json.dumps({"query": query}))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(calls.exists())


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

    def apply_in_terminal(self, action, answer="yes", before_confirm=None, cancel_before_confirm=False):
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
                if cancel_before_confirm:
                    child.terminate()
                    time.sleep(0.1)
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

    def test_nonzero_exit_cannot_prove_a_remote_write_failed(self):
        action = self.prepare()
        self.fake("import sys\nfrom pathlib import Path\nPath(" + repr(str(self.receipt))
                  + ").write_text(sys.stdin.read())\nprint('remote request completed, local formatting failed', file=sys.stderr)\nsys.exit(7)\n", "gh")
        code, out, err = self.apply_in_terminal(action)
        self.assertEqual(code, 2, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["receipt"]["exit_code"], 7)
        self.assertIn("<!-- reckon-action: " + action + " -->", self.receipt.read_text())
        self.fake("import json\nfrom pathlib import Path\nprint(json.dumps([{'url':'https://github.fixture.invalid/acme/api/issues/42','body':Path("
                  + repr(str(self.receipt)) + ").read_text()}]))\n", "gh")
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "applied")

    def test_incomplete_write_receipt_hides_a_secret_prefix(self):
        self.fake("import os,sys\nsys.stdout.write('x' * (262144 - 12) + os.environ['GH_TOKEN'] + '\\n')\n", "gh")
        action = self.prepare()
        code, out, err = self.apply_in_terminal(action)
        self.assertEqual(code, 2, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "unknown")
        self.assertTrue(record["receipt"]["stdout"].endswith("[REDACTED]"))
        self.assertNotIn("synthetic-token"[:12], out + err)
        saved = self.home / "orgs/acme/workspace/actions" / action / "action.json"
        self.assertNotIn("synthetic-token"[:12], saved.read_text())

    def test_a_write_that_cannot_start_has_a_definite_failure(self):
        action = self.prepare()
        (self.bin / "gh").unlink()
        # Hide any system gh (CI runners ship one) so the write truly cannot start.
        with mock.patch.dict(os.environ, PATH=str(self.bin)):
            code, out, err = self.apply_in_terminal(action)
        self.assertEqual(code, 1, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "failed")
        self.assertIsNone(record["receipt"]["exit_code"])
        self.assertFalse(self.receipt.exists())

    def test_native_approval_keeps_the_settings_shown_to_the_operator(self):
        action = self.prepare()
        code, _, err = self.apply_in_terminal(action, before_confirm=lambda: self.secret(
            "acme", "gh", ["GH_TOKEN=rotated-fixture-token", "GH_HOST=other.fixture.invalid"]))
        self.assertEqual(code, 0, err)
        called = json.loads(self.receipt.read_text())
        self.assertEqual(called["token"], "synthetic-token")
        self.assertEqual(called["host"], "github.fixture.invalid")

    def test_policy_changes_during_approval_refuse_the_write(self):
        config = self.home / "orgs/acme/org.json"
        original = config.read_text()
        for field in ("production", "account", "resources"):
            with self.subTest(field=field):
                config.write_text(original)
                if self.receipt.exists():
                    self.receipt.unlink()
                action = self.prepare()
                changed = json.loads(original)
                if field == "production":
                    changed["production"] = ["staging"]
                elif field == "account":
                    changed["connections"]["gh"]["account"] = "new-bot"
                else:
                    changed["connections"]["gh"]["resources"] = {"repo": ["acme/other"]}
                code, _, err = self.apply_in_terminal(action, before_confirm=lambda: config.write_text(json.dumps(changed)))
                self.assertEqual(code, 1, err)
                self.assertFalse(self.receipt.exists())
                path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
                self.assertEqual(json.loads(path.read_text())["status"], "prepared")

    def test_executable_changes_during_approval_refuse_the_write(self):
        binary = self.bin / "gh"
        original = binary.read_text()
        for remove in (False, True):
            with self.subTest(remove=remove):
                binary.write_text(original)
                binary.chmod(0o700)
                if self.receipt.exists():
                    self.receipt.unlink()
                action = self.prepare()
                def change():
                    if remove:
                        binary.unlink()
                    else:
                        binary.write_text(original + "\n# a different release\n")
                code, _, err = self.apply_in_terminal(action, before_confirm=change)
                self.assertEqual(code, 1, err)
                self.assertIn("executable", err)
                self.assertFalse(self.receipt.exists())
                path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
                self.assertEqual(json.loads(path.read_text())["status"], "prepared")

    def test_an_action_cannot_expire_while_waiting_for_confirmation(self):
        action = self.prepare()
        path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
        record = json.loads(path.read_text())
        record["prepared_at"] = (datetime.now(timezone.utc) - timedelta(hours=24) + timedelta(seconds=10)).isoformat()
        path.write_text(json.dumps(record))
        reached_prompt = []
        def wait_until_expired():
            reached_prompt.append(True)
            time.sleep(11)
        code, _, err = self.apply_in_terminal(action, before_confirm=wait_until_expired)
        self.assertEqual(reached_prompt, [True], err)
        self.assertEqual(code, 1, err)
        self.assertIn("24 hours", err)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(json.loads(path.read_text())["status"], "expired")

    def test_future_preparation_times_cannot_extend_an_actions_lifetime(self):
        action = self.prepare()
        path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
        record = json.loads(path.read_text())
        record["prepared_at"] = "2999-01-01T00:00:00Z"
        path.write_text(json.dumps(record))
        code, _, err = self.apply_in_terminal(action)
        self.assertEqual(code, 1, err)
        self.assertIn("future", err)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(json.loads(path.read_text())["status"], "prepared")

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

    def test_changed_saved_profiles_are_refused_at_apply_and_reconcile(self):
        profile = self.home / "orgs/acme/config/gh/gh/hosts.yml"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("github.fixture.invalid:\n  user: original-bot\n")
        action = self.prepare()
        profile.write_text("github.fixture.invalid:\n  user: other-bot\n")
        code, _, err = self.apply_in_terminal(action)
        self.assertEqual(code, 1, err)
        self.assertIn("configuration", err)
        self.assertFalse(self.receipt.exists())
        path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
        record = json.loads(path.read_text())
        record["status"] = "unknown"
        path.write_text(json.dumps(record))
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("configuration", result.stderr)
        self.assertEqual(json.loads(path.read_text())["status"], "unknown")

    def test_profile_changes_at_the_approval_prompt_cannot_change_the_write(self):
        profile = self.home / "orgs/acme/config/gh/gh/hosts.yml"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("github.fixture.invalid:\n  user: original-bot\n")
        action = self.prepare()
        code, _, err = self.apply_in_terminal(action, before_confirm=lambda: profile.write_text(
            "github.fixture.invalid:\n  user: other-bot\n"))
        self.assertEqual(code, 1, err)
        self.assertIn("configuration", err)
        self.assertFalse(self.receipt.exists())

    def test_legacy_actions_without_profile_fingerprints_need_operator_review(self):
        action, path = self.unknown_action()
        record = json.loads(path.read_text())
        del record["configuration_sha256"]
        path.write_text(json.dumps(record))
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("configuration", result.stderr)
        self.assertEqual(json.loads(path.read_text())["status"], "unknown")

    def unknown_action(self):
        action = self.prepare()
        path = self.home / "orgs/acme/workspace/actions" / action / "action.json"
        record = json.loads(path.read_text())
        record["status"] = "unknown"
        record["approved_at"] = record["prepared_at"]
        path.write_text(json.dumps(record))
        return action, path

    def test_reconcile_refuses_changed_targets_and_payloads(self):
        action, path = self.unknown_action()
        original = path.read_text()
        changed = json.loads(original)
        changed["params"]["repo"] = "acme/other"
        path.write_text(json.dumps(changed))
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("changed after it was prepared", result.stderr)
        path.write_text(original)
        self.secret("acme", "gh", ["GH_TOKEN=synthetic-token", "GH_HOST=other.fixture.invalid"])
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("changed after action", result.stderr)
        self.assertEqual(json.loads(path.read_text())["status"], "unknown")

    def test_reconcile_checks_the_pinned_account(self):
        config = self.home / "orgs/acme/org.json"
        data = json.loads(config.read_text())
        data["connections"]["gh"]["account"] = "expected-bot"
        config.write_text(json.dumps(data))
        action, path = self.unknown_action()
        tag = "<!-- reckon-action: " + action + " -->"
        self.fake("import json,sys\nprint(json.dumps({'login':'other-bot'} if sys.argv[1:3] == ['api','user'] else "
                  + repr([{"url": "https://github.fixture.invalid/acme/api/issues/42", "body": tag}]) + "))\n", "gh")
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("other-bot", result.stderr)
        self.assertEqual(json.loads(path.read_text())["status"], "unknown")

    def test_reconcile_accepts_rotated_credentials_for_the_original_target(self):
        action, _ = self.unknown_action()
        tag = "<!-- reckon-action: " + action + " -->"
        self.fake("import json\nprint(json.dumps(" + repr([
            {"url": "https://github.fixture.invalid/acme/api/issues/42", "body": tag}]) + "))\n", "gh")
        self.secret("acme", "gh", ["GH_TOKEN=rotated-fixture-token", "GH_HOST=github.fixture.invalid"])
        result = self.cli("act", "reconcile", action, "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "applied")

    def test_cancellation_before_approval_never_starts_the_write(self):
        action = self.prepare()
        code, out, err = self.apply_in_terminal(action, cancel_before_confirm=True)
        self.assertEqual(code, 2, err)
        record = json.loads(out)
        self.assertEqual(record["status"], "unknown")
        self.assertIsNone(record["receipt"]["exit_code"])
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
