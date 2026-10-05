from __future__ import annotations

import gc
import shutil
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import yaml

import scripts.runtime_broker as runtime_broker
import scripts.validate_learning_os as validate_learning_os
from scripts.runtime_adapter import (
    CasConflict,
    DeploymentResolver,
    GuardRejected,
    MaterializedRepository,
    ResolutionError,
)
from scripts.runtime_broker import (
    CANDIDATE_YAML_MAX_BYTES,
    CANDIDATE_YAML_MAX_DEPTH,
    CANDIDATE_YAML_MAX_NODES,
    DeploymentWriteGate,
    RuntimeCapabilityPolicy,
    RuntimeSessionBroker,
    _candidate_output_path,
    _preflight_candidate_yaml,
)

ROOT = Path(__file__).resolve().parents[1]
RC_ID, CORE_ID, INSTANCE_ID = 9100000101, 9100000102, 9100000103
CORE_COMMIT = "a" * 40
RC_COMMIT = "b" * 40
INSTANCE_COMMIT = "c" * 40
REGISTRY_PATH = "topics/synthetic/coordination/branches.yaml"
RUNTIME_PATH = "topics/synthetic/coordination/branches/main/runtime.yaml"
HANDOFF_PATH = (
    "topics/synthetic/handoffs/synthetic-main-lineage/C01-to-C02.yaml"
)
HANDOFF_23_PATH = (
    "topics/synthetic/handoffs/synthetic-main-lineage/C02-to-C03.yaml"
)
READ_PATH = "learner/knowledge/synthetic.yaml"
WRITE_PATH = "learner/model.yaml"
READ_V1 = yaml.safe_dump({
    "schema_version": "0.3",
    "document_type": "learner_knowledge",
    "revision": 1,
    "domain": "synthetic",
    "concepts": {},
}, sort_keys=False)
WRITE_V1 = yaml.safe_dump({
    "schema_version": "0.3",
    "document_type": "learner_model",
    "updated_at": "2026-09-29T00:00:00Z",
    "working_style": {},
}, sort_keys=False)
WRITE_V2 = yaml.safe_dump({
    "schema_version": "0.3",
    "document_type": "learner_model",
    "updated_at": "2026-09-29T00:01:00Z",
    "working_style": {},
}, sort_keys=False)

EVIDENCE_ID = "evi-synthetic-reference-journey-001"
EVIDENCE_PATH = f"evidence/{EVIDENCE_ID}.yaml"
EVIDENCE_V1 = yaml.safe_dump({
    "schema_version": "0.3",
    "document_type": "evidence",
    "id": EVIDENCE_ID,
    "observed_at": "2026-10-05T00:00:00Z",
    "observation": "synthetic reference-journey observation",
    "interpretation": {
        "direction": "support",
        "diagnosticity": "low",
        "novelty": "low",
        "confidence": "low",
    },
    "targets": ["modern-language-models.tokens"],
}, sort_keys=False)

KNOWLEDGE_EVIDENCE_ID = "evi-synthetic-knowledge-001"

def typed_evidence(
    *,
    evidence_id=KNOWLEDGE_EVIDENCE_ID,
    domain="synthetic",
    concept="token-identity",
    capability="explanation",
    direction="support",
):
    return yaml.safe_dump({
        "schema_version": "0.3",
        "document_type": "evidence",
        "id": evidence_id,
        "observed_at": "2026-10-05T00:05:00Z",
        "observation": {
            "kind": "synthetic",
            "summary": "synthetic typed capability observation",
        },
        "interpretation": {
            "direction": direction,
            "diagnosticity": "medium",
            "novelty": "medium",
            "confidence": "medium",
        },
        "targets": [{
            "type": "capability",
            "domain": domain,
            "concept": concept,
            "capability": capability,
        }],
    }, sort_keys=False)

def knowledge_candidate(
    *,
    domain="synthetic",
    revision=2,
    evidence_id=KNOWLEDGE_EVIDENCE_ID,
    side="support",
):
    refs = {"support": [], "challenge": []}
    refs[side] = [evidence_id]
    return yaml.safe_dump({
        "schema_version": "0.3",
        "document_type": "learner_knowledge",
        "revision": revision,
        "domain": domain,
        "concepts": {
            "token-identity": {
                "capabilities": {
                    "explanation": {
                        "state": "provisional",
                        "confidence": "low",
                        "evidence_refs": refs,
                        "basis_summary": "synthetic bounded reconciliation",
                    }
                }
            }
        },
    }, sort_keys=False)


def locator():
    return {
        "runtime_control": {
            "repository_id": RC_ID,
            "canonical_ref": "main",
            "contract_path": "deployment.yaml",
        },
        "instance": {"repository_id": INSTANCE_ID, "canonical_ref": "main"},
    }


def contract(*, epoch=1, write_state="active", core_commit=CORE_COMMIT):
    return {
        "schema_version": "0.4",
        "document_type": "deployment_binding",
        "deployment": {
            "id": "dep-broker-test",
            "topology": "split",
            "epoch": epoch,
            "write_state": write_state,
        },
        "core": {"repository_id": CORE_ID, "commit": core_commit},
    }


def branch_registry(*, role="main", lifecycle="active", subtopic=None):
    record = {"role": role, "lifecycle": lifecycle}
    if subtopic is not None:
        record["subtopic"] = subtopic
    return {
        "schema_version": "0.3",
        "document_type": "branch_registry",
        "revision": 1,
        "topic": "synthetic",
        "branches": {"main": record},
    }


def branch_runtime(*, generation=3):
    generations = {
        1: {"lifecycle": "archived"},
        2: {"lifecycle": "archived"},
        generation: {"lifecycle": "active"},
    }
    return {
        "schema_version": "0.3",
        "document_type": "branch_runtime",
        "revision": 1,
        "topic": "synthetic",
        "branch_id": "main",
        "lineage_id": "synthetic-main-lineage",
        "active_generation": generation,
        "pending_successor": None,
        "generations": generations,
    }


def learning_handoff(*, source=2, target=3):
    return {
        "schema_version": "0.3",
        "document_type": "learning_handoff",
        "topic": "synthetic",
        "branch_id": "main",
        "lineage_id": "synthetic-main-lineage",
        "from_generation": source,
        "to_generation": target,
    }


