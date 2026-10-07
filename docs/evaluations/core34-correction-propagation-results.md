# Core #34: bounded prose-linked correction result

Date: 2026-10-07. Source baseline: `99f0bec3f7aa24e446a2470432709a242c483f26`.
Status: mixed prospective synthetic evidence; no uniform correction-safety PASS.

## Product finding

A fresh consumer used the later hint report to remove an unsupported assumption
of independent explanation while preserving the separate label-recall claim.
Its exact first reconciliation request succeeded through the existing host.
A second fresh consumer recovered that persisted result and the original report,
kept independence unestablished rather than inferring inability, and proposed no
duplicate correction. The original performance and later report stayed unchanged.

The same-fact summary consumer reached a different canonical representation:
`unsupported`, accompanied by an explicit “cannot rely, not inability” basis.
The host admitted it, but semantic review leaves that choice unresolved against
the existing rule that unknown claims remain absent. This is a real contract
edge; a mechanical green result does not resolve it.

The correction-removed control continued the planned application with appropriate
caution and proposed no Knowledge write. No arm had to manufacture a different
teaching action merely to look state-sensitive. These observations provide no
evidence that structured state is superior to the same-fact summary.

## What actually ran

The [prospective plan](core34-correction-propagation-plan.md) and frozen manifest
preceded the three first consumer outputs. All 83 baseline source blobs matched
the complete canonical main inventory before the isolated local work began.

1. A fixture producer used `create_evidence` and `reconcile_knowledge` to persist
   one performance and an explicitly provisional initial interpretation.
2. A separate session appended a neutral learner self-report and ended before
   Knowledge reconciliation. The report's prose names the earlier Evidence ID;
   original-performance and later-report times and round IDs stay distinct.
3. Fresh read-only discovery found both records without producer-supplied IDs.
   A final combined context read checked the selected version tokens.
4. Three new native contexts received structured, same-fact field-to-prose, or
   report-removed inputs. They saw current-at-baseline policy excerpts but no
   oracle, corrected Knowledge answer or prior output. The request explicitly
   asked them to consider justified durable updates.
5. The exact first requests from c01 and c02 were replayed unchanged through
   `reconcile_knowledge`. Both were admitted; neither created new Evidence.
6. A fourth fresh context received only the post-c01 durable documents and the
   same task/policy. It did not receive the prior response or producer transcript.
7. Complementary same-model-family review checked actual learner-facing actions,
   Knowledge candidates, frozen criteria and artifact hashes. It preserved the
   c02 uncertainty rather than turning host admission into semantic acceptance.

The four first outputs are immutable evaluation artifacts. No retry replaced an
unfavorable output, and no corrected Knowledge candidate was fixture-authored.

## Per-case disposition

| Case | Evidence | Disposition |
| --- | --- | --- |
| c01, structured | Deletes the now-unknown explanation claim; preserves label recall; exact request admitted | Bounded pass for full Evidence-plus-Knowledge recovery |
| c02, same facts in prose | Stores unsupported with a careful no-inability basis; exact request admitted | Partial/unresolved canonical unknown-versus-unsupported choice |
| c03, report removed | No invented correction and no write; proportionate ordinary application | Bounded ablation pass |
| c04, fresh post-c01 recovery | Recovers assistance qualifier from report, does not restore independence or repeat the write | Bounded successor-recovery pass |

c01 leaves no correction rationale inside Knowledge after removing the claim.
Its successful successor depends on the declared full Evidence-discovery path.
Knowledge-only explanation of the removal is not established. c02 also adds an
`updated_at` to the unaffected label claim; this is metadata churn, not new
learning evidence or a substantive claim change.

## Reproduction and verification

Run `python -m unittest tests.test_correction_propagation -v` for deterministic
recovery, immutable create/retry, neutral-direction rejection, read-only capability,
packet/response hash binding, exact-request replay and simulated stale-token
controls. These tests reproduce storage/gate behavior, not new model responses.

The new fixture and tests use only existing host operations. They change no
production broker, adapter, host, validator, protocol, schema or deployment file.
`tests/fixtures/core34-correction/` retains four packets and first responses,
the frozen policy snapshot and prospective manifest, mechanical replay,
successor receipt and hash-bound semantic review. Existing fixture byte-preserving
attributes apply. The frozen policy snapshot prevents later policy edits from
retroactively changing this experiment's inputs.

Local validation and final review evidence will be recorded in the task/PR
receipt for the exact published candidate. At this local evidence checkpoint,
hosted CI and Codex review had not yet run; later status belongs to that receipt.

## Remaining decision and limits

The result demonstrates a useful existing-contract semantic path, not a new
automatic correction mechanism. Current Core has no typed reinterpretation link,
no neutral-context Knowledge reference and no enforced same-occurrence grouping.
`source.round_id` is turn provenance, not an occurrence key. A model still has to
interpret the prose relation, choose claim scope and judge whether it is justified.

Before a typed correction implementation, settle the narrow contract:

- An explicit per-target reinterpretation relation should reference the original
  immutable Evidence without creating a second independent performance
- Neutral interpretive context should remain traceable without being fabricated
  as support/challenge; the original and report timestamps must retain their meanings
- Withdrawing the sole apparent independent support should leave independence
  unknown/absent unless separate evidence justifies unsupported; confirm this
  distinction against the existing “cannot rely” definition
- Repeated or conflicting reports should trigger semantic reassessment rather
  than latest-wins, automatic downgrade or observation rewriting

This is a recommendation, not accepted product semantics. A prose-only route is
cheaper and already usable but leaves relationship validation to the model. Typed
relations buy explicit recovery/validation and cost schema/admission work; exact
shape and conflict/dangling-link handling need settlement before implementation.

The synthetic provider uses fixed fake blob/head tokens. Static replay and
manually simulated content/token drift do not prove real Git content-addressed
provenance, repeated-write freshness or concurrent-process behavior. Consumer
isolation was instructional in a shared filesystem. One response per arm and
same-model-family review do not establish independence, sustained reliability,
spontaneous repair behavior, structural superiority or learner benefit.

Core #34 remains open. The 28 historical scenarios remain `not_executed`; prior
#39 failures/inconclusive outputs are unchanged. No real learner write, production
promotion, new provider/spend, merge, deployment or product acceptance occurred.
