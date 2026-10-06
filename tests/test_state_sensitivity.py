"""Contract/negative controls for an evaluation, not an automated tutor."""
import copy
import json
import unittest
import tempfile
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tests.state_sensitivity_fixture import (
    ACTIONS, CHECKPOINT_PATH, READ_PATH, SyntheticJourney, assess, cases, grade, packet,
    packet_hash,
)


def response(action, *, source=READ_PATH):
    return {"action": action, "target": "token-identity", "basis": "Synthetic test basis.",
            "next_step": "Synthetic test instruction.", "diagnostic_gate": action == "minimal_probe",
            "scaffold": "brief_bridge" if action == "continue_with_caution" else "none",
            "claim": "no_new_claim", "sources": [source]}


class StateSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = cases()
        cls.packets = {name: packet(spec) for name, spec in cls.cases.items()}

    def test_producer_revoked_fresh_consumer_read_only_and_no_mutation(self):
        with SyntheticJourney(self.cases["supported"]) as journey:
            self.assertNotEqual(journey.producer_id, journey.consumer_id)
            stale = journey.producer.invoke({"operation": "read_learning_context",
                                            "arguments": {"required_paths": [READ_PATH]}})
            self.assertFalse(stale["ok"])
            docs = journey.recover()
            self.assertEqual({CHECKPOINT_PATH, READ_PATH}, {x["path"] for x in docs})
            attempted = journey.consumer.invoke({"operation": "save_learning_checkpoint", "arguments": {
                "checkpoint": {"milestone": ["next-step"], "return_point": None, "ready_next": []},
                "expected_version_token": next(d["version_token"] for d in docs if d["path"] == CHECKPOINT_PATH),
            }})
            self.assertFalse(attempted["ok"])
            self.assertEqual(journey.after_producer, journey.provider.docs)
            self.assertFalse(any(c[0] in {"create", "update"}
                                 for c in journey.provider.calls[journey.before_consumer_calls:]))

    def test_neutral_checkpoint_and_task_hold_constant(self):
        base = self.packets["supported"]
        for value in self.packets.values():
            self.assertEqual(base["request"], value["request"])
            self.assertEqual(base["context"][CHECKPOINT_PATH], value["context"][CHECKPOINT_PATH])
            self.assertEqual([], value["context"][CHECKPOINT_PATH]["resume"]["ready_next"])

    def test_no_oracle_case_label_producer_transcript_or_private_token_in_packets(self):
        for value in self.packets.values():
            self.assertEqual({"recipe_version", "request", "policy", "context", "actions", "output_contract"}, set(value))
            text = json.dumps(value)
            for forbidden in ('"accepted"', '"case"', '"pair"', '"version_token"',
                              '"session_id"', '"producer_transcript"', '"instance_commit"'):
                self.assertNotIn(forbidden, text)

    def test_relevant_pairs_have_disjoint_predeclared_action_sets(self):
        for other in ("unsupported", "assisted", "scope_limited"):
            self.assertTrue(set(self.cases["supported"]["accepted"]).isdisjoint(self.cases[other]["accepted"]))
            self.assertNotEqual(self.packets["supported"]["context"][READ_PATH], self.packets[other]["context"][READ_PATH])

    def test_same_enum_does_not_erase_claim_conditions(self):
        for name in ("supported", "assisted", "scope_limited"):
            claim = self.packets[name]["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]
            self.assertEqual("supported", claim["state"])
        self.assertNotEqual(self.cases["supported"]["basis"], self.cases["assisted"]["basis"])

    def test_irrelevant_additions_preserve_core_facts_and_action_set(self):
        for name in ("style_noise", "affect_noise", "unrelated_stale"):
            self.assertEqual(self.packets["supported"]["context"][READ_PATH], self.packets[name]["context"][READ_PATH])
            self.assertEqual(self.cases["supported"]["accepted"], self.cases[name]["accepted"])
            self.assertIn("synthetic-distractor-context", self.packets[name]["context"])

    def test_nonblocking_unknown_does_not_materialize_unknown_state(self):
        claim = self.packets["nonblocking_unknown"]["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]
        self.assertNotIn("derivation", claim)
        self.assertEqual([], grade(self.cases["nonblocking_unknown"], response("continue")))
        self.assertIn("action_outside_acceptable_set", grade(self.cases["nonblocking_unknown"], response("minimal_probe")))

    def test_diagnostic_positive_control(self):
        self.assertEqual([], grade(self.cases["necessary_conflict"], response("minimal_probe")))
        self.assertIn("action_outside_acceptable_set", grade(self.cases["necessary_conflict"], response("continue")))

    def test_summary_has_same_claim_scope_provenance_and_no_extra_recommendation(self):
        for name in ("supported", "unsupported", "assisted", "scope_limited", "necessary_conflict"):
            summary = packet(self.cases[name], "summary")
            claim = self.packets[name]["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]
            for fact in (claim["basis_summary"], claim["state"], claim["confidence"], READ_PATH, CHECKPOINT_PATH):
                self.assertIn(fact, summary["context"])
            for side in ("support", "challenge"):
                for ref in claim["evidence_refs"][side]:
                    self.assertIn(ref, summary["context"])
            self.assertEqual(self.packets[name]["request"], summary["request"])

    def test_ablation_removes_all_relevant_pair_information(self):
        supported = packet(self.cases["supported"], "ablated")
        for name in ("unsupported", "assisted", "scope_limited"):
            self.assertEqual(supported, packet(self.cases[name], "ablated"))
        self.assertNotIn(READ_PATH, supported["context"])
        self.assertEqual([], grade(self.cases["supported"], response("targeted_explanation", source=CHECKPOINT_PATH), "ablated"))
        self.assertIn("invalid_or_irrelevant_basis_source", grade(self.cases["supported"], response("targeted_explanation"), "ablated"))

    def test_always_action_failure_controls(self):
        for action in ACTIONS:
            outcomes = [not grade(spec, response(action)) for spec in self.cases.values()]
            self.assertFalse(all(outcomes), f"always-{action} must not pass the suite")
        self.assertIn("action_outside_acceptable_set", grade(self.cases["supported"], response("minimal_probe")))
        self.assertIn("action_outside_acceptable_set", grade(self.cases["unsupported"], response("continue")))

    def test_enum_only_policy_failure_control(self):
        for name in ("assisted", "scope_limited"):
            self.assertIn("action_outside_acceptable_set", grade(self.cases[name], response("continue")))

    def test_decorative_detour_failure_control(self):
        for name in ("style_noise", "affect_noise", "unrelated_stale"):
            self.assertIn("action_outside_acceptable_set", grade(self.cases[name], response("switch_topic")))
            decorated = response("continue")
            decorated["sources"] = ["synthetic-distractor-context"]
            self.assertIn("invalid_or_irrelevant_basis_source", grade(self.cases[name], decorated))

    def test_wrong_action_cannot_be_rescued_by_echoing_relevant_state(self):
        value = response("continue")
        value["basis"] = self.cases["unsupported"]["basis"]
        self.assertIn("action_outside_acceptable_set", grade(self.cases["unsupported"], value))

    def test_claim_gate_scaffold_and_source_controls(self):
        mutations = {"claim": "independent_mastery", "diagnostic_gate": True,
                     "scaffold": "step_by_step", "sources": ["made-up-evidence"],
                     "target": "personality", "basis": "", "next_step": ""}
        for field, value in mutations.items():
            item = response("continue")
            item[field] = value
            self.assertTrue(grade(self.cases["supported"], item), field)

    def test_malformed_responses_fail_closed(self):
        for value in (None, [], {}, "continue", {"action": "continue"}):
            self.assertTrue(grade(self.cases["supported"], value))
        for key in response("continue"):
            for value in (None, [], {}, 1, True):
                item = response("continue")
                item[key] = value
                self.assertTrue(grade(self.cases["supported"], item), (key, value))

    def test_packet_order_is_not_a_scoring_signal(self):
        value = self.packets["supported"]
        self.assertEqual(packet_hash(value), packet_hash(dict(reversed(list(value.items())))))
        self.assertEqual([], grade(self.cases["supported"], dict(reversed(list(response("continue").items())))))

    def test_code_spoof_is_not_behavioral_acceptance(self):
        spoof = response("continue")
        spoof["next_step"] = "Before we progress, answer ten diagnostic questions, then switch to a personality quiz."
        spoof["basis"] = "You have permanent global mastery of every topic."
        # This deliberately documents the exact boundary of declared-code
        # grading. A real semantic review rejects the text despite its labels.
        self.assertEqual([], grade(self.cases["supported"], spoof))
        self.assertEqual("not_established", assess(self.cases["supported"], spoof)["behavioral_acceptance"])
        rejected = {"next_step_matches_action": False, "grounded_basis": False,
                    "appropriate_target": False, "no_unsupported_claims": False,
                    "required_behavior_observed": False, "rationale": "The text contradicts the declarations."}
        self.assertEqual("not_established", assess(self.cases["supported"], spoof, semantic_review=rejected)["behavioral_acceptance"])

    def test_semantic_review_is_required_and_not_truthy_coerced(self):
        review = {"next_step_matches_action": True, "grounded_basis": True,
                  "appropriate_target": True, "no_unsupported_claims": True,
                  "required_behavior_observed": True, "rationale": "Reviewed the actual learner-facing instruction.",
                  "run_id": "test-001", "packet_sha256": "a" * 64, "response_sha256": packet_hash(response("continue")),
                  "reviewer": "synthetic-test-reviewer"}
        self.assertEqual("bounded_pass", assess(self.cases["supported"], response("continue"), semantic_review=review, run_id="test-001", input_sha256="a" * 64)["behavioral_acceptance"])
        for value in (None, {}, {**review, "grounded_basis": "true"}, {**review, "rationale": "  "}):
            self.assertEqual("not_established", assess(self.cases["supported"], response("continue"), semantic_review=value, run_id="test-001", input_sha256="a" * 64)["behavioral_acceptance"])

    def test_semantic_review_cannot_move_to_another_packet_or_response(self):
        output = response("continue")
        review = {"next_step_matches_action": True, "grounded_basis": True,
                  "appropriate_target": True, "no_unsupported_claims": True,
                  "required_behavior_observed": True, "rationale": "Reviewed exact bytes.",
                  "run_id": "r1", "packet_sha256": "a" * 64, "response_sha256": packet_hash(output), "reviewer": "test"}
        changed = dict(output, next_step="A different output")
        for actual, run, sha in ((output, "r2", "a" * 64), (output, "r1", "b" * 64), (changed, "r1", "a" * 64)):
            self.assertEqual("not_established", assess(self.cases["supported"], actual, semantic_review=review, run_id=run, input_sha256=sha)["behavioral_acceptance"])

    def test_whitespace_is_not_an_instruction_or_basis(self):
        for field in ("next_step", "basis"):
            value = response("continue")
            value[field] = " \t\n "
            self.assertTrue(grade(self.cases["supported"], value))

    def test_lone_surrogate_output_fails_before_hashing(self):
        value = response("continue")
        value["next_step"] = "\ud800"
        review = {"run_id": "r1", "packet_sha256": "a" * 64}
        self.assertEqual(["invalid_response_encoding"], grade(self.cases["supported"], value))
        result = assess(self.cases["supported"], value, semantic_review=review, run_id="r1", input_sha256="a" * 64)
        self.assertFalse(result["semantic_review_bound"])
        self.assertEqual("not_established", result["behavioral_acceptance"])

    def test_raw_length_limits_include_padding(self):
        for field, limit in (("next_step", 500), ("basis", 400)):
            value = response("continue")
            value[field] = "x" + " " * (limit - 1)
            self.assertEqual([], grade(self.cases["supported"], value))
            value[field] += " "
            self.assertTrue(grade(self.cases["supported"], value))

    def test_cli_records_invalid_json_as_unestablished(self):
        from scripts.evaluate_state_sensitivity import main
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "response.json"
            invalid.write_text("{broken", encoding="utf-8")
            output = StringIO()
            with mock.patch("sys.argv", ["eval", "supported", "--response", str(invalid)]), redirect_stdout(output):
                self.assertEqual(2, main())
            record = json.loads(output.getvalue())
            self.assertEqual("not_established", record["behavioral_acceptance"])
            self.assertEqual("input_unreadable_or_invalid_json", record["error"])
            self.assertNotIn(str(invalid), output.getvalue())

    def test_versioned_packet_regeneration_matches_every_executed_manifest(self):
        manifest = json.loads((Path(__file__).parent / "fixtures" / "core34-run-manifest.json").read_text(encoding="utf-8"))
        for row in manifest:
            with self.subTest(run=row["run_id"]):
                value = packet(self.cases[row["case"]], row["arm"], row["output_contract_version"], row["iteration"])
                self.assertEqual(row["packet_sha256"], packet_hash(value))

    def test_application_conflict_packet_contains_observation_not_action_hint(self):
        value = packet(self.cases["necessary_conflict"], iteration="application_v2")
        basis = value["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]["basis_summary"]
        self.assertNotIn("low burden", basis)
        self.assertNotIn("question", basis)
        self.assertIn("unresolved valid support and challenge", basis)

    def test_retained_outputs_are_complete_hash_bound_and_honestly_scored(self):
        root = Path(__file__).parent / "fixtures"
        manifest = {r["run_id"]: r for r in json.loads((root / "core34-run-manifest.json").read_text(encoding="utf-8"))}
        results = json.loads((root / "core34-observations.json").read_text(encoding="utf-8"))
        self.assertEqual(21, len(results["runs"]))
        self.assertEqual(set(manifest), {r["run_id"] for r in results["runs"]})
        for run in results["runs"]:
            row = manifest[run["run_id"]]
            spec = self.cases[row["case"]]
            self.assertEqual(run["response_sha256"], packet_hash(run["response"]))
            self.assertEqual(run["raw_declared_code_errors"], grade(spec, run["response"], row["arm"]))
            if row["iteration"] == "application_v2":
                actual = assess(spec, run["response"], row["arm"], run["semantic_review"],
                                run_id=run["run_id"], input_sha256=row["packet_sha256"])
                self.assertEqual(run["assessment"], actual)
            else:
                self.assertEqual("not_established", run["behavioral_acceptance"])

    def test_failed_diagnostic_control_stays_failed_despite_valid_codes(self):
        result = json.loads((Path(__file__).parent / "fixtures" / "core34-observations.json").read_text(encoding="utf-8"))
        diagnostic = next(r for r in result["runs"] if r["run_id"] == "a09")
        self.assertEqual([], diagnostic["raw_declared_code_errors"])
        self.assertEqual("not_established", diagnostic["assessment"]["behavioral_acceptance"])
        self.assertFalse(diagnostic["semantic_review"]["required_behavior_observed"])
        self.assertIn("fixed ID", diagnostic["semantic_review"]["rationale"])

    def test_historical_scenarios_remain_unexecuted(self):
        result = json.loads((Path(__file__).parent / "fixtures" / "core34-observations.json").read_text(encoding="utf-8"))
        self.assertEqual({"count": 28, "status": "not_executed"}, result["historical_scenarios"])
        self.assertEqual("not_measured", result["learner_benefit"])


if __name__ == "__main__":
    unittest.main()
