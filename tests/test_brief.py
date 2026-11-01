"""reckon brief and session knowledge snapshots. Offline."""
import json
import os
from pathlib import Path
import unittest

from helpers import JENKINS, SOURCE, OrgCase
from reckonlib import investigation as work
from reckonlib import orgs
from reckonlib.store import create, write, write_json


class BriefTests(OrgCase):
    def test_native_history_and_brief_reject_non_directory_state(self):
        commands = {
            "sessions": (("history", "--all-envs"), ("brief",), ("preflight",)),
            "actions": (("brief",),),
            "incidents": (("history", "--all-envs"), ("preflight",)),
        }
        for name, probes in commands.items():
            path = self.root / name
            if path.exists():
                path.rmdir()
            path.write_text("preserved state")
            for command in probes:
                with self.subTest(directory=name, command=command):
                    result = self.cli(*command, "--org", "acme", "--env", "staging", "--json")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(path.read_text(), "preserved state")
            path.unlink()
            if os.name != "nt":
                outside = self.dir / ("outside-" + name)
                outside.mkdir()
                path.symlink_to(outside, target_is_directory=True)
                for command in probes:
                    with self.subTest(directory=name, command=command, redirected=True):
                        result = self.cli(*command, "--org", "acme", "--env", "staging", "--json")
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertEqual(list(outside.iterdir()), [])
                path.unlink()
            path.mkdir()

    def test_native_preflight_reports_knowledge_directory_errors(self):
        for layer in ("staging", "_shared"):
            path = self.root / "knowledge" / layer
            if path.exists():
                path.rmdir()
            write(path, "preserved knowledge state")
            result = self.cli("preflight", "--org", "acme", "--env", "staging", "--json")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(path.read_text(), "preserved knowledge state")
            path.unlink()
            path.mkdir()

    def test_native_preflight_counts_only_regular_knowledge_documents(self):
        path = self.root / "knowledge/staging"
        (path / "directory.md").mkdir(parents=True)
        (path / "directory.json").mkdir()
        result = self.cli("preflight", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["knowledge"]["staging"], 0)
        write(path / "fact.md", "A synthetic fact.")
        write_json(path / "fixture.json", {"fixture": True})
        write(path / "ignored.txt", "Not a knowledge document.")
        if os.name != "nt":
            outside = self.dir / "outside.md"
            outside.write_text("An outside document.")
            (path / "linked.md").symlink_to(outside)
        result = self.cli("preflight", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["knowledge"]["staging"], 2)

    def test_native_previous_review_qualifies_unreadable_action_history(self):
        _, prior = create(self.root, "acme", "staging", "Prior review", "task")
        followups = [{"id": "a-follow", "text": "Follow up on the prior finding."}]
        _, current = create(self.root, "acme", "staging", "Current review", "task", previous={"session": prior["id"], "actions": followups})
        path = self.root / "actions"
        path.write_text("preserved action history")
        for regular_file in (True, False):
            if not regular_file:
                path.unlink()
                write(path / "a-damaged/action.json", "{invalid JSON")
            result = self.cli("resume", current["id"], "--org", "acme", "--env", "staging", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            previous = json.loads(result.stdout)["previous"]
            self.assertEqual(previous["actions"], followups)
            self.assertEqual(previous["writes"], [])
            self.assertIn("action receipts", " ".join(previous["warnings"]))
            report = self.cli("report", current["id"], "--org", "acme", "--env", "staging", "--json")
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertIn("action receipts", Path(json.loads(report.stdout)["report"]).read_text())
            if regular_file:
                self.assertEqual(path.read_text(), "preserved action history")
            else:
                self.assertEqual((path / "a-damaged/action.json").read_text(), "{invalid JSON")

    def test_native_findings_keep_citation_failures_visible(self):
        path, session = create(self.root, "acme", "staging", "Citation integrity", "task")
        record = work.record_run(self.root, session["id"], self.ctx(), {
            "argv": ["jenkins", "job", "list"], "connection": "jenkins-staging", "provider": "jenkins", "values": {},
            "result": {"status": "ok", "exit_code": 0, "stdout": b"[]", "stderr": b"", "duration_ms": 1}})
        work.note(self.root, session["id"], "finding", "A recorded conclusion", [record["id"]])
        record_path = path / "evidence" / record["id"] / "record.json"
        record_path.write_text(json.dumps(dict(record, status="failed")))
        scope = ("--org", "acme", "--env", "staging")
        for arguments in (("brief",), ("report", session["id"])):
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments, *scope, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                if arguments[0] == "brief":
                    finding = data["recent_findings"][0]
                    self.assertFalse(finding["citations"]["ok"])
                    self.assertIn("failed", " ".join(finding["citations"]["problems"]))
                    rendered = self.cli("brief", *scope).stdout
                else:
                    rendered = Path(data["report"]).read_text()
                self.assertIn("A recorded conclusion", rendered)
                self.assertIn("Unsupported", rendered)
                self.assertIn("failed", rendered)
        # Artifact damage must qualify a brief rather than validate an old claim.
        record_path.write_text(json.dumps(record))
        (record_path.parent / "stdout.txt").write_text("[{}]")
        result = self.cli("brief", *scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        finding = json.loads(result.stdout)["recent_findings"][0]
        self.assertFalse(finding["citations"]["ok"])
        self.assertIn("integrity", " ".join(finding["citations"]["problems"]))

    def test_native_findings_qualify_missing_and_partial_evidence(self):
        path, session = create(self.root, "acme", "staging", "Coverage integrity", "task")
        record = work.record_run(self.root, session["id"], self.ctx(), {
            "argv": ["jenkins", "job", "list"], "connection": "jenkins-staging", "provider": "jenkins", "values": {},
            "result": {"status": "ok", "exit_code": 0, "stdout": b"[]", "stderr": b"", "duration_ms": 1}})
        work.note(self.root, session["id"], "finding", "A limited conclusion", [record["id"]])
        record_path = path / "evidence" / record["id"] / "record.json"
        record_path.write_text(json.dumps(dict(record, completeness="partial", data_quality={"warnings": ["Synthetic sampling warning."]})))
        scope = ("--org", "acme", "--env", "staging")
        result = self.cli("brief", *scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        citations = json.loads(result.stdout)["recent_findings"][0]["citations"]
        self.assertTrue(citations["ok"])
        self.assertIn("partial", " ".join(citations["warnings"]))
        self.assertIn("sampling", " ".join(citations["warnings"]))
        result = self.cli("report", session["id"], *scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        for rendered in (self.cli("brief", *scope).stdout, Path(json.loads(result.stdout)["report"]).read_text()):
            self.assertIn("Coverage:", rendered)
            self.assertIn("sampling", rendered)
            self.assertNotIn("Unsupported", rendered)
        data = json.loads((path / "session.json").read_text())
        data["notes"][0]["evidence"] = ["e-missing"]
        (path / "session.json").write_text(json.dumps(data))
        result = self.cli("brief", *scope, "--json")
        self.assertIn("missing evidence", " ".join(json.loads(result.stdout)["recent_findings"][0]["citations"]["problems"]))
        # A foreign org or mismatched session ID cannot contribute local findings.
        for changed in (dict(data, org="other"), dict(data, id="s-other")):
            (path / "session.json").write_text(json.dumps(changed))
            result = self.cli("brief", *scope, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["recent_findings"], [])

    @unittest.skipIf(os.name == "nt", "Unix directory symlink fixture")
    def test_native_brief_refuses_a_redirected_session_folder(self):
        _, data = create(self.root, "acme", "staging", "A local task", "task")
        outside = self.dir / "outside"
        outside.mkdir()
        content = json.dumps(dict(data, id="s-linked", question="An outside task"))
        (outside / "session.json").write_text(content)
        (self.root / "sessions/s-linked").symlink_to(outside, target_is_directory=True)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("symlink", result.stderr)
        self.assertNotIn("An outside task", result.stdout)
        self.assertEqual((outside / "session.json").read_text(), content)

    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"}, "grafana": {"provider": "grafana"}},
                 secrets={"jenkins-staging": JENKINS})
        self.fake("print('{}')", "jenkins")
        self.root = orgs.workspace(self.home, "acme")

    def test_brief_lists_open_work_writes_and_gaps(self):
        path, session = create(self.root, "acme", "staging", "Weekly growth review", "task")
        record = work.record_run(self.root, session["id"], self.ctx(), {
            "argv": ["jenkins", "job", "list"], "connection": "jenkins-staging", "provider": "jenkins", "values": {},
            "result": {"status": "ok", "exit_code": 0, "stdout": b"[]", "stderr": b"", "duration_ms": 1}})
        work.note(self.root, session["id"], "finding", "Signups fell 12% week on week", [record["id"]])
        for status, aid in (("prepared", "a-1"), ("unknown", "a-2"), ("applied", "a-3")):
            write_json(self.root / "actions" / aid / "action.json", {"schema_version": 1, "id": aid, "org": "acme", "status": status, "operation": "github.issue.create",
                                                                     "prepared_at": "2026-10-01T00:00:00Z", "connection": "gh", "environment": "staging", "params": {"repo": "acme/api", "title": "Fixture issue", "body": "Synthetic fixture."}})
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual([s["question"] for s in out["open_sessions"]], ["Weekly growth review"])
        self.assertEqual([f["text"] for f in out["recent_findings"]], ["Signups fell 12% week on week"])
        self.assertEqual([a["id"] for a in out["writes_waiting_for_approval"]], ["a-1"])
        self.assertEqual([a["id"] for a in out["writes_with_unknown_outcome"]], ["a-2"])
        self.assertEqual(out["connections_needing_attention"], ["grafana: no_cli"])
        text = self.cli("brief", "--org", "acme", "--env", "staging").stdout
        self.assertIn("# Brief: acme / staging", text)
        self.assertIn("a-2", text)

    def test_reviews_show_when_each_series_last_finished(self):
        flow = {"id": "growth-review", "series": ["site"], "params": {"site": "sc-domain:acme.com", "week": "2026-10-05"}}
        for status, closed, other in (("done", "2026-10-06T09:00:00Z", {}), ("done", "2026-10-13T09:00:00Z", {}), ("open", None, {}),
                                      ("done", "2026-10-14T09:00:00Z", {"site": "sc-domain:other.com"})):
            _, data = create(self.root, "acme", "staging", "review", "task", workflow=dict(flow, params=dict(flow["params"], **other)))
            path = self.root / "sessions" / data["id"] / "session.json"
            write_json(path, dict(data, status=status, closed_at=closed))
        # An unreadable session and a malformed write are passed over.
        write(self.root / "sessions" / "20200101T000000Z-broken0000" / "session.json", "{not json")
        write_json(self.root / "actions" / "a-x" / "action.json", [])
        write_json(self.root / "actions" / "a-j" / "action.json", {"schema_version": 1, "id": "a-j", "org": "acme", "prepared_at": "2026-10-01T00:00:00Z", "status": "prepared", "operation": "jira.issue.create",
                                                                   "connection": "jira", "environment": "staging",
                                                                   "params": {"project": "ABC", "type": "Task", "summary": "Fix titles"}})
        # Hand-edited workflow data is passed over, dates that cannot be read count as no date, and
        # values that print alike stay separate series.
        for odd in ({"workflow": dict(flow, series=None)}, {"workflow": dict(flow, series="site")}, {"workflow": dict(flow, id=["x"])},
                    {"closed_at": "not-a-date"}, {"closed_at": "2026-10-13"},
                    {"workflow": {"id": "custom", "series": ["a", "b"], "params": {"a": "x b=y", "b": "z"}}},
                    {"workflow": {"id": "custom", "series": ["b", "a"], "params": {"a": "x", "b": "y b=z"}}}):
            _, data = create(self.root, "acme", "staging", "odd", "task", workflow=flow)
            write_json(self.root / "sessions" / data["id"] / "session.json", dict(dict(data, status="done", closed_at="2026-10-01T00:00:00Z"), **odd))
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(sum(r["workflow"] == "custom" for r in out["reviews"]), 2)
        rows = {r["series"]: r for r in out["reviews"] if r["workflow"] == "growth-review"}
        self.assertEqual(rows["site=sc-domain:acme.com"]["last_done"], "2026-10-13T09:00:00Z")
        self.assertEqual(len(rows["site=sc-domain:acme.com"]["open"]), 1)
        self.assertEqual(rows["site=sc-domain:other.com"]["last_done"], "2026-10-14T09:00:00Z")
        self.assertEqual([a["target"] for a in out["writes_waiting_for_approval"]], ["ABC: Fix titles"])
        text = self.cli("brief", "--org", "acme", "--env", "staging").stdout
        self.assertIn("[staging] growth-review site=sc-domain:acme.com: last done 2026-10-13", text)

    def test_reckon_md_lists_the_writes_this_org_can_prepare(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["gh"] = {"provider": "github"}
        orgs.save(self.home, data, self.registry)
        from reckonlib import workspace
        text = workspace.overview(self.home, self.ctx(), self.registry)
        self.assertIn("## Writes you can prepare", text)
        self.assertIn("- `github.issue.create` via `gh`: repo, title, body (optional: labels)", text)
        self.assertNotIn("jira.issue.create", text)

    def test_sessions_record_and_compare_their_knowledge(self):
        write(self.root / "knowledge" / "_shared" / "company.md", "v1\n")
        _, session = create(self.root, "acme", "staging", "q", "task")
        self.assertIn("_shared/company.md", session["knowledge"])
        write(self.root / "knowledge" / "_shared" / "company.md", "v2\n")
        write(self.root / "knowledge" / "staging" / "new.md", "x\n")
        changes = work.resume(self.root, session["id"])["knowledge_changes"]
        self.assertEqual(changes["changed"], ["_shared/company.md"])
        self.assertIn("staging/new.md", changes["added"])


if __name__ == "__main__":
    unittest.main()


class DoctorVersionTests(OrgCase):
    def test_doctor_reports_cli_versions_older_than_the_pin(self):
        self.fake("print('grafana-cli version v0.2.5-12-gabc')", "grafana")
        self.fake("print('jenkins version v9.9.9')", "jenkins")
        out = self.cli("doctor", "--json")
        checks = json.loads(out.stdout)["checks"]
        self.assertTrue(any(c.startswith("grafana 0.2.5 is older than the pinned") for c in checks), checks)
        self.assertFalse(any(c.startswith("jenkins") and "older" in c for c in checks))

    def test_the_version_line_wins_over_the_latest_release(self):
        # gsc prints the newest release it knows of above its own version.
        self.fake("print('latest  0.1.6\\nversion 0.1.9')", "gsc")
        checks = json.loads(self.cli("doctor", "--json").stdout)["checks"]
        self.assertFalse(any(c.startswith("gsc") for c in checks), checks)
        self.fake("print('latest  0.1.9\\nversion 0.1.6')", "gsc")
        checks = json.loads(self.cli("doctor", "--json").stdout)["checks"]
        self.assertTrue(any(c.startswith("gsc 0.1.6 is older than the pinned v0.1.9") for c in checks), checks)

    def test_doctor_says_when_reckon_is_not_on_path(self):
        link = self.home / "bin" / "reckon"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(SOURCE / "scripts" / "reckon")
        checks = json.loads(self.cli("doctor", "--json").stdout)["checks"]
        self.assertIn("reckon command: " + str(link) + " is not on PATH; add to your shell rc: export PATH=\"" + str(link.parent) + ":$PATH\"", checks)
        on_path = self.cli("doctor", "--json", PATH=str(link.parent) + os.pathsep + os.environ["PATH"])
        self.assertIn("reckon command: " + str(link), json.loads(on_path.stdout)["checks"])
