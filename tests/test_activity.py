"""Secret references, the activity log and the all-orgs brief. Offline, fake CLIs."""
import json
import unittest

from helpers import OrgCase
from reckonlib import activity, orgs, workspace

# Fake keychain (macOS security, Linux secret-tool) and 1Password readers.
STORE = ("import sys\nfound = {'jenkins-token': 'kc-secret-1', 'op://Ops/Jenkins/token': 'op-secret-2'}\n"
         "key = sys.argv[-2] if sys.argv[1] == 'find-generic-password' else sys.argv[-1]\n"
         "if key not in found: sys.exit(44)\n"
         "print(found[key])\n")
# Jenkins echoes its token so the test can see what reached the child, and that it is redacted.
JENKINS = "import os\nprint('token=' + os.environ['JENKINS_TOKEN'])\n"


class ActivityTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"},
                              "jenkins-uat": {"provider": "jenkins", "env": "uat"}},
                 secrets={"jenkins-staging": ["JENKINS_URL=https://ci.fixture.invalid", "JENKINS_USER=tester",
                                              "JENKINS_TOKEN=keychain:jenkins-token"],
                          "jenkins-uat": ["JENKINS_URL=https://uat.fixture.invalid", "JENKINS_USER=tester",
                                          "JENKINS_TOKEN=op://Ops/Jenkins/token"]})
        self.fake(JENKINS, "jenkins")
        for binary in ("security", "secret-tool", "op"):
            self.fake(STORE, binary)

    def test_references_resolve_only_inside_a_run_and_are_redacted(self):
        env, values = orgs.child_env(self.home, self.ctx(), "jenkins-staging", self.ctx()["data"]["connections"]["jenkins-staging"],
                                     self.registry["jenkins"])
        self.assertEqual(env["JENKINS_TOKEN"], "kc-secret-1")
        self.assertEqual(values["JENKINS_TOKEN"], "kc-secret-1")
        uat = self.ctx(env="uat")
        env, _ = orgs.child_env(self.home, uat, "jenkins-uat", uat["data"]["connections"]["jenkins-uat"], self.registry["jenkins"])
        self.assertEqual(env["JENKINS_TOKEN"], "op-secret-2")
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("kc-secret-1", result.stdout)
        # A missing item is an error that names the setting, never a fallback to the reference text.
        self.secret("acme", "jenkins-staging", ["JENKINS_URL=https://ci.fixture.invalid", "JENKINS_USER=tester",
                                                "JENKINS_TOKEN=keychain:missing"])
        missing = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("JENKINS_TOKEN", missing.stderr)
        self.assertNotIn("keychain:missing\n", missing.stdout)

    def test_runs_and_refusals_are_logged_without_values(self):
        self.assertEqual(self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list").returncode, 0)
        refused = self.cli("run", "jenkins-uat", "--org", "acme", "--env", "staging", "--", "job", "list")
        self.assertEqual(refused.returncode, 1)
        denied = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "build", "deploy", "--token", "x")
        self.assertEqual(denied.returncode, 1)
        rows = activity.read(self.home, "acme", env="staging")
        self.assertEqual([r["event"] for r in rows], ["refused", "refused", "run"])
        self.assertEqual(rows[2]["command"], ["job", "list"])
        self.assertEqual((rows[2]["status"], rows[2]["actor"]), ("ok", "operator"))
        self.assertIn("jenkins-uat", rows[1]["detail"])
        text = activity.path(self.home, "acme").read_text()
        self.assertNotIn("kc-secret-1", text)
        self.assertEqual(oct(activity.path(self.home, "acme").stat().st_mode & 0o777), "0o600")
        log = self.cli("log", "--org", "acme", "--env", "staging", "--event", "refused", "--json")
        self.assertEqual(len(json.loads(log.stdout)), 2)
        # The brief reports refusals so a wrong-context attempt is seen the next morning.
        brief = json.loads(self.cli("brief", "--org", "acme", "--env", "staging", "--json").stdout)
        self.assertEqual((len(brief["refused_runs"]), brief["runs"]), (2, 1))

    def test_all_orgs_brief_is_for_the_operator_only(self):
        self.org("beta", envs=("prod",), production=["prod"])
        self.cli("run", "jenkins-uat", "--org", "acme", "--env", "staging", "--", "job", "list")
        result = self.cli("brief", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual([r["org"] for r in out["orgs"]], ["acme", "beta"])
        self.assertEqual(out["orgs"][0]["environment"], "all")
        self.assertEqual(len(out["orgs"][0]["refused_runs"]), 1)
        text = self.cli("brief", "--all-orgs").stdout
        self.assertIn("| acme | 0 | 0 | 0 | 0 | 1 | 0 |", text)
        self.assertIn("## acme / all", text)
        pinned = self.cli("brief", "--all-orgs", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(pinned.returncode, 1)
        self.assertIn("pinned agent", pinned.stderr)

    def test_agents_may_not_read_secret_stores_directly(self):
        deny = workspace.settings(self.home, self.ctx(), self.registry)["permissions"]["deny"]
        for rule in ("Bash(security find-generic-password:*)", "Bash(op read:*)", "Bash(secret-tool:*)"):
            self.assertIn(rule, deny)


if __name__ == "__main__":
    unittest.main()
