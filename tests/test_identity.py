"""Connections know their account: verify reports it and flags another one, and a
prepared write is refused when its connection changed or logged in elsewhere. Offline, fake CLIs."""
import json
import unittest
from unittest import mock

from helpers import JENKINS, OrgCase
from reckonlib import actions, connectors, orgs

PARAMS = {"repo": "acme/api", "title": "Fix checkout timeout", "body": "Seen in session s-1."}


class IdentityTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.login = self.dir / "login"
        self.login.write_text("acme-bot")
        # gh api user answers with whatever login the test sets; anything else creates the issue.
        self.used = self.dir / "used"
        self.fake("import json, os, sys\n"
                  "if sys.argv[1:3] == ['api', 'user']: print(json.dumps({'login': open(" + repr(str(self.login)) + ").read()})); sys.exit()\n"
                  "sys.stdin.read()\nopen(" + repr(str(self.used)) + ", 'w').write(os.environ.get('GH_TOKEN', '') + ' ' + os.environ.get('GH_HOST', ''))\n"
                  "print('https://github.com/acme/api/issues/7')\n", "gh")
        self.org(connections={"gh": {"provider": "github", "env": "staging", "account": "acme-bot"},
                              "jenkins": {"provider": "jenkins", "env": "staging"}},
                 secrets={"gh": ["GH_TOKEN=gh-secret-token"], "jenkins": JENKINS})
        self.fake("import json\nprint(json.dumps({'id': 'Tester', 'fullName': 'T'}))", "jenkins")
        self.root = orgs.workspace(self.home, "acme")
        patcher = mock.patch.object(actions, "terminal", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify(self, *names):
        result = self.cli("verify", *names, "--org", "acme", "--env", "staging", "--json")
        return result.returncode, {r["connection"]: r for r in json.loads(result.stdout)}

    def edit(self, cname, **fields):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"][cname].update(fields)
        orgs.save(self.home, data, self.registry)

    def test_verify_reports_the_account_and_flags_another(self):
        code, rows = self.verify()
        self.assertEqual(code, 0)
        self.assertEqual((rows["gh"]["status"], rows["gh"]["account"]), ("ok", "acme-bot"))
        # Not pinned yet: verify says what it found and how to pin it.
        self.assertEqual(rows["jenkins"]["account"], "Tester")
        self.assertIn("reckon connect jenkins --account Tester", rows["jenkins"]["detail"])
        self.login.write_text("someone-else")
        code, rows = self.verify("gh")
        self.assertEqual((code, rows["gh"]["status"]), (1, "wrong_account"))
        self.assertIn("logged in as someone-else, not acme-bot", rows["gh"]["detail"])
        # Case does not matter, and a pattern can allow any account of a domain.
        self.edit("jenkins", account="tester")
        self.assertEqual(self.verify("jenkins")[1]["jenkins"]["status"], "ok")
        self.edit("jenkins", account="dev-*")
        self.assertEqual(self.verify("jenkins")[1]["jenkins"]["status"], "wrong_account")

    def test_status_and_preflight_show_the_last_live_check(self):
        def show(command):
            return json.loads(self.cli(command, "--org", "acme", "--env", "staging", "--json").stdout)
        self.assertNotIn("verified", show("status")["connections"][0])
        self.verify()
        self.login.write_text("someone-else")
        self.verify("gh")
        rows = {r["connection"]: r for r in show("status")["connections"]}
        self.assertEqual((rows["gh"]["verified"]["status"], rows["jenkins"]["verified"]["status"]), ("wrong_account", "ok"))
        warnings = show("preflight")["warnings"]
        self.assertIn("gh failed its last live check (wrong_account); ask the operator to fix it and run reckon verify gh", warnings)
        self.login.write_text("acme-bot")
        self.verify("gh")
        self.assertFalse([w for w in show("preflight")["warnings"] if "live check" in w])
        logged = json.loads(self.cli("log", "--org", "acme", "--env", "staging", "--event", "verify", "--json").stdout)
        self.assertEqual([(r["target"], r["status"]) for r in logged[:2]], [("gh", "ok"), ("gh", "wrong_account")])
        # A check counts only for the configuration it checked, and not for a connection re-added under its name.
        self.edit("gh", account="acme-*")
        rows = {r["connection"]: r for r in show("status")["connections"]}
        self.assertNotIn("verified", rows["gh"])
        self.assertIn("verified", rows["jenkins"])
        self.cli("connect", "jenkins", "--remove", "--org", "acme")
        self.cli("connect", "jenkins", "--provider", "jenkins", "--for-env", "staging", "--org", "acme")
        rows = {r["connection"]: r for r in show("status")["connections"]}
        self.assertNotIn("verified", rows["jenkins"])

    def test_an_account_that_is_not_reported_cannot_be_confirmed(self):
        self.fake("print('not json')", "jenkins")
        self.edit("jenkins", account="tester")
        code, rows = self.verify("jenkins")
        self.assertEqual((code, rows["jenkins"]["status"]), (1, "wrong_account"))
        self.assertIn("did not report which account", rows["jenkins"]["detail"])

    def test_identity_fallbacks_and_values(self):
        spec = {"identity": ["emailAddress", "name", "accountId"]}
        ok = {"status": "ok", "stdout": json.dumps({"accountId": "5b10ac", "emailAddress": ""}).encode()}
        self.assertEqual(connectors.account(spec, ok), "5b10ac")
        for body in (b"[]", b"{\"emailAddress\": true}", b"{\"emailAddress\": {\"x\": 1}}"):
            self.assertIsNone(connectors.account(spec, dict(ok, stdout=body)))
        self.assertIsNone(connectors.account(spec, dict(ok, status="failed")))
        self.assertEqual(connectors.account({"identity": "Account"}, dict(ok, stdout=b'{"Account": 123456789012}')), "123456789012")
        # The whole value is compared, not the 200 characters shown.
        conn = {"account": "a" * 200}
        self.assertIsNone(connectors.account_problem("c", conn, "a" * 200, {}))
        self.assertIn("is logged in as", connectors.account_problem("c", conn, "a" * 200 + "-other", {}))

    def test_a_pinned_account_the_tool_cannot_report_is_not_ok(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["apm"] = {"provider": "cubeapm", "env": "staging", "account": "acme-bot"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "apm", ["CUBEAPM_SERVER=apm.invalid", "CUBEAPM_EMAIL=a@b.invalid", "CUBEAPM_PASSWORD=pw-secret"])
        self.fake("print('{}')", "cubeapm")
        code, rows = self.verify("apm")
        self.assertEqual((code, rows["apm"]["status"]), (1, "wrong_account"))

    def test_connect_sets_and_clears_the_account(self):
        result = self.cli("connect", "jenkins", "--account", "Tester", "--org", "acme", "--json")
        self.assertEqual(json.loads(result.stdout)["account"], "Tester")
        # It already has credentials, so the next step is checking it, not filling them in.
        self.assertEqual(json.loads(result.stdout)["next"], "reckon verify jenkins")
        fresh = self.cli("connect", "gh-other", "--provider", "github", "--org", "acme", "--json")
        self.assertTrue(json.loads(fresh.stdout)["next"].startswith("edit "), fresh.stdout)
        self.assertEqual(self.cli("connect", "jenkins", "--account", "", "--org", "acme").returncode, 0)
        self.assertNotIn("account", orgs.load(self.home, "acme", self.registry)["connections"]["jenkins"])
        refused = self.cli("connect", "apm", "--provider", "cubeapm", "--account", "x", "--org", "acme")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("does not report which account", refused.stderr)
        with self.assertRaisesRegex(ValueError, "account is the login"):
            self.edit("jenkins", account=" padded")

    def prepare(self):
        return actions.prepare(self.home, self.ctx(), self.root, "github.issue.create", dict(PARAMS), self.registry)

    def apply(self, record):
        asked = []
        done = actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=lambda p: asked.append(p) or "yes")
        return done, asked

    def test_apply_confirms_the_account_before_asking(self):
        record, shown = self.prepare()
        self.assertIn("on gh as acme-bot in acme/staging", shown)
        self.login.write_text("someone-else")
        asked = []
        with self.assertRaisesRegex(ValueError, "logged in as someone-else, not acme-bot"):
            actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=lambda p: asked.append(p) or "yes")
        # Refused before the operator was asked, and the action is still waiting.
        self.assertEqual(asked, [])
        self.assertEqual(actions.load(self.root, record["id"])[1]["status"], "prepared")
        self.login.write_text("acme-bot")
        self.assertEqual(self.apply(record)[0]["status"], "applied")

    def test_a_changed_connection_means_preparing_again(self):
        changes = (lambda: self.edit("gh", resources={"repo": ["acme/*"]}),
                   lambda: self.edit("gh", account="acme-*"),
                   lambda: self.secret("acme", "gh", ["GH_TOKEN=gh-secret-token", "GH_HOST=github.example.invalid"]))
        for change in changes:
            data = orgs.load(self.home, "acme", self.registry)
            data["connections"]["gh"] = {"provider": "github", "env": "staging", "account": "acme-bot"}
            orgs.save(self.home, data, self.registry)
            self.secret("acme", "gh", ["GH_TOKEN=gh-secret-token"])
            record, _ = self.prepare()
            change()
            with self.assertRaisesRegex(ValueError, "changed after action .* was prepared"):
                self.apply(record)
        # A rotated token is not a different target; the account check covers who it belongs to.
        self.secret("acme", "gh", ["GH_TOKEN=gh-secret-token"])
        self.edit("gh", account="acme-bot")
        record, _ = self.prepare()
        self.secret("acme", "gh", ["GH_TOKEN=gh-rotated-token"])
        self.assertEqual(self.apply(record)[0]["status"], "applied")
        # Secrets never enter the action record.
        self.assertNotIn("gh-secret-token", json.dumps(actions.load(self.root, record["id"])[1]))

    def test_a_reference_counts_by_the_value_it_gives(self):
        self.secret("acme", "gh", ["GH_TOKEN=gh-secret-token", "GH_HOST=keychain:gh-host"])
        host = {"value": "github.a.invalid"}
        with mock.patch.object(orgs, "reference", side_effect=lambda k, v: host["value"] if v.startswith("keychain:") else v):
            record, _ = self.prepare()
            host["value"] = "github.b.invalid"
            with self.assertRaisesRegex(ValueError, "changed after action"):
                self.apply(record)

    def test_the_write_runs_with_the_settings_that_were_checked(self):
        record, _ = self.prepare()

        def confirm(prompt):
            # Settings edited while the operator reads the preview do not reach the write.
            self.secret("acme", "gh", ["GH_TOKEN=gh-other-token", "GH_HOST=github.other.invalid"])
            return "yes"
        done = actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=confirm)
        self.assertEqual(done["status"], "applied")
        self.assertEqual(self.used.read_text(), "gh-secret-token ")


if __name__ == "__main__":
    unittest.main()
