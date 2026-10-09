"""Storage/gate evidence for prose-linked correction, separate from model quality."""
import copy
import hashlib
import json
import unittest
from unittest import mock

import yaml

from tests.host_replay_compatibility import assert_v3_replay

from tests.correction_propagation_fixture import (
    CorrectionJourney, PERFORMANCE_ID, PERFORMANCE_PATH, REPORT_ID, REPORT_PATH,
    ROOT, READ_PATH, digest, initial_knowledge, packet, performance, report,
)

FIXTURES = ROOT / "tests/fixtures/core34-correction"


class CorrectionPropagationTests(unittest.TestCase):
    def test_producer_cut_really_leaves_knowledge_stale(self):
        with CorrectionJourney() as journey:
            self.assertEqual(initial_knowledge(), journey.provider.docs[READ_PATH])
            self.assertEqual(performance(), journey.provider.docs[PERFORMANCE_PATH])
            self.assertEqual(report(), journey.provider.docs[REPORT_PATH])
            claim = yaml.safe_load(journey.provider.docs[READ_PATH])["concepts"]["token-identity"]["capabilities"]["explanation"]
            self.assertIn("appeared unassisted", claim["basis_summary"])
            self.assertNotIn(REPORT_ID, claim["basis_summary"])

    def test_distinct_report_is_not_a_second_performance(self):
        original, later = yaml.safe_load(performance()), yaml.safe_load(report())
        self.assertEqual("task_response", original["observation"]["kind"])
        self.assertEqual("learner_self_report", later["observation"]["kind"])
        self.assertEqual("neutral", later["interpretation"]["direction"])
        self.assertIn(PERFORMANCE_ID, later["observation"]["summary"])
        self.assertNotEqual(original["source"]["round_id"], later["source"]["round_id"])
        self.assertNotEqual(original["observed_at"], later["observed_at"])
        self.assertEqual(2, len(original["targets"]))
        self.assertEqual(1, len(later["targets"]))
        self.assertEqual("explanation", later["targets"][0]["capability"])

    def test_fresh_recovery_is_read_only_and_same_snapshot(self):
        with CorrectionJourney() as journey:
            journey.provider.calls.clear()
            first, second = journey.recover(), journey.recover()
            self.assertEqual(first, second)
            self.assertEqual({READ_PATH, PERFORMANCE_PATH, REPORT_PATH}, {row["path"] for row in first})
            self.assertEqual(journey.before, journey.provider.docs)
            self.assertEqual(journey.before_blobs, journey.provider.blobs)
            self.assertFalse(any(c[0] in {"create", "update"} for c in journey.provider.calls))
            snapshots = {c[2] for c in journey.provider.calls if c[0] == "snapshot_read"}
            self.assertEqual({journey.provider.instance_head}, snapshots)
            self.assertNotEqual(journey.producer_id, journey.hosts[-1]._session.session_id)

    def test_immutable_original_rewrite_rejected_and_report_retry_noop(self):
        with CorrectionJourney() as journey:
            before = copy.deepcopy(journey.provider.docs)
            altered = yaml.safe_load(performance())
            altered["observation"]["summary"] = "rewritten history"
            response = journey.apply({"operation": "create_evidence", "arguments": {"content": yaml.safe_dump(altered)}})
            self.assertFalse(response["ok"])
            retry = journey.apply({"operation": "create_evidence", "arguments": {"content": report()}})
            self.assertTrue(retry["ok"], retry)
            self.assertFalse(retry["result"]["applied"])
            self.assertEqual(before, journey.provider.docs)

    def test_neutral_report_cannot_be_invented_as_support_or_challenge(self):
        for side in ("support", "challenge"):
            with self.subTest(side=side), CorrectionJourney() as journey:
                docs = journey.recover()
                token = next(d["version_token"] for d in docs if d["path"] == READ_PATH)
                value = yaml.safe_load(initial_knowledge())
                value["revision"] += 1
                value["concepts"]["token-identity"]["capabilities"]["explanation"]["evidence_refs"][side].append(REPORT_ID)
                response = journey.apply({"operation": "reconcile_knowledge", "arguments": {"content": yaml.safe_dump(value), "expected_version_token": token}})
                self.assertFalse(response["ok"])
                self.assertEqual(journey.before, journey.provider.docs)

    def test_stale_knowledge_token_does_not_apply(self):
        with CorrectionJourney() as journey:
            value = yaml.safe_load(initial_knowledge())
            value["revision"] += 1
            response = journey.apply({"operation": "reconcile_knowledge", "arguments": {"content": yaml.safe_dump(value), "expected_version_token": "stale-token"}})
            self.assertFalse(response["ok"])
            self.assertEqual("cas_conflict", response["error"]["code"])
            self.assertEqual(journey.before, journey.provider.docs)

    def test_consumer_read_capability_cannot_apply_reconciliation(self):
        with CorrectionJourney() as journey:
            host = journey.open()
            response = host.invoke({"operation": "reconcile_knowledge", "arguments": {"content": initial_knowledge(), "expected_version_token": journey.provider.blobs[READ_PATH]}})
            self.assertFalse(response["ok"])
            self.assertEqual(journey.before, journey.provider.docs)

    def test_packet_freeze_is_reproducible_and_does_not_contain_oracle(self):
        manifest = json.loads((FIXTURES / "prospective-manifest.json").read_text(encoding="utf-8"))
        for item in manifest["consumers"]:
            value = packet(item["arm"])
            self.assertEqual(item["packet_hash"], digest(value))
            raw = (FIXTURES / (item["id"] + "-packet.json")).read_bytes()
            self.assertEqual(item["packet_bytes_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(value, json.loads(raw))
            self.assertEqual({"recipe_version", "request", "policy", "context", "host_interface", "output_contract"}, set(value))
            self.assertNotIn("criteria", value)
            self.assertNotIn("accepted", value)

    def test_ablation_removes_only_the_report_from_selected_documents(self):
        original = packet("structured")
        ablated = packet("ablated")
        before = {d["path"]: d for d in original["context"]}
        after = {d["path"]: d for d in ablated["context"]}
        self.assertEqual({REPORT_PATH}, set(before) - set(after))
        for path, row in after.items():
            self.assertEqual(before[path], row)
        for field in ("request", "policy", "host_interface", "output_contract"):
            self.assertEqual(original[field], ablated[field])

    def test_frozen_first_requests_replay_without_repair(self):
        rows = json.loads((FIXTURES / "mechanical-replay.json").read_text(encoding="utf-8"))
        for row in rows:
            with self.subTest(consumer=row["id"]), CorrectionJourney(include_report=row["id"] != "c03") as journey:
                raw = (FIXTURES / (row["id"] + "-response.json")).read_bytes()
                self.assertEqual(row["response_bytes_sha256"], hashlib.sha256(raw).hexdigest())
                response = json.loads(raw)
                results = [journey.apply(request) for request in response["host_requests"]]
                assert_v3_replay(self, row["host_results"], results)
                self.assertTrue(all(result["ok"] for result in results))
                self.assertEqual(row["result_documents"], journey.recover())
                self.assertEqual(journey.before[PERFORMANCE_PATH], journey.provider.docs[PERFORMANCE_PATH])
                self.assertEqual(journey.before.get(REPORT_PATH), journey.provider.docs.get(REPORT_PATH))
                self.assertEqual({p for p in journey.before if p.startswith("evidence/")},
                                 {p for p in journey.provider.docs if p.startswith("evidence/")})
                # Host admission above is not semantic acceptance: c02 retains
                # the explicitly unresolved unknown/unsupported disposition.

    def test_successor_packet_is_actual_persisted_result_only(self):
        rows = json.loads((FIXTURES / "mechanical-replay.json").read_text(encoding="utf-8"))
        first = next(row for row in rows if row["id"] == "c01")
        raw = (FIXTURES / "c04-packet.json").read_bytes()
        self.assertEqual(first["fresh_successor_packet_sha256"], hashlib.sha256(raw).hexdigest())
        successor = json.loads(raw)
        self.assertEqual(first["fresh_successor_packet_hash"], digest(successor))
        self.assertEqual(first["result_documents"], successor["context"])
        original = packet("structured")
        for key in original.keys() - {"context"}:
            self.assertEqual(original[key], successor[key])
        self.assertNotIn("host_requests", successor)
        receipt = json.loads((FIXTURES / "successor-receipt.json").read_text(encoding="utf-8"))
        response = (FIXTURES / "c04-response.json").read_bytes()
        self.assertEqual(receipt["response_bytes_sha256"], hashlib.sha256(response).hexdigest())
        self.assertEqual([], json.loads(response)["host_requests"])

    def test_simulated_external_blob_drift_rejects_an_old_packet_request(self):
        with CorrectionJourney() as journey:
            response = json.loads((FIXTURES / "c01-response.json").read_text(encoding="utf-8"))
            # BrokerProvider intentionally uses fixed synthetic tokens. Model
            # a later content/token change explicitly for this control. This
            # does not establish Git object fidelity or concurrent-write proof.
            current = yaml.safe_load(journey.provider.docs[READ_PATH])
            current["revision"] += 1
            current["updated_at"] = "2026-10-07T01:47:00Z"
            text = yaml.safe_dump(current)
            journey.provider.docs[READ_PATH] = text
            journey.provider.blobs[READ_PATH] = hashlib.sha1(text.encode()).hexdigest()
            before = copy.deepcopy(journey.provider.docs)
            result = journey.apply(response["host_requests"][0])
            self.assertFalse(result["ok"])
            self.assertEqual("cas_conflict", result["error"]["code"])
            self.assertEqual(before, journey.provider.docs)

    def test_selection_rejects_final_read_token_drift(self):
        with CorrectionJourney() as journey:
            original = journey.invoke
            read_count = 0

            def changed_read(host, operation, **arguments):
                nonlocal read_count
                result = original(host, operation, **arguments)
                if operation == "read_learning_context":
                    read_count += 1
                    if read_count == 2:
                        result = copy.deepcopy(result)
                        result["documents"][-1]["version_token"] = "changed-snapshot-token"
                return result

            with mock.patch.object(journey, "invoke", side_effect=changed_read):
                with self.assertRaisesRegex(AssertionError, "Selected state changed"):
                    journey.recover()
            self.assertEqual(journey.before, journey.provider.docs)


if __name__ == "__main__":
    unittest.main()
