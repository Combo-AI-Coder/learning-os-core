# Core #34: ordinary-request cold resume, frozen synthetic evidence

Baseline: `2227fa8fab8abd9a78fca884b3315bf4a459c773`.
Related owner: [Core #34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34).
This docs/tests slice adds no runtime, schema, protocol or deployment change.

## Question and recipe

The previous correction evaluation explicitly asked consumers to consider durable
updates. The intake successor received some teaching context separately from
readback. This exploratory cut asks whether an ordinary “Continue where we left
off” request can recover the teaching activity and correction solely from the
supplied durable documents, with the normal product protocols still present.

The existing synthetic `CorrectionJourney` creates a performance, a provisional
Knowledge interpretation, and optionally a later concrete hint report. The
existing `save_learning_checkpoint` operation persists the conceptual return
point. Producer sessions close. A fresh read-only session discovers Evidence
without a supplied Evidence ID and reads Progress, Knowledge and Evidence in one
final snapshot. Context selection is a fixed complete synthetic recipe; it is not
an autonomous context selector or an actual operating-system crash test.

The first [prospective plan](../../tests/fixtures/core34-cold-resume/prospective-plan.json),
report-present Alpha and report-absent Beta packets and first responses were
retained before replay. A third fresh same-model context received the actual
post-Alpha packet, without the prior response, under a separate
[successor plan](../../tests/fixtures/core34-cold-resume/successor-plan.json).
The frozen files are copied unchanged; this publication does not resample them.
The original plan's C6 wording is qualified below rather than retrospectively
rewritten. The later artifact manifest binds saved bytes, not generation events.

## Observations and replay

- Alpha resumes the saved conceptual activity and proposes removal of stale
  independent-explanation support. It preserves separately supported label recall
  and does not infer inability, mastery or a fabricated second performance.
- The exact first Alpha request is denied in a read-only session (`guard_rejected`)
  with zero writes, then admitted unchanged in the synthetic writable session.
  Only `learner/knowledge/synthetic.yaml` changes. Original Evidence and Progress
  bytes remain unchanged.
- Beta resumes the saved activity, invents no assistance report and proposes no
  host request.
- The fresh successor uses actual persisted narrowed Knowledge and the saved
  activity, preserves uncertainty and label recall, and requests no duplicate write.

The [mechanical replay](../../tests/fixtures/core34-cold-resume/mechanical-replay.json)
is distinct from the [same-model semantic review](../../tests/fixtures/core34-cold-resume/semantic-review.json).
The reviewer reproduced the original replay and successor files byte-for-byte;
C1–C5 and successor semantics pass within scope. C6 and the overall evidence status
remain **partial**. Host admission, hashes and keyword occurrence do not establish
teaching quality. The contrast is strongest in durable interpretation and factual
basis, not a measured difference in learner outcomes.

## Reproduction and validation

From the repository root:

    python -m unittest tests.test_cold_resume -v
    python -m tests.cold_resume_fixture --output /path/to/new/external-directory
    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The exporter refuses an existing destination or a directory inside Core and does
not write frozen evidence. It reproduces the timestamp already stored in each
packet; this retrospective clock freeze must not be presented as a prospectively
fixed sampling clock. Alpha and Beta originally have different checkpoint times.
Artifact JSON is protected against checkout newline conversion. Regression tests
check exact inventory and hashes, exact unmodified request replay, permission
rejection, state preservation, successor correspondence and unchanged partial
assessment. A mutated stale-token request remains rejected rather than repaired.
Historical replay uses the protocol text saved in each original packet, so later
policy edits cannot silently rewrite past inputs. The baseline alignment is
recorded by the frozen semantic review. These tests reproduce mechanics; they do
not rerun model sampling.

The baseline had Core validation with zero errors, 727 local tests (726 passed,
one Windows-only skip), and successful synthetic Instance/Deployment CLI checks.
[Baseline hosted CI](https://github.com/Combo-AI-Coder/learning-os-core/actions/runs/37876337631)
passed on Linux and Windows. A preceding local attempt was invalid because a
harness provenance receipt was placed at the strict Core root; it was removed
before the clean restart. That harness mistake was not a Core regression.
Baseline checks do not validate the new publication candidate: its exact-head
aggregate tests, CLI checks, CI and formal review belong in the PR receipt.

## Limits and next product obligation

Three individual same-model first responses and a complementary same-model review
are not independent-provider diversity, statistical reliability or learner effect.
The full packets include product guidance about hint-based withdrawal and a
tool-aware optional-request format. This is reduced task-specific prompting, not
completely unprompted adoption. Responses lack generation-time input-hash or
invocation attestation; later hashes cannot authenticate what originally generated
them or independently prove first-response status. Those facts are attributed to
the original experiment record.

The fake provider reuses head/blob tokens. Replay does not establish real Git
freshness, concurrency, external durable storage recovery, missing-context handling,
or broad-portfolio robustness. No learner answer, retention or transfer was tested.
No strong-summary, protocol ablation, irrelevant-distractor or ambiguous-report arm
was added in this exploratory cut. Core #34 still owns those comparative and
longitudinal acceptance obligations; no new mechanism is justified solely by this
successful bounded observation. The 28 historical `not_executed` cases and all
prior failures remain unchanged. Development integration, real learner validation,
Runtime-Control promotion and public release remain separate decisions.
