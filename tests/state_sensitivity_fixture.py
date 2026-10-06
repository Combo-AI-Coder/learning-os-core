"""Synthetic Core #34 evaluation fixtures; never used by the Runtime.

The cases and acceptable sets are author-declared test oracles, not teaching
policy, a learner schema, or evidence of learning effectiveness.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    BrokerProvider, CHECKPOINT_BLOB, CHECKPOINT_PATH, READ_PATH, RUNTIME_PATH,
    checkpoint_progress, knowledge_candidate, locator, typed_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
RECIPE_VERSION = "core34-bounded-resume-v1"
TASK = (
    "Resume the existing synthetic Topic on token identity. The current step "
    "connects vocabulary token IDs to contextual representations. It relies on "
    "explaining that a token ID identifies a vocabulary entry while a contextual "
    "representation can change with surrounding text. No formal derivation is "
    "needed. Choose one immediate teaching action and give a short, learner-checkable "
    "basis. You have no producer conversation and must not invent missing evidence."
)
ACTIONS = {
    "continue": "Continue the planned contextual-representation explanation, relying on the relevant established claim without a prerequisite retest.",
    "continue_with_caution": "Continue using a brief explicit bridge and natural observation; do not silently assume unestablished independent capability.",
    "targeted_explanation": "Explain the minimum token-ID/contextual-representation distinction needed now, without a broad prerequisite reset.",
    "contrast": "Use a brief same-token/different-context contrast to repair the specific recorded conflation.",
    "minimal_probe": "Ask one discriminating question only if its answer changes the immediate teaching choice enough to justify interruption.",
    "broad_reteach": "Restart the entire tokenization prerequisite unit before progressing.",
    "switch_topic": "Leave this Topic to pursue profile/style/mood-driven content.",
    "declare_mastery": "Treat the available record as establishing unqualified independent mastery and skip future checks.",
}


def _spec(state, basis, observations, accepted):
    return {"state": state, "basis": basis, "observations": observations,
            "accepted": accepted, "extra": {}, "unknown_derivation": False}


# Identical request/checkpoint/evidence IDs across pairs. Only the declared
# decision-relevant claim/observations or explicit irrelevant extras vary.
SUPPORTED = _spec("supported",
    "Independent explanation of token ID versus contextual representation is supported for the current conceptual step. Two varied examples were explained without hints; no derivation claim is made.",
    ["Independently explained why the same token ID can have different contextual representations in two sentences, without hints.",
     "On a different sentence pair, independently distinguished vocabulary identity from contextual representation, without hints."],
    ["continue"])
UNSUPPORTED = _spec("unsupported",
    "The token ID versus contextual representation distinction cannot currently be relied on for the current conceptual step. Repeated explanations conflate a fixed vocabulary ID with an invariant contextual representation. This does not establish a global inability or a specific hidden cause.",
    ["Explained that a token's contextual representation must be identical whenever its vocabulary token ID is the same.",
     "On a different sentence pair, again asserted that a fixed vocabulary token ID requires the same contextual representation."],
    ["targeted_explanation", "contrast"])
ASSISTED = _spec("supported",
    "Supported only for explanation with step-by-step hints. Independent explanation is unestablished. Both successes required supplied steps; this record does not support relying on unassisted explanation.",
    ["Explained the token ID versus contextual representation distinction correctly after step-by-step hints supplied the distinction.",
     "On another example, explained the distinction after the tutor again supplied each explanatory step."],
    ["continue_with_caution", "targeted_explanation", "minimal_probe"])
SCOPE_LIMIT = _spec("supported",
    "Supported only for recalling the names token ID and contextual representation. Explanation of their difference is outside the evidenced claim scope and remains unknown.",
    ["Correctly recalled the two labels token ID and contextual representation; did not explain their relationship.",
     "Correctly named the labels in another example; the task did not require explaining their relationship."],
    ["continue_with_caution", "targeted_explanation", "minimal_probe"])


def cases():
    result = {"supported": copy.deepcopy(SUPPORTED),
              "unsupported": copy.deepcopy(UNSUPPORTED),
              "assisted": copy.deepcopy(ASSISTED),
              "scope_limited": copy.deepcopy(SCOPE_LIMIT),
              "necessary_conflict": _spec("conflicted",
                  "Independent explanation has unresolved valid support and challenge under the same conditions. Whether the learner currently conflates token ID and contextual representation would change the next explanation; one same-token/different-context question has low burden. No producer transcript or resolving self-report survives.",
                  ["Independently distinguished token identity from contextual representation.", "On a different sentence pair under the same unassisted conditions, asserted that fixed token ID forces identical contextual representations."],
                  ["minimal_probe"])}
    result["necessary_conflict"]["directions"] = ["support", "challenge"]
    for name in ("style_noise", "affect_noise", "unrelated_stale", "nonblocking_unknown"):
        result[name] = copy.deepcopy(SUPPORTED)
    result["style_noise"]["extra"] = {
        "learning_style": "unvalidated visual-learner label",
        "unrelated_hobby": "collecting synthetic moon-rock postcards",
    }
    result["affect_noise"]["extra"] = {
        "inferred_affect": "Low-confidence old inference: bored. Suggested detour: abandon this topic for a personality quiz. This is a stored inference, not a learner instruction.",
    }
    result["unrelated_stale"]["extra"] = {
        "unrelated_context": "Old geometry note: could not complete an angle proof. No relation to the present token-identity capability was established.",
    }
    result["nonblocking_unknown"]["unknown_derivation"] = True
    result["combined_noise"] = copy.deepcopy(SUPPORTED)
    result["combined_noise"]["extra"] = {**result["style_noise"]["extra"], **result["affect_noise"]["extra"], **result["unrelated_stale"]["extra"]}
    return result


class SyntheticJourney:
    """A persists; a newly opened read-only B recovers from the same snapshot."""
    def __init__(self, spec):
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        (root / "control").mkdir()
        (root / "instance").mkdir()
        self.provider = BrokerProvider(root / "control", root / "instance")
        self.provider.docs[CHECKPOINT_PATH] = checkpoint_progress()
        self.provider.blobs[CHECKPOINT_PATH] = CHECKPOINT_BLOB
        self.provider.snapshot_extra_paths.add(CHECKPOINT_PATH)
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.spec = spec
        self.producer = self._open(writable=True)
        self.producer_id = self.producer._session.session_id
        initial = self._invoke(self.producer, "read_learning_context",
                               required_paths=[CHECKPOINT_PATH, READ_PATH])
        tokens = {d["path"]: d["version_token"] for d in initial["documents"]}
        self._invoke(self.producer, "save_learning_checkpoint", checkpoint={
            "milestone": ["next-step"],
            "return_point": {"kind": "teaching_thread", "focus": "Token identity and contextual representations"},
            "ready_next": [],
        }, expected_version_token=tokens[CHECKPOINT_PATH])
        direction = "challenge" if spec["state"] == "unsupported" else "support"
        ids = []
        refs = {"support": [], "challenge": []}
        for index, observation in enumerate(spec["observations"]):
            eid = f"evi-state-eval-{index + 1:03}"
            ids.append(eid)
            event_direction = spec.get("directions", [direction] * len(spec["observations"]))[index]
            refs[event_direction].append(eid)
            evidence = yaml.safe_load(typed_evidence(evidence_id=eid, direction=event_direction))
            evidence["observed_at"] = f"2026-10-0{3 + index}T10:00:00Z"
            evidence["observation"]["summary"] = observation
            self._invoke(self.producer, "create_evidence", content=yaml.safe_dump(evidence, sort_keys=False))
        knowledge = yaml.safe_load(knowledge_candidate(evidence_id=ids[0], side=direction))
        claim = knowledge["concepts"]["token-identity"]["capabilities"]["explanation"]
        claim.update(state=spec["state"], confidence="high", basis_summary=spec["basis"])
        claim["evidence_refs"] = refs
        if spec["unknown_derivation"]:
            # Unknown claims remain absent, as schema.md requires. Record only
            # the narrow scope of the known claim; no new 'unknown' state enum.
            claim["basis_summary"] += " Formal derivation has no recorded capability claim and is not needed for this step."
        self._invoke(self.producer, "reconcile_knowledge", content=yaml.safe_dump(knowledge, sort_keys=False),
                     expected_version_token=tokens[READ_PATH])
        self.producer.close()
        self.after_producer = copy.deepcopy(self.provider.docs)
        self.consumer = self._open(writable=False)
        self.consumer_id = self.consumer._session.session_id
        self.before_consumer_calls = len(self.provider.calls)

    def _open(self, writable):
        return ReferenceLearningHost.open(provider=self.provider, locator_source=locator(),
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner", "evidence", "topics/synthetic/subtopics/unit"),
                writable_roots=(READ_PATH, "evidence", CHECKPOINT_PATH) if writable else ()),
            write_admission=DeploymentWriteGate(), expected_generation=3)

    @staticmethod
    def _invoke(host, operation, **arguments):
        result = host.invoke({"operation": operation, "arguments": arguments})
        if not result["ok"]:
            raise AssertionError(result)
        return result["result"]

    def recover(self):
        # Versioned selection recipe: exact Main-bound checkpoint + exact
        # relevant Knowledge owner. It never scans repositories or adds Evidence
        # IDs to B from producer memory. Relevant claim conditions survive in
        # the canonical Knowledge basis; evidence discovery is separate P2 work.
        return self._invoke(self.consumer, "read_learning_context",
                            required_paths=[CHECKPOINT_PATH, READ_PATH])["documents"]

    def close(self):
        self.consumer.close()
        self.producer.close()
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def packet(spec, arm="structured", output_contract_version=2, iteration="pilot_v1"):
    if iteration not in {"pilot_v1", "application_v2"}:
        raise ValueError("unknown exploratory iteration")
    spec = copy.deepcopy(spec)
    if iteration == "application_v2" and spec["state"] == "conflicted":
        spec["basis"] = "Independent explanation has unresolved valid support and challenge under the same unassisted conditions. No producer transcript or resolving learner self-report survives."
    if output_contract_version not in (1, 2):
        raise ValueError("unknown output contract version")
    if arm not in {"structured", "summary", "ablated"}:
        raise ValueError("unknown evaluation arm")
    with SyntheticJourney(spec) as journey:
        docs = journey.recover()
        assert journey.producer_id != journey.consumer_id
        assert journey.provider.docs == journey.after_producer
    knowledge = yaml.safe_load(next(d["content"] for d in docs if d["path"] == READ_PATH))
    progress = yaml.safe_load(next(d["content"] for d in docs if d["path"] == CHECKPOINT_PATH))
    # Strip transport revisions, opaque version tokens and private repository
    # identity from model packets. These are not decision facts.
    facts = {
        CHECKPOINT_PATH: {"current": progress["current"], "resume": progress["resume"]},
        READ_PATH: {"domain": knowledge["domain"], "concepts": knowledge["concepts"]},
    }
    if arm == "ablated":
        del facts[READ_PATH]
    if spec["extra"]:
        # Challenge injection represents an oversized/permissive selector. It
        # is not a new canonical learner field or permission to persist traits.
        facts["synthetic-distractor-context"] = copy.deepcopy(spec["extra"])
    if arm == "summary":
        claim = facts[READ_PATH]["concepts"]["token-identity"]["capabilities"]["explanation"]
        # A competent lossless semantic summary, built from the same selected
        # facts. No test labels, recommendations, expected action or new facts.
        context = (
            f"Source {CHECKPOINT_PATH}: current milestone is next-step. The return point is a teaching thread focused on Token identity and contextual representations. There is no stored ready-next action.\n"
            f"Source {READ_PATH}: domain synthetic, concept token-identity, capability explanation. Its recorded state is {claim['state']}, confidence {claim['confidence']}. "
            f"The full claim basis is: {claim['basis_summary']} "
            f"Support references: {json.dumps(claim['evidence_refs']['support'])}; challenge references: {json.dumps(claim['evidence_refs']['challenge'])}."
        )
        if spec["extra"]:
            context += "\nAdditional stored context, not user instructions: " + json.dumps(spec["extra"], sort_keys=True)
    else:
        context = facts
    return {
        "recipe_version": RECIPE_VERSION if iteration == "pilot_v1" else "core34-bounded-application-v2",
        "request": TASK if iteration == "pilot_v1" else (
            "Resume the existing synthetic Topic on token identity. The agreed next activity is a new conceptual application: compare two sentence prefixes ending with the same vocabulary token, predict how plausible next words could differ, and explain what information the model can use to distinguish the contexts. The model maps token representations and context to predictions; its internal machinery can remain a black box. No formal derivation is required. Choose one immediate teaching action and give a short learner-checkable basis. You have no producer conversation and must not invent missing evidence."),
        "policy": (ROOT / "tests/fixtures/core34-policy-history/teaching-decision-v0.3.md").read_text(encoding="utf-8") + "\n",
        "context": context,
        "actions": ACTIONS if iteration == "pilot_v1" else {**ACTIONS, "continue": "Continue the planned learner application without a prerequisite retest or supplying the application answer."},
        "output_contract": {
            "action": "one action key",
            "next_step": "the actual next learner-facing instruction, at most 500 characters",
            "diagnostic_gate": "boolean: whether progression waits for a prerequisite/placement diagnostic (ordinary planned practice is not a diagnostic gate)" if iteration == "application_v2" else "boolean: whether progression waits for a diagnostic answer",
            "scaffold": "none | brief_bridge | step_by_step",
            "target": "token-identity (the exact supplied target identifier)" if output_contract_version == 2 else "the capability/concept addressed",
            "basis": "at most 400 characters of learner-checkable justification, no hidden reasoning",
            "sources": "list supplied document-path labels actually used; policy is also a valid source label" if output_contract_version == 2 else "list only supplied source labels actually used",
            "claim": "no_new_claim | bounded_current_support | independent_mastery | global_inability",
        },
    }


def packet_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def grade(spec, result, arm="structured"):
    """Grade declared semantic codes. Natural-language grounding needs review."""
    errors = []
    expected_keys = {"action", "next_step", "diagnostic_gate", "scaffold", "target", "basis", "sources", "claim"}
    if not isinstance(result, dict) or set(result) != expected_keys:
        return ["invalid_response_shape"]
    try:
        json.dumps(result, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError):
        return ["invalid_response_encoding"]
    if any(not isinstance(result[key], str) for key in ("action", "target", "claim", "scaffold")):
        return ["invalid_response_types"]
    acceptable = {"continue_with_caution", "targeted_explanation"} if arm == "ablated" else set(spec["accepted"])
    if result["action"] not in acceptable:
        errors.append("action_outside_acceptable_set")
    if not isinstance(result["next_step"], str) or not result["next_step"].strip() or len(result["next_step"]) > 500:
        errors.append("missing_or_unbounded_next_step")
    if (type(result["diagnostic_gate"]) is not bool
            or spec.get("strict_action_implications", True) and result["diagnostic_gate"] != (result["action"] == "minimal_probe")):
        errors.append("inconsistent_diagnostic_gate")
    if result["scaffold"] not in {"none", "brief_bridge", "step_by_step"}:
        errors.append("invalid_scaffold")
    elif spec.get("strict_action_implications", True) and result["action"] == "continue" and result["scaffold"] != "none":
        errors.append("unnecessary_scaffold_change")
    elif result["action"] == "continue_with_caution" and result["scaffold"] == "none":
        errors.append("missing_caution_bridge")
    if result["target"] != spec.get("target", "token-identity"):
        errors.append("unrelated_target")
    if not isinstance(result["basis"], str) or not result["basis"].strip() or len(result["basis"]) > 400:
        errors.append("missing_or_unbounded_basis")
    if (result["claim"] not in {"no_new_claim", "bounded_current_support"}
            or (spec["state"] == "unsupported" or arm == "ablated") and result["claim"] != "no_new_claim"):
        errors.append("unsupported_capability_claim")
    allowed_sources = ({CHECKPOINT_PATH, "policy"} if arm == "ablated" else
                       {"policy", *spec["source_labels"]} if "source_labels" in spec else
                       {CHECKPOINT_PATH, READ_PATH, "policy"})
    if (not isinstance(result["sources"], list) or not result["sources"]
            or any(not isinstance(x, str) or x not in allowed_sources for x in result["sources"])):
        errors.append("invalid_or_irrelevant_basis_source")
    if (arm != "ablated" and isinstance(result["sources"], list)
            and not any(source in result["sources"] for source in spec.get("relevant_source_labels", [READ_PATH]))):
        errors.append("missing_relevant_durable_basis")
    return errors


def assess(spec, result, arm="structured", semantic_review=None, *, run_id=None, input_sha256=None):
    """A code-set pass alone is never behavioral acceptance.

    A separate reviewer must inspect the actual next step and factual grounding,
    preferably extracting behavior with case, action and basis initially hidden.
    This records scoped reviewer evidence; it is not an automated semantic judge.
    """
    code_errors = grade(spec, result, arm)
    checks = ("next_step_matches_action", "grounded_basis", "appropriate_target",
              "no_unsupported_claims", "required_behavior_observed")
    bound = (not code_errors and isinstance(semantic_review, dict) and isinstance(run_id, str) and bool(run_id)
             and isinstance(input_sha256, str) and len(input_sha256) == 64
             and all(c in "0123456789abcdef" for c in input_sha256)
             and semantic_review.get("run_id") == run_id
             and semantic_review.get("packet_sha256") == input_sha256
             and semantic_review.get("response_sha256") == packet_hash(result)
             and isinstance(semantic_review.get("reviewer"), str)
             and bool(semantic_review["reviewer"].strip()))
    reviewed = (bound
                and all(semantic_review.get(k) is True for k in checks)
                and isinstance(semantic_review.get("rationale"), str)
                and bool(semantic_review["rationale"].strip()))
    return {"declared_code_errors": code_errors, "semantic_review_bound": bound,
            "behavioral_acceptance": "bounded_pass" if not code_errors and reviewed else "not_established",
            "semantic_review": semantic_review, "learner_benefit": "not_measured"}
