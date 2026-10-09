---
protocol: evidence-integration
version: "0.3"
schema_compatibility: "0.3"
---

# Evidence Integration

Integrate new evidence with the existing portfolio. Do not use simple vote counts.

Allowed integration outcomes: `retain`, `advance`, `challenge`, `refine`.

## Principles

- Prefer `refine` when apparently conflicting evidence can be explained by meaningful task conditions.
- Prioritize evidence diversity, diagnosticity, independence, transfer, delay, exact claim relevance, and remaining alternative explanations.
- `supported` means current teaching may rely on the claim without extra verification cost unless meaningful contradictory evidence appears.
- `provisional` means there is useful support but important alternatives remain.
- `conflicted` means valid support and challenge remain unresolved for the same claim and conditions.
- `unsupported` means substantive evidence indicates that the learner cannot currently perform reliably under the claim's stated conditions. Mere absence or withdrawal of support is insufficient.

These capability states are the V0.3 learner Knowledge State values defined by `schema.md`. Evidence itself remains an immutable observation plus `interpretation` and target references; integration updates a capability claim only when persistence is justified.

## Default transition guidance

- absent/unknown + meaningful support -> `provisional`
- `provisional` + diverse support / reduced alternatives -> `supported`
- `provisional` + high direct challenge -> `conflicted`
- `supported` + isolated medium contradiction against a broad portfolio -> consider an active anomaly before downgrade
- `supported` + meaningful direct contradiction -> `conflicted` or refine
- `unsupported` + immediate post-intervention success -> normally at most `provisional`
- `conflicted` -> `supported` only after the conflict is explained or resolved, not merely after new correct answers

Higher-level hypotheses require broader evidence and slower updates than concept-level state.

## Withdrawing an apparent support basis

A later report may change what an earlier performance supports without proving
inability. For example, discovering that a hint supplied an explanation removes
that performance's apparent support for independent explanation; it does not by
itself challenge the learner's ability to explain independently.

Reassess the exact claim and conditions against the full relevant Evidence and
current Knowledge, following `evidence-classification.md` and
`persistence-policy.md`:

- If neither valid support nor substantive scoped negative evidence remains,
  the claim is unknown and MUST remain absent, as required by `schema.md`. Remove
  a stale persisted claim only when that conclusion and persistence are justified;
  do not encode unknown as `unsupported` or a new state.
- Preserve other valid independent support and unaffected claims. Evidence of
  assisted performance may still support an appropriately scoped assisted claim;
  it must not be presented as independent performance or erased as failure.
- Substantive evidence of inability to perform reliably can justify `unsupported`
  for its supported scope. Where valid support and challenge remain unresolved
  under the same conditions, preserve the conflict or refine the scope instead
  of discarding one side.
- An ambiguous or low-confidence report does not settle withdrawal. Repeated
  reports about one performance are not additional independent performances;
  contradictory reports require reassessment, not latest-wins or automatic
  downgrade. Preserve the original observations and report history.
- Keep neutral interpretive context neutral. Do not relabel it as `challenge`
  merely to fit a Knowledge reference list; use existing prose context where
  useful and preserve the source Evidence for full-context recovery.
- Missing, denied, or over-budget relevant context is a coverage gap, not
  evidence that support is absent or the learner is unable. Do not make a
  persistent withdrawal/downgrade that depends on unavailable context.

This is semantic integration guidance, not an automatic write, state transition,
or probe trigger. Teaching action remains proportionate under
`teaching-decision.md`; no diagnostic is required merely because support changed.
The Runtime reconciliation operation below does not enforce this judgment.


## Runtime reconciliation boundary

When the ordinary Runtime persists a learner Knowledge change, use the dedicated Knowledge reconciliation operation rather than generic replacement. The operation is a mechanical safety/traceability gate, not the semantic reasoner:

- a missing Knowledge owner may be first-materialized only at revision 1;
- an existing owner is fresh-read and updated under semantic revision + blob/head CAS;
- every newly added support/challenge Evidence reference must be readable under the session's host-owned capability policy and resolve in the same exact Instance snapshot;
- one reconciliation may add at most 32 new Evidence references, with the bound checked before per-Evidence repository I/O;
- newly added references must carry an exact typed capability target matching the Knowledge domain/concept/capability;
- Evidence interpretation.direction must agree with the support/challenge side.

Existing legacy references may remain in current Knowledge, but a new integration cannot use an untyped or unrelated Evidence target merely because the referenced YAML exists. The model/runtime still owns the actual semantic integration judgment under the principles above; this gate does not infer state/confidence from counts.
