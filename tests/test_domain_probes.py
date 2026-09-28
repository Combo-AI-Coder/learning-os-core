from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class DomainProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load(
            (ROOT / "config/core.yaml").read_text(encoding="utf-8")
        )
        cls.domain = "modern-language-models"
        cls.domain_dir = ROOT / "domains" / cls.domain
        cls.curriculum = yaml.safe_load(
            (cls.domain_dir / "curriculum.yaml").read_text(encoding="utf-8")
        )

    def test_anchor_resolves_to_declared_domain_node_and_capabilities(self):
        self.assertIn(
            self.domain, self.config["domains"]["reusable_bases"]
        )
        self.assertEqual(
            self.domain, self.curriculum["domain"]["id"]
        )

        filename = self.config["domains"]["optional_probe_file"]
        self.assertEqual("probes.md", filename)
        asset = (self.domain_dir / filename).read_text(encoding="utf-8")

        def field(label):
            values = re.findall(
                rf"^- {re.escape(label)}: `([^`]+)`$",
                asset,
                flags=re.MULTILINE,
            )
            self.assertEqual(1, len(values), label)
            return values[0]

        self.assertEqual(
            "mlm.tokens-context.trace-and-change.v1",
            field("Stable id"),
        )
        node_id = field("Target node")
        self.assertEqual("representation.tokens_and_context", node_id)
        self.assertIn(node_id, self.curriculum["nodes"])

        node = self.curriculum["nodes"][node_id]
        profile = self.curriculum["capability_profiles"][
            node["capability_profile"]
        ]
        for label in ("Target capability", "Supporting capability"):
            with self.subTest(label=label):
                self.assertIn(field(label), profile)

    def test_probe_discovery_preserves_pull_based_loading_contract(self):
        loading = self.config["bootstrap"]["loading"]
        self.assertIs(
            loading["load_domain_probes_if_probe_selection_relevant"],
            True,
        )
        self.assertIs(loading["load_domain_probes_by_default"], False)
        self.assertIs(loading["load_curriculum_by_default"], False)
        self.assertIs(loading["load_evidence_by_default"], False)

        policy_path = self.config["protocol"]["curriculum"]
        self.assertEqual("protocol/curriculum-policy.md", policy_path)
        policy = " ".join(
            (ROOT / policy_path).read_text(encoding="utf-8").split()
        )
        for required in (
            "`domains/<domain>/probes.md`",
            "`domains.optional_probe_file`",
            "Runtime MUST NOT load Domain probe assets by default.",
            "Runtime MAY load the selected Domain's asset only when "
            "a probe-selection decision is relevant",
            "A missing optional file is normal",
            "same trusted Core snapshot",
        ):
            with self.subTest(required=required):
                self.assertIn(required, policy)


if __name__ == "__main__":
    unittest.main()
