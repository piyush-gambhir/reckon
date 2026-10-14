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
        self.assertEqual((rows["gsc-totals"], rows["bing"], rows["behaviour"]), ("todo", "unavailable", "unavailable"))
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
