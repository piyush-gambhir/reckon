"""Native 1Password flows with synthetic vaults, OS credentials and a fake op."""
import copy
import json
import os
import select
import subprocess
import time
import unittest

from helpers import OrgCase, SOURCE
from reckonlib import orgs

VAULT = "v" * 26

ITEM = "i" * 26

OTHER = "x" * 26

TOKEN = "ops_reader_fixture"

WRITER = "ops_writer_fixture"

INITIAL = {"id": ITEM, "vault": {"id": VAULT}, "title": "External", "category": "API_CREDENTIAL",
           "state": "ACTIVE", "version": 1, "fields": [
               {"id": "username", "label": "JENKINS_USER", "type": "STRING", "value": "private-user"},
               {"id": "credential", "label": "JENKINS_TOKEN", "type": "CONCEALED", "value": "private-token"},
               {"id": "untouched", "label": "Unrelated", "type": "CONCEALED", "value": "preserve-this"}]}

FAKE_OP = r'''import json, os, sys
from pathlib import Path
root = Path(__file__).parent
state_path = root / "op-state.json"
state = json.loads(state_path.read_text())
args = sys.argv[1:]
if args == ["--version"]:
    print(state.get("version", "2.41.0")); sys.exit(0)
if args[:1] == ["--config"]:
    args = args[2:]
credential = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "")
role = "writer" if credential == "ops_writer_fixture" else "reader" if credential == "ops_reader_fixture" else "invalid"
with (root / "op-calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"args": args, "role": role, "unsafe_auth": [k for k in os.environ if
        k.startswith("OP_SESSION_") or k in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT")],
        "desktop": os.environ.get("OP_BIOMETRIC_UNLOCK_ENABLED")}) + "\n")
if args[:2] == state.get("fail_on"):
    print(state.get("error", "credential-bearing-failure"), file=sys.stderr); sys.exit(1)
if role == "invalid":
    print("credential-bearing-failure", file=sys.stderr); sys.exit(1)
if args[:2] == ["vault", "list"]:
    print(json.dumps(state.get(role + "_vaults", [{"id": "v" * 26, "name": "Reckon"}]))); sys.exit(0)
if args[:1] == ["read"]:
    ref = args[-1]
    if not ref.startswith("op://" + "v" * 26 + "/"): sys.exit(1)
    path, _, query = ref.partition("?")
    parts = path[5:].split("/")
    value = next(f["value"] for f in state["items"][parts[1]]["fields"] if f["id"] == parts[-1])
    print(value, end=""); sys.exit(0)
if "--vault" not in args or args[args.index("--vault") + 1] != "v" * 26:
    sys.exit(1)
if args[:2] == ["item", "list"]:
    tag = args[args.index("--tags") + 1]
    print(json.dumps([i for i in state["items"].values() if tag in i.get("tags", [])])); sys.exit(0)
if args[:2] == ["item", "get"]:
    identifier = args[-1]
    found = [i for i in state["items"].values() if i["id"] == identifier or i["title"] == identifier]
    if len(found) != 1: sys.exit(1)
    if state.get("invalid_json"): print("credential-bearing-invalid-json"); sys.exit(0)
    if state.get("corrupt_reads") and found[0]["id"] != "i" * 26:
        for field in found[0]["fields"]: field["value"] = "corrupted"
    print(json.dumps(found[0])); sys.exit(0)
if role != "writer": sys.exit(1)
data = json.load(sys.stdin)
if args[:2] == ["item", "create"]:
    identifier = ("a" + str(len(state["items"])).zfill(25))
    data.update(id=identifier, vault={"id": "v" * 26}, state="ACTIVE", version=1)
elif args[:2] == ["item", "edit"]:
    identifier = args[-1]
    if identifier not in state["items"]: sys.exit(1)
    data["version"] += 1
else: sys.exit(2)
state["items"][identifier] = data
state_path.write_text(json.dumps(state))
if state.get("fail_after_write"):
    print("credential-bearing-failure", file=sys.stderr); sys.exit(1)
print(json.dumps(data))
'''

