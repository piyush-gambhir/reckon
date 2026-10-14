"""reckon run consults a CLI's own resolver (agent-cli contract) before running it. Offline."""
import os
import shutil
import unittest

from helpers import OrgCase, REGISTRY
from reckonlib import connectors

FAKE = '''import json, os, sys
args = sys.argv[1:]
if args[:2] == ["commands", "resolve"]:
    rest = args[3:]
    if os.environ.get("OLD"):
        print(json.dumps({"error": 'unknown command "resolve" for "gsc commands"', "kind": "usage"}), file=sys.stderr); sys.exit(1)
    table = {"token": ("gsc auth token", "read", True), "add": ("gsc sites add", "remote_write", False)}
    hit = next((v for k, v in table.items() if k in rest), ("gsc top queries", "read", False))
    print(json.dumps({"command": hit[0], "effect": hit[1], "secret_output": hit[2], "flags": {}, "args": []}))
else:
    open(os.environ["TMPDIR"] + "/ran", "a").write(" ".join(args) + "\\n")
    print("ran")
'''


class ResolverTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"gsc": {"provider": "gsc"}}, secrets={"gsc": ["GSC_ACCESS_TOKEN=t"]})
        self.fake(FAKE, "gsc")

    def run_(self, *args, **env):
        return self.cli("run", "gsc", "--org", "acme", "--env", "staging", "--", *args, **env)

    def test_reads_run_and_writes_or_credential_output_are_refused(self):
        self.assertEqual(self.run_("top", "queries", "--last", "7d").returncode, 0)
        token = self.run_("auth", "status", "token")
        self.assertEqual(token.returncode, 1)
        self.assertIn("prints a credential", token.stderr)
        write = self.run_("sites", "list", "add")
        self.assertIn("effect remote_write", write.stderr)

    def test_an_older_cli_without_a_resolver_falls_back_to_registry_checks(self):
        self.assertEqual(self.run_("top", "queries", OLD="1").returncode, 0)
        self.assertEqual(self.run_("auth", "token", OLD="1").returncode, 1)  # still denied by the registry

    @unittest.skipUnless(os.environ.get("RECKON_REAL_GSC") and shutil.which(os.environ.get("RECKON_REAL_GSC", "")), "set RECKON_REAL_GSC to a gsc >= 0.1.8")
    def test_real_gsc_resolver(self):
        spec = dict(REGISTRY["gsc"], binary=os.environ["RECKON_REAL_GSC"])
        env = {"PATH": os.environ["PATH"], "TMPDIR": str(self.dir), "HOME": str(self.dir), "GSC_NO_UPDATE_NOTIFIER": "1"}
        self.assertEqual(connectors.resolved(spec, env, ["top", "queries", "--last", "7d"])["effect"], "read")
        for args in (["auth", "-o", "json", "token"], ["sites", "add", "https://x.com/"], ["export", "--out", "d"]):
            with self.assertRaises(ValueError):
                connectors.resolved(spec, env, args)


if __name__ == "__main__":
    unittest.main()


class RealResolverTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RECKON_REAL_BWT") and os.environ.get("RECKON_REAL_CLARITY"), "set RECKON_REAL_BWT and RECKON_REAL_CLARITY")
    def test_bwt_and_clarity_resolvers(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        env = {"PATH": os.environ["PATH"], "TMPDIR": tmp, "HOME": tmp, "BWT_NO_UPDATE_NOTIFIER": "1", "CLARITY_NO_UPDATE_NOTIFIER": "1"}
        for name, binary, read, write in (("bing", os.environ["RECKON_REAL_BWT"], ["sites", "list"], ["-o", "json", "submit", "urls", "https://x.com/a"]),
                                          ("clarity", os.environ["RECKON_REAL_CLARITY"], ["export", "--days", "1"], ["auth", "logout"])):
            spec = dict(REGISTRY[name], binary=binary)
            self.assertEqual(connectors.resolved(spec, env, read)["effect"], "read")
            with self.assertRaises(ValueError):
                connectors.resolved(spec, env, write)
