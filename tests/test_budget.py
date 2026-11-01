"""Per-session read budgets. Offline."""
import json
import os
import subprocess
import time
import unittest

from helpers import JENKINS, OrgCase, SOURCE


class BudgetTests(OrgCase):
    def test_damaged_saved_budgets_cannot_turn_into_unlimited_reads(self):
        made = self.cli("task", "--question", "q", "--budget", "2", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        path = self.home / "orgs/acme/workspace/sessions" / sid / "session.json"
        original = json.loads(path.read_text())
        for budget in (-1, 0, 1001, "2", True, [], {}):
            with self.subTest(budget=budget):
                content = json.dumps(dict(original, budget=budget))
                path.write_text(content)
                for args in (("run", "jenkins", "--session", sid, "--", "job", "list"), ("collect", sid)):
                    # Global context precedes passthrough arguments for run.
                    prefix = ("--org", "acme", "--env", "staging")
                    result = self.cli(args[0], *prefix, *args[1:])
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("budget", result.stderr)
                self.assertFalse(self.counter.exists())
                self.assertEqual(path.read_text(), content)

    def test_damaged_session_notes_and_status_are_refused_before_a_read(self):
        made = self.cli("task", "--question", "q", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        path = self.home / "orgs/acme/workspace/sessions" / sid / "session.json"
        original = json.loads(path.read_text())
        invalid = [("notes", "invalid"), ("notes", [None]), ("notes", [{}]), ("status", "unknown")]
        note = {"id": "n-one", "kind": "question", "text": "q", "evidence": []}
        invalid += [("notes", [note, note]), ("notes", [dict(note, evidence="e-one")]),
                    ("notes", [dict(note, follows="n-one")]), ("notes", [dict(note, withdrawn_at=True)])]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                content = json.dumps(dict(original, **{key: value}))
                path.write_text(content)
                result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("session", result.stderr)
                self.assertFalse(self.counter.exists())
                self.assertEqual(path.read_text(), content)

    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"}}, secrets={"jenkins-staging": JENKINS})
        self.counter = self.dir / "calls"
        self.fake("open(%r, 'a').write('x')\nprint('[]')\n" % str(self.counter), "jenkins")

    def test_reads_stop_at_the_budget_before_running(self):
        made = self.cli("task", "--question", "q", "--budget", "2", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        for _ in range(2):
            self.assertEqual(self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list").returncode, 0)
        third = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list")
        self.assertEqual(third.returncode, 1)
        self.assertIn("budget of 2 reads reached", third.stderr)
        self.assertEqual(len(self.counter.read_text()), 2)  # the third read never ran
        self.assertEqual(self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list").returncode, 0)  # outside the session

    def test_sessions_without_a_budget_are_unlimited_and_bad_budgets_refused(self):
        sid = json.loads(self.cli("task", "--question", "q", "--org", "acme", "--env", "staging", "--json").stdout)["session"]
        for _ in range(3):
            self.assertEqual(self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list").returncode, 0)
        self.assertNotEqual(self.cli("task", "--question", "q", "--budget", "0", "--org", "acme", "--env", "staging").returncode, 0)

    def test_concurrent_reads_cannot_exceed_the_budget(self):
        self.fake("import time\nopen(%r, 'a').write('x')\ntime.sleep(0.5)\nprint('[]')\n" % str(self.counter), "jenkins")
        made = self.cli("task", "--question", "q", "--budget", "1", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        args = ["run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list"]
        first = subprocess.Popen([os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon")), *args],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        try:
            deadline = time.monotonic() + 5
            while not self.counter.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            second = self.cli(*args)
            out, err = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, (out, err))
            self.assertEqual(second.returncode, 1, second.stderr)
            self.assertEqual(self.counter.read_text(), "x")
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()

    def test_a_closed_output_pipe_does_not_lose_evidence_or_refund_a_read(self):
        made = self.cli("task", "--question", "q", "--budget", "1", "--org", "acme", "--env", "staging", "--json")
        sid = json.loads(made.stdout)["session"]
        args = ["run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list"]
        child = subprocess.Popen([os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon")), *args],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        child.stdout.close()
        child.stdout = None
        _, err = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 1, err)
        second = self.cli(*args)
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertIn("budget of 1 reads reached", second.stderr)
        self.assertEqual(self.counter.read_text(), "x")
        records = list((self.home / "orgs/acme/workspace/sessions" / sid / "evidence").glob("*/record.json"))
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
