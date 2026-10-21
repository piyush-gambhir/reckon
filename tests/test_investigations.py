"""Offline acceptance tests for sessions, evidence and the saved workflow. No real tools or network are used."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import BOUNDS, CUBEAPM, OrgCase, SOURCE
from reckonlib import investigation as work
from reckonlib import orgs
from reckonlib.demo import demo
from reckonlib.operations import build
from reckonlib.process import run
from reckonlib.store import create, evidence, lock, read_json, resolve_service, services, session_path, window, write, write_json


class Fixture(OrgCase):
    """Org "acme" (staging, uat) with one shared CubeAPM connection and a mapped checkout service."""

    def setUp(self):
        super().setUp()
        self.org(connections={"cubeapm": {"provider": "cubeapm"}}, secrets={"cubeapm": CUBEAPM})
        self.root = orgs.workspace(self.home, "acme")
        self.mapping = {"schema_version": 1, "services": [{"id": "checkout", "aliases": ["checkout-api"],
            "provenance": {"source": "test fixture", "observed_at": "2026-09-08T00:00:00Z"},
            "sources": {"cubeapm": {"service": "checkout", "metrics": {"latency_ms": "latency{service=$service}"}}}}]}
        self.save_mapping()
        self.context = self.ctx("acme", "staging")

    def save_mapping(self):
        write_json(self.root / "knowledge/staging/services.json", self.mapping)

    def session(self, env="staging"):
        return create(self.root, "acme", env, "Why is checkout slow?", "debug", resolve_service(self.root, env, "checkout"), BOUNDS, "latency")

    def collect(self, sid, **kwargs):
        return work.collect(self.home, self.context, self.root, sid, registry=self.registry, **kwargs)

    def reload(self):
        """Re-read org.json after a test edits it."""
        self.context = self.ctx("acme", "staging")

    def cli_staging(self, *args, **extra):
        return self.cli(*args, "--org", "acme", "--env", "staging", **extra)


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
            parent = "import sys; sys.path.insert(0," + repr(str(SOURCE / "tests/reference")) + "); from reckonlib.process import run; print(run([sys.executable,'-c'," + repr(child) + "] )['status'])"
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
        output = demo(self.home)
        self.assertTrue(output["synthetic"])
        self.assertFalse(output["network_used"])
        self.assertEqual(output["evidence_records"], 7)
        demo_home = Path(output["home"])
        # The demo runs in a throwaway home: the operator's orgs are untouched.
        self.assertEqual(orgs.orgs(self.home), ["acme"])
        self.assertEqual(orgs.orgs(demo_home), ["demo"])
        context = work.resume(orgs.workspace(demo_home, "demo"), output["session"])
        self.assertEqual(len(context["session"]["notes"]), 2)
        self.assertEqual((context["session"]["org"], context["session"]["environment"]), ("demo", "staging"))
        text = Path(output["report"]).read_text()
        self.assertIn("100 ms", text)
        self.assertIn("850 ms", text)
        self.assertEqual(context["session"]["status"], "open")

    def test_collection_passes_the_clis_own_resolver_first(self):
        self.fake('import json, sys\nif sys.argv[1:3] == ["commands", "resolve"]:\n    print(json.dumps({"command": "cubeapm metrics", "effect": "remote_write"}))\nelse:\n    print("[]")')
        path, data = self.session()
        with self.assertRaisesRegex(ValueError, "remote_write"):
            self.collect(data["id"])
        self.assertEqual(evidence(path), [])  # refused before any read started

    def test_collection_is_in_the_activity_log(self):
        self.fake('print("[]")')
        _, data = self.session()
        self.collect(data["id"])
        from reckonlib import activity
        runs = activity.read(self.home, "acme", event="run")
        self.assertEqual({(e["target"], e["operation"], e["session"]) for e in runs}, {("cubeapm", "cubeapm.metrics.range", data["id"])})

    def test_withdrawn_finding_does_not_diagnose(self):
        self.fake('print("[]")')
        _, data = self.session()
        record = self.collect(data["id"])[0]
        note = work.note(self.root, data["id"], "finding", "Latency doubled", [record["id"]])
        work.withdraw(self.root, data["id"], note["id"], "Wrong service")
        self.assertEqual(self.cli_staging("close", data["id"], "--outcome", "diagnosed").returncode, 1)

    def test_collect_resume_notes_report_and_history(self):
        self.fake('import json; print(json.dumps({"status":"success","data":{"resultType":"matrix","result":[{"metric":{},"values":[[0,"100"],[1,"200"]]}]}}))')
        path, data = self.session()
        records = self.collect(data["id"])
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["status"] == "ok" for r in records))
        self.assertEqual(records[0]["series"][0]["mean"], 150)
        self.assertNotEqual(records[0]["window"], records[1]["window"])
        self.assertEqual((records[0]["org"], records[0]["environment"], records[0]["connection"]), ("acme", "staging", "cubeapm"))
        first = records[0]["id"]
        work.note(self.root, data["id"], "finding", "Baseline samples average 150", [first])
        work.note(self.root, data["id"], "hypothesis", "Deployment is a candidate, not established cause", [], "Check deployed SHA")
        context = work.resume(self.root, data["id"])
        self.assertEqual(len(context["session"]["notes"]), 2)
        text = work.report(self.root, data["id"]).read_text()
        self.assertIn("evidence/" + first + "/stdout.txt", text)
        self.assertIn("Check deployed SHA", text)
        self.assertTrue(text.startswith("# Investigation:"))
        self.assertIn("**Org:** acme", text)
        self.assertEqual(work.history(self.root, "staging", "checkout")[0]["id"], data["id"])
        self.assertEqual(work.history(self.root, "uat"), [])
        self.assertEqual(work.history(self.root, "staging", query="no-such-text"), [])
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
        self.secret("acme", "cubeapm", "")
        self.fake('print("[]")')
        path, data = self.session()
        records = self.collect(data["id"])
        self.assertTrue(all(r["status"] == "unavailable" for r in records))
        self.assertTrue(all(r["summary"] == "no_credentials" for r in records))
        self.assertEqual(len(work.resume(self.root, data["id"])["evidence"]), 2)
        with self.assertRaises(ValueError):
            work.note(self.root, data["id"], "finding", "Everything is healthy", [records[0]["id"]])

    def test_missing_cli_is_a_durable_gap(self):
        path, data = self.session()
        records = self.collect(data["id"])
        self.assertTrue(all(r["status"] == "unavailable" and r["summary"] == "no_cli" for r in records))

    def test_provider_without_a_connection_is_unavailable(self):
        self.fake("raise RuntimeError('must not execute')")
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"] = {}
        orgs.save(self.home, data, self.registry)
        self.reload()
        path, session = self.session()
        records = self.collect(session["id"])
        self.assertTrue(all(r["status"] == "unavailable" for r in records))
        self.assertTrue(all("reckon connect" in r["summary"] for r in records))
        self.assertTrue(all(r["connection"] is None for r in records))

    def test_connection_bound_to_another_environment_is_unavailable(self):
        self.fake("raise RuntimeError('must not execute')")
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"] = {"cubeapm-uat": {"provider": "cubeapm", "env": "uat"}}
        orgs.save(self.home, data, self.registry)
        self.reload()
        path, session = self.session()
        self.assertTrue(all(r["status"] == "unavailable" for r in self.collect(session["id"])))

    def test_environment_mismatch_before_execution(self):
        marker = self.dir / "ran"
        self.fake("open(" + repr(str(marker)) + ", 'w').write('x')")
        path, data = self.session()
        with self.assertRaisesRegex(ValueError, "session belongs"):
            work.collect(self.home, self.ctx("acme", "uat"), self.root, data["id"], registry=self.registry)
        self.assertEqual(evidence(path), [])
        self.assertFalse(marker.exists())

    def test_org_mismatch_before_execution(self):
        marker = self.dir / "ran"
        self.fake("open(" + repr(str(marker)) + ", 'w').write('x')")
        self.org("other", ("staging",), {"cubeapm": {"provider": "cubeapm"}}, {"cubeapm": CUBEAPM})
        path, data = self.session()
        with self.assertRaisesRegex(ValueError, "session belongs to acme/staging"):
            work.collect(self.home, self.ctx("other", "staging"), self.root, data["id"], registry=self.registry)
        self.assertEqual(evidence(path), [])
        self.assertFalse(marker.exists())

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
        self.assertNotIn("secret-for-test", (path / "evidence" / records[0]["id"] / "record.json").read_text())

    def test_child_gets_only_its_connection_credentials(self):
        os.environ.update(GH_TOKEN="shell-gh-token", JENKINS_TOKEN="shell-jenkins")
        self.fake('import os,json; print(json.dumps({k: os.environ.get(k) for k in ("GH_TOKEN","JENKINS_TOKEN","CUBEAPM_SERVER","RECKON_ORG","RECKON_ENV")}))')
        path, data = self.session()
        record = self.collect(data["id"], operation="cubeapm.metrics.range", params={"query": "up"})[0]
        seen = json.loads((path / record["artifacts"]["stdout"]["path"]).read_text())
        self.assertEqual(seen, {"GH_TOKEN": None, "JENKINS_TOKEN": None, "CUBEAPM_SERVER": "fixture.invalid", "RECKON_ORG": "acme", "RECKON_ENV": "staging"})

    def test_indented_log_stream_is_parsed(self):
        # The pinned cubeapm prints each log entry as an indented JSON object.
        stream = json.dumps({"_msg": "a"}, indent=2) + "\n" + json.dumps({"_msg": "b"}, indent=2) + "\n"
        summary = work.payload_summary(stream, "ndjson", 100)
        self.assertEqual((summary["summary"], summary["completeness"]), ("2 entries captured.", "bounded"))
        self.assertEqual(work.payload_summary('{"_msg": "a"}\n{"_msg"', "ndjson", 100)["completeness"], "invalid_output")

    def test_working_directory_is_not_redacted_as_a_secret(self):
        values = {"PWD": "/work/reckon", "OLDPWD": "/work", "MYSQL_PWD": "db-secret"}
        self.assertEqual(orgs.redact("cd /work/reckon using db-secret", values), "cd /work/reckon using [REDACTED]")

    def test_bad_output_and_provider_errors_are_not_success(self):
        for output in ('not-json', '{"status":"error","error":"forbidden"}', '{"data":{"resultType":"matrix","result":[null]}}'):
            self.fake("print(" + repr(output) + ")")
            _, data = self.session()
            records = self.collect(data["id"])
            self.assertTrue(all(r["status"] == "failed" for r in records), output)

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
        record["environment"] = "uat"
        write_json(path / "evidence" / record["id"] / "record.json", record)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            work.resume(self.root, data["id"])

    def test_copied_evidence_from_other_org_is_rejected(self):
        self.fake('print("[]")')
        path, data = self.session()
        record = self.collect(data["id"])[0]
        record["org"] = "other"
        write_json(path / "evidence" / record["id"] / "record.json", record)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            work.resume(self.root, data["id"])

    def test_json_error_returns_cli_partial_status(self):
        self.fake('print("not-json")')
        _, data = self.session()
        result = self.cli_staging("collect", data["id"], "--json")
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
        self.assertEqual(target.parent, self.root / "incidents")
        self.assertTrue((target / "RCA.md").exists())
        self.assertIn("**Org:** acme", (target / "RCA.md").read_text())
        self.assertTrue((target / "learnings.md").exists())
        self.assertEqual(len(list((target / "evidence").glob("*/record.json"))), 2)
        self.assertEqual(len(evidence(target)), 2)
        with self.assertRaises(ValueError):
            work.promote(self.root, data["id"], "checkout-latency")

    def test_path_traversal_and_lock_contention_fail(self):
        with self.assertRaises(ValueError):
            session_path(self.root, "../../orgs/acme/secrets/cubeapm.env")
        path, _ = self.session()
        with lock(path):
            with self.assertRaises(OSError):
                with lock(path):
                    self.fail("second writer acquired lock")

    def test_evidence_parent_symlink_cannot_write_outside_session(self):
        self.fake('print("[]")')
        path, data = self.session()
        outside = self.dir / "outside"
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
                outside = self.dir / (name + "-outside")
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
        record["artifacts"]["stdout"]["path"] = "../../orgs/acme/secrets/cubeapm.env"
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
                    output.symlink_to(orgs.secrets_path(self.home, "acme", "cubeapm"))
                with self.assertRaisesRegex(ValueError, "integrity|symlink"):
                    work.note(self.root, data["id"], "finding", "Bad citation", [record["id"]])

    def test_empty_allocation_after_interruption_does_not_block_resume(self):
        path, data = self.session()
        (path / "evidence/e-allocated").mkdir()
        self.assertEqual(work.resume(self.root, data["id"])["evidence"], [])

    def test_promotion_rejects_symlinked_extra_files(self):
        path, data = self.session()
        (path / "extra.txt").symlink_to(orgs.secrets_path(self.home, "acme", "cubeapm"))
        with self.assertRaisesRegex(ValueError, "symlink"):
            work.promote(self.root, data["id"], "linked")
        self.assertFalse(list((self.root / "incidents").glob("*")))

    def test_command_injection_stays_a_single_argument(self):
        marker = self.dir / "should-not-exist"
        self.fake('import json,sys; print(json.dumps(sys.argv))')
        path, data = self.session()
        query = 'up; touch "' + str(marker) + '"'
        record = self.collect(data["id"], operation="cubeapm.metrics.range", params={"query": query})[0]
        self.assertFalse(marker.exists())
        self.assertIn(query, json.loads((path / record["artifacts"]["stdout"]["path"]).read_text()))

    def test_non_cubeapm_metric_source_uses_same_workflow(self):
        self.mapping["services"][0]["sources"] = {"grafana": {"datasource": "prometheus", "metrics": {"latency": "histogram_quantile(0.95, latency_bucket)"}}}
        self.save_mapping()
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["grafana"] = {"provider": "grafana"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "grafana", ["GRAFANA_URL=https://fixture.invalid", "GRAFANA_TOKEN=test-token"])
        self.reload()
        self.fake('print(\'{"status":"success","data":{"resultType":"matrix","result":[]}}\')', "grafana")
        _, session = self.session()
        records = self.collect(session["id"])
        self.assertEqual(records[0]["capability"], "metrics.range")
        self.assertEqual(records[0]["completeness"], "empty")
        self.assertEqual((records[0]["provider"], records[0]["connection"]), ("grafana", "grafana"))

    def test_per_environment_connections_pick_the_right_credentials(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"] = {"cubeapm-staging": {"provider": "cubeapm", "env": "staging"},
                               "cubeapm-uat": {"provider": "cubeapm", "env": "uat"}}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "cubeapm-staging", CUBEAPM)
        self.secret("acme", "cubeapm-uat", [l.replace("fixture.invalid", "uat.invalid") for l in CUBEAPM])
        self.reload()
        self.fake('import os; print(os.environ["CUBEAPM_SERVER"]); print(\'{"status":"success","data":{"resultType":"matrix","result":[]}}\')')
        path, session = self.session()
        record = self.collect(session["id"], operation="cubeapm.metrics.range", params={"query": "up"})[0]
        self.assertEqual(record["connection"], "cubeapm-staging")
        self.assertIn("fixture.invalid", (path / record["artifacts"]["stdout"]["path"]).read_text())

    def test_cli_start_collect_and_close(self):
        self.fake('print("[]")')
        result = self.cli_staging("debug", "--service", "checkout", "--question", "Why slow?", "--from", BOUNDS["from"], "--to", BOUNDS["to"], "--collect", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        started = json.loads(result.stdout)
        self.assertEqual((started["org"], started["environment"]), ("acme", "staging"))
        sid = started["session"]
        self.assertNotEqual(self.cli_staging("close", sid, "--outcome", "diagnosed").returncode, 0)
        self.assertEqual(self.cli_staging("close", sid, "--outcome", "inconclusive").returncode, 0)

    def test_cli_pinned_context_drives_a_session(self):
        self.fake('print("[]")')
        result = self.cli("debug", "--service", "checkout", "--question", "Why slow?", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                          "--json", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(result.returncode, 0, result.stderr)
        sid = json.loads(result.stdout)["session"]
        wrong = self.cli("collect", sid, "--json", RECKON_ORG="acme", RECKON_ENV="uat")
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("session belongs", wrong.stderr)

    def test_start_with_failed_collection_returns_partial_status(self):
        self.fake('raise SystemExit(1)')
        result = self.cli_staging("debug", "--service", "checkout", "--question", "Why slow?", "--from", BOUNDS["from"], "--to", BOUNDS["to"], "--collect", "--json")
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

    def test_ambiguous_service_is_rejected(self):
        self.mapping["services"].append({**self.mapping["services"][0], "id": "checkout-v2"})
        self.save_mapping()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_service(self.root, "staging", "checkout-api")

    def test_trace_arguments_preserve_window_and_filter(self):
        command = build("cubeapm.traces.search", {"service": "checkout", "status": "error", "env_label": "STAG", "limit": 10}, BOUNDS, self.root)
        self.assertIn("STAG", command["argv"])
        self.assertIn(BOUNDS["from"], command["argv"])
        self.assertIn("error", command["argv"])
        self.assertEqual(command["capability"], "traces.search")

    def test_cubeapm_env_label_is_applied_to_trace_searches_automatically(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["cubeapm"]["env_labels"] = {"staging": "STAG", "uat": "UAT"}
        orgs.save(self.home, data, self.registry)
        self.reload()
        self.fake("import json,sys; print(json.dumps(sys.argv))")
        path, session = self.session()
        record = self.collect(session["id"], operation="cubeapm.traces.search", params={"service": "checkout"})[0]
        self.assertEqual(record["status"], "ok")
        argv = json.loads((path / record["artifacts"]["stdout"]["path"]).read_text())
        self.assertEqual(argv[argv.index("--env") + 1], "STAG")
        # Passing the right label explicitly is fine; the uat label is another environment's.
        explicit = self.collect(session["id"], operation="cubeapm.traces.search", params={"service": "checkout", "env_label": "STAG"})
        self.assertEqual(explicit[0]["status"], "reused")
        marker = len(evidence(path))
        with self.assertRaisesRegex(ValueError, "another environment"):
            self.collect(session["id"], operation="cubeapm.traces.search", params={"service": "checkout", "env_label": "UAT"})
        self.assertEqual(len(evidence(path)), marker)

    def test_environment_label_follows_the_context_environment(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["cubeapm"]["env_labels"] = {"staging": "STAG", "uat": "UAT"}
        orgs.save(self.home, data, self.registry)
        self.fake("import json,sys; print(json.dumps(sys.argv))")
        context = self.ctx("acme", "uat")
        path, session = create(self.root, "acme", "uat", "uat q", "debug", self.mapping["services"][0], BOUNDS, "latency")
        record = work.collect(self.home, context, self.root, session["id"], "cubeapm.traces.search", {"service": "checkout"}, registry=self.registry)[0]
        argv = json.loads((path / record["artifacts"]["stdout"]["path"]).read_text())
        self.assertEqual(argv[argv.index("--env") + 1], "UAT")
        self.assertEqual(record["environment"], "uat")


class TaskTests(Fixture):
    def start(self, *extra):
        result = self.cli_staging("task", "--question", "Draft the Q4 pricing page copy", *extra, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_task_session_has_no_service_or_window(self):
        started = self.start("--area", "product")
        path, data = session_path(self.root, started["session"])
        self.assertEqual((data["mode"], data["service"], data["window"], data["playbook"], data["area"]), ("task", None, None, None, "product"))
        self.assertEqual((data["org"], data["environment"], data["status"]), ("acme", "staging", "open"))
        self.assertEqual(started["org"], "acme")
        self.assertEqual(evidence(path), [])

    def test_task_report_is_titled_as_a_task(self):
        started = self.start("--area", "product")
        text = Path(started["report"]).read_text()
        self.assertTrue(text.startswith("# Task: Draft the Q4 pricing page copy"))
        self.assertIn("**Area:** product", text)
        self.assertNotIn("Window:", text)
        self.assertNotIn("root cause", text)

    def test_task_may_carry_a_window_but_needs_both_ends(self):
        started = self.start("--from", BOUNDS["from"], "--to", BOUNDS["to"])
        self.assertEqual(session_path(self.root, started["session"])[1]["window"], BOUNDS)
        half = self.cli_staging("task", "--question", "x", "--from", BOUNDS["from"])
        self.assertEqual(half.returncode, 1)
        self.assertIn("both --from and --to", half.stderr)
        bad = self.cli_staging("task", "--question", "x", "--area", "Bad Area")
        self.assertEqual(bad.returncode, 1)

    def test_collecting_an_operation_without_a_window_fails_clearly(self):
        marker = self.dir / "ran"
        self.fake("open(" + repr(str(marker)) + ", 'w').write('x')")
        sid = self.start()["session"]
        result = self.cli_staging("collect", sid, "--operation", "cubeapm.metrics.range", "--params", '{"query": "up"}')
        self.assertEqual(result.returncode, 1)
        self.assertIn("no time window", result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(evidence(session_path(self.root, sid)[0]), [])

    def test_plain_collect_of_a_task_does_nothing(self):
        sid = self.start()["session"]
        result = self.cli_staging("collect", sid, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_task_collects_evidence_through_run_and_reports_it(self):
        self.fake('print("[]")')
        sid = self.start()["session"]
        result = self.cli("run", "cubeapm", "--session", sid, "--label", "services", "--org", "acme", "--env", "staging", "--", "metrics", "label-values", "service", "-o", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.cli_staging("report", sid, "--json")
        self.assertIn("services", Path(json.loads(report.stdout)["report"]).read_text())
        record = work.resume(self.root, sid)["evidence"][0]
        self.assertEqual((record["connection"], record["status"], record["completeness"]), ("cubeapm", "ok", "bounded"))

    def test_close_done_works_and_diagnosed_needs_a_finding(self):
        sid = self.start()["session"]
        self.assertEqual(self.cli_staging("close", sid, "--outcome", "diagnosed").returncode, 1)
        self.assertEqual(self.cli_staging("close", sid, "--outcome", "done").returncode, 0)
        self.assertEqual(session_path(self.root, sid)[1]["status"], "done")

    def test_task_from_another_environment_is_not_usable_here(self):
        sid = self.start()["session"]
        result = self.cli("resume", sid, "--org", "acme", "--env", "uat")
        self.assertEqual(result.returncode, 0)
        result = self.cli("collect", sid, "--org", "acme", "--env", "uat")
        self.assertEqual(result.returncode, 1)
        self.assertIn("session belongs", result.stderr)

    def test_history_lists_tasks_per_environment(self):
        sid = self.start()["session"]
        mine = json.loads(self.cli_staging("history", "--json").stdout)
        self.assertEqual([h["id"] for h in mine], [sid])
        other = json.loads(self.cli("history", "--org", "acme", "--env", "uat", "--json").stdout)
        self.assertEqual(other, [])
        everything = json.loads(self.cli("history", "--org", "acme", "--env", "uat", "--all-envs", "--json").stdout)
        self.assertEqual([h["id"] for h in everything], [sid])


class DemoTests(OrgCase):
    def test_cli_demo_is_synthetic_and_isolated(self):
        result = self.cli("demo", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["synthetic"])
        self.assertFalse(output["network_used"])
        self.assertEqual(output["evidence_records"], 7)
        self.assertEqual(orgs.orgs(self.home), [])
        self.assertTrue(Path(output["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
