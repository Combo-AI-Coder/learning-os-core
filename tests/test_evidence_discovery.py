"""Synthetic P2 interruption: discover durable Evidence without producer IDs."""
import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import GuardRejected, ResolutionError
from scripts.runtime_broker import DeploymentWriteGate, LearningRuntimeSession, RuntimeCapabilityPolicy
import scripts.runtime_broker as broker_module
from tests.test_runtime_broker import (
    BrokerProvider, INSTANCE_ID, READ_PATH, RUNTIME_PATH, REGISTRY_PATH,
    knowledge_candidate, locator, typed_evidence, contract,
)


def observation(evidence_id="evi-interrupted-001", **changes):
    value = yaml.safe_load(typed_evidence(evidence_id=evidence_id))
    value["context"] = {"topic": "synthetic", "subtopic": "unit"}
    value.update(changes)
    return yaml.safe_dump(value, sort_keys=False)


class EvidenceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "control").mkdir()
        (root / "instance").mkdir()
        self.provider = BrokerProvider(root / "control", root / "instance")
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.gate = DeploymentWriteGate()
        self.host = self.open()

    def open(self, *, reads=("learner/knowledge", "evidence"), writes=()):
        host = ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=reads, writable_roots=writes),
            write_admission=self.gate, expected_generation=3,
        )
        self.addCleanup(host.close)
        return host

    def seed(self, content=None, path=None):
        if content is None:
            content = observation()
        if path is None:
            path = "evidence/" + yaml.safe_load(content)["id"] + ".yaml"
        self.provider.docs[path] = content
        self.provider.blobs[path] = hashlib.sha1(content.encode()).hexdigest()
        self.provider.snapshot_extra_paths.add(path)
        return path

    def discover(self, host=None, **arguments):
        return (host or self.host).invoke({"operation": "discover_learning_evidence", "arguments": arguments})

    def result(self, host=None):
        response = self.discover(host)
        self.assertTrue(response["ok"], response)
        return response["result"]

    def assert_rejected(self, response):
        self.assertFalse(response["ok"], response)
        self.assertNotIn("result", response)
        self.assertIn(response["error"]["code"], ("guard_rejected", "resolution_failed"))
        self.assertFalse(any(call[0] in {"create", "update"} for call in self.provider.calls))

    def test_crash_cut_fresh_consumers_find_durable_evidence_without_id(self):
        producer = self.open(writes=("evidence",))
        saved = producer.invoke({"operation": "create_evidence", "arguments": {"content": observation()}})
        self.assertTrue(saved["ok"], saved)
        producer.close()  # Frozen cut: Evidence persisted; no Knowledge reconciliation.
        expected = copy.deepcopy(self.provider.docs)
        expected_blobs = copy.deepcopy(self.provider.blobs)
        self.provider.calls.clear()
        self.assert_rejected(self.discover(producer))
        first, second = self.open(), self.open()
        self.assertNotEqual(first._session.session_id, second._session.session_id)
        recovered = self.result(first)
        self.assertEqual(recovered, self.result(first))
        self.assertEqual(recovered, self.result(second))
        self.assertEqual(1, len(recovered["evidence"]))
        row = recovered["evidence"][0]
        self.assertEqual(observation(), row["content"])
        self.assertEqual("not_referenced_by_current_knowledge", row["knowledge_references"][0]["reference_status"])
        self.assertEqual("present", recovered["knowledge_owners"][0]["state"])
        self.assertEqual(expected, self.provider.docs)
        self.assertEqual(expected_blobs, self.provider.blobs)
        self.assertFalse(any(c[0] in {"create", "update"} for c in self.provider.calls))
        text = json.dumps(recovered)
        for forbidden in ("unprocessed", "instance_commit", "session_id", "processed", "mastery"):
            self.assertNotIn(forbidden, text)
        snapshots = {c[2] for c in self.provider.calls if c[0] == "snapshot_read" and c[1] == INSTANCE_ID}
        self.assertEqual({self.provider.instance_head}, snapshots)

    def test_reference_status_is_per_target_not_per_observation(self):
        value = yaml.safe_load(observation())
        value["targets"].append(dict(value["targets"][0], capability="derivation"))
        value["targets"].append(copy.deepcopy(value["targets"][0]))
        self.seed(yaml.safe_dump(value))
        self.seed(knowledge_candidate(evidence_id=value["id"]), READ_PATH)
        result = self.result()
        self.assertEqual(1, len(result["evidence"]))
        refs = result["evidence"][0]["knowledge_references"]
        self.assertEqual(2, len(refs))
        self.assertEqual(["referenced", "not_referenced_by_current_knowledge"], [r["reference_status"] for r in refs])
        self.assertEqual(["support"], refs[0]["sides"])
        self.assertEqual([], refs[1]["sides"])

    def test_target_metadata_is_preserved_without_new_identity(self):
        value = yaml.safe_load(observation())
        value["targets"][0]["conditions"] = "Only when using a hint"
        content = yaml.safe_dump(value, sort_keys=False)
        producer = self.open(writes=("evidence",))
        saved = producer.invoke({"operation": "create_evidence", "arguments": {"content": content}})
        self.assertTrue(saved["ok"], saved)
        producer.close()
        self.provider.calls.clear()
        result = self.result()
        self.assertEqual(content, result["evidence"][0]["content"])
        self.assertEqual(1, len(result["evidence"][0]["knowledge_references"]))
        self.assertFalse(any(c[0] in {"create", "update"} for c in self.provider.calls))

    def test_neutral_deferred_or_conflicting_references_are_not_actions(self):
        for direction in ("neutral", "deferred", "challenge"):
            value = yaml.safe_load(observation())
            value["interpretation"]["direction"] = direction
            self.seed(yaml.safe_dump(value))
            knowledge = yaml.safe_load(knowledge_candidate(evidence_id=value["id"]))
            claim = knowledge["concepts"]["token-identity"]["capabilities"]["explanation"]
            claim["evidence_refs"]["challenge"] = [value["id"]]
            self.seed(yaml.safe_dump(knowledge), READ_PATH)
            result = self.result()
            self.assertEqual(["support", "challenge"], result["evidence"][0]["knowledge_references"][0]["sides"])
            self.assertEqual("referenced", result["evidence"][0]["knowledge_references"][0]["reference_status"])

    def test_absent_owner_is_explicit_not_denied_or_unprocessed(self):
        self.seed()
        del self.provider.docs[READ_PATH]
        del self.provider.blobs[READ_PATH]
        result = self.result()
        self.assertEqual([{"path": READ_PATH, "state": "absent", "version_token": None}], result["knowledge_owners"])
        self.assertEqual("not_referenced_by_current_knowledge", result["evidence"][0]["knowledge_references"][0]["reference_status"])

    def test_unrelated_and_context_free_records_are_not_returned(self):
        matching = self.seed()
        self.seed(observation("evi-other-topic", context={"topic": "another", "subtopic": "unit"}))
        self.seed(observation("evi-other-subtopic", context={"topic": "synthetic", "subtopic": "other"}))
        self.seed(typed_evidence(evidence_id="evi-legacy"))
        self.assertEqual([matching], [r["path"] for r in self.result()["evidence"]])

    def test_history_is_not_revalidated_as_a_new_observation(self):
        for observed_at in (None, "2026-10-05T12:00:00", "legacy timestamp"):
            with self.subTest(time=observed_at):
                original = observation(observed_at=observed_at, observation=None)
                self.seed(original)
                self.assertEqual(original, self.result()["evidence"][0]["content"])

    def test_empty_result_and_path_order_do_not_claim_chronology(self):
        self.assertEqual([], self.result()["evidence"])
        self.seed(observation("evi-z", observed_at="2026-10-04T00:00:00Z"))
        self.seed(observation("evi-a", observed_at="2026-10-06T00:00:00"))
        result = self.result()
        self.assertEqual(["evidence/evi-a.yaml", "evidence/evi-z.yaml"], [r["path"] for r in result["evidence"]])
        self.assertEqual("all_context_matches_in_bounded_snapshot", result["coverage"])

    def test_no_model_paths_ids_scope_or_limit_override(self):
        self.provider.calls.clear()
        for field in ("evidence_id", "required_paths", "topic", "subtopic", "limit", "repository_id", "since"):
            self.assert_rejected(self.discover(**{field: "override"}))
        self.assertEqual([], self.provider.calls)

    def test_root_authorization_and_main_binding_required_before_scanning(self):
        for reads in (("learner",), ("evidence/evi-interrupted-001.yaml", "learner")):
            host = self.open(reads=reads)
            self.provider.calls.clear()
            self.assert_rejected(self.discover(host))
            self.assertEqual([], self.provider.calls)
        for role, subtopic in (("practice", "unit"), ("main", None)):
            self.provider.set_branch_registry(role=role, subtopic=subtopic)
            host = self.open()
            self.provider.calls.clear()
            self.assert_rejected(self.discover(host))
            self.assertEqual([], self.provider.calls)

    def test_knowledge_denial_does_not_become_missing(self):
        self.seed()
        host = self.open(reads=("evidence",))
        self.provider.calls.clear()
        self.assert_rejected(self.discover(host))
        self.assertFalse(any(c[0] == "snapshot_read" and c[-1] == READ_PATH for c in self.provider.calls))

    def test_candidate_limit_rejects_before_evidence_content_reads(self):
        for i in range(broker_module.EVIDENCE_DISCOVERY_MAX_CANDIDATES):
            self.seed(observation(f"evi-{i:03}", context=None))
        self.assertEqual([], self.result()["evidence"])
        self.seed(observation("evi-over", context=None))
        self.provider.calls.clear()
        self.assert_rejected(self.discover())
        self.assertFalse(any(c[0] == "snapshot_read" and c[-1].startswith("evidence/") for c in self.provider.calls))

    def test_matching_record_limit_has_no_partial_success(self):
        for i in range(broker_module.EVIDENCE_DISCOVERY_MAX_RECORDS):
            self.seed(observation(f"evi-{i:03}"))
        self.assertEqual(broker_module.EVIDENCE_DISCOVERY_MAX_RECORDS, len(self.result()["evidence"]))
        self.seed(observation("evi-over"))
        self.assert_rejected(self.discover())

    def test_per_record_and_total_target_budgets(self):
        value = yaml.safe_load(observation())
        base = value["targets"][0]
        value["targets"] = [dict(base, capability=f"cap-{i}") for i in range(broker_module.EVIDENCE_DISCOVERY_MAX_TARGETS_PER_RECORD)]
        self.seed(yaml.safe_dump(value))
        self.assertEqual(8, len(self.result()["evidence"][0]["knowledge_references"]))
        value["targets"].append(dict(base, capability="over"))
        self.seed(yaml.safe_dump(value))
        self.assert_rejected(self.discover())
        value["targets"].pop()
        for i in range(8):
            value["id"] = f"evi-{i:03}"
            self.seed(yaml.safe_dump(value))
        self.provider.docs.pop("evidence/evi-interrupted-001.yaml")
        self.provider.blobs.pop("evidence/evi-interrupted-001.yaml")
        self.assertEqual(8, len(self.result()["evidence"]))
        value["id"] = "evi-over"
        self.seed(yaml.safe_dump(value))
        self.assert_rejected(self.discover())

    def test_knowledge_owner_budget(self):
        for i in range(2):
            value = yaml.safe_load(observation(f"evi-{i}"))
            base = value["targets"][0]
            value["targets"] = [dict(base, domain=f"domain-{j+8*i}") for j in range(8)]
            self.seed(yaml.safe_dump(value))
        self.assertEqual(16, len(self.result()["knowledge_owners"]))
        self.seed(observation("evi-over"))
        self.assert_rejected(self.discover())

    def test_document_and_total_byte_boundaries_include_unrelated_records(self):
        content = observation(context=None)
        exact = content + "#" + " " * (broker_module.EVIDENCE_DISCOVERY_MAX_DOCUMENT_BYTES - len(content.encode()) - 1)
        self.seed(exact)
        self.assertTrue(self.discover()["ok"])
        self.seed(exact + " ")
        self.assert_rejected(self.discover())
        self.seed(exact)
        for i in range(3):
            value = observation(f"evi-other-{i}", context=None)
            self.seed(value + "#" + " " * (broker_module.EVIDENCE_DISCOVERY_MAX_DOCUMENT_BYTES - len(value.encode()) - 1))
        self.assertTrue(self.discover()["ok"])
        self.seed(observation("evi-over", context=None))
        self.assert_rejected(self.discover())

    def test_knowledge_bytes_share_budget_and_no_partial_result(self):
        self.seed()
        used = len(observation().encode()) + len(self.provider.docs[READ_PATH].encode())
        with mock.patch.object(broker_module, "EVIDENCE_DISCOVERY_MAX_TOTAL_BYTES", used):
            self.assertTrue(self.discover()["ok"])
        with mock.patch.object(broker_module, "EVIDENCE_DISCOVERY_MAX_TOTAL_BYTES", used - 1):
            self.assert_rejected(self.discover())

    def test_multibyte_content_uses_byte_not_character_budget(self):
        content = observation() + "#" + "界" * 22000
        self.seed(content)
        self.assertLess(len(content), broker_module.EVIDENCE_DISCOVERY_MAX_DOCUMENT_BYTES)
        self.assert_rejected(self.discover())

    def test_malformed_evidence_fails_closed(self):
        path = "evidence/evi-interrupted-001.yaml"
        for changes in ({"id": "wrong"}, {"id": {"id": "evi-interrupted-001"}}, {"document_type": "learner_model"}, {"context": []}, {"context": {"topic": [], "subtopic": "unit"}},
                        {"interpretation": []}, {"interpretation": {"direction": "garbage"}},
                        {"schema_version": "99"}, {"targets": []}, {"targets": "string"},
                        {"targets": ["legacy.node"]}, {"targets": [{"type": "capability"}]},
                        {"targets": [{"type": "capability", "domain": "../escape", "concept": "x", "capability": "y"}]},
                        {"targets": [{"type": "capability", "domain": "d", "concept": [], "capability": "y"}]},
                        {"credentials": "forbidden"}):
            with self.subTest(changes=changes):
                self.seed(observation(**changes), path)
                self.assert_rejected(self.discover())
        for content in ("[not-mapping]", "bad: [", observation() + "id: duplicate\n", "&anchor {self: *anchor}"):
            self.seed(content, path)
            self.assert_rejected(self.discover())

    def test_malformed_knowledge_and_no_readable_owner_are_not_absence(self):
        self.seed()
        original = knowledge_candidate(evidence_id="evi-interrupted-001")
        for content in ("bad: [", original.replace("domain: synthetic", "domain: wrong"),
                        original.replace("revision: 2", "revision: true"),
                        original.replace("state: provisional", "state: []"),
                        original.replace("confidence: low", "confidence: invalid"),
                        original.replace("support:", "support: invalid\n        ignored:"),
                        original.replace("concepts:", "concepts: null\nignored:")):
            self.seed(content, READ_PATH)
            self.assert_rejected(self.discover())
        self.seed(original, READ_PATH)
        read = self.provider.read_materialized_text
        def failing(snapshot, path):
            if path == READ_PATH:
                raise ResolutionError("private provider detail")
            return read(snapshot, path)
        with mock.patch.object(self.provider, "read_materialized_text", side_effect=failing):
            response = self.discover()
            self.assert_rejected(response)
            self.assertNotIn("private", json.dumps(response))

    def test_missing_inventory_and_noncanonical_evidence_path(self):
        self.seed()
        materialize = self.provider.materialize
        def no_inventory(repository_id, ref):
            snapshot = materialize(repository_id, ref)
            return replace(snapshot, blob_shas=None, paths=None) if repository_id == INSTANCE_ID else snapshot
        with mock.patch.object(self.provider, "materialize", side_effect=no_inventory):
            self.assert_rejected(self.discover())
        self.seed(observation(), "evidence/nested/evi-interrupted-001.yaml")
        self.assert_rejected(self.discover())

    def test_revoked_and_forged_capability(self):
        self.host.close()
        self.assert_rejected(self.discover())
        with self.assertRaises(GuardRejected):
            self.host._broker.discover_learning_evidence(LearningRuntimeSession("forged"))

    def test_instance_and_deployment_drift_discard_results(self):
        self.seed()
        for drift in ("head", "generation", "deployment", "binding"):
            with self.subTest(drift=drift):
                read = self.provider.read_materialized_text
                def drifting(snapshot, path):
                    value = read(snapshot, path)
                    if path == READ_PATH:
                        if drift == "head":
                            self.provider.instance_head = "9" * 40
                        elif drift == "generation":
                            self.provider.set_generation(4)
                            self.provider.instance_head = "8" * 40
                        elif drift == "deployment":
                            self.provider.contract = contract(epoch=2)
                        else:
                            self.provider.set_branch_registry(subtopic="another")
                            self.provider.instance_head = "7" * 40
                    return value
                with mock.patch.object(self.provider, "read_materialized_text", side_effect=drifting):
                    self.assert_rejected(self.discover())
                # Reset an independent fixture for the next adversarial drift.
                self.setUp()
                self.seed()

    def test_every_data_read_uses_one_snapshot_and_requires_admission(self):
        self.seed()
        read = self.provider.read_materialized_text
        snapshots = []
        def capture(snapshot, path):
            snapshots.append(snapshot)
            return read(snapshot, path)
        with mock.patch.object(self.provider, "read_materialized_text", side_effect=capture):
            self.result()
        self.assertEqual(2, len(snapshots))
        self.assertIs(snapshots[0], snapshots[1])
        self.host._broker.write_admission = None
        self.provider.calls.clear()
        self.assert_rejected(self.discover())
        self.assertEqual([], self.provider.calls)

    def test_stale_generation_and_stale_binding_rejected_on_entry(self):
        self.seed()
        self.provider.set_generation(4)
        self.assert_rejected(self.discover())
        self.provider.set_generation(3)
        self.provider.set_branch_registry(subtopic="another")
        self.assert_rejected(self.discover())

    def test_cleanup_failure_discards_success(self):
        self.seed()
        release = self.provider.release_materialization
        def fail(snapshot):
            release(snapshot)
            if snapshot.repository_id == INSTANCE_ID:
                raise ResolutionError("cleanup failed")
        with mock.patch.object(self.provider, "release_materialization", side_effect=fail):
            self.assert_rejected(self.discover())

    def test_read_only_surface_cannot_write_recovered_observation(self):
        self.seed()
        self.result()
        self.provider.calls.clear()
        for operation, arguments in (("create_evidence", {"content": observation("evi-new")}),
                                     ("reconcile_knowledge", {"content": knowledge_candidate(), "expected_version_token": self.provider.blobs[READ_PATH]})):
            self.assert_rejected(self.host.invoke({"operation": operation, "arguments": arguments}))


if __name__ == "__main__":
    unittest.main()
