from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import yaml

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
    _preflight_candidate_yaml,
)

ROOT = Path(__file__).resolve().parents[1]
RC_ID, CORE_ID, INSTANCE_ID = 9100000101, 9100000102, 9100000103
CORE_COMMIT = "a" * 40
RC_COMMIT = "b" * 40
INSTANCE_COMMIT = "c" * 40
RUNTIME_PATH = "topics/synthetic/coordination/branches/main/runtime.yaml"
HANDOFF_PATH = (
    "topics/synthetic/handoffs/synthetic-main-lineage/C01-to-C02.yaml"
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


class BrokerProvider:
    def __init__(self, control: Path, instance: Path):
        self.control = control
        self.instance = instance
        self.contract = contract()
        self.calls = []
        self.instance_head = INSTANCE_COMMIT
        self.advance_on_update = False
        self.promote_after_target_read = False
        self.advance_branch_after_target_read = False
        self.docs = {
            RUNTIME_PATH: yaml.safe_dump(branch_runtime(), sort_keys=False),
            READ_PATH: READ_V1,
            WRITE_PATH: WRITE_V1,
        }
        self.blobs = {
            RUNTIME_PATH: "d" * 40,
            READ_PATH: "e" * 40,
            WRITE_PATH: "f" * 40,
        }
        self._seed_materialized_instance()

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
            return MaterializedRepository(self.control, RC_ID, RC_COMMIT, "synthetic/rc")
        if repository_id == CORE_ID:
            if ref != CORE_COMMIT:
                raise ResolutionError("exact Core unavailable")
            return MaterializedRepository(ROOT, CORE_ID, CORE_COMMIT, "synthetic/core")
        if repository_id == INSTANCE_ID:
            for path in (READ_PATH, WRITE_PATH):
                target = self.instance.joinpath(*Path(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    self.docs[path], encoding="utf-8", newline="\n"
                )
            return MaterializedRepository(
                self.instance, INSTANCE_ID, self.instance_head, "synthetic/instance"
            )
        raise ResolutionError("unknown repository")

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
            and ref in {"main", RC_COMMIT}
            and path == "deployment.yaml"
        ):
            return yaml.safe_dump(self.contract, sort_keys=False), "1" * 40, RC_COMMIT
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

    def set_generation(self, generation: int):
        self.docs[RUNTIME_PATH] = yaml.safe_dump(
            branch_runtime(generation=generation), sort_keys=False
        )
        self.blobs[RUNTIME_PATH] = "7" * 40
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

    def test_open_session_binds_active_branch_generation(self):
        session = self.open(expected_generation=3)
        self.assertEqual(3, session.binding.generation)
        self.assertEqual("synthetic", session.binding.topic)
        self.assertEqual("main", session.binding.branch_id)
        self.assertEqual("synthetic-main-lineage", session.binding.lineage_id)

    def test_resolver_releases_partial_materializations_on_failure(self):
        self.provider.calls.clear()
        self.provider.contract = contract(core_commit="not-an-exact-commit")
        with self.assertRaises(ResolutionError):
            DeploymentResolver(self.provider).resolve(locator())
        releases = [
            call for call in self.provider.calls if call[0] == "release"
        ]
        self.assertEqual(
            [("release", RC_ID, RC_COMMIT)],
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
        self.assertEqual(INSTANCE_ID, session.deployment.instance_repository_id)

    def test_writable_session_requires_shared_admission_gate(self):
        broker = RuntimeSessionBroker(self.provider, locator())
        with self.assertRaisesRegex(GuardRejected, "write admission"):
            broker.open_session(
                branch_runtime_path=RUNTIME_PATH,
                policy=self.policy,
                expected_generation=3,
            )

    def test_read_only_session_does_not_require_admission_gate(self):
        broker = RuntimeSessionBroker(self.provider, locator())
        session = broker.open_session(
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner",)),
        )
        self.assertEqual(3, session.binding.generation)

    def test_promotion_barrier_closes_new_write_admissions(self):
        gate = DeploymentWriteGate()
        with gate.promotion_barrier():
            with self.assertRaisesRegex(GuardRejected, "admissions are closed"):
                with gate.write_lease():
                    pass

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
        self.assertEqual(3, session.binding.generation)
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
        original_read = self.provider.read_text
        target_read = False

        def racing_read(repository_id, ref, path):
            nonlocal target_read
            result = original_read(repository_id, ref, path)
            if path == READ_PATH:
                target_read = True
            elif path == RUNTIME_PATH and target_read:
                # Simulate Runtime-Control promotion immediately after the
                # final Branch authority/handoff validation read completes.
                self.provider.contract = contract(epoch=2)
            return result

        self.provider.read_text = racing_read
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
        original_read = self.provider.read_text
        target_read = False

        def racing_read(repository_id, ref, path):
            nonlocal target_read
            result = original_read(repository_id, ref, path)
            if path == READ_PATH:
                target_read = True
            elif path == HANDOFF_PATH and target_read:
                self.provider.set_generation(4)
            return result

        self.provider.read_text = racing_read
        with self.assertRaisesRegex(
            GuardRejected, "authority head changed"
        ):
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
                session,
                authority_head=self.provider.instance_head,
                path=WRITE_PATH,
                content=WRITE_V2,
                contract=self.provider.contract,
            )
        self.assertEqual(str(validator_path), calls[0][0][1])
        self.assertEqual(core_root, calls[0][1]["cwd"])

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
                    session,
                    authority_head=self.provider.instance_head,
                    path=WRITE_PATH,
                    content=WRITE_V2,
                    contract=self.provider.contract,
                )
        self.assertEqual([CORE_ID, INSTANCE_ID], released)

    def test_candidate_validation_timeout_fails_before_provider_update(self):
        session = self.open()
        self.provider.calls.clear()
        with mock.patch(
            "scripts.runtime_broker.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["validator"], timeout=10
            ),
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
        self.assertEqual([CORE_ID, INSTANCE_ID], released)

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
        with self.assertRaisesRegex(GuardRejected, "canonical validation"):
            self.broker.guarded_update(
                session,
                path=path,
                content=invalid,
                expected_blob_sha="e" * 40,
                message="must reject malformed revision",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
        )

    def test_deployed_write_policy_mismatch_fails_before_update(self):
        session = self.open()
        original = self.provider.read_text

        def stale_policy(repository_id, ref, path):
            if (
                repository_id == CORE_ID
                and ref == CORE_COMMIT
                and path == "config/core.yaml"
            ):
                data = yaml.safe_load(
                    (ROOT / "config/core.yaml").read_text(encoding="utf-8")
                )
                data["manifest"]["runtime_session_write_policy_fingerprint"] = (
                    "0" * 64
                )
                return yaml.safe_dump(data, sort_keys=False), "2" * 40, CORE_COMMIT
            return original(repository_id, ref, path)

        self.provider.read_text = stale_policy
        with self.assertRaisesRegex(GuardRejected, "write policy"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content=WRITE_V2,
                expected_blob_sha="f" * 40,
                message="must fail on stale broker policy",
            )
        self.assertFalse(
            any(call[0] == "update" for call in self.provider.calls)
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
        released = [
            call[1]
            for call in self.provider.calls
            if call[0] == "release"
        ]
        self.assertEqual([CORE_ID, INSTANCE_ID], released)


if __name__ == "__main__":
    unittest.main()