FAKE_KEYCHAIN = r'''import json, sys
from pathlib import Path
values = json.loads((Path(__file__).parent / "keychain.json").read_text())
args = sys.argv[1:]
with (Path(__file__).parent / "keychain-calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
key = args[-2] if args[0] == "find-generic-password" else args[-1]
if key not in values: sys.exit(44)
print(values[key], end="")
'''


@unittest.skipIf(os.name == "nt", "Interactive credential tests require a Unix PTY")
class OnePasswordTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"jenkins-dev": {"provider": "jenkins", "env": "staging"},
                              "jenkins-prod": {"provider": "jenkins", "env": "uat"},
                              "shared": {"provider": "jenkins"}}, production=["uat"],
                 secrets={"jenkins-dev": ["# Keep this comment", "JENKINS_URL=https://ci.fixture.invalid",
                                           "JENKINS_USER=reader", "JENKINS_TOKEN=plaintext-token"]})
        self.state_path = self.bin / "op-state.json"
        self.keychain_path = self.bin / "keychain.json"
        self.save_state({"items": {ITEM: copy.deepcopy(INITIAL)}})
        self.keychain_path.write_text(json.dumps({"reckon-reader": TOKEN, "reckon-writer": WRITER}))
        self.binary = self.fake(FAKE_OP, "op")
        for binary in ("security", "secret-tool"):
            self.fake(FAKE_KEYCHAIN, binary)
        self.fake("import os\nprint(os.environ['JENKINS_USER'] + ' ' + os.environ['JENKINS_TOKEN'])\n", "jenkins")
        self.configure()
        self.clear_calls()

    def save_state(self, data):
        self.state_path.write_text(json.dumps(data))

    def state(self):
        return json.loads(self.state_path.read_text())

    def calls(self):
        path = self.bin / "op-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def clear_calls(self):
        for name in ("op-calls.jsonl", "keychain-calls.jsonl"):
            (self.bin / name).write_text("")

    def refs(self, user=None, credential=None):
        self.secret("acme", "jenkins-dev", ["JENKINS_URL=https://ci.fixture.invalid",
                    "JENKINS_USER=" + (user or "op://" + VAULT + "/" + ITEM + "/username"),
                    "JENKINS_TOKEN=" + (credential or "op://" + VAULT + "/" + ITEM + "/credential")])

    def tty_cli(self, *args, replies=(), **extra):
        """Run the native parser and real hidden prompts with dummy terminal input."""
        import errno
        import pty
        import signal
        binary = os.environ.get("RECKON_TEST_BIN", str(SOURCE / "target/debug/reckon"))
        pid, master = pty.fork()
        if pid == 0:
            os.execve(binary, [binary, *args], self.cli_env(**extra))
        output = bytearray()
        pending = list(replies)
        deadline = time.monotonic() + 15
        waited = False
        try:
            while True:
                self.assertLess(time.monotonic(), deadline, "interactive fixture exceeded its deadline: " + repr(bytes(output)))
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(master, 8192)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                if pending and pending[0][0].encode() in output:
                    _, answer = pending.pop(0)
                    answer = answer() if callable(answer) else answer
                    os.write(master, (answer + "\n").encode())
            _, status = os.waitpid(pid, 0)
            waited = True
            return subprocess.CompletedProcess(args, os.waitstatus_to_exitcode(status), output.decode(), "")
        finally:
            if not waited:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(master)

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout[result.stdout.index("{"):])

    def configure(self, writer=True, vault=VAULT, reader="keychain:reckon-reader"):
        args = ["secrets", "setup", "--vault", vault, "--reader", reader, "--op", str(self.binary), "--json"]
        if writer:
            args += ["--writer", "keychain:reckon-writer"]
        return self.success(self.tty_cli(*args))

    def manage(self, action, *extra, target="jenkins-dev", env="staging", replies=()):
        return self.tty_cli("secrets", action, target, *extra, "--org", "acme", "--env", env, "--json", replies=replies)

    def run_connection(self, **extra):
        return self.cli("run", "jenkins-dev", "--org", "acme", "--env", "staging", "--", "job", "list", **extra)

    def journal(self):
        return self.home / "onepassword/connections/acme/jenkins-dev/item.json"

    def test_setup_checks_both_vaults_and_persists_only_references_and_fingerprints(self):
        path = self.home / "onepassword/store.json"
        stored = path.read_text()
        self.assertNotIn(TOKEN, stored)
        self.assertNotIn(WRITER, stored)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        state = self.state()
        state["writer_vaults"] = [{"id": VAULT, "name": "Reckon"}, {"id": OTHER, "name": "Private"}]
        self.save_state(state)
        result = self.tty_cli("secrets", "setup", "--vault", VAULT, "--reader", "keychain:reckon-reader",
                              "--writer", "keychain:reckon-writer", "--op", str(self.binary))
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly", result.stdout)
        self.assertEqual(path.read_text(), stored)
        self.assertTrue(all(c["args"][:2] == ["vault", "list"] for c in self.calls()))

    def test_setup_refuses_wrong_vault_same_tokens_and_unsupported_cli(self):
        for changes, error in (({"vault": OTHER}, "different vault"), ({"version": "2.40.0"}, "2.41.0"),
                               ({"version": "2.41.0-beta.1"}, "stable"), ({"same_tokens": True}, "different")):
            with self.subTest(changes=changes):
                state = self.state()
                state["version"] = changes.get("version", "2.41.0")
                self.save_state(state)
                self.keychain_path.write_text(json.dumps({"reckon-reader": TOKEN, "reckon-writer": TOKEN if changes.get("same_tokens") else WRITER}))
                result = self.tty_cli("secrets", "setup", "--vault", changes.get("vault", VAULT), "--reader", "keychain:reckon-reader",
                                      "--writer", "keychain:reckon-writer", "--op", str(self.binary))
                self.assertEqual(result.returncode, 1)
                self.assertIn(error, result.stdout)

    def test_runtime_batches_fields_and_redacts_reference_values_in_output_cache_and_evidence(self):
        self.refs()
        task = self.success(self.cli("task", "--question", "Synthetic investigation", "--org", "acme", "--env", "staging", "--json"))
        result = self.cli("run", "jenkins-dev", "--org", "acme", "--env", "staging", "--session", task["session"], "--", "job", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[REDACTED] [REDACTED]")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0]["role"], "reader")
        for path in (self.home / "orgs/acme").rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl", ".txt"):
                contents = path.read_text()
                for value in ("private-user", "private-token", TOKEN, WRITER):
                    self.assertNotIn(value, contents, str(path))

    def test_denied_commands_and_outside_vault_references_do_not_contact_op(self):
        self.refs()
        result = self.cli("run", "jenkins", "--org", "acme", "--env", "staging", "--", "job", "list", "--token", "x")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.refs(credential="op://" + OTHER + "/" + ITEM + "/credential")
        result = self.run_connection()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.bin / "keychain-calls.jsonl").read_text(), "")

    def test_ambient_sessions_connect_and_replacement_tokens_cannot_be_used(self):
        self.refs()
        result = self.run_connection(OP_ACCOUNT="personal", OP_SESSION_personal="session", OP_CONNECT_HOST="https://other.invalid",
                                     OP_CONNECT_TOKEN="connect", OP_SERVICE_ACCOUNT_TOKEN=WRITER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0]["unsafe_auth"], [])
        self.assertEqual(self.calls()[0]["desktop"], "false")
        self.clear_calls()
        self.keychain_path.write_text(json.dumps({"reckon-reader": "ops_broader_replacement"}))
        result = self.run_connection()
        self.assertEqual(result.returncode, 1)
        self.assertIn("token changed", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_save_import_check_and_run_reuse_one_item_and_do_not_echo_hidden_values(self):
        result = self.manage("set", "JENKINS_TOKEN", replies=[("Type yes", "yes"), ("(hidden):", "tty-secret-token")])
        self.assertEqual(self.success(result)["saved"], ["JENKINS_TOKEN"])
        self.assertNotIn("tty-secret-token", result.stdout)
        created = next(v for k, v in self.state()["items"].items() if k != ITEM)
        self.assertEqual(created["title"], "acme / jenkins-dev")
        self.assertEqual(created["tags"], ["reckon/acme/staging"])
        imported = self.success(self.manage("import", "--key", "JENKINS_USER", replies=[("Type yes", "yes")]))
        self.assertEqual(imported["imported"], ["JENKINS_USER"])
        self.assertEqual(len(self.state()["items"]), 2)
        self.assertTrue((self.home / "orgs/acme/secrets/jenkins-dev.env").read_text().startswith("# Keep this comment\n"))
        checked = self.success(self.cli("secrets", "check", "jenkins-dev", "--org", "acme", "--env", "staging", "--json"))
        self.assertEqual(checked["status"], "ok")
        result = self.run_connection()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tty-secret-token", result.stdout + result.stderr)
        self.assertTrue(all("tty-secret-token" not in json.dumps(c) for c in self.calls()))

    def test_import_preview_contains_only_field_names_and_default_selection_keeps_keychain_refs(self):
        self.secret("acme", "jenkins-dev", ["JENKINS_URL=https://ci.fixture.invalid", "JENKINS_USER=plain-user",
                                            "JENKINS_TOKEN=plain-token", "JENKINS_PASSWORD=keychain:existing"])
        preview = self.manage("import", "--dry-run")
        self.assertEqual(self.success(preview)["fields"], ["JENKINS_TOKEN"])
        self.assertNotIn("plain-token", preview.stdout)
        self.assertEqual(self.calls(), [])
        imported = self.success(self.manage("import", replies=[("Type yes", "yes")]))
        self.assertEqual(imported["imported"], ["JENKINS_TOKEN"])
        current = (self.home / "orgs/acme/secrets/jenkins-dev.env").read_text()
        self.assertIn("JENKINS_USER=plain-user", current)
        self.assertIn("JENKINS_PASSWORD=keychain:existing", current)
        self.assertNotIn("plain-token", current)

    def test_link_and_explicit_update_preserve_unrelated_fields(self):
        self.success(self.manage("link", "JENKINS_TOKEN", "--item", ITEM, "--field", "JENKINS_TOKEN", replies=[("Type yes", "yes")]))
        denied = self.manage("set", "JENKINS_TOKEN", replies=[("Type yes", "yes"), ("(hidden):", "new-dummy-value")])
        self.assertEqual(denied.returncode, 1)
        self.assertIn("--update-linked", denied.stdout)
        saved = self.manage("set", "JENKINS_TOKEN", "--update-linked", replies=[("Type yes", "yes"), ("(hidden):", "new-dummy-value")])
        self.success(saved)
        fields = self.state()["items"][ITEM]["fields"]
        self.assertEqual(next(f["value"] for f in fields if f["id"] == "credential"), "new-dummy-value")
        self.assertEqual(next(f["value"] for f in fields if f["id"] == "untouched"), "preserve-this")
        self.assertEqual(self.state()["items"][ITEM]["title"], "External")

    def test_unrelated_field_in_managed_item_survives_later_saves(self):
        self.success(self.manage("import", replies=[("Type yes", "yes")]))
        state = self.state()
        created = next(v for k,v in state["items"].items() if k != ITEM)
        created["fields"].append({"id":"other", "label":"Other", "type":"CONCEALED", "value":"keep-me"})
        self.save_state(state)
        self.success(self.manage("set", "JENKINS_TOKEN", replies=[("Type yes", "yes"), ("(hidden):", "rotated-value")]))
        self.assertEqual(len(self.state()["items"]), 2)
        fields = self.state()["items"][created["id"]]["fields"]
        self.assertEqual(next(f["value"] for f in fields if f["id"] == "other"), "keep-me")

    def test_failed_or_uncertain_remote_create_retains_plaintext_and_prevents_duplicate_retry(self):
        before = (self.home / "orgs/acme/secrets/jenkins-dev.env").read_bytes()
        state = self.state()
        state["fail_after_write"] = True
        self.save_state(state)
        failed = self.manage("import", replies=[("Type yes", "yes")])
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("credential-bearing-failure", failed.stdout)
        self.assertEqual(len(self.state()["items"]), 2)
        self.assertEqual((self.home / "orgs/acme/secrets/jenkins-dev.env").read_bytes(), before)
        retry = self.manage("import", replies=[("Type yes", "yes")])
        self.assertEqual(retry.returncode, 1)
        self.assertIn("recover", retry.stdout)
        self.assertEqual(sum(c["args"][:2] == ["item", "create"] for c in self.calls()), 1)
        recovered = self.success(self.manage("recover", replies=[("Type yes", "yes")]))
        self.assertEqual(recovered["status"], "recovered")
        state = self.state()
        state["fail_after_write"] = False
        self.save_state(state)
        self.success(self.manage("import", replies=[("Type yes", "yes")]))
        self.assertEqual(len(self.state()["items"]), 2)
        self.assertFalse(json.loads(self.journal().read_text()).get("pending_create"))

    def test_no_matching_recovery_item_requires_explicit_retry(self):
        orgs.write_json(self.journal(), {"schema_version": 1, "pending_create": True,
                                       "title": "acme / jenkins-dev", "tag": "reckon/acme/staging"})
        cancelled = self.manage("recover", replies=[("Type retry", "yes")])
        self.assertEqual(cancelled.returncode, 1)
        self.assertTrue(json.loads(self.journal().read_text())["pending_create"])
        recovered = self.success(self.manage("recover", replies=[("Type retry", "retry")]))
        self.assertEqual(recovered["status"], "retry_enabled")
        self.success(self.manage("import", replies=[("Type yes", "yes")]))
        self.assertEqual(len(self.state()["items"]), 2)

    def test_recovery_with_multiple_matching_items_requires_explicit_link(self):
        orgs.write_json(self.journal(), {"schema_version": 1, "pending_create": True,
                                       "title": "acme / jenkins-dev", "tag": "reckon/acme/staging"})
        state = self.state()
        for identifier in (ITEM, OTHER):
            state["items"][identifier] = dict(copy.deepcopy(INITIAL), id=identifier,
                                              title="acme / jenkins-dev", tags=["reckon/acme/staging"])
        self.save_state(state)
        failed = self.manage("recover")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("several items", failed.stdout)
        self.success(self.manage("link", "JENKINS_TOKEN", "--item", ITEM, "--field", "credential", replies=[("Type yes", "yes")]))
        self.assertEqual(json.loads(self.journal().read_text())["item"], ITEM)

    def test_remote_verification_failure_does_not_replace_plaintext(self):
        path = self.home / "orgs/acme/secrets/jenkins-dev.env"
        before = path.read_bytes()
        state = self.state()
        state["corrupt_reads"] = True
        self.save_state(state)
        failed = self.manage("import", replies=[("Type yes", "yes")])
        self.assertEqual(failed.returncode, 1)
        self.assertIn("verification failed", failed.stdout)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_concurrent_settings_edits_are_retained_after_remote_save(self):
        path = self.home / "orgs/acme/secrets/jenkins-dev.env"
        def edit_settings():
            path.write_text("JENKINS_TOKEN=concurrent-user-edit\n")
            return "yes"
        result = self.manage("import", replies=[("Type yes", edit_settings)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("changed during", result.stdout)
        self.assertEqual(path.read_text(), "JENKINS_TOKEN=concurrent-user-edit\n")
        self.assertEqual(len(self.state()["items"]), 2)

    def test_reader_only_installation_can_read_and_link_but_refuses_secret_writes(self):
        self.configure(writer=False)
        self.refs()
        self.assertEqual(self.success(self.cli("secrets", "check", "jenkins-dev", "--org", "acme", "--env", "staging", "--json"))["status"], "ok")
        result = self.manage("set", "JENKINS_TOKEN")
        self.assertEqual(result.returncode, 1)
        self.assertIn("separate 1Password writer", result.stdout)
        self.assertNotIn("(hidden):", result.stdout)
        self.success(self.manage("link", "JENKINS_TOKEN", "--item", ITEM, "--field", "credential", replies=[("Type yes", "yes")]))
        self.assertTrue(all(c["role"] == "reader" for c in self.calls()))

    def test_names_sections_and_derived_references_are_scoped_and_deduplicated(self):
        self.refs(user="op://Reckon/External/JENKINS_USER", credential="op://Reckon/External/JENKINS_TOKEN")
        self.assertEqual(self.run_connection().returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.clear_calls()
        ref = "op://Reckon/" + ITEM + "/credential?attribute=value"
        self.refs(user=ref, credential=ref)
        self.assertEqual(self.run_connection().returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(self.calls()[0]["args"][-1].startswith("op://" + VAULT + "/"))
        state = self.state()
        state["items"][ITEM]["sections"] = [{"id": "auth", "label": "Authentication"}]
        state["items"][ITEM]["fields"][1]["section"] = {"id": "auth"}
        self.save_state(state)
        self.refs(credential="op://Reckon/External/Authentication/JENKINS_TOKEN")
        self.assertEqual(self.run_connection().returncode, 0)
        linked = self.success(self.manage("link", "JENKINS_TOKEN", "--item", ITEM, "--field", "Authentication/JENKINS_TOKEN",
                                          replies=[("Type yes", "yes")]))
        self.assertEqual(linked["linked"], ["JENKINS_TOKEN"])
        self.assertIn("op://" + VAULT + "/" + ITEM + "/auth/credential", (self.home / "orgs/acme/secrets/jenkins-dev.env").read_text())

    def test_non_api_items_cannot_be_overwritten_through_json_templates(self):
        state = self.state()
        state["items"][ITEM]["category"] = "LOGIN"
        self.save_state(state)
        self.refs()
        result = self.manage("set", "JENKINS_TOKEN", "--update-linked", replies=[("Type yes", "yes"), ("(hidden):", "dummy-new-value")])
        self.assertEqual(result.returncode, 1)
        self.assertIn("only API Credential", result.stdout)
        self.assertFalse(any(c["args"][:2] == ["item", "edit"] for c in self.calls()))

    def test_redaction_covers_escaped_reference_values_and_short_values(self):
        for value in ("private\nname-λ", "a!", 'private"name\\value'):
            with self.subTest(value=value):
                state = self.state()
                state["items"][ITEM]["fields"][0]["value"] = value
                self.save_state(state)
                self.refs()
                self.fake("import json,os\nprint(json.dumps({'user':os.environ['JENKINS_USER']}))\n", "jenkins")
                result = self.run_connection()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["user"], "[REDACTED]")

    def test_malformed_fields_and_item_responses_fail_without_disclosing_values(self):
        self.refs()
        for changes in ({"state": "ARCHIVED"}, {"vault": {"id": OTHER}}, {"id": OTHER}, {"fields": []}):
            with self.subTest(changes=changes):
                state = self.state()
                state["items"][ITEM] = dict(copy.deepcopy(INITIAL), **changes)
                self.save_state(state)
                result = self.run_connection()
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("private-token", result.stdout + result.stderr)

    def test_op_and_resolver_errors_are_classified_and_redacted(self):
        self.refs()
        state = self.state()
        for changes, expected in (({"fail_on": ["item", "get"], "error": "429 private-token"}, "rate limit"),
                                   ({"invalid_json": True}, "invalid data")):
            with self.subTest(changes=changes):
                changed = dict(state, **changes)
                self.save_state(changed)
                result = self.run_connection()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("private-token", result.stdout + result.stderr)
                self.assertNotIn("credential-bearing", result.stdout + result.stderr)
        self.save_state(state)
        self.fake("import json,os,sys\nif sys.argv[1:3] == ['commands','resolve']:\n"
                  "    print(json.dumps({'error':os.environ['JENKINS_USER']}));sys.exit(1)\n", "jenkins")
        result = self.run_connection()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-user", result.stdout + result.stderr + (self.home / "orgs/acme/activity.jsonl").read_text())

    def test_status_is_local_and_management_refuses_pinned_or_nonterminal_agents(self):
        self.refs()
        self.assertTrue(self.success(self.cli("secrets", "status", "--org", "acme", "--json"))["configured"])
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.bin / "keychain-calls.jsonl").read_text(), "")
        for action, extra in (("set", ["JENKINS_TOKEN"]), ("import", []), ("recover", []),
                              ("link", ["JENKINS_TOKEN", "--item", ITEM, "--field", "credential"])):
            with self.subTest(action=action):
                result = self.cli("secrets", action, "jenkins-dev", *extra, RECKON_ORG="acme", RECKON_ENV="staging")
                self.assertEqual(result.returncode, 1)
                self.assertIn("pinned agent", result.stderr)
        result = self.cli("secrets", "set", "jenkins-dev", "JENKINS_TOKEN", "--org", "acme", "--env", "staging", stdin="yes\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("own terminal", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_production_and_shared_changes_require_the_org_name(self):
        for target, env in (("jenkins-prod", "uat"), ("shared", "staging")):
            with self.subTest(target=target):
                result = self.manage("set", "JENKINS_TOKEN", target=target, env=env, replies=[("Type acme", "yes")])
                self.assertEqual(result.returncode, 1)
                self.assertIn("cancelled", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_tools_and_launched_agents_receive_no_1password_authentication(self):
        self.refs()
        self.fake("import os,json\nprint(json.dumps([k for k in os.environ if k.startswith('OP_')]))", "jenkins")
        result = self.run_connection(OP_SERVICE_ACCOUNT_TOKEN=WRITER, OP_SESSION_personal="session")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        self.fake("import os,json\nprint(json.dumps([k for k in os.environ if k.startswith('OP_')]))", "codex")
        result = self.cli("open", "acme/staging", "--runtime", "codex", OP_SERVICE_ACCOUNT_TOKEN=WRITER, OP_CONNECT_TOKEN="connect")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), [])
        permissions = json.loads((self.home / "orgs/acme/workspace/.claude/settings.json").read_text())["permissions"]
        self.assertIn("Bash(reckon secrets set:*)", permissions["deny"])
        self.assertIn("Bash(reckon secrets check:*)", permissions["allow"])
        self.assertTrue(any("onepassword/**" in p for p in permissions["deny"]))

    def test_verification_and_approved_provider_writes_use_only_the_reader(self):
        self.refs()
        self.assertEqual(self.cli("verify", "jenkins-dev", "--org", "acme", "--env", "staging", "--json").returncode, 0)
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["gh"] = {"provider": "github", "env": "staging", "account": "acme-bot"}
        orgs.save(self.home, data, self.registry)
        self.secret("acme", "gh", ["GH_TOKEN=op://" + VAULT + "/" + ITEM + "/credential"])
        self.fake("import json,os,sys\nif sys.argv[1:3] == ['api','user']:print(json.dumps({'login':'acme-bot'}))\n"
                  "else:print('https://github.fixture.invalid/acme/api/issues/7 ' + os.environ['GH_TOKEN'])\n", "gh")
        prepared = self.success(self.cli("act", "prepare", "github.issue.create", "--org", "acme", "--env", "staging", "--json",
                                         "--params", json.dumps({"repo": "acme/api", "title": "Synthetic issue", "body": "Fixture only"})))
        self.clear_calls()
        result = self.tty_cli("act", "apply", prepared["action"], "--org", "acme", "--env", "staging", "--json", replies=[("Type yes", "yes")])
        self.assertEqual(self.success(result)["status"], "applied")
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(all(c["role"] == "reader" for c in self.calls()))
        self.assertNotIn("private-token", result.stdout)
        receipt = self.home / "orgs/acme/workspace/actions" / prepared["action"] / "action.json"
        self.assertNotIn("private-token", receipt.read_text())

    def test_connection_removal_keeps_remote_items_and_benchmark_reports_no_values(self):
        self.refs()
        result = self.success(self.cli("secrets", "benchmark", "jenkins-dev", "--org", "acme", "--env", "staging", "--samples", "1", "--json"))
        self.assertTrue(result["includes_os_credential_lookup"])
        self.assertEqual(result["subsequent"]["per_field"]["op_invocations_per_sample"], 2)
        self.assertEqual(result["subsequent"]["per_item"]["op_invocations_per_sample"], 1)
        self.assertNotIn("private-token", json.dumps(result))
        before = self.state()
        self.clear_calls()
        self.success(self.cli("connect", "jenkins-dev", "--org", "acme", "--env", "staging", "--remove", "--json"))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.calls(), [])

    def test_watches_redact_reference_values_before_saving_inbox_items(self):
        self.refs()
        feed = self.bin / "feed.json"
        feed.write_text(json.dumps([{"id":"one", "title":"private-user"}]))
        self.fake("from pathlib import Path\nprint(Path(" + repr(str(feed)) + ").read_text())", "jenkins")
        self.success(self.cli("watch", "add", "builds", "--org", "acme", "--env", "staging", "--connection", "jenkins-dev",
                              "--title", "title", "--", "job", "list", "-o", "json"))
        first = self.cli("watch", "run", "builds", "--org", "acme", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        feed.write_text(json.dumps([{"id":"one", "title":"private-user"}, {"id":"two", "title":"private-user"}]))
        second = self.cli("watch", "run", "builds", "--org", "acme", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        inbox = self.cli("inbox", "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(inbox.returncode, 0, inbox.stderr)
        self.assertIn("[REDACTED]", inbox.stdout)
        for path in (self.home / "orgs/acme").rglob("*.json*"):
            self.assertNotIn("private-user", path.read_text(), str(path))

    def test_typed_collection_redacts_evidence_and_resolver_errors(self):
        self.refs()
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"].pop("shared")
        orgs.save(self.home, data, self.registry)
        self.fake("import json,os\nprint(json.dumps([{'id':'one','user':os.environ['JENKINS_USER']}]))", "jenkins")
        task = self.success(self.cli("task", "--question", "Synthetic collection", "--org", "acme", "--env", "staging",
                                     "--from", "2026-10-21T10:00:00Z", "--to", "2026-10-21T10:10:00Z", "--json"))
        collected = self.cli("collect", task["session"], "--operation", "jenkins.builds", "--params", json.dumps({"job":"deploy-api"}),
                             "--org", "acme", "--env", "staging", "--json")
        self.assertEqual(collected.returncode, 0, collected.stderr)
        root = self.home / "orgs/acme/workspace/sessions" / task["session"]
        for path in root.rglob("*"):
            if path.is_file():
                self.assertNotIn("private-user", path.read_text(), str(path))
        self.fake("import json,os,sys\nif sys.argv[1:3] == ['commands','resolve']:\n"
                  "    print(json.dumps({'error':os.environ['JENKINS_USER']}));sys.exit(1)\n", "jenkins")
        failed = self.cli("collect", task["session"], "--operation", "jenkins.builds", "--params", json.dumps({"job":"deploy-api"}),
                          "--org", "acme", "--env", "staging", "--refresh", "--json")
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("private-user", failed.stdout + failed.stderr)
