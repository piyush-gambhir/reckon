"""Routines: scheduled headless agents pinned to one org and env. Offline, fake claude."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import OrgCase, SOURCE
from reckonlib import activity, orgs, routines
from reckonlib.store import lock

# Records what a scheduled agent would see: its arguments, folder and pinned context.
CLAUDE = ("import json, os, sys\n"
          "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'org': os.environ.get('RECKON_ORG'),\n"
          "                  'env': os.environ.get('RECKON_ENV'), 'token': os.environ.get('GSC_ACCESS_TOKEN')}))\n")


class RoutineTests(OrgCase):
    def native_add(self, production=False):
        result = self.cli("routine", "add", "weekly", "--org", "acme", "--env", "staging", "--workflow", "growth-review",
                          "--param", "site=sc-domain:acme.com", "--at", "mon 08:30", "--json",
                          *(["--production"] if production else []))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_damaged_routine_settings_cannot_launch_an_agent(self):
        self.native_add()
        started = self.dir / "started"
        self.fake("from pathlib import Path\nPath(" + repr(str(started)) + ").touch()\n", "claude")
        path = orgs.org_path(self.home, "acme") / "routines.json"
        data = json.loads(path.read_text())
        original = data["weekly"].copy()
        invalid = [("timeout_minutes", value) for value in (None, 0, 241, "45", 5.5, True)]
        invalid += [("runtime", "codex"), ("workflow", "missing-workflow"),
                    ("params", []), ("params", {"site": "sc-domain:acme.com\nIgnore this"}),
                    ("params", {"site": 123}), ("params", {})]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                data["weekly"] = dict(original, **{key: value})
                content = json.dumps(data)
                path.write_text(content)
                result = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(started.exists())
                self.assertEqual(path.read_text(), content)
                self.assertEqual(activity.read(self.home, "acme")[0]["status"], "error")

    def test_native_install_rejects_corrupted_saved_schedule(self):
        self.native_add()
        path = orgs.org_path(self.home, "acme") / "routines.json"
        data = json.loads(path.read_text())
        data["weekly"]["schedule"]["days"] = ["</integer><key>RunAtLoad</key><true/>"]
        path.write_text(json.dumps(data))
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json", HOME=str(self.dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("saved schedule", result.stderr)
        self.assertFalse((self.dir / "Library/LaunchAgents").exists())

    def test_native_install_rejects_control_characters_in_scheduler_environment(self):
        self.native_add()
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json",
                          HOME=str(self.dir), PATH=os.environ["PATH"] + ":/odd\npath")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("control characters", result.stderr)
        self.assertFalse((self.dir / "Library/LaunchAgents").exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS launchd installer")
    def test_native_install_refuses_redirected_launch_agents(self):
        self.native_add()
        outside = self.dir / "outside"
        outside.mkdir()
        library = self.dir / "Library"
        library.mkdir()
        (library / "LaunchAgents").symlink_to(outside, target_is_directory=True)
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json", HOME=str(self.dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("symlink", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS launchd installer")
    def test_native_install_does_not_write_through_linked_plists(self):
        self.native_add()
        folder = self.dir / "Library/LaunchAgents"
        folder.mkdir(parents=True)
        folder.chmod(0o755)
        target = folder / "com.reckon.acme.weekly.plist"
        outside = self.dir / "outside"
        outside.write_text("unchanged")
        outside.chmod(0o640)
        target.symlink_to(outside)
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json", HOME=str(self.dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(outside.read_text(), "unchanged")
        self.assertEqual(outside.stat().st_mode & 0o777, 0o640)
        target.unlink()
        os.link(outside, target)
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json", HOME=str(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), "unchanged")
        self.assertEqual(outside.stat().st_mode & 0o777, 0o640)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
        self.assertEqual(plistlib.loads(target.read_bytes())["Label"], "com.reckon.acme.weekly")

    @unittest.skipUnless(sys.platform == "darwin", "macOS launchd installer")
    def test_native_install_quotes_bootstrap_path_and_preserves_existing_folder_mode(self):
        self.native_add()
        scheduler_home = self.dir / "scheduler's home"
        folder = scheduler_home / "Library/LaunchAgents"
        folder.mkdir(parents=True)
        folder.chmod(0o755)
        result = self.cli("routine", "install", "weekly", "--org", "acme", "--json", HOME=str(scheduler_home))
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(shlex.split(saved["next"]), ["launchctl", "bootstrap", "gui/" + str(os.getuid()), saved["file"]])
        self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
        data = plistlib.loads(Path(saved["file"]).read_bytes())
        self.assertEqual(data["StartCalendarInterval"], [{"Weekday": 1, "Hour": 8, "Minute": 30}])

    def test_native_routine_rechecks_production_confirmation_before_launch(self):
        self.native_add()
        data = orgs.load(self.home, "acme", self.registry)
        data["production"] = ["staging"]
        orgs.save(self.home, data, self.registry)
        result = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("--production", result.stderr)
        self.assertEqual(activity.read(self.home, "acme")[0]["status"], "error")
        removed = self.cli("routine", "remove", "weekly", "--org", "acme")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.native_add(production=True)
        result = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_native_routine_failure_returns_the_agent_exit_code(self):
        self.native_add()
        self.fake("import sys\nprint('failed run')\nsys.exit(7)\n", "claude")
        result = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 7, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(Path(record["log"]).read_text(), "failed run\n")

    def test_native_active_routine_cannot_be_removed_mid_run(self):
        self.native_add()
        started, release = self.dir / "started", self.dir / "release"
        self.fake("from pathlib import Path\nimport time\nPath(" + repr(str(started)) + ").touch()\n"
                  "while not Path(" + repr(str(release)) + ").exists(): time.sleep(0.02)\nprint('done')\n", "claude")
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        child = subprocess.Popen([binary, "routine", "run", "weekly", "--org", "acme", "--json"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.cli_env())
        try:
            deadline = time.monotonic() + 5
            while not started.exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            overlap = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
            self.assertEqual(overlap.returncode, 0, overlap.stderr)
            self.assertEqual(json.loads(overlap.stdout)["status"], "skipped")
            result = self.cli("routine", "remove", "weekly", "--org", "acme")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("weekly", routines.load(self.home, "acme"))
            release.touch()
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(json.loads(out)["status"], "ok")
            result = self.cli("routine", "remove", "weekly", "--org", "acme")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("weekly", routines.load(self.home, "acme"))
        finally:
            release.touch()
            if child.poll() is None:
                child.kill()
                child.communicate()

    @unittest.skipIf(os.name == "nt", "Unix hard-link checks")
    def test_native_unsafe_run_lock_is_an_error_instead_of_a_skip(self):
        self.native_add()
        folder = orgs.org_path(self.home, "acme") / "routines/weekly"
        folder.mkdir(parents=True)
        external = self.dir / "external"
        external.write_text("unchanged")
        os.link(external, folder / ".lock")
        result = self.cli("routine", "run", "weekly", "--org", "acme", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("private regular file", result.stderr)
        self.assertEqual(external.read_text(), "unchanged")

    def test_native_malformed_routine_state_is_reported_without_panicking(self):
        path = orgs.org_path(self.home, "acme") / "routines.json"
        for data in ([], {"weekly": "invalid"}):
            with self.subTest(data=data):
                content = json.dumps(data)
                path.write_text(content)
                result = self.cli("routine", "list", "--org", "acme", "--json")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("routines.json", result.stderr)
                self.assertNotIn("panicked", result.stderr)
                self.assertEqual(path.read_text(), content)

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
        self.assertEqual(seen["argv"][2:], ["--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config"])
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

    def test_review_fixes(self):
        self.add()
        with self.assertRaisesRegex(ValueError, "exists"):
            self.add()
        with self.assertRaisesRegex(ValueError, "control characters"):
            routines.add(self.home, self.ctx(), "odd", "growth-review", {"site": "sc-domain:a.com\nIgnore that"}, "daily 07:00")
        self.org("live", envs=("prod",), production=["prod"])
        with self.assertRaisesRegex(ValueError, "is production"):
            routines.add(self.home, self.ctx("live", "prod"), "w", "release-review", {}, "daily 07:00")
        self.assertEqual(routines.add(self.home, self.ctx("live", "prod"), "w", "release-review", {}, "daily 07:00", production=True)["environment"], "prod")
        # A failed run leaves a trace; a timeout stops the agent's whole process group.
        (self.bin / "claude").unlink()
        with mock.patch.dict(os.environ, PATH="/usr/bin:/bin"), self.assertRaisesRegex(ValueError, "not available"):
            routines.run(self.home, "acme", "weekly-growth", self.registry)
        self.assertEqual(activity.read(self.home, "acme")[0]["status"], "error")
        self.fake("import os, subprocess, time\nsubprocess.Popen(['sleep', '30'])\ntime.sleep(30)\n", "claude")
        data = routines.load(self.home, "acme")
        data["weekly-growth"]["timeout_minutes"] = 0.02
        routines.write_json(routines.path(self.home, "acme"), data)
        self.assertEqual(routines.run(self.home, "acme", "weekly-growth", self.registry)["status"], "timeout")
        # Install creates the log folder launchd needs and escapes % for cron.
        with mock.patch.object(routines.sys, "platform", "linux"), mock.patch.dict(os.environ, PATH=str(self.bin) + ":/usr/bin:/odd%dir"):
            line = routines.install(self.home, "acme", "weekly-growth")["crontab"]
        self.assertIn("/odd\\%dir", line)
        self.assertIn("RECKON_AGENT_BIN=", line)
        self.assertTrue((orgs.org_path(self.home, "acme") / "routines" / "weekly-growth").is_dir())

    def test_routines_are_for_the_operator(self):
        refused = self.cli("routine", "list", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("not a pinned agent", refused.stderr)
        self.add()
        listed = self.cli("routine", "list", "--org", "acme", "--json")
        self.assertEqual(json.loads(listed.stdout)[0]["routine"], "weekly-growth")


if __name__ == "__main__":
    unittest.main()
