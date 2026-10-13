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
