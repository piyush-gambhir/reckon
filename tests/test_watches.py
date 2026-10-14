"""Watches poll saved reads into the inbox; the brief lists what is new. Offline, fake stripe."""
import json
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
                         "--title", "reason", "--", "disputes", "list", "--limit", "20")
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

    def test_bad_watches_are_refused_or_reported(self):
        refused = self.cli("watch", "add", "w", "--org", "acme", "--env", "prod", "--connection", "stripe", "--", "charges", "create")
        self.assertEqual(refused.returncode, 1)
        watches.add(self.home, self.ctx(env="prod"), "w", "stripe", ["charges", "list"], "missing", "id", registry=self.registry)
        self.assertEqual(watches.run(self.home, "acme", self.registry)[0]["status"], "error")
        pinned = self.cli("watch", "run", "--org", "acme", RECKON_ORG="acme", RECKON_ENV="prod")
        self.assertEqual(pinned.returncode, 1)
        # The agent may read its own environment's inbox.
        self.assertEqual(self.cli("inbox", RECKON_ORG="acme", RECKON_ENV="prod").returncode, 0)


if __name__ == "__main__":
    unittest.main()
