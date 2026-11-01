"""Goals bound to reads, reported by the brief from saved evidence. Offline."""
import hashlib
import json
import unittest

from helpers import OrgCase
from reckonlib import goals, orgs
from reckonlib import investigation as work
from reckonlib.store import create, write_json

GOALS = {"goals": [
    {"id": "clicks", "title": "Organic clicks (28 days)", "provider": "gsc", "command": ["performance"], "path": "current.clicks", "target": 5000},
    {"id": "p95", "title": "Checkout p95 ms", "provider": "cubeapm", "command": ["metrics", "query"], "path": "data.0.value",
     "target": 800, "direction": "down"}]}


class GoalTests(OrgCase):
    def test_native_goal_reads_keep_coverage_warnings(self):
        self.secret("acme", "gsc", ["GSC_ACCESS_TOKEN=fixture-goal-token"])
        payload = {"current": {"clicks": 5200, "start": "2026-10-01", "end": "2026-10-07",
                               "complete": False, "data_state": "all", "row_count": 1}}
        self.fake("print(" + repr(json.dumps(payload)) + ")\n", "gsc")
        scope = ("--org", "acme", "--env", "staging")
        made = self.cli("task", "--question", "Goal coverage", *scope, "--json")
        self.assertEqual(made.returncode, 0, made.stderr)
        sid = json.loads(made.stdout)["session"]
        read = self.cli("run", "gsc", "--session", sid, *scope, "--", "performance", "--site", "sc-domain:acme.com", "-o", "json")
        self.assertEqual(read.returncode, 0, read.stderr)
        result = self.cli("brief", *scope, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        goal = json.loads(result.stdout)["goals"][0]
        self.assertEqual((goal["value"], goal["status"], goal["completeness"]), (5200, "on track", "partial"))
        self.assertIn("pagination", " ".join(goal["warnings"]))
        self.assertIn("preliminary", " ".join(goal["warnings"]))
        self.assertTrue(goal["data_quality"]["sources"])
        rendered = self.cli("brief", *scope).stdout
        self.assertIn("Coverage:", rendered)
        self.assertIn("partial", rendered)
        self.assertIn("preliminary", rendered)

    def test_native_newest_goal_read_uses_instants_and_rejects_invalid_times(self):
        records = [self.read({"current": {"clicks": value}}) for value in (4100, 5200)]
        stamps = ("2026-10-01T23:00:00-05:00", "2026-10-02T03:30:00Z")
        paths = []
        for record, stamp in zip(records, stamps):
            path = self.root / "sessions" / record["session"] / "evidence" / record["id"] / "record.json"
            path.write_text(json.dumps(dict(record, observed_at=stamp)))
            paths.append(path)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        goal = json.loads(result.stdout)["goals"][0]
        self.assertEqual((goal["value"], goal["evidence"]), (4100, records[0]["id"]))
        for stamp in ("not-a-time", "2026-10-02T04:00:00", None):
            with self.subTest(stamp=stamp):
                content = json.dumps(dict(records[0], observed_at=stamp))
                paths[0].write_text(content)
                result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
                goal = json.loads(result.stdout)["goals"][0]
                self.assertEqual(goal["status"], "invalid")
                self.assertNotIn("value", goal)
                self.assertEqual(paths[0].read_text(), content)

    def test_native_goal_comparisons_preserve_large_integer_precision(self):
        record = self.read({"current": {"clicks": 0}})
        folder = self.root / "sessions" / record["session"] / "evidence" / record["id"]
        cases = [(9007199254740992, 9007199254740993, "up", "behind"),
                 (9007199254740993, 9007199254740992.0, "down", "behind"),
                 (-9007199254740993, -9007199254740992, "up", "behind"),
                 (18446744073709551615, 18446744073709551614, "down", "behind"),
                 (9007199254740992.0, 9007199254740993, "up", "behind"),
                 (0.5, 0, "up", "on track"), (-0.5, 0, "up", "behind"),
                 (1e300, 18446744073709551615, "up", "on track"),
                 (-1e300, -9223372036854775808, "up", "behind")]
        for value, target, direction, expected in cases:
            with self.subTest(value=value, target=target, direction=direction):
                output = json.dumps({"current": {"clicks": value}}).encode()
                (folder / "stdout.txt").write_bytes(output)
                saved = dict(record)
                saved["artifacts"]["stdout"].update(bytes=len(output), sha256=hashlib.sha256(output).hexdigest())
                (folder / "record.json").write_text(json.dumps(saved))
                write_json(self.root / "knowledge/_shared/goals.json",
                           {"goals": [dict(GOALS["goals"][0], target=target, direction=direction)]})
                result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                goal = json.loads(result.stdout)["goals"][0]
                self.assertEqual((goal["value"], goal["status"]), (value, expected))

    def test_native_goal_reads_refuse_foreign_org_metadata(self):
        record = self.read({"current": {"clicks": 5200}})
        folder = self.root / "sessions" / record["session"]
        session = json.loads((folder / "session.json").read_text())
        (folder / "session.json").write_text(json.dumps(dict(session, org="other")))
        path = folder / "evidence" / record["id"] / "record.json"
        content = json.dumps(dict(record, org="other"))
        path.write_text(content)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        goal = json.loads(result.stdout)["goals"][0]
        self.assertEqual(goal["status"], "invalid")
        self.assertNotIn("value", goal)
        self.assertEqual(path.read_text(), content)

    def test_native_goal_matches_do_not_accept_nested_or_positional_assignments(self):
        write_json(self.root / "knowledge/_shared/goals.json",
                   {"goals": [dict(GOALS["goals"][0], match=["sc-domain:acme.com", "28d"])]})
        wanted = self.read({"current": {"clicks": 4100}},
                           ("gsc", "performance", "--site=sc-domain:acme.com", "--last=28d", "-o", "json"))
        for extra in (("--filter=prefix=sc-domain:acme.com", "--filter=prefix=28d"),
                      ("prefix=sc-domain:acme.com", "prefix=28d"),
                      ("sc-domain:acme.com=other", "28d=other")):
            self.read({"current": {"clicks": 99999}},
                      ("gsc", "performance", "--site=sc-domain:other.com", "--last=1d", *extra, "-o", "json"))
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        goal = json.loads(result.stdout)["goals"][0]
        self.assertEqual((goal["value"], goal["evidence"]), (4100, wanted["id"]))

    def test_native_malformed_goal_state_is_visible_in_the_brief(self):
        path = self.root / "knowledge/_shared/goals.json"
        for data in ([], {}, {"goals": "invalid"},
                     *({"goals": [dict(GOALS["goals"][0], direction=value)]} for value in (True, None, 1))):
            with self.subTest(data=data):
                content = json.dumps(data)
                path.write_text(content)
                result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = json.loads(result.stdout)["goals"]
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0]["status"], "invalid")
                self.assertEqual(path.read_text(), content)

    def setUp(self):
        super().setUp()
        self.org(connections={"gsc": {"provider": "gsc"}})
        self.root = orgs.workspace(self.home, "acme")
        write_json(self.root / "knowledge" / "_shared" / "goals.json", GOALS)

    def read(self, stdout, argv=("gsc", "performance", "--site", "sc-domain:acme.com", "-o", "json")):
        _, session = create(self.root, "acme", "staging", "Weekly review", "task")
        return work.record_run(self.root, session["id"], self.ctx(), {
            "argv": list(argv), "connection": "gsc", "provider": "gsc", "values": {},
            "result": {"status": "ok", "exit_code": 0, "stdout": json.dumps(stdout).encode(), "stderr": b"", "duration_ms": 1}})

    def test_brief_reports_goals_from_the_newest_read(self):
        rows = {r["goal"]: r for r in goals.report(self.root, "staging", self.registry)}
        self.assertEqual((rows["clicks"]["status"], rows["p95"]["status"]), ("no reading", "no reading"))
        self.read({"current": {"clicks": 4100}})
        newest = self.read({"current": {"clicks": 5200}})
        rows = {r["goal"]: r for r in goals.report(self.root, "staging", self.registry)}
        self.assertEqual((rows["clicks"]["value"], rows["clicks"]["status"], rows["clicks"]["evidence"]), (5200, "on track", newest["id"]))
        out = json.loads(self.cli("brief", "--org", "acme", "--env", "staging", "--json").stdout)
        self.assertEqual(out["goals"][0]["status"], "on track")
        text = self.cli("brief", "--org", "acme", "--env", "staging").stdout
        self.assertIn("Organic clicks (28 days): 5,200 vs target 5,000, on track", text)

    def test_a_missing_number_or_a_bad_file_is_reported(self):
        self.read({"current": {"clicks": "n/a"}})
        self.assertEqual(goals.report(self.root, "staging", self.registry)[0]["status"], "unreadable")
        # Another environment's reads never count.
        self.assertEqual(goals.report(self.root, "uat", self.registry)[0]["status"], "no reading")
        write_json(self.root / "knowledge" / "_shared" / "goals.json", {"goals": [{"id": "x", "title": "x", "provider": "gsc",
                                                                                   "command": ["performance"], "path": "a b", "target": 1}]})
        self.assertEqual(goals.report(self.root, "staging", self.registry)[0]["status"], "invalid")
        self.assertEqual(self.cli("brief", "--all-orgs").returncode, 0)

    def test_match_keeps_other_reads_out(self):
        write_json(self.root / "knowledge" / "_shared" / "goals.json", {"goals": [dict(GOALS["goals"][0], match=["sc-domain:acme.com", "28d"])]})
        self.read({"current": {"clicks": 5200}}, ("gsc", "performance", "--site", "sc-domain:acme.com", "--last", "28d", "-o", "json"))
        self.read({"current": {"clicks": 90}}, ("gsc", "performance", "--site", "sc-domain:other.com", "--last", "28d", "-o", "json"))
        self.assertEqual(goals.report(self.root, "staging", self.registry)[0]["value"], 5200)

    def test_native_brief_refuses_to_report_tampered_goal_evidence(self):
        record = self.read({"current": {"clicks": 4100}})
        artifact = self.root / "sessions" / record["session"] / "evidence" / record["id"] / "stdout.txt"
        artifact.write_text(json.dumps({"current": {"clicks": 99999}}))
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["goals"][0]
        self.assertEqual(row["status"], "invalid")
        self.assertNotIn("value", row)
        self.assertIn("integrity", row["next"])


if __name__ == "__main__":
    unittest.main()
