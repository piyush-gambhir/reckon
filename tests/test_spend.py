"""Agent usage per org from Claude Code and Codex session files. Offline fixtures."""
from datetime import datetime, timedelta, timezone
import json
import re
import unittest

from helpers import OrgCase
from reckonlib import orgs, spend


def stamp(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SpendTests(OrgCase):
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
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 50, "cached_input_tokens": 20, "output_tokens": 5}}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 80, "cached_input_tokens": 30, "output_tokens": 9}}}})) + "\n")
        result = spend.report(self.home, "acme", days=30)
        rows = {m["model"]: m for m in result["models"]}
        self.assertEqual((rows["claude-opus-5-5"]["input"], rows["claude-opus-5-5"]["output"], rows["claude-opus-5-5"]["cache_read"]), (12, 20, 200))
        self.assertEqual((rows["gpt-6.1-sol"]["input"], rows["gpt-6.1-sol"]["cache_read"], rows["gpt-6.1-sol"]["output"]), (50, 30, 9))
        self.assertIsNone(result["usd"])
        (self.home / "prices.json").write_text(json.dumps({"claude-opus-5-5": {"input": 1e6, "output": 0, "cache_read": 0, "cache_write": 0},
                                                           "gpt-6.1-sol": {"input": 0, "output": 1e6, "cache_read": 0, "cache_write": 0}}))
        self.assertEqual(spend.report(self.home, "acme", days=30)["usd"], 21.0)

    def test_all_orgs_spend_is_for_the_operator(self):
        refused = self.cli("spend", "--all-orgs", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(refused.returncode, 1)
        listed = json.loads(self.cli("spend", "--all-orgs", "--json").stdout)
        self.assertEqual([r["org"] for r in listed], ["acme", "beta"])
        brief = self.cli("brief", "--all-orgs").stdout
        self.assertIn("Agent tokens", brief)


if __name__ == "__main__":
    unittest.main()
