"""Adopted third-party CLIs: entries are fail-closed allowlists. Offline.

RECKON_ADOPTED_BIN=<dir with stripe, linear, supabase, shopify...> also checks each
allowed read command against the real binary's --help (no account needed)."""
import os
import subprocess
import tempfile
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
    "linear": (["issue", "list", "--json", "-s", "started"], [["api", "query { viewer { id } }"], ["auth", "token"], ["issue", "create"],
                                                         ["issue", "list", "--workspace", "x"], ["issue", "view", "ENG-1", "-w"], ["config"]]),
    "resend": (["emails", "list", "--json"], [["emails", "send"], ["api-keys", "list"], ["logs", "get", "x"], ["emails", "list", "--api-key", "x"],
                                              ["domains", "list", "-p", "other"], ["broadcasts", "send", "b"]]),
    "shopify": (["store", "execute", "--query", "query { shop { name } }"], [["store", "execute", "--allow-mutations", "--query", "mutation {}"],
                                                                            ["store", "execute", "--store", "other.myshopify.com"], ["theme", "push"],
                                                                            ["app", "deploy"], ["store", "execute", "--query-file", "q.graphql"]]),
    "supabase": (["projects", "list", "-o", "json"], [["projects", "api-keys"], ["db", "push"], ["inspect", "db", "bloat", "--linked"],
                                                      ["migration", "list"], ["functions", "list", "--project-ref", "x"], ["link"]]),
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


@unittest.skipUnless(os.environ.get("RECKON_ADOPTED_BIN"), "set RECKON_ADOPTED_BIN to a folder of release binaries")
class RealBinaryTests(unittest.TestCase):
    def test_every_allowed_command_exists(self):
        folder, home = os.environ["RECKON_ADOPTED_BIN"], tempfile.mkdtemp()
        env = {"HOME": home, "XDG_CONFIG_HOME": home, "CI": "1", "NO_COLOR": "1", "PATH": folder + ":/usr/bin:/bin", "DO_NOT_TRACK": "1",
               "STRIPE_CLI_TELEMETRY_OPTOUT": "1", "SUPABASE_TELEMETRY_DISABLED": "1", "SHOPIFY_CLI_NO_ANALYTICS": "1"}
        checked = 0
        for name, spec in REGISTRY.items():
            binary = os.path.join(folder, spec["binary"])
            if spec.get("mode") != "allowlist" or not os.access(binary, os.X_OK):
                continue
            for words in spec["read_commands"]:
                if any("*" in w for w in words):
                    continue
                done = subprocess.run([binary] + words + ["--help"], env=env, capture_output=True, text=True, timeout=60)
                # Some CLIs print the parent's help for an unknown verb, so the full path must appear.
                text = " ".join((done.stdout + done.stderr).split()).lower()
                self.assertTrue(done.returncode == 0 and " ".join([spec["binary"]] + words).lower() in text, (name, words))
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
