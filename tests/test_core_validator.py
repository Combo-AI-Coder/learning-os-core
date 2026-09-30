from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.validate_learning_os import (
    instance_write_policy_fingerprint,
    validate_core,
)

# Materialized Core repository root (for the real-snapshot integration test).
CORE_REPO_ROOT = Path(__file__).resolve().parents[1]

CORE_README = """# Learning OS — Core

Release: 0.4.0
"""


def write_file(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_yaml(root: Path, rel: str, data: dict) -> None:
    write_file(root, rel, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def core_config(protocol: dict | None = None, reusable_bases: list | None = None) -> dict:
    """A structurally valid config/core.yaml contract document."""
    return {
        "schema_version": "0.4",
        "document_type": "core_config",
        "updated_at": "2026-08-24T09:30:55+08:00",
        "product": {"id": "learning-os", "name": "Learning OS"},
        "manifest": {
            "release": "0.4.0",
            "runtime_session_write_policy_fingerprint": (
                instance_write_policy_fingerprint()
            ),
            # B2-B: manifest.artifact_schema 已移除；state schema 轴改由
            # supported_instance_state_schema_versions 声明（G8 D1/D3）。
            "supported_instance_state_schema_versions": ["0.3"],
        },
        "time": {
            "timestamp_format": "iso8601",
            "require_reliable_source": True,
            "timezone_ownership": "instance",
        },
        "protocol": protocol if protocol is not None else {"runtime_core": "protocol/runtime-core.md"},
        "governance": {
            "core_mutation": {
                "model": "pull_request_required",
                "force_push": "forbidden",
                "deletion": "forbidden",
                "required_checks": ["validate-core"],
            }
        },
        "domains": {
            "reusable_bases": reusable_bases if reusable_bases is not None else ["_template"],
            "template": "domains/_template/curriculum.yaml",
        },
    }


def synthetic_curriculum(domain: str = "_template") -> dict:
    """Synthetic (never real-learner) curriculum fixture."""
    return {
        "schema_version": "0.1",
        "document_type": "curriculum",
        "domain": {"id": domain, "title": domain},
        "curriculum_version": "0.1",
        "nodes": {},
        "edges": [],
        "aliases": {},
    }


def core_errors(root: Path) -> set[str]:
    return {f.code for f in validate_core(root) if f.severity == "error"}


class CoreValidatorTests(unittest.TestCase):
    def symlink_or_skip(self, link: Path, target: Path | str, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows host does not grant symbolic-link creation privilege")
            raise

    def make_snapshot(self, config: dict | None = None, readme: str | None = CORE_README, template: bool = True) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        if readme is not None:
            write_file(root, "README.md", readme)
        write_file(root, "protocol/runtime-core.md", "# Runtime Core\n")
        write_yaml(root, "config/core.yaml", config if config is not None else core_config())
        if template:
            write_yaml(root, "domains/_template/curriculum.yaml", synthetic_curriculum())
        return root

    def external_file(self, root: Path, name: str = "external.md", text: str = "# external\n") -> Path:
        """Create a real readable file outside root but under the same parent."""
        td = tempfile.TemporaryDirectory(dir=root.parent, prefix="s2a-outside-")
        self.addCleanup(td.cleanup)
        path = Path(td.name) / name
        path.write_text(text, encoding="utf-8")
        self.assertTrue(path.is_file())
        self.assertEqual(text, path.read_text(encoding="utf-8"))
        return path

    def assert_valid(self, root: Path) -> None:
        errors = [f.render() for f in validate_core(root) if f.severity == "error"]
        self.assertEqual([], errors)

    # ===== Positive: PASS =====

    def test_minimal_valid_core_snapshot(self):
        self.assert_valid(self.make_snapshot())

    def test_valid_core_config_pass(self):
        # Full contract surface: identity, manifest, compatibility, time,
        # governance, domains and protocol routing all present and valid.
        root = self.make_snapshot(config=core_config(
            protocol={
                "runtime_core": "protocol/runtime-core.md",
                "schema": "protocol/schema.md",
            },
        ))
        write_file(root, "protocol/schema.md", "# Schema\n")
        self.assert_valid(root)

    def test_synthetic_reusable_domain_template_fixture(self):
        # A synthetic reusable domain (declared in core.yaml) plus the
        # standard _template both validate as Core material.
        root = self.make_snapshot(config=core_config(reusable_bases=["_template", "synthetic-domain"]))
        write_yaml(root, "domains/synthetic-domain/curriculum.yaml", synthetic_curriculum("synthetic-domain"))
        self.assert_valid(root)

    def test_real_materialized_core_snapshot(self):
        # Integration: the actual materialized Core repository tree passes.
        self.assert_valid(CORE_REPO_ROOT)

    # ===== Negative: prohibited ownership material =====

    def test_fail_core_contains_learner(self):
        root = self.make_snapshot()
        write_yaml(root, "learner/background.yaml", {"schema_version": "0.3", "document_type": "learner_background"})
        self.assertIn("core.plane", core_errors(root))

    def test_fail_core_contains_evidence(self):
        root = self.make_snapshot()
        write_file(root, "evidence/evi_1.yaml", "# empty marker\n")
        self.assertIn("core.plane", core_errors(root))

    def test_fail_core_contains_private_lineage_control(self):
        root = self.make_snapshot()
        write_yaml(root, "runtime/lineages/learning-os-design.yaml", {
            "schema_version": "0.3", "document_type": "lineage_control",
            "lineage": {"id": "learning-os-design"}, "active_generation": 8, "pending_handoff": None,
        })
        errors = core_errors(root)
        self.assertIn("core.private_lineage", errors)

    def test_fail_core_contains_instance_plane_document(self):
        # Instance-plane YAML material even outside canonical directories.
        root = self.make_snapshot()
        write_yaml(root, "docs/learner-knowledge.yaml", {
            "schema_version": "0.3", "document_type": "learner_knowledge",
            "domain": "domain-a", "concepts": {},
        })
        self.assertIn("core.plane_document", core_errors(root))

    def test_fail_instance_authoritative_config(self):
        root = self.make_snapshot()
        write_file(root, "config/project.yaml", "# instance-authoritative\n")
        self.assertIn("core.config_forbidden", core_errors(root))

    def test_fail_core_config_declares_instance_repository_id(self):
        config = core_config()
        config["manifest"]["instance_repository_id"] = 1343815303
        self.assertIn("core.prohibited_key", core_errors(self.make_snapshot(config=config)))

    # ===== Negative: deployment-active configuration =====

    def test_fail_deployment_active_values(self):
        config = core_config()
        config["manifest"]["deployment_status"] = "active"
        self.assertIn("core.deployment_authority", core_errors(self.make_snapshot(config=config)))

    def test_fail_deployment_epoch_and_write_state_keys(self):
        config = core_config()
        config["deployment"] = {"epoch": 2, "write_state": "active"}
        self.assertIn("core.prohibited_key", core_errors(self.make_snapshot(config=config)))

    def test_fail_canonical_status_drift(self):
        config = core_config()
        config["manifest"]["canonical_status"] = "canonical"
        self.assertIn("core.deployment_authority", core_errors(self.make_snapshot(config=config)))

    # ===== Negative: contract/schema semantics =====

    def test_fail_wrong_schema_version(self):
        config = core_config()
        config["schema_version"] = "0.3"
        self.assertIn("core.schema_version", core_errors(self.make_snapshot(config=config)))

    def test_fail_unreliable_timestamp_semantics(self):
        config = core_config()
        config["time"]["require_reliable_source"] = False
        self.assertIn("core.reliable_time", core_errors(self.make_snapshot(config=config)))

    def test_fail_missing_core_config(self):
        root = self.make_snapshot()
        (root / "config/core.yaml").unlink()
        self.assertIn("core.config_missing", core_errors(root))

    def test_fail_missing_instance_state_schema_support(self):
        config = core_config()
        config["manifest"]["supported_instance_state_schema_versions"] = []
        self.assertIn("core.instance_schema_support", core_errors(self.make_snapshot(config=config)))

    def test_fail_runtime_session_write_policy_fingerprint_format(self):
        config = core_config()
        config["manifest"]["runtime_session_write_policy_fingerprint"] = "invalid"
        self.assertIn(
            "core.runtime_session_write_policy",
            core_errors(self.make_snapshot(config=config)),
        )

    def test_core_validation_allows_different_valid_write_policy_fingerprint(self):
        config = core_config()
        config["manifest"]["runtime_session_write_policy_fingerprint"] = "0" * 64
        self.assert_valid(self.make_snapshot(config=config))

    def test_repository_core_write_policy_fingerprint_matches_local_semantics(self):
        config = yaml.safe_load(
            (CORE_REPO_ROOT / "config/core.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            instance_write_policy_fingerprint(),
            config["manifest"]["runtime_session_write_policy_fingerprint"],
        )

    # ===== Negative: structural credentials/secrets =====

    def test_fail_structural_secret_key(self):
        config = core_config()
        config["bootstrap"] = {"api_key": "whatever"}
        self.assertIn("core.prohibited_key", core_errors(self.make_snapshot(config=config)))

    def test_fail_structural_credential_value(self):
        root = self.make_snapshot()
        write_file(root, "protocol/notes.md", "token: ghp_0123456789abcdefGHIJKL\n")
        self.assertIn("core.credential_value", core_errors(root))

    def test_fail_structural_credential_value_in_yaml(self):
        config = core_config()
        config["product"]["notes"] = "leaked ghp_0123456789abcdefGHIJKL"
        self.assertIn("core.credential_value", core_errors(self.make_snapshot(config=config)))

    # ===== Negative: layout/routing integrity =====

    def test_fail_unexpected_top_level_entry(self):
        root = self.make_snapshot()
        write_file(root, "topics/topic-a/goal.yaml", "# instance plane\n")
        self.assertIn("core.plane", core_errors(root))

    def test_fail_unknown_top_level_file(self):
        root = self.make_snapshot()
        write_file(root, "stray.txt", "x")
        self.assertIn("core.top_level", core_errors(root))

    def test_fail_undeclared_domain_base(self):
        root = self.make_snapshot()
        write_yaml(root, "domains/undeclared-domain/curriculum.yaml", synthetic_curriculum("undeclared-domain"))
        self.assertIn("core.domain_undeclared", core_errors(root))

    def test_fail_declared_base_without_directory(self):
        root = self.make_snapshot(config=core_config(reusable_bases=["_template", "missing-domain"]))
        self.assertIn("core.domain_declared_missing", core_errors(root))

    def test_fail_protocol_route_to_missing_file(self):
        config = core_config(protocol={"runtime_core": "protocol/runtime-core.md", "schema": "protocol/schema.md"})
        root = self.make_snapshot(config=config)  # schema.md intentionally absent
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_absolute_existing_external_file(self):
        root = self.make_snapshot()
        outside = self.external_file(root)
        write_yaml(root, "config/core.yaml", core_config(protocol={"runtime_core": str(outside)}))
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_traversal_existing_external_file(self):
        root = self.make_snapshot()
        outside = self.external_file(root)
        rel = f"../{outside.parent.name}/{outside.name}"
        write_yaml(root, "config/core.yaml", core_config(protocol={"runtime_core": rel}))
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_windows_drive_form(self):
        root = self.make_snapshot(config=core_config(protocol={"runtime_core": r"C:\outside\runtime-core.md"}))
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_backslash_form(self):
        root = self.make_snapshot(config=core_config(protocol={"runtime_core": r"protocol\runtime-core.md"}))
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_symlink_inside_snapshot(self):
        root = self.make_snapshot(config=core_config(protocol={"runtime_core": "protocol/link.route"}))
        write_file(root, "protocol/inside-target.route", "inside\n")
        self.symlink_or_skip(root / "protocol/link.route", "inside-target.route")
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_symlink_outside_snapshot(self):
        root = self.make_snapshot(config=core_config(protocol={"runtime_core": "protocol/link.route"}))
        outside = self.external_file(root, name="outside.route", text="outside\n")
        self.symlink_or_skip(root / "protocol/link.route", outside)
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_protocol_route_symlink_directory_component(self):
        root = self.make_snapshot(config=core_config(protocol={"runtime_core": "protocol/linked/outside.route"}))
        td = tempfile.TemporaryDirectory(dir=root.parent, prefix="s2a-outside-dir-")
        self.addCleanup(td.cleanup)
        outside_dir = Path(td.name)
        (outside_dir / "outside.route").write_text("outside\n", encoding="utf-8")
        self.symlink_or_skip(root / "protocol/linked", outside_dir, target_is_directory=True)
        self.assertIn("core.protocol_route", core_errors(root))

    def test_fail_unrouted_protocol_document(self):
        root = self.make_snapshot()
        write_file(root, "protocol/orphan.md", "# unrouted\n")
        self.assertIn("core.protocol_orphan", core_errors(root))

    def test_readme_does_not_own_deployment_status(self):
        root = self.make_snapshot(readme="# Learning OS — Core\n\nRelease: 0.4.0\n")
        self.assertNotIn("core.status_marker", core_errors(root))

    def test_fail_domain_identity_mismatch(self):
        root = self.make_snapshot()
        data = synthetic_curriculum("other-domain")
        write_yaml(root, "domains/_template/curriculum.yaml", data)
        self.assertIn("core.domain_identity", core_errors(root))


if __name__ == "__main__":
    unittest.main()
