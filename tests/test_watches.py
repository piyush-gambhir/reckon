"""Watches poll saved reads into the inbox; the brief lists what is new. Offline, fake stripe."""
import json
import os
import subprocess
import time
import unittest

from helpers import OrgCase, SOURCE
from reckonlib import activity, orgs, watches

# Disputes come from a file the test edits between polls.
STRIPE = ("import json, os\n"
          "print(open(os.environ['TMPDIR'] + '/../disputes.json').read() if os.path.exists(os.environ['TMPDIR'] + '/../disputes.json') else '{\"data\": []}')\n")


class WatchTests(OrgCase):
    def native_add(self, name="w", production=False):
        result = self.cli("watch", "add", name, "--org", "acme", "--env", "staging", "--connection", "stripe",
                          "--items", "data", "--json", *(["--production"] if production else []), "--", "disputes", "list")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_watch_removal_refuses_an_active_poll_without_losing_state(self):
        self.native_add()
        started, release = self.dir / "started", self.dir / "release"
        self.fake("from pathlib import Path\nimport time\nPath(" + repr(str(started)) + ").touch()\n"
                  "while not Path(" + repr(str(release)) + ").exists(): time.sleep(0.02)\nprint('{\"data\":[]}')\n", "stripe")
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        child = subprocess.Popen([binary, "watch", "run", "w", "--org", "acme", "--json"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        try:
            deadline = time.monotonic() + 5
            while not started.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            overlap = self.cli("watch", "run", "w", "--org", "acme", "--json")
            self.assertEqual(overlap.returncode, 0, overlap.stderr)
            self.assertEqual(json.loads(overlap.stdout)[0]["status"], "skipped")
            removed = self.cli("watch", "remove", "w", "--org", "acme", "--json")
            self.assertEqual(removed.returncode, 1, removed.stderr)
            self.assertIn("w", watches.load(self.home, "acme"))
            release.touch()
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(json.loads(out)[0]["status"], "baseline")
            removed = self.cli("watch", "remove", "w", "--org", "acme", "--json")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertNotIn("w", watches.load(self.home, "acme"))
        finally:
            release.touch()
            if child.poll() is None:
                child.kill()
                child.communicate()

    def test_native_watch_rechecks_production_confirmation_at_poll_time(self):
        self.native_add()
        data = orgs.load(self.home, "acme", self.registry)
        data["production"].append("staging")
        orgs.save(self.home, data, self.registry)
        result = self.cli("watch", "run", "w", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("--production", json.loads(result.stdout)[0]["detail"])
        self.assertFalse((self.home / "orgs/acme/runtime/watches/w/state.json").exists())
        removed = self.cli("watch", "remove", "w", "--org", "acme")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.native_add(production=True)
        result = self.cli("watch", "run", "w", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["status"], "baseline")

    def test_native_watch_starts_a_new_baseline_when_its_source_changes(self):
        self.native_add()
        args = ("watch", "run", "w", "--org", "acme", "--json")
        self.disputes("dp_1")
        first = self.cli(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)[0]["status"], "baseline")
        self.secret("acme", "stripe", ["STRIPE_API_KEY=rk_other_source"])
        self.disputes("dp_1", "dp_2")
        changed = self.cli(*args)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(json.loads(changed.stdout)[0]["status"], "baseline")
        self.assertEqual(json.loads(changed.stdout)[0]["new"], 0)
        self.disputes("dp_1", "dp_2", "dp_3")
        third = self.cli(*args)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(json.loads(third.stdout)[0]["new"], 1)
        self.assertEqual([row["id"] for row in watches.inbox(self.home, "acme")], ["dp_3"])

    def test_native_malformed_watch_state_is_reported_without_panicking_or_overwriting(self):
        path = orgs.org_path(self.home, "acme") / "watches.json"
        for data in ([], {"w": "invalid"}):
            with self.subTest(data=data):
                content = json.dumps(data)
                path.write_text(content)
                result = self.cli("watch", "list", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("watches.json", result.stderr)
                self.assertNotIn("panicked", result.stderr)
                self.assertEqual(path.read_text(), content)

    def setUp(self):
        super().setUp()
        self.org(envs=("staging", "prod"), production=["prod"], connections={"stripe": {"provider": "stripe"}},
                 secrets={"stripe": ["STRIPE_API_KEY=rk_live_secret_1"]})
        self.fake(STRIPE, "stripe")
        self.feed = orgs.org_path(self.home, "acme") / "runtime" / "stripe" / "disputes.json"

    def disputes(self, *ids):
        self.feed.parent.mkdir(parents=True, exist_ok=True)
        self.feed.write_text(json.dumps({"data": [{"id": i, "reason": "fraudulent " + i} for i in ids]}))

    def test_first_poll_is_a_baseline_then_new_items_reach_the_inbox_and_brief(self):
        added = self.cli("watch", "add", "disputes", "--org", "acme", "--env", "prod", "--connection", "stripe", "--items", "data",
                         "--title", "reason", "--production", "--", "disputes", "list", "--limit", "20")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.disputes("dp_1", "dp_2")
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["status"], "baseline")
        self.disputes("dp_1", "dp_2", "dp_3")
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["new"], 1)
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["new"], 0)
        items = watches.inbox(self.home, "acme", env="prod")
        self.assertEqual([(i["id"], i["title"]) for i in items], [("dp_3", "fraudulent dp_3")])
        self.assertEqual(watches.inbox(self.home, "acme", env="staging"), [])
        text = self.cli("brief", "--org", "acme", "--env", "prod").stdout
        self.assertIn("disputes: fraudulent dp_3", text)
        self.assertEqual(activity.read(self.home, "acme", env="prod", event="watch")[1]["new"], 1)

    def test_titles_are_escaped_and_a_changed_watch_restarts(self):
        watches.add(self.home, self.ctx(env="prod"), "d", "stripe", ["disputes", "list"], "data", "id", "reason", self.registry, True)
        self.disputes("dp_1")
        watches.run(self.home, "acme", self.registry)
        self.feed.write_text(json.dumps({"data": [{"id": "dp_1"}, {"id": "dp_2", "reason": "\x1b[2Jfake\nline"}]}))
        watches.run(self.home, "acme", self.registry)
        self.assertEqual(watches.inbox(self.home, "acme")[0]["title"], "\\x1b[2Jfake line")
        # Removing and adding it again starts from a baseline, not a flood.
        watches.remove(self.home, "acme", "d")
        watches.add(self.home, self.ctx(env="prod"), "d", "stripe", ["disputes", "list"], "data", "id", None, self.registry, True)
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["status"], "baseline")
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["pg"] = {"provider": "postgres"}
        orgs.save(self.home, data, self.registry)
        with self.assertRaisesRegex(ValueError, "cannot be watched"):
            watches.add(self.home, self.ctx(env="prod"), "pg", "pg", ["-c", "select 1"], "", "id", None, self.registry, True)

    def test_bad_watches_are_refused_or_reported(self):
        refused = self.cli("watch", "add", "w", "--org", "acme", "--env", "prod", "--connection", "stripe", "--", "charges", "create")
        self.assertEqual(refused.returncode, 1)
        refused = self.cli("watch", "add", "w", "--org", "acme", "--env", "prod", "--connection", "stripe", "--", "charges", "list")
        self.assertIn("pass --production", refused.stderr)
        watches.add(self.home, self.ctx(env="prod"), "w", "stripe", ["charges", "list"], "missing", "id", registry=self.registry, production=True)
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["status"], "error")
        pinned = self.cli("watch", "run", "--org", "acme", RECKON_ORG="acme", RECKON_ENV="prod")
        self.assertEqual(pinned.returncode, 1)
        self.assertEqual(self.cli("watch", "list", "--org", "acme", RECKON_ORG="", RECKON_ENV="").returncode, 1)
        # The agent may read its own environment's inbox.
        self.assertEqual(self.cli("inbox", RECKON_ORG="acme", RECKON_ENV="prod").returncode, 0)

    def test_a_watch_reads_its_own_connection_from_a_linked_folder(self):
        self.disputes("dp_1")
        watches.add(self.home, self.ctx(env="staging"), "w", "stripe", ["disputes", "list"], "data", "id", registry=self.registry)
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["stripe-b"] = {"provider": "stripe"}
        orgs.save(self.home, data, self.registry)
        repo = self.dir / "repo"
        repo.mkdir()
        # A link made before stripe existed (reckon link refuses it now) prefers stripe-b;
        # the saved watch still reads stripe, wherever it is polled from.
        orgs.write(self.home / "links.json", json.dumps({"schema_version": 1, "links": {str(orgs.folder(repo)): {
            "org": "acme", "env": "staging", "use": ["stripe-b"]}}}))
        here = os.getcwd()
        os.chdir(repo)
        self.addCleanup(os.chdir, here)
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["status"], "baseline")

    def test_a_removed_connection_is_not_replaced_by_another_of_its_tool(self):
        watches.add(self.home, self.ctx(env="staging"), "w", "stripe", ["disputes", "list"], "data", "id", registry=self.registry)
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["stripe-other"] = data["connections"].pop("stripe")
        orgs.save(self.home, data, self.registry)
        result = watches.run(self.home, "acme", self.registry)[0]
        self.assertEqual(result["status"], "error")
        self.assertIn("no longer exists", result["detail"])

    @unittest.skipIf(os.name == "nt", "Unix hard-link and mode checks")
    def test_native_inbox_refuses_hard_links_without_acknowledging_new_items(self):
        watches.add(self.home, self.ctx(), "w", "stripe", ["disputes", "list"], "data", "id", registry=self.registry)
        self.disputes("dp_1")
        args = ("watch", "run", "w", "--org", "acme", "--json")
        baseline = self.cli(*args)
        self.assertEqual(json.loads(baseline.stdout)[0]["status"], "baseline", baseline.stderr)
        external = self.dir / "external"
        external.write_text("unchanged")
        external.chmod(0o644)
        inbox = orgs.workspace(self.home, "acme") / "inbox.jsonl"
        os.link(external, inbox)
        self.disputes("dp_1", "dp_2")
        refused = self.cli(*args)
        self.assertEqual(json.loads(refused.stdout)[0]["status"], "error", refused.stderr)
        self.assertEqual(external.read_text(), "unchanged")
        self.assertEqual(external.stat().st_mode & 0o777, 0o644)
        inbox.unlink()
        retried = self.cli(*args)
        self.assertEqual(json.loads(retried.stdout)[0]["new"], 1, retried.stderr)
        self.assertEqual(inbox.stat().st_mode & 0o777, 0o600)

    def test_native_concurrent_watches_keep_every_inbox_record_intact(self):
        names = ["w" + str(i) for i in range(4)]
        for name in names:
            watches.add(self.home, self.ctx(), name, "stripe", ["disputes", "list"], "data", "id", registry=self.registry)
        baseline = self.cli("watch", "run", "--org", "acme", "--json")
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertTrue(all(r["status"] == "baseline" for r in json.loads(baseline.stdout)))
        self.disputes(*(str(i) + "x" * 180 for i in range(100)))
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        children = [subprocess.Popen([binary, "watch", "run", name, "--org", "acme", "--json"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env()) for name in names]
        try:
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                self.assertEqual(json.loads(out)[0]["new"], 100)
            inbox = orgs.workspace(self.home, "acme") / "inbox.jsonl"
            rows = [json.loads(line) for line in inbox.read_text().splitlines()]
            self.assertEqual(len(rows), 400)
            self.assertEqual({name: sum(row["watch"] == name for row in rows) for name in names}, dict.fromkeys(names, 100))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()


if __name__ == "__main__":
    unittest.main()
