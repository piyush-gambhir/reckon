"""Routines: scheduled headless agents pinned to one org and env. Offline, fake claude."""
import json
import os
from pathlib import Path
import plistlib
import unittest
from unittest import mock

from helpers import OrgCase
from reckonlib import activity, orgs, routines
from reckonlib.store import lock

# Records what a scheduled agent would see: its arguments, folder and pinned context.
CLAUDE = ("import json, os, sys\n"
          "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'org': os.environ.get('RECKON_ORG'),\n"
          "                  'env': os.environ.get('RECKON_ENV'), 'token': os.environ.get('GSC_ACCESS_TOKEN')}))\n")


class RoutineTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"gsc": {"provider": "gsc"}}, secrets={"gsc": ["GSC_ACCESS_TOKEN=gsc-token-1"]})
        self.fake(CLAUDE, "claude")

    def add(self):
        return routines.add(self.home, self.ctx(), "weekly-growth", "growth-review", {"site": "sc-domain:acme.com"}, "mon 08:30")

    def test_add_checks_the_recipe_and_schedule_up_front(self):
        with self.assertRaisesRegex(ValueError, "needs --param site"):
            routines.add(self.home, self.ctx(), "weekly", "growth-review", {}, "mon 08:30")
        with self.assertRaisesRegex(ValueError, "--at takes"):
            routines.add(self.home, self.ctx(), "weekly", "growth-review", {"site": "sc-domain:acme.com"}, "every monday")
        saved = self.add()
        self.assertEqual((saved["environment"], saved["schedule"]["days"], saved["schedule"]["hour"]), ("staging", [1], 8))
        self.assertEqual(routines.schedule("weekdays 07:00")["days"], [1, 2, 3, 4, 5])

    def test_a_run_is_a_pinned_headless_agent_without_credentials(self):
        self.add()
        result = routines.run(self.home, "acme", "weekly-growth", self.registry)
        self.assertEqual(result["status"], "ok")
        seen = json.loads(Path(result["log"]).read_text())
        self.assertEqual((seen["org"], seen["env"], seen["token"]), ("acme", "staging", None))
        self.assertEqual(seen["cwd"], os.path.realpath(orgs.workspace(self.home, "acme")))
        self.assertEqual(seen["argv"][0], "-p")
        self.assertIn("reckon task --workflow growth-review --param site=sc-domain:acme.com", seen["argv"][1])
        self.assertEqual(oct(os.stat(result["log"]).st_mode & 0o777), "0o600")
        self.assertEqual(activity.read(self.home, "acme")[0]["event"], "routine")

    def test_an_overlapping_run_is_skipped(self):
        self.add()
        with lock(orgs.org_path(self.home, "acme") / "routines" / "weekly-growth"):
            self.assertEqual(routines.run(self.home, "acme", "weekly-growth", self.registry)["status"], "skipped")

    def test_install_writes_a_schedule_but_does_not_load_it(self):
        self.add()
        with mock.patch.object(routines.sys, "platform", "linux"):
            line = routines.install(self.home, "acme", "weekly-growth")["crontab"]
        self.assertTrue(line.startswith("30 8 * * 1 "))
        self.assertIn("'routine' 'run' 'weekly-growth' '--org' 'acme'", line)
        with mock.patch.object(routines.sys, "platform", "darwin"), mock.patch.dict(os.environ, HOME=str(self.dir)):
            result = routines.install(self.home, "acme", "weekly-growth")
        data = plistlib.loads(Path(result["file"]).read_bytes())
        self.assertEqual(data["Label"], "com.reckon.acme.weekly-growth")
        self.assertEqual(data["StartCalendarInterval"], [{"Weekday": 1, "Hour": 8, "Minute": 30}])
        self.assertIn("launchctl bootstrap", result["next"])

    def test_routines_are_for_the_operator(self):
        refused = self.cli("routine", "list", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("not a pinned agent", refused.stderr)
        self.add()
        listed = self.cli("routine", "list", "--org", "acme", "--json")
        self.assertEqual(json.loads(listed.stdout)[0]["routine"], "weekly-growth")


if __name__ == "__main__":
    unittest.main()
