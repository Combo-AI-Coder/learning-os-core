from __future__ import annotations
"""V0.4-B2-B split-aware Instance validation test envelope.

验证面：validate_instance(instance_snapshot, deployed_core, deployment_binding)。

Fixture 策略（synthetic-only）：
- 全部 fixture 为程序化临时目录（tempfile），不落盘 tests/fixtures/**，
  不含任何真实 learner 状态、真实 Evidence 或真实凭证。
- deployed_core 是合成物化的 Core 快照（仅 load_core 所需的最小面：
  config/core.yaml + domains/<base>/curriculum.yaml）。
- deployment_binding 是合成 trusted deployment binding（B2-B 定义的
  synthetic fixture 形态）；live binding（epoch enforcement /
  write_state routing / ID 解析）属 resolver/runtime surface，不在本面实现。

另含一条集成测试：用真实物化 Core 仓库树作为 deployed_core（证明 exact
Core snapshot 满足 load_core 契约）。真实 Instance 快照的 exact 验证在
saga Step 10/14 以显式记录完成，不在 CI 单测中依赖 sibling checkout。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.validate_learning_os import validate_instance

# 与授权记录一致的真实 repository ID（仅作为 synthetic context 的取值）
CORE_REPO_ID = 1343815302
INSTANCE_REPO_ID = 1343815864
CORE_REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "base-domain"
BASE_VERSION = "0.2"
EVI_ID = "evi-synthetic-001"


def write_file(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_yaml(root: Path, rel: str, data: dict) -> None:
    write_file(root, rel, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def deployment_binding(core_commit: str | None = None) -> dict:
    """合成 deployment binding（B2-B 唯一定义的 synthetic 形态）。"""
    return {
        "context_type": "synthetic",
        "core_repository_id": CORE_REPO_ID,
        "core_commit": core_commit or "a" * 40,
        "instance_repository_id": INSTANCE_REPO_ID,
        "topology": "noncanonical-split",
        "epoch": 1,
        "write_state": "not_deployed",
    }


# ---- 合成 Core 快照（deployed_core 最小面）----

def core_curriculum(domain: str = DOMAIN, version: str = BASE_VERSION) -> dict:
    return {
        "schema_version": "0.1",
        "document_type": "curriculum",
        "domain": {"id": domain, "title": domain},
        "curriculum_version": version,
        "nodes": {
            f"{domain}.foundation": {"title": "Foundation", "kind": "concept"},
            f"{domain}.application": {"title": "Application", "kind": "application"},
        },
        "edges": [{"id": f"{domain}.edge-1", "relation": "supports", "strength": "medium"}],
        "aliases": {f"{domain}.alias": f"{domain}.foundation"},
        "capability_profiles": {"conceptual_core": {"conceptual_structure": "expected"}},
    }


def make_core(root: Path, domain: str = DOMAIN, version: str = BASE_VERSION,
              state_schemas: list | None = None) -> Path:
    write_yaml(root, "config/core.yaml", {
        "schema_version": "0.4",
        "document_type": "core_config",
        "product": {"id": "learning-os", "name": "Learning OS"},
        "manifest": {
            "release": "0.4.0",
            "supported_instance_state_schema_versions": state_schemas if state_schemas is not None else ["0.3"],
        },
    })
    write_yaml(root, f"domains/{domain}/curriculum.yaml", core_curriculum(domain, version))
    return root


# ---- Instance 文档构造器（全部 synthetic state schema 0.3）----

def instance_config_doc() -> dict:
    return {
        "schema_version": "0.4",
        "document_type": "instance_config",
        "updated_at": "2026-08-24T12:00:00+08:00",
        "product": {"id": "learning-os", "name": "Learning OS"},
        "instance": {"display_timezone": "Asia/Shanghai"},
        "nonproduction": True,
    }


def make_instance(root: Path) -> Path:
    write_file(root, "README.md", "# Learning OS — Instance\n\nStatus: NONCANONICAL V0.4 TARGET — NOT DEPLOYED\n")
    write_yaml(root, "config/instance.yaml", instance_config_doc())
    return root


def extension_doc(domain: str = DOMAIN, version: str = BASE_VERSION, revision: int = 1,
                  nodes: dict | None = None, edges: list | None = None,
                  aliases: dict | None = None, capability_profiles: dict | None = None,
                  probes: list | None = None) -> dict:
    doc: dict = {
        "schema_version": "0.4",
        "document_type": "curriculum_extension",
        "domain": domain,
        "base_version": version,
        "extension_revision": revision,
    }
    if nodes is not None:
        doc["nodes"] = nodes
    if edges is not None:
        doc["edges"] = edges
    if aliases is not None:
        doc["aliases"] = aliases
    if capability_profiles is not None:
        doc["capability_profiles"] = capability_profiles
    if probes is not None:
        doc["probes"] = probes
    return doc


def write_full_state(root: Path, *, curriculum_refs: list, provenance: list,
                     handoff_ref: str, topic: str = "modern-language-models",
                     subtopic: str = "language-modeling", knowledge_schema: str = "0.3",
                     evidence_extra: dict | None = None, plan_provenance_extra: dict | None = None) -> None:
    """写一套完整合法的 Instance 0.3 状态文档；关键参数由各测试注入。"""
    t, s = topic, subtopic
    write_yaml(root, f"learner/knowledge/{t}.yaml", {
        "schema_version": knowledge_schema, "document_type": "learner_knowledge",
        "revision": 1, "domain": DOMAIN,
        "concepts": {f"{DOMAIN}.foundation": {"capabilities": {"explanation": {
            "state": "provisional", "confidence": "low",
            "evidence_refs": {"support": [EVI_ID]},
        }}}},
    })
    evi = {
        "schema_version": "0.3", "document_type": "evidence", "id": EVI_ID,
        "observed_at": "2026-08-24T10:00:00+08:00",
        "observation": "synthetic observation",
        "interpretation": {"direction": "support", "diagnosticity": "low",
                           "novelty": "low", "confidence": "low"},
        "targets": [f"{DOMAIN}.foundation"],
    }
    if evidence_extra:
        evi.update(evidence_extra)
    write_yaml(root, f"evidence/{EVI_ID}.yaml", evi)
    write_yaml(root, f"topics/{t}/goal.yaml", {
        "schema_version": "0.3", "document_type": "topic_goal",
        "revision": 1, "topic": t, "goal": {"statement": "synthetic goal"},
    })
    write_yaml(root, f"topics/{t}/plan.yaml", {
        "schema_version": "0.3", "document_type": "topic_plan",
        "revision": 1, "topic": t,
        "plan": {"status": "active", "based_on": {"goal_revision": 1, "curricula": provenance}},
    })
    write_yaml(root, f"topics/{t}/progress.yaml", {
        "schema_version": "0.3", "document_type": "topic_progress",
        "revision": 1, "topic": t, "plan_revision": 1, "lifecycle": "active", "milestones": {},
    })
    write_yaml(root, f"topics/{t}/subtopics/{s}/definition.yaml", {
        "schema_version": "0.3", "document_type": "subtopic_definition",
        "subtopic": {"id": s, "kind": "standard", "lifecycle": "active"},
    })
    write_yaml(root, f"topics/{t}/subtopics/{s}/plan.yaml", {
        "schema_version": "0.3", "document_type": "subtopic_plan",
        "revision": 1, "topic": t, "subtopic": s,
        "plan": {"status": "active", "based_on": {"topic_plan_revision": 1},
                 "milestones": [{"id": "m1", "curriculum_refs": curriculum_refs}]},
    })
    write_yaml(root, f"topics/{t}/subtopics/{s}/progress.yaml", {
        "schema_version": "0.3", "document_type": "subtopic_progress",
        "revision": 1, "topic": t, "subtopic": s, "plan_revision": 1, "milestones": {},
    })
    handoff_rel = f"topics/{t}/subtopics/{s}/handoffs/{t}-main-lineage/C01-to-C02.yaml"
    write_yaml(root, handoff_rel, {
        "schema_version": "0.3", "document_type": "learning_handoff",
        "topic": t, "branch_id": f"{s}-main", "lineage_id": f"{t}-main-lineage",
        "from_generation": 1, "to_generation": 2,
    })
    write_yaml(root, f"topics/{t}/coordination/branches.yaml", {
        "schema_version": "0.3", "document_type": "branch_registry",
        "revision": 1, "topic": t, "branches": {f"{s}-main": {"role": "main", "lifecycle": "active"}},
    })
    write_yaml(root, f"topics/{t}/coordination/branches/{s}-main/runtime.yaml", {
        "schema_version": "0.3", "document_type": "branch_runtime",
        "revision": 1, "topic": t, "branch_id": f"{s}-main", "lineage_id": f"{t}-main-lineage",
        "active_generation": 2, "pending_successor": None,
        "generations": {"1": {"lifecycle": "archived", "handoff_ref": handoff_ref},
                        "2": {"lifecycle": "active"}},
    })
    write_yaml(root, "execution/weekly/2026-w34.yaml", {
        "schema_version": "0.3", "document_type": "weekly_execution",
        "revision": 1, "window": {"start": "2026-08-24", "end": "2026-08-30"},
    })
    write_yaml(root, "runtime/ui/conversation-sequences.yaml", {
        "schema_version": "0.3", "document_type": "conversation_sequence_registry",
        "sequence_format": {"prefix": "C", "minimum_width": 2,
                            "allocation": "monotonic_reservation", "usage": "synthetic_test"},
        "scopes": {"learning_os": {"last_allocated": 1, "bootstrap_basis": "synthetic"}},
    })


class _Unset:
    """哨兵类：区分“未提供 ctx（用合成默认）”与“显式传入 None（缺失 context）”。"""


_UNSET = _Unset()


class InstanceValidationTests(unittest.TestCase):
    """B2-B required test envelope：positive PASS / negative FAIL。"""

    def setUp(self) -> None:
        core_td = tempfile.TemporaryDirectory()
        inst_td = tempfile.TemporaryDirectory()
        self.addCleanup(core_td.cleanup)
        self.addCleanup(inst_td.cleanup)
        self.core = make_core(Path(core_td.name))
        self.instance = make_instance(Path(inst_td.name))

    def errors(self, instance: Path | None = None, core: Path | None = None,
               ctx: object = _UNSET) -> set[str]:
        findings = validate_instance(
            instance if instance is not None else self.instance,
            core if core is not None else self.core,
            deployment_binding() if ctx is _UNSET else ctx,
        )
        return {f.code for f in findings if f.severity == "error"}

    def assert_pass(self, instance: Path | None = None, core: Path | None = None,
                    ctx: object = _UNSET) -> None:
        errors = [f.render() for f in validate_instance(
            instance if instance is not None else self.instance,
            core if core is not None else self.core,
            deployment_binding() if ctx is _UNSET else ctx,
        ) if f.severity == "error"]
        self.assertEqual([], errors)

    def valid_refs(self, node: str, domain: str = DOMAIN) -> list:
        return [{"type": "curriculum_node", "domain": domain, "id": node}]

    def valid_legacy_provenance(self, version: str = BASE_VERSION) -> list:
        return [{"domain": DOMAIN, "curriculum_version": version}]

    def valid_new_provenance(self, version: str = BASE_VERSION, revision: int = 1) -> list:
        return [{"domain": DOMAIN, "base_version": version, "extension_revision": revision}]

    def handoff_ref_ok(self) -> str:
        return "topics/modern-language-models/subtopics/language-modeling/handoffs/modern-language-models-main-lineage/C01-to-C02.yaml"

    # ===== Positive: PASS =====

    def test_minimal_instance_config_pass(self):
        # minimal instance_config：仅 README + config/instance.yaml 即合法。
        self.assert_pass()

    def test_valid_state_documents_schema_03_pass(self):
        # 全套 0.3 状态文档（learner/Evidence/Topic/Subtopic/branch/weekly/sequence）。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_valid_core_base_reference_pass(self):
        # subtopic_plan 的 curriculum_refs 解析到 Core base 节点。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_additive_extension_pass(self):
        # additive extension：新增节点/边，不触碰 Core base；引用经合并视图解析。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(nodes={f"{DOMAIN}.local-extension": {"title": "Local", "kind": "application"}}))
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.local-extension"),
                         provenance=self.valid_new_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_new_local_domain_pass(self):
        # 新本地 Domain（curriculum/local/**）不与 Core base 冲突即合法。
        write_yaml(self.instance, "curriculum/local/new-domain/curriculum.yaml", {
            "schema_version": "0.3", "document_type": "curriculum",
            "domain": {"id": "new-domain", "title": "New Domain"},
            "curriculum_version": "1.0",
            "nodes": {"new-domain.root": {"title": "Root", "kind": "concept"}},
            "edges": [], "aliases": {},
        })
        write_full_state(self.instance, curriculum_refs=self.valid_refs("new-domain.root", "new-domain"),
                         provenance=[{"domain": "new-domain", "curriculum_version": "1.0"}],
                         handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_new_local_probe_pass(self):
        # extension 新增 local probe（additive probes 列表）。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(nodes={f"{DOMAIN}.probe-node": {"title": "Probe Node", "kind": "skill"}},
                                 probes=[{"id": f"{DOMAIN}.probe-1", "prompt": "synthetic probe"}]))
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.probe-node"),
                         provenance=self.valid_new_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_legacy_provenance_pass(self):
        # legacy form（curriculum_version）对 Core base 仍可解析。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_new_provenance_pass(self):
        # new form（base_version + extension_revision）在存在 extension 时可解析。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(nodes={f"{DOMAIN}.local-extension": {"title": "Local", "kind": "application"}}))
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_new_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_branch_runtime_pass(self):
        # branch_runtime（学习 lineage，Instance 拥有）结构合法。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_safe_in_root_handoff_ref_pass(self):
        # handoff_ref 指向 Instance root 内既有 learning_handoff 文档。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assert_pass()

    def test_real_core_snapshot_as_deployed_core_pass(self):
        # 集成：真实物化 Core 仓库树作为 deployed_core（minimal instance）。
        self.assert_pass(core=CORE_REPO_ROOT)

    def test_cli_instance_surface_pass(self):
        # CLI 面：--instance + --core-snapshot + --deployment-binding（file 形态）。
        ctx_file = self.instance.parent / "deployment-binding.yaml"
        write_file(self.instance.parent, "deployment-binding.yaml", yaml.safe_dump(deployment_binding(), sort_keys=False))
        result = subprocess.run(
            [sys.executable, str(CORE_REPO_ROOT / "scripts/validate_learning_os.py"),
             str(self.instance), "--instance", "--core-snapshot", str(self.core),
             "--deployment-binding", str(ctx_file)],
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    # ===== Negative: FAIL =====

    def test_fail_state_schema_04(self):
        # 状态文档 schema_version 0.4：Core 仅支持 0.3（D2），fail closed。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok(),
                         knowledge_schema="0.4")
        self.assertIn("instance.state_schema_unsupported", self.errors())

    def test_fail_unsupported_state_schema(self):
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok(),
                         knowledge_schema="0.5")
        self.assertIn("instance.state_schema_unsupported", self.errors())

    def test_fail_missing_core(self):
        # deployed_core 缺失：fail closed。
        self.assertIn("instance.core_snapshot", self.errors(core=self.instance / "no-such-core"))

    def test_fail_missing_deployment_binding(self):
        # trusted context 缺失：fail closed。
        self.assertIn("instance.deployment_binding", self.errors(ctx=None))

    def test_fail_core_owned_top_level_directory(self):
        # Core 拥有的顶层目录（domains/）出现在 Instance 快照。
        write_yaml(self.instance, "domains/base-domain/curriculum.yaml", core_curriculum())
        self.assertIn("instance.core_owned_top", self.errors())

    def test_fail_core_owned_config_file(self):
        # config/core.yaml 属 Core plane，禁止出现在 Instance config/。
        write_file(self.instance, "config/core.yaml", "# core contract\n")
        self.assertIn("instance.config_forbidden", self.errors())

    def test_fail_lineage_control_in_instance(self):
        # project-design lineage_control 属 Control plane：目录与文档双拒绝。
        write_yaml(self.instance, "runtime/lineages/learning-os-design.yaml", {
            "schema_version": "0.3", "document_type": "lineage_control",
            "lineage": {"id": "learning-os-design"}, "active_generation": 8, "pending_handoff": None,
        })
        errors = self.errors()
        self.assertIn("instance.private_lineage", errors)
        self.assertIn("instance.forbidden_document", errors)

    def test_fail_deployment_authority_fields(self):
        # deployment/control authority 字段（deployment_epoch 等）禁止出现在 Instance。
        config = instance_config_doc()
        config["deployment_epoch"] = 2
        write_yaml(self.instance, "config/instance.yaml", config)
        self.assertIn("instance.authority_key", self.errors())

    def test_fail_core_node_collision(self):
        # extension 节点与 Core base 节点同 ID = shadow，fail closed。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(nodes={f"{DOMAIN}.foundation": {"title": "Shadow", "kind": "concept"}}))
        self.assertIn("instance.node_collision", self.errors())

    def test_fail_core_edge_collision(self):
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(edges=[{"id": f"{DOMAIN}.edge-1", "relation": "extends", "strength": "weak"}]))
        self.assertIn("instance.edge_collision", self.errors())

    def test_fail_capability_override(self):
        # extension capability_profiles 覆盖 Core base profile = fail closed。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(capability_profiles={"conceptual_core": {"transfer": "not_expected"}}))
        self.assertIn("instance.capability_override", self.errors())

    def test_fail_delete_tombstone_core(self):
        # extension 节点携带 tombstone = 删除语义，additive-only 拒绝。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(nodes={f"{DOMAIN}.foundation": {"tombstone": True}}))
        self.assertIn("instance.core_delete", self.errors())

    def test_fail_core_alias_redirect(self):
        # extension 重定义 Core base alias = redirect，fail closed。
        write_yaml(self.instance, "curriculum/extensions/base-extension.yaml",
                   extension_doc(aliases={f"{DOMAIN}.alias": f"{DOMAIN}.application"}))
        self.assertIn("instance.alias_redirect", self.errors())

    def test_fail_same_id_different_content(self):
        # 本地 Domain 与 Core base 同 ID 不同内容 = shadow，fail closed。
        write_yaml(self.instance, "curriculum/local/base-domain/curriculum.yaml", {
            "schema_version": "0.3", "document_type": "curriculum",
            "domain": {"id": DOMAIN, "title": "Shadow Domain"},
            "curriculum_version": "9.9",
            "nodes": {f"{DOMAIN}.other": {"title": "Other", "kind": "concept"}},
            "edges": [], "aliases": {},
        })
        self.assertIn("instance.domain_shadow", self.errors())

    def test_fail_unresolved_semantic_reference(self):
        # curriculum_refs 指向有效 domain 中不存在的节点。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.nonexistent"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok())
        self.assertIn("reference.curriculum_node", self.errors())

    def test_fail_traversal_handoff_ref(self):
        # handoff_ref 携带 ../ 穿越：fail closed。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(),
                         handoff_ref="../../outside/secret.yaml")
        self.assertIn("instance.ref_traversal", self.errors())

    def test_fail_absolute_handoff_ref(self):
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(),
                         handoff_ref="/etc/passwd")
        self.assertIn("instance.ref_absolute", self.errors())

    def test_fail_cross_plane_reference(self):
        # 引用落在 Instance 自有 plane 之外（protocol/ 属 Core plane）。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(),
                         handoff_ref="protocol/runtime-core.md")
        self.assertIn("instance.ref_cross_plane", self.errors())

    def test_fail_unknown_document_type(self):
        write_yaml(self.instance, "learner/notes.yaml", {
            "schema_version": "0.3", "document_type": "mystery_document",
        })
        self.assertIn("instance.unknown_document_type", self.errors())

    def test_fail_malformed_evidence(self):
        # Evidence 使用 legacy classification_confidence = malformed。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=self.valid_legacy_provenance(), handoff_ref=self.handoff_ref_ok(),
                         evidence_extra={"classification_confidence": "high"})
        self.assertIn("evidence.legacy_confidence", self.errors())

    def test_fail_ambiguous_provenance_form(self):
        # 同一条目同时携带 legacy 与 new form 字段 = ambiguous。
        write_full_state(self.instance, curriculum_refs=self.valid_refs(f"{DOMAIN}.foundation"),
                         provenance=[{"domain": DOMAIN, "curriculum_version": BASE_VERSION,
                                      "base_version": BASE_VERSION, "extension_revision": 1}],
                         handoff_ref=self.handoff_ref_ok())
        self.assertIn("instance.provenance_ambiguous", self.errors())

    # ===== Negative: trusted context fail-closed 形态 =====

    def test_fail_deployment_binding_unknown_key(self):
        ctx = deployment_binding()
        ctx["live_endpoint"] = "https://github.example"
        self.assertIn("instance.deployment_binding_keys", self.errors(ctx=ctx))

    def test_fail_deployment_binding_live_type(self):
        # live binding 形态未在 B2-B 定义：拒绝。
        ctx = deployment_binding()
        ctx["context_type"] = "live"
        self.assertIn("instance.deployment_binding_type", self.errors(ctx=ctx))

    def test_fail_deployment_binding_bad_commit(self):
        ctx = deployment_binding()
        ctx["core_commit"] = "not-a-commit"
        self.assertIn("instance.deployment_binding_commit", self.errors(ctx=ctx))

    def test_fail_deployment_binding_missing_key(self):
        ctx = deployment_binding()
        del ctx["epoch"]
        self.assertIn("instance.deployment_binding_missing", self.errors(ctx=ctx))

    # ===== Negative: Instance config 边界 =====

    def test_fail_instance_config_missing(self):
        (self.instance / "config/instance.yaml").unlink()
        self.assertIn("instance.config_missing", self.errors())

    def test_fail_instance_config_core_override(self):
        # instance.yaml 重定义 Core 拥有区块（chat_routing 等）= fail closed。
        config = instance_config_doc()
        config["chat_routing"] = {"modes": ["learning"]}
        write_yaml(self.instance, "config/instance.yaml", config)
        self.assertIn("instance.core_override", self.errors())

    def test_fail_instance_product_mismatch(self):
        # product.id 与 deployed Core 不一致。
        config = instance_config_doc()
        config["product"]["id"] = "other-os"
        write_yaml(self.instance, "config/instance.yaml", config)
        self.assertIn("instance.product_mismatch", self.errors())


class InstanceLearningHandoffTargetIntegrityTests(unittest.TestCase):
    """R1 split-Instance Branch-runtime handoff target/type/identity envelope."""

    HANDOFF = "topics/modern-language-models/subtopics/language-modeling/handoffs/modern-language-models-main-lineage/C01-to-C02.yaml"
    RUNTIME = "topics/modern-language-models/coordination/branches/language-modeling-main/runtime.yaml"

    def setUp(self) -> None:
        core_td = tempfile.TemporaryDirectory()
        inst_td = tempfile.TemporaryDirectory()
        self.addCleanup(core_td.cleanup)
        self.addCleanup(inst_td.cleanup)
        self.core = make_core(Path(core_td.name))
        self.instance = make_instance(Path(inst_td.name))

    def seed(self, handoff_ref=HANDOFF) -> None:
        write_full_state(
            self.instance,
            curriculum_refs=[{"type": "curriculum_node", "domain": DOMAIN, "id": f"{DOMAIN}.foundation"}],
            provenance=[{"domain": DOMAIN, "curriculum_version": BASE_VERSION}],
            handoff_ref=handoff_ref,
        )

    def errors(self) -> set[str]:
        return {f.code for f in validate_instance(self.instance, self.core, deployment_binding()) if f.severity == "error"}

    def assert_pass(self) -> None:
        errors = [f.render() for f in validate_instance(self.instance, self.core, deployment_binding()) if f.severity == "error"]
        self.assertEqual([], errors)

    def read_yaml(self, rel: str) -> dict:
        return yaml.safe_load((self.instance / rel).read_text(encoding="utf-8"))

    def mutate_handoff(self, **over) -> None:
        data = self.read_yaml(self.HANDOFF)
        data.update(over)
        write_yaml(self.instance, self.HANDOFF, data)

    def mutate_runtime(self, data: dict) -> None:
        write_yaml(self.instance, self.RUNTIME, data)

    def symlink_or_skip(self, link: Path, target: Path | str, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows host does not grant symbolic-link creation privilege")
            raise

    def test_valid_completed_handoff(self):
        self.seed()
        self.assert_pass()

    def test_valid_pending_successor_handoff_without_n_plus_one_assumption(self):
        self.seed()
        self.mutate_handoff(to_generation=3)
        runtime = self.read_yaml(self.RUNTIME)
        runtime["active_generation"] = 1
        runtime["pending_successor"] = {"generation": 3}
        runtime["generations"] = {"1": {"lifecycle": "handoff_pending", "handoff_ref": self.HANDOFF}}
        self.mutate_runtime(runtime)
        self.assert_pass()

    def test_fail_allowed_prefix_final_symlink_escape(self):
        self.seed()
        target_data = self.read_yaml(self.HANDOFF)
        outside_td = tempfile.TemporaryDirectory(); self.addCleanup(outside_td.cleanup)
        outside = Path(outside_td.name) / "handoff.yaml"
        write_yaml(Path(outside_td.name), "handoff.yaml", target_data)
        link = self.instance / self.HANDOFF
        link.unlink()
        self.symlink_or_skip(link, outside)
        self.assertIn("branch.handoff_ref", self.errors())

    def test_fail_allowed_prefix_directory_component_symlink_escape(self):
        self.seed()
        target_data = self.read_yaml(self.HANDOFF)
        lineage_dir = (self.instance / self.HANDOFF).parent
        (lineage_dir / "C01-to-C02.yaml").unlink()
        lineage_dir.rmdir()
        outside_td = tempfile.TemporaryDirectory(); self.addCleanup(outside_td.cleanup)
        outside = Path(outside_td.name)
        write_yaml(outside, "C01-to-C02.yaml", target_data)
        self.symlink_or_skip(lineage_dir, outside, target_is_directory=True)
        self.assertIn("branch.handoff_ref", self.errors())

    def test_fail_instance_owned_existing_non_handoff_target(self):
        plan = "topics/modern-language-models/plan.yaml"
        self.seed(plan)
        self.assertIn("branch.handoff_ref_target", self.errors())

    def test_fail_wrong_branch(self):
        self.seed()
        self.mutate_handoff(branch_id="other-branch")
        self.assertIn("branch.handoff_ref_identity", self.errors())

    def test_fail_wrong_lineage(self):
        self.seed()
        self.mutate_handoff(lineage_id="other-lineage")
        self.assertIn("branch.handoff_ref_identity", self.errors())

    def test_fail_wrong_topic(self):
        self.seed()
        self.mutate_handoff(topic="other-topic")
        self.assertIn("branch.handoff_ref_identity", self.errors())

    def test_fail_wrong_from_generation(self):
        self.seed()
        self.mutate_handoff(from_generation=2)
        self.assertIn("branch.handoff_ref_identity", self.errors())

    def test_fail_wrong_to_generation(self):
        self.seed()
        self.mutate_handoff(to_generation=3)
        self.assertIn("branch.handoff_ref_identity", self.errors())

    def test_fail_explicit_empty_handoff_ref(self):
        self.seed("")
        self.assertIn("instance.ref_invalid", self.errors())

    def test_fail_missing_handoff_target(self):
        missing = "topics/modern-language-models/subtopics/language-modeling/handoffs/modern-language-models-main-lineage/C09-to-C10.yaml"
        self.seed(missing)
        self.assertIn("branch.handoff_ref", self.errors())

    def test_benign_github_workflow_metadata_is_allowed(self):
        write_file(self.instance, ".github/workflows/validate-instance.yml", "name: validate-instance\non: push\n")
        errors = self.errors()
        self.assertNotIn("instance.core_owned_top", errors)
        self.assertNotIn("instance.top_level", errors)
        self.assertEqual(set(), errors)


class SplitInstanceStorageRegistryTests(unittest.TestCase):
    """R2C conformance to the frozen 28-type / 29-family split registry."""

    SAMPLES = (
        ("config/instance.yaml", "instance_config"),
        ("curriculum/extensions/ext.yaml", "curriculum_extension"),
        ("curriculum/local/local-domain/curriculum.yaml", "curriculum"),
        ("runtime/ui/conversation-sequences.yaml", "conversation_sequence_registry"),
        ("learner/background.yaml", "learner_background"),
        ("learner/model.yaml", "learner_model"),
        ("learner/calibration.yaml", "learner_calibration"),
        ("learner/costs.yaml", "learner_costs"),
        ("learner/execution.yaml", "learner_execution"),
        ("learner/knowledge/domain.yaml", "learner_knowledge"),
        ("topics/topic/goal.yaml", "topic_goal"),
        ("topics/topic/plan.yaml", "topic_plan"),
        ("topics/topic/progress.yaml", "topic_progress"),
        ("topics/topic/deferred.yaml", "topic_deferred"),
        ("topics/topic/subtopics/sub/definition.yaml", "subtopic_definition"),
        ("topics/topic/subtopics/sub/plan.yaml", "subtopic_plan"),
        ("topics/topic/subtopics/sub/progress.yaml", "subtopic_progress"),
        ("execution/weekly/2026-W35.yaml", "weekly_execution"),
        ("topics/topic/execution/daily/2026-08-28.yaml", "daily_execution"),
        ("topics/topic/execution/sessions/s1.yaml", "execution_session"),
        ("topics/topic/coordination/branches.yaml", "branch_registry"),
        ("topics/topic/coordination/branches/main/runtime.yaml", "branch_runtime"),
        ("topics/topic/coordination/branches/main/report.yaml", "branch_report"),
        ("topics/topic/coordination/events/evt.yaml", "coordination_event"),
        ("topics/topic/coordination/topic-report.yaml", "topic_report"),
        ("coordination/hub/runtime.yaml", "hub_runtime"),
        ("topics/topic/handoffs/lineage/C01-to-C02.yaml", "learning_handoff"),
        ("topics/topic/subtopics/sub/handoffs/lineage/C01-to-C02.yaml", "learning_handoff"),
        ("evidence/evi.yaml", "evidence"),
    )

    def setUp(self) -> None:
        core_td = tempfile.TemporaryDirectory()
        inst_td = tempfile.TemporaryDirectory()
        self.addCleanup(core_td.cleanup)
        self.addCleanup(inst_td.cleanup)
        self.core = make_core(Path(core_td.name))
        self.instance = make_instance(Path(inst_td.name))

    def errors(self, instance: Path | None = None) -> set[str]:
        findings = validate_instance(instance or self.instance, self.core, deployment_binding())
        return {f.code for f in findings if f.severity == "error"}

    def assert_pass(self) -> None:
        errors = [f.render() for f in validate_instance(self.instance, self.core, deployment_binding()) if f.severity == "error"]
        self.assertEqual([], errors)

    def test_registry_contract_matches_all_29_r2b_rows(self):
        from scripts.validate_learning_os import (
            INSTANCE_ALL_TYPES,
            INSTANCE_CANONICAL_PATH_RULES,
            INSTANCE_CONTRACT_TYPES,
            INSTANCE_CURRICULUM_TYPES,
            INSTANCE_STATE_TYPES,
            instance_expected_types,
        )
        self.assertEqual(29, len(INSTANCE_CANONICAL_PATH_RULES))
        self.assertEqual(28, len(INSTANCE_ALL_TYPES))
        self.assertEqual(INSTANCE_ALL_TYPES, INSTANCE_STATE_TYPES | INSTANCE_CONTRACT_TYPES | INSTANCE_CURRICULUM_TYPES)
        self.assertEqual(INSTANCE_ALL_TYPES, frozenset(t for _, t in self.SAMPLES))
        for path, expected in self.SAMPLES:
            with self.subTest(path=path):
                self.assertEqual((expected,), instance_expected_types(path))

    def test_r2a_zero_match_known_type_now_fails_closed(self):
        write_yaml(self.instance, "learner/random/foo.yaml", {
            "schema_version": "0.3", "document_type": "learner_model",
        })
        self.assertIn("path.unregistered", self.errors())

    def test_known_type_in_another_registered_family_fails(self):
        write_yaml(self.instance, "learner/model.yaml", {
            "schema_version": "0.3", "document_type": "learner_background",
        })
        self.assertIn("path.document_type", self.errors())

    def test_unknown_type_on_registered_path_stays_unknown(self):
        write_yaml(self.instance, "learner/model.yaml", {
            "schema_version": "0.3", "document_type": "mystery_document",
        })
        errors = self.errors()
        self.assertIn("instance.unknown_document_type", errors)
        self.assertIn("path.document_type", errors)

    def test_allowed_top_level_unregistered_interior_fails(self):
        write_yaml(self.instance, "topics/topic/random.yaml", {
            "schema_version": "0.3", "document_type": "topic_goal",
            "revision": 1, "topic": "topic", "goal": {"statement": "x"},
        })
        self.assertIn("path.unregistered", self.errors())

    def test_nested_evidence_fails_path_registration(self):
        write_yaml(self.instance, "evidence/nested/evi.yaml", {
            "schema_version": "0.3", "document_type": "evidence", "id": "evi",
            "observed_at": "2026-08-28T00:00:00Z", "observation": "x",
            "interpretation": {"direction": "support", "diagnosticity": "low", "novelty": "low", "confidence": "low"},
            "targets": ["x"],
        })
        self.assertIn("path.unregistered", self.errors())

    def test_invalid_top_level_coordination_paths_fail_closed(self):
        invalid = (
            "coordination/foo.yaml",
            "coordination/hub/foo.yaml",
            "coordination/random/runtime.yaml",
            "coordination/hub/nested/runtime.yaml",
        )
        for path in invalid:
            with self.subTest(path=path):
                td = tempfile.TemporaryDirectory()
                self.addCleanup(td.cleanup)
                instance = make_instance(Path(td.name))
                write_yaml(instance, path, {"schema_version": "0.3", "document_type": "hub_runtime"})
                self.assertIn("path.unregistered", self.errors(instance))

    def test_registry_ambiguity_fails_closed_without_first_match(self):
        import re
        from unittest.mock import patch
        duplicate_rules = (
            (re.compile(r"config/instance\.yaml"), "instance_config"),
            (re.compile(r"config/instance\.yaml"), "instance_config"),
        )
        with patch("scripts.validate_learning_os.INSTANCE_CANONICAL_PATH_RULES", duplicate_rules):
            self.assertIn("path.ambiguous", self.errors())

    def test_five_previously_missing_types_pass_on_canonical_paths(self):
        for path, document_type in (
            ("topics/topic/execution/daily/2026-08-28.yaml", "daily_execution"),
            ("topics/topic/coordination/branches/main/report.yaml", "branch_report"),
            ("topics/topic/coordination/events/evt.yaml", "coordination_event"),
            ("coordination/hub/runtime.yaml", "hub_runtime"),
            ("topics/topic/coordination/topic-report.yaml", "topic_report"),
        ):
            doc = {"schema_version": "0.3", "document_type": document_type}
            if document_type != "coordination_event":
                doc["revision"] = 1
            write_yaml(self.instance, path, doc)
        self.assert_pass()

    def test_five_previously_missing_types_fail_off_family(self):
        for path, document_type in (
            ("topics/topic/execution/daily-extra/x.yaml", "daily_execution"),
            ("topics/topic/coordination/reports/main.yaml", "branch_report"),
            ("topics/topic/coordination/event/evt.yaml", "coordination_event"),
            ("coordination/hub/foo.yaml", "hub_runtime"),
            ("topics/topic/coordination/report.yaml", "topic_report"),
        ):
            with self.subTest(document_type=document_type):
                td = tempfile.TemporaryDirectory()
                self.addCleanup(td.cleanup)
                instance = make_instance(Path(td.name))
                write_yaml(instance, path, {"schema_version": "0.3", "document_type": document_type})
                self.assertIn("path.unregistered", self.errors(instance))

    def test_hub_runtime_top_level_coordination_is_narrowly_allowed(self):
        write_yaml(self.instance, "coordination/hub/runtime.yaml", {
            "schema_version": "0.3", "document_type": "hub_runtime",
            "revision": 1,
        })
        self.assert_pass()

    def test_revisioned_family_requires_revision_even_when_other_fields_are_sparse(self):
        write_yaml(self.instance, "learner/execution.yaml", {
            "schema_version": "0.3", "document_type": "learner_execution",
        })
        self.assertIn("revision.invalid", self.errors())

    def test_both_learning_handoff_own_storage_families_pass(self):
        doc = {
            "schema_version": "0.3", "document_type": "learning_handoff",
            "topic": "topic", "branch_id": "branch", "lineage_id": "lineage",
            "from_generation": 1, "to_generation": 2,
        }
        write_yaml(self.instance, "topics/topic/handoffs/lineage/C01-to-C02.yaml", doc)
        write_yaml(self.instance, "topics/topic/subtopics/sub/handoffs/lineage/C02-to-C03.yaml", {
            **doc, "from_generation": 2, "to_generation": 3,
        })
        self.assert_pass()

    def test_flat_evidence_family_passes(self):
        write_yaml(self.instance, "evidence/evi.yaml", {
            "schema_version": "0.3", "document_type": "evidence", "id": "evi",
            "observed_at": "2026-08-28T00:00:00Z", "observation": "x",
            "interpretation": {"direction": "support", "diagnosticity": "low", "novelty": "low", "confidence": "low"},
            "targets": ["x"],
        })
        self.assert_pass()


if __name__ == "__main__":
    unittest.main()
