"""Approved writes: prepared by the agent, applied once by the operator. Offline, fake gh."""
import json
import unittest
from unittest import mock

from helpers import OrgCase
from reckonlib import actions, orgs, workspace
from reckonlib.store import write_json

GH = ["GH_TOKEN=gh-secret-token"]
PARAMS = {"repo": "acme/api", "title": "Fix checkout timeout", "body": "Seen in session s-1.\nSee evidence e-1."}
FAKE_GH = ("import os, sys\n"
           "body = sys.stdin.read()\n"
           "open(os.environ['TMPDIR'] + '/body', 'w').write(body)\n"
           "if 'fail' in sys.argv: print('boom ' + os.environ['GH_TOKEN'], file=sys.stderr); sys.exit(1)\n"
           "print('https://github.com/acme/api/issues/7')\n")


class ActionTests(OrgCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(actions, "terminal", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.org(envs=("staging", "prod"), production=["prod"],
                 connections={"gh-staging": {"provider": "github", "env": "staging"}, "gh-prod": {"provider": "github", "env": "prod"}},
                 secrets={"gh-staging": GH, "gh-prod": GH})
        self.fake(FAKE_GH, "gh")
        self.root = orgs.workspace(self.home, "acme")

    def prepare(self, env="staging", params=None):
        return actions.prepare(self.home, self.ctx(env=env), self.root, "github.issue.create", dict(params or PARAMS), self.registry)[0]

    def apply(self, record, typed, env="staging"):
        return actions.apply(self.home, self.ctx(env=env), self.root, record["id"], self.registry, confirm=lambda prompt: typed)

    def test_prepare_validates_and_freezes_the_payload(self):
        for bad in ({"repo": "acme/api", "title": "x"}, dict(PARAMS, repo="not a repo"), dict(PARAMS, extra="x"),
                    dict(PARAMS, labels="ok,$(rm)")):
            with self.assertRaises(ValueError):
                self.prepare(params=bad)
        with self.assertRaisesRegex(ValueError, "unsupported write"):
            actions.prepare(self.home, self.ctx(), self.root, "github.repo.delete", PARAMS, self.registry)
        record = self.prepare()
        self.assertEqual((record["status"], record["connection"]), ("prepared", "gh-staging"))
        self.assertEqual(record["payload_sha256"], actions.digest(record))

    def test_apply_runs_once_and_keeps_a_receipt(self):
        record = self.prepare()
        done = self.apply(record, "yes")
        self.assertEqual(done["status"], "applied")
        self.assertEqual(done["receipt"]["url"], "https://github.com/acme/api/issues/7")
        body = (self.home / "orgs/acme/runtime/gh-staging/tmp/body").read_text()
        self.assertEqual(body, PARAMS["body"] + "\n\n" + actions.marker(record["id"]) + "\n")
        with self.assertRaisesRegex(ValueError, "runs once"):
            self.apply(record, "yes")

    def test_anything_but_the_confirmation_cancels(self):
        record = self.prepare()
        self.assertEqual(self.apply(record, "y")["status"], "cancelled")
        self.assertFalse((self.home / "orgs/acme/runtime/gh-staging/tmp/body").exists())

    def test_production_needs_the_org_name(self):
        record = self.prepare(env="prod")
        self.assertEqual(self.apply(record, "yes", env="prod")["status"], "cancelled")
        record = self.prepare(env="prod")
        self.assertEqual(self.apply(record, "acme", env="prod")["status"], "applied")

    def test_a_changed_action_is_refused(self):
        record = self.prepare()
        path = self.root / "actions" / record["id"] / "action.json"
        changed = dict(json.loads(path.read_text()), params=dict(PARAMS, title="Something else"))
        write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "changed after it was prepared"):
            self.apply(record, "yes")

    def test_another_environment_cannot_apply_it(self):
        record = self.prepare()
        with self.assertRaisesRegex(ValueError, "belongs to acme/staging"):
            self.apply(record, "acme", env="prod")

    def test_failure_is_recorded_with_secrets_redacted(self):
        record = self.prepare(params=dict(PARAMS, labels="fail"))
        with mock.patch.object(actions, "argv", lambda op, p, i: (["gh", "fail"], b"")):
            done = self.apply(record, "yes")
        self.assertEqual(done["status"], "failed")
        self.assertNotIn("gh-secret-token", json.dumps(done))

    def test_an_interrupted_run_is_unknown_not_retried(self):
        record = self.prepare()
        with mock.patch.object(actions, "bounded", return_value={"status": "timeout", "exit_code": None, "stdout": b"", "stderr": b""}):
            self.assertEqual(self.apply(record, "yes")["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "runs once"):
            self.apply(record, "yes")

    def test_review_fixes(self):
        record = self.prepare()
        # The executor itself refuses inside a pinned agent, whatever confirm a caller passes.
        with mock.patch.dict("os.environ", RECKON_ORG="acme", RECKON_ENV="staging"):
            with self.assertRaisesRegex(ValueError, "not a pinned agent"):
                self.apply(record, "yes")
        with mock.patch.object(actions, "terminal", return_value=False):
            with self.assertRaisesRegex(ValueError, "own terminal"):
                self.apply(record, "yes")
        # A replaced connection is not silently used.
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["github"] = data["connections"].pop("gh-staging")
        orgs.save(self.home, data, self.registry)
        with self.assertRaisesRegex(ValueError, "changed or no longer serves"):
            self.apply(record, "yes")
        # Control characters are shown, not interpreted.
        shown = actions.preview(dict(record, params=dict(PARAMS, body="ok\x1b[2J\x1b[Hfake")), False)
        self.assertNotIn("\x1b", shown)
        self.assertIn("\\x1b[2J", shown)

    def test_stale_actions_expire(self):
        record = self.prepare()
        path = self.root / "actions" / record["id"] / "action.json"
        write_json(path, dict(json.loads(path.read_text()), prepared_at="2020-01-01T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "prepare it again"):
            self.apply(record, "yes")
        self.assertEqual(json.loads(path.read_text())["status"], "expired")

    def test_applied_issues_carry_the_action_marker(self):
        record = self.prepare()
        self.apply(record, "yes")
        body = (self.home / "orgs/acme/runtime/gh-staging/tmp/body").read_text()
        self.assertTrue(body.startswith(PARAMS["body"]))
        self.assertIn(actions.marker(record["id"]), body)

    def test_reconcile_upgrades_only_on_proof(self):
        timeout = {"status": "timeout", "exit_code": None, "stdout": b"", "stderr": b""}
        record = self.prepare()
        with mock.patch.object(actions, "bounded", return_value=timeout):
            self.assertEqual(self.apply(record, "yes")["status"], "unknown")
        # A same-title issue without this action's marker proves nothing; the outcome stays unknown.
        self.fake("import json\nprint(json.dumps([{'number': 3, 'url': 'u3', 'body': 'Fix checkout timeout', 'createdAt': '2999-01-01T00:00:00Z'}]))", "gh")
        still = actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)
        self.assertEqual(still["status"], "unknown")
        self.assertIn("still unknown", still["note"])
        tag = actions.marker(record["id"])
        self.fake("import json\nprint(json.dumps([{'number': 7, 'url': 'https://github.com/acme/api/issues/7', 'body': 'x\\n" + tag + "', 'createdAt': '2000-01-01T00:00:00Z'}]))", "gh")
        done = actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)
        self.assertEqual((done["status"], done["receipt"]["url"]), ("applied", "https://github.com/acme/api/issues/7"))
        with self.assertRaisesRegex(ValueError, "only unknown outcomes"):
            actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)

    def test_reconcile_refuses_a_reassigned_connection(self):
        record = self.prepare()
        with mock.patch.object(actions, "bounded", return_value={"status": "timeout", "exit_code": None, "stdout": b"", "stderr": b""}):
            self.apply(record, "yes")
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["gh-staging"]["env"] = "prod"
        orgs.save(self.home, data, self.registry)
        with self.assertRaisesRegex(ValueError, "no longer serves"):
            actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)

    def test_the_agent_may_prepare_but_never_apply(self):
        record = self.prepare()
        pinned = self.cli("act", "apply", record["id"], RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(pinned.returncode, 1)
        self.assertIn("not a pinned agent", pinned.stderr)
        no_terminal = self.cli("act", "apply", record["id"])
        self.assertEqual(no_terminal.returncode, 1)
        self.assertIn("own terminal", no_terminal.stderr)
        made = self.cli("act", "prepare", "github.issue.create", "--org", "acme", "--env", "staging", "--body-stdin",
                        "--params", json.dumps({"repo": "acme/api", "title": "From the agent"}), "--json", stdin="body text")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.assertIn("reckon act apply", json.loads(made.stdout)["next"])
        rules = workspace.settings(self.home, self.ctx(), self.registry)["permissions"]
        self.assertIn("Bash(reckon act prepare:*)", rules["allow"])
        self.assertIn("Bash(reckon act apply:*)", rules["deny"])


if __name__ == "__main__":
    unittest.main()


FAKE_JIRA = ("import json, os, sys\n"
             "args, tmp = sys.argv[1:], os.environ['TMPDIR']\n"
             "if args[:2] == ['issue', 'search']:\n"
             "    path = tmp + '/listing'\n"
             "    print(open(path).read() if os.path.exists(path) else '[]'); sys.exit()\n"
             "open(tmp + '/args', 'w').write(json.dumps({'args': args, 'read_only': os.environ.get('JIRA_READ_ONLY'),"
             " 'no_input': os.environ.get('JIRA_NO_INPUT')}))\n"
             "print(json.dumps({'id': '10001', 'key': 'ABC-7', 'self': 'https://acme.atlassian.net/rest/api/3/issue/10001'}))\n")
JIRA_PARAMS = {"project": "ABC", "type": "Task", "summary": "-Checkout times out", "description": "Seen in s-1.\n",
               "labels": "growth,checkout", "parent": "ABC-1"}


class JiraWriteTests(OrgCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(actions, "terminal", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.org(connections={"jira": {"provider": "jira"}},
                 secrets={"jira": ["JIRA_SITE=acme.atlassian.net", "JIRA_EMAIL=bot@acme.invalid", "JIRA_TOKEN=jira-secret-token"]})
        self.fake(FAKE_JIRA, "jira")
        self.root = orgs.workspace(self.home, "acme")
        self.tmp = self.home / "orgs/acme/runtime/jira/tmp"

    def prepare(self, **changes):
        return actions.prepare(self.home, self.ctx(), self.root, "jira.issue.create", dict(JIRA_PARAMS, **changes), self.registry)[0]

    def test_parameters_are_checked(self):
        for bad in ({"project": "abc"}, {"type": "Task; rm"}, {"summary": "two\nlines"}, {"labels": "two words"},
                    {"parent": "12"}, {"description": "x" * 30001}, {"assignee": "me"}):
            with self.assertRaises(ValueError, msg=bad):
                self.prepare(**bad)

    def test_apply_creates_one_issue_and_links_it(self):
        record = self.prepare()
        # Org-wide connection: the operator types the org name.
        done = actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=lambda p: "acme")
        self.assertEqual(done["status"], "applied")
        self.assertEqual(done["receipt"]["url"], "https://acme.atlassian.net/browse/ABC-7")
        ran = json.loads((self.tmp / "args").read_text())
        self.assertEqual(ran["args"], ["issue", "create", "--project=ABC", "--type=Task", "--summary=-Checkout times out",
                                       "--description=Seen in s-1.\n\n" + actions.marker(record["id"], "jira.issue.create"),
                                       "--label=growth", "--label=checkout", "--parent=ABC-1", "-o", "json"])
        # The write runs without read-only mode and never waits for input.
        self.assertEqual((ran["read_only"], ran["no_input"]), (None, "true"))

    def test_reconcile_finds_the_issue_by_its_marker(self):
        timeout = {"status": "timeout", "exit_code": None, "stdout": b"", "stderr": b""}
        record = self.prepare()
        with mock.patch.object(actions, "bounded", return_value=timeout):
            self.assertEqual(actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=lambda p: "acme")["status"], "unknown")
        other = {"key": "ABC-6", "self": "https://acme.atlassian.net/rest/api/3/issue/10000", "fields": {"summary": JIRA_PARAMS["summary"]}}
        (self.tmp / "listing").write_text(json.dumps([other]))
        self.assertEqual(actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)["status"], "unknown")
        mine = {"key": "ABC-7", "self": "https://acme.atlassian.net/rest/api/3/issue/10001",
                "fields": {"description": {"type": "doc", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": actions.marker(record["id"], "jira.issue.create")}]}]}}}
        (self.tmp / "listing").write_text(json.dumps([other, mine]))
        done = actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)
        self.assertEqual((done["status"], done["receipt"]["url"]), ("applied", "https://acme.atlassian.net/browse/ABC-7"))


FAKE_GSC = ("import json, os, sys\n"
            "args, tmp = sys.argv[1:], os.environ['TMPDIR']\n"
            "if args[:2] == ['sitemaps', 'get']:\n"
            "    print(open(tmp + '/sitemap').read()); sys.exit()\n"
            "open(tmp + '/args', 'w').write(json.dumps({'args': args, 'read_only': os.environ.get('GSC_READ_ONLY')}))\n"
            "print(json.dumps({'site': 'sc-domain:acme.com', 'sitemap': args[2], 'submitted': True}))\n")
SITEMAP = {"site": "sc-domain:acme.com", "sitemap": "https://www.acme.com/sitemap.xml"}


class SitemapWriteTests(OrgCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(actions, "terminal", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.org(connections={"gsc": {"provider": "gsc", "resources": {"site": ["sc-domain:acme.com"]}}},
                 secrets={"gsc": ["GSC_ACCESS_TOKEN=gsc-secret-token"]})
        self.fake(FAKE_GSC, "gsc")
        self.root = orgs.workspace(self.home, "acme")
        self.tmp = self.home / "orgs/acme/runtime/gsc/tmp"

    def prepare(self, **changes):
        return actions.prepare(self.home, self.ctx(), self.root, "gsc.sitemap.submit", dict(SITEMAP, **changes), self.registry)[0]

    def apply(self, record):
        return actions.apply(self.home, self.ctx(), self.root, record["id"], self.registry, confirm=lambda p: "acme")

    def test_the_sitemap_must_be_inside_an_allowed_property(self):
        for bad, message in (({"sitemap": "https://other.com/sitemap.xml"}, "inside the property"),
                             ({"site": "acme.com"}, "Search Console property"),
                             ({"sitemap": "https://www.acme.com/*.xml"}, "full URL"),
                             ({"site": "sc-domain:other.com", "sitemap": "https://other.com/s.xml"}, "outside")):
            with self.assertRaisesRegex(ValueError, message):
                self.prepare(**bad)
        # A URL-prefix property inside the allowed domain is fine.
        self.prepare(site="https://www.acme.com/", sitemap="https://www.acme.com/news/sitemap.xml")

    def test_apply_submits_once_without_read_only(self):
        done = self.apply(self.prepare())
        self.assertEqual(done["status"], "applied")
        self.assertEqual(done["receipt"]["url"], "https://search.google.com/search-console/sitemaps?resource_id=sc-domain%3Aacme.com")
        ran = json.loads((self.tmp / "args").read_text())
        self.assertEqual(ran, {"args": ["sitemaps", "submit", "https://www.acme.com/sitemap.xml", "--site=sc-domain:acme.com", "-o", "json"],
                               "read_only": None})

    def test_reconcile_needs_a_submission_after_the_apply(self):
        timeout = {"status": "timeout", "exit_code": None, "stdout": b"", "stderr": b""}
        record = self.prepare()
        with mock.patch.object(actions, "bounded", return_value=timeout):
            self.assertEqual(self.apply(record)["status"], "unknown")
        (self.tmp / "sitemap").write_text(json.dumps({"path": SITEMAP["sitemap"], "lastSubmitted": "2020-01-01T00:00:00Z"}))
        self.assertEqual(actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)["status"], "unknown")
        from reckonlib.store import now
        (self.tmp / "sitemap").write_text(json.dumps({"path": SITEMAP["sitemap"], "lastSubmitted": now()}))
        self.assertEqual(actions.reconcile(self.home, self.ctx(), self.root, record["id"], self.registry)["status"], "applied")
