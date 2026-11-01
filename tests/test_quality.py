"""Source coverage remains visible when a review saves otherwise successful reads."""
import json
import hashlib

from helpers import BOUNDS, CUBEAPM, JENKINS, OrgCase


class QualityTests(OrgCase):
    def test_reuse_requires_the_same_typed_operation(self):
        made = self.cli("task", "--question", "reuse identity", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                        "--budget", "2", *self.scope, "--json")
        sid = json.loads(made.stdout)["session"]
        calls = self.dir / "identity-calls"
        self.fake("from pathlib import Path\np=Path(" + repr(str(calls)) + ")\np.write_text((p.read_text() if p.exists() else '') + 'x')\n"
                  'print(\'{"status":"success","data":{"resultType":"matrix","result":[]}}\')\n')
        args = ("collect", sid, "--operation", "cubeapm.metrics.range", "--params", '{"query":"up"}', *self.scope, "--json")
        first = self.cli(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        record = json.loads(first.stdout)[0]
        folder = self.home / "orgs/acme/workspace/sessions" / sid
        artifact = folder / record["artifacts"]["stdout"]["path"]
        artifact.write_text("[]")
        record.update(operation="cubeapm.traces.search", capability="traces.search",
                      command=["cubeapm", "traces", "search", "--service", "fixture"])
        record["artifacts"]["stdout"].update(bytes=2, sha256=hashlib.sha256(b"[]").hexdigest())
        (folder / "evidence" / record["id"] / "record.json").write_text(json.dumps(record))
        second = self.cli(*args)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)[0]["status"], "ok")
        self.assertEqual(calls.read_text(), "xx")

    def test_detail_results_must_match_the_requested_resource(self):
        made = self.cli("task", "--question", "requested identities", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                        *self.scope, "--json")
        sid = json.loads(made.stdout)["session"]
        cases = [
            ("jenkins.build.get", {"job": "deploy", "number": 42}, "jenkins", {"number": 43}, "number", 42),
            ("github.run.get", {"repo": "acme/api", "number": 42}, "gh", {"databaseId": 43}, "databaseId", 42),
            ("cubeapm.trace.get", {"trace_id": "0123456789abcdef"}, "cubeapm",
             {"traceID": "fedcba9876543210", "spans": []}, "traceID", "0123456789abcdef"),
        ]
        for operation, params, binary, payload, key, expected in cases:
            args = ("collect", sid, "--operation", operation, "--params", json.dumps(params), *self.scope, "--json")
            self.fake("print(" + repr(json.dumps(payload)) + ")\n", binary)
            failed = self.cli(*args)
            self.assertEqual(failed.returncode, 2, failed.stderr)
            self.assertEqual(json.loads(failed.stdout)[0]["status"], "failed")
            payload[key] = expected
            self.fake("print(" + repr(json.dumps(payload)) + ")\n", binary)
            accepted = self.cli(*args)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_saved_metadata_cannot_disable_typed_output_checks(self):
        made = self.cli("task", "--question", "saved metadata", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                        *self.scope, "--json")
        sid = json.loads(made.stdout)["session"]
        self.fake('print(\'{"status":"success","data":{"resultType":"matrix","result":[]}}\')')
        collected = self.cli("collect", sid, "--operation", "cubeapm.metrics.range", "--params", '{"query":"up"}',
                             *self.scope, "--json")
        self.assertEqual(collected.returncode, 0, collected.stderr)
        record = json.loads(collected.stdout)[0]
        path = self.home / "orgs/acme/workspace/sessions" / sid / "evidence" / record["id"] / "record.json"
        for key, value in [("operation", "unknown.output"), ("operation", None), ("provider", "github"),
                           ("capability", "run"), ("command", None),
                           ("command", ["cubeapm", "traces", "search"])]:
            with self.subTest(key=key, value=value):
                content = json.dumps(dict(record, **{key: value}))
                path.write_text(content)
                resumed = self.cli("resume", sid, *self.scope, "--json")
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                evidence = json.loads(resumed.stdout)["evidence"][0]
                self.assertEqual((evidence["status"], evidence["completeness"]), ("failed", "invalid_output"))
                self.assertEqual(path.read_text(), content)

    def test_typed_metrics_require_the_range_contract_and_revalidate_saved_reads(self):
        made = self.cli("task", "--question", "typed contract", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                        "--budget", "20", *self.scope, "--json")
        sid = json.loads(made.stdout)["session"]
        args = ("collect", sid, "--operation", "cubeapm.metrics.range", "--params", '{"query":"up"}', *self.scope, "--json")
        for payload in ([], {}, 1, {"status": "success", "data": {"resultType": "vector", "result": []}}):
            self.fake("print(" + repr(json.dumps(payload)) + ")\n")
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["completeness"], "invalid_output")
        calls = self.dir / "typed-calls"
        payload = {"status": "success", "data": {"resultType": "matrix", "result": []}}
        self.fake("from pathlib import Path\np=Path(" + repr(str(calls)) + ")\np.write_text((p.read_text() if p.exists() else '') + 'x')\nprint(" + repr(json.dumps(payload)) + ")\n")
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)[0]
        folder = self.home / "orgs/acme/workspace/sessions" / sid
        record_file = folder / "evidence" / record["id"] / "record.json"
        artifact = folder / record["artifacts"]["stdout"]["path"]
        artifact.write_text("[]")
        record["artifacts"]["stdout"].update(bytes=2, sha256=hashlib.sha256(b"[]").hexdigest())
        historical = json.dumps(record)
        record_file.write_text(historical)
        resumed = json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)
        old = next(row for row in resumed["evidence"] if row["id"] == record["id"])
        self.assertEqual((old["status"], old["completeness"]), ("failed", "invalid_output"))
        note = self.cli("note", sid, "--kind", "finding", "--text", "unsupported claim", "--evidence", record["id"], *self.scope)
        self.assertEqual(note.returncode, 1, note.stderr)
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["status"], "ok")
        self.assertEqual(calls.read_text(), "xx")
        self.assertEqual(record_file.read_text(), historical)
        latest = json.loads(result.stdout)[0]
        latest_file = folder / "evidence" / latest["id"] / "record.json"
        latest.update(completeness="partial", summary="Historical coverage limit.")
        latest_file.write_text(json.dumps(latest))
        resumed = json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)
        retained = next(row for row in resumed["evidence"] if row["id"] == latest["id"])
        self.assertEqual((retained["completeness"], retained["summary"]), ("partial", "Historical coverage limit."))

    def test_malformed_metric_samples_fail_reads_and_typed_collection(self):
        good = {"metric": {}, "values": [[0, "1"]]}
        results = [[{"metric": {}, "values": [[0, "not a number"]]}],
                   [{"metric": {}, "values": [["bad timestamp", "1"]]}],
                   [{"metric": {}, "values": [[0, "1", "extra"]]}],
                   [good] * 20 + [{"metric": {}, "values": [None]}]]
        for series in results:
            self.fake("print(" + repr(json.dumps({"data": {"resultType": "matrix", "result": series}})) + ")\n")
            result = self.cli("run", "cubeapm", "--session", self.sid, *self.scope, "--",
                              "metrics", "query", "up", "-o", "json")
            self.assertEqual(result.returncode, 2, result.stderr)
        made = self.cli("task", "--question", "invalid metric read", "--from", BOUNDS["from"], "--to", BOUNDS["to"],
                        *self.scope, "--json")
        sid = json.loads(made.stdout)["session"]
        result = self.cli("collect", sid, "--operation", "cubeapm.metrics.range", "--params", '{"query":"up"}',
                          *self.scope, "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        records = json.loads(self.cli("resume", sid, *self.scope, "--json").stdout)["evidence"]
        self.assertEqual((records[0]["status"], records[0]["completeness"]), ("failed", "invalid_output"))

    def test_nonfinite_and_histogram_samples_are_not_reported_as_empty(self):
        for series, field, count in [
            ({"metric": {}, "values": [[0, "NaN"], [1, "+Inf"], [2, "-Inf"]]}, "non_finite_samples", 3),
            ({"metric": {}, "histograms": [[0, {"count": "1", "sum": "2", "buckets": [[0, "0", "3", "1"]]}]]},
             "histogram_samples", 1),
        ]:
            record = self.read("cubeapm", ["metrics", "query", "up", "-o", "json"],
                               {"data": {"resultType": "matrix", "result": [series]}})
            self.assertEqual(record["completeness"], "bounded")
            self.assertEqual(record["series"][0][field], count)
            self.assertIsNone(record["series"][0]["mean"])
        record = self.read("cubeapm", ["metrics", "query", "up", "-o", "json"],
                           {"data": {"resultType": "matrix", "result": [{"metric": {}}]}})
        self.assertEqual(record["completeness"], "partial")
        self.assertTrue(record["series"][0]["samples_unreported"])

    def test_metric_summary_labels_cannot_create_oversized_metadata(self):
        series = [{"metric": {"service": "x" * 60000}, "values": [[0, "1"]]}] * 20
        record = self.read("cubeapm", ["metrics", "query", "up", "-o", "json"],
                           {"data": {"resultType": "matrix", "result": series}})
        self.assertLess(len(json.dumps(record).encode()), 1048576)
        self.assertTrue(record["series"][0]["labels_truncated"])

    def setUp(self):
        super().setUp()
        self.org(connections={name: {"provider": name, "env": "staging"}
                              for name in ("bing", "clarity", "jenkins", "github", "cubeapm")},
                 secrets={"bing": ["BWT_API_KEY=bing-fixture-token"],
                          "clarity": ["CLARITY_API_TOKEN=clarity-fixture-token"],
                          "jenkins": JENKINS, "github": ["GH_TOKEN=github-fixture-token"], "cubeapm": CUBEAPM})
        self.scope = ("--org", "acme", "--env", "staging")
        made = self.cli("task", "--question", "source coverage", *self.scope, "--json")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.sid = json.loads(made.stdout)["session"]
        self.folder = self.home / "orgs/acme/workspace/sessions" / self.sid
        self.reads = 0

    def read(self, provider, args, payload):
        self.fake("print(" + repr(json.dumps(payload)) + ")\n", {"bing": "bwt", "github": "gh"}.get(provider, provider))
        self.reads += 1
        label = "read-" + str(self.reads)
        result = self.cli("run", provider, "--session", self.sid, "--label", label, *self.scope, "--", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(self.cli("resume", self.sid, *self.scope, "--json").stdout)["evidence"]
        return next(record for record in records if record["label"] == label)

    def test_bing_missing_days_and_empty_crawl_results_remain_qualified(self):
        payload = {"window_days": 7, "current": {"start": "2026-10-08", "end": "2026-10-14", "days_with_data": 7},
                   "previous": {"start": "2026-10-01", "end": "2026-10-07", "days_with_data": 3}}
        record = self.read("bing", ["stats", "summary", "--site", "https://acme.invalid", "-o", "json"], payload)
        self.assertEqual(record["completeness"], "partial")
        self.assertIn("previous: 3 of 7 days", " ".join(record["data_quality"]["warnings"]))
        record = self.read("bing", ["crawl", "issues", "--site", "https://acme.invalid", "-o", "json"], [])
        self.assertIn("does not establish", " ".join(record["data_quality"]["warnings"]))
        record = self.read("bing", ["stats", "summary", "-o", "json"], {"window_days": 7, "current": None})
        self.assertEqual(record["completeness"], "partial")
        self.assertIn("no dated", " ".join(record["data_quality"]["warnings"]))
        self.fake("print('current period table')\n", "bwt")
        result = self.cli("run", "bing", "--session", self.sid, "--label", "text-summary", *self.scope,
                          "--", "stats", "summary")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(self.cli("resume", self.sid, *self.scope, "--json").stdout)["evidence"]
        record = next(record for record in records if record["label"] == "text-summary")
        self.assertEqual(record["completeness"], "bounded")
        self.assertNotIn("no dated", " ".join(record["data_quality"]["warnings"]))

    def test_clarity_sampling_and_export_cap_are_saved(self):
        record = self.read("clarity", ["recordings", "list", "-o", "json"], [])
        self.assertEqual(record["completeness"], "partial")
        self.assertIn("sample", " ".join(record["data_quality"]["warnings"]))
        record = self.read("clarity", ["export", "-o", "json"],
                           [{"metricName": "Traffic", "information": [{"sessions": 1}] * 1000}])
        self.assertEqual(record["completeness"], "partial")
        self.assertEqual(record["data_quality"]["sources"][0]["row_limit"], 1000)
        offline = self.read("clarity", ["export", "dimensions", "-o", "json"], ["Device"])
        self.assertNotIn("data_quality", offline)

    def test_list_limits_and_recent_build_scope_reach_checks_and_reports(self):
        builds = self.read("jenkins", ["build", "list", "deploy", "--limit=2", "-o", "json"],
                           [{"number": 1}, {"number": 2}])
        self.assertEqual(builds["completeness"], "partial")
        self.assertEqual(builds["temporal_scope"], "recent_builds_not_window_filtered")
        self.assertIn("not filtered", " ".join(builds["data_quality"]["warnings"]))
        pulls = self.read("github", ["pr", "list", "-L2", "--json", "number"], [{"number": 1}, {"number": 2}])
        self.assertEqual(pulls["completeness"], "partial")
        runs = self.read("github", ["run", "list", "--json", "databaseId"], [{"databaseId": i} for i in range(20)])
        self.assertEqual(runs["completeness"], "partial")
        checked = json.loads(self.cli("check", self.sid, *self.scope, "--json").stdout)
        self.assertIn("limit", " ".join(checked["warnings"]))
        made = json.loads(self.cli("report", self.sid, *self.scope, "--json").stdout)
        with open(made["report"]) as source:
            rendered = source.read()
        self.assertIn("not filtered", rendered)
        self.assertIn("limit", rendered)

    def test_github_attached_json_and_text_projections_keep_valid_evidence(self):
        record = self.read("github", ["pr", "list", "--limit=2", "--json=number"], [{"number": 1}, {"number": 2}])
        self.assertEqual(record["completeness"], "partial")
        for projection in (["--jq", ".[].title"], ["--template", "{{range .}}{{.title}}{{end}}"]):
            self.fake("print('fixture title')\n", "gh")
            result = self.cli("run", "github", "--session", self.sid, *self.scope, "--", "pr", "list",
                              "--json", "title", *projection)
            self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(self.cli("resume", self.sid, *self.scope, "--json").stdout)["evidence"]
        self.assertTrue(all(record["status"] == "ok" for record in records))

    def test_metric_provider_warnings_survive_summaries(self):
        record = self.read("cubeapm", ["metrics", "query", "up", "-o", "json"],
                           {"status": "success", "warnings": ["some series were omitted"],
                            "data": {"resultType": "matrix", "result": []}})
        self.assertIn("some series were omitted", " ".join(record["data_quality"]["warnings"]))

    def test_unfinished_builds_and_runs_are_labeled_as_changing(self):
        record = self.read("jenkins", ["build", "get", "deploy", "42", "-o", "json"],
                           {"number": 42, "building": True, "result": ""})
        self.assertIn("still running", " ".join(record["data_quality"]["warnings"]))
        record = self.read("github", ["run", "view", "42", "--json", "status,conclusion"],
                           {"status": "in_progress", "conclusion": ""})
        self.assertIn("may change", " ".join(record["data_quality"]["warnings"]))
