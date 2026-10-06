# Core #34: bounded state-sensitivity / invariance evaluation

Date: 2026-10-06. Development evaluation only; no deployment, learner-state write, learner acceptance, or research-candidate promotion.

## Result and remaining boundary

This file preserves the initial 21-run slice. A subsequent [diagnostic-quality repair](core34-diagnostic-repair-results.md) supplies the current bounded local repair result while retaining every initial failure and limitation.

This slice adds an offline, reusable synthetic journey/packet exporter and a two-stage evaluator. A synthetic producer persists a neutral checkpoint and existing-schema Knowledge through the owning reference-host operations, is revoked, and a newly opened read-only consumer recovers exactly the bounded task context. Fresh isolated model consumers then receive only exported task/policy/context packets. They receive no producer conversation, per-case accepted set, test label, or in-memory Evidence ID needed to fetch a producer's work.

All **21 first outputs** are retained: 12 pilot runs and nine runs in a subsequent exploratory application iteration. There were no selective reruns. Each run records input hash, output-contract version, raw output and scoped review disposition. The 28 historical scenarios remain `not_executed`.

The pilot was **behaviorally inconclusive**. Action names and bases changed, but several next learner-facing steps still taught essentially the same prerequisite. Its p09 conflict packet also contained an intervention/value judgment, so that run is explicitly policy-cued, not blind diagnostic evidence. The subsequent iteration was declared after this feedback and before its outputs; it is not retroactive preregistration or a confirmatory study.

The application iteration gives a bounded observable contrast: a01 asks an unsolved downstream application; a02/a04 supply the relevant distinction before application; a03 supplies a smaller bridge. Combined distractors (a05) preserve the unsolved task without added scaffolding or testing. Same-facts summaries (a06/a07) produce comparable behavior; ablation (a08) rationally uses a modest bridge.

**The diagnostic positive control fails.** a09 chooses a short prerequisite question but asks only whether the token ID stays fixed. A learner who believes fixed ID implies fixed contextual representation can answer that correctly, so the question does not resolve the actual conflicting capability claim. All nine outputs pass declared-code grading, while only eight receive bounded behavior/grounding dispositions. The suite is therefore partially demonstrated, not an all-pass decision-quality result. No output was rerun to hide this failure.

Application-v2 detailed behavioral adjudication is recorded in `tests/fixtures/core34-observations.json`; its fixed nine-run plan is [core34-application-v2-plan.md](core34-application-v2-plan.md). A declared-code pass alone is never behavioral acceptance.

## What is exercised

- Sensitivity to supported versus repeatedly challenged relevant capability state
- Same-`supported`-enum controls with assistance-only and naming-only claim limits
- Invariance to synthetic style/hobby, weak affect and unrelated stale-topic context; a nonblocking absent derivation claim in the pilot
- A positive uncertainty case, with actual diagnosticity reviewed rather than accepting the `minimal_probe` label alone
- A strong narrative summary with the same permitted claim facts, conditions, confidence and Evidence references
- Structured-context ablation that removes relevant Knowledge everywhere; support/challenge worlds become byte-identical to the consumer
- Failure controls for always-continue, always-probe, always-repair, enum-only reading, irrelevant detours, state parroting with the wrong action, invented claims/sources, malformed output, and declaration/text spoofing

Strong-summary equality is a valid result. This comparison cannot establish structured-format superiority. A rational cautious action after ablation is not scored as a teaching error simply because relevant information is gone.

## Public surface and isolation

The initial slice described here is test/evaluation tooling, not a Runtime teaching policy. The later linked repair separately proposes a narrow teaching-policy clarification. Production `reference_host.py`, broker, adapter, validators, schema, write permissions and deployment pins are unchanged. Claim conditions remain in existing factual `basis_summary` and Evidence observations; no mastery percentage, readiness field, hint threshold or forgetting rule is added.

The selection recipe is versioned and bounded: exact Main-bound Progress plus the relevant synthetic Knowledge owner, from one host read. It retains scope/assistance limits but strips opaque transport tokens, private repository identity and revision noise from model packets. The checkpoint contains no recommended next action. Explicit distractor injection stress-tests what happens if a permissive selector supplies extra context; it is not a new canonical learner field or authorization to persist inferred traits. The model receives relevant Evidence references as part of Knowledge, but discovery/recovery of unreferenced Evidence is separate P2 work.

