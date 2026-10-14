"""Workflow recipes: parameters, checks judged from evidence, closing rules. Offline, fake gsc."""
import json
import unittest

from helpers import OrgCase
from reckonlib import orgs, workflows
from reckonlib.store import write_json


class WorkflowTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"gsc": {"provider": "gsc"}}, secrets={"gsc": ["GSC_ACCESS_TOKEN=gsc-token-1"]})
        self.fake("print('{\"rows\": []}')", "gsc")
        self.scope = ("--org", "acme", "--env", "staging")

    def start(self, *params):
        result = self.cli("task", "--workflow", "growth-review", *[a for p in params for a in ("--param", p)], *self.scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def check(self, sid):
        return json.loads(self.cli("check", sid, *self.scope, "--json").stdout)

    def test_recipes_ship_valid_and_parameters_are_checked(self):
        self.assertEqual(sorted(workflows.recipes(orgs.workspace(self.home, "acme"))), ["growth-review", "release-review"])
        for params, message in (((), "needs --param site"), (("site=acme.com",), "must match"),
                                (("site=sc-domain:acme.com", "colour=blue"), "has no parameter colour")):
            refused = self.cli("task", "--workflow", "growth-review", *[a for p in params for a in ("--param", p)], *self.scope)
            self.assertEqual(refused.returncode, 1, params)
            self.assertIn(message, refused.stderr)
        started = self.start("site=sc-domain:acme.com", "week=2026-10-12")
        session = json.loads(self.cli("resume", started["session"], *self.scope, "--json").stdout)
        self.assertIn("Growth review for sc-domain:acme.com, week of 2026-10-12", json.dumps(session))
        self.assertIn("gsc-totals: Search Console totals against the previous period", started["checks"])

    def test_done_is_judged_from_evidence(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        rows = {r["id"]: r["status"] for r in self.check(sid)["workflow"]["checks"]}
        # No bing or clarity connection in this org: those checks cannot be done here.
        self.assertEqual((rows["gsc-totals"], rows["bing-summary"], rows["behaviour"]), ("todo", "unavailable", "unavailable"))
        run = self.cli("run", "gsc", "--session", sid, *self.scope, "--", "performance", "--site", "sc-domain:acme.com", "-o", "json")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = self.check(sid)
        rows = {r["id"]: r for r in result["workflow"]["checks"]}
        self.assertEqual(rows["gsc-totals"]["status"], "met")
        self.assertIn("workflow checks still to do", " ".join(result["warnings"]))
        refused = self.cli("close", sid, "--outcome", "done", *self.scope)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("gsc-trend", refused.stderr)
        for check in ("gsc-trend", "top-queries", "top-pages", "opportunities"):
            self.cli("note", sid, "--kind", "question", "--text", "skip " + check + ": property is two days old", *self.scope)
        evidence = rows["gsc-totals"]["evidence"][0]
        self.cli("note", sid, "--kind", "finding", "--text", "Clicks steady", "--evidence", evidence, *self.scope)
        closed = self.cli("close", sid, "--outcome", "done", *self.scope)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertTrue(self.check(sid)["ok"])

    def test_review_fixes(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        # Another environment, where gsc looks unavailable, can neither judge nor close the session.
        for args in (("check", sid), ("close", sid, "--outcome", "done")):
            other = self.cli(*args, "--org", "acme", "--env", "uat")
            self.assertEqual(other.returncode, 1, args)
            self.assertIn("session belongs to acme/staging", other.stderr)
        # A help page or a parent command does not meet a check.
        self.cli("run", "gsc", "--session", sid, *self.scope, "--", "performance", "--help")
        self.cli("run", "gsc", "--session", sid, *self.scope, "--", "insights", "-o", "json")
        rows = {r["id"]: r["status"] for r in self.check(sid)["workflow"]["checks"]}
        self.assertEqual((rows["gsc-totals"], rows["opportunities"]), ("todo", "todo"))
        # Findings cannot be skipped; an unknown skip id is reported.
        self.cli("note", sid, "--kind", "question", "--text", "skip findings: nothing to say", *self.scope)
        self.cli("note", sid, "--kind", "question", "--text", "skip gsc-totalz: typo", *self.scope)
        result = self.check(sid)
        self.assertEqual({r["id"]: r["status"] for r in result["workflow"]["checks"]}["findings"], "todo")
        self.assertIn("gsc-totalz", " ".join(result["warnings"]))
        # A value is substituted once, as written.
        question = workflows.start(orgs.workspace(self.home, "acme"), "growth-review", {"site": "sc-domain:{week}", "week": "2026-01-05"})[0]
        self.assertEqual(question, "Growth review for sc-domain:{week}, week of 2026-01-05")

    def test_bad_org_recipes_fail_with_a_reason(self):
        base = {"id": "mine", "title": "x", "question": "y"}
        for broken, message in ((dict(base, checks=["text"]), "unique id"),
                                (dict(base, params={"a": {"pattern": "("}}, checks=[{"id": "f", "text": "t", "findings": 1}]), "pattern"),
                                (dict(base, checks=[{"id": "f", "text": "t", "findings": "1"}]), "whole number"),
                                (dict(base, checks=[{"id": "f", "text": "t", "findings": 0}]), "at least 1"),
                                (dict(base, checks=[{"id": "c", "text": "t", "any": [{"provider": "gsc", "command": []}]}]), "needs any"),
                                (dict(base, budget="5", checks=[{"id": "f", "text": "t", "findings": 1}]), "budget")):
            with self.assertRaisesRegex(ValueError, message):
                workflows.validate(broken, "mine.json")

    def test_an_org_can_replace_a_recipe(self):
        root = orgs.workspace(self.home, "acme")
        write_json(root / "workflows" / "growth-review.json", {
            "id": "growth-review", "title": "Ours", "question": "Our review", "checks": [{"id": "only", "text": "x", "findings": 1}]})
        self.assertEqual(workflows.recipes(root)["growth-review"]["title"], "Ours")
        write_json(root / "workflows" / "broken.json", {"id": "Bad Id", "title": "x", "question": "y", "checks": []})
        listed = self.cli("workflows", *self.scope)
        self.assertEqual(listed.returncode, 1)
        self.assertIn("broken.json", listed.stderr)


if __name__ == "__main__":
    unittest.main()
