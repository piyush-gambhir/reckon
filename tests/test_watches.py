"""Watches poll saved reads into the inbox; the brief lists what is new. Offline, fake stripe."""
import json
import os
import unittest

from helpers import OrgCase
from reckonlib import activity, orgs, watches

# Disputes come from a file the test edits between polls.
STRIPE = ("import json, os\n"
          "print(open(os.environ['TMPDIR'] + '/../disputes.json').read() if os.path.exists(os.environ['TMPDIR'] + '/../disputes.json') else '{\"data\": []}')\n")


class WatchTests(OrgCase):
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


if __name__ == "__main__":
    unittest.main()