The synthetic producer is a fixture, not an evaluated model that infers Knowledge. These experiments therefore test consumers of deliberately authored, coherent durable facts; they do not validate state extraction, factual correctness of a real learner model, or interruption/correction semantics.

## Scoring and reproducibility

- `tests/state_sensitivity_fixture.py` contains only synthetic fixture production, packet export and declared-code validation
- `scripts/evaluate_state_sensitivity.py` exports a blind packet or scores one retained response, without a provider/network call
- `tests/fixtures/core34-run-manifest.json` freezes all input hashes, arm/iteration and output-contract versions
- `tests/fixtures/core34-observations.json` retains raw outputs, parsing/normalization disclosures and separate semantic judgments
- `tests/test_state_sensitivity.py` regression-protects the mechanical/scoring contract and counterexamples

Example export:

```sh
python scripts/evaluate_state_sensitivity.py supported --iteration application_v2
```

For a retained pilot p01 response, explicitly select `--iteration pilot_v1 --contract-version 1`. The first three pilot consumers received an imprecise target/source contract. Their raw target phrases were semantically on-topic, but failed the strict target-ID check; p03 also named the supplied policy by title. Any normalized copy is recorded separately. Raw outputs are never overwritten, and none of these pilot runs is promoted to a behavioral sensitivity pass.

Scoring with `--response response.json` checks declared codes but returns `behavioral_acceptance: not_established` unless an explicit reviewed disposition is provided. A review must bind run ID, exact packet hash, exact response hash and reviewer provenance; stale/transplanted reviews fail closed. It must affirm observable required behavior, matching action/text, grounded basis, target relevance and claim limits. Reviewer provenance is recorded, not cryptographically authenticated. A false reviewer judgment is not made true by hashing it.

The application review first extracts observable features from shuffled next-step text with case/state/action/basis hidden, then joins them to the fixed plan and checks full-output grounding. This is same-model quality amplification, not an independent audit. No private reasoning is requested or retained.

## Verification and limits

Required verification is Core validation and the complete unit suite, plus exact-head hosted Linux/Windows CI. The result PR owns final run links and review state. Tests verify that prior producer capabilities are revoked, fresh sessions differ, B is read-only, and B's recovery cannot mutate fixture state. All 47 original baseline files were checked against Git blob hashes before final validation.

The exact backend model build, temperature and seed are not exposed by the execution surface. These runs use fresh isolated contexts and high reasoning effort but cannot be perfectly replayed as a provider benchmark. Packet hashes cover the exported task packets, not the complete provider prompt. Packet regeneration and retained output re-scoring are deterministic. One sample per packet, one conceptual task/domain, author-defined acceptable sets and explicitly labeled distractors support only a bounded reference comparison.

No real learner took part. Delayed retention/transfer, teaching effectiveness, broad adversarial robustness, recency/decay rules, P2 recovery/correction, production promotion, and product acceptance remain unmeasured or separate. This slice does not complete all of Core #34.

## Authority and source anchors

- [Core #34 accepted requirement](https://github.com/Combo-AI-Coder/learning-os-core/issues/34)
- [Core baseline 37e915d](https://github.com/Combo-AI-Coder/learning-os-core/tree/37e915d4423ada9b6a2ed296a811da4f1945cce3): teaching-decision v0.3, evidence-integration and schema remain authoritative
- [PR #38 corrected research candidate](https://github.com/Combo-AI-Coder/learning-os-core/blob/46438ea88a1563c23c2bca4f479a49a7a1b7d1d7/docs/research/2026-10-06-longitudinal-learner-state-evidence.md) informs candidate tests only; its evidence gaps and unmerged status are preserved

Current teaching policy governs the evaluation: supported claims normally permit continuation without retesting; provisional/unknown state does not automatically mandate a probe; a probe must discriminate a decision-relevant uncertainty at proportionate cost. No candidate research statement overrides that policy.
