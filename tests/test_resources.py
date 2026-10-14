"""Connections bound to the resources they may touch. Offline."""
import json
import unittest
from unittest import mock

from helpers import OrgCase
from reckonlib import actions, connectors, orgs

GH = ["GH_TOKEN=gh-token"]


class ResourceTests(OrgCase):
    def setUp(self):
        super().setUp()
        self.org(connections={"gh": {"provider": "github", "resources": {"repo": ["acme/*"]}},
                              "gsc": {"provider": "gsc", "resources": {"site": ["sc-domain:acme.com"]}},
                              "k8s": {"provider": "kubernetes", "env": "staging", "resources": {"namespace": ["shop", "shop-*"]}}},
                 secrets={"gh": GH, "gsc": ["GSC_ACCESS_TOKEN=t"]})

    def bound(self, name, args, values=None):
        conn = orgs.load(self.home, "acme", self.registry)["connections"][name]
        connectors.bind(self.registry[conn["provider"]], args, conn, values or {})

    def test_targets_must_be_inside_the_allowed_resources(self):
        self.bound("gh", ["pr", "list", "--repo", "acme/api"])
        self.bound("gh", ["pr", "list", "-Racme/web"])
        for args in (["pr", "list", "--repo", "other/api"], ["pr", "list", "-R", "other/api"], ["pr", "list"]):
            with self.assertRaises(ValueError):
                self.bound("gh", args)
        self.bound("k8s", ["get", "pods", "-n", "shop-eu"])
        with self.assertRaisesRegex(ValueError, "outside this connection"):
            self.bound("k8s", ["get", "pods", "--namespace=billing"])

    def test_the_connection_default_counts_when_it_matches(self):
        self.bound("gsc", ["top", "queries", "--last", "7d"], {"GSC_SITE": "sc-domain:acme.com"})
        with self.assertRaisesRegex(ValueError, "outside this connection"):
            self.bound("gsc", ["top", "queries"], {"GSC_SITE": "sc-domain:other.com"})
        with self.assertRaisesRegex(ValueError, "name it with --site"):
            self.bound("gsc", ["top", "queries"])

    def test_reckon_run_and_prepared_writes_enforce_it(self):
        self.fake("print('ran')", "gh")
        refused = self.cli("run", "gh", "--org", "acme", "--env", "staging", "--", "pr", "list", "--repo", "other/api")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("outside this connection", refused.stderr)
        self.assertNotIn("ran", refused.stdout)
        with self.assertRaisesRegex(ValueError, "outside gh"):
            actions.prepare(self.home, self.ctx(), orgs.workspace(self.home, "acme"), "github.issue.create",
                            {"repo": "other/api", "title": "t", "body": "b"}, self.registry)

    def test_review_bypasses_are_closed(self):
        refused = [
            ("gh", ["issue", "view", "https://github.com/other/private/issues/1", "--repo", "acme/api"], {}),
            ("gh", ["search", "issues", "--repo", "acme/api"], {}),
            ("gh", ["pr", "list", "--repo", "acme/api,other/private"], {}),
            ("gh", ["pr", "list", "--search", "--repo=acme/api"], {"GH_REPO": "other/private"}),
            ("gh", ["repo", "view", "other/private"], {}),
            ("gsc", ["sites", "list", "--site", "sc-domain:acme.com"], {}),
            ("gsc", ["sites", "get", "sc-domain:other.com", "--site", "sc-domain:acme.com"], {}),
            ("gsc", ["inspect", "https://other.com/page", "--site", "sc-domain:acme.com"], {}),
            ("k8s", ["get", "pods", "-n", "shop", "-A"], {}),
            ("k8s", ["get", "pods", "-n", "shop", "--all-namespaces"], {}),
            ("k8s", ["get", "namespace/billing", "-n", "shop"], {}),
            ("k8s", ["get", "nodes", "-n", "shop"], {}),
        ]
        for name, args, values in refused:
            with self.assertRaises(ValueError, msg=(name, args)):
                self.bound(name, args, values)
        allowed = [
            ("gh", ["repo", "view", "acme/api"], {}),
            ("gh", ["pr", "list", "--repo", "acme/api"], {"GH_REPO": "acme/web"}),
            ("gsc", ["sites", "get", "sc-domain:acme.com"], {}),
            ("gsc", ["inspect", "https://www.acme.com/pricing", "--site", "sc-domain:acme.com"], {}),
            ("k8s", ["get", "pods", "-n", "shop", "-o", "wide"], {}),
        ]
        for name, args, values in allowed:
            self.bound(name, args, values)

    def test_aws_region_binding_uses_both_defaults_and_refuses_s3(self):
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["aws"] = {"provider": "aws", "resources": {"region": ["us-east-1"]}}
        orgs.save(self.home, data, self.registry)
        self.bound("aws", ["ec2", "describe-instances"], {"AWS_DEFAULT_REGION": "us-east-1"})
        with self.assertRaises(ValueError):
            self.bound("aws", ["ec2", "describe-instances"], {"AWS_DEFAULT_REGION": "us-west-2"})
        with self.assertRaisesRegex(ValueError, "cannot be limited"):
            self.bound("aws", ["s3", "ls", "s3://bucket", "--region", "us-east-1"])

    def test_narrowed_limits_apply_to_already_prepared_writes(self):
        root = orgs.workspace(self.home, "acme")
        data = orgs.load(self.home, "acme", self.registry)
        data["connections"]["gh"]["resources"] = {"repo": ["*"]}
        orgs.save(self.home, data, self.registry)
        record = actions.prepare(self.home, self.ctx(), root, "github.issue.create", {"repo": "other/api", "title": "t", "body": "b"}, self.registry)[0]
        data["connections"]["gh"]["resources"] = {"repo": ["acme/*"]}
        orgs.save(self.home, data, self.registry)
        with mock.patch.object(actions, "terminal", return_value=True):
            with self.assertRaisesRegex(ValueError, "now outside gh"):
                actions.apply(self.home, self.ctx(), root, record["id"], self.registry, confirm=lambda p: "yes")

    def test_connect_sets_and_clears_limits_and_rejects_unknown_kinds(self):
        result = self.cli("connect", "gh", "--org", "acme", "--allow", "repo=acme-labs/*", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["resources"], {"repo": ["acme/*", "acme-labs/*"]})
        self.assertIn("repo: acme/*, acme-labs/*", (orgs.workspace(self.home, "acme") / "RECKON.md").read_text())
        self.assertNotEqual(self.cli("connect", "gh", "--org", "acme", "--allow", "site=x").returncode, 0)
        cleared = self.cli("connect", "gh", "--org", "acme", "--allow-any", "--json")
        self.assertEqual(json.loads(cleared.stdout)["resources"], {})


if __name__ == "__main__":
    unittest.main()
