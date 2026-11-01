"""Agent usage per org from Claude Code and Codex session files. Offline fixtures."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

from helpers import OrgCase, SOURCE
from reckonlib import orgs, spend


def stamp(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SpendTests(OrgCase):
    def codex_thread(self, number):
        return f"00000000-0000-4000-8000-{number:012d}"

    def codex_context(self, turn="turn-1", cwd=None):
        return {"type": "turn_context", "payload": {"turn_id": turn, "cwd": cwd or self.root, "model": "fixture-model"}}

    def codex_response(self, response="response-1", thread=1, turn="turn-1", usage=None, timestamp=None):
        counts = {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0,
                  "cache_write_input_tokens": 0, "reasoning_output_tokens": 0}
        counts.update(usage or {})
        counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
        return {"type": "token_usage_record", "timestamp": timestamp or stamp(), "payload": {
            "thread_id": self.codex_thread(thread), "turn_id": turn,
            "session_id": self.codex_thread(thread), "root_turn_id": turn, "response_id": response,
            "usage": counts, "turn_token_usage": dict(counts), "thread_token_usage": dict(counts)}}

    def codex_log(self, name, events, thread=1, cwd=None, directory="sessions", **metadata):
        folder = self.dir / "codex" / directory
        folder.mkdir(parents=True, exist_ok=True)
        header = {"type": "session_meta", "payload": {
            "id": self.codex_thread(thread), "cwd": cwd or self.root, **metadata}}
        path = folder / name
        path.write_text("\n".join(json.dumps(row) for row in (header, *events)) + "\n")
        return path

    def codex_counter(self, amount, total, timestamp=None):
        return {"type": "event_msg", "timestamp": timestamp or stamp(), "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": amount},
                                            "total_token_usage": {"input_tokens": total}}}}

    def codex_settings(self, thread):
        return {"type": "event_msg", "payload": {"type": "thread_settings_applied",
                                                  "thread_id": self.codex_thread(thread)}}

    def codex_fill(self, previous=0):
        record = self.codex_counter(0, 0)
        record["payload"]["info"]["model_context_window"] = 100000
        record["payload"]["info"]["total_token_usage"]["total_tokens"] = 100000
        record["payload"]["info"]["last_token_usage"]["total_tokens"] = 100000 - previous
        return record

    def test_native_codex_legacy_usage_after_response_receipts_is_counted(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=20)
        def at(seconds): return (start + timedelta(seconds=seconds)).isoformat()
        receipt = self.codex_response(usage={"input_tokens": 10, "output_tokens": 0}, timestamp=at(1))
        events = [self.codex_context(), self.codex_counter(4, 4, at(0)), receipt,
                  self.codex_counter(10, 14, at(2)), self.codex_context("legacy-turn"),
                  self.codex_counter(5, 19, at(3)), self.codex_counter(5, 19, at(4))]
        path = self.codex_log("mixed.jsonl", events)
        before = path.read_bytes()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 19)
        self.assertEqual(path.read_bytes(), before)
        events[-2]["payload"]["info"]["last_token_usage"]["input_tokens"] = 10
        events[-2]["payload"]["info"]["total_token_usage"]["input_tokens"] = 24
        events.pop()
        self.codex_log("mixed.jsonl", events)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 24)

    def test_native_codex_context_fills_start_new_legacy_accounting_epochs(self):
        old = self.codex_counter(7, 7, stamp(40))
        events = [self.codex_context(), old, self.codex_fill(7), self.codex_fill(7),
                  self.codex_counter(7, 7), self.codex_counter(7, 7)]
        path = self.codex_log("filled.jsonl", events)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 7)
        old["timestamp"] = stamp()
        self.codex_log("filled.jsonl", events)
        before = path.read_bytes()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 14)
        self.assertEqual(path.read_bytes(), before)

    def test_native_codex_response_coverage_handles_stale_and_repeated_counters(self):
        first = self.codex_response("first", usage={"input_tokens": 10, "output_tokens": 0})
        second = self.codex_response("second", usage={"input_tokens": 5, "output_tokens": 0})
        events = [self.codex_context(), self.codex_counter(4, 4), first, self.codex_counter(4, 4), second,
                  self.codex_counter(5, 19), self.codex_counter(5, 19), self.codex_counter(4, 4), self.codex_counter(3, 22)]
        self.codex_log("coverage.jsonl", events)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 22)
        # SpendingOnly changes cumulative usage but leaves the stored last usage stale.
        self.codex_log("coverage.jsonl", [self.codex_context(), self.codex_counter(4, 4), first,
                       self.codex_counter(4, 14), self.codex_counter(3, 17)])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 17)

    def test_native_codex_fills_do_not_reuse_inherited_counter_seeds(self):
        original = self.codex_log("original.jsonl", [self.codex_context(), self.codex_counter(7, 7)])
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        beta = str(orgs.workspace(self.home, "beta"))
        self.codex_log("child.jsonl", [*rows, self.codex_settings(2), self.codex_context("child-turn", beta),
                       self.codex_counter(5, 12), self.codex_fill(12), self.codex_counter(7, 7)],
                       thread=2, cwd=beta, forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 7), ("beta", 12)])

    def test_native_codex_receipt_prefixes_do_not_consume_later_coverage(self):
        for amount in (5, 10):
            with self.subTest(amount=amount):
                first = self.codex_response("first", usage={"input_tokens": 10, "output_tokens": 0})
                second = self.codex_response("second", usage={"input_tokens": amount, "output_tokens": 0})
                self.codex_log("prefixes.jsonl", [self.codex_context(), self.codex_counter(4, 4), first, second,
                               self.codex_counter(10, 14), self.codex_counter(10, 14),
                               self.codex_counter(amount, 14 + amount), self.codex_counter(3, 17 + amount)])
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)[0]["tokens"], 17 + amount)

    def test_native_codex_context_fills_separate_response_coverage(self):
        first = self.codex_response("first", usage={"input_tokens": 10, "output_tokens": 0})
        second = self.codex_response("second", usage={"input_tokens": 5, "output_tokens": 0})
        for mirrored in (False, True):
            with self.subTest(mirrored=mirrored):
                events = [self.codex_context(), self.codex_counter(4, 4), first]
                if mirrored:
                    events.append(self.codex_counter(10, 14))
                events.extend([self.codex_fill(14), second, self.codex_counter(5, 5),
                               self.codex_counter(10, 15)])
                path = self.codex_log("reset-coverage.jsonl", events)
                before = path.read_bytes()
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)[0]["tokens"], 29)
                self.assertEqual(path.read_bytes(), before)

    def test_native_codex_ambiguous_response_coverage_fails_across_contexts(self):
        receipt = self.codex_response(usage={"input_tokens": 7, "output_tokens": 0})
        self.codex_log("ambiguous.jsonl", [self.codex_context(), self.codex_counter(7, 7), receipt,
                       self.codex_context("resumed-turn"), self.codex_counter(7, 14)])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("incomplete agent usage: ambiguous response coverage", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_native_codex_referenced_fills_keep_the_zero_counter_baseline(self):
        source_events = [self.codex_context(), self.codex_counter(7, 7), self.codex_fill(7)]
        for ordinal, event in enumerate(source_events, 1):
            event["ordinal"] = ordinal
        original = self.codex_log("original.jsonl", source_events, history_mode="paginated")
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        rows[0]["ordinal"] = 0
        original.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        base = {"thread_id": self.codex_thread(1), "end_ordinal_exclusive": 4,
                "end_byte_offset": len(original.read_bytes())}
        response = self.codex_response(thread=2, turn="child-turn", usage={"input_tokens": 5, "output_tokens": 0})
        child_events = [self.codex_context("child-turn"), response, self.codex_fill(7),
                        self.codex_counter(5, 5), self.codex_counter(7, 12)]
        for ordinal, event in enumerate(child_events, 5):
            event["ordinal"] = ordinal
        self.codex_log("child.jsonl", child_events, thread=2, history_mode="paginated", history_base=base)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 19)

    def test_native_codex_transcript_syntax_errors_cannot_report_partial_usage(self):
        path = self.codex_log("syntax.jsonl", [self.codex_counter(7, 7)])
        header, record = path.read_bytes().splitlines(keepends=True)
        private = b'synthetic-private-transcript'
        malformed = b'{"type":"event_msg","private":"' + private + b'","payload":{"type":"token_count"}\n'
        cases = [b'{"type":"session_meta","private":"' + private + b'"\n' + record,
                 b'null\n' + record, b'{"type":"event_msg","payload":{}}\n' + record,
                 b'{"type":"session_meta","payload":[]}\n' + record,
                 b'{"type":"session_meta","payload":{"cwd":false}}\n' + record,
                 header + b'null\n', header + b'{"type":"event_msg","payload":false}\n',
                 header + b'{}\n', header + b'{"type":false}\n', header + b'{"type":"event_msg","payload":{}}\n',
                 header + b'{"type":"turn_context","payload":{"cwd":false}}\n' + record,
                 header + b'{"type":"token_usage_record","payload":[]}\n',
                 header + malformed, header + record + malformed, header + record + malformed.rstrip(b'\n'),
                 header + b'{"type":"progress","private":"' + private + b'"\n',
                 header + record.replace(b'"event_msg"', b'"event_\xffmsg"')]
        for packed in (False, True):
            target = path.with_suffix(".jsonl.zst") if packed else path
            if packed:
                path.rename(path.with_suffix(".saved"))
            for content in cases:
                with self.subTest(packed=packed, content=content[:30]):
                    target.write_bytes(self.zstd_frame(content) if packed else content)
                    before = target.read_bytes()
                    result = self.cli("spend", "--org", "acme", "--json")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("usage", result.stderr)
                    self.assertNotIn(private.decode(), result.stdout + result.stderr)
                    self.assertEqual(target.read_bytes(), before)

    def test_native_claude_transcript_syntax_errors_cannot_report_partial_usage(self):
        self.claude_session(self.root, [("m1", 0, 7)])
        path = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        record = path.read_bytes()
        malformed = b'{"type":"assistant","private":"synthetic-private-transcript","message":{"usage":{}}\n'
        row = json.loads(record)
        invalid_workspace = dict(row, cwd=None)
        for content in (malformed, record + malformed, record + malformed.rstrip(b'\n'),
                        b'null\n', b'{}\n', b'{"type":"assistant","message":false}\n',
                        (json.dumps(invalid_workspace) + "\n").encode(),
                        record + b'{"type":"progress"\n', record.replace(b'"assistant"', b'"assis\xfftant"')):
            with self.subTest(content=content[:30]):
                path.write_bytes(content)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertNotIn("synthetic-private-transcript", result.stdout + result.stderr)
                self.assertEqual(path.read_bytes(), content)

    def test_native_usage_accepts_json_escapes_blank_lines_and_valid_unterminated_records(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        claude = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        content = claude.read_text().replace('"assistant"', '"\\u0061ssistant"').replace('"usage"', '"us\\u0061ge"')
        claude.write_text("\n \t\n" + content.rstrip("\n"))
        codex = self.codex_log("escaped.jsonl", [self.codex_context(), self.codex_counter(10, 10)])
        content = codex.read_text().replace('"session_meta"', '"session_\\u006deta"').replace('"token_count"', '"token\\u005fcount"')
        codex.write_text("\n \t\n" + content.rstrip("\n"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 126)

    def test_native_scoped_usage_timestamps_must_be_valid(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        claude = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        assistant = json.loads(claude.read_text())
        for runtime in ("claude", "codex"):
            for timestamp in (None, False, "", "synthetic-private-timestamp"):
                with self.subTest(runtime=runtime, timestamp=timestamp):
                    if runtime == "claude":
                        assistant["timestamp"] = timestamp
                        claude.write_text(json.dumps(assistant) + "\n")
                    else:
                        record = self.codex_counter(7, 7)
                        record["timestamp"] = timestamp
                        self.codex_log("timestamp.jsonl", [self.codex_context(), record])
                    result = self.cli("spend", "--org", "acme", "--json")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("usage", result.stderr)
                    self.assertNotIn("synthetic-private-timestamp", result.stdout + result.stderr)
            if runtime == "claude":
                claude.unlink()
        placeholders = []
        for usage in (None, {}):
            record = self.codex_counter(0, 7)
            record["timestamp"] = None
            record["payload"]["info"]["last_token_usage"] = usage
            placeholders.append(record)
        self.codex_log("timestamp.jsonl", [self.codex_context(), self.codex_counter(7, 7), *placeholders])
        foreign = self.codex_counter(9, 9)
        foreign["timestamp"] = "invalid"
        self.codex_log("foreign.jsonl", [foreign], thread=2, cwd=str(orgs.workspace(self.home, "beta")))
        claude.write_text(json.dumps({"type": "assistant", "cwd": None, "timestamp": None,
                                     "message": {"model": "<synthetic>", "usage": {"input_tokens": 1000}}}) + "\n")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 7)
        self.assertEqual(self.cli("spend", "--org", "beta", "--json").returncode, 1)

    def test_native_history_offsets_use_actual_bytes_without_a_final_newline(self):
        root_id = self.codex_thread(1)
        path = self.codex_log(f"rollout-2026-10-01T00-00-00-{root_id}.jsonl",
                              [dict(self.codex_context(), ordinal=1), dict(self.codex_counter(7, 7), ordinal=2)],
                              history_mode="paginated", note="fixture snowman ☃")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["ordinal"] = 0
        content = "\r\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
        path.write_bytes(content)
        base = {"thread_id": root_id, "end_ordinal_exclusive": 3, "end_byte_offset": len(content)}
        self.codex_log(f"rollout-2026-10-02T00-00-00-{root_id}_{self.codex_thread(4)}.jsonl",
                       [dict(self.codex_counter(5, 12), ordinal=4)], history_mode="paginated", history_base=base)
        for packed in (False, True):
            if packed:
                path.rename(path.with_suffix(".saved"))
                path.with_suffix(".jsonl.zst").write_bytes(self.zstd_frame(content))
            result = self.cli("spend", "--org", "acme", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)

    def test_native_transcript_syntax_errors_reach_budget_warnings(self):
        self.assertEqual(self.cli("spend", "--org", "acme", "--set-budget", "100").returncode, 0)
        path = self.codex_log("broken.jsonl", [self.codex_counter(7, 7)])
        path.write_bytes(path.read_bytes() + b'{"type":"event_msg","payload":{"type":"token_count"}\n')
        self.assertEqual(self.cli("brief", "--all-orgs", "--json").returncode, 1)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not check", " ".join(json.loads(result.stdout)["agent_budget"]))

    def test_native_usage_window_excludes_future_observations(self):
        self.claude_session(self.root, [("current-message", 0, 3), ("future-message", -10, 7), ("current-message", -10, 999)])
        claude = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        result = self.cli("spend", "--org", "acme", "--days", "30", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 114)
        claude.unlink()
        for kind in ("legacy", "response"):
            with self.subTest(kind=kind):
                if kind == "legacy":
                    events = [self.codex_counter(7, 7, stamp(-10)), self.codex_counter(3, 10)]
                else:
                    events = [self.codex_response("future", timestamp=stamp(-10)),
                              self.codex_response("current", usage={"input_tokens": 3, "output_tokens": 0})]
                self.codex_log("future.jsonl", [self.codex_context(), *events])
                result = self.cli("spend", "--org", "acme", "--days", "30", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)[0]["tokens"], 3)

    def zstd_frame(self, content):
        # A standard frame containing one raw block keeps fixtures dependency-free.
        self.assertLess(len(content), 128 * 1024)
        return (bytes.fromhex("28b52ffd a0") + len(content).to_bytes(4, "little")
                + ((len(content) << 3) | 1).to_bytes(3, "little") + content)

    def test_native_codex_compressed_rollouts_count_once_and_preserve_sources(self):
        path = self.codex_log("archived.jsonl", [self.codex_context(), self.codex_response()], directory="archived_sessions")
        content = path.read_bytes()
        packed = path.with_suffix(".jsonl.zst")
        packed.write_bytes(self.zstd_frame(content))
        before = packed.read_bytes()
        path.rename(path.with_suffix(".saved"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        path.write_bytes(content)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        self.assertEqual(packed.read_bytes(), before)
        self.assertEqual(path.read_bytes(), content)

    def test_native_codex_compressed_streams_validate_all_frames(self):
        path = self.codex_log("framed.jsonl", [self.codex_context(), self.codex_counter(7, 7)])
        lines = path.read_bytes().splitlines(keepends=True)
        path.rename(path.with_suffix(".saved"))
        packed = path.with_suffix(".jsonl.zst")
        skip = bytes.fromhex("502a4d18") + (3).to_bytes(4, "little") + b"tag"
        valid = self.zstd_frame(b"".join(lines[:2])) + skip + self.zstd_frame(lines[2])
        packed.write_bytes(valid)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 7)
        for invalid in (b"", b"invalid frame", valid[:-1], valid + b"trailing corruption",
                        valid + skip[:-1], valid + bytes.fromhex("28b52ffd a4") + (0).to_bytes(4, "little")
                        + (1).to_bytes(3, "little") + b"bad!",
                        bytes.fromhex("28b52ffd a0 ffffffff 010000")):
            with self.subTest(length=len(invalid)):
                packed.write_bytes(invalid)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(packed.read_bytes(), invalid)

    def test_native_codex_standard_compressed_blocks_and_checksums_are_read(self):
        path = self.codex_log("compressed.jsonl", [])
        prefix = path.read_bytes() + b'{"type":"event_msg","timestamp":"' + stamp().encode()
        # Synthetic JSON suffix encoded once by the reference zstd CLI at level 3.
        suffix = bytes.fromhex(
            "28b52ffd0458750200d204101690a71a03cc85c21b095878245474f72373549ce7ca38c3ee9ad3d4c5026c940e96974d0887"
            "d25074e665e700cb62c5948181a490ba4d8ed2972db834eacee203004ae104a7fbe0d88402b415197d")
        packed = path.with_suffix(".jsonl.zst")
        packed.write_bytes(self.zstd_frame(prefix) + suffix)
        path.rename(path.with_suffix(".saved"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 7)
        damaged = packed.read_bytes()[:-1] + bytes([packed.read_bytes()[-1] ^ 1])
        packed.write_bytes(damaged)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("checksum", result.stderr)

    def test_native_codex_compressed_prefixes_keep_decoded_byte_positions(self):
        root_id = self.codex_thread(1)
        events = [dict(self.codex_context(), ordinal=1), dict(self.codex_counter(7, 7), ordinal=2)]
        path = self.codex_log(f"rollout-2026-10-01T00-00-00-{root_id}.jsonl", events, history_mode="paginated")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["ordinal"] = 0
        content = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
        path.rename(path.with_suffix(".saved"))
        packed = path.with_suffix(".jsonl.zst")
        packed.write_bytes(self.zstd_frame(content))
        base = {"thread_id": root_id, "end_ordinal_exclusive": 3, "end_byte_offset": len(content)}
        self.codex_log(f"rollout-2026-10-02T00-00-00-{root_id}_{self.codex_thread(4)}.jsonl",
                       [dict(self.codex_counter(5, 12), ordinal=4)], history_mode="paginated", history_base=base)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)

    def test_native_codex_repeated_legacy_snapshots_are_not_new_usage(self):
        now = datetime.now(timezone.utc) - timedelta(seconds=20)
        events = [self.codex_context()]
        for seconds, amount, total in ((0, 7, 7), (1, 7, 7), (2, 0, 7), (3, 5, 12), (4, 5, 12)):
            event = self.codex_counter(amount, total, (now + timedelta(seconds=seconds)).isoformat())
            event["payload"]["rate_limits"] = {"primary": {"used_percent": seconds}}
            event["payload"]["info"]["total_token_usage"]["total_tokens"] = 100 + seconds
            events.append(event)
        path = self.codex_log("repeated.jsonl", events)
        before = path.read_bytes()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        self.assertEqual(path.read_bytes(), before)
        events[-1]["payload"]["info"]["last_token_usage"]["input_tokens"] = 4
        self.codex_log("repeated.jsonl", events)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("conflicting agent usage", result.stderr)

    def test_native_codex_anonymous_snapshots_keep_independent_logs_separate(self):
        now = datetime.now(timezone.utc) - timedelta(seconds=5)
        for number in (1, 2):
            first = self.codex_counter(7, 7, (now + timedelta(seconds=number)).isoformat())
            repeated = self.codex_counter(7, 7, (now + timedelta(seconds=number + 1)).isoformat())
            self.codex_log(f"anonymous-{number}.jsonl", [self.codex_context(), first, repeated], id=None)
        folder = self.dir / "codex/sessions"
        (folder / "copy.jsonl").write_bytes((folder / "anonymous-1.jsonl").read_bytes())
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 14)

    def test_native_codex_legacy_cumulative_counters_are_validated(self):
        for total in (False, {"input_tokens": -1}, {"input_tokens": True},
                      {"input_tokens": 2 ** 63}, {"input_tokens": 7, "cached_input_tokens": 8}):
            with self.subTest(total=total):
                counter = self.codex_counter(7, 7)
                counter["payload"]["info"]["total_token_usage"] = total
                path = self.codex_log("invalid-cumulative.jsonl", [self.codex_context(), counter])
                before = path.read_bytes()
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(path.read_bytes(), before)
        self.codex_log("invalid-cumulative.jsonl", [self.codex_context(), self.codex_counter(5, 12), self.codex_counter(7, 7)])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cumulative counters decreased", result.stderr)

    def test_native_codex_legacy_repeats_keep_their_first_age_and_workspace(self):
        beta = str(orgs.workspace(self.home, "beta"))
        old = self.codex_counter(100, 100, stamp(40))
        repeated = self.codex_counter(100, 100)
        first = self.codex_counter(7, 107, (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat())
        fresh = self.codex_counter(5, 112)
        self.codex_log("repeated.jsonl", [self.codex_context(), old, repeated, first,
                       self.codex_context("beta-turn", beta), self.codex_counter(7, 107), fresh])
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 7), ("beta", 5)])

    def test_native_codex_copied_history_seeds_do_not_charge_the_child(self):
        original = self.codex_log("original.jsonl", [self.codex_context(), self.codex_counter(7, 7)])
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        beta = str(orgs.workspace(self.home, "beta"))
        self.codex_log("child.jsonl", [*rows, self.codex_settings(2), self.codex_context("child-turn", beta),
                       self.codex_counter(7, 7), self.codex_counter(5, 12)], thread=2, cwd=beta,
                       forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 7), ("beta", 5)])

    def test_native_codex_referenced_history_seeds_do_not_charge_reverted_work(self):
        root_id = self.codex_thread(1)
        events = [dict(self.codex_context(), ordinal=1), dict(self.codex_counter(7, 7), ordinal=2)]
        path = self.codex_log(f"rollout-2026-10-01T00-00-00-{root_id}.jsonl", events, history_mode="paginated")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["ordinal"] = 0
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        base = {"thread_id": root_id, "end_ordinal_exclusive": 3, "end_byte_offset": path.stat().st_size}
        replacement = self.codex_log(f"rollout-2026-10-02T00-00-00-{root_id}_{self.codex_thread(4)}.jsonl",
                       [dict(self.codex_counter(7, 7), ordinal=4), dict(self.codex_counter(5, 12), ordinal=5)],
                       history_mode="paginated", history_base=base)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        rows = [json.loads(line) for line in replacement.read_text().splitlines()]
        rows[0]["ordinal"] = 3
        replacement.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        base = {"thread_id": self.codex_thread(4), "end_ordinal_exclusive": 5,
                "end_byte_offset": sum(len(line) for line in replacement.read_bytes().splitlines(keepends=True)[:2])}
        self.codex_log(f"rollout-2026-10-03T00-00-00-{root_id}_{self.codex_thread(5)}.jsonl",
                       [dict(self.codex_counter(7, 7), ordinal=6), dict(self.codex_counter(3, 10), ordinal=7)],
                       history_mode="paginated", history_base=base)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 15)

    def test_native_codex_legacy_copied_prefixes_keep_original_owners_and_dates(self):
        old = self.codex_counter(100, 100, stamp(40))
        recent = self.codex_counter(7, 107)
        original = self.codex_log("original.jsonl", [self.codex_context(), old, recent], directory="archived_sessions")
        original_rows = [json.loads(line) for line in original.read_text().splitlines()]
        original_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        os.utime(original, (original_time, original_time))
        inherited = json.loads(json.dumps(original_rows))
        for row in inherited:
            row["timestamp"] = stamp()
        beta = str(orgs.workspace(self.home, "beta"))
        child = self.codex_log("child.jsonl", [*inherited, self.codex_settings(2),
                                self.codex_context("child-turn", beta), self.codex_counter(5, 112)],
                               thread=2, cwd=beta, forked_from_id=self.codex_thread(1))
        child_rows = [json.loads(line) for line in child.read_text().splitlines()]
        self.codex_log("grandchild.jsonl", [*child_rows, self.codex_settings(3),
                       self.codex_context("grandchild-turn"), self.codex_counter(3, 115)],
                       thread=3, forked_from_id=self.codex_thread(2))
        before = {path: path.read_bytes() for path in (original, child)}
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 10), ("beta", 5)])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_native_codex_legacy_incomplete_copies_fail_without_a_guessed_total(self):
        original = self.codex_log("original.jsonl", [self.codex_context(), self.codex_counter(7, 7)])
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        child = self.codex_log("child.jsonl", [*rows, self.codex_settings(2), self.codex_context("child-turn"),
                                               self.codex_counter(5, 12)], thread=2, forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        saved = original.rename(original.with_suffix(".saved"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        saved.rename(original)
        before = original.read_bytes()
        self.codex_log("original.jsonl", rows[1:], timestamp=stamp(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("original revision", result.stderr)
        original.write_bytes(before)
        self.codex_log("child.jsonl", rows, thread=2, forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        changed = json.loads(json.dumps(rows))
        changed[-1]["payload"]["info"]["last_token_usage"]["input_tokens"] = 8
        self.codex_log("child.jsonl", [*changed, self.codex_settings(2)], thread=2,
                       forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)

    def test_native_codex_reverts_keep_distinct_physical_counter_history(self):
        event = self.codex_counter(7, 7)
        self.codex_log("original.jsonl", [self.codex_context(), event], timestamp=stamp(2))
        self.codex_log("replacement.jsonl", [self.codex_context("new-turn"), event], timestamp=stamp(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 14)

    def test_native_codex_paginated_references_require_an_available_prefix(self):
        root_id = self.codex_thread(1)
        source_events = [self.codex_context(), self.codex_counter(7, 7), self.codex_counter(20, 27)]
        for ordinal, event in enumerate(source_events, 1):
            event["ordinal"] = ordinal
        original = self.codex_log(f"rollout-2026-10-01T00-00-00-{root_id}.jsonl", source_events, history_mode="paginated")
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        rows[0]["ordinal"] = 0
        original.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        retained_bytes = sum(len(line) for line in original.read_bytes().splitlines(keepends=True)[:3])
        base = {"thread_id": root_id, "end_ordinal_exclusive": 3, "end_byte_offset": retained_bytes}
        replacement = self.codex_log(f"rollout-2026-10-02T00-00-00-{root_id}_{self.codex_thread(4)}.jsonl",
                       [dict(self.codex_counter(5, 12), ordinal=4)], history_mode="paginated", history_base=base)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 32)
        saved = original.rename(original.with_suffix(".saved"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        saved.rename(original)
        for field, value in (("end_byte_offset", retained_bytes + 1), ("end_ordinal_exclusive", 0),
                             ("end_ordinal_exclusive", "3"), ("thread_id", False)):
            with self.subTest(field=field, value=value):
                malformed = dict(base, **{field: value})
                self.codex_log(replacement.name, [dict(self.codex_counter(5, 12), ordinal=4)],
                               history_mode="paginated", history_base=malformed)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
        self.codex_log(replacement.name, [dict(self.codex_counter(5, 12), ordinal=4)],
                       history_mode="paginated", history_base=base)
        original.write_text(json.dumps(rows[0]) + "\n")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)

    def test_native_codex_copied_ordinal_boundary_identifies_the_childs_work(self):
        self.codex_log("original.jsonl", [self.codex_context(), self.codex_counter(7, 7)])
        beta = str(orgs.workspace(self.home, "beta"))
        events = [dict(self.codex_counter(7, 7), ordinal=1),
                  dict(self.codex_context("child-turn", beta), ordinal=2),
                  dict(self.codex_counter(5, 12), ordinal=3)]
        self.codex_log("child.jsonl", events, thread=2, cwd=beta,
                       forked_from_id=self.codex_thread(1), subagent_history_start_ordinal=2)
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 7), ("beta", 5)])
        for boundary in ("2", False, 99):
            with self.subTest(boundary=boundary):
                path = self.codex_log("child.jsonl", events, thread=2, cwd=beta,
                                      forked_from_id=self.codex_thread(1), subagent_history_start_ordinal=boundary)
                before = path.read_bytes()
                result = self.cli("spend", "--org", "beta", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(path.read_bytes(), before)

    def test_native_codex_lineage_cycles_fail_only_for_relevant_workspaces(self):
        beta = str(orgs.workspace(self.home, "beta"))
        identifier = self.codex_thread(2)
        base = {"thread_id": identifier, "end_ordinal_exclusive": 1, "end_byte_offset": 1}
        for _ in range(5):
            path = self.codex_log(f"rollout-2026-10-01T00-00-00-{identifier}.jsonl",
                                  [dict(self.codex_counter(7, 7), ordinal=1)], thread=2, cwd=beta,
                                  history_mode="paginated", history_base=base)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[0]["ordinal"] = 0
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            base["end_byte_offset"] = len(path.read_bytes().splitlines(keepends=True)[0])
        before = path.read_bytes()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)
        result = self.cli("spend", "--org", "beta", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cyclic history references", result.stderr)
        self.assertEqual(self.cli("brief", "--all-orgs", "--json").returncode, 1)
        self.assertEqual(self.cli("spend", "--org", "beta", "--set-budget", "100").returncode, 0)
        result = self.cli("brief", "--org", "beta", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not check", " ".join(json.loads(result.stdout)["agent_budget"]))
        self.assertEqual(path.read_bytes(), before)

    def test_native_codex_metadata_and_settings_supply_missing_turn_context(self):
        original = self.codex_log("original.jsonl", [self.codex_counter(7, 7)])
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        beta = str(orgs.workspace(self.home, "beta"))
        settings = self.codex_settings(2)
        settings["payload"]["thread_settings"] = {"cwd": beta, "model": "child-model"}
        self.codex_log("child.jsonl", [*rows, settings, self.codex_counter(5, 12)], thread=2, cwd=beta,
                       forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = json.loads(result.stdout)
        self.assertEqual([(row["org"], row["tokens"]) for row in reports], [("acme", 7), ("beta", 5)])
        self.assertEqual(reports[1]["models"][0]["model"], "child-model")
        before = original.read_bytes()
        saved = original.rename(original.with_suffix(".saved"))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        self.assertEqual(saved.read_bytes(), before)

    def test_native_codex_observed_responses_are_counted_and_priced(self):
        first = self.codex_response(usage={"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2})
        second = self.codex_response("response-2", usage={"input_tokens": 5, "output_tokens": 1})
        second["payload"]["thread_token_usage"].update(input_tokens=15, cached_input_tokens=4, output_tokens=3, total_tokens=18)
        second["payload"]["turn_token_usage"] = dict(second["payload"]["thread_token_usage"])
        path = self.codex_log("observed.jsonl", [self.codex_context(), first, second])
        before = path.read_bytes()
        (self.home / "prices.json").write_text(json.dumps({"fixture-model": {
            "input": 1e6, "output": 1e6, "cache_read": 1e6, "cache_write": 1e6}}))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)[0]
        self.assertEqual((report["tokens"], report["usd"]), (18, 18))
        self.assertEqual([(row["input"], row["output"], row["cache_read"], row["cache_write"]) for row in report["models"]], [(11, 3, 4, 0)])
        self.assertEqual(path.read_bytes(), before)

    def test_native_codex_observed_format_preserves_pre_upgrade_counters(self):
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        def legacy(seconds, amount, total):
            return {"type": "event_msg", "timestamp": (now + timedelta(seconds=seconds)).isoformat(), "payload": {
                "type": "token_count", "info": {"last_token_usage": {"input_tokens": amount}, "total_token_usage": {"input_tokens": total}}}}
        record = self.codex_response(usage={"input_tokens": 10, "output_tokens": 0}, timestamp=(now + timedelta(seconds=1)).isoformat())
        self.codex_log("mixed.jsonl", [self.codex_context(), legacy(0, 4, 4), record,
                                       legacy(2, 10, 14), legacy(3, 10, 14)])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 14)

    def test_native_codex_response_replays_keep_the_original_age_and_owner(self):
        old = self.codex_response("old-response", timestamp=stamp(40))
        original = self.codex_log("old.jsonl", [self.codex_context(), old], directory="archived_sessions")
        old_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        os.utime(original, (old_time, old_time))
        copied = json.loads(json.dumps(old))
        copied["timestamp"] = stamp()
        fresh = self.codex_response("fresh-response", turn="fresh-turn", usage={"input_tokens": 7, "output_tokens": 0})
        self.codex_log("revert.jsonl", [self.codex_context(), copied, self.codex_context("fresh-turn"), fresh])
        beta = str(orgs.workspace(self.home, "beta"))
        child = self.codex_response("child-response", thread=2, turn="child-turn", usage={"input_tokens": 5, "output_tokens": 0})
        self.codex_log("fork.jsonl", [self.codex_context(), copied, self.codex_context("child-turn", beta), child],
                       thread=2, cwd=beta, forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 7), ("beta", 5)])

    def test_native_codex_observed_records_reject_malformed_and_conflicting_receipts(self):
        original = self.codex_response()
        cases = []
        for field, value in (("response_id", ""), ("turn_id", False), ("thread_id", None),
                             ("usage", False), ("usage", {}), ("usage", {"input_tokens": -1}),
                             ("usage", {"input_tokens": 10, "output_tokens": True, "cached_input_tokens": 0}),
                             ("usage", {"input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 0})):
            changed = json.loads(json.dumps(original))
            changed["payload"][field] = value
            cases.append(changed)
        for changed in cases:
            with self.subTest(payload=changed["payload"]):
                path = self.codex_log("invalid.jsonl", [self.codex_context(), changed])
                before = path.read_bytes()
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(path.read_bytes(), before)
        changed = json.loads(json.dumps(original))
        changed["payload"]["usage"]["input_tokens"] = 9
        path = self.codex_log("invalid.jsonl", [self.codex_context(), original, changed])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        original["timestamp"] = "invalid"
        self.codex_log("invalid.jsonl", [self.codex_context(), original])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)

    def test_native_codex_observed_usage_follows_turn_workspace_context(self):
        beta = str(orgs.workspace(self.home, "beta"))
        first = self.codex_response(usage={"input_tokens": 10, "output_tokens": 0})
        second = self.codex_response("beta-response", turn="beta-turn", usage={"input_tokens": 15, "output_tokens": 0})
        events = [self.codex_context(), first, self.codex_context("beta-turn", beta), second]
        self.codex_log("contexts.jsonl", events)
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 10), ("beta", 15)])
        second["payload"]["usage"]["input_tokens"] = -1
        self.codex_log("contexts.jsonl", events)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 10)
        self.assertEqual(self.cli("spend", "--org", "beta", "--json").returncode, 1)
        invalid_owner = self.codex_settings(1)
        invalid_owner["payload"]["thread_id"] = False
        for metadata, prefix in (({"id": False}, []), ({"forked_from_id": False}, []), ({}, [invalid_owner])):
            with self.subTest(metadata=metadata):
                path = self.codex_log("foreign-header.jsonl", [*prefix, self.codex_context(), self.codex_counter(3, 3)],
                                      cwd=beta, **metadata)
                before = path.read_bytes()
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(path.read_bytes(), before)
        path.unlink()
        conflicting = json.loads(json.dumps(second))
        conflicting["payload"]["turn_id"] = "other-beta-turn"
        conflicting["payload"]["usage"]["input_tokens"] = 15
        self.codex_log("contexts.jsonl", [*events, self.codex_context("other-beta-turn", beta), conflicting])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 10)
        self.assertEqual(self.cli("spend", "--org", "beta", "--json").returncode, 1)

    def test_native_codex_format_transition_preserves_order_at_equal_timestamps(self):
        timestamp = stamp()
        legacy = {"type": "event_msg", "timestamp": timestamp, "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": 4},
                                            "total_token_usage": {"input_tokens": 4}}}}
        response = self.codex_response(usage={"input_tokens": 10, "output_tokens": 0}, timestamp=timestamp)
        mirror = json.loads(json.dumps(legacy))
        mirror["payload"]["info"] = {"last_token_usage": False, "total_token_usage": {"input_tokens": 14}}
        self.codex_log("transition.jsonl", [self.codex_context(), legacy, response, mirror])
        self.codex_log("mirror-only.jsonl", [self.codex_context(), mirror])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 14)

    def test_native_codex_response_ids_are_scoped_to_threads_for_budgets(self):
        for thread in (1, 2):
            response = self.codex_response(thread=thread, usage={"cache_write_input_tokens": 3})
            response["payload"]["session_id"] = self.codex_thread(1)
            self.codex_log(f"thread-{thread}.jsonl", [self.codex_context(), response], thread=thread)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 30)
        self.assertEqual(self.cli("spend", "--org", "acme", "--set-budget", "20").returncode, 0)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("over its budget", " ".join(json.loads(result.stdout)["agent_budget"]))

    def test_native_codex_copied_responses_require_consistent_originals(self):
        original = self.codex_response()
        copied = json.loads(json.dumps(original))
        path = self.codex_log("child.jsonl", [self.codex_context(), copied], thread=2,
                              forked_from_id=self.codex_thread(1))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("no original receipt", result.stderr)
        self.codex_log("original.jsonl", [self.codex_context(), original])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 12)
        for usage in ({"input_tokens": -1}, {"input_tokens": 9, "output_tokens": 2, "cached_input_tokens": 0}):
            with self.subTest(usage=usage):
                copied["payload"]["usage"] = usage
                path = self.codex_log("child.jsonl", [self.codex_context(), copied], thread=2,
                                      forked_from_id=self.codex_thread(1))
                before = path.read_bytes()
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(path.read_bytes(), before)

    def test_native_codex_sessions_do_not_merge_identical_counter_snapshots(self):
        self.native_codex_usage({"input_tokens": 10, "output_tokens": 2})
        folder = self.dir / "codex/sessions"
        original = folder / "session.jsonl"
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        second = json.loads(json.dumps(rows))
        second[0]["payload"]["id"] = "00000000-0000-4000-8000-000000000002"
        (folder / "second.jsonl").write_text("\n".join(json.dumps(row) for row in second) + "\n")
        (folder / "copy.jsonl").write_bytes(original.read_bytes())
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 24)
        budget = self.cli("spend", "--org", "acme", "--set-budget", "20")
        self.assertEqual(budget.returncode, 0, budget.stderr)
        brief = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("over its budget", " ".join(json.loads(brief.stdout)["agent_budget"]))
        for identity in (False, 0, "", "  ", [], {}):
            with self.subTest(identity=identity):
                second[0]["payload"]["id"] = identity
                content = "\n".join(json.dumps(row) for row in second) + "\n"
                (folder / "second.jsonl").write_text(content)
                invalid = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(invalid.returncode, 1, invalid.stderr)
                self.assertIn("usage", invalid.stderr)
                self.assertEqual((folder / "second.jsonl").read_text(), content)

    def test_native_codex_replayed_counters_are_validated_before_deduplication(self):
        self.native_codex_usage({"input_tokens": 10, "output_tokens": 2})
        folder = self.dir / "codex/sessions"
        original = (folder / "session.jsonl").rename(folder / "a-original.jsonl")
        before = original.read_bytes()
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        replay = folder / "z-replay.jsonl"
        for usage in ({"input_tokens": -1}, {"input_tokens": True},
                      {"input_tokens": 10, "cached_input_tokens": 11},
                      {"input_tokens": 9, "output_tokens": 2}):
            with self.subTest(usage=usage):
                copied = json.loads(json.dumps(rows))
                copied[1]["payload"]["info"]["last_token_usage"] = usage
                content = "\n".join(json.dumps(row) for row in copied) + "\n"
                replay.write_text(content)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertEqual(replay.read_text(), content)
                self.assertEqual(original.read_bytes(), before)
            replay.unlink()
        valid = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(json.loads(valid.stdout)[0]["tokens"], 12)

    def test_native_codex_duplicate_timestamps_compare_as_instants(self):
        self.native_codex_usage({"input_tokens": 10})
        folder = self.dir / "codex/sessions"
        original = folder / "session.jsonl"
        rows = [json.loads(line) for line in original.read_text().splitlines()]
        instant = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(microsecond=0)
        for identified in (True, False):
            for name, stamp_ in (("session", instant.strftime("%Y-%m-%dT%H:%M:%SZ")),
                                 ("offset", instant.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()),
                                 ("fraction", instant.strftime("%Y-%m-%dT%H:%M:%S.000000Z"))):
                copied = json.loads(json.dumps(rows))
                if not identified:
                    copied[0]["payload"].pop("id")
                copied[1]["timestamp"] = stamp_
                (folder / (name + ".jsonl")).write_text("\n".join(json.dumps(row) for row in copied) + "\n")
            result = self.cli("spend", "--org", "acme", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["tokens"], 10)

    def test_native_codex_archived_usage_counts_without_duplicating_active_logs(self):
        self.native_codex_usage({"input_tokens": 10})
        active = self.dir / "codex/sessions"
        archive = active.rename(self.dir / "codex/archived_sessions")
        original = (archive / "session.jsonl").read_bytes()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 10)
        active.mkdir()
        (active / "copy.jsonl").write_bytes(original)
        other = [json.loads(line) for line in original.decode().splitlines()]
        other[0]["payload"]["cwd"] = str(orgs.workspace(self.home, "beta"))
        other[0]["payload"]["id"] = "00000000-0000-4000-8000-000000000002"
        content = "\n".join(json.dumps(row) for row in other) + "\n"
        (archive / "beta.jsonl").write_text(content)
        result = self.cli("spend", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(row["org"], row["tokens"]) for row in json.loads(result.stdout)], [("acme", 10), ("beta", 10)])
        self.assertEqual((archive / "session.jsonl").read_bytes(), original)
        saved = archive.rename(self.dir / "saved-archive")
        archive.write_text("preserved invalid archive path")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        self.assertEqual(archive.read_text(), "preserved invalid archive path")
        self.assertEqual((saved / "session.jsonl").read_bytes(), original)

    def test_native_usage_and_price_paths_cannot_silently_become_empty(self):
        configured = self.cli("spend", "--org", "acme", "--set-budget", "1000")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        paths = (self.dir / "claude/projects", self.dir / "codex/sessions", self.home / "prices.json")
        for path in paths:
            with self.subTest(path=path.name):
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.name == "prices.json":
                    path.mkdir()
                    (path / "keep.txt").write_text("preserved data")
                else:
                    path.write_text("preserved data")
                for args in (("spend", "--org", "acme"), ("brief", "--all-orgs")):
                    result = self.cli(*args, "--json")
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertNotIn('"agent_tokens": 0', result.stdout)
                brief = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
                self.assertEqual(brief.returncode, 0, brief.stderr)
                self.assertIn("could not check", " ".join(json.loads(brief.stdout)["agent_budget"]))
                if path.is_dir():
                    self.assertEqual((path / "keep.txt").read_text(), "preserved data")
                else:
                    self.assertEqual(path.read_text(), "preserved data")
            if path.is_dir():
                (path / "keep.txt").unlink()
                path.rmdir()
            else:
                path.unlink()
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)

    @unittest.skipIf(os.name == "nt", "Unix transcript permissions fixture")
    def test_native_usage_access_errors_cannot_report_partial_totals_as_complete(self):
        if os.geteuid() == 0:
            self.skipTest("privileged users can bypass the permissions fixture")
        self.claude_session(self.root, [("m1", 0, 5)])
        self.native_codex_usage({"input_tokens": 10})
        claude = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        codex = self.dir / "codex/sessions/blocked/session.jsonl"
        codex.parent.mkdir()
        (self.dir / "codex/sessions/session.jsonl").rename(codex)
        for path in (claude, codex):
            original = path.read_bytes()
            for target in (path.parent, path):
                with self.subTest(runtime=path.name, directory=target.is_dir()):
                    mode = target.stat().st_mode & 0o777
                    target.chmod(0)
                    try:
                        result = self.cli("spend", "--org", "acme", "--json")
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn("usage", result.stderr)
                    finally:
                        target.chmod(mode)
                    self.assertEqual(path.read_bytes(), original)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 126)

    @unittest.skipIf(os.name == "nt", "Unix pipe and symlink fixtures")
    def test_native_usage_handles_log_named_directories_and_refuses_pipes(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        self.native_codex_usage({"input_tokens": 10})
        project = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root)
        project = project.rename(project.with_name(project.name + ".jsonl"))
        codex = self.dir / "codex/sessions/nested.jsonl"
        codex.mkdir()
        (self.dir / "codex/sessions/session.jsonl").rename(codex / "session.jsonl")
        (project / "alias.jsonl").symlink_to(project / "s.jsonl")
        (codex / "alias.jsonl").symlink_to(codex / "session.jsonl")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 126)
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        for folder in (project, codex):
            pipe = folder / "blocked.jsonl"
            os.mkfifo(pipe, 0o600)
            try:
                result = subprocess.run([binary, "spend", "--org", "acme", "--json"],
                                        capture_output=True, text=True, env=self.cli_env(), timeout=3)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
            finally:
                pipe.unlink()

    def test_native_claude_usage_requires_an_object_when_present(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        path = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        original = json.loads(path.read_text())
        for usage in (False, 0, "", [], "synthetic-private-usage-value"):
            with self.subTest(usage=usage):
                row = json.loads(json.dumps(original))
                row["message"]["usage"] = usage
                path.write_text(json.dumps(row) + "\n")
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertNotIn("synthetic-private-usage-value", result.stdout + result.stderr)
        for usage in (None, {}):
            row = json.loads(json.dumps(original))
            row["message"]["usage"] = usage
            path.write_text(json.dumps(row) + "\n")
            result = self.cli("spend", "--org", "acme", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)
        path.write_text(json.dumps(original) + "\n")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 116)

    def native_codex_usage(self, usage):
        folder = self.dir / "codex/sessions"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "session.jsonl").write_text("\n".join(json.dumps(row) for row in (
            {"type": "session_meta", "payload": {"cwd": self.root, "id": "00000000-0000-4000-8000-000000000001"}},
            {"type": "event_msg", "timestamp": stamp(), "payload": {"type": "token_count", "info": {
                "total_token_usage": usage, "last_token_usage": usage}}},
        )) + "\n")

    def test_native_invalid_codex_counts_cannot_lower_the_reported_usage(self):
        invalid = (False, 0, "", [], {"input_tokens": 10, "cached_input_tokens": 20},
                   {"input_tokens": -10}, {"input_tokens": "10"},
                   {"input_tokens": True}, {"input_tokens": 1.5},
                   {"input_tokens": 2 ** 63}, {"input_tokens": 10, "output_tokens": -10}, "damaged", [10])
        for usage in invalid:
            with self.subTest(usage=usage):
                self.native_codex_usage(usage)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertNotIn("panicked", result.stderr)

    def test_native_all_org_brief_reports_usage_errors_instead_of_zero(self):
        self.native_codex_usage({"input_tokens": -10})
        result = self.cli("brief", "--all-orgs", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("usage", result.stderr)
        self.assertNotIn('"agent_tokens": 0', result.stdout)

    def test_native_token_overflow_is_reported_without_panicking(self):
        self.claude_session(self.root, [("m1", 0, 2 ** 63 - 1), ("m2", 0, 100)])
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("overflow", result.stderr)
        self.assertNotIn("panicked", result.stderr)

    def test_native_invalid_claude_counts_are_reported(self):
        folder = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root)
        for count in (-1, "10", True, 1.5, 2 ** 63):
            with self.subTest(count=count):
                if folder.exists():
                    (folder / "s.jsonl").unlink()
                self.claude_session(self.root, [("m1", 0, count)])
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("usage", result.stderr)
                self.assertNotIn("panicked", result.stderr)

    def test_native_claude_usage_requires_an_identity_for_deduplication(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        path = self.dir / "claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root) / "s.jsonl"
        row = json.loads(path.read_text())
        row["message"]["id"] = ""
        path.write_text(json.dumps(row) + "\n")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("id or uuid", result.stderr)
        row["uuid"] = "fallback-id"
        path.write_text(json.dumps(row) + "\n")
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 116)

    def test_native_codex_overflow_in_models_and_report_total_is_an_error(self):
        for separate_models in (False, True):
            with self.subTest(separate_models=separate_models):
                self.native_codex_usage({"input_tokens": 2 ** 63 - 1})
                path = self.dir / "codex/sessions/session.jsonl"
                with path.open("a") as out:
                    if separate_models:
                        out.write(json.dumps({"type": "turn_context", "payload": {"model": "another-model"}}) + "\n")
                    out.write(json.dumps({"type": "event_msg", "timestamp": stamp(), "payload": {"type": "token_count", "info": {
                        "total_token_usage": {"input_tokens": 2 ** 63}, "last_token_usage": {"input_tokens": 1}}}}) + "\n")
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("overflow", result.stderr)
                self.assertNotIn("panicked", result.stderr)

    def test_native_large_budget_is_not_converted_to_zero(self):
        self.claude_session(self.root, [("m1", 0, 100)])
        result = self.cli("spend", "--org", "acme", "--set-budget", str(2 ** 64 - 1))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.cli("brief", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["agent_budget"], [])

    def test_native_bad_prices_cannot_report_a_negative_or_overflowed_cost(self):
        self.claude_session(self.root, [("m1", 0, 5)])
        path = self.home / "prices.json"
        for price in (-1e6, 1e308):
            with self.subTest(price=price):
                content = json.dumps({"claude-opus-5-5": {"input": price}})
                path.write_text(content)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("panicked", result.stderr)
                self.assertEqual(path.read_text(), content)
        path.write_text(json.dumps({"claude-opus-5-5": {"input": 1e6}}))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["usd"], 5)

    def test_native_valid_codex_counts_keep_optional_counters_at_zero(self):
        for usage in (None, {}):
            self.native_codex_usage(usage)
            result = self.cli("spend", "--org", "acme", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)
        self.native_codex_usage({"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7})
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual(row["tokens"], 107)
        self.assertEqual([(m["input"], m["output"], m["cache_read"], m["cache_write"]) for m in row["models"]], [(80, 7, 20, 0)])

    def test_native_workspace_traversal_does_not_attribute_another_orgs_usage(self):
        escaped = self.root + "/../../beta/workspace"
        self.claude_session(escaped, [("other-org", 0, 5000)])
        self.native_codex_usage({"input_tokens": 5000})
        path = self.dir / "codex/sessions/session.jsonl"
        path.write_text(path.read_text().replace(self.root, escaped))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)

    @unittest.skipIf(os.name == "nt", "Unix directory symlink fixture")
    def test_native_workspace_symlinks_do_not_attribute_another_orgs_usage(self):
        alias = Path(self.root) / "other-org"
        alias.symlink_to(orgs.workspace(self.home, "beta"), target_is_directory=True)
        self.claude_session(str(alias), [("other-org", 0, 5000)])
        self.native_codex_usage({"input_tokens": 5000})
        path = self.dir / "codex/sessions/session.jsonl"
        path.write_text(path.read_text().replace(self.root, str(alias)))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)

    def test_native_deleted_workspace_subdirectories_still_count(self):
        cwd = self.root + "/deleted/directory"
        self.assertFalse(Path(cwd).exists())
        self.claude_session(cwd, [("m1", 0, 5)])
        self.native_codex_usage({"input_tokens": 10})
        path = self.dir / "codex/sessions/session.jsonl"
        path.write_text(path.read_text().replace(self.root, cwd))
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 126)

    def test_native_deleted_symlink_children_do_not_cross_workspace_boundaries(self):
        alias = Path(self.root) / "other-org"
        target = orgs.workspace(self.home, "beta") / "nested"
        target.mkdir()
        alias.symlink_to(target, target_is_directory=True)
        for suffix in ("deleted-directory", "../deleted-directory"):
            with self.subTest(suffix=suffix):
                cwd = str(alias) + "/" + suffix
                self.claude_session(cwd, [("synthetic-foreign", 0, 5000)])
                self.codex_log("deleted-symlink.jsonl", [self.codex_counter(5000, 5000)], cwd=cwd)
                result = self.cli("spend", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)

    def test_native_deleted_symlink_children_within_workspace_still_count(self):
        target = Path(self.root) / "nested"
        target.mkdir()
        alias = Path(self.root) / "alias"
        alias.symlink_to(target, target_is_directory=True)
        cwd = str(alias / "deleted-directory")
        self.claude_session(cwd, [("synthetic-local", 0, 5)])
        self.codex_log("deleted-symlink.jsonl", [self.codex_counter(10, 10)], cwd=cwd)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 126)

    def test_native_dangling_workspace_symlinks_are_not_attributed(self):
        alias = Path(self.root) / "dangling"
        alias.symlink_to(self.dir / "deleted-foreign-workspace", target_is_directory=True)
        cwd = str(alias / "deleted-directory")
        self.claude_session(cwd, [("synthetic-unknown", 0, 5000)])
        self.codex_log("dangling.jsonl", [self.codex_counter(5000, 5000)], cwd=cwd)
        result = self.cli("spend", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["tokens"], 0)

    def setUp(self):
        super().setUp()
        self.org()
        self.org("beta")
        self.root = str(orgs.workspace(self.home, "acme"))

    def claude_session(self, cwd, rows):
        folder = self.dir / "claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "s.jsonl").open("a") as out:
            for mid, days_ago, tokens in rows:
                out.write(json.dumps({"type": "assistant", "cwd": cwd, "timestamp": stamp(days_ago), "message": {
                    "id": mid, "model": "claude-opus-5-5", "content": "never read",
                    "usage": {"input_tokens": tokens, "output_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 1}}}) + "\n")

    def test_usage_is_attributed_by_workspace_and_window(self):
        # The same message streamed twice counts once; another org's and old messages do not count.
        self.claude_session(self.root, [("m1", 0, 5), ("m1", 0, 5), ("m2", 1, 7), ("m3", 40, 1000)])
        self.claude_session(str(orgs.workspace(self.home, "beta")), [("b1", 0, 999)])
        codex = self.dir / "codex" / "sessions" / "2026" / "10" / "14"
        codex.mkdir(parents=True)
        (codex / "rollout-1.jsonl").write_text("\n".join(json.dumps(x) for x in (
            {"type": "session_meta", "payload": {"cwd": self.root + "/knowledge", "timestamp": stamp()}},
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            {"type": "event_msg", "timestamp": stamp(40), "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1000}, "last_token_usage": {"input_tokens": 1000}}}},
            {"type": "event_msg", "timestamp": stamp(), "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1050, "output_tokens": 5},
                "last_token_usage": {"input_tokens": 50, "cached_input_tokens": 20, "output_tokens": 5}}}},
            {"type": "event_msg", "timestamp": stamp(), "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1080, "output_tokens": 9},
                "last_token_usage": {"input_tokens": 30, "cached_input_tokens": 10, "output_tokens": 4}}}})) + "\n")
        # A forked session replaying the same events counts them once.
        (codex / "rollout-2.jsonl").write_text((codex / "rollout-1.jsonl").read_text())
        result = spend.report(self.home, "acme", days=30)
        rows = {m["model"]: m for m in result["models"]}
        self.assertEqual((rows["claude-opus-5-5"]["input"], rows["claude-opus-5-5"]["output"], rows["claude-opus-5-5"]["cache_read"]), (12, 20, 200))
        # Only the in-window turns count: input 80 - cached 30, output 9.
        self.assertEqual((rows["gpt-6.1-sol"]["input"], rows["gpt-6.1-sol"]["cache_read"], rows["gpt-6.1-sol"]["output"]), (50, 30, 9))
        self.assertIsNone(result["usd"])
        (self.home / "prices.json").write_text(json.dumps({"claude-opus-5-5": {"input": 1e6, "output": 0, "cache_read": 0, "cache_write": 0},
                                                           "gpt-6.1-sol": {"input": 0, "output": 1e6, "cache_read": 0, "cache_write": 0}}))
        self.assertEqual(spend.report(self.home, "acme", days=30)["usd"], 21.0)

    def test_streamed_usage_and_synthetic_messages(self):
        folder = self.dir / "claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", self.root)
        folder.mkdir(parents=True)
        lines = [{"type": "assistant", "cwd": self.root, "timestamp": stamp(), "message": {"id": "m1", "model": "claude-opus-5-5",
                  "usage": {"input_tokens": 3, "output_tokens": out}}} for out in (1, 500)]
        lines.append({"type": "assistant", "cwd": self.root, "timestamp": "2026-10-14T08:00:00", "message": {
            "id": "s1", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}}})
        (folder / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        result = spend.report(self.home, "acme", days=30)
        self.assertEqual([(m["model"], m["output"]) for m in result["models"]], [("claude-opus-5-5", 500)])
        # A pinned agent cannot name another org.
        refused = self.cli("spend", "--org", "beta", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("pinned", refused.stderr)

    def test_a_budget_warns_in_the_brief_and_spend(self):
        self.claude_session(self.root, [("m1", 0, 5000)])
        set_ = self.cli("spend", "--org", "acme", "--set-budget", "1000", "--budget-days", "7")
        self.assertEqual(set_.returncode, 0, set_.stderr)
        self.assertIn("budget 1,000 tokens per 7 days", set_.stdout)
        self.assertIn("over its budget of 1,000", spend.over(self.home, "acme"))
        self.assertIn("over its budget", self.cli("brief", "--all-orgs").stdout)
        self.assertIsNone(spend.over(self.home, "beta"))
        self.assertEqual(self.cli("spend", "--org", "acme", "--set-budget", "5", RECKON_ORG="acme", RECKON_ENV="staging").returncode, 1)
        self.cli("spend", "--org", "acme", "--set-budget", "0")
        self.assertIsNone(spend.over(self.home, "acme"))
        # A hand-edited bad budget warns instead of breaking open, spend or the brief, and can be cleared.
        path = orgs.org_path(self.home, "acme") / "org.json"
        data = json.loads(path.read_text())
        data["agent_token_budget"] = {"tokens": True, "days": 0}
        path.write_text(json.dumps(data))
        self.assertEqual(self.cli("spend", "--org", "acme").returncode, 0)
        self.assertEqual(self.cli("brief", "--all-orgs").returncode, 0)
        self.assertIn("could not check the agent budget", self.cli("brief", "--org", "acme", "--env", "staging").stdout)
        with self.assertRaisesRegex(ValueError, "agent_token_budget needs"):
            orgs.save(self.home, data, self.registry)
        self.assertEqual(self.cli("spend", "--org", "acme", "--set-budget", "0").returncode, 0)
        self.assertEqual(self.cli("spend", "--set-budget", "5", "--all-orgs").returncode, 1)

    def test_all_orgs_spend_is_for_the_operator(self):
        refused = self.cli("spend", "--all-orgs", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(refused.returncode, 1)
        listed = json.loads(self.cli("spend", "--all-orgs", "--json").stdout)
        self.assertEqual([r["org"] for r in listed], ["acme", "beta"])
        brief = self.cli("brief", "--all-orgs").stdout
        self.assertIn("Agent tokens", brief)


if __name__ == "__main__":
    unittest.main()
