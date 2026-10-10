# Core #19: narrow intake controls, bounded synthetic evidence

Baseline: `7da297a5e726f4f57b97774557d87167e5a740dd`.
This is Core-only development evidence. No deployment, real learner mutation,
new provider service, broader preference system or project acceptance is implied.

## Implemented capability

The v4 reference host adds Main-bound `set_intake_preference` and
`reset_intake_preference`. Both derive the target from session binding or the
fixed global owner, accept only scope/depth/version token, and retain existing
read/write capabilities. Topic changes require an existing Goal. Explicit global
minimal/thorough can create the minimal lazy Learner Execution; absent balanced
and reset leave it absent. Existing balanced can be explicitly stored. Reset
removes only the selected intake key and preserves unrelated semantic fields.

One fresh pinned authority snapshot decides create/update/no-op inside the shared
write lease. Every outcome passes deployment/generation/session/role/fingerprint
and deployed-validator checks. Writes use exact-ref plus blob CAS or create-only
CAS. The dedicated operation registry and manifest fingerprint change together;
old deployed policy and new host refuse ordinary writes rather than weakening
compatibility. The three-depth resolver is unchanged.

## First-output observation

The [prospective plan](core19-intake-controls-plan.md) fixed policy, case facts
and semantic criteria before sampling. One fresh same-model native consumer
returned eight first decisions in one context; these are not eight independent
trials. Four exact requests were replayed without repair: Topic minimal, first
global thorough, Topic reset, and absent-global balanced. The first three changed
only their allowed owner; balanced returned `applied: false` and created nothing.
The other four cases proposed no write. Actual Plan/Progress and other provider
state remained unchanged. No original response was edited after sampling.

A second fresh native context received only actual durable readback plus the same
policy and known synthetic facts for three successor cases. It recovered Topic
minimal, inherited global thorough after first creation, and inherited global
thorough after Topic reset. It chose to resume the existing activity without
intake questions or further requests. The consumer knew no prior chat response. Some teaching context (time budget,
prior exposure and progress summary) was supplied separately rather than fully
represented in the readback YAML. The durable-only conclusion is therefore intake
preference/owner inheritance recovery, not complete teaching-context recovery.

Independent-of-consumer semantic assessment is retained in the hash-bound
`semantic-review.json`; its scope and findings are distinct from host admission.
The reviewer found all eight first decisions and three successor decisions within
the frozen scope, with no unauthorized persistence, capability inference or
premature save-success claim. Current teaching practice questions remain distinct
from optional background intake; minimal does not prohibit all teaching questions.
Neither a resolver return, a keyword match nor an admitted request establishes
teaching quality. Precise semantic conclusions follow the retained review, while
mechanical tests only reproduce the evidence and reject missing/duplicate rows.

## Reproduce the retained evidence offline

