"""reckon run --cache: identical recent reads are reused, labelled and never hold secrets. Offline."""
import json
import os
import subprocess
import unittest

from helpers import JENKINS, OrgCase, SOURCE
from reckonlib import orgs


class CacheTests(OrgCase):
    def test_output_limit_hides_a_secret_cut_off_mid_value(self):
        sid = self.native_session()
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                self.fake("import os,sys\nsys." + stream + ".write('result: ' + os.environ['JENKINS_TOKEN'] + '\\nmore output')\n", "jenkins")
                result = self.run_("--max-bytes", "20", "--session", sid)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertNotIn("jenkins-toke", result.stdout + result.stderr)
        session = orgs.workspace(self.home, "acme") / "sessions" / sid
        for path in (session / "evidence").glob("*/*.txt"):
            self.assertNotIn("jenkins-toke", path.read_text())

    def test_timeout_hides_a_secret_cut_off_mid_value(self):
        self.fake("import os,sys,time\nsys.stdout.write('result: ' + os.environ['JENKINS_TOKEN'][:12])\nsys.stdout.flush()\ntime.sleep(5)\n", "jenkins")
        sid = self.native_session()
        result = self.run_("--timeout", "0.2", "--session", sid)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(result.stdout, "result: [REDACTED]")
        session = orgs.workspace(self.home, "acme") / "sessions" / sid
        for path in (session / "evidence").glob("*/*.txt"):
            self.assertNotIn("jenkins-toke", path.read_text())

    def test_verify_hides_a_secret_cut_off_in_its_error_detail(self):
        self.fake("import os,sys\nsys.stderr.write('x' * (262144 - 12) + os.environ['JENKINS_TOKEN'] + '\\n')\n", "jenkins")
        result = self.cli("verify", "jenkins-staging", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual(row["status"], "output_limit")
        self.assertTrue(row["detail"].endswith("[REDACTED]"))
        self.assertNotIn("jenkins-toke", result.stdout + result.stderr)

    def native_session(self):
        result = self.cli("task", "--question", "output bounds", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["session"]

    def test_redacted_results_and_evidence_share_the_requested_output_limit(self):
        self.secret("acme", "jenkins-staging", JENKINS[:2] + ["JENKINS_TOKEN=ABCD"])
        self.fake("import os,sys\ntext=os.environ['JENKINS_TOKEN'] * 4 + '\\n'\nsys.stdout.write(text)\nsys.stderr.write(text)\n", "jenkins")
        sid = self.native_session()
        results = [self.run_("--cache", "1h", "--max-bytes", "40", "--session", sid) for _ in range(2)]
        for result in results:
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertLessEqual(len(result.stdout.encode()), 40)
        self.assertEqual(results[0].stdout, results[1].stdout)
        session = orgs.workspace(self.home, "acme") / "sessions" / sid
        records = [json.loads(path.read_text()) for path in (session / "evidence").glob("*/record.json")]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["status"], "output_limit")
            self.assertEqual(record["completeness"], "partial")
            self.assertLessEqual(sum((session / part["path"]).stat().st_size for part in record["artifacts"].values()), 40)

    def test_utf8_replacement_obeys_the_requested_output_limit(self):
        self.fake("import sys\nsys.stdout.buffer.write(b'\\xff' * 20)\n", "jenkins")
        sid = self.native_session()
        result = self.run_("--max-bytes", "24", "--session", sid)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "\ufffd" * 8)
        session = orgs.workspace(self.home, "acme") / "sessions" / sid
        record = json.loads(next((session / "evidence").glob("*/record.json")).read_text())
        self.assertEqual(record["status"], "output_limit")
        self.assertEqual((session / record["artifacts"]["stdout"]["path"]).read_text(), result.stdout)

    def test_failed_saved_json_returns_a_partial_exit_status(self):
        sid = self.native_session()
        args = ("run", "jenkins", "--org", "acme", "--env", "staging", "--session", sid, "--", "job", "list", "-o", "json")
        for output in ("not JSON", '{"status":"error","error":"forbidden"}'):
            with self.subTest(output=output):
                self.fake("print(" + repr(output) + ")\n", "jenkins")
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.fake("import sys\nprint('{}')\nsys.exit(7)\n", "jenkins")
        self.assertEqual(self.cli(*args).returncode, 7)

    def test_cached_redaction_markers_remain_stable(self):
        self.secret("acme", "jenkins-staging", JENKINS[:2] + ["JENKINS_TOKEN=ACTE"])
        self.fake("import os,sys\nsys.stdout.write(os.environ['JENKINS_TOKEN'])\n", "jenkins")
        first = self.run_("--cache", "1h")
        second = self.run_("--cache", "1h")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, "[REDACTED]")
        self.assertEqual(second.stdout, first.stdout)
        sid = self.native_session()
        limited = self.run_("--cache", "1h", "--max-bytes", "8", "--session", sid)
        self.assertEqual(limited.returncode, 3, limited.stderr)
        self.assertEqual(limited.stdout, "[REDACTE")
        session = orgs.workspace(self.home, "acme") / "sessions" / sid
        record = json.loads(next((session / "evidence").glob("*/record.json")).read_text())
        self.assertEqual((session / record["artifacts"]["stdout"]["path"]).read_text(), limited.stdout)

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

    def test_changed_saved_profiles_and_tool_versions_do_not_reuse_cached_results(self):
        profile = self.home / "orgs/acme/config/jenkins-staging/jenkins-cli/config.yaml"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("server: https://first.fixture.invalid\n")
        self.assertEqual(self.run_("--cache", "1h").returncode, 0)
        profile.write_text("server: https://other.fixture.invalid\n")
        changed_profile = self.run_("--cache", "1h")
        self.assertEqual(changed_profile.returncode, 0, changed_profile.stderr)
        self.assertNotIn("cached result observed at", changed_profile.stderr)
        self.assertEqual(self.calls(), 2)
        self.fake("open(%r, 'a').write('x')\nprint('new version')\n" % str(self.counter), "jenkins")
        changed_tool = self.run_("--cache", "1h")
        self.assertEqual(changed_tool.returncode, 0, changed_tool.stderr)
        self.assertIn("new version", changed_tool.stdout)
        self.assertEqual(self.calls(), 3)

    def test_binary_stdin_is_part_of_the_exact_cache_identity(self):
        self.fake("import sys\nprint(sys.stdin.buffer.read().hex())\n", "jenkins")
        args = [os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon")), "run", "jenkins",
                "--org", "acme", "--env", "staging", "--cache", "1h", "--stdin", "--", "job", "list"]
        for data in (b"\xff", b"\xfe", b"\xff"):
            result = subprocess.run(args, input=data, capture_output=True, env=self.cli_env())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), data.hex().encode())

    def test_cached_reads_obey_the_current_output_limit(self):
        self.assertEqual(self.run_("--cache", "1h").returncode, 0)
        result = self.run_("--cache", "1h", "--max-bytes", "8")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertLessEqual(len(result.stdout.encode()), 8)
        self.assertIn("output_limit", result.stderr)
        self.assertEqual(self.calls(), 1)

    def test_cached_output_limits_preserve_utf8_boundaries(self):
        self.fake("print('€' * 20)\n", "jenkins")
        self.assertEqual(self.run_("--cache", "1h").returncode, 0)
        result = self.run_("--cache", "1h", "--max-bytes", "4")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "€")

    def test_invalid_limits_are_refused_even_on_cache_hits(self):
        self.assertEqual(self.run_("--cache", "1h").returncode, 0)
        for flag, value in (("--timeout", "0"), ("--max-bytes", "0"), ("--max-bytes", "8388609")):
            result = self.run_("--cache", "1h", flag, value)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("cached result observed at", result.stderr)
        self.assertEqual(self.calls(), 1)

    def test_json_escaping_cannot_create_an_unreadable_cache_entry(self):
        self.fake("import sys\nsys.stdout.write('\\x00' * (2 * 1048576))\n", "jenkins")
        for _ in range(2):
            result = self.run_("--cache", "1h", "--max-bytes", "3145728")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout), 2 * 1048576)

    def test_unreadable_or_malformed_cache_entries_are_replaced_by_fresh_reads(self):
        self.assertEqual(self.run_("--cache", "1h").returncode, 0)
        path = next((self.home / "orgs/acme/cache").glob("*.json"))
        for content in ("not JSON", "{}", " " * (8 * 1048576 + 1)):
            path.write_text(content)
            result = self.run_("--cache", "1h")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("cached result observed at", result.stderr)
            self.assertIn("observed_at", json.loads(path.read_text()))
        self.assertEqual(self.calls(), 4)


if __name__ == "__main__":
    unittest.main()
