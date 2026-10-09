# Core #34: consumer-selected bounded context acquisition

Source: main `0c1611568c9222d1329a6963bd64c93c5c3b5ec7`, tree
`593090cf68c7e56f5c319602ade9da48a313f5f8`.
Owner: [Core #34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34).
Status: bounded synthetic acquisition evidence; the wider issue remains **partial/open**.
This publication adds docs/tests, not a product selector or Runtime mechanism.

## Question and actionable conclusion

The earlier [answer-closure successors](core34-answer-closure-results.md) received
a fixed complete recovery trace. Here a fresh consumer starts with only the
ordinary request “Continue where we left off,” bound Topic/Subtopic, generic
canonical path vocabulary, product guidance and existing read-operation contracts.
No durable state, concrete Evidence ID, Knowledge-owner path, prior question/answer
or expected next action is supplied in that initial packet.

All three first consumers chose useful requests themselves: read Subtopic
Progress, discover context-matching Evidence, then read the Knowledge owner
actually returned by discovery. The existing operations suffice for these
product-guided, rich-checkpoint continuations. This observation supplies no reason
to add a new selector for this slice. It does not settle wider search requirements,
prove that all retrieved content was necessary, or estimate reliability.

## Prospective design

The [plan](../../tests/fixtures/core34-context-selection/prospective-plan.json),
[freeze](../../tests/fixtures/core34-context-selection/prospective-freeze.json) and
[method review](../../tests/fixtures/core34-context-selection/prospective-method-review.json)
precede all consumer host calls. The freeze occurred at 15:50:55 UTC on 2026-10-09.
One shared baseline supports the smallest pair of declared semantic contrasts:

- baseline: unchanged retained q03 elm post-state;
- relevant saved-position contrast: unchanged retained q03 pine post-state;
- irrelevant contrast: baseline plus one same-context Evidence record about an
  unrelated completed counting activity, created with the existing owning operation.

There is **one identical initial semantic packet and three first interactive
trajectories**, not three different semantic initial inputs. Opaque handles and
assigned local paths differ; actual later operation results depend on the stored
world. No consumer was resampled and no first output/request repaired. One result
per coherent world does not identify a causal effect separately from model variation.

The fixed read policy allows Knowledge, Evidence and the bound Subtopic namespace;
it grants no write capability. Budgets are existing per-operation contracts:
32 combined required/optional paths per read; discovery 128 candidates, 16 matches,
16 owners, 8 targets per record/64 total, 64 KiB per document and 256 KiB scanned
Evidence plus Knowledge. No experiment-wide call, token or duration ceiling was
invented. The criteria permit a supported checkpoint-only continuation and do not
prescribe discovery, a gold path list, a particular read order or an action label.

## Actual observations

| Stored world | First learner-facing continuation | Actually obtained contents |
| --- | --- | --- |
| Saved correct one-way explanation, older contradiction unresolved | Connects the triangular/green explanation to the earlier star/red reversal without claiming mastery or repeating the closed question | Progress, four Evidence records and current Knowledge: six distinct document contents |
| Correction saved, no learner response to it observed | Gives a new two-tray square/blue comparison distinguishing a valid blue circle from an invalid red square; retains the unresolved conflict | Progress, four Evidence records and current Knowledge: six distinct document contents |
| Baseline plus counting Evidence | Stays on the directional explanation and star/red connection; no counting detour or new relevant capability claim | Progress, five Evidence records, including the visible counting record, and current Knowledge: seven distinct document contents |

All three made three successful host calls. Their first-call arguments differ:
baseline made both Progress and Definition optional; the other two required
Progress and made Definition optional. Every actual result returned Progress and
reported Definition in `missing_optional`. Missing Definition was not inflated
into absent Evidence or a need to restart intake.

Each Knowledge locator came from actual discovery before its content was requested.
Topic and Domain happen to share the string `synthetic`, but no observed consumer
relied on guessing that mapping. Discovery's internal Knowledge read and returned
owner/reference metadata are distinct from the separate model-visible content read.
The outputs were thus portfolio-checked, although these rich checkpoints could
have supported a narrower acceptable response.

The irrelevant record was actually delivered and acknowledged only in acquisition
accounting. This is one observed non-diversion with a **visible** non-target record,
not merely invariance under an unseen backend addition or general distractor resistance.

The [semantic/method review](../../tests/fixtures/core34-context-selection/semantic-method-review.json)
finds that all three satisfy the applicable frozen criteria. It inspects actual
teaching content and claim boundaries; host admission, hashes and format flags
are not semantic graders. It is same-family independent-context review, not an
independent-provider audit.

## Mechanical evidence and provenance

Nine original requests replay exactly through existing host/fake-provider operations:
result values, provider-call traces and full states match. There were zero consumer
write attempts, unsupported operations, failed operations or state changes. No new
learner performance/source occurrence was created; common history across the
counterfactual worlds must not be pooled as repeated performances.

Within each trajectory there was no repeated unchanged document content. Encoded
consumer-visible result bytes were 10,299 / 9,951 / 11,663; delivered document-content
bytes were 6,340 / 5,997 / 7,051. Backend traces had 52 / 52 / 53 entries, including
authority/materialization work. These are neither token counts nor production cost
measurements. Nine requests comprise four semantic request shapes and seven raw
byte hashes, not nine independent consumer trials.

The common packet and three first responses remain byte-identical. Eleven source
records are retained verbatim. Three derived trajectory files remove only a
machine-specific request-file path and append source-log hashes/omission disclosure;
raw request bytes, parsed requests, UTC times, results, provider calls and state
comparisons are unchanged. Other derived provenance records explicitly describe
their summaries/omissions. The manifest distinguishes these from verbatim evidence.
Existing q03 states are reused by exact dependency hash rather than copied again.

Original orchestration task texts and generic reporting/authorization text remain
outside publication. The declared submitted-task hashes and common pre-dispatch
append relationship are retained. The disclosed append introduced no source fact,
oracle or read recipe, and its effect was not separately ablated. Local dispatch
receipt times are receipt-creation times, sometimes after the first host read;
they are not exact backend invocation timestamps. Operation receipts establish the
read route but do not independently attest complete native filesystem/tool history
or provider-side prompt/build/seed inputs.

## Preserved failures and method repairs

The first preparation failed on an incorrect public schema heading label after
successfully creating the synthetic distractor. The heading was corrected before
freeze/consumer dispatch; both creation receipts and original failure hashes remain
represented in the [preparation record](../../tests/fixtures/core34-context-selection/preparation-diagnostics.json).
They refer to separate temporary copies of one authored fixture, not two learner
performances. No failed model output was replaced.

Pre-dispatch method review found receipt collision/overwrite risk and lost
provider/state evidence on exception paths in the temporary bridge. Repairs and
operator-only malformed-request, denied-write, missing-read, exception and
concurrent-rejection negatives remain documented. The sealer separately accounts
for known owning writes, unsupported operations, transport rejections and missing
raw-byte limitations. None of these operator tests is counted as a consumer result.

The replay helper was authored after freeze as deterministic postprocessing. The
publication helper is a separate checkout-relative implementation; neither is
retrospectively called preregistered code. Publication validation failures and
exact-head CI/reviews belong in the PR receipt, not an inferred all-pass ledger.

## Reproduction and limits

From the Core repository root:

    python -m unittest tests.test_context_selection tests.test_fixture_checkout -v
    python -m tests.context_selection_fixture --output /path/to/new/external-directory
    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The exporter refuses existing or in-Core output destinations. It regenerates the
common historical packet, reconstructs the three worlds and replays all retained
requests without a model, real provider or frozen-output rewrite. Regression guards
cover inventory/hashes, dependency identity, exact raw/parsed/result bindings,
complete receipt/review coverage, discovered-owner ordering, optional absence,
visible distractor inclusion, full state/source-occurrence preservation and Git
checkout byte parity. They protect this witness, not a universal read sequence.

There are only nine or ten stored document paths and unusually helpful checkpoints.
Unknown bindings/schema, sparse or misleading checkpoints, large/noisy portfolios,
ranking/minimal-read efficiency, capacity recovery and cross-topic selection remain
untested. Same-family instruction isolation shares a filesystem and is not a hard
sandbox. Static fake tokens and reconstructed sessions do not prove production
freshness, concurrent CAS, durable transport or process-crash recovery.

No real learner, subsequent answer, retention, transfer or learning benefit was
measured. Core #34, earlier failed/partial results and 28 historical `not_executed`
cases remain unchanged. Core #45's two different unexplained local full-suite
failures are not erased by later green checks. This slice does not authorize
Runtime changes, deployment or product acceptance.
