"""Workflow recipes: parameters, checks judged from evidence, closing rules. Offline, fake gsc."""
import json
import os
from pathlib import Path
import unittest

from helpers import OrgCase
from reckonlib import orgs, workflows
from reckonlib.store import write_json


class WorkflowTests(OrgCase):
    def test_native_previous_review_uses_finish_time_and_stays_in_the_org(self):
        first = self.start("site=sc-domain:acme.com")["session"]
        second = self.start("site=sc-domain:acme.com")["session"]
        root = orgs.workspace(self.home, "acme")
        for sid, created, closed in ((first, "2026-10-01T10:00:00Z", "2026-10-02T23:00:00-05:00"),
                                     (second, "2026-10-02T12:00:00Z", "2026-10-03T03:30:00Z")):
            self.finish(sid)
            result = self.cli("close", sid, "--outcome", "done", *self.scope)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = root / "sessions" / sid / "session.json"
            data = json.loads(path.read_text())
            path.write_text(json.dumps(dict(data, created_at=created, closed_at=closed)))
        next_review = self.start("site=sc-domain:acme.com")
        self.assertEqual(next_review["previous"]["session"], first)
        for sid in (first, second):
            path = root / "sessions" / sid / "session.json"
            saved = json.loads(path.read_text())
            path.write_text(json.dumps(dict(saved, closed_at="2026-10-05T00:00:00Z")))
        # Equal recorded finish times use the same deterministic tie in both views.
        next_review = self.start("site=sc-domain:acme.com")
        brief = self.cli("brief", *self.scope, "--json")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        review = next(row for row in json.loads(brief.stdout)["reviews"] if row["workflow"] == "growth-review")
        self.assertEqual(next_review["previous"]["session"], max(first, second))
        self.assertEqual(review["session"], max(first, second))
        foreign = dict(data, id="s-foreign", org="other", created_at="2026-10-06T00:00:00Z", closed_at="2026-10-06T01:00:00Z")
        write_json(root / "sessions/s-foreign/session.json", foreign)
        next_review = self.start("site=sc-domain:acme.com")
        self.assertEqual(next_review["previous"]["session"], max(first, second))

    def test_native_previous_summary_refuses_changed_context(self):
        first = self.start("site=sc-domain:acme.com")["session"]
        self.finish(first)
        self.assertEqual(self.cli("close", first, "--outcome", "done", *self.scope).returncode, 0)
        second = self.start("site=sc-domain:acme.com")["session"]
        path = orgs.workspace(self.home, "acme") / "sessions" / first / "session.json"
        original = json.loads(path.read_text())
        action_path = path.parents[2] / "actions/a-outside/action.json"
        action = {"schema_version": 1, "id": "a-outside", "org": "acme", "environment": "staging", "connection": "gh",
                  "operation": "github.issue.create", "session": first, "status": "prepared", "prepared_at": "2026-10-01T00:00:00Z",
                  "params": {"repo": "acme/api", "title": "Fixture issue", "body": "Synthetic fixture."}, "receipt": {"url": "https://outside.fixture.invalid/1"}}
        write_json(action_path, action)
        resumed = self.cli("resume", second, *self.scope, "--json")
        self.assertEqual(len(json.loads(resumed.stdout)["previous"]["writes"]), 1)
        brief = self.cli("brief", *self.scope, "--json")
        self.assertEqual(len(json.loads(brief.stdout)["writes_waiting_for_approval"]), 1)
        for changed in (dict(action, org="other"), dict(action, environment="uat"), dict(action, id="a-wrong")):
            write_json(action_path, changed)
            resumed = self.cli("resume", second, *self.scope, "--json")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["previous"]["writes"], [])
            self.assertNotIn("outside.fixture.invalid", resumed.stdout)
            brief = self.cli("brief", *self.scope, "--json")
            self.assertEqual(brief.returncode, 0, brief.stderr)
            self.assertEqual(json.loads(brief.stdout)["writes_waiting_for_approval"], [])
        for key, value in (("org", "other"), ("environment", "uat")):
            with self.subTest(key=key):
                changed = dict(original, **{key: value}, question="foreign-context-marker")
                changed["notes"] = [dict(note, text="foreign-context-marker") for note in original["notes"]]
                content = json.dumps(changed)
                path.write_text(content)
                resumed = self.cli("resume", second, *self.scope, "--json")
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                previous = json.loads(resumed.stdout)["previous"]
                self.assertEqual(previous["status"], "unavailable")
                self.assertTrue(previous["actions"])
                self.assertNotIn("foreign-context-marker", resumed.stdout)
                self.assertEqual(path.read_text(), content)

    def test_native_history_orders_instants_and_refuses_foreign_org_sessions(self):
        root = orgs.workspace(self.home, "acme")
        for sid, stamp, org in (("20261001T000000Z-ffff", "2026-10-01T23:00:00-05:00", "acme"),
                                ("20261002T000000Z-0000", "2026-10-02T03:30:00Z", "acme"),
                                ("20261003T000000Z-ffff", "2026-10-03T00:00:00Z", "other"),
                                ("20261004T000000Z-ffff", "invalid", "acme")):
            write_json(root / "sessions" / sid / "session.json", {"schema_version": 1, "id": sid, "org": org,
                       "environment": "staging", "mode": "task", "status": "open", "created_at": stamp,
                       "question": "foreign-context-marker" if org == "other" else sid, "notes": []})
        result = self.cli("history", *self.scope, "--limit", "1", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["id"] for row in json.loads(result.stdout)], ["20261001T000000Z-ffff"])
        incident = root / "incidents/2026-10-03-foreign"
        for metadata in ({"org": "other"}, [], "invalid"):
            write_json(incident / "session.json", metadata)
            (incident / "RCA.md").write_text("# foreign-context-marker\n")
            result = self.cli("history", *self.scope, "--all-envs", "--query", "foreign-context-marker", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [])
        path = root / "sessions/20261003T000000Z-ffff/session.json"
        content = path.read_text()
        for arguments in (("resume",), ("report",), ("note", "--kind", "question", "--text", "local note")):
            result = self.cli(arguments[0], "20261003T000000Z-ffff", *arguments[1:], *self.scope, "--json")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("foreign-context-marker", result.stdout)
            self.assertEqual(path.read_text(), content)

    def test_previous_review_marks_claims_with_failed_evidence(self):
        first = self.start("site=sc-domain:acme.com")["session"]
        self.finish(first)
        result = self.cli("close", first, "--outcome", "done", *self.scope)
        self.assertEqual(result.returncode, 0, result.stderr)
        second = self.start("site=sc-domain:acme.com")["session"]
        root = orgs.workspace(self.home, "acme")
        record_path = next((root / "sessions" / first / "evidence").glob("*/record.json"))
        content = json.dumps(dict(json.loads(record_path.read_text()), status="failed"))
        record_path.write_text(content)
        result = self.cli("resume", second, *self.scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = json.loads(result.stdout)["previous"]
        self.assertTrue(previous["actions"], previous)
        self.assertIn("Unsupported", " ".join(previous["warnings"]))
        self.assertIn("failed", " ".join(previous["warnings"]))
        report = self.cli("report", second, *self.scope, "--json")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("Unsupported", Path(json.loads(report.stdout)["report"]).read_text())
        self.assertEqual(record_path.read_text(), content)
        (record_path.parent / "stdout.txt").write_text("damaged output")
        resumed = self.cli("resume", second, *self.scope, "--json")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        previous = json.loads(resumed.stdout)["previous"]
        self.assertTrue(previous["actions"], previous)
        self.assertIn("integrity", " ".join(previous["warnings"]))

    def test_gsc_quality_is_visible_in_saved_evidence_checks_and_reports(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        period = {"start": "2026-10-01", "end": "2026-10-07", "data_state": "final",
                  "complete": True, "row_count": 1, "metadata_returned": False}
        payloads = [
            dict(period, complete=False, rows=[{"clicks": 10}]),
            {"current": dict(period, data_state="all", first_incomplete_date="2026-10-07"),
             "previous": dict(period, complete=False), "rows": [{"clicks": 10}]},
            {"sources": [dict(period, data_state="hourly_all")], "rows": []},
        ]
        for label, payload in zip(("result", "comparison", "insight"), payloads):
            self.fake("print(" + repr(json.dumps(payload)) + ")\n", "gsc")
            result = self.cli("run", "gsc", "--session", sid, "--label", label, *self.scope, "--", "performance",
                              "--site", "sc-domain:acme.com", "-o", "json")
            self.assertEqual(result.returncode, 0, result.stderr)
        resumed = json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)
        records = resumed["evidence"]
        self.assertEqual(len(records), 3)
        by_output = {r["label"]: r for r in records}
        self.assertEqual(by_output["result"]["completeness"], "partial")
        self.assertEqual(by_output["comparison"]["completeness"], "partial")
        self.assertIn("preliminary", " ".join(by_output["insight"]["data_quality"]["warnings"]))
        warnings = " ".join(self.check(sid)["warnings"])
        self.assertIn("pagination", warnings)
        self.assertIn("preliminary", warnings)
        report = json.loads(self.cli("report", sid, *self.scope, "--json").stdout)["report"]
        with open(report) as source:
            rendered = source.read()
        self.assertIn("preliminary", rendered)
        self.assertIn("America/Los_Angeles", rendered)

    @unittest.skipIf(os.name == "nt", "Unix directory symlink fixture")
    def test_previous_review_summary_does_not_follow_redirected_actions(self):
        first = self.start("site=sc-domain:acme.com")["session"]
        self.finish(first, actions=())
        closed = self.cli("close", first, "--outcome", "done", *self.scope)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        second = self.start("site=sc-domain:acme.com")["session"]
        root = orgs.workspace(self.home, "acme")
        outside = self.dir / "outside"
        outside.mkdir()
        content = json.dumps({"session": first, "id": "a-outside", "status": "applied", "operation": "github.issue.create",
                              "receipt": {"url": "https://outside.fixture.invalid/issue/1"}})
        (outside / "action.json").write_text(content)
        (root / "actions").mkdir(exist_ok=True)
        (root / "actions/a-outside").symlink_to(outside, target_is_directory=True)
        result = self.cli("resume", second, *self.scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["previous"]["writes"], [])
        self.assertIn("action receipts", " ".join(json.loads(result.stdout)["previous"]["warnings"]))
        self.assertNotIn("outside.fixture.invalid", result.stdout)
        self.assertEqual((outside / "action.json").read_text(), content)

    def test_damaged_workflow_snapshots_cannot_pass_or_close_a_review(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        path = orgs.workspace(self.home, "acme") / "sessions" / sid / "session.json"
        original = json.loads(path.read_text())
        invalid = [False, "invalid", {}, dict(original["workflow"], checks=[]),
                   dict(original["workflow"], checks=[{"id": "f", "text": "t", "findings": "1"}]),
                   dict(original["workflow"], report={"min_actions": -1})]
        for workflow in invalid:
            with self.subTest(workflow=workflow):
                content = json.dumps(dict(original, workflow=workflow))
                path.write_text(content)
                for args in (("check", sid), ("close", sid, "--outcome", "done")):
                    result = self.cli(*args, *self.scope, "--json")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("workflow", result.stderr)
                    self.assertEqual(path.read_text(), content)

    def test_successful_close_refuses_a_finding_that_fails_integrity_checks(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        self.finish(sid, actions=())
        path = orgs.workspace(self.home, "acme") / "sessions" / sid / "session.json"
        data = json.loads(path.read_text())
        finding = next(note for note in data["notes"] if note["kind"] == "finding")
        finding["evidence"] = ["e-missing"]
        content = json.dumps(data)
        path.write_text(content)
        checked = self.cli("check", sid, *self.scope, "--json")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        for outcome in ("done", "diagnosed"):
            result = self.cli("close", sid, "--outcome", outcome, *self.scope)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("missing evidence", result.stderr)
            self.assertEqual(path.read_text(), content)

    def test_damaged_previous_actions_cannot_remove_followup_requirements(self):
        sid = self.start("site=sc-domain:acme.com")["session"]
        self.finish(sid, actions=())
        path = orgs.workspace(self.home, "acme") / "sessions" / sid / "session.json"
        data = json.loads(path.read_text())
        for previous in ("invalid", {}, {"session": "prior", "actions": "invalid"},
                         {"session": "prior", "actions": [{}]}):
            with self.subTest(previous=previous):
                content = json.dumps(dict(data, previous=previous))
                path.write_text(content)
                checked = self.cli("check", sid, *self.scope, "--json")
                self.assertEqual(checked.returncode, 1, checked.stderr)
                self.assertIn("previous", checked.stderr)
                closed = self.cli("close", sid, "--outcome", "done", *self.scope)
                self.assertEqual(closed.returncode, 1, closed.stderr)
                self.assertEqual(path.read_text(), content)

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
