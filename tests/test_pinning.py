"""A pinned agent stays inside its org and environment for saved work, listings and briefs;
approval connections run only where Claude Code asks first. Offline."""
import json
import unittest

from helpers import JENKINS, OrgCase
from reckonlib import connectors, orgs
from reckonlib.store import create, write_json


class PinnedAgentTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"}, "db": {"provider": "postgres", "env": "staging"}},
                 secrets={"jenkins-staging": JENKINS, "db": ["PGHOST=db.invalid", "PGUSER=reader", "PGPASSWORD=pg-secret-1234"]})
        self.org("other", envs=("prod",))
        self.root = orgs.workspace(self.home, "acme")
        self.fake("print('1')", "psql")

    def agent(self, *args, env="staging", **extra):
        return self.cli(*args, RECKON_ORG="acme", RECKON_ENV=env, **extra)

    def test_approval_connections_need_claude_codes_prompt(self):
        read = ("run", "db", "--", "-c", "SELECT 1")
        refused = self.agent(*read, RECKON_RUNTIME="codex")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("needs Claude Code", refused.stderr)
        self.assertEqual(self.agent(*read).returncode, 1)  # pinned with no runtime named: refused too
        self.assertEqual(self.agent(*read, RECKON_RUNTIME="claude").returncode, 0)
        operator = self.cli("run", "db", "--org", "acme", "--env", "staging", "--", "-c", "SELECT 1")
        self.assertEqual(operator.returncode, 0, operator.stderr)
        log = json.loads(self.cli("log", "--org", "acme", "--env", "staging", "--event", "refused", "--json").stdout)
        self.assertTrue(any("needs Claude Code" in e["detail"] for e in log))

    def test_session_commands_stay_in_the_pinned_environment(self):
        _, uat = create(self.root, "acme", "uat", "UAT release check", "task")
        for command in (("note", uat["id"], "--kind", "question", "--text", "q"), ("resume", uat["id"]), ("report", uat["id"])):
            result = self.agent(*command)
            self.assertEqual(result.returncode, 1, command)
            self.assertIn("session belongs to acme/uat", result.stderr)
        self.assertEqual(self.agent("note", uat["id"], "--kind", "question", "--text", "q", env="uat").returncode, 0)
        # The operator may still work on another environment's session.
        self.assertEqual(self.cli("note", uat["id"], "--org", "acme", "--env", "staging", "--kind", "question", "--text", "q").returncode, 0)

    def test_listings_show_only_the_pinned_org_and_environment(self):
        self.assertEqual([o["org"] for o in json.loads(self.agent("org", "list", "--json").stdout)], ["acme"])
        self.assertEqual(self.agent("org", "show", "other").returncode, 1)
        checks = json.loads(self.agent("doctor", "--json").stdout)["checks"]
        self.assertIn("orgs: acme", checks)
        self.assertEqual(self.agent("history", "--all-envs").returncode, 1)
        for aid, env in (("a-staging", "staging"), ("a-uat", "uat")):
            write_json(self.root / "actions" / aid / "action.json", {"id": aid, "status": "prepared", "operation": "github.issue.create",
                                                                    "connection": "gh", "environment": env, "params": {"repo": "acme/api"}})
        self.assertEqual([a["id"] for a in json.loads(self.agent("act", "list", "--json").stdout)], ["a-staging"])
        self.assertEqual(self.agent("act", "show", "a-uat").returncode, 1)
        operator = json.loads(self.cli("act", "list", "--org", "acme", "--env", "staging", "--json").stdout)
        self.assertEqual(sorted(a["id"] for a in operator), ["a-staging", "a-uat"])

    def test_brief_leaves_out_other_environments_work(self):
        create(self.root, "acme", "uat", "UAT only", "task")
        create(self.root, "acme", "staging", "Staging review", "task")
        write_json(self.root / "actions" / "a-uat" / "action.json", {"id": "a-uat", "status": "prepared", "operation": "github.issue.create",
                                                                   "connection": "gh", "environment": "uat", "params": {"repo": "acme/api"}})
        out = json.loads(self.agent("brief", "--json").stdout)
        self.assertEqual([s["question"] for s in out["open_sessions"]], ["Staging review"])
        self.assertEqual(out["writes_waiting_for_approval"], [])


class CacheKeyTests(unittest.TestCase):
    def test_repointing_a_connection_changes_the_key(self):
        ctx = {"org": "acme", "env": "staging"}
        conn = {"provider": "jenkins", "env": "staging"}
        one = connectors.cache_key(ctx, "jenkins", conn, {"JENKINS_URL": "https://a.invalid"}, ["jenkins", "job", "list"], None)
        two = connectors.cache_key(ctx, "jenkins", conn, {"JENKINS_URL": "https://b.invalid"}, ["jenkins", "job", "list"], None)
        self.assertNotEqual(one, two)
        self.assertEqual(one, connectors.cache_key(ctx, "jenkins", conn, {"JENKINS_URL": "https://a.invalid"}, ["jenkins", "job", "list"], None))


if __name__ == "__main__":
    unittest.main()
