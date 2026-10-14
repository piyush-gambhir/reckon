"""reckon run --cache: identical recent reads are reused, labelled and never hold secrets. Offline."""
import json
import unittest

from helpers import JENKINS, OrgCase
from reckonlib import orgs


class CacheTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"}, "jenkins-uat": {"provider": "jenkins", "env": "uat"}},
                 secrets={"jenkins-staging": JENKINS, "jenkins-uat": JENKINS})
        self.counter = self.dir / "calls"
        self.fake("import os, sys\nopen(%r, 'a').write('x')\nprint('token ' + os.environ['JENKINS_TOKEN'] + ' ' + ' '.join(sys.argv[1:]))\n" % str(self.counter), "jenkins")

    def run_(self, *extra, env="staging"):
        return self.cli("run", "jenkins", "--org", "acme", "--env", env, *extra, "--", "job", "list")

    def calls(self):
        return len(self.counter.read_text()) if self.counter.exists() else 0

    def test_identical_reads_are_reused_within_the_age(self):
        first = self.run_("--cache", "15m")
        second = self.run_("--cache", "15m")
        self.assertEqual(self.calls(), 1)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("cached result observed at", second.stderr)
        self.run_()
        self.assertEqual(self.calls(), 2)  # without --cache, always a fresh read

    def test_cache_is_per_environment_and_never_stores_secrets(self):
        self.run_("--cache", "1h")
        self.run_("--cache", "1h", env="uat")
        self.assertEqual(self.calls(), 2)
        stored = "".join(p.read_text() for p in (self.home / "orgs/acme/cache").glob("*.json"))
        self.assertNotIn("jenkins-token-123", stored)
        self.assertIn("[REDACTED]", stored)

    def test_cached_evidence_keeps_its_real_observation_time(self):
        sid = json.loads(self.cli("task", "--question", "q", "--org", "acme", "--env", "staging", "--json").stdout)["session"]
        self.run_("--cache", "1h")
        entry = json.loads(next((self.home / "orgs/acme/cache").glob("*.json")).read_text())
        self.run_("--cache", "1h", "--session", sid)
        record = json.loads(next((orgs.workspace(self.home, "acme") / "sessions" / sid / "evidence").glob("*/record.json")).read_text())
        self.assertEqual(record["observed_at"], entry["observed_at"])

    def test_bad_ages_are_refused(self):
        self.assertNotEqual(self.run_("--cache", "soon").returncode, 0)


if __name__ == "__main__":
    unittest.main()
