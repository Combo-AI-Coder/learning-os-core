from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.runtime_adapter import (
    CasConflict,
    GuardRejected,
    MaterializedRepository,
    ResolutionError,
)
from scripts.runtime_broker import (
    RuntimeCapabilityPolicy,
    RuntimeSessionBroker,
)

ROOT = Path(__file__).resolve().parents[1]
RC_ID, CORE_ID, INSTANCE_ID = 9100000101, 9100000102, 9100000103
CORE_COMMIT = "a" * 40
RC_COMMIT = "b" * 40
INSTANCE_COMMIT = "c" * 40
RUNTIME_PATH = "topics/synthetic/coordination/branches/main/runtime.yaml"
READ_PATH = "scratch/context.txt"
WRITE_PATH = "scratch/state.txt"


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
        self.docs = {
            RUNTIME_PATH: yaml.safe_dump(branch_runtime(), sort_keys=False),
            READ_PATH: "context-v1\n",
            WRITE_PATH: "state-v1\n",
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
            return MaterializedRepository(
                self.instance, INSTANCE_ID, INSTANCE_COMMIT, "synthetic/instance"
            )
        raise ResolutionError("unknown repository")

    def read_text(self, repository_id, ref, path):
        self.calls.append(("read", repository_id, ref, path))
        if ref != "main":
            raise ResolutionError("unexpected ref")
        if repository_id == RC_ID and path == "deployment.yaml":
            return yaml.safe_dump(self.contract, sort_keys=False), "1" * 40, RC_COMMIT
        if repository_id == INSTANCE_ID and path in self.docs:
            return self.docs[path], self.blobs[path], INSTANCE_COMMIT
        raise ResolutionError("unexpected read")

    def update_text(
        self, repository_id, branch, path, content, expected_blob_sha, message
    ):
        self.calls.append(("update", repository_id, branch, path, expected_blob_sha))
        if repository_id != INSTANCE_ID or branch != "main" or path not in self.docs:
            raise CasConflict("unexpected update target")
        if expected_blob_sha != self.blobs[path]:
            raise CasConflict("stale blob")
        self.docs[path] = content
        self.blobs[path] = "9" * 40
        return "8" * 40

    def set_generation(self, generation: int):
        self.docs[RUNTIME_PATH] = yaml.safe_dump(
            branch_runtime(generation=generation), sort_keys=False
        )
        self.blobs[RUNTIME_PATH] = "7" * 40


class RuntimeSessionBrokerTests(unittest.TestCase):
    def setUp(self):
        rc = tempfile.TemporaryDirectory()
        inst = tempfile.TemporaryDirectory()
        self.addCleanup(rc.cleanup)
        self.addCleanup(inst.cleanup)
        self.provider = BrokerProvider(Path(rc.name), Path(inst.name))
        self.broker = RuntimeSessionBroker(self.provider)
        self.policy = RuntimeCapabilityPolicy(
            readable_roots=("scratch",),
            writable_roots=("scratch/state.txt",),
        )

    def open(self, **kwargs):
        return self.broker.open_session(
            locator(),
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

    def test_open_session_rejects_wrong_expected_generation(self):
        with self.assertRaisesRegex(GuardRejected, "not active"):
            self.open(expected_generation=2)

    def test_capability_policy_rejects_write_outside_read_scope(self):
        with self.assertRaisesRegex(ResolutionError, "writable root"):
            RuntimeCapabilityPolicy(
                readable_roots=("scratch/context.txt",),
                writable_roots=("other/state.txt",),
            )

    def test_read_is_scoped_and_returns_target_blob(self):
        session = self.open()
        result = self.broker.read_instance_text(session, READ_PATH)
        self.assertEqual("context-v1\n", result.content)
        self.assertEqual("e" * 40, result.version_token)
        self.assertFalse(hasattr(result, "commit_sha"))
        with self.assertRaisesRegex(GuardRejected, "outside"):
            self.broker.read_instance_text(session, "learner/model.yaml")

    def test_generation_change_blocks_subsequent_read(self):
        session = self.open()
        self.provider.set_generation(4)
        with self.assertRaisesRegex(GuardRejected, "generation"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_deployment_change_blocks_subsequent_read(self):
        session = self.open()
        self.provider.contract = contract(epoch=2)
        with self.assertRaisesRegex(GuardRejected, "epoch"):
            self.broker.read_instance_text(session, READ_PATH)

    def test_generic_write_cannot_mutate_branch_runtime_authority(self):
        policy = RuntimeCapabilityPolicy(
            readable_roots=("topics/synthetic",),
            writable_roots=("topics/synthetic",),
        )
        session = self.broker.open_session(
            locator(), branch_runtime_path=RUNTIME_PATH, policy=policy
        )
        with self.assertRaisesRegex(GuardRejected, "Branch runtime"):
            self.broker.guarded_update(
                session,
                path=RUNTIME_PATH,
                content="x",
                expected_blob_sha="d" * 40,
                message="must fail",
            )

    def test_generation_change_blocks_write_before_target_cas(self):
        session = self.open()
        self.provider.set_generation(4)
        with self.assertRaisesRegex(GuardRejected, "generation"):
            self.broker.guarded_update(
                session,
                path=WRITE_PATH,
                content="state-v2\n",
                expected_blob_sha="f" * 40,
                message="test stale session",
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_allowed_write_uses_deployment_generation_and_target_cas(self):
        session = self.open()
        result = self.broker.guarded_update(
            session,
            path=WRITE_PATH,
            content="state-v2\n",
            expected_blob_sha="f" * 40,
            message="test broker write",
        )
        self.assertEqual("8" * 40, result)
        self.assertEqual("state-v2\n", self.provider.docs[WRITE_PATH])
        names = [call[0] for call in self.provider.calls]
        self.assertLess(names.index("read"), names.index("update"))


if __name__ == "__main__":
    unittest.main()
