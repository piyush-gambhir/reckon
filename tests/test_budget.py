"""Per-session read budgets. Offline."""
import json
import unittest

from helpers import JENKINS, OrgCase


class BudgetTests(OrgCase):
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


if __name__ == "__main__":
    unittest.main()
