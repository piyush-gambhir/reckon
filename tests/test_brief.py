"""reckon brief and session knowledge snapshots. Offline."""
import json
import unittest

from helpers import JENKINS, OrgCase
from reckonlib import investigation as work
from reckonlib import orgs
from reckonlib.store import create, write, write_json


class BriefTests(OrgCase):
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
            write_json(self.root / "actions" / aid / "action.json", {"id": aid, "status": status, "operation": "github.issue.create",
                                                                     "connection": "gh", "environment": "staging", "params": {"repo": "acme/api"}})
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
