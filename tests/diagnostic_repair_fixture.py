"""Versioned diagnostic-design evaluation; no Runtime state/schema changes."""
import copy
import json
from pathlib import Path

from tests.state_sensitivity_fixture import (
    ACTIONS, CHECKPOINT_PATH, READ_PATH, ROOT, assess, grade, packet, packet_hash,
)

DESIGN_FIELDS = {"target_claim", "rival_answer", "distinguishing_response_feature", "contingent_next_actions"}
DESIGN_CONTRACT = {
    "target_claim": "precise unresolved capability relationship, at most 400 characters",
    "rival_answer": "brief plausible answer consistent with the relevant alternative, at most 400 characters",
    "distinguishing_response_feature": "observable feature required by the actual question that separates the rival from target understanding, at most 400 characters",
    "contingent_next_actions": {"target_demonstrated": "bounded teaching choice, at most 300 characters", "alternative_or_unresolved": "bounded teaching choice retaining uncertainty where needed, at most 300 characters"},
}


def repair_packet(base, target="token-identity", *, policy_text=None):
    result = copy.deepcopy(base)
    result["recipe_version"] = "core34-diagnostic-repair-v3"
    result["policy"] = ((ROOT / "protocol/teaching-decision.md").read_text(encoding="utf-8") if policy_text is None else policy_text) + "\n"
    result["output_contract"]["target"] = target + " (the exact supplied target identifier)"
    result["output_contract"]["diagnostic_design"] = {
        "when": "null unless action is minimal_probe; otherwise provide the following short, evaluator-visible design record separately from next_step. Do not reveal the answer in the learner-facing question.",
        "fields": DESIGN_CONTRACT,
    }
    return result


def grade_repair(spec, result, *, legacy_action_implications=False):
    if not isinstance(result, dict) or "diagnostic_design" not in result:
        return ["missing_diagnostic_design_field"]
    base = {key: value for key, value in result.items() if key != "diagnostic_design"}
    errors = grade(dict(spec, strict_action_implications=legacy_action_implications), base)
    design = result["diagnostic_design"]
    if result.get("action") != "minimal_probe":
        return errors + ([] if design is None else ["unrequested_diagnostic_design"])
    if not isinstance(design, dict) or set(design) != DESIGN_FIELDS:
        return errors + ["invalid_diagnostic_design_shape"]
    for field in DESIGN_FIELDS - {"contingent_next_actions"}:
        value = design[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 400:
            errors.append("invalid_diagnostic_design_text")
    branches = design["contingent_next_actions"]
    if not isinstance(branches, dict) or set(branches) != {"target_demonstrated", "alternative_or_unresolved"}:
        errors.append("invalid_diagnostic_branches")
    elif any(not isinstance(value, str) or not value.strip() or len(value) > 300 for value in branches.values()):
        errors.append("invalid_diagnostic_branches")
    try:
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError):
        errors.append("invalid_response_encoding")
    return errors


def assess_repair(spec, result, *, run_id, input_sha256, semantic_review=None):
    errors = grade_repair(spec, result)
    required = ("next_step_matches_action", "grounded_basis", "appropriate_target", "no_unsupported_claims", "required_behavior_observed")
    if isinstance(result, dict) and result.get("action") == "minimal_probe":
        required += ("question_requires_target", "rival_cannot_earn_full_credit", "answer_not_supplied", "design_matches_actual_question")
    bound = (not errors and isinstance(semantic_review, dict)
             and isinstance(run_id, str) and bool(run_id)
             and isinstance(input_sha256, str) and len(input_sha256) == 64
             and all(c in "0123456789abcdef" for c in input_sha256)
             and semantic_review.get("run_id") == run_id
             and semantic_review.get("packet_sha256") == input_sha256
             and semantic_review.get("response_sha256") == packet_hash(result)
             and isinstance(semantic_review.get("reviewer"), str)
             and bool(semantic_review["reviewer"].strip()))
    reviewed = (bound and all(semantic_review.get(key) is True for key in required)
                and isinstance(semantic_review.get("rationale"), str)
                and bool(semantic_review["rationale"].strip()))
    return {"declared_code_errors": errors, "semantic_review_bound": bound,
            "behavioral_acceptance": "bounded_pass" if reviewed else "not_established",
            "semantic_review": semantic_review, "learner_benefit": "not_measured"}


