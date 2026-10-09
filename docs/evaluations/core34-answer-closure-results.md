# Core #34: answer-conditioned closing and fresh continuation

Experiment baseline: `73d7d37c8492bae800ef3e31239cd190a4423f23`.
Owner: [Core #34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34).
Status: bounded synthetic evidence; the wider issue remains **partial/open**.
This publication adds docs/tests, not a Runtime teaching mechanism.

## Question and difference from earlier evidence

The earlier [q03 diagnostic-design result](core34-diagnostic-repair-results.md)
established a question's quality and described contingent next actions. It did
not execute feedback on an actual supplied answer or recover that interaction's
later continuation. This slice reuses the exact retained learner-facing question:
a tray is described only by every triangular piece being green; can a selected
green piece be square, and why?

Three authored fictional answers are the **only difference** among the first
consumer B packets. The ordinary supplied request asks B to respond and leave a
sensible stopping point before a fresh session. Each B receives actual existing-
host recovery, current product protocols and the normal owning-operation
interface. It receives no q03 design/rival-answer record, private accepted set,
producer transcript or another consumer's output.

The [prospective plan](../../tests/fixtures/core34-answer-closure/prospective-plan.json)
and [freeze](../../tests/fixtures/core34-answer-closure/prospective-freeze.json)
precede all new consumer invocations. There are **three first B trajectories and
three first C trajectories**, with six distinct input packets. Each distinct
actual post-state receives one fresh successor C. None required duplicate-input
reuse, and no output was resampled or repaired.

## Actual first observations

| Fictional answer | B feedback and executed persistence | Fresh C using only actual durable readback |
| --- | --- | --- |
| Correct conclusion with explicit one-way reason (`elm`) | Confirms the reason; creates one support Evidence record, adds its reference while retaining the old unresolved conflict, and saves the stopping checkpoint | Uses the saved bridge from the current explanation to the older star/red reversal; one fresh read, no writes |
| Explicit reversed implication (`pine`) | Explains the precise reversal with a valid/invalid tray contrast; creates one challenge Evidence record, retains existing support/conflict, and saves the correction and next step | Uses the saved new tray comparison and preserves that no learner response to the correction has been observed; one fresh read, no writes |
| Bare “Yes” (`reed`) | Recognizes only the correct conclusion; keeps the missing reason distinct from its own explanation; saves only a checkpoint | Resumes the saved striped/yellow explanation request, without treating tutor explanation as learner performance; one fresh read, no writes |

All three direction claims remain `conflicted`. This is compatible with a
meaningful contrast: the first two differ in observation, interpretation and
persisted support versus challenge; the third withholds capability inference.
A newer correct answer does not by itself explain the old valid contradiction.
The supported forward capability remains unchanged.

Checkpoint-only persistence in `reed` is genuine answer-conditioned continuity,
not a no-persistence result. It sits outside the Evidence P1/P2 shorthand. The
frozen criteria allow set-valued actions and proportionate natural continuation;
they do not require a different action label, a new probe, or a Knowledge state
transition in every condition.

The [independent-context semantic review](../../tests/fixtures/core34-answer-closure/semantic-review-final.json)
finds that all six retained trajectories satisfy the frozen bounded criteria.
It examines the actual feedback, claim limits, observation identity and
checkpoint/resume behavior. It is same-model quality amplification, not an
independent-provider audit or a success-rate estimate. Host admission, hashes and
keyword occurrence do not establish teaching quality.

## Actual operations and preserved state

- B performed 6, 6 and 3 host calls, respectively. Seven owning writes were
  applied: two create-Evidence, two Knowledge reconciliations, three checkpoints.
- Each C performed one fresh read and zero write attempts. Each final C state
  equals its B state; no new performance was invented or duplicated.
- Original Evidence bytes, the forward capability, milestone statuses, watch and
  avoid-retesting fields are preserved.
- Each current fictional source occurrence is recorded at most once. The bare
  answer is retained only at its useful checkpoint scope.
- All 18 original request byte strings were replayed unchanged. Host result
  values, provider-call traces, changed paths and final states match exactly.
- Every C recovered content/version token equals its own actual B post-state.
  No current-turn question/answer or B-response sidecar is supplied to C.

Support and challenge belong to separate counterfactual worlds sharing a
fictional source-round identifier. They must not be pooled as repeated
independent performances by one learner. A justified reinterpretation event is
not automatically duplicate performance, but these observed successors did not
create one. Later C repair cannot retroactively relabel a B outcome.

## Preparation, provenance and qualifications

The retained hd03 prior observations/claims were copied into the existing
synthetic Topic/unit fixture. Context locator fields were remapped, and Knowledge
revision 1 was advanced to 2 to reconcile over the fake provider's empty
revision-1 owner. Prior semantic content was not rewritten. Existing owning host
operations produced the observations, Knowledge and q03 checkpoint; their actual
preparation trace is retained and reproduced.

Before any consumer, method review found that malformed/non-object requests
could escape the temporary bridge's first receipt. The bridge was corrected
before dispatch to record raw bytes/hash/time before parsing and to stop further
owning writes after a transport/type failure. The local original bridge/reviews
and mechanical negative controls remain preserved. This was preparation repair,
not a repaired model output. No such failed request occurred in the six observed
consumer trajectories.

The closing/successor and deterministic replay helpers were authored after the
prospective criteria froze. They implement the declared recipe and were inspected
in method review; their code is not retrospectively called preregistered. Internal
packet/task-text hashes are not provider-side invocation attestations. The first
native dispatch omitted only the task file's trailing newline, recorded in the
original local receipt; full backend prompts/build/seed are not exposed.

Six packets and six first responses are copied byte-for-byte. Preparation,
post-state, recovery, plan and review records are also retained verbatim. Public
trajectory receipts remove only a machine-specific request-file path and add its
source-log hash/omission disclosure; raw request bytes, parsed requests, results,
UTC times, provider calls and changed paths are unchanged. The publication
manifest distinguishes these derived receipts from verbatim artifacts.

## Reproduction and publication verification

From the repository root:

    python -m unittest tests.test_answer_closure tests.test_fixture_checkout -v
    python -m tests.answer_closure_fixture --output /path/to/new/external-directory
    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The test-only exporter refuses an existing or in-Core destination. It regenerates
all six packets and executes the original requests through existing host/fake-
provider operations. It never calls a model, loads a real learner, rewrites frozen
outputs, or repairs a failed request. Live Core still validates normally, while
historical packet guidance and synthetic time stay frozen rather than silently
adopting a later protocol edit.

Regression tests protect exact inventory/hashes, nested request/result bindings,
complete receipt coverage, actual fresh-read order despite static fake tokens,
one current source occurrence, unaffected state, actual successor recovery and
checkout-byte preservation. These protect this retained experiment; they do not
create a general semantic grader or forbid legitimate future reinterpretation.
Exact-head full/focused validation, hosted Linux/Windows checks, real failures and
formal review belong in the publication PR receipt, not an inferred all-pass ledger.

## Remaining limits

The answers are authored and the source question is known. The closing boundary
is explicitly requested, initial recovery is a fixed complete recipe, and product
guidance/interface are supplied. This does not establish spontaneous stopping,
autonomous context selection, unseen transfer, no-guidance adoption or structured-
state superiority. Existing strong-summary/ablation comparisons are not rerun here.

Consumers and reviewer are instruction-isolated contexts sharing a filesystem;
hard sandbox isolation and independent model/provider diversity are not
established. A bridge call reconstructs the fake provider and opens/closes its
host session. Static fake head/blob tokens and fixed event time do not establish
production freshness, real concurrent CAS, crash recovery or deployment safety.

No actual learner, retention, transfer or learning benefit was tested. No new
Runtime/protocol/schema mechanism is justified by this observation. Core #34,
all earlier failures/partial results and the 28 historical `not_executed` cases
remain unchanged. In particular, Core #45's two different unexplained local
full-suite failures are not erased by this study or its hosted results.
