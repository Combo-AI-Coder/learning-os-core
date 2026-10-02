# Intake control and Git-context audit - 2026-10-02

Status: tested implementation proposal; not deployed and not learner acceptance.
Baseline: `ff99a83306dd8c9a77566462d912d563e4691443`.

## 1. Confirmed Git caller-context defect

Context-free Git calls inherited the caller's working directory even though
HOME, system/global Git configuration, SSH settings and repository bindings were
isolated. A synthetic broken caller `.git` file made public `materialize()` fail
on an otherwise valid bound repository. A synthetic local configuration marker
was visible to context-free Git configuration inspection. Both conditions were
reproduced on the pristine implementation before changing the provider.

Context-free calls now use the existing provider-owned empty home, with its parent
excluded through `GIT_CEILING_DIRECTORIES`. Explicit repository working directories
remain unchanged. This is an availability/configuration-isolation correction;
it does not claim a demonstrated credential leak or remote-code execution.

The ceiling is necessary: moving to a nested empty directory alone would still
allow Git to discover the parent's repository. The negative test checks that
parent configuration is not inherited while normal branch validation still works.
Git's documented discovery and ceiling behavior is the relevant external contract:
https://git-scm.com/docs/git#Documentation/git.txt-GITCEILINGDIRECTORIES

## 2. Bounded product slice for issue #19

`intake_policy.py` provides a pure, immutable reference resolver with three modes:
minimal, balanced and thorough. Priority is current explicit instruction, then
Topic preference, then learner-global preference, then Core's balanced default.
A current immediate-start request defers intake in every mode. Known context is
reused; thorough is not an exhaustive survey or mandatory placement test.

The existing Topic Goal `goal.preferences.intake_depth` and lazy Learner Execution
`preferences.intake_depth` own persisted scoped choices. Remove a scoped key to
restore inheritance. A one-turn request is not a durable default. Existing
revision, role, generation and CAS boundaries remain in force.

The Instance validator rejects invalid explicit modes/types in both owners.
Existing missing preferences retain prior balanced behavior; no new storage family,
schema axis, authority, teaching engine, generated question bank or learner data
is added. The broker's synthetic Core fixture now copies the helper alongside
the validator so standalone policy verification exercises the real dependency.

This completes the preference contract/reference-resolver slice, not all of #19.
Natural-language interpretation, host presentation, sparse domain probes and
measured fresh-learner experience still need targeted product acceptance.

## 3. Verification and CI

Baseline Windows Python 3.13.14: 492 tests, 482 passed and 10 platform skips.
Final Windows: 503 tests, 493 passed and 10 platform skips.
Final Linux Python 3.14.4: 503 tests, 502 passed and one platform skip.
Core structural validation reports zero errors; diff whitespace checks pass.

New coverage is two Git-context tests and nine intake test methods, including
64 precedence combinations, immediate-start behavior, reset/non-mutation, stale
lower-priority preferences and valid/invalid persisted values in both owners.
The absent intake feature was first demonstrated by the new suite's import error;
this is missing-feature evidence, not a newly discovered security defect.

An initial Windows Store-alias test run timed out; native Python passed. A WSL
run from a Windows-created worktree also failed because ambient Git discovery
encountered its Windows-format gitdir pointer. Neither failed run is counted as
a pass. The latter motivated the independent synthetic caller-context repro;
final suites include both new isolation controls.

CI pins the Actions revisions already used by the sibling Harness, disables
credential persistence, declares read-only contents permission, bounds job time,
and adds Windows unit/validation coverage while preserving Linux CLI fixtures.

Reproduce:

```sh
python scripts/validate_learning_os.py . --core
python -m unittest discover -s tests -v
```

## 4. Closure and boundaries

Persistence is target-owned implementation, synthetic tests, protocol/schema
documentation and this audit evidence. The two reproduced Git-context guards and
deterministic preference contract are verified; broader teaching efficacy remains
open with fresh-learner acceptance as its trigger. Review is same-agent, not an
independent security assessment or formal proof.

The prior REST provider hardening is already in baseline PR #26; this work does
not re-credit those changes. There is no Runtime-Control pin/epoch update, live
Instance write, canary activation, main-branch merge or production promotion.

## 5. Hosted Windows fixture correction

The first hosted run, `37030858220` at `55c9bdf`, passed Linux but found one
Windows fixture error in 503 tests. The pre-existing relative-filesystem-remote
test called `os.path.relpath` from the checkout on drive D: to a temporary remote
on drive C:. That relative path cannot exist; the exception happened before any
provider operation. All intake and new Git-context controls passed in that run.

The fixture now constructs the relative remote from its own temporary directory,
then explicitly changes the caller directory before materialization. It preserves
the absolute-binding and exact-commit assertions and strengthens the actual
stabilization check. No production code, platform skip or CI error suppression
is introduced. The focused test passes on native Windows and Linux. Hosted
verification for the correction is recorded in the accompanying PR checks; the
failed first run is retained as evidence rather than being described as a pass.
