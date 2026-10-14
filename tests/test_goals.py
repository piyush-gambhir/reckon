"""Goals bound to reads, reported by the brief from saved evidence. Offline."""
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


if __name__ == "__main__":
    unittest.main()
