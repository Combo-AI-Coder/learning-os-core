"""Deterministic synthetic tests separating first-use creation from checkpoint UX."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

from scripts.first_use import (
    FirstUseIntent,
    FirstUseRejected,
    FirstUseResult,
    SyntheticFirstUseAuthority,
    SyntheticFirstUseService,
    SyntheticFirstUseStore,
    _documents,
    assess_first_use_ux,
    has_saved_learning_position,
    save_and_verify_learning_checkpoint,
)
from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import (
    CasConflict,
    GitRepositoryBinding,
    PreparedFirstUseStep,
    SessionDeploymentContext,
    SyntheticPreparedGitProvider,
    WriteCleanupFailed,
    WriteOutcomeUnknown,
    WritePublishedCleanupFailed,
)
from scripts.runtime_broker import (
    CANDIDATE_YAML_MAX_BYTES,
    DeploymentWriteGate,
    RuntimeCapabilityPolicy,
    RuntimeSessionBroker,
)
from tests import test_first_use as first_use_fixture
from tests.test_runtime_broker import (
    BrokerProvider,
    CHECKPOINT_BLOB,
    CHECKPOINT_PATH,
    CORE_COMMIT,
    CORE_ID,
    INSTANCE_ID,
    RC_ID,
    RUNTIME_PATH,
    checkpoint_progress,
    locator as broker_locator,
)

TOPIC = first_use_fixture.TOPIC
UNIT = first_use_fixture.UNIT
PROGRESS_PATH = first_use_fixture.PROGRESS


def starter_progress(*, revision: int = 1) -> str:
    return yaml.safe_dump({
        "schema_version": "0.3",
        "document_type": "subtopic_progress",
        "revision": revision,
        "updated_at": "2026-11-24T00:00:00Z",
        "topic": "synthetic",
        "subtopic": "unit",
        "plan_revision": 1,
        "milestones": {
            "foundation": {"status": "planned"},
            "next-step": {"status": "planned"},
        },
        "current": {"milestone": ["foundation"]},
        "blockers": [],
        "resume": {
            "return_point": None,
            "ready_next": ["Explain foundation."],
        },
        "watch": [{"kind": "node", "reason": "preserve me"}],
        "avoid_retesting": [],
    }, sort_keys=False)


class FirstUseUxContractTests(unittest.TestCase):
    def setUp(self):
        self.control_dir = tempfile.TemporaryDirectory()
        self.instance_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.control_dir.cleanup)
        self.addCleanup(self.instance_dir.cleanup)
        self.provider = BrokerProvider(Path(self.control_dir.name), Path(self.instance_dir.name))
        self.provider.docs[CHECKPOINT_PATH] = starter_progress()
        self.provider.blobs[CHECKPOINT_PATH] = CHECKPOINT_BLOB
        self.provider.snapshot_extra_paths.add(CHECKPOINT_PATH)
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.gate = DeploymentWriteGate()

    def open_host(self, *, readable=("topics/synthetic/subtopics/unit",), writable=(CHECKPOINT_PATH,), register_cleanup=True):
        host = ReferenceLearningHost.open(
            provider=self.provider,
            locator_source=broker_locator(),
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=readable, writable_roots=writable),
            write_admission=self.gate,
            expected_generation=3,
        )
        if register_cleanup:
            self.addCleanup(host.close)
        return host

    def test_creation_leaves_planned_progress_without_a_saved_position(self):
        intent = FirstUseIntent(TOPIC, "Prediction and context", "Understand conditional next-token prediction.")
        docs = _documents(intent, stamp="2026-11-24T00:00:00Z", lineage="first-use-synthetic")
        progress_doc = next(content for path, content in docs if path == PROGRESS_PATH)
        parsed = yaml.safe_load(progress_doc)
        self.assertEqual(1, parsed["revision"])
        self.assertIsNone(parsed["resume"]["return_point"])
        self.assertTrue(all(
            entry["status"] == "planned"
            for entry in parsed["milestones"].values()
        ))
        self.assertFalse(has_saved_learning_position(progress_doc))

    def test_declining_creation_allows_continuing_teaching_without_persistence(self):
        state = assess_first_use_ux(declined=True)
        self.assertEqual("declined", state.phase)
        self.assertFalse(state.setup_preparation_recorded)
        self.assertEqual("not_established", state.starter_setup_state)
        self.assertEqual("not_saved", state.learning_position_state)
        self.assertFalse(state.can_save_checkpoint)
        self.assertTrue(state.can_continue_teaching)
        self.assertFalse(state.requires_recreation)
        self.assertFalse(state.mastery_established)

        save_result = save_and_verify_learning_checkpoint(
            None,
            progress_path=CHECKPOINT_PATH,
            checkpoint={"milestone": ["foundation"], "return_point": {"focus": "none"}, "ready_next": []},
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(save_result.saved)
        self.assertFalse(save_result.verified)
        self.assertEqual("host_unavailable", save_result.status)
        self.assertFalse(save_result.can_save_checkpoint)
        self.assertEqual("host_unavailable", save_result.error_code)

        for kwargs in (
            {"result": FirstUseResult("prepared")},
            {"progress_document": starter_progress()},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(FirstUseRejected, "invalid_state"):
                assess_first_use_ux(declined=True, **kwargs)

    def test_result_status_labels_never_imply_checkpoint_writability_without_live_writable_host(self):
        readonly_host = self.open_host(writable=())
        self.provider.set_branch_registry(role="practice", subtopic="unit")
        practice_host = self.open_host()
        self.provider.set_branch_registry(role="main", subtopic="unit")
        writable_host = self.open_host()

        for result in (
            FirstUseResult("already_applied", None),
            FirstUseResult("delivery_pending", None),
            FirstUseResult("activated_unissued", None),
            FirstUseResult("already_applied", readonly_host),
            FirstUseResult("active", practice_host),
        ):
            with self.subTest(status=result.status, host=result.host):
                self.assertTrue(result.starter_setup_established)
                self.assertFalse(result.can_save_checkpoint)
                self.assertFalse(result.requires_recreation)
                ux = assess_first_use_ux(result)
                self.assertFalse(ux.can_save_checkpoint)
                self.assertFalse(ux.requires_recreation)
                self.assertFalse(ux.auto_overwrite_allowed)

        live_result = FirstUseResult("already_applied", writable_host)
        self.assertTrue(live_result.can_save_checkpoint)
        writable_host.close()
        self.assertFalse(live_result.can_save_checkpoint)

        revoked_host = self.open_host(register_cleanup=False)
        revoked_result = FirstUseResult("active", revoked_host)
        self.assertTrue(revoked_result.can_save_checkpoint)
        revoked_host._broker.close_session(revoked_host._session)
        self.assertFalse(revoked_result.can_save_checkpoint)

    def test_save_and_verify_distinguishes_starter_noop_verified_save_conflict_and_readback_gaps(self):
        host = self.open_host()
        starter = yaml.safe_load(starter_progress())
        starter_checkpoint = {
            "milestone": starter["current"]["milestone"],
            "return_point": starter["resume"]["return_point"],
            "ready_next": starter["resume"]["ready_next"],
        }
        noop = save_and_verify_learning_checkpoint(
            host,
            progress_path=CHECKPOINT_PATH,
            checkpoint=starter_checkpoint,
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(noop.saved)
        self.assertTrue(noop.verified)
        self.assertFalse(noop.applied)
        self.assertEqual("unchanged_starter_only", noop.status)
        self.assertEqual("starter_setup_established", assess_first_use_ux(host=host, checkpoint_result=noop).phase)

        saved_checkpoint = {
            "milestone": ["next-step"],
            "return_point": {"kind": "teaching_thread", "focus": "Distinguish context vectors."},
            "ready_next": ["Compare bank contexts."],
        }
        wrong_path = save_and_verify_learning_checkpoint(
            host,
            progress_path="topics/other/subtopics/unit/progress.yaml",
            checkpoint=saved_checkpoint,
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(wrong_path.saved)
        self.assertEqual("rejected", wrong_path.status)
        self.assertEqual("guard_rejected", wrong_path.error_code)
        self.assertEqual(starter_progress(), self.provider.docs[CHECKPOINT_PATH])

        conflict = save_and_verify_learning_checkpoint(
            host,
            progress_path=CHECKPOINT_PATH,
            checkpoint=saved_checkpoint,
            expected_version_token="0" * 40,
        )
        self.assertFalse(conflict.saved)
        self.assertFalse(conflict.verified)
        self.assertEqual("conflict", conflict.status)
        self.assertEqual("cas_conflict", conflict.error_code)
        self.assertTrue(conflict.retryable)

        saved = save_and_verify_learning_checkpoint(
            host,
            progress_path=CHECKPOINT_PATH,
            checkpoint=saved_checkpoint,
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertTrue(saved.saved)
        self.assertTrue(saved.verified)
        self.assertTrue(saved.applied)
        self.assertEqual("saved_verified", saved.status)
        self.assertEqual(2, saved.revision)
        self.assertFalse(saved.mastery_established)
        ux = assess_first_use_ux(FirstUseResult("active", host), checkpoint_result=saved)
        self.assertEqual("checkpoint_saved_verified", ux.phase)
        self.assertEqual("saved_verified", ux.learning_position_state)
        self.assertFalse(ux.mastery_established)

        repeat = save_and_verify_learning_checkpoint(
            host,
            progress_path=CHECKPOINT_PATH,
            checkpoint=saved_checkpoint,
            expected_version_token=saved.version_token,
        )
        self.assertTrue(repeat.saved)
        self.assertTrue(repeat.verified)
        self.assertFalse(repeat.applied)
        self.assertEqual("already_saved_verified", repeat.status)

        real_invoke = host.invoke
        def fail_readback(request):
            if request.get("operation") == "read_learning_context":
                return {"ok": False, "error": {"code": "guard_rejected", "retryable": False}}
            return real_invoke(request)

        with mock.patch.object(host, "invoke", side_effect=fail_readback):
            unverified = save_and_verify_learning_checkpoint(
                host,
                progress_path=CHECKPOINT_PATH,
                checkpoint={**saved_checkpoint, "ready_next": ["Explain contextual difference."]},
                expected_version_token=saved.version_token,
            )
        self.assertTrue(unverified.saved)
        self.assertFalse(unverified.verified)
        self.assertEqual("saved_unverified", unverified.status)
        ux_unverified = assess_first_use_ux(FirstUseResult("active", host), checkpoint_result=unverified)
        self.assertEqual("starter_setup_established", ux_unverified.phase)
        self.assertEqual("saved_unverified", ux_unverified.learning_position_state)

        latest_read = host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [CHECKPOINT_PATH]}})
        latest_token = latest_read["result"]["documents"][0]["version_token"]
        def tampered_readback(request):
            response = real_invoke(request)
            if request.get("operation") == "read_learning_context" and response.get("ok"):
                altered = yaml.safe_load(response["result"]["documents"][0]["content"])
                altered["resume"]["ready_next"] = ["Concurrent external edit."]
                response["result"]["documents"][0]["content"] = yaml.safe_dump(altered, sort_keys=False)
            return response

        with mock.patch.object(host, "invoke", side_effect=tampered_readback):
            mismatch = save_and_verify_learning_checkpoint(
                host,
                progress_path=CHECKPOINT_PATH,
                checkpoint={**saved_checkpoint, "ready_next": ["Fresh expected step."]},
                expected_version_token=latest_token,
            )
        self.assertTrue(mismatch.saved)
        self.assertFalse(mismatch.verified)
        self.assertEqual("verification_mismatch", mismatch.status)
        self.assertEqual("saved_unverified", assess_first_use_ux(host=host, checkpoint_result=mismatch).learning_position_state)

    def test_published_checkpoint_with_cleanup_failure_is_verified_by_readback(self):
        host = self.open_host()
        update_text = self.provider.update_text
        real_invoke = host.invoke

        def publish_then_fail_cleanup(*args, **kwargs):
            raise WritePublishedCleanupFailed(update_text(*args, **kwargs))

        for readback, error_code in (
            ("match", "write_published_cleanup_failed"),
            ("denied", "guard_rejected"),
            ("unavailable", "write_published_cleanup_failed"),
            ("mismatch", "verification_mismatch"),
        ):
            with self.subTest(readback=readback):
                def invoke(request):
                    if request["operation"] == "read_learning_context":
                        if readback == "denied":
                            return {"ok": False, "error": {"code": "guard_rejected", "retryable": False}}
                        if readback == "unavailable":
                            raise OSError("synthetic snapshot unavailable")
                        if readback == "mismatch":
                            with mock.patch.dict(self.provider.docs, {CHECKPOINT_PATH: starter_progress()}):
                                return real_invoke(request)
                    return real_invoke(request)

                with (mock.patch.object(self.provider, "update_text", side_effect=publish_then_fail_cleanup),
                      mock.patch.object(host, "invoke", side_effect=invoke) as calls):
                    saved = save_and_verify_learning_checkpoint(
                        host, progress_path=CHECKPOINT_PATH,
                        checkpoint={
                            "milestone": ["foundation"], "return_point": None, "ready_next": [f"Review {readback}."],
                        },
                        expected_version_token=self.provider.blobs[CHECKPOINT_PATH],
                    )
                verified = readback == "match"
                status = "saved_verified" if verified else "saved_unverified"
                self.assertTrue(saved.saved)
                self.assertTrue(saved.applied)
                self.assertEqual(verified, saved.verified)
                self.assertEqual(status, saved.status)
                self.assertEqual(error_code, saved.error_code)
                self.assertFalse(saved.retryable)
                self.assertEqual(status, assess_first_use_ux(host=host, checkpoint_result=saved).learning_position_state)
                self.assertEqual(2, calls.call_count)
                calls.assert_called_with({
                    "operation": "read_learning_context", "arguments": {"required_paths": [CHECKPOINT_PATH]},
                })
                if verified:
                    self.assertEqual(2, saved.revision)
                    self.assertEqual(self.provider.blobs[CHECKPOINT_PATH], saved.version_token)

    def test_unacknowledged_checkpoint_writes_do_not_report_a_save_or_retry(self):
        host = self.open_host()
        for error, code, status in (
            (WriteOutcomeUnknown("synthetic push unconfirmed"), "write_outcome_unknown", "outcome_unknown"),
            (WriteCleanupFailed("synthetic cleanup failure"), "write_cleanup_failed", "rejected"),
        ):
            with (self.subTest(code=code), mock.patch.object(self.provider, "update_text", side_effect=error),
                  mock.patch.object(host, "invoke", wraps=host.invoke) as invoke):
                request = {
                    "checkpoint": {"milestone": ["next-step"], "return_point": None, "ready_next": []},
                    "expected_version_token": CHECKPOINT_BLOB,
                }
                saved = save_and_verify_learning_checkpoint(host, progress_path=CHECKPOINT_PATH, **request)
                self.assertFalse(saved.saved)
                self.assertFalse(saved.applied)
                self.assertFalse(saved.verified)
                self.assertFalse(saved.retryable)
                self.assertEqual(status, saved.status)
                self.assertEqual(code, saved.error_code)
                invoke.assert_called_once_with({"operation": "save_learning_checkpoint", "arguments": request})
                ux = assess_first_use_ux(host=host, checkpoint_result=saved)
                self.assertFalse(ux.auto_overwrite_allowed)
                if status == "outcome_unknown":
                    self.assertEqual("unknown", ux.learning_position_state)

    def test_checkpoint_capability_rechecks_generation_and_deployment(self):
        for fence in ("generation", "frozen"):
            with (self.subTest(fence=fence), mock.patch.dict(self.provider.docs),
                  mock.patch.dict(self.provider.contract["deployment"])):
                host = self.open_host()
                activated = FirstUseResult("active", host)
                self.assertTrue(activated.can_save_checkpoint)
                self.assertTrue(assess_first_use_ux(activated).can_save_checkpoint)
                if fence == "generation":
                    runtime = yaml.safe_load(self.provider.docs[RUNTIME_PATH])
                    runtime["active_generation"] = 4
                    runtime["generations"][3]["lifecycle"] = "archived"
                    runtime["generations"][4] = {"lifecycle": "active"}
                    self.provider.docs[RUNTIME_PATH] = yaml.safe_dump(runtime, sort_keys=False)
                else:
                    self.provider.contract["deployment"]["write_state"] = "frozen"

                self.assertFalse(activated.can_save_checkpoint)
                self.assertFalse(assess_first_use_ux(activated).can_save_checkpoint)
                rejected = save_and_verify_learning_checkpoint(
                    host,
                    progress_path=CHECKPOINT_PATH,
                    checkpoint={"milestone": ["foundation"], "return_point": None, "ready_next": []},
                    expected_version_token=CHECKPOINT_BLOB,
                )
                self.assertEqual("rejected", rejected.status)
                self.assertEqual("guard_rejected", rejected.error_code)
                self.assertFalse(rejected.can_save_checkpoint)

    def test_large_progress_within_broker_limit_can_be_saved_and_verified(self):
        progress = yaml.safe_load(checkpoint_progress())
        progress["resume"]["return_point"]["focus"] = "x" * (70 * 1024)
        content = yaml.safe_dump(progress, sort_keys=False)
        self.assertGreater(len(content.encode("utf-8")), 64 * 1024)
        self.assertLess(len(content.encode("utf-8")), CANDIDATE_YAML_MAX_BYTES)
        self.assertTrue(has_saved_learning_position(content))

        saved = save_and_verify_learning_checkpoint(
            self.open_host(),
            progress_path=CHECKPOINT_PATH,
            checkpoint={
                "milestone": progress["current"]["milestone"],
                "return_point": progress["resume"]["return_point"],
                "ready_next": progress["resume"]["ready_next"],
            },
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertTrue(saved.saved)
        self.assertTrue(saved.verified)
        self.assertTrue(saved.applied)
        self.assertEqual("saved_verified", saved.status)
        self.assertTrue(has_saved_learning_position(self.provider.docs[CHECKPOINT_PATH]))

    def test_invalid_readback_preserves_applied_checkpoint_result(self):
        host = self.open_host()
        real_invoke = host.invoke
        for index, fields in enumerate((
            {},
            {"content": None},
            {"content": yaml.safe_load(starter_progress())},
            {"content": "schema_version: [\n"},
            {"content": "just a string"},
        )):
            with self.subTest(fields=fields):
                def invalid_readback(request):
                    response = real_invoke(request)
                    if request.get("operation") == "read_learning_context" and response.get("ok"):
                        document = response["result"]["documents"][0]
                        document.pop("content", None)
                        document.update(fields)
                    return response

                with mock.patch.object(host, "invoke", side_effect=invalid_readback):
                    saved = save_and_verify_learning_checkpoint(
                        host,
                        progress_path=CHECKPOINT_PATH,
                        checkpoint={
                            "milestone": ["foundation"],
                            "return_point": None,
                            "ready_next": [f"Step {index}."],
                        },
                        expected_version_token=self.provider.blobs[CHECKPOINT_PATH],
                    )
                self.assertTrue(saved.saved)
                self.assertFalse(saved.verified)
                self.assertEqual("saved_unverified", saved.status)
                self.assertTrue(saved.applied)
                self.assertEqual("invalid_progress_document", saved.error_code)

    def test_conflict_after_saved_checkpoint_requires_fresh_progress_for_ux(self):
        host = self.open_host()
        activated = FirstUseResult("active", host)
        checkpoint = {
            "milestone": ["foundation"],
            "return_point": {"focus": "Saved foundation example."},
            "ready_next": [],
        }
        saved = save_and_verify_learning_checkpoint(
            host, progress_path=CHECKPOINT_PATH, checkpoint=checkpoint,
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertEqual("saved_verified", saved.status)
        conflict = save_and_verify_learning_checkpoint(
            host, progress_path=CHECKPOINT_PATH, checkpoint=checkpoint,
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertEqual("conflict", conflict.status)
        self.assertEqual("unknown", assess_first_use_ux(activated, checkpoint_result=conflict).learning_position_state)
        fresh = host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [CHECKPOINT_PATH]}})
        self.assertTrue(fresh["ok"], fresh)
        self.assertEqual("saved_verified", assess_first_use_ux(
            activated, checkpoint_result=conflict,
            progress_document=fresh["result"]["documents"][0]["content"],
        ).learning_position_state)

    def test_has_saved_learning_position_validation(self):
        for bad in (
            "schema_version: [\n",
            "just a string",
            "document_type: topic_progress\nrevision: 1\nmilestones: {}\ncurrent: {}\nresume: {}\n",
            {"document_type": "subtopic_progress", "revision": True, "milestones": {}, "current": {}, "resume": {}},
            {"document_type": "subtopic_progress", "revision": 0, "milestones": {}, "current": {}, "resume": {}},
            {"document_type": "subtopic_progress", "revision": 1, "milestones": [], "current": {}, "resume": {}},
            "x" * (CANDIDATE_YAML_MAX_BYTES + 1),
            123,
        ):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(FirstUseRejected):
                has_saved_learning_position(bad)

        base = yaml.safe_load(starter_progress())
        self.assertFalse(has_saved_learning_position(base))
        empty_return = yaml.safe_load(starter_progress())
        empty_return["resume"]["return_point"] = {}
        self.assertFalse(has_saved_learning_position(empty_return))
        with_return = yaml.safe_load(starter_progress())
        with_return["resume"]["return_point"] = {"focus": "explain token lookup"}
        self.assertTrue(has_saved_learning_position(with_return))
        completed_milestone = yaml.safe_load(starter_progress())
        completed_milestone["milestones"]["foundation"]["status"] = "completed"
        self.assertTrue(has_saved_learning_position(completed_milestone))
        self.assertTrue(has_saved_learning_position(checkpoint_progress()))


class _FastFirstUseProvider(SyntheticPreparedGitProvider):
    """In-memory step/ref stub driving SyntheticFirstUseService into a BrokerProvider."""

    def __init__(self, bindings, prepared_store: Path, broker_provider: BrokerProvider):
        super().__init__(bindings, prepared_store=prepared_store)
        self.broker_provider = broker_provider
        self.head = "1" * 40
        self.step_outcomes: dict[int, str] = {}
        self.inspect_error_indices: set[int] = set()
        self.pending_docs: dict[int, tuple[str, str]] = {}

    def resolve_ref(self, repository_id, ref):
        if repository_id == INSTANCE_ID and ref == "main":
            return self.broker_provider.instance_head
        return self.head

    def preflight_first_use(self, repository_id, branch, documents, *, operation_id):
        self.pending_docs = {index: (path, content) for index, (path, content) in enumerate(documents)}
        return {"head": self.broker_provider.instance_head, "entries": []}

    def prepare_first_use_step(
        self, *, repository_id, branch, path, content, expected_parent, expected_blob,
        operation_id, step_index, intent_digest, manifest_digest, timestamp,
    ):
        candidate = f"{step_index + 2:040x}"
        self.pending_docs[step_index] = (path, content)
        return PreparedFirstUseStep(
            candidate=candidate,
            artifact_sha256=f"{step_index + 20:064x}",
            descriptor={
                "path": path,
                "parent": expected_parent,
                "tree": f"{step_index + 30:040x}",
                "new_blob": hashlib.sha1(content.encode("utf-8")).hexdigest(),
            },
        )

    def inspect_first_use_step(self, step):
        index = int(step.candidate, 16) - 2
        if index in self.inspect_error_indices:
            raise CasConflict("synthetic transport loss")
        return self.step_outcomes.get(index, "not_applied")

    def publish_first_use_step(self, step, *, authorize):
        authorize()
        index = int(step.candidate, 16) - 2
        self.head = step.candidate
        self.broker_provider.instance_head = step.candidate
        self.step_outcomes[index] = "applied"
        if index in self.pending_docs:
            path, content = self.pending_docs[index]
            self.broker_provider.docs[path] = content
            self.broker_provider.blobs[path] = hashlib.sha1(content.encode("utf-8")).hexdigest()
            self.broker_provider.snapshot_extra_paths.add(path)


class FirstUseServiceUxStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "control.git").mkdir(exist_ok=True)
        (self.root / "instance.git").mkdir(exist_ok=True)
        (self.root / "control-broker").mkdir(exist_ok=True)
        (self.root / "instance-broker").mkdir(exist_ok=True)
        self.broker_provider = BrokerProvider(self.root / "control-broker", self.root / "instance-broker")
        self.locator = broker_locator()
        bindings = [
            GitRepositoryBinding(RC_ID, str(self.root / "control.git"), "synthetic/control"),
            GitRepositoryBinding(INSTANCE_ID, str(self.root / "instance.git"), "synthetic/instance", writable=True),
        ]
        self.provider = _FastFirstUseProvider(bindings, self.root / "prepared", self.broker_provider)
        self.addCleanup(self.provider.close)
        self.authority = SyntheticFirstUseAuthority()
        self.intent = FirstUseIntent(
            "synthetic", "Prediction and context", "Understand next-token prediction and tokens/context.", unit="unit"
        )
        self.receiver = self.authority.authorize(
            principal="synthetic-learner",
            surface="synthetic-cli",
            operation_id="operation-ux",
            intent=self.intent,
            locator=self.locator,
        )
        self.gate = DeploymentWriteGate()

    def make_service(self, *, event=None):
        service = SyntheticFirstUseService(
            provider=self.provider,
            locator=self.locator,
            authority=self.authority,
            store=SyntheticFirstUseStore(self.root / "receipts", self.authority.integrity_key),
            write_admission=self.gate,
            event=event,
        )
        service._validate = mock.Mock(return_value=(
            SessionDeploymentContext(
                "dep-broker-test", 1, CORE_ID, CORE_COMMIT, INSTANCE_ID, RC_ID, "main", "deployment.yaml"
            ),
            self.broker_provider.control_head,
            self.broker_provider.instance_head,
        ))
        service._current = lambda receipt, receiver: self.authority.check(receiver, receipt["binding"])
        service.broker = RuntimeSessionBroker(self.broker_provider, self.locator, write_admission=self.gate)
        return service

    def test_end_to_end_creation_vs_checkpoint_and_readonly_resume(self):
        service = self.make_service()
        prepared = service.prepare("operation-ux", self.receiver, self.intent)
        self.assertEqual("prepared", prepared.status)
        self.assertTrue(prepared.setup_preparation_recorded)
        self.assertFalse(prepared.starter_setup_established)
        self.assertFalse(prepared.can_save_checkpoint)
        self.assertFalse(prepared.requires_recreation)
        self.assertEqual("preparation_recorded", assess_first_use_ux(prepared).phase)

        activated = service.apply("operation-ux", self.receiver, self.intent)
        self.assertEqual("active", activated.status)
        self.assertTrue(activated.starter_setup_established)
        self.assertTrue(activated.can_save_checkpoint)
        self.addCleanup(activated.host.close)

        # Re-applying while the issued Host is active reuses it; prepare/inspect never imply writable.
        reused = service.apply("operation-ux", self.receiver, self.intent)
        self.assertEqual("already_applied", reused.status)
        self.assertIs(activated.host, reused.host)
        self.assertTrue(reused.can_save_checkpoint)
        self.assertFalse(service.prepare("operation-ux", self.receiver, self.intent).can_save_checkpoint)
        self.assertFalse(service.inspect("operation-ux", self.receiver, self.intent).can_save_checkpoint)

        initial_read = activated.host.invoke(
            {"operation": "read_learning_context", "arguments": {"required_paths": [CHECKPOINT_PATH]}}
        )
        self.assertTrue(initial_read["ok"], initial_read)
        initial_doc = initial_read["result"]["documents"][0]
        self.assertFalse(has_saved_learning_position(initial_doc["content"]))

        before_checkpoint_ux = assess_first_use_ux(activated, progress_document=initial_doc["content"])
        self.assertEqual("starter_setup_established", before_checkpoint_ux.phase)
        self.assertEqual("not_saved", before_checkpoint_ux.learning_position_state)
        self.assertTrue(before_checkpoint_ux.can_save_checkpoint)
        self.assertFalse(before_checkpoint_ux.mastery_established)

        saved = save_and_verify_learning_checkpoint(
            activated.host,
            progress_path=CHECKPOINT_PATH,
            checkpoint={
                "milestone": ["next-token"],
                "return_point": {"kind": "teaching_thread", "focus": "Next-token example done; compare bank contexts."},
                "ready_next": ["Compare river-bank and money-bank contexts."],
            },
            expected_version_token=initial_doc["version_token"],
        )
        self.assertTrue(saved.saved)
        self.assertTrue(saved.verified)
        self.assertEqual("saved_verified", saved.status)
        self.assertEqual(2, saved.revision)

        after_checkpoint_ux = assess_first_use_ux(activated, checkpoint_result=saved)
        self.assertEqual("checkpoint_saved_verified", after_checkpoint_ux.phase)
        self.assertEqual("saved_verified", after_checkpoint_ux.learning_position_state)
        self.assertTrue(after_checkpoint_ux.can_save_checkpoint)
        self.assertFalse(after_checkpoint_ux.mastery_established)

        activated.host.close()
        self.assertFalse(activated.can_save_checkpoint)
        reapplied_after_save = self.make_service().apply("operation-ux", self.receiver, self.intent)
        self.assertEqual("already_applied", reapplied_after_save.status)
        self.assertIsNone(reapplied_after_save.host)
        self.assertTrue(reapplied_after_save.starter_setup_established)
        self.assertFalse(reapplied_after_save.can_save_checkpoint)
        self.assertFalse(reapplied_after_save.requires_recreation)

        readonly_consumer = ReferenceLearningHost.open(
            provider=self.broker_provider,
            locator_source=self.locator,
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("topics/synthetic", "evidence")),
            expected_generation=None,
            write_admission=self.gate,
        )
        self.addCleanup(readonly_consumer.close)
        consumer_read = readonly_consumer.invoke(
            {"operation": "read_learning_context", "arguments": {"required_paths": [CHECKPOINT_PATH]}}
        )
        self.assertTrue(consumer_read["ok"], consumer_read)
        consumer_ux = assess_first_use_ux(
            host=readonly_consumer, progress_document=consumer_read["result"]["documents"][0]["content"]
        )
        self.assertEqual("checkpoint_saved_verified", consumer_ux.phase)
        self.assertEqual("established", consumer_ux.starter_setup_state)
        self.assertEqual("saved_verified", consumer_ux.learning_position_state)
        self.assertFalse(consumer_ux.can_save_checkpoint)
        self.assertFalse(consumer_ux.requires_recreation)
        self.assertFalse(consumer_ux.mastery_established)

    def test_interrupted_delivery_unknown_outcomes_and_activated_unissued(self):
        # 1. Partial staging + outcome_unknown at step 0 vs step 9.
        for step_index, expected_starter in ((0, "not_established"), (9, "unknown")):
            with self.subTest(step_index=step_index):
                self.doCleanups()
                self.setUp()
                service = self.make_service()
                prepared = service.prepare("operation-ux", self.receiver, self.intent)
                self.assertEqual("prepared", prepared.status)
                self.assertTrue(prepared.setup_preparation_recorded)
                self.assertFalse(prepared.starter_setup_established)
                self.assertEqual("preparation_recorded", assess_first_use_ux(prepared).phase)

                self.provider.inspect_error_indices.add(step_index)
                unknown = service.apply("operation-ux", self.receiver, self.intent)
                self.assertEqual("outcome_unknown", unknown.status)
                self.assertTrue(unknown.setup_preparation_recorded)
                self.assertEqual(expected_starter, unknown.starter_setup_state)
                self.assertFalse(unknown.can_save_checkpoint)
                self.assertFalse(unknown.requires_recreation)
                ux = assess_first_use_ux(unknown)
                self.assertEqual("outcome_unknown", ux.phase)
                self.assertFalse(ux.can_save_checkpoint)
                self.assertFalse(ux.requires_recreation)
                self.assertFalse(ux.auto_overwrite_allowed)

                # Retrying prepare() preserves recorded state without asking to recreate.
                reprised = service.prepare("operation-ux", self.receiver, self.intent)
                self.assertEqual("outcome_unknown", reprised.status)
                self.assertTrue(reprised.setup_preparation_recorded)
                self.assertEqual(expected_starter, reprised.starter_setup_state)

        # 2. Delivery pending after activation.
        self.doCleanups()
        self.setUp()
        def stop_on_delivery(event, _receipt):
            if event == "delivery_pending":
                raise first_use_fixture.Restart()

        service = self.make_service(event=stop_on_delivery)
        service.prepare("operation-ux", self.receiver, self.intent)
        with self.assertRaises(first_use_fixture.Restart):
            service.apply("operation-ux", self.receiver, self.intent)

        pending = self.make_service().apply("operation-ux", self.receiver, self.intent)
        self.assertEqual("delivery_pending", pending.status)
        self.assertIsNone(pending.host)
        self.assertTrue(pending.setup_preparation_recorded)
        self.assertTrue(pending.starter_setup_established)
        self.assertFalse(pending.can_save_checkpoint)
        self.assertFalse(pending.requires_recreation)
        pending_ux = assess_first_use_ux(pending)
        self.assertEqual("starter_setup_established", pending_ux.phase)
        self.assertEqual("unknown", pending_ux.learning_position_state)
        self.assertFalse(pending_ux.can_save_checkpoint)
        self.assertFalse(pending_ux.requires_recreation)
        self.assertFalse(pending_ux.auto_overwrite_allowed)

        # 3. Activated_unissued after revocation at step 10.
        self.doCleanups()
        self.setUp()
        def revoke_after_activation(event, receipt):
            if event == "step_confirmed" and receipt["confirmed"] == 10:
                self.authority.revoke(self.receiver)

        revoked_service = self.make_service(event=revoke_after_activation)
        revoked_service.prepare("operation-ux", self.receiver, self.intent)
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            revoked_service.apply("operation-ux", self.receiver, self.intent)
        restored = self.authority.authorize(
            principal="synthetic-learner",
            surface="synthetic-cli",
            operation_id="operation-ux",
            intent=self.intent,
            locator=self.locator,
        )
        inspected = self.make_service().inspect("operation-ux", restored, self.intent)
        self.assertEqual("activated_unissued", inspected.status)
        self.assertTrue(inspected.setup_preparation_recorded)
        self.assertTrue(inspected.starter_setup_established)
        self.assertFalse(inspected.can_save_checkpoint)
        self.assertFalse(inspected.requires_recreation)


if __name__ == "__main__":
    unittest.main()
