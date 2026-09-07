"""Offline acceptance tests. No production profiles or network are used."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
from reckonlib import environment as envs
from reckonlib import investigation as work
from reckonlib.demo import demo
from reckonlib.operations import build, plan
from reckonlib.process import run
from reckonlib.store import create, evidence, lock, read_json, resolve_service, services, session_path, window, write, write_json

BOUNDS = {"from": "2026-09-08T14:00:00Z", "to": "2026-09-08T14:30:00Z"}


class RepositoryBoundaryTests(unittest.TestCase):
    def test_tenant_state_is_never_tracked(self):
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=SOURCE).decode().split("\0")
        private_roots = {".config", ".reckon-env", ".reckon-demo", "infra-knowledge", "incidents", "sessions", "bin"}
        for name in filter(None, tracked):
            path = Path(name)
            self.assertNotIn(path.parts[0], private_roots, name)
            self.assertFalse(path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example", name)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reckon-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        shutil.copyfile(SOURCE / ".envrc", self.root / ".envrc")
        shutil.copyfile(SOURCE / "scripts/integrations.tsv", self.root / "scripts/integrations.tsv")
        (self.root / "bin").mkdir()
        write(self.root / ".reckon-env", "staging")
        self.mapping = {"schema_version": 1, "services": [{"id": "checkout", "aliases": ["checkout-api"],
            "provenance": {"source": "test fixture", "observed_at": "2026-09-08T00:00:00Z"},
            "sources": {"cubeapm": {"service": "checkout", "metrics": {"latency_ms": "latency{service=$service}"}}}}]}
        self.save_mapping()
        self.credentials()

    def save_mapping(self):
        write_json(self.root / "infra-knowledge/staging/services.json", self.mapping)

    def credentials(self, extra=""):
        write(self.root / ".env.staging", "PATH='" + str(self.root / "bin") + ":/usr/bin:/bin'\n" +
              "CUBEAPM_HOST=fixture.invalid\nCUBEAPM_EMAIL=test@fixture.invalid\nCUBEAPM_PASSWORD=secret-for-test\n" + extra)

    def fake(self, source, binary="cubeapm"):
        write(self.root / "bin" / binary, "#!" + sys.executable + "\n" + source)
        (self.root / "bin" / binary).chmod(0o700)

    def session(self):
        return create(self.root, "staging", resolve_service(self.root, "staging", "checkout"), "Why is checkout slow?", BOUNDS, "latency", "debug")

    def collect(self, sid, **kwargs):
        return work.collect(self.root, sid, "staging", **kwargs)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(SOURCE / "scripts/reckon.py"), "--root", str(self.root), *args],
                              capture_output=True, text=True, env={k: v for k, v in os.environ.items() if not k.startswith("RECKON_")})


class EnvironmentTests(Fixture):
    def test_same_shell_switch_clears_credentials_and_aliases(self):
        write(self.root / ".env.production", "GRAFANA_TOKEN=production-secret\nCUBEAPM_HOST=production.invalid\nEXTRA_CREDENTIAL=old\n")
        script = '. "$RECKON_ROOT/.envrc" >/dev/null; export RECKON_ENV=staging; . "$RECKON_ROOT/.envrc" >/dev/null; printf "%s|%s|%s" "${GRAFANA_TOKEN-unset}" "$CUBEAPM_SERVER" "${EXTRA_CREDENTIAL-unset}"'
        for shell in ("bash", "zsh"):
            if not shutil.which(shell):
                continue
            with self.subTest(shell=shell):
                result = subprocess.run([shell, "-c", script], capture_output=True, text=True,
                                        env={"PATH": os.environ["PATH"], "RECKON_ROOT": str(self.root), "RECKON_ENV": "production"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "unset|fixture.invalid|unset")

    def test_invalid_switch_removes_previous_credentials(self):
        result = subprocess.run(["bash", "-c", '. "$RECKON_ROOT/.envrc" >/dev/null; RECKON_ENV=invalid; . "$RECKON_ROOT/.envrc" >/dev/null 2>&1; printf "%s" "${CUBEAPM_PASSWORD-unset}"'],
                                capture_output=True, text=True, env={"PATH": os.environ["PATH"], "RECKON_ROOT": str(self.root), "RECKON_ENV": "staging"})
        self.assertEqual(result.stdout, "unset")

    def test_fresh_process_and_provider_scoping(self):
        os.environ["GRAFANA_TOKEN"] = "outside-token"
        self.addCleanup(os.environ.pop, "GRAFANA_TOKEN", None)
        values = envs.load(self.root, "staging")
        self.assertNotIn("GRAFANA_TOKEN", values)
        self.assertNotIn("CUBEAPM_PASSWORD", envs.scope(values, "github"))
        self.assertEqual(values["CUBEAPM_SERVER"], "fixture.invalid")
        self.assertNotIn("KUBECONFIG", envs.scope(values, "github"))
        self.assertIn("KUBECONFIG", envs.scope(values, "kubernetes"))

    def test_read_only_cannot_be_disabled_in_environment_files(self):
        self.credentials("ES_READ_ONLY=false\n")
        self.assertEqual(envs.load(self.root, "staging")["ES_READ_ONLY"], "true")

    def test_runtime_home_cache_and_temp_are_provider_and_environment_scoped(self):
        outside = self.root / "operator-home"
        outside.mkdir()
        (outside / "global-credentials").write_text("outside-profile")
        values = envs.load(self.root, "staging")
        values["HOME"] = str(outside)
        staging = envs.scope(values, "cubeapm")
        production = envs.scope({**values, "RECKON_ENV": "production"}, "cubeapm")
        github = envs.scope(values, "github")
        self.assertNotEqual(staging["HOME"], production["HOME"])
        self.assertNotEqual(staging["HOME"], github["HOME"])
        probe = "import os; from pathlib import Path; assert not (Path.home()/'global-credentials').exists(); (Path(os.environ['TMPDIR'])/'marker').write_text('private')"
        result = run([sys.executable, "-c", probe], env=staging)
        self.assertEqual(result["status"], "ok", result["stderr"])
        for key in ("HOME", "USERPROFILE", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP"):
            directory = Path(staging[key])
            directory.relative_to(self.root / ".config/staging/runtime/cubeapm")
            if os.name != "nt":
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(values["HOME"], str(outside))
        self.assertEqual(list(outside.iterdir()), [outside / "global-credentials"])

    def test_profile_directory_symlink_is_rejected_before_activation(self):
        outside = self.root / "outside-config"
        outside.mkdir()
        (self.root / ".config").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            envs.load(self.root, "staging")
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_environment_is_explicit(self):
        (self.root / ".reckon-env").unlink()
        result = self.cli("services", "list", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("select an environment", result.stderr)

    def test_ambiguous_service_is_rejected(self):
        self.mapping["services"].append({**self.mapping["services"][0], "id": "checkout-v2"})
        self.save_mapping()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_service(self.root, "staging", "checkout-api")

    def test_mysql_placeholder_option_file_is_not_readiness(self):
        write(self.root / ".env.staging", "MYSQL_HOST=mysql.example.com\nMYSQL_USER=readonly\n")
        self.fake("pass", "mysql")
        values = envs.load(self.root, "staging")
        self.assertFalse(envs.readiness(self.root, values)["mysql"]["configured"])


class ProcessTests(unittest.TestCase):
    def test_timeout_is_bounded_without_external_timeout_tool(self):
        result = run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(result["duration_ms"], 2000)

    def test_output_limit_is_bounded_for_both_streams(self):
        result = run([sys.executable, "-c", "import os;\nwhile True: os.write(1,b'x'*8192); os.write(2,b'y'*8192)"], max_bytes=10000, timeout=2)
        self.assertEqual(result["status"], "output_limit")
        self.assertLessEqual(len(result["stdout"]) + len(result["stderr"]), 10000)

    def test_nonzero_exit_preserves_output(self):
        result = run([sys.executable, "-c", "print('partial'); raise SystemExit(7)"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertIn(b"partial", result["stdout"])

    @unittest.skipIf(os.name == "nt", "POSIX process-group guarantee")
    def test_child_with_inherited_pipe_cannot_outlive_deadline(self):
        result = run([sys.executable, "-c", "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])"], timeout=0.15)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(result["duration_ms"], 2000)

    @unittest.skipIf(os.name == "nt", "POSIX cancellation")
    def test_sigterm_cancels_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "pid"
            child = "import os,time; from pathlib import Path; Path(" + repr(str(marker)) + ").write_text(str(os.getpid())); time.sleep(10)"
            parent = "import sys; sys.path.insert(0," + repr(str(SOURCE / "scripts")) + "); from reckonlib.process import run; print(run([sys.executable,'-c'," + repr(child) + "] )['status'])"
            proc = subprocess.Popen([sys.executable, "-B", "-c", parent], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                proc.send_signal(signal.SIGTERM)
                stdout, stderr = proc.communicate(timeout=3)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertIn(b"cancelled", stdout)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


class WorkflowTests(Fixture):
    def test_offline_demo_exercises_real_collection_and_resume(self):
        (self.root / "examples/demo").mkdir(parents=True)
        shutil.copyfile(SOURCE / "examples/services.json", self.root / "examples/services.json")
        shutil.copyfile(SOURCE / "examples/demo/tool.py", self.root / "examples/demo/tool.py")
        output = demo(self.root)
        self.assertEqual(output["evidence_records"], 7)
        context = work.resume(Path(output["workspace"]), output["session"])
        self.assertEqual(len(context["session"]["notes"]), 2)
        self.assertIn("100 ms", Path(output["report"]).read_text())
        self.assertIn("850 ms", Path(output["report"]).read_text())
        self.assertEqual(context["session"]["status"], "open")
        self.assertFalse(output["network_used"])

    def test_collect_resume_notes_report_and_history(self):
        self.fake('import json; print(json.dumps({"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[[0,"100"],[1,"200"]]}]}}))')
        path, data = self.session()
        records = self.collect(data["id"])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["status"] == "ok" for r in records))
        self.assertEqual(records[0]["series"][0]["mean"], 150)
        self.assertNotEqual(records[0]["window"], records[1]["window"])
        self.assertEqual(records[0]["environment"], "staging")
        first = records[0]["id"]
        work.note(self.root, data["id"], "finding", "Baseline samples average 150", [first])
        work.note(self.root, data["id"], "hypothesis", "Deployment is a candidate, not established cause", [], "Check deployed SHA")
        context = work.resume(self.root, data["id"])
        self.assertEqual(len(context["session"]["notes"]), 2)
        text = work.report(self.root, data["id"]).read_text()
        self.assertIn("evidence/" + first + "/stdout.txt", text)
        self.assertIn("Check deployed SHA", text)
        self.assertEqual(work.history(self.root, "staging", "checkout")[0]["id"], data["id"])
        self.assertEqual(work.history(self.root, "production"), [])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / "session.json").stat().st_mode & 0o777, 0o600)

    def test_success_is_reused_and_refresh_is_explicit(self):
        self.fake('print("[]")')
        path, data = self.session()
        self.collect(data["id"])
        self.assertTrue(all(r["status"] == "reused" for r in self.collect(data["id"])))
        self.assertEqual(len(evidence(path)), 2)
        self.collect(data["id"], refresh=True)
        self.assertEqual(len(evidence(path)), 4)

    def test_missing_credentials_are_durable_gaps(self):
        write(self.root / ".env.staging", "PATH=/usr/bin:/bin\n")
        path, data = self.session()
        records = self.collect(data["id"])
        self.assertTrue(all(r["status"] == "unavailable" for r in records))
        self.assertEqual(len(work.resume(self.root, data["id"])["evidence"]), 2)
        with self.assertRaises(ValueError):
            work.note(self.root, data["id"], "finding", "Everything is healthy", [records[0]["id"]])

    def test_environment_mismatch_before_execution(self):
        self.fake("raise RuntimeError('must not execute')")
        path, data = self.session()
        with self.assertRaisesRegex(ValueError, "session belongs"):
            work.collect(self.root, data["id"], "production")
        self.assertEqual(evidence(path), [])

    def test_no_cross_session_evidence_references(self):
        self.fake('print("[]")')
        _, first = self.session()
        record = self.collect(first["id"])[0]
        _, second = self.session()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            work.note(self.root, second["id"], "finding", "Wrong evidence", [record["id"]])

    def test_secrets_redacted_from_artifacts_and_metadata(self):
        self.fake('import os,json; print(json.dumps({"message":os.environ["CUBEAPM_PASSWORD"]}))')
        path, data = self.session()
        records = self.collect(data["id"])
        for record in records:
            output = (path / record["artifacts"]["stdout"]["path"]).read_text()
            self.assertNotIn("secret-for-test", output)
            self.assertIn("[REDACTED]", output)
            self.assertNotIn("secret-for-test", json.dumps(record))

    def test_bad_output_and_provider_errors_are_not_success(self):
        for output in ('not-json', '{"status":"error","error":"forbidden"}', '{"data":{"resultType":"matrix","result":[null]}}'):
            self.fake("print(" + repr(output) + ")")
            _, data = self.session()
            records = self.collect(data["id"])
            self.assertTrue(all(r["status"] == "failed" for r in records))

    def test_interrupted_record_is_retained_when_collection_resumes(self):
        self.fake('print("[]")')
        path, data = self.session()
        record = self.collect(data["id"])[0]
        record["status"] = "running"
        write_json(path / "evidence" / record["id"] / "record.json", record)
        self.collect(data["id"])
        saved = {r["id"]: r for r in evidence(path)}
        self.assertEqual(saved[record["id"]]["status"], "interrupted")
        self.assertEqual(len(saved), 3)

    def test_copied_evidence_from_other_environment_is_rejected(self):
        self.fake('print("[]")')
        path, data = self.session()
        record = self.collect(data["id"])[0]
        record["environment"] = "production"
        write_json(path / "evidence" / record["id"] / "record.json", record)
        with self.assertRaisesRegex(ValueError, "environment mismatch"):
            work.resume(self.root, data["id"])

    def test_json_error_returns_cli_partial_status(self):
        self.fake('print("not-json")')
        _, data = self.session()
        result = self.cli("collect", data["id"], "--env", "staging", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(all(record["status"] == "failed" for record in json.loads(result.stdout)))

    def test_failed_collection_is_saved_and_retryable(self):
        self.fake("import time; time.sleep(10)")
        path, data = self.session()
        records = self.collect(data["id"], timeout=0.1)
        self.assertTrue(all(r["status"] == "timeout" for r in records))
        self.fake('print("[]")')
        self.assertTrue(all(r["status"] == "ok" for r in self.collect(data["id"])))
        self.assertEqual(len(evidence(path)), 4)

    def test_promote_is_snapshot_and_does_not_overwrite(self):
        self.fake('print("[]")')
        path, data = self.session()
        self.collect(data["id"])
        target = work.promote(self.root, data["id"], "checkout-latency")
        self.assertTrue((target / "RCA.md").exists())
        self.assertTrue((target / "learnings.md").exists())
        self.assertEqual(len(list((target / "evidence").glob("*/record.json"))), 2)
        with self.assertRaises(ValueError):
            work.promote(self.root, data["id"], "checkout-latency")

    def test_path_traversal_and_lock_contention_fail(self):
        with self.assertRaises(ValueError):
            session_path(self.root, "../../.env.production")
        path, _ = self.session()
        with lock(path):
            with self.assertRaises(OSError):
                with lock(path):
                    self.fail("second writer acquired lock")

    def test_evidence_parent_symlink_cannot_write_outside_session(self):
        self.fake('print("[]")')
        path, data = self.session()
        outside = self.root / "outside"
        outside.mkdir()
        (path / "evidence").rmdir()
        (path / "evidence").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.collect(data["id"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_metadata_and_lock_symlinks_are_rejected(self):
        for name in ("session.json", ".lock"):
            with self.subTest(name=name):
                path, data = self.session()
                outside = self.root / (name + "-outside")
                if name == "session.json":
                    (path / name).rename(outside)
                else:
                    outside.write_text("do not modify")
                    outside.chmod(0o644)
                before = outside.read_bytes(), outside.stat().st_mode
                (path / name).symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    work.note(self.root, data["id"], "question", "Check this", [])
                self.assertEqual((outside.read_bytes(), outside.stat().st_mode), before)

    def test_changed_evidence_cannot_be_reused_cited_reported_or_promoted(self):
        self.fake('print("[]")')
        path, data = self.session()
        record = self.collect(data["id"])[0]
        # Same byte count as the original []\n: this must check the hash too.
        (path / record["artifacts"]["stdout"]["path"]).write_text('{}\n')
        checks = [lambda: self.collect(data["id"]),
                  lambda: work.note(self.root, data["id"], "finding", "Unsupported", [record["id"]]),
                  lambda: work.report(self.root, data["id"]),
                  lambda: work.promote(self.root, data["id"], "changed")]
        for check in checks:
            with self.subTest(action=check), self.assertRaisesRegex(ValueError, "integrity"):
                check()

    def test_evidence_artifacts_must_use_their_own_paths(self):
        self.fake('print("[]")')
        path, data = self.session()
        record = self.collect(data["id"])[0]
        record["artifacts"]["stdout"]["path"] = "../../.env.staging"
        write_json(path / "evidence" / record["id"] / "record.json", record)
        with self.assertRaisesRegex(ValueError, "artifact path"):
            work.resume(self.root, data["id"])

    def test_missing_or_linked_artifact_cannot_be_cited(self):
        self.fake('print("[]")')
        for linked in (False, True):
            with self.subTest(linked=linked):
                path, data = self.session()
                record = self.collect(data["id"])[0]
                output = path / record["artifacts"]["stdout"]["path"]
                output.unlink()
                if linked:
                    output.symlink_to(self.root / ".env.staging")
                with self.assertRaisesRegex(ValueError, "integrity|symlink"):
                    work.note(self.root, data["id"], "finding", "Bad citation", [record["id"]])

    def test_empty_allocation_after_interruption_does_not_block_resume(self):
        path, data = self.session()
        (path / "evidence/e-allocated").mkdir()
        self.assertEqual(work.resume(self.root, data["id"])["evidence"], [])

    def test_promotion_rejects_symlinked_extra_files(self):
        path, data = self.session()
        (path / "extra.txt").symlink_to(self.root / ".env.staging")
        with self.assertRaisesRegex(ValueError, "symlink"):
            work.promote(self.root, data["id"], "linked")
        self.assertFalse(list((self.root / "incidents").glob("*")))

    def test_command_injection_stays_a_single_argument(self):
        marker = self.root / "should-not-exist"
        self.fake('import json,sys; print(json.dumps(sys.argv))')
        path, data = self.session()
        query = 'up; touch "' + str(marker) + '"'
        record = self.collect(data["id"], operation="cubeapm.metrics.range", params={"query": query})[0]
        self.assertFalse(marker.exists())
        self.assertIn(query, json.loads((path / record["artifacts"]["stdout"]["path"]).read_text()))

    def test_non_cubeapm_metric_source_uses_same_workflow(self):
        self.mapping["services"][0]["sources"] = {"grafana": {"datasource": "prometheus", "metrics": {"latency": "histogram_quantile(0.95, latency_bucket)"}}}
        self.save_mapping()
        self.credentials("GRAFANA_URL=https://fixture.invalid\nGRAFANA_TOKEN=test-token\n")
        self.fake('print(\'{"status":"success","data":{"resultType":"matrix","result":[]}}\')', "grafana")
        _, data = self.session()
        records = self.collect(data["id"])
        self.assertEqual(records[0]["capability"], "metrics.range")
        self.assertEqual(records[0]["completeness"], "empty")
        self.assertEqual(records[0]["provider"], "grafana")

    def test_cli_start_collect_and_close(self):
        self.fake('print("[]")')
        result = self.cli("debug", "--env", "staging", "--service", "checkout", "--question", "Why slow?", "--from", BOUNDS["from"], "--to", BOUNDS["to"], "--collect", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        sid = json.loads(result.stdout)["session"]
        self.assertNotEqual(self.cli("close", sid, "--outcome", "diagnosed").returncode, 0)
        self.assertEqual(self.cli("close", sid, "--outcome", "inconclusive").returncode, 0)

    def test_start_with_failed_collection_returns_partial_status(self):
        self.fake('raise SystemExit(1)')
        result = self.cli("debug", "--env", "staging", "--service", "checkout", "--question", "Why slow?", "--from", BOUNDS["from"], "--to", BOUNDS["to"], "--collect", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(json.loads(result.stdout)["collection"]["partial"])
        self.assertTrue(Path(json.loads(result.stdout)["report"]).exists())

    def test_operational_bounds_and_unknown_arguments(self):
        with self.assertRaises(ValueError):
            build("cubeapm.metrics.range", {"query": "up", "token": "no"}, BOUNDS, self.root)
        with self.assertRaises(ValueError):
            build("cubeapm.metrics.range", {"query": "up", "step": 1}, BOUNDS, self.root)
        with self.assertRaises(ValueError):
            build("git.show", {"path": str(self.root), "revision": "--output=bad"}, BOUNDS, self.root)
        with self.assertRaises(ValueError):
            window("2026-09-08T14:00:00", BOUNDS["to"])

    def test_trace_arguments_preserve_window_and_filter(self):
        command = build("cubeapm.traces.search", {"service": "checkout", "status": "error", "env_label": "STAG", "limit": 10}, BOUNDS, self.root)
        self.assertIn("STAG", command["argv"])
        self.assertIn(BOUNDS["from"], command["argv"])
        self.assertEqual(command["capability"], "traces.search")


if __name__ == "__main__":
    unittest.main()
