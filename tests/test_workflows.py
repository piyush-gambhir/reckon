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
        # Actions must cite evidence, and the recipe caps how many a review ends with.
        self.assertEqual(self.cli("note", sid, "--kind", "action", "--text", "Rewrite titles", *self.scope).returncode, 1)
        for n in range(6):
            self.cli("note", sid, "--kind", "action", "--text", "Fix page " + str(n), "--evidence", evidence, *self.scope)
        too_many = self.cli("close", sid, "--outcome", "done", *self.scope)
        self.assertIn("report-actions", too_many.stderr)
        self.assertIn("--withdraw", too_many.stderr)
        sixth = [n["id"] for n in json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)["session"]["notes"] if n["text"] == "Fix page 5"][0]
        withdrawn = self.cli("note", sid, "--withdraw", sixth, "--text", "same page as action 4", *self.scope)
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        closed = self.cli("close", sid, "--outcome", "done", *self.scope)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        report = json.loads(self.cli("report", sid, *self.scope, "--json").stdout)["report"]
        with open(report) as text:
            self.assertIn("Fix page 0", text.read().split("## Recommended actions")[1])
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

    def finish(self, sid, actions=("Rewrite the pricing title", "Add FAQ schema")):
        """Close a growth review as done with one finding and the given actions."""
        run = self.cli("run", "gsc", "--session", sid, *self.scope, "--", "performance", "--site", "sc-domain:acme.com", "-o", "json")
        self.assertEqual(run.returncode, 0, run.stderr)
        evidence = self.check(sid)["workflow"]["checks"][0]["evidence"][0]
        for check in ("gsc-trend", "top-queries", "top-pages", "opportunities"):
            self.cli("note", sid, "--kind", "question", "--text", "skip " + check + ": not needed here", *self.scope)
        self.cli("note", sid, "--kind", "finding", "--text", "Clicks steady", "--evidence", evidence, *self.scope)
        ids = [json.loads(self.cli("note", sid, "--kind", "action", "--text", text, "--evidence", evidence, *self.scope, "--json").stdout)["id"]
               for text in actions]
        return evidence, ids

    def test_the_next_review_starts_from_the_last_one(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["gh"] = {"provider": "github"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "gh", ["GH_TOKEN=gh-token-1"])
        first = self.start("site=sc-domain:acme.com")
        self.assertNotIn("previous", first)
        _, ids = self.finish(first["session"])
        prepared = self.cli("act", "prepare", "github.issue.create", "--session", first["session"], *self.scope,
                            "--params", json.dumps({"repo": "acme/web", "title": "Add FAQ schema", "body": "From the growth review."}))
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        # An open review is not the last one: the next run waits for a finished review.
        self.assertNotIn("previous", self.start("site=sc-domain:acme.com"))
        self.assertEqual(self.cli("close", first["session"], "--outcome", "done", *self.scope).returncode, 0)
        # Another site is another series.
        self.assertNotIn("previous", self.start("site=sc-domain:other.com"))
        second = self.start("site=sc-domain:acme.com")
        last = second["previous"]
        self.assertEqual(last["session"], first["session"])
        self.assertEqual([a["id"] for a in last["actions"]], ids)
        self.assertEqual([(w["operation"], w["status"]) for w in last["writes"]], [("github.issue.create", "prepared")])
        self.assertIn("follow-up: each action from the last review", second["checks"])
        sid = second["session"]
        evidence, _ = self.finish(sid, actions=())
        # Only a finding can follow up, and only an action from the last review.
        for kind, ref, message in (("finding", "n-unknown", "not an action from this session's last review"),
                                   ("action", ids[0], "a follow-up is a finding")):
            refused = self.cli("note", sid, "--kind", kind, "--text", "x", "--evidence", evidence, "--follows", ref, *self.scope)
            self.assertEqual(refused.returncode, 1, kind)
            self.assertIn(message, refused.stderr)
        self.cli("note", sid, "--kind", "finding", "--text", "Pricing title rewritten; CTR up", "--evidence", evidence,
                 "--follows", ids[0], *self.scope)
        rows = {r["id"]: r for r in self.check(sid)["workflow"]["checks"]}
        self.assertEqual((rows["follow-up"]["status"], rows["follow-up"]["missing"]), ("todo", [ids[1]]))
        refused = self.cli("close", sid, "--outcome", "done", *self.scope)
        self.assertIn("follow-up", refused.stderr)
        self.cli("note", sid, "--kind", "finding", "--text", "FAQ schema not added yet", "--evidence", evidence, "--follows", ids[1], *self.scope)
        self.assertEqual(self.cli("close", sid, "--outcome", "done", *self.scope).returncode, 0)
        report = json.loads(self.cli("report", sid, *self.scope, "--json").stdout)["report"]
        with open(report) as text:
            since = text.read().split("## Since the last review")[1].split("## ")[0]
        self.assertIn("followed up: Pricing title rewritten; CTR up", since)
        self.assertIn("github.issue.create): prepared", since)
        # The chain continues: the third review follows the second, which had no actions of its own.
        third = self.start("site=sc-domain:acme.com")
        self.assertEqual((third["previous"]["session"], third["previous"]["actions"]), (sid, []))
        self.assertNotIn("follow-up: each action from the last review", third["checks"])

    def test_the_chain_survives_damage_and_follows_the_copy(self):
        first = self.start("site=sc-domain:acme.com")["session"]
        _, ids = self.finish(first)
        self.cli("close", first, "--outcome", "done", *self.scope)
        sessions = orgs.workspace(self.home, "acme") / "sessions"
        # Malformed sessions and action records are passed over, not fatal.
        broken = sessions / "20990101T000000Z-broken0000"
        broken.mkdir()
        write_json(broken / "session.json", {"schema_version": 1, "id": broken.name, "environment": "staging", "workflow": "x"})
        locked = sessions / "20200101T000000Z-locked0000"
        locked.mkdir()
        write_json(locked / "session.json", {})
        (locked / "session.json").chmod(0)
        self.addCleanup((locked / "session.json").chmod, 0o600)
        (orgs.workspace(self.home, "acme") / "actions" / "junk").mkdir(parents=True)
        write_json(orgs.workspace(self.home, "acme") / "actions" / "junk" / "action.json", [])
        second = self.start("site=sc-domain:acme.com")
        self.assertEqual(second["previous"]["session"], first)
        sid = second["session"]
        # An action added to the last run afterwards is not one this run must follow up, and is not shown as one.
        path = sessions / first / "session.json"
        data = json.loads(path.read_text())
        data["notes"].append(dict(data["notes"][-1], id="n-latecomer", text="Added later"))
        write_json(path, data)
        resumed = json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)
        self.assertEqual([a["id"] for a in resumed["previous"]["actions"]], ids)
        # A deleted last run leaves this run readable, from the copy taken when it started.
        import shutil
        shutil.rmtree(sessions / first)
        resumed = self.cli("resume", sid, *self.scope, "--json")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["previous"]["status"], "unavailable")
        self.assertEqual(self.cli("report", sid, *self.scope).returncode, 0)

    def test_runs_in_the_same_second_order_by_creation_time(self):
        root = orgs.workspace(self.home, "acme")
        for suffix, created, note in (("ffff", "2026-10-01T00:00:00.000001Z", "older"), ("0000", "2026-10-01T00:00:00.000002Z", "newer")):
            folder = root / "sessions" / ("20261001T000000Z-" + suffix + "000000")
            folder.mkdir(parents=True)
            write_json(folder / "session.json", {
                "schema_version": 1, "id": folder.name, "environment": "staging", "status": "done", "created_at": created,
                "workflow": {"id": "growth-review", "params": {"site": "sc-domain:acme.com"}},
                "notes": [{"id": "n-" + note, "kind": "action", "text": note}]})
        found = workflows.previous(root, "staging", {"id": "growth-review", "series": ["site"], "params": {"site": "sc-domain:acme.com"}})
        self.assertEqual(found["actions"][0]["text"], "newer")

    def test_bad_org_recipes_fail_with_a_reason(self):
        base = {"id": "mine", "title": "x", "question": "y"}
        for broken, message in ((dict(base, checks=["text"]), "unique id"),
                                (dict(base, params={"a": {"pattern": "("}}, checks=[{"id": "f", "text": "t", "findings": 1}]), "pattern"),
                                (dict(base, checks=[{"id": "f", "text": "t", "findings": "1"}]), "whole number"),
                                (dict(base, checks=[{"id": "f", "text": "t", "findings": 0}]), "at least 1"),
                                (dict(base, checks=[{"id": "c", "text": "t", "any": [{"provider": "gsc", "command": []}]}]), "needs any"),
                                (dict(base, budget="5", checks=[{"id": "f", "text": "t", "findings": 1}]), "budget"),
                                (dict(base, series=["site"], checks=[{"id": "f", "text": "t", "findings": 1}]), "series"),
                                (dict(base, checks=[{"id": "follow-up", "text": "t", "findings": 1}]), "reserved")):
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
