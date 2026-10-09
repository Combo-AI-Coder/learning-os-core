"""Diagnostic-design gates do not replace semantic review of the question."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock
import yaml

from tests.diagnostic_repair_fixture import grade_repair, assess_repair, repair_packet, repair_packet_with_referenced_evidence, prospective_packet, prospective_spec
from tests.state_sensitivity_fixture import ROOT, READ_PATH, SyntheticJourney, cases, packet, packet_hash


def probe():
    return {
        "action": "minimal_probe", "target": "token-identity", "scaffold": "none",
        "diagnostic_gate": True, "claim": "no_new_claim", "sources": [READ_PATH],
        "next_step": "For the same vocabulary token in two different contexts, must its contextual representation be identical? Explain whether the shared token ID is sufficient to decide.",
        "basis": "The stored independent explanations contain unresolved support and challenge for this relationship.",
        "diagnostic_design": {
            "target_claim": "Whether identical token IDs require identical contextual representations.",
            "rival_answer": "Yes: the same ID means the representation must be identical.",
            "distinguishing_response_feature": "An explanation distinguishes fixed vocabulary identity from a context-dependent representation; merely stating that IDs are fixed is insufficient.",
            "contingent_next_actions": {
                "target_demonstrated": "Continue the planned context-sensitive application.",
                "alternative_or_unresolved": "Supply the missing distinction or retain uncertainty if the explanation is ambiguous.",
            },
        },
    }


def review_for(response):
    return {"run_id": "test", "packet_sha256": "a" * 64, "response_sha256": packet_hash(response),
            "reviewer": "synthetic-test-reviewer", "rationale": "Reviewed the actual question and rival answer.",
            **{key: True for key in ("next_step_matches_action", "grounded_basis", "appropriate_target",
                "no_unsupported_claims", "required_behavior_observed", "question_requires_target",
                "rival_cannot_earn_full_credit", "answer_not_supplied", "design_matches_actual_question")}}


class DiagnosticRepairTests(unittest.TestCase):
    def setUp(self):
        self.spec = cases()["necessary_conflict"]

    def test_design_record_is_required_but_not_sufficient(self):
        value = probe()
        self.assertEqual([], grade_repair(self.spec, value))
        self.assertEqual("not_established", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64)["behavioral_acceptance"])
        del value["diagnostic_design"]
        self.assertIn("missing_diagnostic_design_field", grade_repair(self.spec, value))

    def test_question_and_rival_require_bound_semantic_review(self):
        value = probe(); review = review_for(value)
        self.assertEqual("bounded_pass", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64, semantic_review=review)["behavioral_acceptance"])
        for key in ("question_requires_target", "rival_cannot_earn_full_credit", "answer_not_supplied", "design_matches_actual_question"):
            for invalid in (False, "true", 1, None):
                changed = dict(review, **{key: invalid})
                self.assertEqual("not_established", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64, semantic_review=changed)["behavioral_acceptance"])

    def test_shared_prerequisite_cannot_be_rescued_by_design_declaration(self):
        value = probe(); value["next_step"] = "Does the vocabulary token ID stay fixed in two different contexts?"
        self.assertEqual([], grade_repair(self.spec, value))
        review = review_for(value)
        review.update(question_requires_target=False, rival_cannot_earn_full_credit=False,
                      design_matches_actual_question=False,
                      rationale="Both the target and the representation-confusion alternative can answer that the ID stays fixed.")
        self.assertEqual("not_established", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64, semantic_review=review)["behavioral_acceptance"])

    def test_answer_leakage_and_application_only_are_not_diagnostic_passes(self):
        examples = [
            ("The token ID stays fixed while the contextual representation can vary. Repeat that distinction.", "answer_not_supplied"),
            ("Suggest a likely next word after each sentence prefix.", "question_requires_target"),
        ]
        for question, failed_check in examples:
            value = probe();value["next_step"] = question
            review = review_for(value);review[failed_check] = False
            self.assertEqual("not_established", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64, semantic_review=review)["behavioral_acceptance"])

    def test_nonprobe_does_not_require_or_allow_fabricated_design(self):
        value = probe();value.update(action="continue",diagnostic_gate=False,diagnostic_design=None)
        self.assertEqual([], grade_repair(cases()["supported"], value))
        value["diagnostic_design"] = probe()["diagnostic_design"]
        self.assertIn("unrequested_diagnostic_design", grade_repair(cases()["supported"], value))

    def test_new_contract_does_not_import_legacy_action_implications(self):
        value = probe();value["diagnostic_gate"] = False
        self.assertEqual([], grade_repair(self.spec, value))
        self.assertIn("inconsistent_diagnostic_gate", grade_repair(self.spec, value, legacy_action_implications=True))
        value.update(action="continue",diagnostic_design=None,scaffold="brief_bridge")
        self.assertEqual([], grade_repair(cases()["supported"], value))
        self.assertIn("unnecessary_scaffold_change", grade_repair(cases()["supported"], value, legacy_action_implications=True))
        value["diagnostic_gate"] = 0
        self.assertIn("inconsistent_diagnostic_gate", grade_repair(cases()["supported"], value))

    def test_design_shapes_and_raw_length_limits(self):
        for malformed in (None, {}, [], "valid", 1):
            value = probe();value["diagnostic_design"] = malformed
            self.assertTrue(grade_repair(self.spec, value))
        for key in ("target_claim", "rival_answer", "distinguishing_response_feature"):
            for malformed in (None, [], {}, True, " ", "x" + " " * 400):
                value = probe();value["diagnostic_design"][key] = malformed
                self.assertTrue(grade_repair(self.spec, value))
        value = probe();value["diagnostic_design"]["contingent_next_actions"]["target_demonstrated"] = "x" * 301
        self.assertTrue(grade_repair(self.spec, value))

    def test_malformed_responses_do_not_crash_assessment(self):
        for value in (None, [], {}, "probe", True):
            actual = assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64)
            self.assertEqual("not_established", actual["behavioral_acceptance"])
        value = probe();value["diagnostic_design"]["rival_answer"] = "\ud800"
        self.assertIn("invalid_response_encoding", grade_repair(self.spec, value))
        self.assertEqual("not_established", assess_repair(self.spec, value, run_id="test", input_sha256="a" * 64)["behavioral_acceptance"])

    def test_review_cannot_transfer_to_revised_question(self):
        value = probe();review = review_for(value)
        changed = copy.deepcopy(value);changed["next_step"] = "Does the ID stay fixed?"
        actual = assess_repair(self.spec, changed, run_id="test", input_sha256="a" * 64, semantic_review=review)
        self.assertFalse(actual["semantic_review_bound"])
        self.assertEqual("not_established", actual["behavioral_acceptance"])

    def test_old_policy_bytes_preserve_historical_inputs(self):
        old = ROOT / "tests/fixtures/core34-policy-history/teaching-decision-v0.3.md"
        self.assertEqual("780639c5207b19ff9ceca294bfb526d018537773235f1cb8850b62826aca1d48", hashlib.sha256(old.read_bytes()).hexdigest())
        value = packet(cases()["supported"], iteration="application_v2")
        self.assertIn('version: "0.3"', value["policy"])
        self.assertNotIn("### Target-discriminating check", value["policy"])
        repaired = repair_packet(value)
        # This helper prepares a current candidate; only the original v0.3
        # input above is historical. Later compatible owner revisions are valid.
        self.assertEqual((ROOT / "protocol/teaching-decision.md").read_text(encoding="utf-8") + "\n", repaired["policy"])
        self.assertIn("### Target-discriminating check", repaired["policy"])
        self.assertEqual(value["context"], repaired["context"])
        self.assertNotIn("diagnostic_design", value["output_contract"])

    def test_bounded_recipe_recovers_actual_referenced_answers(self):
        value = repair_packet_with_referenced_evidence(self.spec)
        evidence = [item for key, item in value["context"].items() if key.startswith("evidence/")]
        self.assertEqual(2, len(evidence))
        summaries = [item["observation"]["summary"] for item in evidence]
        self.assertTrue(any("identical contextual representations" in text for text in summaries))
        self.assertIn("diagnostic_design", value["output_contract"])

    def test_referenced_context_does_not_reintroduce_historical_policy_cue(self):
        value = repair_packet_with_referenced_evidence(self.spec)
        claim = value["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]
        self.assertNotIn("low burden", claim["basis_summary"])
        self.assertNotIn("question", claim["basis_summary"])
        historical = prospective_packet("q00")
        self.assertIn("low burden", historical["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]["basis_summary"])
        self.assertNotEqual(packet_hash(historical), packet_hash(prospective_packet("q10")))

    def test_nonblocking_basis_annotation_is_not_duplicated(self):
        value = repair_packet_with_referenced_evidence(cases()["nonblocking_unknown"])
        basis = value["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]["basis_summary"]
        self.assertEqual(1, basis.count("Formal derivation has no recorded capability claim"))

    def test_q10_has_separate_honest_reproduction_provenance(self):
        root = ROOT / "tests/fixtures/core34-diagnostic-repair"
        manifest = json.loads((root / "prospective-manifest.json").read_text(encoding="utf-8"))
        row = next(r for r in manifest["runs"] if r["run_id"] == "q10")
        source = root / row["reproduction_exporter_snapshot"]
        self.assertEqual(row["reproduction_exporter_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertIn("not claimed to have been frozen before the run", row["snapshot_provenance"])
        self.assertNotEqual(manifest["exporter_sha256"], row["reproduction_exporter_sha256"])

    def test_heldout_cannot_cite_an_unsupplied_checkpoint(self):
        from tests.state_sensitivity_fixture import CHECKPOINT_PATH
        value = prospective_packet("h04")
        spec = prospective_spec(value, False)
        output = probe();output.update(action="continue",diagnostic_gate=False,diagnostic_design=None,target="fraction_units")
        output["sources"] = [READ_PATH, CHECKPOINT_PATH]
        self.assertNotIn(CHECKPOINT_PATH, value["context"])
        self.assertIn("invalid_or_irrelevant_basis_source", grade_repair(spec, output))

    def test_reference_selector_rejects_path_escape_and_excess_budget(self):
        original = SyntheticJourney.recover
        for refs in (["../other"], [f"evi-extra-{i}" for i in range(9)]):
            def altered(journey):
                documents = original(journey)
                item = next(d for d in documents if d["path"] == READ_PATH)
                value = yaml.safe_load(item["content"])
                value["concepts"]["token-identity"]["capabilities"]["explanation"]["evidence_refs"] = {"support": refs, "challenge": []}
                item["content"] = yaml.safe_dump(value)
                return documents
            with mock.patch.object(SyntheticJourney, "recover", altered), self.assertRaises(ValueError):
                repair_packet_with_referenced_evidence(self.spec)

    def test_reference_selector_rejects_snapshot_drift(self):
        original = SyntheticJourney.recover
        def altered(journey):
            documents = original(journey)
            next(d for d in documents if d["path"] == READ_PATH)["version_token"] = "f" * 40
            return documents
        with mock.patch.object(SyntheticJourney, "recover", altered), self.assertRaisesRegex(ValueError, "context changed"):
            repair_packet_with_referenced_evidence(self.spec)

    def test_reference_selector_rejects_mismatched_capability(self):
        original = SyntheticJourney._invoke
        def altered(host, operation, **arguments):
            result = original(host, operation, **arguments)
            if operation == "read_learning_context" and len(arguments["required_paths"]) > 2:
                evidence = next(d for d in result["documents"] if d["path"].startswith("evidence/"))
                value = yaml.safe_load(evidence["content"])
                value["targets"][0]["concept"] = "unrelated"
                evidence["content"] = yaml.safe_dump(value)
            return result
        with mock.patch.object(SyntheticJourney, "_invoke", staticmethod(altered)), self.assertRaisesRegex(ValueError, "does not match"):
            repair_packet_with_referenced_evidence(self.spec)

    def test_prospective_packets_regenerate_from_frozen_policy_and_facts(self):
        root = ROOT / "tests/fixtures/core34-diagnostic-repair"
        manifest = json.loads((root / "prospective-manifest.json").read_text(encoding="utf-8"))
        for row in manifest["runs"]:
            self.assertEqual(row["packet_sha256"], packet_hash(prospective_packet(row["run_id"])))
        self.assertEqual(manifest["policy_sha256"], hashlib.sha256((root / "teaching-decision-v0.4-candidate.md").read_bytes()).hexdigest())
        self.assertEqual(manifest["exporter_sha256"], hashlib.sha256((root / "candidate2-exporter.py.txt").read_bytes()).hexdigest())

    def test_heldout_files_remain_designer_frozen_bytes(self):
        root = ROOT / "tests/fixtures/core34-diagnostic-repair"
        freeze = json.loads((root / "heldout-freeze.json").read_text(encoding="utf-8"))
        expected = freeze["content_sha256_before_freeze_record"]
        for path in (root / "heldout-cases").glob("*.json"):
            self.assertEqual(expected["cases/" + path.name], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(expected["private/evaluation_oracles.json"], hashlib.sha256((root / "heldout-oracles.json").read_bytes()).hexdigest())

    def test_prospective_outputs_and_bound_reviews_recompute(self):
        root = ROOT / "tests/fixtures/core34-diagnostic-repair"
        data = json.loads((root / "prospective-observations.json").read_text(encoding="utf-8"))
        self.assertEqual(8, len(data["runs"]))
        self.assertEqual(["q10", "q01", "q02", "q03"], data["clean_conditional_runs"])
        for row in data["runs"]:
            value = prospective_packet(row["run_id"])
            spec = prospective_spec(value, row["run_id"].startswith("q"))
            self.assertEqual(row["packet_sha256"], packet_hash(value))
            self.assertEqual(row["response_sha256"], packet_hash(row["response"]))
            self.assertEqual(row["legacy_declared_code_errors"], grade_repair(spec, row["response"], legacy_action_implications=True))
            self.assertEqual(row["task_scoped_declared_code_errors"], grade_repair(spec, row["response"]))
            self.assertEqual(row["assessment"], assess_repair(spec, row["response"], run_id=row["run_id"], input_sha256=row["packet_sha256"], semantic_review=row["semantic_review"]))
        self.assertFalse(next(row for row in data["runs"] if row["run_id"] == "q00")["included_in_clean_result"])

    def test_prior_repair_mismatch_and_unexecuted_cases_are_preserved(self):
        root = ROOT / "tests/fixtures/core34-diagnostic-repair"
        original = json.loads((root / "observations.json").read_text(encoding="utf-8"))
        failed = next(row for row in original["runs"] if row["run_id"] == "r01")
        self.assertEqual("not_established", failed["behavioral_acceptance"])
        self.assertIn("action_outside_acceptable_set", failed["declared_code_errors"])
        manifest = json.loads((root / "prospective-manifest.json").read_text(encoding="utf-8"))
        for row in manifest["runs"]:
            if row["run_id"] in {"h01", "h02", "h03"}:
                self.assertEqual("not_executed_oracle_scope_mismatch", row["execution_status"])

    def test_retained_a09_is_unchanged_and_failed(self):
        records = json.loads((ROOT / "tests/fixtures/core34-observations.json").read_text(encoding="utf-8"))
        old = next(row for row in records["runs"] if row["run_id"] == "a09")
        self.assertEqual("not_established", old["assessment"]["behavioral_acceptance"])
        self.assertNotIn("diagnostic_design", old["response"])
        self.assertEqual(old["response_sha256"], packet_hash(old["response"]))


if __name__ == "__main__":
    unittest.main()
