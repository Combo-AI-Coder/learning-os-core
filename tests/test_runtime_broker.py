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
            snapshot_docs = {
                path: content
                for path, content in self.docs.items()
                if (
                    path in {READ_PATH, WRITE_PATH}
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
        shutil.copy2(
            ROOT / "scripts/validate_learning_os.py",
            root / "scripts/validate_learning_os.py",
        )
        return root

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
        original_materialize = self.provider.materialize
        target_read = False

        def racing_read(repository_id, ref, path):
            nonlocal target_read
            result = original_read(repository_id, ref, path)
            if path == READ_PATH:
                target_read = True
            return result

        def racing_materialize(repository_id, ref):
            result = original_materialize(repository_id, ref)
            if repository_id == INSTANCE_ID and target_read:
                self.provider.set_generation(4)
            return result

        self.provider.read_text = racing_read
        self.provider.materialize = racing_materialize
        with self.assertRaisesRegex(
            GuardRejected,
            "authority head changed|Branch registry fresh-read failed closed",
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
        self.assertEqual([CORE_ID, CORE_ID, INSTANCE_ID], released)

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
                    self.broker.guarded_update(
                        session,
                        path=path,
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
        self.assertEqual([CORE_ID, CORE_ID, INSTANCE_ID], released)


if __name__ == "__main__":
    unittest.main()
