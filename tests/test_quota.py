"""Daily API quotas counted per connection (Clarity's 10 export requests). Offline, fake clarity."""
import json
import unittest

from helpers import OrgCase
from reckonlib import connectors, orgs


class QuotaTests(OrgCase):
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
