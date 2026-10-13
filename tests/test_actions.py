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
        self.assertEqual(body, PARAMS["body"])
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
        with mock.patch.object(actions, "argv", lambda op, p: (["gh", "fail"], b"")):
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
