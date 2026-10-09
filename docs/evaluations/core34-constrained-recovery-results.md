# Core #34: actual constrained-read synthetic comparison

Experiment baseline: `d6ddb41490d761bdc8e4f25ba7dee7d1dd84278d`.
Owner: [Core #34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34).
Status: bounded, policy-conditioned synthetic observations; the wider issue
remains **partial/open**. This publication changes docs/tests only.

## Question and difference from earlier evidence

The earlier withdrawn-support gap packets were trimmed after unrestricted
recovery. The cold-resume slice used a fixed complete synthetic recovery recipe;
its genuine write-denial check did not establish constrained reading. This
comparison supplies the actual outputs of constrained existing-host reads from
the start, without a successful unrestricted recovery or producer transcript
being supplied to the consumer first.

The fixed external recipe first reads saved Progress and current Knowledge,
then calls `discover_learning_evidence`. A failure stops discovery recovery. A
success is followed by one required combined read of Progress, Knowledge, all
discovered Evidence, and canonical Evidence paths referenced by the recovered
Knowledge. The fresh consumer receives the complete actual request/result trace,
normal product protocols, the ordinary request "Continue where we left off,"
and a common output format including a coverage statement. It does not receive
arm names, hidden source records, expected answers, or a coverage sidecar.

The harness chooses this recipe; the model does not autonomously select or
execute recovery operations. Trusted host startup still performs its ordinary
validation. This is not a claim that the host itself never materializes source
state while enforcing the model-facing capability policy.

## Prospective conditions and preserved first results

The [revised prospective plan](../../tests/fixtures/core34-constrained-recovery/prospective-plan.json)
was frozen before revised fixture runs and every consumer invocation. The five
conditions have identical initial Progress/Knowledge and product guidance.
There are **four unique input packets and four first native outputs**. Denial
and capacity rejection have byte-identical public envelopes and share one first
output; they are not two independent consumer trials.

| Condition | Actual existing-host read behavior | First-output observation |
| --- | --- | --- |
| Full visibility (`cedar`) | Discovery and final combined read recover both original performance and concrete hint report | Remove only stale independent-explanation support; preserve label recall and continue the saved activity |
| Read authority denied (`iris`) | Discovery rejects before any Evidence-content read | Treat the portfolio as unrecovered, continue proportionately, and request no durable withdrawal or downgrade |
| Original Evidence missing after bootstrap (`juniper`) | Discovery returns the report; final required read rejects because the original referenced performance is unavailable | Use the visible report cautiously but preserve uncertainty and request no durable change |
| Matching-record capacity exceeded (`laurel`) | Seventeen context-matching records cross the existing sixteen-record limit; discovery discards the entire result | Same observable packet/output as `iris`, without inventing a failure cause |
| No report in the explicit scope (`maple`) | Successful bounded discovery and final read contain only the original performance | Invent no hint or correction; retain provisional claims and continue |

All four distinct replies continue the stored conceptual sentence-prefix
comparison. The frozen criteria permit that invariance: incomplete evidence need
not force a new diagnostic or interrupt safe existing activity. The meaningful
contrast is the evidence basis and persistence decision, not a requirement to
produce different teaching prose in every condition.

The sole proposed request, from `cedar`, was replayed unchanged in the fictional
writable provider. It was admitted and changed only
`learner/knowledge/synthetic.yaml`. Label recall, original Evidence and Progress
were preserved. Empty-request cases invoked no mutation. Mechanical admission is
separate from the retained [semantic and causal review](../../tests/fixtures/core34-constrained-recovery/complementary-review.json).
That complementary native review supports the applicable frozen criteria for
these observed cases, not a success-rate or independent-provider claim.

## Failed preparation and audit corrections

The [first plan](../../tests/fixtures/core34-constrained-recovery/initial-prospective-plan.json)
removed referenced Evidence before fresh-session bootstrap. Host startup rejected
that invalid Instance with `reference.evidence_missing`; no consumer ran on that
attempt. A [derived public-safe failure record](../../tests/fixtures/core34-constrained-recovery/initial-preparation-failure.json)
retains the result and raw-log hash without publishing machine-specific traceback
paths. The original local log remains unchanged.

The revised missing condition opens a valid session and recovers Progress and
Knowledge before deliberately deleting the original fictional Evidence record
and advancing the fake Instance head. Its unchanged-state receipt compares
against state **after that fault injection**. It proves no additional mutation
by subsequent reads, not a mutation-free setup or a valid initial dangling
reference. It does not claim production snapshot/concurrency behavior.

The original clock receipt incorrectly described a seven-hour UTC clock skew
based on local file-display time. The recorded UTC timestamps agree to the
displayed second. The [erratum](../../tests/fixtures/core34-constrained-recovery/audit-errata.json)
corrects that annotation without rewriting the original receipt, result summary,
inputs or first outputs. Exact subsecond ordering is not established. Packet
hashes existed before dispatch and were included in the consumer tasks, but
these internal records are not provider-side generation attestations.

The initial mechanical receipt includes named checks weaker than their names
alone might imply: a top-level-key check did not prove nested provenance, and a
capacity assertion used the literal 17. The retained review inspected nested
traces and the actual source bound. Publication regression tests additionally
reproduce exact nested packet bytes and provider-call traces, check the live
bound, and include nested-tampering and surplus-inventory negative controls.

## Reproduction and publication verification

From the repository root:

    python -m unittest tests.test_constrained_recovery tests.test_fixture_checkout -v
    python -m tests.constrained_recovery_fixture --output /path/to/new/external-directory
    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The exporter refuses Core or an existing destination. It reproduces fixed
synthetic clock values and saved product-policy text; it never samples a model,
rewrites frozen observations, or repairs a returned request. A later live policy
edit is not silently inserted into historical input. Host startup continues to
validate the live Core normally.

The tests protect exact frozen inventory/hashes, common facts, actual failures,
unchanged first-request replay, unaffected state, and Windows/Linux checkout
bytes. A separate deterministic at-limit control establishes that sixteen
matching records can be discovered successfully. A read-only replay rejects the
unchanged proposed request before separately granted fake write authority; a
changed stale-token request is rejected without repair. These are later
mechanical regression controls, not additional model trials.

The original external offline run reproduced all five packets and request
outcomes with no model resampling. That is provenance for this slice, not a pass
for the publication candidate. Exact-head focused/aggregate validation, hosted
Linux/Windows checks and formal review belong in the PR receipt.

## Limits and remaining obligations

- Capacity means the existing **matching-record count** bound here, not token,
  time, per-document-byte or aggregate-byte exhaustion, pagination, or a useful
  fallback strategy. Fifteen neutral synthetic notes are not new performances.
- No report is confirmed only among the explicit Topic/Subtopic context matches
  in the successful bounded snapshot, not across topics or legacy history.
- The ordinary request still includes full product guidance and an explicit
  coverage output field. No policy ablation or structured-state superiority is
  established. The guidance already discusses unavailable context.
- Native contexts are instructionally isolated in a shared filesystem.
  Independent model/provider diversity and unseen generation history are not
  established. Four first outputs are not statistical reliability evidence.
- Fixed fake blob/head tokens and synthetic intervention do not establish real
  Git freshness, concurrent writes, operating-system crash recovery, or external
  durable storage behavior. The host does not enforce semantic judgment.
- No actual learner response, retention, transfer or learning benefit was tested.
  The earlier failures, #44's partial assessment and all 28 historical
  `not_executed` cases are unchanged.

This narrows one actual-read evidence gap. It creates no new runtime operation,
protocol, schema, capability policy, deployment, real learner state, merge
authority, public-release decision or product acceptance.
