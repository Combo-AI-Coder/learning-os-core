from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class CollaborationBoundaryTests(unittest.TestCase):
    def test_core_has_public_safe_agent_contract(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("product Core", text)
        self.assertIn("Runtime-Control is authoritative", text)
        self.assertIn("project/session state database", text)
        self.assertIn("feature branch", text)

    def test_project_design_handoff_is_not_runtime_routed(self):
        config = yaml.safe_load((ROOT / "config/core.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("project_handoff", config["protocol"])
        self.assertFalse((ROOT / "protocol/project-handoff-policy.md").exists())

        runtime_core = (ROOT / "protocol/runtime-core.md").read_text(encoding="utf-8")
        self.assertNotIn("project-handoff-policy.md", runtime_core)
        self.assertNotIn("materialized project-design lineage", runtime_core)
        self.assertIn("project/session writer authority", runtime_core)

    def test_split_bootstrap_does_not_reference_legacy_project_config(self):
        naming = (ROOT / "protocol/conversation-naming-policy.md").read_text(encoding="utf-8")
        runtime_core = (ROOT / "protocol/runtime-core.md").read_text(encoding="utf-8")
        self.assertNotIn("config/project.yaml", naming)
        self.assertNotIn("config/project.yaml", runtime_core)
        self.assertIn("config/core.yaml", naming)
        self.assertIn("sequence_registry", naming)

    def test_core_contract_matches_current_pr_validation_and_deployment_semantics(self):
        config = yaml.safe_load((ROOT / "config/core.yaml").read_text(encoding="utf-8"))
        self.assertEqual(["validate"], config["governance"]["core_mutation"]["required_checks"])

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("exact Core commit pinned by Runtime-Control", readme)
        self.assertIn("does not change the deployed Core", readme)
        self.assertNotIn(
            "modern-language-models curriculum is deferred",
            (ROOT / "config/core.yaml").read_text(encoding="utf-8"),
        )

    def test_remaining_core_protocols_do_not_own_project_collaboration(self):
        for relative in (
            "protocol/persistence-policy.md",
            "protocol/schema.md",
            "protocol/repository-governance-policy.md",
        ):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("project-handoff-policy.md", text)

        governance = (ROOT / "protocol/repository-governance-policy.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("domains/<domain>/probes.md", governance)
        self.assertIn("Core / CORE_PROTECTED", governance)
        self.assertIn("Project/session collaboration", governance)
        self.assertIn("external to Core", governance)

if __name__ == "__main__":
    unittest.main()