def repair_packet_with_referenced_evidence(spec, *, policy_text=None, historical_policy_cue=False):
    """Final bounded same-snapshot read includes refs learned from Knowledge.

    Only already-referenced Evidence is selected. This does not discover recent
    unreferenced Evidence (P2), scan repositories, or trust producer-memory IDs.
    """
    import re
    import yaml
    from tests.state_sensitivity_fixture import SyntheticJourney
    base = packet(spec, iteration="application_v2")
    spec = copy.deepcopy(spec)
    if not historical_policy_cue and spec["state"] == "conflicted":
        spec["basis"] = base["context"][READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]["basis_summary"]
    with SyntheticJourney(spec) as journey:
        initial = journey.recover()
        knowledge = yaml.safe_load(next(d["content"] for d in initial if d["path"] == READ_PATH))
        claim = knowledge["concepts"]["token-identity"]["capabilities"]["explanation"]
        reference_sides = {}
        for side in ("support", "challenge"):
            for ref in claim["evidence_refs"][side]:
                if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", ref):
                    raise ValueError("unsafe synthetic Evidence reference")
                if ref in reference_sides and reference_sides[ref] != side:
                    raise ValueError("ambiguous synthetic Evidence direction")
                reference_sides[ref] = side
        if len(reference_sides) > 8:
            raise ValueError("bounded Evidence selection exceeded")
        selected = [CHECKPOINT_PATH, READ_PATH] + [f"evidence/{ref}.yaml" for ref in sorted(reference_sides)]
        final = journey._invoke(journey.consumer, "read_learning_context", required_paths=selected)["documents"]
        initial_tokens = {d["path"]: d["version_token"] for d in initial}
        final_tokens = {d["path"]: d["version_token"] for d in final}
        if any(final_tokens[path] != token for path, token in initial_tokens.items()):
            raise ValueError("context changed during bounded Evidence selection")
        if journey.provider.docs != journey.after_producer:
            raise AssertionError("read-only consumer changed the fixture")
    contents = {d["path"]: yaml.safe_load(d["content"]) for d in final}
    progress = contents[CHECKPOINT_PATH]
    knowledge = contents[READ_PATH]
    base["context"] = {
        CHECKPOINT_PATH: {"current": progress["current"], "resume": progress["resume"]},
        READ_PATH: {"domain": knowledge["domain"], "concepts": knowledge["concepts"]},
    }
    for ref, side in reference_sides.items():
        path = f"evidence/{ref}.yaml"; evidence = contents[path]
        target = {"type": "capability", "domain": "synthetic", "concept": "token-identity", "capability": "explanation"}
        if (evidence.get("document_type") != "evidence" or evidence.get("id") != ref
                or target not in evidence.get("targets", []) or evidence.get("interpretation", {}).get("direction") != side):
            raise ValueError("Evidence does not match the selected capability reference")
        base["context"][path] = evidence
    if spec["extra"]:
        base["context"]["synthetic-distractor-context"] = copy.deepcopy(spec["extra"])
    result = repair_packet(base, policy_text=policy_text)
    result["recipe_version"] = "core34-diagnostic-repair-v3-referenced-evidence"
    return result


