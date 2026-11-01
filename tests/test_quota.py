"""Daily API quotas counted per connection (Clarity's 10 export requests). Offline, fake clarity."""
import json
from datetime import datetime, timedelta, timezone
import unittest

from helpers import OrgCase
from reckonlib import connectors, orgs


class QuotaTests(OrgCase):
    def test_native_malformed_quota_cannot_reset_the_daily_allowance(self):
        calls = self.dir / "calls"
        self.fake("from pathlib import Path\nPath(" + repr(str(calls)) + ").touch()\nprint('[]')\n", "clarity")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        invalid = [[], {}, {"day": today}, {"day": "invalid", "used": 10}, {"day": future, "used": 10}]
        invalid += [{"day": today, "used": value} for value in ("10", -1, 1.5, True, None)]
        for data in invalid:
            with self.subTest(data=data):
                content = json.dumps(data)
                self.ledger.write_text(content)
                result = self.cli("run", "clarity", "--org", "acme", "--env", "staging", "--", "export", "--days", "1")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("quota.json", result.stderr)
                self.assertEqual(self.ledger.read_text(), content)
                self.assertFalse(calls.exists())

    def test_native_quota_rolls_over_only_a_valid_previous_day(self):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps({"day": "2020-01-01", "used": 10}))
        args = ("run", "clarity", "--org", "acme", "--env", "staging", "--", "export", "--days", "1")
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.ledger.read_text())
        self.assertEqual(data["used"], 1)
        data["used"] = 10
        self.ledger.write_text(json.dumps(data))
        result = self.cli(*args)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("used its 10", result.stderr)
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 10)

    def setUp(self):
        super().setUp()
        self.org(connections={"clarity": {"provider": "clarity"}}, secrets={"clarity": ["CLARITY_API_TOKEN=clarity-token-1"]})
        self.fake("print('[]')", "clarity")
        self.ledger = orgs.org_path(self.home, "acme") / "runtime" / "clarity" / "quota.json"

    def run_clarity(self, *args, cache=0):
        return connectors.run(self.home, self.ctx(), "clarity", list(args), self.registry, cache_seconds=cache)

    def test_exports_are_counted_and_refused_when_spent(self):
        notes = [self.run_clarity("export", "--days", "1")["quota_note"] for _ in range(10)]
        self.assertEqual(notes[:7], [None] * 7)
        self.assertEqual(notes[-1], "clarity: 0 of 10 Export API requests left today (UTC)")
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 10)
        with self.assertRaisesRegex(ValueError, "used its 10 Export API requests"):
            self.run_clarity("export", "--days", "2")
        # Calls that do not reach the Export API, and cache hits, spend nothing.
        self.run_clarity("export", "dimensions")
        self.run_clarity("auth", "status")
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 10)

    def test_cache_hits_and_verify_share_the_ledger(self):
        self.run_clarity("export", "--days", "1", cache=600)
        self.run_clarity("export", "--days", "1", cache=600)
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 1)
        rows = connectors.verify(self.home, self.ctx(), self.registry)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 2)
        # Yesterday's count does not carry over.
        self.ledger.write_text(json.dumps({"day": "2020-01-01", "used": 10}))
        self.assertIsNone(self.run_clarity("export", "--days", "1")["quota_note"])
        self.assertEqual(json.loads(self.ledger.read_text())["used"], 1)


if __name__ == "__main__":
    unittest.main()
