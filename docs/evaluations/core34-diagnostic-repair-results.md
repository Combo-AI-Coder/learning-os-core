# Core #34: scoped diagnostic-quality repair and re-review

Date: 2026-10-06. Local development candidate; no merge, deployment, real learner mutation or learner-effect acceptance.

## Outcome

The clean prospective diagnostic set passes artifact-level adversarial review: **q10 and q01–q03** each require the disputed relationship in the actual learner-facing question. Three independently designed ordinary teaching controls (**h04–h06**) also pass, and the supplemental supported/distractor pair (**r02/r03**) preserves proportionate continuation without added testing or detours.

For the original failure, the repaired question asks whether the same vocabulary token ID forces the **context-processed representation** to be identical and requests an explanation. A learner who still believes that implication cannot earn full credit merely by saying that the ID is fixed or proposing different next words. The independent-context reviewer tested that concrete rival answer. This fixes the narrow question-quality defect; it does not prove that ordinary teaching must choose a probe.

The original **a09** remains failed. The later **r01** remains failed under its frozen mandatory-probe oracle, which independent review found was narrower than canonical policy. Neither record is relabeled as passing. The [original sensitivity/invariance results](core34-state-sensitivity-results.md) retain the inconclusive pilot and bounded application evidence.

The final claim is therefore precise: **a bounded conditional diagnostic-design repair and its ordinary-teaching controls pass local review**. It is not a blanket pass for Core #34, spontaneous probe selection, broad tutor reliability, or learning effectiveness. Target-hosted CI, external review and integration retain their separate publication/authority boundary.

## Actual changes

1. Candidate teaching-decision v0.4 clarifies the existing target-required probe rule. Before issuing a probe, consider a concrete plausible rival answer relevant to the uncertainty. If that answer can receive full credit, the question does not require the target. Revise it or choose a proportionate teaching/natural-observation action. Unknown state alone still does not justify probing.
2. The evaluation-only v3 response contract adds a short diagnostic-design record for probe actions: precise target, rival answer, observable separating feature and bounded contingent teaching actions. It is outside learner Knowledge and outside the learner-facing question. Self-declared design validity cannot establish acceptance.
3. The reference recipe retrieves bounded Evidence already referenced by durable Knowledge. A first bounded read supplies the IDs; a final combined read returns Progress, Knowledge and those Evidence documents in one broker snapshot. Changed Knowledge/Progress version tokens, unsafe references, more than eight selected references, or mismatched typed capability/direction fail closed. The eight-reference ceiling is a test-recipe bound, not a learner-model threshold.
4. The q/h assessments bind semantic review to the run ID, exact exported packet hash and exact response hash. The separate r02/r03 manual reviews carry later-verified hash references; their original candidate1 observations remain historical `not_established` entries, not retroactively rewritten machine assessments. Actual question quality, scope and factual grounding remain separate from field/type validation. These bindings support reproducibility, not reviewer authenticity or a proof that a judgment is correct.

Runtime broker, host, adapter, validators, permissions and learner schema are unchanged. The only product-policy change is the candidate clarification; it creates no generic pedagogical engine, mastery threshold, decay function, misconception taxonomy, deployment pin or state-write operation. Discovery of recent **unreferenced** Evidence and P2 recovery/correction remain out of scope.

## Preserved iterations and scope corrections

- All 21 initial outputs remain unchanged; all 28 historical scenarios remain `not_executed`
- Candidate1 adds r01–r03. r01 chose a cautious application instead of a probe. That failed the old oracle but was not itself shown to violate product policy: natural observation and local refinement are permitted
- A separate prospective contract evaluates question quality **conditional on a justified diagnostic request**. This explicitly supplies the design task, so success cannot be counted as cold state-driven mandatory-probe selection
- The six held-out source cases and their private evaluation oracles were frozen by a separate designer before consumer runs. They cover measurement, probability, verbal inference, supported continuation, a nonblocking absent capability, and condition-limited support
- h01–h03 spontaneous conflict packets were prepared but remain unexecuted because the frozen diagnostic oracles do not enumerate every policy-conformant non-probe alternative. Their conditional counterparts q01–q03 are the evaluated conflict cases
- q00's reference-read fixture accidentally reintroduced the historical intervention/value hint into Knowledge basis. Its raw output and qualified review remain, but it is excluded from the clean conditional set. The corrected factual packet produced the separately retained first q10 output
- Across these iterations, 32 model outputs are preserved. There were no selective reruns of unchanged prompts to obtain favorable samples

Two inherited code-grader assumptions also required a transparent compatibility repair. The old unsolved-application task tied `continue` to no added scaffolding and tied a diagnostic label to a prerequisite gate. The new supplied contracts do not make those implications: h04 explicitly asks for a worked example, and conditional question design does not itself decide whether ordinary teaching must pause. Independent contract review checked the frozen requests, oracles and actual outputs before the evaluator was scoped correctly. Legacy-profile grades are retained alongside corrected-profile grades. Actual rival discrimination, no-answer-leakage and no-gratuitous-testing criteria were not weakened.

## Independent-context review findings

The reviewer first inspected actual question text, attempted a misconception-consistent full-credit answer, then checked design/basis alignment against the exact inputs and sealed oracle. It did not participate in implementation, case design or consumer generation. It uses the same model family, so this is quality amplification rather than independent audit.

- **q10:** explicitly rejects the inference from fixed ID to necessarily identical processed representations; different continuations alone are insufficient
- **q01:** requires combining the two changed rectangle dimensions, rather than merely identifying a side-length factor
- **q02:** requires next-toss reasoning after a streak, rather than isolated fair-coin recall
- **q03:** requires a reverse-inference/counterexample judgment, rather than repeating the already-supported forward relation
- **h04:** continues the requested worked fraction example with a legitimate bridge, without a readiness quiz
- **h05:** uses supported count-reading/addition and does not test unrelated logarithmic-axis knowledge
- **h06:** introduces the uncovered negative-factor condition locally, without assuming competence or inability beyond positive-factor evidence
- **r02/r03:** preserve the ordinary application demand across irrelevant extra context

Important limitations remain: q01/q02 revisit challenging conditions rather than novel transfer, q10 prefixes differ in length (so position is not isolated from lexical context), and no downstream learner-answer grading or real learner outcome was tested.

## Reproduction and verification

- `scripts/evaluate_diagnostic_repair.py q10` exports the retained clean packet.
- `--response response.json` validates declared output shape; `--semantic-review review.json` requires matching run/packet/response bindings and semantic checks.
- `tests/fixtures/core34-diagnostic-repair/prospective-manifest.json`, `prospective-observations.json`, and `adversarial-review.json` preserve the packet hashes, raw outputs, grader profiles, and counterexample reviews. The q10 reproduction exporter snapshot was added during review (q10's input hash was recorded prior to its call). Model sampling is non-deterministic, while packet regeneration and response re-scoring are deterministic.
