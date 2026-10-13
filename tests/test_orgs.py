"""Orgs, connections, secrets, child environments, argument checks, reckon run, workspace and migration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import CUBEAPM, JENKINS, OrgCase, SOURCE, sha256, tree
from reckonlib import connectors, migrate, orgs, workspace
from reckonlib.process import run as bounded
from reckonlib.store import create, evidence, write, write_json


class RepositoryBoundaryTests(unittest.TestCase):
    def test_tenant_state_is_never_tracked(self):
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=SOURCE).decode().split("\0")
        private_roots = {".config", ".reckon-env", ".reckon-demo", "infra-knowledge", "incidents", "sessions", "bin"}
        for name in filter(None, tracked):
            path = Path(name)
            self.assertNotIn(path.parts[0], private_roots, name)
            self.assertFalse(path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example", name)


class OrgTests(OrgCase):
    def test_org_names_environments_and_production_are_validated(self):
        for bad in ("Acme", "-acme", "a_b", "", "a" * 41, "../x"):
            with self.subTest(org=bad), self.assertRaises(ValueError):
                orgs.create(self.home, bad, None, ["prod"], [], self.registry)
        with self.assertRaisesRegex(ValueError, "unique"):
            orgs.create(self.home, "acme", None, ["prod", "prod"], [], self.registry)
        with self.assertRaisesRegex(ValueError, "environment names"):
            orgs.create(self.home, "acme", None, ["Prod"], [], self.registry)
        with self.assertRaisesRegex(ValueError, "production must list"):
            orgs.create(self.home, "acme", None, ["staging"], ["prod"], self.registry)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            orgs.create(self.home, "acme", None, [], [], self.registry)
        self.assertEqual(orgs.orgs(self.home), [])
        self.org("acme")
        with self.assertRaisesRegex(ValueError, "already exists"):
            orgs.create(self.home, "acme", None, ["prod"], [], self.registry)

    def test_production_defaults_to_prod_and_production_names(self):
        data = orgs.create(self.home, "acme", "Acme Inc", ["prod", "staging"], None, self.registry)
        self.assertEqual(data["production"], ["prod"])
        self.assertEqual(data["name"], "Acme Inc")
        self.assertTrue(self.ctx("acme", "prod")["production"])
        self.assertFalse(self.ctx("acme", "staging")["production"])

    def test_org_directories_are_private(self):
        self.org()
        base = self.home / "orgs" / "acme"
        for directory in ("secrets", "config", "runtime", "workspace"):
            self.assertEqual((base / directory).stat().st_mode & 0o777, 0o700, directory)

    def test_connection_shapes_are_validated(self):
        self.org()
        data = orgs.load(self.home, "acme", self.registry)
        cases = {
            "unknown provider": {"provider": "nope"},
            "env and env_labels": {"provider": "jenkins", "env": "staging", "env_labels": {"staging": "S"}},
            "env outside the org": {"provider": "jenkins", "env": "prod"},
            "label for unknown env": {"provider": "cubeapm", "env_labels": {"prod": "PROD"}},
            "bad label": {"provider": "cubeapm", "env_labels": {"staging": "has space"}},
            "extra field": {"provider": "cubeapm", "token": "x"},
        }
        for title, conn in cases.items():
            with self.subTest(title):
                data["connections"] = {"c": conn}
                with self.assertRaises(ValueError):
                    orgs.save(self.home, data, self.registry)
        data["connections"] = {"Bad_Name": {"provider": "cubeapm"}}
        with self.assertRaises(ValueError):
            orgs.save(self.home, data, self.registry)

    def test_context_precedence_arguments_then_pinned_then_saved_file(self):
        self.org("acme", ("staging", "uat"))
        self.org("other", ("dev",))
        orgs.set_context(self.home, "acme", "uat", self.registry)
        saved = orgs.context(self.home, registry=self.registry)
        self.assertEqual((saved["org"], saved["env"]), ("acme", "uat"))
        self.assertIn("context", saved["source"])
        os.environ.update(RECKON_ORG="acme", RECKON_ENV="staging")
        pinned = orgs.context(self.home, registry=self.registry)
        self.assertEqual((pinned["org"], pinned["env"], pinned["source"]), ("acme", "staging", "RECKON_ORG/RECKON_ENV"))
        # A pinned agent cannot name another org or environment...
        for org, env in (("other", "dev"), ("acme", "uat"), ("other", None)):
            with self.assertRaisesRegex(ValueError, "pinned to acme/staging"):
                orgs.context(self.home, org, env, self.registry)
        # ...but may name its own, and an org named alone keeps the pinned environment.
        self.assertEqual(orgs.context(self.home, "acme", "staging", self.registry)["source"], "arguments")
        self.assertEqual(orgs.context(self.home, "acme", None, self.registry)["env"], "staging")
        os.environ.pop("RECKON_ORG")
        os.environ.pop("RECKON_ENV")
        explicit = orgs.context(self.home, "other", "dev", self.registry)
        self.assertEqual((explicit["org"], explicit["env"], explicit["source"]), ("other", "dev", "arguments"))
        # Outside an agent, an org named alone uses the saved environment, never another org's.
        self.assertEqual(orgs.context(self.home, "acme", None, self.registry)["env"], "uat")
        with self.assertRaisesRegex(ValueError, "no environment selected"):
            orgs.context(self.home, "other", None, self.registry)

    def test_missing_unknown_and_ambiguous_context_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no org selected"):
            orgs.context(self.home, registry=self.registry)
        self.org("acme", ("staging", "uat"))
        with self.assertRaisesRegex(ValueError, "no org selected"):
            orgs.context(self.home, registry=self.registry)
        with self.assertRaisesRegex(ValueError, "unknown org"):
            orgs.context(self.home, "ghost", "staging", self.registry)
        with self.assertRaisesRegex(ValueError, "no environment selected"):
            orgs.context(self.home, "acme", None, self.registry)
        with self.assertRaisesRegex(ValueError, "no environment"):
            orgs.context(self.home, "acme", "prod", self.registry)
        os.environ.update(RECKON_ORG="acme")
        with self.assertRaisesRegex(ValueError, "no environment selected"):
            orgs.context(self.home, registry=self.registry)
        os.environ.update(RECKON_ENV="nonsense")
        with self.assertRaisesRegex(ValueError, "no environment"):
            orgs.context(self.home, registry=self.registry)

    def test_folder_of_another_org_is_rejected(self):
        self.org("acme")
        self.org("other", ("dev",))
        foreign = self.home / "orgs" / "other" / "workspace" / "sessions"
        own = self.home / "orgs" / "acme" / "workspace" / "sessions"
        with self.assertRaisesRegex(ValueError, "belongs to org other"):
            orgs.context(self.home, "acme", "staging", self.registry, cwd=foreign)
        self.assertEqual(orgs.context(self.home, "acme", "staging", self.registry, cwd=own)["org"], "acme")
        self.assertEqual(orgs.context(self.home, "acme", "staging", self.registry, cwd=self.dir)["org"], "acme")
        # Secrets live beside the workspace, not in it: only the workspace folder pins an org.
        self.assertEqual(orgs.context(self.home, "acme", "staging", self.registry, cwd=self.home / "orgs" / "other" / "secrets")["org"], "acme")

    def test_set_context_rejects_unknown_org_and_environment(self):
        self.org("acme")
        with self.assertRaisesRegex(ValueError, "no environment"):
            orgs.set_context(self.home, "acme", "prod", self.registry)
        with self.assertRaisesRegex(ValueError, "unknown org"):
            orgs.set_context(self.home, "ghost", "staging", self.registry)
        self.assertFalse((self.home / "context").exists())
        orgs.set_context(self.home, "acme", "uat", self.registry)
        self.assertEqual((self.home / "context").read_text(), "acme/uat\n")

    def test_cli_org_add_use_and_status(self):
        bad = self.cli("org", "add", "Bad_Name")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("org names", bad.stderr)
        for args in (["org", "add", "acme", "--envs", "prod,prod"], ["org", "add", "acme", "--envs", "uat", "--production", "prod"]):
            self.assertNotEqual(self.cli(*args).returncode, 0, args)
        added = self.cli("org", "add", "acme", "--envs", "prod,uat", "--json")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["production"], ["prod"])
        self.assertEqual(self.cli("use", "acme/nope").returncode, 1)
        self.assertEqual(self.cli("use", "acme/uat").returncode, 0)
        status = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual((status["org"], status["environment"]), ("acme", "uat"))
        pinned = json.loads(self.cli("status", "--json", RECKON_ORG="acme", RECKON_ENV="prod").stdout)
        self.assertEqual(pinned["environment"], "prod")
        self.assertTrue(pinned["production"])
        self.assertEqual(pinned["resolved_from"], "RECKON_ORG/RECKON_ENV")

    def test_cli_without_any_context_fails_closed(self):
        result = self.cli("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no org selected", result.stderr)


class ResolveTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"},
                              "jenkins-uat": {"provider": "jenkins", "env": "uat"},
                              "cubeapm": {"provider": "cubeapm", "env_labels": {"staging": "STAG", "uat": "UAT"}}})

    def test_provider_resolves_to_the_one_connection_for_the_environment(self):
        self.assertEqual(orgs.resolve(self.ctx("acme", "staging"), "jenkins")[0], "jenkins-staging")
        self.assertEqual(orgs.resolve(self.ctx("acme", "uat"), "jenkins")[0], "jenkins-uat")
        self.assertEqual(orgs.resolve(self.ctx("acme", "uat"), "cubeapm")[0], "cubeapm")
        self.assertEqual(orgs.resolve(self.ctx("acme", "staging"), "jenkins-staging")[1]["provider"], "jenkins")

    def test_connection_bound_to_another_environment_is_refused(self):
        with self.assertRaisesRegex(ValueError, "belongs to uat"):
            orgs.resolve(self.ctx("acme", "staging"), "jenkins-uat")

    def test_two_candidates_are_ambiguous(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["jenkins-shared"] = {"provider": "jenkins"}
        orgs.save(self.home, data, self.registry)
        with self.assertRaisesRegex(ValueError, "several jenkins connections"):
            orgs.resolve(self.ctx("acme", "staging"), "jenkins")
        self.assertEqual(orgs.resolve(self.ctx("acme", "staging"), "jenkins-shared")[0], "jenkins-shared")

    def test_no_connection_names_reckon_connect(self):
        with self.assertRaisesRegex(ValueError, "reckon connect"):
            orgs.resolve(self.ctx("acme", "staging"), "grafana")


class SecretsTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins": {"provider": "jenkins"}})
        self.spec = self.registry["jenkins"]

    def read(self):
        return orgs.secrets(self.home, "acme", "jenkins", self.spec)

    def test_missing_file_is_empty_and_not_configured(self):
        self.assertEqual(self.read(), {})
        self.assertFalse(orgs.configured(self.home, "acme", "jenkins", self.spec))

    def test_values_are_literal_and_never_shell_evaluated(self):
        pwned = self.dir / "pwned"
        self.secret("acme", "jenkins", ["JENKINS_TOKEN=$(touch " + str(pwned) + ")", "JENKINS_URL=`touch " + str(pwned) + "`"])
        values = self.read()
        self.assertEqual(values["JENKINS_TOKEN"], "$(touch " + str(pwned) + ")")
        self.assertEqual(values["JENKINS_URL"], "`touch " + str(pwned) + "`")
        self.assertFalse(pwned.exists())

    def test_quotes_comments_blank_lines_and_export_prefix(self):
        self.secret("acme", "jenkins", ["# comment", "", "JENKINS_URL=\"https://j.invalid\"", "export JENKINS_USER='bob'",
                                        "JENKINS_TOKEN = tok-1", "JENKINS_INSECURE=true"])
        # "KEY = value" is not KEY=value
        with self.assertRaisesRegex(ValueError, "expected KEY=value"):
            self.read()
        self.secret("acme", "jenkins", ["# comment", "", "JENKINS_URL=\"https://j.invalid\"", "export JENKINS_USER='bob'", "JENKINS_TOKEN=tok-1"])
        self.assertEqual(self.read(), {"JENKINS_URL": "https://j.invalid", "JENKINS_USER": "bob", "JENKINS_TOKEN": "tok-1"})

    def test_key_outside_the_providers_prefixes_is_rejected_without_echoing_the_value(self):
        self.secret("acme", "jenkins", ["JENKINS_URL=https://j.invalid", "AWS_SECRET_ACCESS_KEY=super-secret-value"])
        with self.assertRaises(ValueError) as caught:
            self.read()
        message = str(caught.exception)
        self.assertIn("AWS_SECRET_ACCESS_KEY", message)
        self.assertNotIn("super-secret-value", message)

    def test_malformed_line_error_does_not_leak_the_line(self):
        self.secret("acme", "jenkins", ["this-contains-super-secret-value"])
        with self.assertRaises(ValueError) as caught:
            self.read()
        self.assertNotIn("super-secret-value", str(caught.exception))

    def test_group_or_world_readable_file_is_rejected(self):
        for mode in (0o640, 0o604, 0o644, 0o660):
            with self.subTest(mode=oct(mode)):
                self.secret("acme", "jenkins", ["JENKINS_TOKEN=super-secret-value"], mode=mode)
                with self.assertRaises(ValueError) as caught:
                    self.read()
                self.assertIn("chmod 600", str(caught.exception))
                self.assertNotIn("super-secret-value", str(caught.exception))

    def test_placeholders_and_empty_values_are_not_configured(self):
        for value in ("", "replace_me", "https://jenkins.example.com", "changeme", "<your token>"):
            with self.subTest(value=value):
                self.secret("acme", "jenkins", ["JENKINS_URL=" + value, "JENKINS_USER=bob", "JENKINS_TOKEN=" + (value or "")])
                self.assertFalse(orgs.configured(self.home, "acme", "jenkins", self.spec))
        self.secret("acme", "jenkins", JENKINS)
        self.assertTrue(orgs.configured(self.home, "acme", "jenkins", self.spec))

    def test_alternative_required_keys_either_one_satisfies(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["grafana"] = {"provider": "grafana"}
        orgs.save(self.home, data, self.registry)
        spec = self.registry["grafana"]
        self.secret("acme", "grafana", ["GRAFANA_URL=https://g.invalid", "GRAFANA_PASSWORD=pw-123"])
        self.assertTrue(orgs.configured(self.home, "acme", "grafana", spec))
        self.secret("acme", "grafana", ["GRAFANA_URL=https://g.invalid"])
        self.assertFalse(orgs.configured(self.home, "acme", "grafana", spec))

    def test_saved_cli_profile_counts_as_configured_only_when_non_empty(self):
        config = self.home / "orgs" / "acme" / "config" / "jenkins" / "jenkins-cli"
        config.mkdir(parents=True)
        (config / "config.yaml").write_text("")
        self.assertFalse(orgs.configured(self.home, "acme", "jenkins", self.spec))
        (config / "config.yaml").write_text("server: https://j.invalid\n")
        self.assertTrue(orgs.configured(self.home, "acme", "jenkins", self.spec))

    def test_redact_hides_secret_values_urls_with_passwords_and_bearer_tokens(self):
        values = {"JENKINS_TOKEN": "tok-abcdef", "JENKINS_USER": "bob", "PWD": "/work/reckon", "MYSQL_PWD": "db-secret"}
        text = "tok-abcdef bob https://u:pw123@host/x Authorization: Bearer abc.def-ghi /work/reckon db-secret"
        out = orgs.redact(text, values)
        self.assertNotIn("tok-abcdef", out)
        self.assertNotIn("pw123", out)
        self.assertNotIn("abc.def-ghi", out)
        self.assertNotIn("db-secret", out)
        self.assertIn("bob", out)
        self.assertIn("/work/reckon", out)

    def test_template_creates_private_commented_file_once(self):
        path, created = connectors.template(self.home, "acme", "jenkins", self.spec)
        self.assertTrue(created)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("JENKINS_TOKEN=", path.read_text())
        self.assertEqual(self.read()["JENKINS_TOKEN"], "")
        path.write_text("JENKINS_TOKEN=mine\n")
        self.assertFalse(connectors.template(self.home, "acme", "jenkins", self.spec)[1])
        self.assertEqual(path.read_text(), "JENKINS_TOKEN=mine\n")


class ChildEnvTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"},
                              "github": {"provider": "github"},
                              "gsc": {"provider": "gsc"},
                              "cubeapm": {"provider": "cubeapm", "env_labels": {"staging": "STAG", "uat": "UAT"}}},
                 secrets={"jenkins-staging": JENKINS, "github": ["GH_TOKEN=conn-gh-token"]})
        os.environ.update(GH_TOKEN="shell-gh-token", AWS_SECRET_ACCESS_KEY="shell-aws", JENKINS_TOKEN="shell-jenkins", LANG="C")
        self.ctx_ = self.ctx()

    def child(self, name, interactive=False):
        conn = self.ctx_["data"]["connections"][name]
        return orgs.child_env(self.home, self.ctx_, name, conn, self.registry[conn["provider"]], interactive=interactive)

    def test_only_base_keys_pass_through_from_the_shell(self):
        env, _ = self.child("cubeapm")
        for key in ("GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "JENKINS_TOKEN"):
            self.assertNotIn(key, env)
        self.assertEqual(env["LANG"], "C")
        self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_shell_credentials_never_substitute_for_connection_secrets(self):
        env, values = self.child("jenkins-staging")
        self.assertEqual(env["JENKINS_TOKEN"], "jenkins-token-123")
        self.assertEqual(values["JENKINS_TOKEN"], "jenkins-token-123")
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        gh, _ = self.child("github")
        self.assertEqual(gh["GH_TOKEN"], "conn-gh-token")
        self.assertNotIn("JENKINS_TOKEN", gh)

    def test_private_home_config_cache_and_tmp_per_connection(self):
        env, _ = self.child("jenkins-staging")
        runtime = self.home / "orgs" / "acme" / "runtime" / "jenkins-staging"
        for key in ("HOME", "USERPROFILE", "XDG_CACHE_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA", "TMPDIR", "TMP", "TEMP"):
            directory = Path(env[key])
            directory.relative_to(runtime)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700, key)
        config = Path(env["XDG_CONFIG_HOME"])
        self.assertEqual(config, self.home / "orgs" / "acme" / "config" / "jenkins-staging")
        self.assertEqual(config.stat().st_mode & 0o777, 0o700)
        other, _ = self.child("github")
        self.assertNotEqual(other["HOME"], env["HOME"])
        self.assertNotEqual(other["XDG_CONFIG_HOME"], env["XDG_CONFIG_HOME"])
        probe = "import os; from pathlib import Path; (Path(os.environ['TMPDIR'])/'marker').write_text('private'); print(Path.home())"
        result = bounded([sys.executable, "-c", probe], env=env)
        self.assertEqual(result["status"], "ok", result["stderr"])
        self.assertEqual(result["stdout"].decode().strip(), env["HOME"])
        self.assertEqual((Path(env["TMPDIR"]) / "marker").read_text(), "private")

    def test_read_only_switches_are_forced_over_the_secrets_file(self):
        self.secret("acme", "jenkins-staging", JENKINS + ["JENKINS_READ_ONLY=false", "JENKINS_NO_INPUT=false"])
        env, values = self.child("jenkins-staging")
        self.assertEqual(values["JENKINS_READ_ONLY"], "false")
        self.assertEqual(env["JENKINS_READ_ONLY"], "true")
        self.assertEqual(env["JENKINS_NO_INPUT"], "true")
        self.assertEqual(env["CI"], "1")

    def test_interactive_login_does_not_force_non_interactive_switches(self):
        env, _ = self.child("jenkins-staging", interactive=True)
        self.assertNotIn("CI", env)
        self.assertNotEqual(env.get("JENKINS_READ_ONLY"), "true")

    def test_gsc_keeps_the_real_home_but_has_its_own_config(self):
        os.environ["HOME"] = str(self.dir / "real-home")
        env, _ = self.child("gsc")
        self.assertEqual(env["HOME"], str(self.dir / "real-home"))
        self.assertEqual(env["XDG_CONFIG_HOME"], str(self.home / "orgs" / "acme" / "config" / "gsc"))
        self.assertTrue(Path(env["TMPDIR"]).is_relative_to(self.home) if hasattr(Path, "is_relative_to") else str(self.home) in env["TMPDIR"])
        other, _ = self.child("github")
        self.assertNotEqual(other["HOME"], os.environ["HOME"])

    def test_env_labels_set_the_label_and_pinned_context(self):
        env, _ = self.child("cubeapm")
        self.assertEqual(env["RECKON_ENV_LABEL"], "STAG")
        self.assertEqual((env["RECKON_ORG"], env["RECKON_ENV"], env["RECKON_CONNECTION"], env["RECKON_HOME"]),
                         ("acme", "staging", "cubeapm", str(self.home)))
        plain, _ = self.child("github")
        self.assertNotIn("RECKON_ENV_LABEL", plain)
        self.ctx_ = self.ctx("acme", "uat")
        self.assertEqual(self.child("cubeapm")[0]["RECKON_ENV_LABEL"], "UAT")

    def test_provider_paths_point_into_the_connection_config(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["aws"] = {"provider": "aws"}
        orgs.save(self.home, data, self.registry)
        self.ctx_ = self.ctx()
        env, _ = self.child("aws")
        config = self.home / "orgs" / "acme" / "config" / "aws"
        self.assertEqual(env["AWS_CONFIG_FILE"], str(config / "aws" / "config"))
        self.assertEqual(env["AWS_SHARED_CREDENTIALS_FILE"], str(config / "aws" / "credentials"))


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from helpers import REGISTRY
        cls.registry = REGISTRY

    def ok(self, provider, args, label=None):
        connectors.check(provider, self.registry[provider], args, label)

    def refused(self, provider, args, label=None, pattern=""):
        with self.assertRaisesRegex(ValueError, pattern):
            connectors.check(provider, self.registry[provider], args, label)

    def test_denied_flags_exact_equals_and_short_prefix(self):
        self.refused("jenkins", ["job", "list", "--server", "https://other.invalid"], pattern="--server")
        self.refused("jenkins", ["job", "list", "--server=https://other.invalid"], pattern="--server")
        self.refused("jenkins", ["job", "list", "--read-only=false"], pattern="--read-only")
        self.ok("jenkins", ["job", "list", "--status", "FAILURE", "-o", "json"])
        self.refused("kubernetes", ["get", "pods", "-s", "https://evil.invalid"], pattern="-s")
        self.refused("kubernetes", ["get", "pods", "-shttps://evil.invalid"], pattern="-s")
        self.ok("kubernetes", ["get", "pods", "--selector", "app=x", "-n", "default"])

    def test_nul_bytes_are_refused(self):
        self.refused("jenkins", ["job", "list", "a\x00b"], pattern="invalid argument")

    def test_denied_commands(self):
        self.refused("jenkins", ["login"], pattern="login")
        self.refused("jenkins", ["LOGIN"], pattern="login")
        self.refused("gsc", ["auth", "login"], pattern="auth login")
        self.ok("gsc", ["auth", "status"])
        self.refused("cubeapm", ["logs", "delete", "--query", "x"], pattern="logs delete")
        self.ok("cubeapm", ["logs", "query", "error"])
        self.refused("cubeapm", ["ingest"], pattern="ingest")

    def test_denied_commands_cannot_hide_behind_a_leading_flag(self):
        # tokens() stops at the first flag, so subcommand CLIs must start with the command.
        self.refused("jenkins", ["-o", "json", "login"], pattern="before any flags")
        self.refused("kubernetes", ["-n", "x", "get", "pods"], pattern="before any flags")
        self.ok("jenkins", ["--help"])
        # psql takes flags first and has no subcommands.
        self.ok("postgres", ["-c", "SELECT 1"])

    def test_cli_shorthands_for_target_and_auth_are_refused(self):
        for args in (["job", "list", "-s", "https://other.invalid"], ["job", "list", "-u", "other"], ["job", "list", "-t", "tok"], ["job", "list", "-k"]):
            self.refused("jenkins", args, pattern="fixed by the connection")
        self.refused("jira", ["issue", "search", "-e", "other@x.invalid"], pattern="-e")
        self.refused("elasticsearch", ["cluster", "health", "-p", "pw"], pattern="-p")
        self.refused("nginxpm", ["proxy", "list", "-ppw"], pattern="-p")
        self.ok("gsc", ["top", "queries", "-s", "sc-domain:example.com", "-o", "json"])

    def test_short_flag_clusters_are_parsed_like_the_cli(self):
        self.refused("kubernetes", ["get", "pods", "-Ashttps://other.invalid"], pattern="-s")
        self.ok("kubernetes", ["get", "pods", "-ojsonpath={.items[*].spec}"])
        self.ok("kubernetes", ["get", "pods", "-lapp=search"])
        self.refused("mysql", ["-e", "SELECT 1", "-pother"], pattern="-p")

    def test_abbreviated_long_flags_are_refused_where_the_parser_expands_them(self):
        self.refused("aws", ["sts", "get-caller-identity", "--pro", "other"], pattern="--profile")
        self.refused("aws", ["logs", "filter-log-events", "--endp", "https://other.invalid"], pattern="--endpoint-url")
        self.ok("aws", ["logs", "filter-log-events", "--end-time", "1"])
        # Cobra does not expand abbreviations, so a real flag that prefixes a denied one stays usable.
        self.ok("cubeapm", ["traces", "search", "--query", "GET /x"])

    def test_database_overrides_are_refused(self):
        self.refused("postgres", ["-d", "postgresql://other:pw@prod.invalid/db", "-c", "SELECT 1"], pattern="connection strings")
        self.refused("postgres", ["-c", "SELECT 1", "host=prod.invalid user=other"], pattern="connection strings")
        self.ok("postgres", ["-c", "SELECT * FROM users WHERE user = 'x' LIMIT 1"])
        self.ok("postgres", ["-d", "analytics", "-c", "SELECT 1"])
        for args in (["-e", "SELECT 1", "--password=other"], ["-e", "SELECT 1", "--password", "other"], ["-e", "SELECT 1", "-p"]):
            self.refused("mysql", args, pattern="fixed by the connection")

    def test_review_round_two_bypasses_are_closed(self):
        # kcat chooses producer mode on its own unless a read mode is given.
        self.refused("kafka", ["-t", "events", "messages.txt"], pattern="read mode")
        self.ok("kafka", ["-C", "-t", "events", "-o", "-10", "-e"])
        self.ok("kafka", ["-L", "-J"])
        # One-letter abbreviations where the parser expands them.
        self.refused("aws", ["sts", "get-caller-identity", "--e", "http://other.invalid"], pattern="--endpoint-url")
        self.refused("postgres", ["--u", "other", "-c", "SELECT 1"], pattern="--username")
        # MySQL option prefixes, underscores and password aliases.
        self.refused("mysql", ["-e", "SELECT 1", "--loose-host=prod.invalid"], pattern="--host")
        self.refused("mysql", ["-e", "SELECT 1", "--init_command=SET SESSION TRANSACTION READ WRITE"], pattern="--init-command")
        self.refused("mysql", ["-e", "SELECT 1", "--password1=x"], pattern="--password")
        # Credential-printing and credential-reading commands.
        self.refused("gsc", ["auth", "token"], pattern="auth token")
        self.refused("redis", ["CONFIG", "GET", "requirepass"], pattern="read commands")
        # Boolean ClickHouse -m must not hide -C.
        self.refused("clickhouse", ["-mC/etc/clickhouse.xml", "--query", "SELECT 1"], pattern="-C")
        # Elasticsearch -f owns its attached file name.
        self.ok("elasticsearch", ["search", "query", "logs", "-f./query.json"])
        # psql: -c inside a cluster owns the SQL that follows.
        self.ok("postgres", ["-Ac", "SELECT * FROM users WHERE user = 1"])
        self.ok("postgres", ["--comm", "SELECT * FROM users WHERE user = 1"])
        self.refused("postgres", ["-c", "SELECT 1", "-dhost=prod.invalid"], pattern="connection strings")

    def test_kubernetes_secrets_are_matched_by_resource_type(self):
        self.ok("kubernetes", ["get", "pods", "-n", "external-secrets"])
        self.ok("kubernetes", ["logs", "pod/external-secrets-controller"])
        self.ok("kubernetes", ["explain", "secret"])
        for args in (["get", "secrets"], ["get", "-n", "x", "secret/db"], ["describe", "pods,secrets"], ["get", "Secrets.v1"]):
            self.refused("kubernetes", args, pattern="secrets are not readable")

    def test_cubeapm_scope_follows_the_real_command(self):
        self.refused("cubeapm", ["metric", "query", "up"], pattern="select UAT", label="UAT")
        self.refused("cubeapm", ["metrics", "-o", "json", "query", "up"], pattern="full command first", label="UAT")
        self.refused("cubeapm", ["metrics", "query", 'up{env!="UAT"}'], pattern="reaches outside", label="UAT")
        self.refused("cubeapm", ["logs", "query", "env:UAT OR error"], pattern="or/unless", label="UAT")
        self.ok("cubeapm", ["logs", "query", "error or timeout", "--stream", '{env="UAT"}'], label="UAT")
        # An ordinary word that equals another environment's label is not a selector.
        self.ok("cubeapm", ["logs", "query", "env:UAT AND error"], label="UAT")

    def test_kubernetes_cannot_select_resources_from_files(self):
        for args in (["get", "-f", "-"], ["get", "--filename=manifest.yaml"], ["get", "-k", "dir"], ["get", "--kustomize", "dir"]):
            self.refused("kubernetes", args, pattern="fixed by the connection")

    def test_redis_latency_reset_is_not_a_read(self):
        self.refused("redis", ["LATENCY", "RESET"], pattern="read commands")
        self.ok("redis", ["LATENCY", "LATEST"])

    def test_secret_words_are_refused_anywhere_in_kubectl_arguments(self):
        self.refused("kubernetes", ["get", "-n", "x", "secrets"], pattern="secret")
        self.ok("kubernetes", ["get", "-n", "x", "pods"])

    def test_cubeapm_tags_are_one_pair_each(self):
        # --tags is a repeatable string array: "service=a,environment=UAT" is one tag named service,
        # so it does not count as the environment and --env is still added.
        self.assertEqual(connectors.scope(self.registry["cubeapm"], ["traces", "search", "--tags", "service=a,environment=UAT"], "UAT", ())[-2:],
                         ["--env", "UAT"])
        # Anything that reads as another environment's selector is still refused, conservatively.
        self.refused("cubeapm", ["traces", "search", "--tags", "service=a,environment=PROD"], pattern="reaches outside", label="UAT")
        self.refused("cubeapm", ["traces", "search", "--tags", "environment=PROD"], pattern="must use UAT", label="UAT")

    def test_github_allowlist(self):
        self.ok("github", ["pr", "list", "--json", "number"])
        self.ok("github", ["search", "prs", "x"])
        self.refused("github", ["pr", "merge", "12"], pattern="allows only")
        self.refused("github", ["api", "repos/x/y"], pattern="allows only")
        self.refused("github", ["pr", "list", "--web"], pattern="--web")

    def test_kubernetes_allowlist_and_secrets(self):
        self.ok("kubernetes", ["get", "pods", "-n", "default"])
        self.ok("kubernetes", ["logs", "pod/x"])
        self.refused("kubernetes", ["get", "secrets"], pattern="secrets are not readable")
        self.refused("kubernetes", ["get", "secret/db"], pattern="secrets are not readable")
        self.refused("kubernetes", ["describe", "secrets", "db"], pattern="secrets are not readable")
        self.refused("kubernetes", ["delete", "pod", "x"], pattern="allows only")
        self.refused("kubernetes", ["exec", "-it", "x", "--", "sh"], pattern="allows only")
        self.refused("kubernetes", ["get", "pods", "--kubeconfig", "/tmp/x"], pattern="--kubeconfig")

    def test_aws_allowlist_and_sensitive_reads(self):
        self.ok("aws", ["logs", "filter-log-events", "--log-group-name", "g"])
        self.ok("aws", ["ecs", "describe-services", "--cluster", "c"])
        self.ok("aws", ["s3", "ls"])
        self.refused("aws", ["secretsmanager", "get-secret-value", "--secret-id", "x"], pattern="secretsmanager")
        self.refused("aws", ["ssm", "get-parameter", "--name", "x"], pattern="ssm get-parameter")
        self.refused("aws", ["iam", "create-user", "--user-name", "x"], pattern="allows only")
        self.refused("aws", ["s3", "rm", "s3://b/k"], pattern="allows only")
        self.refused("aws", ["ecs", "describe-services", "--profile", "other"], pattern="--profile")

    def test_cubeapm_label_guard(self):
        self.refused("cubeapm", ["traces", "search", "--env", "PROD"], "UAT", pattern="must be UAT")
        self.refused("cubeapm", ["traces", "search", "--env=PROD"], "UAT", pattern="must be UAT")
        self.refused("cubeapm", ["traces", "search", "--tags", "environment=PROD"], "UAT", pattern="must use UAT")
        self.refused("cubeapm", ["traces", "search", "--tags=env=PROD"], "UAT", pattern="must use UAT")
        self.ok("cubeapm", ["traces", "search", "--env", "UAT"], "UAT")
        self.ok("cubeapm", ["traces", "search", "--env=UAT", "--tags", "environment=UAT"], "UAT")
        self.ok("cubeapm", ["traces", "search", "--tags", "team=payments"], "UAT")
        # Without a label (an unshared connection) nothing is guarded.
        self.ok("cubeapm", ["traces", "search", "--env", "PROD"])


class RunTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-staging": {"provider": "jenkins", "env": "staging"},
                              "jenkins-uat": {"provider": "jenkins", "env": "uat"}},
                 secrets={"jenkins-staging": JENKINS, "jenkins-uat": [l.replace("jenkins-token-123", "uat-token-456") for l in JENKINS]})
        self.ws = orgs.workspace(self.home, "acme")
        self.marker = self.dir / "ran"
        self.fake("import json, os, sys\n"
                  "open(" + repr(str(self.marker)) + ", 'a').write('x')\n"
                  "if 'fail' in sys.argv:\n    print('partial output'); sys.exit(7)\n"
                  "print(json.dumps({'argv': sys.argv[1:], 'url': os.environ.get('JENKINS_URL'), 'token': os.environ.get('JENKINS_TOKEN'),\n"
                  "                  'ro': os.environ.get('JENKINS_READ_ONLY'), 'org': os.environ.get('RECKON_ORG'), 'gh': os.environ.get('GH_TOKEN')}))\n",
                  "jenkins")

    def run_cli(self, *args, **extra):
        """Flags go before the first "--"; everything after it is the tool's own arguments."""
        cut = args.index("--") if "--" in args else len(args)
        return self.cli("run", *args[:cut], "--org", "acme", "--env", "staging", *args[cut:], **extra)

    def test_credentials_are_injected_and_redacted_from_output(self):
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list", GH_TOKEN="shell-gh")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["url"], "https://jenkins.fixture.invalid")
        self.assertEqual(out["token"], "[REDACTED]")
        self.assertNotIn("jenkins-token-123", result.stdout + result.stderr)
        self.assertEqual((out["ro"], out["org"], out["gh"]), ("true", "acme", None))
        self.assertEqual(out["argv"], ["job", "list"])

    def test_each_environment_gets_its_own_credentials(self):
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "uat", "--", "job", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("uat-token-456", result.stdout)
        self.assertEqual(json.loads(result.stdout)["token"], "[REDACTED]")

    def test_exit_code_passes_through(self):
        result = self.cli("run", "jenkins-staging", "--org", "acme", "--env", "staging", "--", "fail")
        self.assertEqual(result.returncode, 7)
        self.assertIn("partial output", result.stdout)

    def test_arguments_after_double_dash_reach_the_cli_unchanged(self):
        tricky = ["job", "list", "; rm -rf /", "$(touch " + str(self.dir / "pwned") + ")", "a b", "--", "-x"]
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", *tricky)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["argv"], tricky)
        self.assertFalse((self.dir / "pwned").exists())

    def test_denied_arguments_and_commands_never_start_the_cli(self):
        for args in (["job", "list", "--server", "https://other.invalid"], ["login"], ["config", "set"]):
            with self.subTest(args=args):
                result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", *args)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.marker.exists())

    def test_connection_for_another_environment_is_refused_before_running(self):
        result = self.cli("run", "jenkins-uat", "--org", "acme", "--env", "staging", "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("belongs to uat", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_missing_credentials_and_missing_cli_are_clear(self):
        self.secret("acme", "jenkins-staging", "")
        result = self.run_cli("jenkins-staging", "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no credentials", result.stderr)
        self.assertFalse(self.marker.exists())
        (self.bin / "jenkins").unlink()
        self.secret("acme", "jenkins-staging", JENKINS)
        result = self.run_cli("jenkins", "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stderr)

    def test_run_without_a_command_is_a_usage_error(self):
        result = self.run_cli("jenkins")
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage", result.stderr)

    def test_session_flag_stores_verifiable_evidence(self):
        path, session = create(self.ws, "acme", "staging", "What jobs exist?", "task")
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", session["id"], "--label", "jobs", "--", "job", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("saved as evidence", result.stderr)
        records = evidence(path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record["org"], record["environment"], record["connection"], record["status"], record["label"]),
                         ("acme", "staging", "jenkins-staging", "ok", "jobs"))
        self.assertEqual(record["operation"], "run:jenkins")
        stdout = (path / record["artifacts"]["stdout"]["path"]).read_text()
        self.assertNotIn("jenkins-token-123", stdout)
        self.assertIn("[REDACTED]", stdout)
        self.assertNotIn("jenkins-token-123", json.dumps(record))

    def test_session_from_another_environment_is_refused_before_the_cli_runs(self):
        path, session = create(self.ws, "acme", "uat", "Other env", "task")
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", session["id"], "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("session belongs", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(evidence(path), [])

    def test_session_from_another_org_is_refused_before_the_cli_runs(self):
        self.org("other", ("staging",))
        path, session = create(orgs.workspace(self.home, "other"), "other", "staging", "Other org", "task")
        # The id exists only in the other org's workspace, so acme cannot even see it.
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", session["id"], "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.marker.exists())

    def test_label_requires_a_session(self):
        result = self.run_cli("jenkins", "--label", "x", "--", "job", "list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--label needs --session", result.stderr)

    def test_failed_run_is_still_kept_as_evidence(self):
        path, session = create(self.ws, "acme", "staging", "Failures", "task")
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--session", session["id"], "--", "fail")
        self.assertEqual(result.returncode, 7)
        self.assertEqual([r["status"] for r in evidence(path)], ["failed"])

    def test_a_pinned_agent_cannot_reach_another_environment(self):
        result = self.cli("run", "jenkins", "--env", "uat", "--", "job", "list", RECKON_ORG="acme", RECKON_ENV="staging")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pinned to acme/staging", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.cli("run", "jenkins", "--", "job", "list", RECKON_ORG="acme", RECKON_ENV="staging").returncode, 0)

    def test_session_evidence_never_stores_injected_credentials(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["warehouse"] = {"provider": "clickhouse", "env": "staging"}
        data["connections"]["cache"] = {"provider": "redis", "env": "staging"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "warehouse", ["CLICKHOUSE_HOST=ch.invalid", "CLICKHOUSE_USER=reader", "CLICKHOUSE_PASSWORD=ch-secret-pw"])
        self.secret("acme", "cache", ["REDIS_URL=redis://:redis-secret-pw@cache.invalid:6379/0"])
        self.fake("print('1')", "clickhouse")
        self.fake("print('PONG')", "redis-cli")
        sid = json.loads(self.cli("task", "--question", "q", "--org", "acme", "--env", "staging", "--json").stdout)["session"]
        for target, args in (("warehouse", ["--query", "SELECT 1"]), ("cache", ["PING"])):
            result = self.run_cli(target, "--session", sid, "--", *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        stored = "".join(p.read_text() for p in (self.ws / "sessions" / sid).rglob("*") if p.is_file())
        self.assertNotIn("ch-secret-pw", stored)
        self.assertNotIn("redis-secret-pw", stored)

    def test_streamed_cubeapm_logs_are_valid_session_evidence(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["cubeapm"] = {"provider": "cubeapm", "env": "staging"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "cubeapm", CUBEAPM)
        self.fake("import json\nfor i in range(2): print(json.dumps({'_msg': i}, indent=2))", "cubeapm")
        sid = json.loads(self.cli("task", "--question", "q", "--org", "acme", "--env", "staging", "--json").stdout)["session"]
        self.assertEqual(self.run_cli("cubeapm", "--session", sid, "--", "logs", "query", "error", "-o", "json").returncode, 0)
        record = evidence(self.ws / "sessions" / sid)[0]
        self.assertEqual(record["status"], "ok")
        self.assertNotEqual(record["completeness"], "invalid_output")

    def test_shared_cubeapm_reads_stay_on_the_active_label(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["cubeapm"] = {"provider": "cubeapm", "env_labels": {"staging": "STAG", "uat": "UAT"}}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "cubeapm", CUBEAPM)
        self.fake("import sys; print(sys.argv[1:])", "cubeapm")
        # Trace searches without an environment get the active label added.
        result = self.run_cli("cubeapm", "--", "traces", "search", "--service", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'--env', 'STAG'", result.stdout)
        self.assertEqual(self.run_cli("cubeapm", "--", "traces", "search", "--env", "UAT").returncode, 1)
        # Another environment's label anywhere in a query is refused.
        other = self.run_cli("cubeapm", "--", "metrics", "query", 'up{env="UAT"}')
        self.assertEqual(other.returncode, 1)
        self.assertIn("reaches outside STAG", other.stderr)
        self.assertEqual(self.run_cli("cubeapm", "--", "logs", "query", "env:UAT AND error").returncode, 1)
        # Query commands must name the active label; discovery commands need not.
        self.assertIn("select STAG", self.run_cli("cubeapm", "--", "metrics", "query", "up").stderr)
        self.assertEqual(self.run_cli("cubeapm", "--", "metrics", "query", 'up{env="STAG"}').returncode, 0)
        self.assertEqual(self.run_cli("cubeapm", "--", "metrics", "labels", "--last", "1h").returncode, 0)

    def test_labelled_connection_is_unavailable_where_it_has_no_label(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["cubeapm"] = {"provider": "cubeapm", "env_labels": {"uat": "UAT"}}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "cubeapm", CUBEAPM)
        self.fake("print('ran')", "cubeapm")
        result = self.run_cli("cubeapm", "--", "metrics", "labels")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ran", result.stdout)
        with self.assertRaisesRegex(ValueError, "no label for staging"):
            orgs.resolve(self.ctx(), "cubeapm")


class WorkspaceTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"grafana": {"provider": "grafana"}, "github": {"provider": "github"},
                              "postgres-staging": {"provider": "postgres", "env": "staging"},
                              "cubeapm": {"provider": "cubeapm", "env_labels": {"staging": "STAG"}}})
        self.ctx_ = self.ctx()
        self.root = workspace.ensure(self.home, self.ctx_, self.registry)
        self.settings = json.loads((self.root / ".claude" / "settings.json").read_text())["permissions"]

    def test_workspace_lives_under_the_org_and_is_private(self):
        self.assertEqual(self.root, self.home / "orgs" / "acme" / "workspace")
        for directory in ("knowledge/_shared", "knowledge/staging", "knowledge/uat", "sessions", "incidents"):
            self.assertTrue((self.root / directory).is_dir(), directory)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_overview_lists_connections_and_labels(self):
        text = (self.root / "RECKON.md").read_text()
        for expected in ("`grafana`", "`github`", "`postgres-staging`", "`cubeapm`", "staging=STAG", "acme"):
            self.assertIn(expected, text)
        self.assertNotIn(chr(0x2014), text)

    def test_claude_md_is_only_created_when_missing(self):
        guide = self.root / "CLAUDE.md"
        self.assertIn("@RECKON.md", guide.read_text())
        guide.write_text("my own notes\n")
        (self.root / "AGENTS.md").write_text("my agents notes\n")
        (self.root / "RECKON.md").write_text("stale\n")
        workspace.ensure(self.home, self.ctx_, self.registry)
        self.assertEqual(guide.read_text(), "my own notes\n")
        self.assertEqual((self.root / "AGENTS.md").read_text(), "my agents notes\n")
        self.assertIn("grafana", (self.root / "RECKON.md").read_text())

    def test_settings_allow_reads_through_reckon_run_for_own_and_allowlist_tools(self):
        for rule in ("Bash(reckon run grafana:*)", "Bash(reckon run github:*)", "Bash(reckon run cubeapm:*)", "Bash(reckon preflight:*)"):
            self.assertIn(rule, self.settings["allow"])

    def test_settings_put_approval_tools_under_ask(self):
        self.assertIn("Bash(reckon run postgres-staging:*)", self.settings["ask"])
        self.assertIn("Bash(reckon run postgres:*)", self.settings["ask"])
        self.assertFalse([r for r in self.settings["allow"] if "postgres" in r])

    def test_settings_deny_login_and_secrets(self):
        self.assertIn("Bash(reckon login:*)", self.settings["deny"])
        self.assertIn("Bash(reckon connect:*)", self.settings["deny"])
        secrets = "/" + str(self.home / "orgs") + "/*/secrets/**"
        for tool in ("Read", "Edit", "Write"):
            self.assertIn(tool + "(" + secrets + ")", self.settings["deny"])
        self.assertTrue(secrets.startswith("//"))

    def test_skill_links_resolve_to_the_reckon_skill(self):
        source = (SOURCE / "skills" / "reckon").resolve()
        for runtime in (".claude", ".agents"):
            link = self.root / runtime / "skills" / "reckon"
            self.assertTrue(link.is_symlink(), runtime)
            self.assertEqual(link.resolve(), source)
            self.assertTrue((link / "SKILL.md").is_file())

    def test_reckon_command_symlink_points_at_the_script(self):
        link = self.home / "bin" / "reckon"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(SOURCE / "scripts" / "reckon.py"))
        workspace.ensure(self.home, self.ctx_, self.registry)
        self.assertEqual(os.readlink(link), str(SOURCE / "scripts" / "reckon.py"))

    def test_knowledge_templates_are_seeded_without_overwriting(self):
        shared = sorted((self.root / "knowledge" / "_shared").glob("*.md"))
        self.assertTrue(shared)
        shared[0].write_text("edited\n")
        workspace.ensure(self.home, self.ctx_, self.registry)
        self.assertEqual(shared[0].read_text(), "edited\n")

    def test_agent_env_strips_credentials_and_pins_the_context(self):
        os.environ.update(GH_TOKEN="g", GITHUB_TOKEN="g2", AWS_ACCESS_KEY_ID="a", AWS_SECRET_ACCESS_KEY="s", JENKINS_URL="j",
                          JENKINS_TOKEN="t", KUBECONFIG="/tmp/kube", XDG_CONFIG_HOME="/tmp/xdg", RECKON_ORG="old", RECKON_ENV="old",
                          RECKON_CONNECTION="old", RECKON_FOO="x", CUBEAPM_PASSWORD="p", HOME="/home/me", LANG="C.UTF-8",
                          REDISCLI_AUTH="r", GOOGLE_APPLICATION_CREDENTIALS="/tmp/adc.json")
        env = workspace.agent_env(self.home, self.ctx_, self.registry)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "JENKINS_URL", "JENKINS_TOKEN",
                    "KUBECONFIG", "XDG_CONFIG_HOME", "RECKON_CONNECTION", "RECKON_FOO", "CUBEAPM_PASSWORD", "REDISCLI_AUTH",
                    "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(key, env)
        self.assertEqual((env["RECKON_HOME"], env["RECKON_ORG"], env["RECKON_ENV"]), (str(self.home), "acme", "staging"))
        self.assertEqual(env["HOME"], "/home/me")
        self.assertEqual(env["LANG"], "C.UTF-8")
        self.assertTrue(env["PATH"].startswith(str(self.home / "bin") + os.pathsep))
        self.assertTrue(env["PATH"].endswith(os.environ["PATH"]))


class MigrationTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.legacy = self.dir / "legacy"
        legacy = self.legacy
        write(legacy / ".env.production", "\n".join([
            "# production", "JENKINS_URL=https://jenkins.prod.invalid", "JENKINS_USER=prod-user", "JENKINS_TOKEN=prod-token-value",
            "JENKINS_EXTRA=$(cat /etc/passwd)", "CUBEAPM_HOST=cube.invalid", "CUBEAPM_EMAIL=ops@cube.invalid",
            "CUBEAPM_PASSWORD='cube-pass'", "UNRELATED_SETTING=1"]) + "\n")
        write(legacy / ".env.uat", "CUBEAPM_HOST=cube.invalid\nCUBEAPM_EMAIL=ops@cube.invalid\nCUBEAPM_PASSWORD='cube-pass'\n")
        write(legacy / ".config/production/jenkins-cli/config.yaml", "server: https://jenkins.prod.invalid\n")
        write(legacy / "infra-knowledge/_shared/a.md", "shared fact\n")
        write(legacy / "infra-knowledge/production/b.md", "prod fact\n")
        write(legacy / "incidents/2026-01-01-x/RCA.md", "# RCA: old\n")
        path, self.session = create(legacy, None, "production", "Legacy question", "task")
        eid = "e-legacy000001"
        folder = path / "evidence" / eid
        folder.mkdir()
        record = {"schema_version": 1, "id": eid, "session": self.session["id"], "environment": "production", "provider": "jenkins",
                  "operation": "run:jenkins", "label": "legacy", "status": "ok", "completeness": "bounded", "observed_at": "2026-01-01T00:00:00Z",
                  "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, "temporal_scope": "as_requested_in_command",
                  "artifacts": {}}
        import hashlib
        for stream in ("stdout", "stderr"):
            body = ("legacy " + stream + "\n").encode()
            (folder / (stream + ".txt")).write_bytes(body)
            record["artifacts"][stream] = {"path": "evidence/" + eid + "/" + stream + ".txt", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        write_json(folder / "record.json", record)
        self.assertEqual(len(evidence(path)), 1)
        self.sid = self.session["id"]

    def migrate(self, **kwargs):
        kwargs.setdefault("rename", {"production": "prod"})
        kwargs.setdefault("share", ("cubeapm",))
        return migrate.migrate(self.home, "acme", self.legacy, self.registry, **kwargs)

    def test_migrated_values_read_back_exactly(self):
        for value in ('"quoted"', "'single'", " padded ", "plain", "a=b#c"):
            self.secret("acme", "jenkins-staging", ["JENKINS_TOKEN=" + migrate.literal(value)])
            self.assertEqual(orgs.secrets(self.home, "acme", "jenkins-staging", self.registry["jenkins"])["JENKINS_TOKEN"], value)

    def test_shell_quoting_decides_what_is_literal(self):
        path = self.dir / "quoting.env"
        write(path, "JENKINS_TOKEN='$literal-token'\nJENKINS_URL=https://$HOST/x\nJENKINS_USER=\"a${B}\"\n")
        values, skipped = migrate.parse(path)
        self.assertEqual(values, {"JENKINS_TOKEN": "$literal-token"})
        self.assertEqual(len(skipped), 2)

    def test_an_existing_destination_folder_is_refused_and_kept(self):
        keep = self.home / "orgs" / "acme" / "notes.txt"
        write(keep, "mine")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.migrate()
        self.assertEqual(keep.read_text(), "mine")

    def test_failed_creation_leaves_no_partial_org(self):
        from unittest import mock
        with mock.patch.object(orgs, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.migrate(rename={"production": "prod"})
        self.assertFalse((self.home / "orgs" / "acme").exists())

    def test_dry_run_reports_the_plan_and_writes_nothing(self):
        before = tree(self.legacy)
        summary = self.migrate(dry_run=True)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["environments"], ["prod", "uat"])
        self.assertEqual(set(summary["connections"]), {"cubeapm", "jenkins-prod"})
        self.assertEqual(summary["sessions"], 1)
        self.assertEqual(summary["incidents"], 1)
        self.assertTrue(summary["knowledge"])
        self.assertFalse(self.home.exists() and any(self.home.iterdir()))
        self.assertEqual(tree(self.legacy), before)

    def test_skipped_values_are_listed_by_name_without_their_content(self):
        summary = self.migrate(dry_run=True)
        skipped = "\n".join(summary["skipped"])
        self.assertIn("JENKINS_EXTRA", skipped)
        self.assertIn("UNRELATED_SETTING (no matching tool)", skipped)
        self.assertNotIn("/etc/passwd", skipped)

    def test_migrate_builds_the_org_connections_and_secrets(self):
        before = tree(self.legacy)
        self.migrate()
        data = orgs.load(self.home, "acme", self.registry)
        self.assertEqual(data["environments"], ["prod", "uat"])
        self.assertEqual(data["production"], ["prod"])
        self.assertEqual(data["connections"], {"cubeapm": {"provider": "cubeapm"}, "jenkins-prod": {"provider": "jenkins", "env": "prod"}})
        cube = orgs.secrets(self.home, "acme", "cubeapm", self.registry["cubeapm"])
        self.assertEqual(cube, {"CUBEAPM_SERVER": "cube.invalid", "CUBEAPM_EMAIL": "ops@cube.invalid", "CUBEAPM_PASSWORD": "cube-pass"})
        jenkins = orgs.secrets(self.home, "acme", "jenkins-prod", self.registry["jenkins"])
        self.assertEqual(jenkins, {"JENKINS_URL": "https://jenkins.prod.invalid", "JENKINS_USER": "prod-user", "JENKINS_TOKEN": "prod-token-value"})
        for name in ("cubeapm", "jenkins-prod"):
            path = orgs.secrets_path(self.home, "acme", name)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("JENKINS_EXTRA", path.read_text())
            self.assertNotIn("UNRELATED", path.read_text())
        self.assertEqual(tree(self.legacy), before)

    def test_config_knowledge_incidents_and_sessions_are_copied(self):
        self.migrate()
        org_dir = self.home / "orgs" / "acme"
        self.assertEqual((org_dir / "config/jenkins-prod/jenkins-cli/config.yaml").read_text(), "server: https://jenkins.prod.invalid\n")
        self.assertTrue(orgs.configured(self.home, "acme", "jenkins-prod", self.registry["jenkins"]))
        root = orgs.workspace(self.home, "acme")
        self.assertEqual((root / "knowledge/_shared/a.md").read_text(), "shared fact\n")
        self.assertEqual((root / "knowledge/prod/b.md").read_text(), "prod fact\n")
        self.assertFalse((root / "knowledge/production").exists())
        self.assertEqual((root / "incidents/2026-01-01-x/RCA.md").read_text(), "# RCA: old\n")
        path = root / "sessions" / self.sid
        session = json.loads((path / "session.json").read_text())
        self.assertEqual((session["org"], session["environment"]), ("acme", "prod"))
        records = evidence(path)
        self.assertEqual([(r["org"], r["environment"]) for r in records], [("acme", "prod")])

    def test_migrated_session_works_with_the_new_commands(self):
        self.migrate()
        result = self.cli("resume", self.sid, "--org", "acme", "--env", "prod", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["evidence"]), 1)
        wrong = self.cli("resume", self.sid, "--org", "acme", "--env", "uat", "--json")
        self.assertEqual(wrong.returncode, 0)
        self.assertEqual(self.cli("note", self.sid, "--org", "acme", "--env", "uat", "--kind", "question", "--text", "q").returncode, 0)

    def test_every_source_file_is_left_unchanged(self):
        before = tree(self.legacy)
        modes = {p: p.stat().st_mode for p in self.legacy.rglob("*")}
        self.migrate()
        self.migrate_again_is_rejected()
        self.assertEqual(tree(self.legacy), before)
        self.assertEqual({p: p.stat().st_mode for p in self.legacy.rglob("*")}, modes)

    def migrate_again_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.migrate()

    def test_shared_provider_with_different_values_is_an_error(self):
        write(self.legacy / ".env.uat", "CUBEAPM_HOST=another.invalid\nCUBEAPM_EMAIL=ops@cube.invalid\nCUBEAPM_PASSWORD='cube-pass'\n")
        with self.assertRaisesRegex(ValueError, "different settings"):
            self.migrate()
        self.assertFalse((self.home / "orgs" / "acme").exists())

    def test_differing_values_without_share_become_one_connection_per_environment(self):
        write(self.legacy / ".env.uat", "CUBEAPM_HOST=another.invalid\nCUBEAPM_EMAIL=ops@cube.invalid\nCUBEAPM_PASSWORD='cube-pass'\n")
        self.migrate(share=())
        data = orgs.load(self.home, "acme", self.registry)
        self.assertEqual(set(data["connections"]), {"cubeapm-prod", "cubeapm-uat", "jenkins-prod"})
        self.assertEqual(data["connections"]["cubeapm-uat"], {"provider": "cubeapm", "env": "uat"})
        self.assertEqual(orgs.secrets(self.home, "acme", "cubeapm-uat", self.registry["cubeapm"])["CUBEAPM_SERVER"], "another.invalid")

    def test_identical_values_in_every_environment_are_shared_automatically(self):
        self.migrate(share=())
        self.assertEqual(orgs.load(self.home, "acme", self.registry)["connections"]["cubeapm"], {"provider": "cubeapm"})

    def test_without_rename_environments_keep_their_old_names(self):
        self.migrate(rename={})
        data = orgs.load(self.home, "acme", self.registry)
        self.assertEqual(data["environments"], ["production", "uat"])
        self.assertIn("jenkins-production", data["connections"])
        self.assertEqual(data["production"], ["production"])

    def test_bad_rename_and_unknown_share_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "no staging environment"):
            self.migrate(rename={"staging": "stg"})
        with self.assertRaisesRegex(ValueError, "merge"):
            self.migrate(rename={"production": "uat"})
        with self.assertRaisesRegex(ValueError, "unknown provider"):
            self.migrate(share=("nope",))
        with self.assertRaisesRegex(ValueError, "no pre-org layout"):
            migrate.migrate(self.home, "acme", self.dir, self.registry)

    def test_cli_migrate_dry_run(self):
        result = self.cli("migrate", "--org", "acme", "--from", str(self.legacy), "--rename", "production=prod", "--share", "cubeapm", "--dry-run", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["environments"], ["prod", "uat"])
        self.assertFalse((self.home / "orgs").exists())


if __name__ == "__main__":
    unittest.main()