Start from a complete Core checkout and the isolated dependency setup in the
[README](../../README.md#offline-validation-and-retained-evidence). From the
repository root, run the existing focused test module on POSIX:

    ../learning-os-core-venv/bin/python -m unittest discover -s tests -p "test_intake_experience.py" -v

On Windows PowerShell, retain the README's `PYTHONUTF8=1` setting and use
`..\learning-os-core-venv\Scripts\python.exe` for the interpreter.
This runs six deterministic tests: frozen input/case checks, exact request
replay, missing/duplicate response rejection, successor-state binding, frozen
policy preservation and artifact-bound semantic-review coverage. It does not
sample a new consumer, rerun the semantic reviewer or grade teaching quality.
The full Core validator and aggregate tests in the README remain the required
integration checks; this focused command does not replace them.

Follow the existing materials in this order:

1. [Prospective plan](core19-intake-controls-plan.md) and
   [consumer packet](../../tests/fixtures/core19-intake/consumer-packet.json):
   the frozen policy, learner messages, initial state and granted capabilities.
2. [Original consumer decisions](../../tests/fixtures/core19-intake/consumer-response.json):
   `i03` sets Topic minimal, `i04` first-creates global thorough, `i05` resets
   the Topic override to inherit global thorough, and `i08` requests global
   balanced while the owner is absent. The other cases justify no write.
3. [Mechanical replay](../../tests/fixtures/core19-intake/mechanical-replay.json):
   the host receipts, changed paths and durable readback. The first three
   requests change their scoped owner; `i08` returns `applied: false` without
   creating one. [The helper](../../tests/intake_controls_fixture.py) rebuilds
   each case with a fake provider and fixed clock in temporary directories.
4. [Successor packet](../../tests/fixtures/core19-intake/successor-packet.json),
   [original successor decisions](../../tests/fixtures/core19-intake/successor-response.json)
   and [semantic assessment](../../tests/fixtures/core19-intake/semantic-review.json):
   three fresh-context decisions and the retained, artifact-bound review.

The setup starts with an existing synthetic Topic, Goal, Plan, Progress and Main
Branch. The helper supplies the trusted locator, provider, read/write policy,
write gate and generation; the consumer packet supplies owner version tokens.
It does not demonstrate creating a first Topic from an empty installation. In an
actual host session, existing-owner tokens must come from a current
`read_learning_context`; absent-global creation requires verified absence and a
null token. The frozen example tokens are not inputs for a live learner write.
See [scoped intake admission](../reference-host.md#scoped-intake-controls-surface-v4).

`IntakeJourney.recover()` opens a new broker session and reads the post-replay
owners. The later consumer packet also carries `known_context`, including facts
not fully represented in those owners. This replay verifies preference and
inheritance recovery plus the retained artifact bindings, not complete
teaching-context recovery from durable state alone. The original model outputs
and their scope limits remain unchanged. No credentials, real Instance, model
runner or transport service are needed for this offline path.

## Validation and historical compatibility

New deterministic tests cover owner preservation, first creation, no-op absence,
fresh-session inheritance, invalid request/owner, role/capability limits, revoked
session, frozen deployment, generation drift, stale tokens, ref drift, create
races and concurrent unrelated edits. The trusted fake provider and fixed clock
make replay deterministic; they do not prove real Git provenance or multiprocess
concurrency. Current-only and local teaching instructions remain consumer
interpretation, not an NLP classifier or broker-enforced intent detector.

The first full Linux run exposed 12 historical Core34 replay failures caused by
v3 response-envelope equality after the additive v4 surface change. No original
policy, first output, packet, receipt, criterion or historical manifest was
regenerated. One live regression source needed to evolve; its original bytes were
archived at `tests/fixtures/core34-source-history/` and a single explicit mapping
continues to verify the original manifest hash. All other historical paths stay
unchanged. Checkout parity covers both live and archived files.

The tests-only compatibility comparator permits exactly v3-to-v4 at the legacy
operation response envelope or historical coverage-error location, validates the
complete envelope shape and compares every other field with type sensitivity.
It does not change runtime responses or ignore arbitrary payload versions.
Complementary internal review caught an initially overbroad recursive exception;
that gap was repaired with nested-payload and malformed-envelope negative cases.
The first finding remains in the review record instead of being relabelled as
caught by the original implementation.

Local Core validation, aggregate tests, both workflow synthetic CLI surfaces,
exact published head/tree, hosted CI and subsequent Code/Security Review status
belong to the task/PR receipt. A previous head's pass is not reused. Required
validation and applicable review-entry rules remain separate from merge gates.
Hosted platform coverage that did not execute remains missing, not a pass.

## Limits and remaining outcome

This demonstrates bounded prompted behavior and owning-operation mechanics on
controlled synthetic input. It does not show real learner benefit, long-run
reliability, unprompted adoption, cross-provider diversity, user acceptance or a
production host rollout. Resource token/compute/cost metrics are unavailable and
are not inferred from agent counts or elapsed time. Core #19 remains open for
broader experience acceptance; this is not a prerequisite for #34's existing-Topic
longitudinal work. Merge, deployment and real learner observation remain separate
authorization and acceptance boundaries.