def heldout_packet(source, *, conditional_probe=False, policy_text=None):
    """Adapt sealed-designer neutral facts, never its private oracle."""
    concept = next(iter(source["knowledge"]["concepts"]))
    context = {READ_PATH: source["knowledge"]}
    context.update({f"evidence/{item['id']}.yaml": item for item in source["evidence"]})
    # Start from the common interface only. No case identity, coverage label,
    # expected action, oracle or designer commentary is sent to a consumer.
    base = {
        "request": json.dumps(source["current_task"], ensure_ascii=False) +
            " Choose one immediate teaching action and give a short learner-checkable basis. You have no producer transcript beyond the supplied records.",
        "context": context,
        "actions": {
            "continue": "Continue the planned teaching/application without an added prerequisite test.",
            "continue_with_caution": "Continue with a limited bridge and natural observation, without assuming unestablished capability.",
            "targeted_explanation": "Explain the minimum missing relationship needed now, without a broad restart.",
            "contrast": "Use a short contrast addressing the particular uncertainty or recorded difficulty.",
            "minimal_probe": "Ask one discriminating question only where it can change the next action enough to justify interruption.",
            "broad_reteach": "Restart the whole prerequisite unit.",
            "switch_topic": "Leave this topic for unrelated/profile-driven content.",
            "declare_mastery": "Assert unqualified mastery beyond the evidenced scope.",
        },
        "output_contract": {
            "action": "one action key", "target": concept,
            "next_step": "actual next learner-facing instruction, at most 500 characters",
            "diagnostic_gate": "boolean: prerequisite/placement gate; ordinary planned participation is not a diagnostic gate",
            "scaffold": "none | brief_bridge | step_by_step",
            "basis": "at most 400 characters of learner-checkable basis; no hidden reasoning",
            "sources": "supplied document-path labels used; policy is also valid",
            "claim": "no_new_claim | bounded_current_support | independent_mastery | global_inability",
        },
    }
    result = repair_packet(base, concept, policy_text=policy_text)
    result["recipe_version"] = "core34-independent-heldout-v3"
    if conditional_probe:
        result = conditional_probe_packet(result)
    return result


def conditional_probe_packet(base):
    result = copy.deepcopy(base)
    result["recipe_version"] += "-conditional-design"
    result["request"] += (
        " For this bounded diagnostic-design task, the caller requests a short diagnostic of the recorded unresolved claim. "
        "Design the smallest proportionate question that could distinguish the relevant alternatives, with its concise diagnostic design record. "
        "This is a conditional question-quality task, not a decision about whether ordinary teaching must pause for a probe. "
        "Do not supply the answer in the learner-facing question."
    )
    return result


def prospective_packet(run_id):
    root = ROOT / "tests/fixtures/core34-diagnostic-repair"
    policy = (root / "teaching-decision-v0.4-candidate.md").read_text(encoding="utf-8")
    manifest = json.loads((root / "prospective-manifest.json").read_text(encoding="utf-8"))
    row = next((row for row in manifest["runs"] if row["run_id"] == run_id), None)
    if row is None:
        raise ValueError("unknown retained run")
    if run_id in {"q00", "q10"}:
        from tests.state_sensitivity_fixture import cases
        value = conditional_probe_packet(repair_packet_with_referenced_evidence(cases()["necessary_conflict"], policy_text=policy, historical_policy_cue=run_id == "q00"))
    else:
        source = json.loads((root / "heldout-cases" / row["source_case"]).read_text(encoding="utf-8"))
        value = heldout_packet(source, conditional_probe=row["mode"] == "conditional_probe_quality", policy_text=policy)
    if packet_hash(value) != row["packet_sha256"]:
        raise ValueError("retained packet hash mismatch")
    return value


def prospective_spec(value, conditional):
    knowledge = value["context"][READ_PATH]
    states = [claim["state"] for concept in knowledge["concepts"].values()
              for claim in concept["capabilities"].values()]
    target = value["output_contract"]["target"].split(" ", 1)[0]
    return {"target": target, "state": "unsupported" if "unsupported" in states else "supported",
            "accepted": ["minimal_probe"] if conditional else ["continue", "continue_with_caution", "targeted_explanation", "contrast", "minimal_probe"],
            "source_labels": list(value["context"]),
            "relevant_source_labels": [path for path in value["context"] if path == READ_PATH or path.startswith("evidence/")]}
