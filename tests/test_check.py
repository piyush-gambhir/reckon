"""reckon check: deterministic quality checks on saved sessions. Offline."""
import json
import unittest

from helpers import JENKINS, OrgCase
from reckonlib import investigation as work
from reckonlib import orgs
from reckonlib.store import create, write, write_json


class CheckTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"}}, secrets={"jenkins-staging": JENKINS})
        self.root = orgs.workspace(self.home, "acme")
        _, self.session = create(self.root, "acme", "staging", "Release review", "task")
        self.sid = self.session["id"]

    def evidence(self, status="ok", stdout=b"[]"):
        return work.record_run(self.root, self.sid, self.ctx(), {
            "argv": ["jenkins", "build", "list", "-o", "json"], "connection": "jenkins-staging", "provider": "jenkins", "values": {},
            "result": {"status": status, "exit_code": 0 if status == "ok" else 1, "stdout": stdout, "stderr": b"", "duration_ms": 1}})

    def test_a_clean_session_passes(self):
        ref = self.evidence()["id"]
        work.note(self.root, self.sid, "finding", "Two deploys failed", [ref])
        work.note(self.root, self.sid, "hypothesis", "Flaky test", [], "Rerun the suite")
        result = work.check(self.root, self.sid)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["counts"]["findings"], 1)

    def test_problems_and_warnings_are_reported(self):
        work.note(self.root, self.sid, "hypothesis", "Flaky test", [])
        path = self.root / "sessions" / self.sid / "session.json"
        data = json.loads(path.read_text())
        data["status"] = "done"
        data["notes"].append({"id": "n-x", "kind": "finding", "text": "Unsupported claim", "evidence": [], "next_check": "", "recorded_at": "2026-10-14T00:00:00Z"})
        write_json(path, data)
        write(self.root / "knowledge" / "_shared" / "company.md", "changed\n")
        result = work.check(self.root, self.sid)
        self.assertFalse(result["ok"])
        self.assertTrue(any("without evidence" in p for p in result["problems"]))
        self.assertTrue(any("hypothesis without a next check" in w for w in result["warnings"]))
        self.assertTrue(any("knowledge changed" in w for w in result["warnings"]))
        cli = self.cli("check", self.sid, "--org", "acme", "--env", "staging")
        self.assertEqual(cli.returncode, 1)
        self.assertIn("problem:", cli.stdout)

    def test_tampered_evidence_fails_integrity(self):
        ref = self.evidence()["id"]
        (self.root / "sessions" / self.sid / "evidence" / ref / "stdout.txt").write_text("edited")
        result = work.check(self.root, self.sid)
        self.assertFalse(result["ok"])
        self.assertIn("integrity", result["problems"][0])


if __name__ == "__main__":
    unittest.main()
