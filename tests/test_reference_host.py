import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.reference_host import (
    REFERENCE_HOST_OPERATIONS,
    ReferenceLearningHost,
)
from scripts.runtime_broker import (
    DeploymentWriteGate,
    RuntimeCapabilityPolicy,
)
from tests.test_runtime_broker import (
    BrokerProvider,
    KNOWLEDGE_EVIDENCE_ID,
    READ_PATH,
    RUNTIME_PATH,
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
        self.host = ReferenceLearningHost.open(
            provider=self.provider,
            locator_source=locator(),
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(
                readable_roots=("learner", "evidence"),
                writable_roots=(
                    READ_PATH,
                    "learner/knowledge/new-domain.yaml",
                    "evidence",
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

    def test_surface_is_narrow_and_excludes_generic_or_continuity_writes(self):
        self.assertEqual(
            {
                "read_learning_context",
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
                "surface_version": "v1",
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