class BrokerProvider:
    def __init__(self, control: Path, instance: Path):
        self.control = control
        self.instance = instance
        self._contract = contract()
        self.control_head = RC_COMMIT
        self.calls = []
        self.instance_head = INSTANCE_COMMIT
        self.advance_on_update = False
        self.promote_after_target_read = False
        self.advance_branch_after_target_read = False
        self.docs = {
            REGISTRY_PATH: yaml.safe_dump(branch_registry(), sort_keys=False),
            RUNTIME_PATH: yaml.safe_dump(branch_runtime(), sort_keys=False),
            READ_PATH: READ_V1,
            WRITE_PATH: WRITE_V1,
        }
        self.blobs = {
            REGISTRY_PATH: "4" * 40,
            RUNTIME_PATH: "d" * 40,
            READ_PATH: "e" * 40,
            WRITE_PATH: "f" * 40,
        }
        self._seed_materialized_instance()

    @property
    def contract(self):
        return self._contract

    @contract.setter
    def contract(self, value):
        self._contract = value
        next_value = (int(self.control_head, 16) + 1) % (1 << 160)
        self.control_head = f"{next_value:040x}"

    def resolve_ref(self, repository_id, ref):
        self.calls.append(("resolve", repository_id, ref))
        if repository_id == RC_ID:
            if ref == "main":
                return self.control_head
            if ref == self.control_head:
                return ref
        if repository_id == INSTANCE_ID:
            if ref == "main":
                return self.instance_head
            if ref == self.instance_head:
                return ref
        if repository_id == CORE_ID and ref == CORE_COMMIT:
            return CORE_COMMIT
        raise ResolutionError("unexpected ref")

    def _seed_materialized_instance(self):
        (self.instance / "config").mkdir(parents=True, exist_ok=True)
        (self.instance / "README.md").write_text(
            "# Synthetic Instance\n", encoding="utf-8"
        )
        (self.instance / "config/instance.yaml").write_text(
            yaml.safe_dump({
                "schema_version": "0.4",
                "document_type": "instance_config",
                "product": {"id": "learning-os"},
                "instance": {"display_timezone": "UTC"},
            }, sort_keys=False),
            encoding="utf-8",
        )

    def materialize(self, repository_id, ref):
        self.calls.append(("materialize", repository_id, ref))
        if repository_id == RC_ID:
            (self.control / "deployment.yaml").write_text(
                yaml.safe_dump(self.contract, sort_keys=False), encoding="utf-8"
            )
            return MaterializedRepository(
                self.control, RC_ID, self.control_head, "synthetic/rc"
            )
        if repository_id == CORE_ID:
            if ref != CORE_COMMIT:
                raise ResolutionError("exact Core unavailable")
            return MaterializedRepository(ROOT, CORE_ID, CORE_COMMIT, "synthetic/core")
        if repository_id == INSTANCE_ID:
            snapshot_docs = {
                path: content
                for path, content in self.docs.items()
                if (
                    path in {READ_PATH, WRITE_PATH, RUNTIME_PATH, REGISTRY_PATH}
                    or path.startswith("evidence/")
                    or "/handoffs/" in path
                    or path.startswith("curriculum/extensions/")
                    or path.startswith("curriculum/local/")
                    or "/execution/daily/" in path
                    or (
                        path.startswith("topics/")
                        and path.endswith("/goal.yaml")
                    )
                )
            }
            for path, content in snapshot_docs.items():
                target = self.instance.joinpath(*Path(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    content, encoding="utf-8", newline="\n"
                )
            return MaterializedRepository(
                self.instance,
                INSTANCE_ID,
                self.instance_head,
                "synthetic/instance",
                tuple(
                    (path, self.blobs[path])
                    for path in snapshot_docs
                    if path in self.blobs
                ),
            )
        raise ResolutionError("unknown repository")

    def read_materialized_text(self, snapshot, path):
        self.calls.append(("snapshot_read", snapshot.repository_id, snapshot.commit_sha, path))
        if snapshot.repository_id != INSTANCE_ID or path not in self.docs:
            raise ResolutionError("unexpected materialized read")
        blob_by_path = dict(snapshot.blob_shas or ())
        if path not in blob_by_path:
            raise ResolutionError("materialized blob identity is unavailable")
        content = self.docs[path]
        blob = blob_by_path[path]
        if path == READ_PATH and self.promote_after_target_read:
            self.promote_after_target_read = False
            self.contract = contract(epoch=2)
        if path == READ_PATH and self.advance_branch_after_target_read:
            self.advance_branch_after_target_read = False
            self.set_generation(4)
        return content, blob

    def release_materialization(self, snapshot):
        self.calls.append((
            "release",
            snapshot.repository_id,
            snapshot.commit_sha,
        ))

    def read_text(self, repository_id, ref, path):
        self.calls.append(("read", repository_id, ref, path))
        if (
            repository_id == RC_ID
            and ref in {"main", self.control_head}
            and path == "deployment.yaml"
        ):
            return (
                yaml.safe_dump(self.contract, sort_keys=False),
                "1" * 40,
                self.control_head,
            )
        if (
            repository_id == CORE_ID
            and ref == CORE_COMMIT
            and path == "config/core.yaml"
        ):
            content = (ROOT / "config/core.yaml").read_text(encoding="utf-8")
            return content, "2" * 40, CORE_COMMIT
        if (
            repository_id == INSTANCE_ID
            and ref in {"main", self.instance_head}
            and path in self.docs
        ):
            content = self.docs[path]
            blob = self.blobs[path]
            head = self.instance_head
            if path == READ_PATH and self.promote_after_target_read:
                self.promote_after_target_read = False
                self.contract = contract(epoch=2)
            if path == READ_PATH and self.advance_branch_after_target_read:
                self.advance_branch_after_target_read = False
                self.set_generation(4)
            return content, blob, head
        raise ResolutionError("unexpected read")

    def update_text(
        self,
        repository_id,
        branch,
        path,
        content,
        expected_blob_sha,
        message,
        expected_ref_sha=None,
    ):
        self.calls.append((
            "update", repository_id, branch, path,
            expected_blob_sha, expected_ref_sha,
        ))
        if repository_id != INSTANCE_ID or branch != "main" or path not in self.docs:
            raise CasConflict("unexpected update target")
        if self.advance_on_update:
            self.advance_on_update = False
            self.set_generation(4)
        if expected_ref_sha is not None and expected_ref_sha != self.instance_head:
            raise CasConflict("branch head compare-and-swap mismatch")
        if expected_blob_sha != self.blobs[path]:
            raise CasConflict("stale blob")
        self.docs[path] = content
        self.blobs[path] = "9" * 40
        self.instance_head = "8" * 40
        return self.instance_head

    def create_text(
        self,
        repository_id,
        branch,
        path,
        content,
        message,
        expected_ref_sha=None,
    ):
        self.calls.append((
            "create", repository_id, branch, path, expected_ref_sha,
        ))
        if repository_id != INSTANCE_ID or branch != "main" or path in self.docs:
            raise CasConflict("unexpected create target")
        if expected_ref_sha is not None and expected_ref_sha != self.instance_head:
            raise CasConflict("branch head compare-and-swap mismatch")
        self.docs[path] = content
        self.blobs[path] = "b" * 40
        self.instance_head = "8" * 40
        return self.instance_head

    def set_pending_successor(
        self,
        *,
        source_generation: int = 2,
        successor_generation: int = 3,
        handoff_path: str = HANDOFF_23_PATH,
    ):
        runtime = branch_runtime(generation=source_generation)
        runtime["revision"] = 4
        runtime["pending_successor"] = successor_generation
        runtime["generations"][source_generation] = {
            "lifecycle": "handoff_pending",
            "handoff_ref": handoff_path,
        }
        self.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.blobs[RUNTIME_PATH] = "d" * 40
        self.docs[handoff_path] = yaml.safe_dump(
            learning_handoff(
                source=source_generation,
                target=successor_generation,
            ),
            sort_keys=False,
        )
        self.blobs[handoff_path] = "a" * 40
        self.instance_head = INSTANCE_COMMIT

    def set_generation(self, generation: int):
        self.docs[RUNTIME_PATH] = yaml.safe_dump(
            branch_runtime(generation=generation), sort_keys=False
        )
        self.blobs[RUNTIME_PATH] = "7" * 40
        self.instance_head = "6" * 40

    def set_branch_registry(
        self, *, role="main", lifecycle="active", subtopic=None
    ):
        self.docs[REGISTRY_PATH] = yaml.safe_dump(
            branch_registry(
                role=role,
                lifecycle=lifecycle,
                subtopic=subtopic,
            ),
            sort_keys=False,
        )
        self.blobs[REGISTRY_PATH] = "5" * 40
        self.instance_head = "6" * 40


class RuntimeSessionBrokerTests(unittest.TestCase):
    def setUp(self):
        rc = tempfile.TemporaryDirectory()
        inst = tempfile.TemporaryDirectory()
        self.addCleanup(rc.cleanup)
        self.addCleanup(inst.cleanup)
        self.provider = BrokerProvider(Path(rc.name), Path(inst.name))
        self.write_gate = DeploymentWriteGate()
        self.broker = RuntimeSessionBroker(
            self.provider,
            locator(),
            write_admission=self.write_gate,
        )
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=("learner/model.yaml",),
        )

    def open(self, **kwargs):
        kwargs.setdefault("expected_generation", 3)
        return self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=self.policy,
            **kwargs,
        )

    def deployed_core_snapshot(self) -> Path:
        tempdir = tempfile.TemporaryDirectory(prefix="synthetic-deployed-core-")
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "config").mkdir(parents=True)
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "config/core.yaml", root / "config/core.yaml")
        for filename in ("validate_learning_os.py", "intake_policy.py"):
            shutil.copy2(ROOT / "scripts" / filename, root / "scripts" / filename)
        return root

    def test_open_rejects_numeric_generation_aliases(self):
        for generation, alias in ((1, True), (1, 1.0), (3, 3.0)):
            with self.subTest(generation=generation, alias=alias):
                self.provider.set_generation(generation)
                with self.assertRaises(ResolutionError):
                    self.open(expected_generation=alias)

    def test_open_rejects_invalid_generation_before_provider_io(self):
        for value in (False, 0, -1, "3", [], {}, 3.0, float("nan")):
            with self.subTest(value=value):
                self.provider.calls.clear()
                with self.assertRaises(ResolutionError):
                    self.open(expected_generation=value)
                self.assertEqual([], self.provider.calls)

    def test_learning_context_reuses_one_authority_snapshot_and_reports_optional_missing(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
        )
        session = self.open()
        self.provider.calls.clear()
        bundle = self.broker.read_learning_context(
            session,
            required_paths=(READ_PATH, WRITE_PATH),
            optional_paths=("learner/execution.yaml",),
        )
        self.assertEqual(
            [READ_PATH, WRITE_PATH],
            [path for path, _ in bundle.documents],
        )
        self.assertEqual(
            ("learner/execution.yaml",),
            bundle.missing_optional,
        )
        materialized = [
            call[1]
            for call in self.provider.calls
            if call[0] == "materialize"
        ]
        self.assertEqual([INSTANCE_ID], materialized)
        snapshot_heads = {
            call[2]
            for call in self.provider.calls
            if call[0] == "snapshot_read"
        }
        self.assertEqual({INSTANCE_COMMIT}, snapshot_heads)

    def test_learning_context_rejects_required_missing_path(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
        )
        session = self.open()
        with self.assertRaisesRegex(
            GuardRejected, "required learning context path is missing"
        ):
            self.broker.read_learning_context(
                session,
                required_paths=("learner/execution.yaml",),
            )

    def test_learning_context_rejects_excess_paths_before_normalizing_entries(self):
        class ExplodingPath:
            def __str__(self):
                raise AssertionError("path normalization must not run")

        self.provider.calls.clear()
        required = [ExplodingPath()] * 33
        with self.assertRaisesRegex(ResolutionError, "path count"):
            self.broker.read_learning_context(
                object(),
                required_paths=required,
            )
        self.assertEqual([], self.provider.calls)

    def test_learning_context_rejects_oversized_paths_before_session_lookup(self):
        self.provider.calls.clear()
        oversized_paths = (
            "a" * 5000,
            "learner/" + ("é" * 128),
        )
        for path in oversized_paths:
            with self.subTest(path_kind=len(path)):
                with self.assertRaisesRegex(ResolutionError, "byte limit"):
                    self.broker.read_learning_context(
                        object(),
                        required_paths=(path,),
                    )
        self.assertEqual([], self.provider.calls)

    def test_snapshot_path_inventory_fails_closed_when_metadata_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            snapshot = MaterializedRepository(
                Path(tempdir),
                INSTANCE_ID,
                INSTANCE_COMMIT,
                "synthetic/instance",
            )
            with self.assertRaisesRegex(GuardRejected, "path inventory"):
                self.broker._snapshot_path_inventory(snapshot)

    def test_learning_context_rejects_duplicate_and_excess_paths_before_io(self):
        self.policy = RuntimeCapabilityPolicy(readable_roots=("learner",))
        session = self.open()
        self.provider.calls.clear()
        with self.assertRaises(ResolutionError):
            self.broker.read_learning_context(
                session,
                required_paths=(READ_PATH,),
                optional_paths=(READ_PATH,),
            )
        self.assertEqual([], self.provider.calls)
        with self.assertRaises(ResolutionError):
            self.broker.read_learning_context(
                session,
                required_paths=tuple(
                    f"learner/knowledge/synthetic-{index}.yaml"
                    for index in range(33)
                ),
            )
        self.assertEqual([], self.provider.calls)

    def test_learning_context_detects_deployment_drift_after_batch_read(self):
        self.policy = RuntimeCapabilityPolicy(readable_roots=("learner",))
        session = self.open()
        self.provider.promote_after_target_read = True
        with self.assertRaises(GuardRejected):
            self.broker.read_learning_context(
                session,
                required_paths=(READ_PATH, WRITE_PATH),
            )

    def test_learning_context_detects_instance_head_drift_after_batch_read(self):
        self.policy = RuntimeCapabilityPolicy(readable_roots=("learner",))
        session = self.open()
        self.provider.advance_branch_after_target_read = True
        with self.assertRaises(GuardRejected):
            self.broker.read_learning_context(
                session,
                required_paths=(READ_PATH, WRITE_PATH),
            )

    def test_create_evidence_is_create_only_and_idempotent_for_same_payload(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = self.broker.create_evidence(
            session,
            content=EVIDENCE_V1,
            message="test: create synthetic evidence",
        )
        self.assertTrue(first.applied)
        self.assertEqual(EVIDENCE_V1, self.provider.docs[EVIDENCE_PATH])
        create_calls = [
            call for call in self.provider.calls if call[0] == "create"
        ]
        self.assertEqual(1, len(create_calls))

        second = self.broker.create_evidence(
            session,
            content=EVIDENCE_V1,
            message="test: retry same synthetic evidence",
        )
        self.assertFalse(second.applied)
        create_calls = [
            call for call in self.provider.calls if call[0] == "create"
        ]
        self.assertEqual(1, len(create_calls))

    def test_create_evidence_rejects_binary_scalar_before_persistence(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        candidate = (
            EVIDENCE_V1
            + "context:\n"
            + "  leaked: !!binary Z2hwX3N5bnRoZXRpY190b2tlbg==\n"
        )
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "tagged scalars are not allowed"
        ):
            self.broker.create_evidence(
                session,
                content=candidate,
                message="test: reject binary scalar Evidence",
            )
        self.assertFalse(any(
            call[0] == "create" for call in self.provider.calls
        ))

    def test_create_evidence_idempotency_handles_huge_hex_integer(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        candidate = (
            EVIDENCE_V1
            + "context:\n"
            + "  huge_int: 0x"
            + ("f" * 4200)
            + "\n"
        )
        first = self.broker.create_evidence(
            session,
            content=candidate,
            message="test: create huge-integer Evidence",
        )
        self.assertTrue(first.applied)
        second = self.broker.create_evidence(
            session,
            content=candidate,
            message="test: retry huge-integer Evidence",
        )
        self.assertFalse(second.applied)

    def test_create_evidence_rejects_tagged_container_before_persistence(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        for tagged_context in (
            "!!set {secret: null}",
            "!!pairs [{secret: value}]",
        ):
            with self.subTest(tagged_context=tagged_context):
                candidate = EVIDENCE_V1 + f"context: {tagged_context}\n"
                self.provider.calls.clear()
                with self.assertRaisesRegex(
                    GuardRejected, "tagged collections are not allowed"
                ):
                    self.broker.create_evidence(
                        session,
                        content=candidate,
                        message="test: reject tagged Evidence container",
                    )
                self.assertFalse(
                    any(call[0] == "create" for call in self.provider.calls)
                )

    def test_create_evidence_idempotency_handles_reordered_large_mapping(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["context"] = {
            f"k{index:04d}": {"value": index, "flag": bool(index % 2)}
            for index in range(1000)
        }
        first_text = yaml.safe_dump(first, sort_keys=False)
        self.broker.create_evidence(
            session,
            content=first_text,
            message="test: create reordered-map Evidence",
        )
        second = dict(first)
        second["context"] = dict(reversed(list(first["context"].items())))
        result = self.broker.create_evidence(
            session,
            content=yaml.safe_dump(second, sort_keys=False),
            message="test: retry reordered-map Evidence",
        )
        self.assertFalse(result.applied)

    def test_create_evidence_idempotency_normalizes_equivalent_aware_timestamps(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["observed_at"] = "2026-10-05T00:00:00Z"
        first_text = yaml.safe_dump(first, sort_keys=False)
        first_text = first_text.replace(
            "'2026-10-05T00:00:00Z'", "2026-10-05T00:00:00Z"
        )
        self.broker.create_evidence(
            session,
            content=first_text,
            message="test: create timestamped Evidence",
        )

        second = yaml.safe_load(first_text)
        second["observed_at"] = "2026-10-04T20:00:00-04:00"
        second_text = yaml.safe_dump(second, sort_keys=False)
        second_text = second_text.replace(
            "'2026-10-04T20:00:00-04:00'",
            "2026-10-04T20:00:00-04:00",
        )
        result = self.broker.create_evidence(
            session,
            content=second_text,
            message="test: retry equivalent timestamp Evidence",
        )
        self.assertFalse(result.applied)

    def test_create_evidence_idempotency_preserves_equal_float_semantics(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["context"] = {"synthetic_float": 0.0}
        first_text = yaml.safe_dump(first, sort_keys=False)
        self.broker.create_evidence(
            session,
            content=first_text,
            message="test: create float Evidence",
        )
        second = yaml.safe_load(first_text)
        second["context"]["synthetic_float"] = -0.0
        result = self.broker.create_evidence(
            session,
            content=yaml.safe_dump(second, sort_keys=False),
            message="test: retry equal float Evidence",
        )
        self.assertFalse(result.applied)

    def test_create_evidence_idempotency_normalizes_equal_float_mapping_keys(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["context"] = {0.0: "zero", -1.0: "negative"}
        first_text = yaml.safe_dump(first, sort_keys=False)
        self.broker.create_evidence(
            session,
            content=first_text,
            message="test: create float-key Evidence",
        )
        second = yaml.safe_load(first_text)
        second["context"] = {-0.0: "zero", -1.0: "negative"}
        result = self.broker.create_evidence(
            session,
            content=yaml.safe_dump(second, sort_keys=False),
            message="test: retry equal float-key Evidence",
        )
        self.assertFalse(result.applied)

    def test_type_sensitive_semantics_distinguish_yaml_pairs_from_plain_lists(self):
        pairs = yaml.safe_load(
            "value: !!pairs\n"
            "- a: b\n"
        )["value"]
        plain = [["a", "b"]]
        self.assertIsInstance(pairs[0], tuple)
        self.assertIsInstance(plain[0], list)
        self.assertFalse(
            self.broker._type_sensitive_semantic_equal(pairs, plain)
        )

    def test_create_evidence_idempotency_handles_boundary_aware_timestamp(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["observed_at"] = "0001-01-01T00:00:00+14:00"
        text = yaml.safe_dump(first, sort_keys=False).replace(
            "'0001-01-01T00:00:00+14:00'",
            "0001-01-01T00:00:00+14:00",
        )
        first_result = self.broker.create_evidence(
            session,
            content=text,
            message="test: create boundary-timestamp Evidence",
        )
        self.assertTrue(first_result.applied)
        second_result = self.broker.create_evidence(
            session,
            content=text,
            message="test: retry boundary-timestamp Evidence",
        )
        self.assertFalse(second_result.applied)

    def test_create_evidence_idempotency_is_type_sensitive(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        first = yaml.safe_load(EVIDENCE_V1)
        first["context"] = {"synthetic_flag": True}
        first_text = yaml.safe_dump(first, sort_keys=False)
        self.broker.create_evidence(
            session,
            content=first_text,
            message="test: create typed synthetic evidence",
        )
        changed = yaml.safe_load(first_text)
        changed["context"]["synthetic_flag"] = 1
        with self.assertRaisesRegex(
            GuardRejected, "already exists with different content"
        ):
            self.broker.create_evidence(
                session,
                content=yaml.safe_dump(changed, sort_keys=False),
                message="test: typed Evidence collision",
            )

    def test_create_evidence_idempotent_retry_still_validates_exact_core_candidate(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        invalid = yaml.safe_load(EVIDENCE_V1)
        invalid.pop("observed_at")
        invalid_text = yaml.safe_dump(invalid, sort_keys=False)
        self.provider.docs[EVIDENCE_PATH] = invalid_text
        self.provider.blobs[EVIDENCE_PATH] = "a" * 40
        self.provider.instance_head = "8" * 40
        with self.assertRaisesRegex(
            GuardRejected, "candidate Instance state failed canonical validation"
        ):
            self.broker.create_evidence(
                session,
                content=invalid_text,
                message="test: invalid existing Evidence must not noop",
            )

    def test_create_evidence_rejects_same_id_with_different_content(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        self.broker.create_evidence(
            session,
            content=EVIDENCE_V1,
            message="test: create synthetic evidence",
        )
        changed = yaml.safe_load(EVIDENCE_V1)
        changed["observation"] = "different synthetic observation"
        with self.assertRaisesRegex(
            GuardRejected, "already exists with different content"
        ):
            self.broker.create_evidence(
                session,
                content=yaml.safe_dump(changed, sort_keys=False),
                message="test: conflicting synthetic evidence",
            )

    def test_create_evidence_requires_evidence_write_capability(self):
        session = self.open()
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "outside the session capability policy"
        ):
            self.broker.create_evidence(
                session,
                content=EVIDENCE_V1,
                message="test: denied synthetic evidence",
            )
        self.assertFalse(
            any(call[0] == "create" for call in self.provider.calls)
        )

    def seed_knowledge_evidence(
        self,
        *,
        domain="synthetic",
        concept="token-identity",
        capability="explanation",
        direction="support",
        evidence_id=KNOWLEDGE_EVIDENCE_ID,
    ):
        path = f"evidence/{evidence_id}.yaml"
        self.provider.docs[path] = typed_evidence(
            evidence_id=evidence_id,
            domain=domain,
            concept=concept,
            capability=capability,
            direction=direction,
        )
        self.provider.blobs[path] = "a" * 40
        return path

    def test_generic_update_rejects_learner_knowledge_without_dedicated_operation(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        with self.assertRaisesRegex(
            GuardRejected, "dedicated Knowledge reconciliation"
        ):
            self.broker.guarded_update(
                session,
                path=READ_PATH,
                content=knowledge_candidate(),
                expected_blob_sha="e" * 40,
                message="test: generic Knowledge write must fail",
            )

    def test_reconcile_knowledge_updates_existing_with_exact_typed_evidence(self):
        self.seed_knowledge_evidence()
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        candidate = knowledge_candidate()
        result = self.broker.reconcile_knowledge(
            session,
            content=candidate,
            expected_blob_sha="e" * 40,
            message="test: reconcile synthetic Knowledge",
        )
        self.assertTrue(result.applied)
        self.assertEqual(candidate, self.provider.docs[READ_PATH])
        self.assertTrue(any(
            call[0] == "update" and call[3] == READ_PATH
            for call in self.provider.calls
        ))

    def test_reconcile_knowledge_can_remove_legacy_duplicate_evidence_refs(self):
        self.seed_knowledge_evidence()
        current = yaml.safe_load(knowledge_candidate(revision=1))
        current_refs = current["concepts"]["token-identity"]["capabilities"][
            "explanation"
        ]["evidence_refs"]["support"]
        current_refs.append(KNOWLEDGE_EVIDENCE_ID)
        self.provider.docs[READ_PATH] = yaml.safe_dump(
            current, sort_keys=False
        )
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        self.provider.calls.clear()
        candidate = knowledge_candidate(revision=2)
        result = self.broker.reconcile_knowledge(
            session,
            content=candidate,
            expected_blob_sha="e" * 40,
            message="test: remove legacy duplicate Knowledge Evidence ref",
        )
        self.assertTrue(result.applied)
        self.assertEqual(candidate, self.provider.docs[READ_PATH])
        self.assertFalse(any(
            call[0] == "snapshot_read" and call[3].startswith("evidence/")
            for call in self.provider.calls
        ))

    def test_reconcile_knowledge_rejects_duplicate_candidate_evidence_refs(self):
        self.seed_knowledge_evidence()
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        candidate = yaml.safe_load(knowledge_candidate())
        candidate["concepts"]["token-identity"]["capabilities"][
            "explanation"
        ]["evidence_refs"]["support"].append(KNOWLEDGE_EVIDENCE_ID)
        with self.assertRaisesRegex(
            GuardRejected, "candidate Knowledge support evidence refs are duplicated"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=yaml.safe_dump(candidate, sort_keys=False),
                expected_blob_sha="e" * 40,
                message="test: reject duplicate candidate Evidence refs",
            )

    def test_reconcile_knowledge_requires_read_capability_for_new_evidence(self):
        self.seed_knowledge_evidence()
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "read capability policy"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=knowledge_candidate(),
                expected_blob_sha="e" * 40,
                message="test: Evidence read capability must be enforced",
            )
        self.assertFalse(any(
            call[0] == "snapshot_read" and call[3].startswith("evidence/")
            for call in self.provider.calls
        ))
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_reconcile_knowledge_bounds_new_evidence_refs_before_evidence_io(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        candidate = yaml.safe_load(knowledge_candidate())
        refs = [
            f"evi-synthetic-many-{index:03d}"
            for index in range(
                runtime_broker.KNOWLEDGE_RECONCILE_MAX_NEW_EVIDENCE_REFS + 1
            )
        ]
        candidate["concepts"]["token-identity"]["capabilities"][
            "explanation"
        ]["evidence_refs"]["support"] = refs
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "too many new Evidence references"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=yaml.safe_dump(candidate, sort_keys=False),
                expected_blob_sha="e" * 40,
                message="test: bound Knowledge Evidence fanout",
            )
        self.assertFalse(any(
            call[0] == "snapshot_read" and call[3].startswith("evidence/")
            for call in self.provider.calls
        ))
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_reconcile_knowledge_rejects_unrelated_new_evidence_target(self):
        self.seed_knowledge_evidence(concept="other-concept")
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        with self.assertRaisesRegex(
            GuardRejected, "does not exactly target"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=knowledge_candidate(),
                expected_blob_sha="e" * 40,
                message="test: reject unrelated Evidence",
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_reconcile_knowledge_rejects_support_direction_mismatch(self):
        self.seed_knowledge_evidence(direction="challenge")
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        with self.assertRaisesRegex(
            GuardRejected, "cannot populate support"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=knowledge_candidate(),
                expected_blob_sha="e" * 40,
                message="test: reject direction mismatch",
            )

    def test_reconcile_knowledge_creates_first_owner_at_revision_one(self):
        self.provider.docs.pop(READ_PATH)
        self.provider.blobs.pop(READ_PATH)
        evidence_id = "evi-synthetic-new-domain-001"
        self.seed_knowledge_evidence(
            domain="new-domain",
            evidence_id=evidence_id,
        )
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=("learner/knowledge",),
        )
        session = self.open()
        candidate = knowledge_candidate(
            domain="new-domain",
            revision=1,
            evidence_id=evidence_id,
        )
        result = self.broker.reconcile_knowledge(
            session,
            content=candidate,
            expected_blob_sha=None,
            message="test: create first synthetic Knowledge owner",
        )
        self.assertTrue(result.applied)
        path = "learner/knowledge/new-domain.yaml"
        self.assertEqual(candidate, self.provider.docs[path])
        self.assertTrue(any(
            call[0] == "create" and call[3] == path
            for call in self.provider.calls
        ))

    def test_reconcile_knowledge_rejects_noninitial_first_revision(self):
        self.provider.docs.pop(READ_PATH)
        self.provider.blobs.pop(READ_PATH)
        evidence_id = "evi-synthetic-new-domain-002"
        self.seed_knowledge_evidence(
            domain="new-domain",
            evidence_id=evidence_id,
        )
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=("learner/knowledge",),
        )
        session = self.open()
        with self.assertRaisesRegex(
            GuardRejected, "must use revision 1"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=knowledge_candidate(
                    domain="new-domain",
                    revision=2,
                    evidence_id=evidence_id,
                ),
                expected_blob_sha=None,
                message="test: reject invalid first Knowledge revision",
            )

    def test_read_only_open_may_omit_expected_generation(self):
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner",)),
        )
        self.assertEqual(3, self.broker._session_state(session).binding.generation)
        self.broker.close_session(session)

    def test_dot_is_not_a_canonical_capability_root(self):
        with self.assertRaises(ResolutionError):
            RuntimeCapabilityPolicy(readable_roots=(".",))

    def test_open_session_binds_active_branch_generation(self):
        session = self.open(expected_generation=3)
        state = self.broker._session_state(session)
        self.assertEqual(3, state.binding.generation)
        self.assertEqual("synthetic", state.binding.topic)
        self.assertEqual("main", state.binding.branch_id)
        self.assertEqual("synthetic-main-lineage", state.binding.lineage_id)
        self.assertEqual("main", state.binding.role)
        self.assertIsNone(state.binding.subtopic)

    def test_resolver_releases_partial_materializations_on_failure(self):
        self.provider.calls.clear()
        self.provider.contract = contract(core_commit="not-an-exact-commit")
        with self.assertRaises(ResolutionError):
            DeploymentResolver(self.provider).resolve(locator())
        releases = [
            call for call in self.provider.calls if call[0] == "release"
        ]
        self.assertEqual(
            [("release", RC_ID, self.provider.control_head)],
            releases,
        )

    def test_open_session_releases_bootstrap_materializations(self):
        self.provider.calls.clear()
        self.open(expected_generation=3)
        released = [
            call[1]
            for call in self.provider.calls
            if call[0] == "release"
        ]
        self.assertEqual([INSTANCE_ID, CORE_ID, RC_ID], released)

    def test_broker_copies_host_trusted_locator_at_construction(self):
        source = locator()
        broker = RuntimeSessionBroker(
            self.provider,
            source,
            write_admission=self.write_gate,
        )
        source["instance"]["repository_id"] = INSTANCE_ID + 99
        session = broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=self.policy,
            expected_generation=3,
        )
        self.assertEqual(
            INSTANCE_ID,
            broker._session_state(session).deployment.instance_repository_id,
        )

    def test_writable_session_requires_shared_admission_gate(self):
        broker = RuntimeSessionBroker(self.provider, locator())
        with self.assertRaisesRegex(GuardRejected, "operation admission"):
            broker.open_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_generation=3,
            )

    def test_read_only_session_requires_shared_admission_gate(self):
        broker = RuntimeSessionBroker(self.provider, locator())
        with self.assertRaisesRegex(GuardRejected, "operation admission"):
            broker.open_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=RuntimeCapabilityPolicy(readable_roots=("learner",)),
            )

    def test_opaque_session_copy_cannot_expand_capabilities(self):
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner",)),
        )
        self.assertFalse(hasattr(session, "policy"))
        self.assertFalse(hasattr(session, "deployment"))
        self.assertFalse(hasattr(session, "binding"))
        copied = replace(session)
        self.provider.calls.clear()
        with self.assertRaisesRegex(GuardRejected, "outside"):
            self.broker.guarded_update(
                copied,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="copied read-only capability must remain read-only",
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_copied_session_handle_survives_original_object_collection(self):
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner",)),
        )
        copied = replace(session)
        del session
        gc.collect()
        result = self.broker.read_instance_text(copied, READ_PATH)
        self.assertEqual(READ_V1, result.content)

    def test_close_session_revokes_all_handle_copies(self):
        session = self.open()
        copied = replace(session)
        self.broker.close_session(session)
        with self.assertRaisesRegex(GuardRejected, "not broker-issued"):
            self.broker.read_instance_text(copied, READ_PATH)
        with self.assertRaisesRegex(GuardRejected, "not broker-issued"):
            self.broker.close_session(copied)

    def test_close_session_drains_inflight_write_before_return(self):
        session = self.open()
        validation_entered = threading.Event()
        release_validation = threading.Event()
        close_done = threading.Event()
        failures = []

        def blocking_validation(*args, **kwargs):
            validation_entered.set()
            if not release_validation.wait(2):
                raise AssertionError("validation release timed out")

        def writer():
            try:
                self.broker.guarded_update(
                    session,
                    path=WRITE_PATH,
                    content=WRITE_V2,
                    expected_blob_sha="f" * 40,
                    message="close must drain this write",
                )
            except Exception as exc:
                failures.append(exc)

        def closer():
            try:
                self.broker.close_session(session)
                close_done.set()
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(
            self.broker, "_validate_candidate", side_effect=blocking_validation
        ):
            writer_thread = threading.Thread(target=writer)
            close_thread = threading.Thread(target=closer)
            writer_thread.start()
            self.assertTrue(validation_entered.wait(1))
            close_thread.start()
            self.assertFalse(close_done.wait(0.05))
            release_validation.set()
            writer_thread.join(2)
            close_thread.join(2)

        self.assertFalse(writer_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertTrue(close_done.is_set())
        self.assertEqual([], failures)
        self.assertEqual(WRITE_V2, self.provider.docs[WRITE_PATH])
        with self.assertRaisesRegex(GuardRejected, "not broker-issued"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_forged_session_handle_is_rejected(self):
        session = self.open()
        forged = replace(session, session_id="forged-session-token")
        with self.assertRaisesRegex(GuardRejected, "not broker-issued"):
            self.broker.read_instance_text(forged, READ_PATH)

    def test_public_freshness_check_does_not_expose_instance_commit(self):
        session = self.open()
        self.assertIsNone(self.broker.assert_current(session))

    def test_session_capability_is_bound_to_issuing_broker(self):
        session = self.open()
        other = RuntimeSessionBroker(
            self.provider,
            locator(),
            write_admission=self.write_gate,
        )
        with self.assertRaisesRegex(GuardRejected, "not broker-issued"):
            other.read_instance_text(session, READ_PATH)

    def test_promotion_barrier_closes_new_write_admissions(self):
        gate = DeploymentWriteGate()
        with gate.promotion_barrier():
            with self.assertRaisesRegex(GuardRejected, "admissions are closed"):
                with gate.write_lease():
                    pass

    def test_promotion_barrier_closes_new_read_admissions(self):
        gate = DeploymentWriteGate()
        with gate.promotion_barrier():
            with self.assertRaisesRegex(GuardRejected, "promotion is in progress"):
                with gate.read_lease():
                    pass

    def test_promotion_barrier_drains_inflight_read(self):
        gate = DeploymentWriteGate()
        holder_ready = threading.Event()
        release_holder = threading.Event()
        promotion_entered = threading.Event()

        def hold_read():
            with gate.read_lease():
                holder_ready.set()
                release_holder.wait(2)

        def promote():
            with gate.promotion_barrier():
                promotion_entered.set()

        reader = threading.Thread(target=hold_read)
        promoter = threading.Thread(target=promote)
        reader.start()
        self.assertTrue(holder_ready.wait(1))
        promoter.start()
        self.assertFalse(promotion_entered.wait(0.05))
        release_holder.set()
        self.assertTrue(promotion_entered.wait(1))
        reader.join(1)
        promoter.join(1)
        self.assertFalse(reader.is_alive())
        self.assertFalse(promoter.is_alive())

    def test_promotion_barrier_drains_inflight_write(self):
        gate = DeploymentWriteGate()
        holder_ready = threading.Event()
        release_holder = threading.Event()
        promotion_entered = threading.Event()

        def hold_write():
            with gate.write_lease():
                holder_ready.set()
                release_holder.wait(2)

        def promote():
            with gate.promotion_barrier():
                promotion_entered.set()

        writer = threading.Thread(target=hold_write)
        promoter = threading.Thread(target=promote)
        writer.start()
        self.assertTrue(holder_ready.wait(1))
        promoter.start()
        self.assertFalse(promotion_entered.wait(0.05))
        release_holder.set()
        self.assertTrue(promotion_entered.wait(1))
        writer.join(1)
        promoter.join(1)
        self.assertFalse(writer.is_alive())
        self.assertFalse(promoter.is_alive())

    def test_open_session_rejects_wrong_expected_generation(self):
        with self.assertRaisesRegex(GuardRejected, "not active"):
            self.open(expected_generation=2)

    def test_normal_open_session_stays_fail_closed_while_handoff_pending(self):
        self.provider.set_pending_successor()
        with self.assertRaisesRegex(
            ResolutionError, "active generation is not active"
        ):
            self.broker.open_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_generation=2,
            )

    def test_claim_successor_session_claims_pending_generation(self):
        self.provider.set_pending_successor()
        session = self.broker.claim_successor_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=self.policy,
            expected_successor_generation=3,
        )
        state = self.broker._session_state(session)
        self.assertEqual(3, state.binding.generation)

        runtime = yaml.safe_load(self.provider.docs[RUNTIME_PATH])
        self.assertEqual(5, runtime["revision"])
        self.assertEqual(3, runtime["active_generation"])
        self.assertIsNone(runtime["pending_successor"])
        self.assertEqual("archived", runtime["generations"][2]["lifecycle"])
        self.assertEqual("active", runtime["generations"][3]["lifecycle"])

        updates = [
            call for call in self.provider.calls if call[0] == "update"
        ]
        self.assertEqual(INSTANCE_COMMIT, updates[0][-1])

        result = self.broker.guarded_update(
            session,
            path=WRITE_PATH,
            content=WRITE_V2,
            expected_blob_sha="f" * 40,
            message="successor session write",
        )
        self.assertTrue(result.applied)

    def test_claim_successor_accepts_mapping_and_nonconsecutive_generation(self):
        self.provider.set_pending_successor(
            source_generation=2,
            successor_generation=5,
        )
        runtime = yaml.safe_load(self.provider.docs[RUNTIME_PATH])
        runtime["pending_successor"] = {"generation": 5}
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        session = self.broker.claim_successor_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=self.policy,
            expected_successor_generation=5,
        )
        state = self.broker._session_state(session)
        self.assertEqual(5, state.binding.generation)
        claimed = yaml.safe_load(self.provider.docs[RUNTIME_PATH])
        self.assertEqual(5, claimed["active_generation"])
        self.assertEqual("active", claimed["generations"][5]["lifecycle"])

    def test_claim_successor_rejects_branch_without_pending_handoff(self):
        with self.assertRaisesRegex(ResolutionError, "pending successor"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=4,
            )

    def test_claim_successor_rejects_wrong_pending_generation(self):
        self.provider.set_pending_successor()
        with self.assertRaisesRegex(GuardRejected, "not pending"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=4,
            )

    def test_claim_successor_requires_canonical_handoff_ref(self):
        self.provider.set_pending_successor()
        runtime = yaml.safe_load(self.provider.docs[RUNTIME_PATH])
        runtime["generations"][2].pop("handoff_ref")
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "learning handoff"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=3,
            )

    def test_claim_successor_rejects_handoff_target_mismatch(self):
        self.provider.set_pending_successor()
        self.provider.docs[HANDOFF_23_PATH] = yaml.safe_dump(
            learning_handoff(source=2, target=4),
            sort_keys=False,
        )
        with self.assertRaisesRegex(ResolutionError, "handoff_ref_identity"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=3,
            )

    def test_claim_successor_fails_closed_on_branch_head_cas_race(self):
        self.provider.set_pending_successor()
        self.provider.advance_on_update = True
        with self.assertRaisesRegex(CasConflict, "branch head"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=3,
            )

    def test_claim_successor_requires_active_deployment(self):
        self.provider.set_pending_successor()
        self.provider.contract = contract(write_state="frozen")
        with self.assertRaisesRegex(GuardRejected, "not active"):
            self.broker.claim_successor_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_successor_generation=3,
            )

    def test_claim_successor_does_not_issue_stale_handle_after_readback_drift(self):
        self.provider.set_pending_successor()
        original = self.broker._read_branch_runtime

        def drifted_readback(**kwargs):
            runtime, head = original(**kwargs)
            runtime["active_generation"] = 4
            runtime["generations"][3]["lifecycle"] = "archived"
            runtime["generations"][4] = {"lifecycle": "active"}
            return runtime, head

        with mock.patch.object(
            self.broker,
            "_read_branch_runtime",
            side_effect=drifted_readback,
        ):
            with self.assertRaisesRegex(
                GuardRejected, "did not become canonical"
            ):
                self.broker.claim_successor_session(
                    branch_runtime_path=RUNTIME_PATH,
                    policy=self.policy,
                    expected_successor_generation=3,
                )
        self.assertEqual({}, self.broker._issued_sessions)

    def test_open_session_requires_active_branch_registry_lifecycle(self):
        for lifecycle in ("idle", "retired"):
            with self.subTest(lifecycle=lifecycle):
                self.provider.set_branch_registry(lifecycle=lifecycle)
                with self.assertRaisesRegex(ResolutionError, "lifecycle is not active"):
                    self.open(expected_generation=3)
                self.provider.set_branch_registry(lifecycle="active")

    def test_open_session_requires_canonical_branch_runtime_path(self):
        with self.assertRaisesRegex(ResolutionError, "not canonical"):
            self.broker.open_session(
                branch_runtime_path="learner/model.yaml",
                policy=self.policy,
                expected_generation=3,
            )

    def test_open_session_rejects_runtime_identity_path_mismatch(self):
        other_path = (
            "topics/synthetic/coordination/branches/other/runtime.yaml"
        )
        self.provider.docs[other_path] = yaml.safe_dump(
            branch_runtime(), sort_keys=False
        )
        self.provider.blobs[other_path] = "5" * 40
        with self.assertRaisesRegex(ResolutionError, "identity"):
            self.broker.open_session(
                branch_runtime_path=other_path,
                policy=self.policy,
                expected_generation=3,
            )

    def test_writable_session_requires_established_generation(self):
        with self.assertRaisesRegex(GuardRejected, "established generation"):
            self.broker.open_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
            )

    def test_read_only_session_may_bind_current_generation(self):
        policy = RuntimeCapabilityPolicy(readable_roots=("learner",))
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
        )
        self.assertEqual(
            3, self.broker._session_state(session).binding.generation
        )
        with self.assertRaisesRegex(GuardRejected, "outside"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must remain read-only",
            )

    def test_capability_policy_rejects_write_outside_read_scope(self):
        with self.assertRaisesRegex(ResolutionError, "writable root"):
            RuntimeCapabilityPolicy(
                readable_roots=("learner/knowledge.yaml",),
                writable_roots=("other/state.txt",),
            )

    def test_repository_paths_reject_single_backslashes(self):
        with self.assertRaisesRegex(ResolutionError, "canonical relative"):
            RuntimeCapabilityPolicy(
                readable_roots=(r"learner\model.yaml",),
            )
        session = self.open()
        with self.assertRaisesRegex(ResolutionError, "canonical relative"):
            self.broker.guarded_update(
                session,
                path=r"learner\model.yaml",
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must fail",
            )

    def test_read_is_scoped_and_returns_target_blob(self):
        session = self.open()
        result = self.broker.read_instance_text(session, READ_PATH)
        self.assertEqual(READ_V1, result.content)
        self.assertEqual("e" * 40, result.version_token)
        self.assertFalse(hasattr(result, "commit_sha"))
        with self.assertRaisesRegex(GuardRejected, "outside"):
            self.broker.read_instance_text(
                session, "topics/synthetic/progress.yaml"
            )

    def test_generation_change_blocks_subsequent_read(self):
        session = self.open()
        self.provider.set_generation(4)
        with self.assertRaisesRegex(GuardRejected, "generation"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_promotion_during_target_read_discards_result(self):
        session = self.open()
        self.provider.promote_after_target_read = True
        with self.assertRaisesRegex(GuardRejected, "epoch"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_branch_advance_during_target_read_discards_result(self):
        session = self.open()
        self.provider.advance_branch_after_target_read = True
        with self.assertRaisesRegex(
            GuardRejected, "generation|authority head"
        ):
            self.broker.read_instance_text(session, READ_PATH)

    def test_promotion_during_final_branch_validation_discards_result(self):
        session = self.open()
        original_snapshot_read = self.provider.read_materialized_text
        original_resolve = self.provider.resolve_ref
        target_read = False

        def racing_snapshot_read(snapshot, path):
            nonlocal target_read
            result = original_snapshot_read(snapshot, path)
            if path == READ_PATH:
                target_read = True
            return result

        def racing_resolve(repository_id, ref):
            nonlocal target_read
            result = original_resolve(repository_id, ref)
            if repository_id == INSTANCE_ID and target_read:
                # Simulate Runtime-Control promotion after final Instance
                # authority validation but before the final control-head check.
                target_read = False
                self.provider.contract = contract(epoch=2)
            return result

        self.provider.read_materialized_text = racing_snapshot_read
        self.provider.resolve_ref = racing_resolve
        with self.assertRaisesRegex(GuardRejected, "epoch"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_instance_advance_during_final_handoff_read_discards_result(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.provider.docs[HANDOFF_PATH] = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "learning_handoff",
            "topic": "synthetic",
            "branch_id": "main",
            "lineage_id": "synthetic-main-lineage",
            "from_generation": 1,
            "to_generation": 2,
        }, sort_keys=False)
        self.provider.blobs[HANDOFF_PATH] = "3" * 40
        original_snapshot_read = self.provider.read_materialized_text
        original_resolve = self.provider.resolve_ref
        target_read = False

        def racing_snapshot_read(snapshot, path):
            nonlocal target_read
            result = original_snapshot_read(snapshot, path)
            if path == READ_PATH:
                target_read = True
            return result

        def racing_resolve(repository_id, ref):
            nonlocal target_read
            if repository_id == INSTANCE_ID and target_read:
                target_read = False
                self.provider.set_generation(4)
            return original_resolve(repository_id, ref)

        self.provider.read_materialized_text = racing_snapshot_read
        self.provider.resolve_ref = racing_resolve
        with self.assertRaisesRegex(
            GuardRejected,
            "generation|authority head changed|Branch registry fresh-read failed closed",
        ):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_rejects_duplicate_keys(self):
        session = self.open()
        self.provider.docs[RUNTIME_PATH] = (
            yaml.safe_dump(branch_runtime(), sort_keys=False)
            + "active_generation: 4\n"
        )
        with self.assertRaisesRegex(
            GuardRejected, "duplicate mapping key"
        ):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_rejects_yaml_alias_graphs(self):
        session = self.open()
        self.provider.docs[RUNTIME_PATH] = (
            yaml.safe_dump(branch_runtime(), sort_keys=False)
            + "cycle: &cycle [*cycle]\n"
        )
        with self.assertRaisesRegex(GuardRejected, "aliases and anchors"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_registry_rejects_yaml_alias_graphs(self):
        session = self.open()
        self.provider.docs[REGISTRY_PATH] = (
            yaml.safe_dump(branch_registry(), sort_keys=False)
            + "cycle: &cycle [*cycle]\n"
        )
        with self.assertRaisesRegex(GuardRejected, "aliases and anchors"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_handoff_rejects_yaml_alias_graphs(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.provider.docs[HANDOFF_PATH] = (
            yaml.safe_dump({
                "schema_version": "0.3",
                "document_type": "learning_handoff",
                "topic": "synthetic",
                "branch_id": "main",
                "lineage_id": "synthetic-main-lineage",
                "from_generation": 1,
                "to_generation": 2,
            }, sort_keys=False)
            + "cycle: &cycle [*cycle]\n"
        )
        self.provider.blobs[HANDOFF_PATH] = "3" * 40
        with self.assertRaisesRegex(GuardRejected, "aliases and anchors"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_forbidden_keys_fail_closed(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["repository_id"] = INSTANCE_ID
        runtime["generations"][3]["token"] = "ghp_" + "a" * 36
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "trust boundary"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_branch_runtime_handoff_state_blocks_subsequent_read(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][3]["lifecycle"] = "handoff_pending"
        runtime["pending_successor"] = {"generation": 4}
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "fresh-read failed closed"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_rejects_missing_handoff_ref_target(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "handoff_ref"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_rejects_mismatched_handoff_identity(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.provider.docs[HANDOFF_PATH] = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "learning_handoff",
            "topic": "synthetic",
            "branch_id": "main",
            "lineage_id": "wrong-lineage",
            "from_generation": 1,
            "to_generation": 2,
        }, sort_keys=False)
        self.provider.blobs[HANDOFF_PATH] = "3" * 40
        with self.assertRaisesRegex(GuardRejected, "handoff_ref identity"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_rejects_handoff_lineage_path_mismatch(self):
        session = self.open()
        wrong_path = (
            "topics/synthetic/handoffs/other-lineage/C01-to-C02.yaml"
        )
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = wrong_path
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.provider.docs[wrong_path] = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "learning_handoff",
            "topic": "synthetic",
            "branch_id": "main",
            "lineage_id": "synthetic-main-lineage",
            "from_generation": 1,
            "to_generation": 2,
        }, sort_keys=False)
        self.provider.blobs[wrong_path] = "3" * 40
        with self.assertRaisesRegex(GuardRejected, "handoff_ref identity"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_fresh_branch_runtime_accepts_valid_archived_handoff_ref(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        self.provider.docs[HANDOFF_PATH] = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "learning_handoff",
            "topic": "synthetic",
            "branch_id": "main",
            "lineage_id": "synthetic-main-lineage",
            "from_generation": 1,
            "to_generation": 2,
        }, sort_keys=False)
        self.provider.blobs[HANDOFF_PATH] = "3" * 40
        result = self.broker.read_instance_text(session, READ_PATH)
        self.assertEqual(READ_V1, result.content)

    def test_handoff_history_uses_one_exact_head_materialization(self):
        second_handoff = (
            "topics/synthetic/handoffs/synthetic-main-lineage/"
            "C02-to-C03.yaml"
        )
        runtime = branch_runtime()
        runtime["generations"][1]["handoff_ref"] = HANDOFF_PATH
        runtime["generations"][2]["handoff_ref"] = second_handoff
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        for path, from_generation, to_generation in (
            (HANDOFF_PATH, 1, 2),
            (second_handoff, 2, 3),
        ):
            self.provider.docs[path] = yaml.safe_dump({
                "schema_version": "0.3",
                "document_type": "learning_handoff",
                "topic": "synthetic",
                "branch_id": "main",
                "lineage_id": "synthetic-main-lineage",
                "from_generation": from_generation,
                "to_generation": to_generation,
            }, sort_keys=False)
            self.provider.blobs[path] = "3" * 40

        self.provider.calls.clear()
        parsed, head = self.broker._read_branch_runtime(
            instance_repository_id=INSTANCE_ID,
            instance_ref="main",
            runtime_path=RUNTIME_PATH,
        )
        self.assertEqual(3, parsed["active_generation"])
        self.assertEqual(INSTANCE_COMMIT, head)
        handoff_reads = [
            call for call in self.provider.calls
            if call[0] == "read" and call[3] in {HANDOFF_PATH, second_handoff}
        ]
        self.assertEqual([], handoff_reads)
        instance_materializations = [
            call for call in self.provider.calls
            if call[0] == "materialize" and call[1] == INSTANCE_ID
        ]
        self.assertEqual(1, len(instance_materializations))

    def test_branch_runtime_schema_and_required_fields_fail_closed(self):
        session = self.open()
        cases = []
        unsupported = branch_runtime()
        unsupported["schema_version"] = "9.9"
        cases.append(unsupported)
        missing = branch_runtime()
        missing.pop("revision")
        cases.append(missing)
        for runtime in cases:
            with self.subTest(runtime=runtime):
                self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
                    runtime, sort_keys=False
                )
                with self.assertRaisesRegex(
                    GuardRejected, "fresh-read failed closed"
                ):
                    self.broker.read_instance_text(session, READ_PATH)

    def test_pending_successor_with_active_source_fails_closed(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["pending_successor"] = {"generation": 4}
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "fresh-read failed closed"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_second_active_generation_fails_closed(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][4] = {"lifecycle": "active"}
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "exactly one active"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_unsupported_generation_lifecycle_fails_closed(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"][1]["lifecycle"] = "corrupt"
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "lifecycle"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_invalid_archived_generation_key_fails_closed(self):
        session = self.open()
        runtime = branch_runtime()
        runtime["generations"]["legacy"] = {"lifecycle": "archived"}
        self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(
            runtime, sort_keys=False
        )
        with self.assertRaisesRegex(GuardRejected, "generation key"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_deployment_change_blocks_subsequent_read(self):
        session = self.open()
        self.provider.contract = contract(epoch=2)
        with self.assertRaisesRegex(GuardRejected, "epoch"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_read_only_session_remains_available_while_writes_are_frozen(self):
        self.provider.contract = contract(write_state="frozen")
        policy = RuntimeCapabilityPolicy(readable_roots=("learner",))
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
        )
        result = self.broker.read_instance_text(session, READ_PATH)
        self.assertEqual(READ_V1, result.content)

    def test_generic_write_cannot_mutate_branch_runtime_authority(self):
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=("topics/synthetic",),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        for runtime_path in (
            RUNTIME_PATH,
            "topics/synthetic/coordination/branches/other/runtime.yaml",
        ):
            with self.subTest(runtime_path=runtime_path):
                with self.assertRaisesRegex(GuardRejected, "Branch runtime"):
                    self.broker.guarded_update(
                        session,
                        path=runtime_path,
                        content="x",
                        expected_blob_sha="d" * 40,
                        message="must fail",
                    )

    def test_write_lease_covers_validation_and_provider_update(self):
        session = self.open()
        observed = []
        original_validate = self.broker._validate_candidate
        original_update = self.provider.update_text

        def validating(*args, **kwargs):
            observed.append(("validate", self.write_gate._holders))
            return original_validate(*args, **kwargs)

        def updating(*args, **kwargs):
            observed.append(("update", self.write_gate._holders))
            return original_update(*args, **kwargs)

        self.broker._validate_candidate = validating
        self.provider.update_text = updating
        self.broker.guarded_update(
            session,
            path=WRITE_PATH,
            content=WRITE_V2,
            expected_blob_sha="f" * 40,
            message="test shared write lease",
        )
        self.assertIn(("validate", 1), observed)
        self.assertIn(("update", 1), observed)

    def test_deployment_change_during_validation_blocks_final_write(self):
        session = self.open()
        original_validate = self.broker._validate_candidate

        def validating(*args, **kwargs):
            original_validate(*args, **kwargs)
            self.provider.contract = contract(epoch=2)

        self.broker._validate_candidate = validating
        with self.assertRaisesRegex(GuardRejected, "epoch"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must fail after deployment drift",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_generation_change_blocks_write_before_target_cas(self):
        session = self.open()
        self.provider.set_generation(4)
        with self.assertRaisesRegex(GuardRejected, "generation"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="test stale session",
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_generic_update_rejects_topic_route_progress_without_hub_transition(self):
        path = "topics/synthetic/progress.yaml"
        self.provider.docs[path] = "revision: 1\n"
        self.provider.blobs[path] = "2" * 40
        self.provider.set_branch_registry(role="practice", subtopic="unit")
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "Hub-class transition"):
            self.broker.guarded_update(
                session,
                path=path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="topic route requires Hub-class transition",
            )

    def test_generic_update_rejects_weekly_execution_without_hub_transition(self):
        path = "execution/weekly/2026-W40.yaml"
        self.provider.docs[path] = "revision: 1\n"
        self.provider.blobs[path] = "2" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("execution",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "Hub-class transition"):
            self.broker.guarded_update(
                session,
                path=path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="weekly planning requires Hub-class transition",
            )

    def test_main_branch_may_replace_bound_subtopic_progress(self):
        path = "topics/synthetic/subtopics/unit/progress.yaml"
        content = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "subtopic_progress",
            "revision": 1,
            "topic": "synthetic",
            "subtopic": "unit",
            "plan_revision": 1,
            "milestones": {},
        }, sort_keys=False)
        self.provider.docs[path] = content
        self.provider.blobs[path] = "2" * 40
        self.provider.set_branch_registry(role="main", subtopic="unit")
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with mock.patch.object(
            self.broker, "_validate_candidate", return_value=None
        ):
            result = self.broker.guarded_update(
                session,
                path=path,
                content=content,
                expected_blob_sha="2" * 40,
                message="advance bound subtopic progress",
            )
        self.assertTrue(result.applied)

    def test_practice_branch_cannot_replace_main_subtopic_progress(self):
        path = "topics/synthetic/subtopics/unit/progress.yaml"
        self.provider.docs[path] = "revision: 1\n"
        self.provider.blobs[path] = "2" * 40
        self.provider.set_branch_registry(role="practice", subtopic="unit")
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "cannot replace subtopic_progress"):
            self.broker.guarded_update(
                session,
                path=path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="must not move Main route position",
            )

    def test_main_branch_cannot_replace_other_subtopic_progress(self):
        path = "topics/synthetic/subtopics/other/progress.yaml"
        self.provider.docs[path] = "revision: 1\n"
        self.provider.blobs[path] = "2" * 40
        self.provider.set_branch_registry(role="main", subtopic="unit")
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "outside the bound Subtopic"):
            self.broker.guarded_update(
                session,
                path=path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="must stay within bound subtopic",
            )

    def test_branch_may_replace_only_its_bound_branch_report(self):
        own_path = "topics/synthetic/coordination/branches/main/report.yaml"
        other_path = "topics/synthetic/coordination/branches/other/report.yaml"
        self.provider.set_branch_registry(role="practice")
        for path in (own_path, other_path):
            self.provider.docs[path] = "revision: 1\n"
            self.provider.blobs[path] = "2" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=("topics/synthetic",),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )

        with mock.patch.object(
            self.broker, "_validate_candidate", return_value=None
        ):
            result = self.broker.guarded_update(
                session,
                path=own_path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="update own Branch report",
            )
        self.assertTrue(result.applied)

        self.provider.docs[other_path] = "revision: 1\n"
        self.provider.blobs[other_path] = "2" * 40
        with self.assertRaisesRegex(GuardRejected, "outside the bound Branch"):
            self.broker.guarded_update(
                session,
                path=other_path,
                content="revision: 2\n",
                expected_blob_sha="2" * 40,
                message="must not spoof another Branch report",
            )

    def test_branch_registry_role_change_blocks_existing_session(self):
        session = self.open()
        self.provider.set_branch_registry(role="practice")
        with self.assertRaisesRegex(GuardRejected, "role changed"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_generic_update_rejects_immutable_append_families(self):
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic", "evidence"),
            writable_roots=("topics/synthetic", "evidence"),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        immutable_paths = (
            "evidence/ev001.yaml",
            "topics/synthetic/execution/sessions/ses001.yaml",
            "topics/synthetic/coordination/events/event001.yaml",
            (
                "topics/synthetic/handoffs/synthetic-main-lineage/"
                "C03-to-C04.yaml"
            ),
        )
        for immutable_path in immutable_paths:
            with self.subTest(path=immutable_path):
                with self.assertRaisesRegex(
                    GuardRejected, "immutable/create-only"
                ):
                    self.broker.guarded_update(
                        session,
                        path=immutable_path,
                        content="x",
                        expected_blob_sha="f" * 40,
                        message="must not overwrite history",
                    )

    def test_generic_update_rejects_global_hub_runtime(self):
        path = "coordination/hub/runtime.yaml"
        policy = RuntimeCapabilityPolicy(
            readable_roots=("coordination",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "Global Hub runtime"):
            self.broker.guarded_update(
                session,
                path=path,
                content="x",
                expected_blob_sha="0" * 40,
                message="must reject Branch write to Global Hub runtime",
            )

    def test_generic_update_rejects_protocol_governed_branch_registry(self):
        path = "topics/synthetic/coordination/branches.yaml"
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(
            GuardRejected, "dedicated transition"
        ):
            self.broker.guarded_update(
                session,
                path=path,
                content="x",
                expected_blob_sha="4" * 40,
                message="must use Hub-owned Branch transition",
            )

    def test_generic_update_rejects_protocol_governed_sequence_registry(self):
        path = "runtime/ui/conversation-sequences.yaml"
        policy = RuntimeCapabilityPolicy(
            readable_roots=("runtime/ui",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(
            GuardRejected, "dedicated transition"
        ):
            self.broker.guarded_update(
                session,
                path=path,
                content="x",
                expected_blob_sha="4" * 40,
                message="must use allocation transaction",
            )

    def test_generic_update_rejects_unclassified_paths(self):
        policy = RuntimeCapabilityPolicy(
            readable_roots=("scratch",),
            writable_roots=("scratch",),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "unclassified"):
            self.broker.guarded_update(
                session,
                path="scratch/state.txt",
                content="x",
                expected_blob_sha="f" * 40,
                message="must fail closed",
            )

    def test_generic_update_uses_split_instance_path_registry(self):
        path = "config/instance.yaml"
        content = yaml.safe_dump({
            "schema_version": "0.4",
            "document_type": "instance_config",
            "product": {"id": "learning-os"},
            "instance": {"display_timezone": "UTC"},
        }, sort_keys=False)
        self.provider.docs[path] = content
        self.provider.blobs[path] = "4" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("config",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        with mock.patch.object(
            self.broker, "_validate_candidate", return_value=None
        ):
            result = self.broker.guarded_update(
                session,
                path=path,
                content=content,
                expected_blob_sha="4" * 40,
                message="test split registry",
            )
        self.assertTrue(result.applied)

        legacy_policy = RuntimeCapabilityPolicy(
            readable_roots=("config",),
            writable_roots=("config/project.yaml",),
        )
        legacy_session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=legacy_policy,
            expected_generation=3,
        )
        with self.assertRaisesRegex(GuardRejected, "unclassified"):
            self.broker.guarded_update(
                legacy_session,
                path="config/project.yaml",
                content="x",
                expected_blob_sha="5" * 40,
                message="legacy path must fail closed",
            )

    def test_candidate_topic_identity_must_match_canonical_path(self):
        path = "topics/synthetic/goal.yaml"
        current = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "topic_goal",
            "revision": 1,
            "topic": "synthetic",
            "goal": {"statement": "synthetic"},
        }, sort_keys=False)
        self.provider.docs[path] = current
        self.provider.blobs[path] = "6" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        candidate = yaml.safe_load(current)
        candidate["revision"] = 2
        candidate["topic"] = "physics"
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "canonical validation"
        ):
            self.broker.guarded_update(
                session,
                path=path,
                content=yaml.safe_dump(candidate, sort_keys=False),
                expected_blob_sha="6" * 40,
                message="must bind candidate topic to canonical path",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_knowledge_candidate_domain_must_form_canonical_filename(self):
        candidate = yaml.safe_load(READ_V1)
        candidate["domain"] = "physics/escape"
        policy = RuntimeCapabilityPolicy(
            readable_roots=("learner/knowledge",),
            writable_roots=("learner/knowledge",),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "canonical Knowledge path"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=yaml.safe_dump(candidate, sort_keys=False),
                expected_blob_sha=None,
                message="must derive one canonical Knowledge filename",
            )
        self.assertFalse(
            any(call[0] in {"update", "create"} for call in self.provider.calls)
        )

    def test_branch_head_advance_after_generation_check_blocks_write(self):
        session = self.open()
        self.provider.advance_on_update = True
        with self.assertRaisesRegex(CasConflict, "branch head"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="test concurrent handoff",
            )
        self.assertEqual(WRITE_V1, self.provider.docs[WRITE_PATH])

    def test_candidate_output_path_rejects_windows_escape_and_device_forms(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with mock.patch("scripts.runtime_broker.os.name", "nt"):
                for path in (
                    "learner/knowledge/D:outside.yaml",
                    "learner/knowledge/state.yaml:ads",
                    "learner/knowledge/CON.yaml",
                    "learner/knowledge/name. ",
                ):
                    with self.subTest(path=path):
                        with self.assertRaisesRegex(GuardRejected, "unsafe on Windows"):
                            _candidate_output_path(root, path)

    def test_candidate_output_path_remains_under_snapshot_root(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            candidate = _candidate_output_path(
                root, "learner/knowledge/synthetic.yaml"
            )
            candidate.relative_to(root.resolve(strict=True))

    def test_candidate_yaml_preflight_rejects_aliases_and_anchors(self):
        with self.assertRaisesRegex(GuardRejected, "aliases and anchors"):
            _preflight_candidate_yaml(
                "root: &root [value]\ncopy: *root\n"
            )

    def test_candidate_yaml_preflight_enforces_resource_limits(self):
        with self.assertRaisesRegex(GuardRejected, "byte limit"):
            _preflight_candidate_yaml(
                "value: " + ("x" * CANDIDATE_YAML_MAX_BYTES)
            )
        deep = (
            "value: "
            + ("[" * (CANDIDATE_YAML_MAX_DEPTH + 1))
            + "0"
            + ("]" * (CANDIDATE_YAML_MAX_DEPTH + 1))
        )
        with self.assertRaisesRegex(GuardRejected, "nesting-depth"):
            _preflight_candidate_yaml(deep)
        many_nodes = "\n".join(
            f"k{index}: v"
            for index in range(CANDIDATE_YAML_MAX_NODES + 1)
        )
        with self.assertRaisesRegex(GuardRejected, "node limit"):
            _preflight_candidate_yaml(many_nodes)

    def test_candidate_validation_uses_deployed_core_validator(self):
        session = self.open()
        core_td = tempfile.TemporaryDirectory()
        self.addCleanup(core_td.cleanup)
        core_root = Path(core_td.name)
        validator_path = (
            core_root / "scripts" / "validate_learning_os.py"
        )
        validator_path.parent.mkdir(parents=True)
        validator_path.write_text(
            "# synthetic pinned validator\n", encoding="utf-8"
        )

        original_materialize = self.provider.materialize
        calls = []

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root,
                    CORE_ID,
                    CORE_COMMIT,
                    "synthetic/core",
                )
            return original_materialize(repository_id, ref)

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        self.provider.materialize = materialize
        with mock.patch(
            "scripts.runtime_broker.subprocess.run", side_effect=run
        ):
            self.broker._validate_candidate(
                self.broker._session_state(session),
                authority_head=self.provider.instance_head,
                path=WRITE_PATH,
                content=WRITE_V2,
                contract=self.provider.contract,
            )
        self.assertEqual(str(validator_path), calls[0][0][1])
        self.assertEqual(core_root, calls[0][1]["cwd"])
        self.assertEqual(subprocess.DEVNULL, calls[0][1]["stdout"])
        self.assertEqual(subprocess.DEVNULL, calls[0][1]["stderr"])
        self.assertNotIn("capture_output", calls[0][1])

    def test_candidate_validator_core_provenance_must_match_deployment(self):
        session = self.open()
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    ROOT,
                    CORE_ID,
                    "0" * 40,
                    "synthetic/core",
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        with self.assertRaisesRegex(
            GuardRejected, "candidate Core validator provenance"
        ):
            self.broker._validate_candidate(
                self.broker._session_state(session),
                authority_head=self.provider.instance_head,
                path=WRITE_PATH,
                content=WRITE_V2,
                contract=self.provider.contract,
            )

    def test_pinned_authority_unexpected_exception_releases_snapshot(self):
        session = self.open()
        self.provider.calls.clear()

        def unexpected_registry_failure(**kwargs):
            raise RuntimeError("synthetic unexpected registry failure")

        with mock.patch.object(
            self.broker,
            "_read_branch_registry_from_snapshot",
            side_effect=unexpected_registry_failure,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "unexpected registry failure"
            ):
                self.broker._pin_instance_authority(
                    self.broker._session_state(session)
                )

        materialized = [
            call[1]
            for call in self.provider.calls
            if call[0] == "materialize"
        ]
        released = [
            call[1]
            for call in self.provider.calls
            if call[0] == "release"
        ]
        self.assertEqual([INSTANCE_ID], materialized)
        self.assertEqual([INSTANCE_ID], released)

    def test_candidate_cleanup_attempts_all_releases_when_one_fails(self):
        session = self.open()
        released = []
        original_release = self.provider.release_materialization

        def flaky_release(snapshot):
            released.append(snapshot.repository_id)
            if snapshot.repository_id == CORE_ID:
                raise RuntimeError("synthetic release failure")
            original_release(snapshot)

        self.provider.release_materialization = flaky_release
        with mock.patch(
            "scripts.runtime_broker.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["validator"], 0, "", ""
            ),
        ):
            with self.assertRaisesRegex(
                GuardRejected, "materialization cleanup failed"
            ):
                self.broker._validate_candidate(
                    self.broker._session_state(session),
                    authority_head=self.provider.instance_head,
                    path=WRITE_PATH,
                    content=WRITE_V2,
                    contract=self.provider.contract,
                )
        self.assertEqual([CORE_ID, INSTANCE_ID], released)

    def test_candidate_validation_timeout_fails_before_provider_update(self):
        session = self.open()
        self.provider.calls.clear()
        real_run = subprocess.run

        def timeout_candidate_only(command, **kwargs):
            if "--write-policy-fingerprint" in command:
                return real_run(command, **kwargs)
            raise subprocess.TimeoutExpired(cmd=command, timeout=10)

        with mock.patch(
            "scripts.runtime_broker.subprocess.run",
            side_effect=timeout_candidate_only,
        ):
            with self.assertRaisesRegex(GuardRejected, "timed out"):
                self.broker.guarded_update(
                    session,
                    path=WRITE_PATH,
                    content=WRITE_V2,
                    expected_blob_sha="f" * 40,
                    message="must time out safely",
                )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )
        released = [
            call[1]
            for call in self.provider.calls
            if call[0] == "release"
        ]
        self.assertEqual([INSTANCE_ID, CORE_ID], released)

    def test_revisioned_replacement_rejects_non_advancing_revision_before_cas(self):
        path = READ_PATH
        current = yaml.safe_load(READ_V1)
        current["revision"] = 2
        self.provider.docs[path] = yaml.safe_dump(
            current, sort_keys=False
        )
        self.provider.blobs[path] = "e" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        for candidate_revision in (2, 1):
            with self.subTest(candidate_revision=candidate_revision):
                candidate = dict(current)
                candidate["revision"] = candidate_revision
                self.provider.calls.clear()
                with self.assertRaisesRegex(
                    GuardRejected, "advance semantic revision"
                ):
                    self.broker.reconcile_knowledge(
                        session,
                        content=yaml.safe_dump(
                            candidate, sort_keys=False
                        ),
                        expected_blob_sha="e" * 40,
                        message="must reject revision rollback",
                    )
                self.assertFalse(
                    any(call[0] == "update" for call in self.provider.calls)
                )

    def test_curriculum_extension_requires_extension_revision_advance(self):
        path = "curriculum/extensions/runtime-broker-test.yaml"
        current = yaml.safe_dump({
            "schema_version": "0.4",
            "document_type": "curriculum_extension",
            "domain": "modern-language-models",
            "base_version": "0.2",
            "extension_revision": 5,
            "nodes": {},
        }, sort_keys=False)
        self.provider.docs[path] = current
        self.provider.blobs[path] = "a" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("curriculum/extensions",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        for candidate_revision in (5, 4):
            with self.subTest(candidate_revision=candidate_revision):
                candidate = yaml.safe_load(current)
                candidate["extension_revision"] = candidate_revision
                candidate["nodes"] = {
                    "language_modeling.runtime_broker_test": {
                        "title": "Changed without semantic version advance",
                        "kind": "concept",
                    }
                }
                self.provider.calls.clear()
                with self.assertRaisesRegex(
                    GuardRejected, "advance semantic extension_revision"
                ):
                    self.broker.guarded_update(
                        session,
                        path=path,
                        content=yaml.safe_dump(
                            candidate, sort_keys=False
                        ),
                        expected_blob_sha="a" * 40,
                        message="must reject extension revision rollback",
                    )
                self.assertFalse(
                    any(call[0] == "update" for call in self.provider.calls)
                )

    def test_locked_daily_baseline_preserves_objective_membership(self):
        path = "topics/synthetic/execution/daily/2026-09-30.yaml"
        current = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "daily_execution",
            "revision": 1,
            "topic": "synthetic",
            "baseline_locked": True,
            "baseline_objectives": [
                {"id": "objective-a", "status": "planned"},
            ],
        }, sort_keys=False)
        self.provider.docs[path] = current
        self.provider.blobs[path] = "7" * 40
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic/execution",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )

        for mutation in ("add", "replace", "unlock"):
            with self.subTest(mutation=mutation):
                candidate = yaml.safe_load(current)
                candidate["revision"] = 2
                if mutation == "add":
                    candidate["baseline_objectives"].append(
                        {"id": "objective-b", "status": "planned"}
                    )
                elif mutation == "replace":
                    candidate["baseline_objectives"] = [
                        {"id": "objective-b", "status": "planned"}
                    ]
                else:
                    candidate["baseline_locked"] = False
                self.provider.calls.clear()
                with self.assertRaisesRegex(
                    GuardRejected, "locked Daily baseline"
                ):
                    self.broker.guarded_update(
                        session,
                        path=path,
                        content=yaml.safe_dump(
                            candidate, sort_keys=False
                        ),
                        expected_blob_sha="7" * 40,
                        message="must preserve locked Daily denominator",
                    )
                self.assertFalse(
                    any(call[0] == "update" for call in self.provider.calls)
                )

        candidate = yaml.safe_load(current)
        candidate["revision"] = 2
        candidate["baseline_objectives"][0]["status"] = "completed"
        result = self.broker.guarded_update(
            session,
            path=path,
            content=yaml.safe_dump(candidate, sort_keys=False),
            expected_blob_sha="7" * 40,
            message="allow status change within locked Daily baseline",
        )
        self.assertTrue(result.applied)

    def test_local_curriculum_requires_dedicated_multi_document_transition(self):
        path = "curriculum/local/runtime-broker-local/curriculum.yaml"
        policy = RuntimeCapabilityPolicy(
            readable_roots=("curriculum/local",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        self.provider.calls.clear()
        with self.assertRaisesRegex(
            GuardRejected, "dedicated transition operation for curriculum"
        ):
            self.broker.guarded_update(
                session,
                path=path,
                content="not-even-parsed: true\n",
                expected_blob_sha="b" * 40,
                message="must route curriculum changes through owning transition",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_revisioned_candidate_requires_positive_integer_revision(self):
        path = READ_PATH
        policy = RuntimeCapabilityPolicy(
            readable_roots=("learner",),
            writable_roots=(path,),
        )
        session = self.broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=policy,
            expected_generation=3,
        )
        invalid = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "learner_knowledge",
            "revision": "broken",
            "domain": "synthetic",
            "concepts": {},
        }, sort_keys=False)
        with self.assertRaisesRegex(
            GuardRejected, "revisioned replacement revision is invalid"
        ):
            self.broker.reconcile_knowledge(
                session,
                content=invalid,
                expected_blob_sha="e" * 40,
                message="must reject malformed revision",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_deployed_write_policy_fingerprint_output_is_bounded(self):
        session = self.open()
        core_root = self.deployed_core_snapshot()
        validator_path = core_root / "scripts" / "validate_learning_os.py"
        validator_path.write_text(
            "import sys\n"
            "if '--write-policy-fingerprint' in sys.argv:\n"
            "    sys.stdout.write('a' * 4096)\n",
            encoding="utf-8",
            newline="\n",
        )
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        with self.assertRaisesRegex(
            GuardRejected, "write policy output exceeded Runtime budget"
        ):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must bound fingerprint output",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )
        self.assertEqual(
            {},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_deployed_write_policy_manifest_mismatch_fails_before_update(self):
        session = self.open()
        core_root = self.deployed_core_snapshot()
        data = yaml.safe_load(
            (core_root / "config/core.yaml").read_text(encoding="utf-8")
        )
        data["manifest"]["runtime_session_write_policy_fingerprint"] = "0" * 64
        (core_root / "config/core.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n"
        )
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        with self.assertRaisesRegex(GuardRejected, "manifest write policy"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must fail on stale broker policy declaration",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )
        self.assertEqual(
            {},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_deployed_write_policy_is_computed_from_exact_core_code(self):
        session = self.open()
        core_root = self.deployed_core_snapshot()
        validator_path = core_root / "scripts/validate_learning_os.py"
        source = validator_path.read_text(encoding="utf-8")
        old = '"subtopic_progress": {\n        "roles": ("main",),'
        new = '"subtopic_progress": {\n        "roles": ("practice",),'
        self.assertIn(old, source)
        validator_path.write_text(
            source.replace(old, new, 1), encoding="utf-8", newline="\n"
        )
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        with self.assertRaisesRegex(
            GuardRejected, "manifest write policy does not match"
        ):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must compute exact deployed Core policy",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_deployed_core_policy_verification_is_cached_but_host_policy_rechecked(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        core = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        expected = runtime_broker.instance_write_policy_fingerprint()
        real_compute = runtime_broker._compute_bounded_write_policy_fingerprint
        real_host = runtime_broker.instance_write_policy_fingerprint

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            wraps=real_compute,
        ) as compute_policy, mock.patch.object(
            runtime_broker,
            "instance_write_policy_fingerprint",
            wraps=real_host,
        ) as host_policy:
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=core
            )
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=core
            )

        self.assertEqual(1, compute_policy.call_count)
        self.assertEqual(2, host_policy.call_count)
        self.assertEqual(
            {(CORE_ID, CORE_COMMIT): expected},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_malformed_core_policy_fingerprint_does_not_populate_cache(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        core = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )

        for malformed in ("abc", "g" * 64):
            self.broker._verified_core_write_policy_fingerprints.clear()
            with mock.patch.object(
                runtime_broker,
                "_compute_bounded_write_policy_fingerprint",
                return_value=malformed,
            ):
                with self.assertRaisesRegex(
                    GuardRejected,
                    "write policy computation failed closed",
                ):
                    self.broker._verified_deployed_core_write_policy_fingerprint(
                        state, core_snapshot=core
                    )
            self.assertEqual(
                {},
                self.broker._verified_core_write_policy_fingerprints,
            )

    def test_warm_core_policy_cache_hit_without_snapshot_does_not_materialize(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        core = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        expected = runtime_broker.instance_write_policy_fingerprint()

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ) as compute_policy:
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=core
            )
            original_materialize = self.provider.materialize

            def reject_core_materialize(repository_id, ref):
                if repository_id == CORE_ID:
                    raise AssertionError("warm policy cache must not materialize Core")
                return original_materialize(repository_id, ref)

            self.provider.materialize = reject_core_materialize
            self.broker._assert_deployed_write_policy_compatible(state)

        self.assertEqual(1, compute_policy.call_count)

    def test_concurrent_core_policy_cache_miss_computes_once(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        core = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        expected = runtime_broker.instance_write_policy_fingerprint()
        barrier = threading.Barrier(6)
        errors = []

        def worker():
            try:
                barrier.wait()
                self.broker._assert_deployed_write_policy_compatible(
                    state, core_snapshot=core
                )
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ) as compute_policy:
            threads = [
                threading.Thread(target=worker)
                for _ in range(6)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([], errors)
        self.assertEqual(1, compute_policy.call_count)
        self.assertEqual(
            {(CORE_ID, CORE_COMMIT): expected},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_cached_core_policy_still_fails_on_host_policy_drift(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        core = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        expected = runtime_broker.instance_write_policy_fingerprint()

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ) as compute_policy, mock.patch.object(
            runtime_broker,
            "instance_write_policy_fingerprint",
            side_effect=[expected, "0" * 64],
        ) as host_policy:
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=core
            )
            with self.assertRaisesRegex(
                GuardRejected,
                "Runtime broker write policy does not match",
            ):
                self.broker._assert_deployed_write_policy_compatible(
                    state, core_snapshot=core
                )

        self.assertEqual(1, compute_policy.call_count)
        self.assertEqual(2, host_policy.call_count)

    def test_create_evidence_requires_deployed_core_operation_fingerprint(self):
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("evidence", "learner"),
            writable_roots=("evidence",),
        )
        session = self.open()
        with mock.patch.object(
            validate_learning_os,
            "INSTANCE_DEDICATED_RUNTIME_OPERATIONS",
            {},
        ):
            legacy_fingerprint = (
                validate_learning_os.instance_write_policy_fingerprint()
            )
        current_fingerprint = (
            validate_learning_os.instance_write_policy_fingerprint()
        )
        self.assertNotEqual(legacy_fingerprint, current_fingerprint)

        core_root = self.deployed_core_snapshot()
        core_config_path = core_root / "config/core.yaml"
        core_config = yaml.safe_load(
            core_config_path.read_text(encoding="utf-8")
        )
        core_config["manifest"][
            "runtime_session_write_policy_fingerprint"
        ] = legacy_fingerprint
        core_config_path.write_text(
            yaml.safe_dump(core_config, sort_keys=False),
            encoding="utf-8",
        )
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        self.provider.calls.clear()
        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=legacy_fingerprint,
        ):
            with self.assertRaisesRegex(
                GuardRejected,
                "does not match the exact deployed Core",
            ):
                self.broker.create_evidence(
                    session,
                    content=EVIDENCE_V1,
                    message="test: old Core cannot authorize create Evidence",
                )
        self.assertFalse(
            any(call[0] == "create" for call in self.provider.calls)
        )

    def test_reconcile_knowledge_requires_deployed_core_operation_fingerprint(self):
        self.seed_knowledge_evidence()
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("learner", "evidence"),
            writable_roots=(READ_PATH,),
        )
        session = self.open()
        with mock.patch.object(
            validate_learning_os,
            "INSTANCE_DEDICATED_RUNTIME_OPERATIONS",
            {"create_evidence": "v1"},
        ):
            legacy_fingerprint = (
                validate_learning_os.instance_write_policy_fingerprint()
            )
        current_fingerprint = (
            validate_learning_os.instance_write_policy_fingerprint()
        )
        self.assertNotEqual(legacy_fingerprint, current_fingerprint)

        core_root = self.deployed_core_snapshot()
        core_config_path = core_root / "config/core.yaml"
        core_config = yaml.safe_load(
            core_config_path.read_text(encoding="utf-8")
        )
        core_config["manifest"][
            "runtime_session_write_policy_fingerprint"
        ] = legacy_fingerprint
        core_config_path.write_text(
            yaml.safe_dump(core_config, sort_keys=False),
            encoding="utf-8",
        )
        original_materialize = self.provider.materialize

        def materialize(repository_id, ref):
            if repository_id == CORE_ID:
                return MaterializedRepository(
                    core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
                )
            return original_materialize(repository_id, ref)

        self.provider.materialize = materialize
        self.provider.calls.clear()
        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=legacy_fingerprint,
        ):
            with self.assertRaisesRegex(
                GuardRejected,
                "does not match the exact deployed Core",
            ):
                self.broker.reconcile_knowledge(
                    session,
                    content=knowledge_candidate(),
                    expected_blob_sha="e" * 40,
                    message="test: old Core cannot authorize Knowledge reconcile",
                )
        self.assertFalse(
            any(call[0] in {"update", "create"} for call in self.provider.calls)
        )

    def test_deployed_core_policy_cache_is_keyed_by_exact_core_identity(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        expected = runtime_broker.instance_write_policy_fingerprint()
        next_commit = "b" * 40
        next_state = replace(
            state,
            deployment=replace(
                state.deployment,
                core_commit=next_commit,
            ),
        )
        first = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        second = MaterializedRepository(
            core_root, CORE_ID, next_commit, "synthetic/core"
        )

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ) as compute_policy:
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=first
            )
            self.broker._assert_deployed_write_policy_compatible(
                next_state, core_snapshot=second
            )

        self.assertEqual(2, compute_policy.call_count)
        self.assertEqual(
            {(CORE_ID, next_commit): expected},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_failed_core_policy_cleanup_does_not_populate_cache(self):
        session = self.open()
        state = self.broker._session_state(session)
        expected = runtime_broker.instance_write_policy_fingerprint()
        original_release = self.provider.release_materialization
        failed_once = False

        def flaky_release(snapshot):
            nonlocal failed_once
            if snapshot.repository_id == CORE_ID and not failed_once:
                failed_once = True
                raise RuntimeError("synthetic Core cleanup failure")
            return original_release(snapshot)

        self.provider.release_materialization = flaky_release
        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ) as compute_policy:
            with self.assertRaisesRegex(
                GuardRejected,
                "materialization cleanup failed",
            ):
                self.broker._verified_deployed_core_write_policy_fingerprint(
                    state
                )
            self.assertEqual(
                {},
                self.broker._verified_core_write_policy_fingerprints,
            )
            self.broker._verified_deployed_core_write_policy_fingerprint(
                state
            )

        self.assertEqual(2, compute_policy.call_count)
        self.assertEqual(
            {(CORE_ID, CORE_COMMIT): expected},
            self.broker._verified_core_write_policy_fingerprints,
        )

    def test_cached_core_policy_does_not_bypass_snapshot_provenance(self):
        session = self.open()
        state = self.broker._session_state(session)
        core_root = self.deployed_core_snapshot()
        expected = runtime_broker.instance_write_policy_fingerprint()
        correct = MaterializedRepository(
            core_root, CORE_ID, CORE_COMMIT, "synthetic/core"
        )
        wrong_commit = MaterializedRepository(
            core_root, CORE_ID, "b" * 40, "synthetic/core"
        )
        wrong_repository = MaterializedRepository(
            core_root, CORE_ID + 1, CORE_COMMIT, "synthetic/core"
        )

        with mock.patch.object(
            runtime_broker,
            "_compute_bounded_write_policy_fingerprint",
            return_value=expected,
        ):
            self.broker._assert_deployed_write_policy_compatible(
                state, core_snapshot=correct
            )
            for wrong in (wrong_commit, wrong_repository):
                with self.assertRaisesRegex(
                    GuardRejected,
                    "provenance changed",
                ):
                    self.broker._assert_deployed_write_policy_compatible(
                        state, core_snapshot=wrong
                    )

    def test_invalid_candidate_state_is_rejected_before_provider_update(self):
        session = self.open()
        invalid = yaml.safe_dump({
            "schema_version": "0.3",
            "document_type": "topic_goal",
            "revision": 1,
            "topic": "synthetic",
            "goal": {},
        }, sort_keys=False)
        with self.assertRaisesRegex(GuardRejected, "canonical validation"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=invalid,
                expected_blob_sha="f" * 40,
                message="must reject invalid candidate",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_provider_without_exact_head_cas_fails_write_closed(self):
        session = self.open()
        previous_head = self.provider.instance_head

        def unsupported(*args, **kwargs):
            self.assertEqual(previous_head, kwargs.get("expected_ref_sha"))
            raise CasConflict("exact branch-head CAS is unsupported")

        self.provider.update_text = unsupported
        with self.assertRaisesRegex(CasConflict, "unsupported"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="test unsupported exact head provider",
            )
        self.assertEqual(WRITE_V1, self.provider.docs[WRITE_PATH])

    def test_prewrite_cleanup_failure_blocks_canonical_update(self):
        session = self.open()
        self.provider.calls.clear()
        original_release = self.provider.release_materialization
        failed_once = False

        def flaky_release(snapshot):
            nonlocal failed_once
            if snapshot.repository_id == INSTANCE_ID and not failed_once:
                failed_once = True
                raise RuntimeError("synthetic prewrite cleanup failure")
            original_release(snapshot)

        self.provider.release_materialization = flaky_release
        with self.assertRaisesRegex(
            GuardRejected, "materialization cleanup failed"
        ):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="cleanup failure must reject before update",
            )

        self.assertEqual(WRITE_V1, self.provider.docs[WRITE_PATH])
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_allowed_write_uses_deployment_generation_branch_and_target_cas(self):
        session = self.open()
        self.provider.calls.clear()
        previous_head = self.provider.instance_head
        result = self.broker.guarded_update(
            session,
            path=WRITE_PATH,
            content=WRITE_V2,
            expected_blob_sha="f" * 40,
            message="test broker write",
        )
        self.assertTrue(result.applied)
        self.assertFalse(hasattr(result, "commit_sha"))
        self.assertFalse(hasattr(result, "sha"))
        self.assertEqual(WRITE_V2, self.provider.docs[WRITE_PATH])
        update = next(
            call for call in self.provider.calls if call[0] == "update"
        )
        self.assertEqual(previous_head, update[5])
        names = [call[0] for call in self.provider.calls]
        self.assertLess(names.index("read"), names.index("update"))
        materialized = [
            call[1]
            for call in self.provider.calls
            if call[0] == "materialize"
        ]
        self.assertEqual([INSTANCE_ID, CORE_ID], materialized)
        released = [
            call[1]
            for call in self.provider.calls
            if call[0] == "release"
        ]
        self.assertEqual([INSTANCE_ID, CORE_ID], released)

    def test_read_reuses_one_exact_instance_authority_snapshot(self):
        session = self.open()
        self.provider.calls.clear()
        result = self.broker.read_instance_text(session, READ_PATH)
        self.assertEqual(READ_V1, result.content)
        materialized = [
            call[1]
            for call in self.provider.calls
            if call[0] == "materialize"
        ]
        self.assertEqual([INSTANCE_ID], materialized)
        self.assertEqual(
            2,
            sum(
                1
                for call in self.provider.calls
                if call[:3] == ("resolve", INSTANCE_ID, "main")
            ),
        )
        self.assertEqual(
            1,
            sum(
                1
                for call in self.provider.calls
                if call[0] == "read"
                and call[1] == RC_ID
                and call[3] == "deployment.yaml"
            ),
        )
        self.assertFalse(
            any(
                call[0] == "read" and call[1] == INSTANCE_ID
                for call in self.provider.calls
            )
        )
        self.assertEqual(
            1,
            sum(
                1
                for call in self.provider.calls
                if call[:4]
                == ("snapshot_read", INSTANCE_ID, INSTANCE_COMMIT, READ_PATH)
            ),
        )


if __name__ == "__main__":
    unittest.main()
