"""Adopted third-party CLIs: entries are fail-closed allowlists. Offline."""
import unittest

from helpers import REGISTRY
from reckonlib import connectors

CASES = {
    "stripe": (["charges", "list", "--limit", "10"], [["charges", "create"], ["get", "/v1/charges"], ["charges", "list", "--api-key", "x"],
                                                      ["charges", "list", "--stripe-account", "acct_x"], ["listen"], ["login"]]),
    "razorpay": (["payments", "list", "--count", "10"], [["payments", "capture", "pay_x"], ["configure"], ["payments", "list", "--key-id", "x"]]),
    "gog": (["drive", "ls", "--max", "5", "--json"], [["gmail", "send"], ["drive", "upload", "f"], ["drive", "ls", "--account", "x"],
                                                      ["api", "GET", "/x"], ["auth", "add"], ["drive", "get", "id", "--out", "f"]]),
    "sentry": (["issue", "list", "--limit", "10"], [["auth", "token"], ["api", "/x"], ["issue", "list", "--token", "x"], ["issue", "resolve", "1"]]),
    "cloudflare": (["zones", "list"], [["dns", "records", "create"], ["deploy"], ["zones", "list", "--profile", "x"], ["accounts", "tokens", "list"]]),
}


class AdoptedTests(unittest.TestCase):
    def test_entries_allow_their_reads_and_refuse_writes_and_overrides(self):
        for name, (read, refused) in CASES.items():
            spec = REGISTRY[name]
            self.assertEqual(spec["mode"], "allowlist", name)
            connectors.check(name, spec, spec["verify"])
            connectors.check(name, spec, read)
            for args in refused:
                with self.assertRaises(ValueError, msg=(name, args)):
                    connectors.check(name, spec, args)


if __name__ == "__main__":
    unittest.main()
