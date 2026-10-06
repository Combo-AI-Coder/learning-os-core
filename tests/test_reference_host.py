import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import yaml

from scripts.reference_host import (
    REFERENCE_HOST_OPERATIONS,
    ReferenceLearningHost,
)
from scripts.runtime_broker import (
    DeploymentWriteGate,
    LEARNING_CONTEXT_MAX_DOCUMENTS,
    RuntimeCapabilityPolicy,
)
from tests.test_runtime_broker import (
    BrokerProvider,
    CHECKPOINT_BLOB,
    CHECKPOINT_PATH,
    KNOWLEDGE_EVIDENCE_ID,
    READ_PATH,
    RUNTIME_PATH,
    checkpoint_progress,
    knowledge_candidate,
    locator,
    typed_evidence,
)


class ReferenceLearningHostTests(unittest.TestCase):
    def setUp(self):
        control = tempfile.TemporaryDirectory()
        instance = tempfile.TemporaryDirectory()
        self.addCleanup(control.cleanup)
        self.addCleanup(instance.cleanup)
        self.provider = BrokerProvider(
            Path(control.name), Path(instance.name)
        )
        self.provider.docs[CHECKPOINT_PATH] = checkpoint_progress()
        self.provider.blobs[CHECKPOINT_PATH] = CHECKPOINT_BLOB
        self.provider.snapshot_extra_paths.add(CHECKPOINT_PATH)
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.host = ReferenceLearningHost.open(
            provider=self.provider,
            locator_source=locator(),
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(
                readable_roots=(
                    "learner",
                    "evidence",
                    "topics/synthetic/subtopics/unit",
                ),
                writable_roots=(
                    READ_PATH,
                    "learner/knowledge/new-domain.yaml",
                    "evidence",
                    CHECKPOINT_PATH,
                ),
            ),
            write_admission=DeploymentWriteGate(),
            expected_generation=3,
        )
        self.addCleanup(self.host.close)

    def invoke(self, operation, **arguments):
        return self.host.invoke({
            "operation": operation,
            "arguments": arguments,
        })

    def assert_guard_rejection_without_writes(self, result):
        self.assertEqual(
            {"code": "guard_rejected", "retryable": False}, result["error"]
        )
        self.assertFalse(result["ok"])
        self.assertFalse(any(call[0] in {"create", "update"}
                             for call in self.provider.calls))

    def test_context_combined_limit_precedes_iteration_and_provider_io(self):
        class UnscannableList(list):
            def __iter__(self):
                raise AssertionError("oversized paths must not be scanned or copied")

        limit = LEARNING_CONTEXT_MAX_DOCUMENTS
        for required_count, optional_count in ((limit + 1, 0), (0, limit + 1),
                                                (limit, 1), (1, limit)):
            with self.subTest(required=required_count, optional=optional_count):
                self.provider.calls.clear()
                result = self.invoke(
                    "read_learning_context",
                    required_paths=UnscannableList([READ_PATH] * required_count),
                    optional_paths=UnscannableList([READ_PATH] * optional_count),
                )
                self.assertFalse(result["ok"])
                self.assertEqual("resolution_failed", result["error"]["code"])
                self.assertEqual([], self.provider.calls)

    def test_context_combined_limit_allows_exact_boundary(self):
        optional = [f"learner/missing-{i}.yaml"
                    for i in range(LEARNING_CONTEXT_MAX_DOCUMENTS - 1)]
        result = self.invoke("read_learning_context", required_paths=[READ_PATH],
                             optional_paths=optional)
        self.assertTrue(result["ok"])
        self.assertEqual(optional, result["result"]["missing_optional"])

    def test_candidate_yaml_scalar_errors_have_stable_public_envelopes(self):
        invalid_scalars = ["9999-99-99", "null\n9999-99-99: invalid key"]
        for scalar in ('!!timestamp not-a-time', '!!bool not-a-bool', '!!int ""'):
            invalid_scalars.extend((scalar, "null\n" + scalar + ": invalid key"))
        digit_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        if digit_limit:
            invalid_scalars.append("!!int " + "9" * (digit_limit + 100))
        for operation, content in (("create_evidence", typed_evidence()),
                                   ("reconcile_knowledge", knowledge_candidate())):
            for invalid_scalar in invalid_scalars:
                with self.subTest(operation=operation, scalar=invalid_scalar[:20]):
                    self.provider.calls.clear()
                    arguments = {"content": content + "invalid: " + invalid_scalar + "\n"}
                    if operation == "reconcile_knowledge":
                        arguments["expected_version_token"] = "e" * 40
                    result = self.invoke(operation, **arguments)
                    self.assert_guard_rejection_without_writes(result)
                    self.assertEqual([], self.provider.calls)

    def test_candidate_yaml_overflow_has_stable_public_envelope(self):
        # Constructor overflow is platform/scalar dependent; simulate only the
        # load step, leaving bounded preflight and the public operation intact.
        with mock.patch("scripts.runtime_broker.yaml.safe_load",
                        side_effect=OverflowError("private scalar details")):
            for operation in ("create_evidence", "reconcile_knowledge"):
                arguments = {"content": "document_type: evidence\n"}
                if operation == "reconcile_knowledge":
                    arguments["expected_version_token"] = "e" * 40
                result = self.invoke(operation, **arguments)
                self.assert_guard_rejection_without_writes(result)
                self.assertNotIn("private scalar details", json.dumps(result))

    def test_persisted_yaml_scalar_errors_have_stable_public_envelopes(self):
        evidence_path = f"evidence/{KNOWLEDGE_EVIDENCE_ID}.yaml"
        cases = [(location, scalar)
                 for location in ("existing_evidence", "referenced_evidence", "current_knowledge")
                 for scalar in ("9999-99-99", "!!timestamp not-a-time",
                                "!!bool not-a-bool", '!!int ""')]
        for location, scalar in cases:
            with self.subTest(location=location, scalar=scalar):
                self.provider.docs[evidence_path] = typed_evidence()
                self.provider.blobs[evidence_path] = "3" * 40
                self.provider.docs[READ_PATH] = yaml.safe_dump({
                    "schema_version": "0.3", "document_type": "learner_knowledge",
                    "revision": 1, "domain": "synthetic", "concepts": {},
                })
                path = READ_PATH if location == "current_knowledge" else evidence_path
                self.provider.docs[path] += "invalid: " + scalar + "\n"
                before = dict(self.provider.docs)
                self.provider.calls.clear()
                if location == "existing_evidence":
                    result = self.invoke("create_evidence", content=typed_evidence())
                else:
                    result = self.invoke("reconcile_knowledge", content=knowledge_candidate(),
                                         expected_version_token="e" * 40)
                self.assert_guard_rejection_without_writes(result)
                self.assertEqual(before, self.provider.docs)

    def test_new_evidence_requires_observation_content_and_valid_time(self):
        invalid_values = {
            "observation": (None, "", "  ", {}, {"kind": "synthetic"},
                            {"summary": None}, {"summary": "  "}, [], 42),
            "observed_at": (None, "", "  ", "not-a-time", "9999-99-99T00:00:00Z",
                            "2026-10-05", True, 42, {}),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = yaml.safe_load(typed_evidence())
                    candidate[field] = value
                    self.provider.calls.clear()
                    before = dict(self.provider.docs)
                    result = self.invoke("create_evidence", content=yaml.safe_dump(candidate))
                    self.assert_guard_rejection_without_writes(result)
                    self.assertEqual(before, self.provider.docs)
        for field in invalid_values:
            with self.subTest(missing=field):
                candidate = yaml.safe_load(typed_evidence())
                del candidate[field]
                self.provider.calls.clear()
                self.assert_guard_rejection_without_writes(
                    self.invoke("create_evidence", content=yaml.safe_dump(candidate))
                )

    def test_new_evidence_rejects_normalized_invalid_timestamp_offsets(self):
        for offset in ("+01:99", "-00:99", "+00:00:99", "+24:00", "+0199"):
            for quoted in (False, True):
                with self.subTest(offset=offset, quoted=quoted):
                    content = typed_evidence()
                    timestamp = "2026-10-05T12:00:00" + offset
                    lines = [line for line in content.splitlines()
                             if not line.startswith("observed_at:")]
                    lines.append("observed_at: " + (json.dumps(timestamp) if quoted else timestamp))
                    self.provider.calls.clear()
                    result = self.invoke("create_evidence", content="\n".join(lines) + "\n")
                    self.assert_guard_rejection_without_writes(result)

    def test_new_evidence_timestamp_ignores_non_string_key_decoys(self):
        for index, decoy in enumerate(("2026-10-05T12:00:00Z", "{nested: value}", "[item]")):
            for actual in ("not-a-time", "2026-10-05T12:00:00+01:99", "2026-10-05T12:00:00Z"):
                with self.subTest(decoy=decoy, actual=actual):
                    candidate = yaml.safe_load(typed_evidence(evidence_id=f"evi-decoy-{index}"))
                    candidate["observed_at"] = actual
                    content = "!!null observed_at: " + decoy + "\n" + yaml.safe_dump(candidate)
                    self.provider.calls.clear()
                    result = self.invoke("create_evidence", content=content)
                    if actual.endswith("Z"):
                        self.assertTrue(result["ok"])
                        self.assertTrue(result["result"]["applied"])
                    else:
                        self.assert_guard_rejection_without_writes(result)

    def test_new_evidence_accepts_valid_iso_timestamp_profiles(self):
        for index, timestamp in enumerate(("2026-10-05T12:00:00Z",
                "2026-10-05T12:00:00+08:00", "2026-10-05T12:00:00-03:30",
                "2026-10-05T12:00:00.123456+01:30:15", "2026-10-05 12:00:00",
                "20261005T120000+0800", "2026-W41-1T12:00", "2026-10-05T12")):
            with self.subTest(timestamp=timestamp):
                candidate = yaml.safe_load(typed_evidence(evidence_id=f"evi-time-{index}"))
                candidate["observed_at"] = timestamp
                result = self.invoke("create_evidence", content=yaml.safe_dump(candidate))
                self.assertTrue(result["ok"])
                self.assertTrue(result["result"]["applied"])

    def test_new_evidence_accepts_string_and_structured_observations(self):
        for index, observation in enumerate(("observed explanation", {"summary": "observed explanation"})):
            candidate = yaml.safe_load(typed_evidence(evidence_id=f"evi-valid-{index}"))
            candidate["observation"] = observation
            result = self.invoke("create_evidence", content=yaml.safe_dump(candidate))
            self.assertTrue(result["ok"])
            self.assertTrue(result["result"]["applied"])

    def test_legacy_null_evidence_read_retry_and_reconciliation_remain_compatible(self):
        candidate = yaml.safe_load(typed_evidence())
        candidate["observation"] = None
        candidate["observed_at"] = None
        content = yaml.safe_dump(candidate)
        path = f"evidence/{KNOWLEDGE_EVIDENCE_ID}.yaml"
        self.provider.docs[path] = content
        self.provider.blobs[path] = "3" * 40
        read = self.invoke("read_learning_context", required_paths=[path])
        self.assertTrue(read["ok"])
        self.assertEqual(content, read["result"]["documents"][0]["content"])
        retry = self.invoke("create_evidence", content=content)
        self.assertTrue(retry["ok"])
        self.assertFalse(retry["result"]["applied"])
        reconciled = self.invoke("reconcile_knowledge", content=knowledge_candidate(),
                                 expected_version_token="e" * 40)
        self.assertTrue(reconciled["ok"])
        self.assertEqual(content, self.provider.docs[path])

    def test_legacy_style_id_cannot_bypass_new_evidence_admission(self):
        candidate = yaml.safe_load(typed_evidence(evidence_id="evt_legacy_style"))
        candidate["observed_at"] = None
        self.provider.calls.clear()
        self.assert_guard_rejection_without_writes(
            self.invoke("create_evidence", content=yaml.safe_dump(candidate))
        )

    def test_surface_is_narrow_and_excludes_generic_or_continuity_writes(self):
        self.assertEqual(
            {
                "read_learning_context",
                "discover_learning_evidence",
                "save_learning_checkpoint",
                "create_evidence",
                "reconcile_knowledge",
            },
            set(REFERENCE_HOST_OPERATIONS),
        )
        for operation in (
            "guarded_update",
            "claim_successor_session",
            "update_branch_runtime",
        ):
            result = self.invoke(operation)
            self.assertFalse(result["ok"])
            self.assertEqual("guard_rejected", result["error"]["code"])

    def test_synthetic_reference_journey_reads_creates_and_reconciles(self):
        context = self.invoke(
            "read_learning_context",
            required_paths=[READ_PATH],
            optional_paths=["learner/execution.yaml"],
        )
        self.assertTrue(context["ok"])
        self.assertEqual(
            ["learner/execution.yaml"],
            context["result"]["missing_optional"],
        )
        [knowledge] = context["result"]["documents"]
        self.assertEqual(READ_PATH, knowledge["path"])
        self.assertEqual("e" * 40, knowledge["version_token"])

        evidence = typed_evidence(
            evidence_id=KNOWLEDGE_EVIDENCE_ID,
        )
        created = self.invoke("create_evidence", content=evidence)
        self.assertEqual(
            {
                "surface_version": "v3",
                "ok": True,
                "operation": "create_evidence",
                "result": {"applied": True},
            },
            created,
        )

        candidate = knowledge_candidate()
        reconciled = self.invoke(
            "reconcile_knowledge",
            content=candidate,
            expected_version_token=knowledge["version_token"],
        )
        self.assertTrue(reconciled["ok"])
        self.assertEqual(
            {"applied": True}, reconciled["result"]
        )
        self.assertEqual(candidate, self.provider.docs[READ_PATH])

    def test_reference_host_saves_bound_learning_checkpoint(self):
        result = self.invoke(
            "save_learning_checkpoint",
            checkpoint={
                "milestone": ["next-step"],
                "return_point": {
                    "kind": "teaching_thread",
                    "focus": "fresh consumer resumes here",
                },
                "ready_next": ["continue with a new example"],
            },
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertEqual(
            {
                "surface_version": "v3",
                "ok": True,
                "operation": "save_learning_checkpoint",
                "result": {"applied": True},
            },
            result,
        )
        saved = yaml.safe_load(self.provider.docs[CHECKPOINT_PATH])
        self.assertEqual(2, saved["revision"])
        self.assertEqual(["next-step"], saved["current"]["milestone"])
        self.assertEqual(
            "fresh consumer resumes here",
            saved["resume"]["return_point"]["focus"],
        )
        self.assertEqual(
            ["continue with a new example"],
            saved["resume"]["ready_next"],
        )
        self.assertEqual(
            [{"kind": "node", "reason": "preserve me"}],
            saved["watch"],
        )

    def test_reference_host_rejects_oversized_checkpoint_array(self):
        result = self.invoke(
            "save_learning_checkpoint",
            checkpoint={
                "milestone": ["foundation"] * 17,
                "return_point": None,
                "ready_next": [],
            },
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("resolution_failed", result["error"]["code"])

    def test_persisted_large_hex_integer_cannot_escape_checkpoint_envelope(self):
        digit_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        if not digit_limit:
            self.skipTest("interpreter has no integer conversion digit limit")
        current = yaml.safe_load(checkpoint_progress())
        self.provider.docs[CHECKPOINT_PATH] += "legacy_counter: 0x" + "f" * (digit_limit + 100) + "\n"
        for checkpoint in (
            {"milestone": [], "return_point": None, "ready_next": []},
            {"milestone": current["current"]["milestone"],
             "return_point": current["resume"]["return_point"],
             "ready_next": current["resume"]["ready_next"]},
        ):
            with self.subTest(checkpoint=checkpoint):
                before = dict(self.provider.docs)
                self.provider.calls.clear()
                result = self.invoke("save_learning_checkpoint", checkpoint=checkpoint,
                                     expected_version_token=CHECKPOINT_BLOB)
                self.assert_guard_rejection_without_writes(result)
                self.assertEqual(before, self.provider.docs)

    def test_reference_host_rejects_unserializable_checkpoint_integer(self):
        import sys

        max_digits = (
            sys.get_int_max_str_digits()
            if hasattr(sys, "get_int_max_str_digits")
            else 0
        )
        if max_digits == 0:
            self.skipTest("interpreter has no integer conversion digit limit")
        result = self.invoke(
            "save_learning_checkpoint",
            checkpoint={
                "milestone": ["foundation"],
                "return_point": {"ordinal": 10 ** (max_digits + 100)},
                "ready_next": [],
            },
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("resolution_failed", result["error"]["code"])

    def test_reference_host_rejects_checkpoint_shape_extension(self):
        result = self.invoke(
            "save_learning_checkpoint",
            checkpoint={
                "milestone": ["next-step"],
                "return_point": None,
                "ready_next": [],
                "milestones": {"next-step": {"status": "completed"}},
            },
            expected_version_token=CHECKPOINT_BLOB,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("resolution_failed", result["error"]["code"])
        self.assertEqual(
            checkpoint_progress(), self.provider.docs[CHECKPOINT_PATH]
        )
    def test_fresh_consumers_recover_durable_checkpoint_and_knowledge(self):
        producer_session_id = self.host._session.session_id
        initial = self.invoke(
            "read_learning_context",
            required_paths=[CHECKPOINT_PATH, READ_PATH],
        )
        self.assertTrue(initial["ok"])
        by_path = {
            item["path"]: item
            for item in initial["result"]["documents"]
        }

        checkpoint = {
            "milestone": ["next-step"],
            "return_point": {
                "kind": "teaching_thread",
                "milestone": "next-step",
                "focus": "fresh consumers resume from durable state",
            },
            "ready_next": ["apply the concept to a fresh example"],
        }
        saved = self.invoke(
            "save_learning_checkpoint",
            checkpoint=checkpoint,
            expected_version_token=by_path[CHECKPOINT_PATH]["version_token"],
        )
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["result"]["applied"])

        evidence = typed_evidence(
            evidence_id=KNOWLEDGE_EVIDENCE_ID,
        )
        created = self.invoke("create_evidence", content=evidence)
        self.assertTrue(created["ok"])
        self.assertTrue(created["result"]["applied"])

        candidate = knowledge_candidate()
        reconciled = self.invoke(
            "reconcile_knowledge",
            content=candidate,
            expected_version_token=by_path[READ_PATH]["version_token"],
        )
        self.assertTrue(reconciled["ok"])
        self.assertTrue(reconciled["result"]["applied"])

        self.host.close()

        recovered = []
        consumer_session_ids = []
        for _ in range(2):
            consumer = ReferenceLearningHost.open(
                provider=self.provider,
                locator_source=locator(),
                branch_runtime_path=RUNTIME_PATH,
                policy=RuntimeCapabilityPolicy(
                    readable_roots=(
                        "learner",
                        "evidence",
                        "topics/synthetic/subtopics/unit",
                    ),
                    writable_roots=(
                        READ_PATH,
                        "learner/knowledge/new-domain.yaml",
                        "evidence",
                        CHECKPOINT_PATH,
                    ),
                ),
                write_admission=DeploymentWriteGate(),
                expected_generation=3,
            )
            try:
                consumer_session_ids.append(consumer._session.session_id)
                result = consumer.invoke({
                    "operation": "read_learning_context",
                    "arguments": {
                        "required_paths": [CHECKPOINT_PATH, READ_PATH],
                    },
                })
                self.assertTrue(result["ok"])
                recovered.append(result["result"]["documents"])
            finally:
                consumer.close()

        self.assertNotIn(producer_session_id, consumer_session_ids)
        self.assertEqual(2, len(set(consumer_session_ids)))
        self.assertEqual(recovered[0], recovered[1])

        recovered_by_path = {
            item["path"]: item
            for item in recovered[0]
        }
        progress = yaml.safe_load(
            recovered_by_path[CHECKPOINT_PATH]["content"]
        )
        knowledge = yaml.safe_load(
            recovered_by_path[READ_PATH]["content"]
        )
        self.assertEqual(
            checkpoint["milestone"], progress["current"]["milestone"]
        )
        self.assertEqual(
            checkpoint["return_point"], progress["resume"]["return_point"]
        )
        self.assertEqual(
            checkpoint["ready_next"], progress["resume"]["ready_next"]
        )
        self.assertEqual(
            [KNOWLEDGE_EVIDENCE_ID],
            knowledge["concepts"]["token-identity"]["capabilities"]
            ["explanation"]["evidence_refs"]["support"],
        )

    def test_reference_host_can_first_materialize_knowledge_owner(self):
        evidence_id = "evi-reference-host-new-domain-001"
        evidence = typed_evidence(
            evidence_id=evidence_id,
            domain="new-domain",
        )
        created = self.invoke("create_evidence", content=evidence)
        self.assertTrue(created["result"]["applied"])

        candidate = knowledge_candidate(
            domain="new-domain",
            revision=1,
            evidence_id=evidence_id,
        )
        reconciled = self.invoke(
            "reconcile_knowledge",
            content=candidate,
            expected_version_token=None,
        )
        self.assertTrue(reconciled["ok"])
        self.assertEqual(
            candidate,
            self.provider.docs["learner/knowledge/new-domain.yaml"],
        )

    def test_same_evidence_retry_returns_idempotent_ack(self):
        evidence = typed_evidence(
            evidence_id=KNOWLEDGE_EVIDENCE_ID,
        )
        first = self.invoke("create_evidence", content=evidence)
        second = self.invoke("create_evidence", content=evidence)
        self.assertTrue(first["result"]["applied"])
        self.assertFalse(second["result"]["applied"])

    def test_cas_conflict_is_visible_without_internal_error_text(self):
        candidate = knowledge_candidate()
        result = self.invoke(
            "reconcile_knowledge",
            content=candidate,
            expected_version_token="0" * 40,
        )
        self.assertFalse(result["ok"])
        self.assertEqual("cas_conflict", result["error"]["code"])
        self.assertTrue(result["error"]["retryable"])
        rendered = json.dumps(result)
        self.assertNotIn("compare-and-swap", rendered)
        self.assertNotIn(str(self.provider.instance), rendered)

    def test_request_shape_is_strict_and_host_generates_write_messages(self):
        result = self.host.invoke({
            "operation": "create_evidence",
            "arguments": {
                "content": typed_evidence(),
                "message": "model-controlled commit message",
            },
        })
        self.assertFalse(result["ok"])
        self.assertEqual(
            "resolution_failed", result["error"]["code"]
        )
        self.assertFalse(
            any(call[0] == "create" for call in self.provider.calls)
        )

    def test_result_does_not_expose_session_or_trusted_locator(self):
        result = self.invoke(
            "read_learning_context",
            required_paths=[READ_PATH],
        )
        rendered = json.dumps(result)
        self.assertNotIn(self.host._session.session_id, rendered)
        locator_data = yaml.safe_dump(locator(), sort_keys=True)
        self.assertNotIn(locator_data, rendered)

    def test_closed_host_fails_closed(self):
        self.host.close()
        result = self.invoke(
            "read_learning_context",
            required_paths=[READ_PATH],
        )
        self.assertFalse(result["ok"])
        self.assertEqual("guard_rejected", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
