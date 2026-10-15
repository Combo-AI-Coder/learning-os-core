# Offline synthetic reference example

Run two existing examples and inspect why their persisted state leads to a
particular continuation. You need Python 3.12, a Git client on `PATH` accepting
`--no-lazy-fetch`, a POSIX shell, and network access to obtain the public source
and its declared dependency.
The example executions use local synthetic data, with no model calls, accounts,
credentials, real learner state, or private Instance repository.

The Git provider needs `GIT_NO_LAZY_FETCH` plus filtered fetch/refetch behavior.
Upstream [Git 2.45 introduced the no-lazy-fetch interface](https://github.com/git/git/blob/v2.45.0/Documentation/RelNotes/2.45.0.txt);
[Git's manual](https://git-scm.com/docs/git/2.45.0) documents the environment
variable. This recipe conservatively requires the CLI interface and probes it
before setup, then tests actual provider materialization before either exporter.
Git 2.52.0 was used for the original Linux replay. Some older builds have an
[environment-only backport](https://github.com/git/git/commit/7b70e9efb18c2cc3f219af399bd384c5801ba1d7);
their version number alone does not establish compatibility with this recipe.

These are **two separate chapters, not one end-to-end learner journey**:

- **A. Answer-conditioned continuity:** replay retained fictional answers through
  the reference host and a fake provider; inspect Evidence, Knowledge, Progress,
  and the retained fresh-session replies.
- **B. Local-Git recovery:** run a scripted integration example against three
  disposable local repositories; inspect acknowledged writes, producer loss,
  fresh-process recovery, retries, and rejected conflicts.

Chapter A does not prove Git crash/concurrency behavior. Chapter B does not
validate Chapter A's teaching decisions. Neither is a live tutor, an arbitrary-
input learning session, or evidence of learner benefit, retention, or transfer.

## 1. Set up and run

Use a new disposable directory. All outputs and the virtual environment stay
outside the source checkout. Do not point these commands at a real Instance.

The recipe pins executable/fixture baseline
[`39ad24e70efb39141b36ffea9771eb74ee9698d7`](https://github.com/Combo-AI-Coder/learning-os-core/tree/39ad24e70efb39141b36ffea9771eb74ee9698d7).
That older snapshot does **not** contain this guide. A later guide/review candidate
has its own commit or tree identity; a validation receipt must record that
identity separately instead of calling this baseline the guide's commit. A Core
development snapshot is not a deployment pin.

```sh
set -eu
if ! git --no-lazy-fetch --version; then
  printf '%s\n' 'Stop: install a Git client supporting --no-lazy-fetch; do not bypass this check.' >&2
  exit 1
fi
BASE=$(mktemp -d "${TMPDIR:-/tmp}/learning-os-example.XXXXXX")
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONOPTIMIZE
git -c core.autocrlf=false clone --no-checkout \
  https://github.com/Combo-AI-Coder/learning-os-core.git "$BASE/core"
cd "$BASE/core"
git -c core.autocrlf=false checkout --detach \
  39ad24e70efb39141b36ffea9771eb74ee9698d7
test "$(git rev-parse HEAD)" = 39ad24e70efb39141b36ffea9771eb74ee9698d7
test -z "$(git status --porcelain)"
python3.12 -m venv "$BASE/venv"
PY="$BASE/venv/bin/python"
"$PY" -m pip install -r requirements-dev.txt
"$PY" --version
git --version
"$PY" -m pip show PyYAML
"$PY" -m unittest -v \
  tests.test_runtime_adapter.GitCliProviderTests.test_materialize_uses_host_bound_identity_and_strips_git_metadata
"$PY" scripts/validate_learning_os.py . --core
"$PY" -m tests.answer_closure_fixture --output "$BASE/answer-closure"
"$PY" -m tests.git_recovery_fixture --output "$BASE/git-recovery"
test -z "$(git status --porcelain)"
printf 'Source: %s\nAnswer closure: %s\nLocal Git: %s\n' \
  "$BASE/core" "$BASE/answer-closure" "$BASE/git-recovery"
```

The dependency range `PyYAML>=6.0,<7` is not a locked environment; retain the
resolved version with your results. Do not use Python's `-O` option or set
`PYTHONOPTIMIZE`: the Git example requires assertions. This is a POSIX recipe;
it does not establish a Windows run. See the [README](../README.md#offline-validation-and-retained-evidence)
for the separate Windows validation setup.

Expected top-level results:

1. The provider preflight runs one passing test against disposable local Git
   repositories. It exercises the provider's blob-free metadata and bounded
   hydration path; accepting the CLI flag alone is not this behavior check.
   The Core validator then exits 0 with `validated Core snapshot: 0 error(s)`.
2. The answer exporter exits 0 without a success banner and creates seven files:
   `mechanical-replay.json` and `{elm,pine,reed}-{b,c}-packet.json`.
3. The Git exporter exits 0 and prints JSON with `status: "passed"`,
   `process_count: 8`, and this run's `source_identity_sha256`.

Stop on any unexpected exit or mismatch; see the stop rules below. A preflight
failure stops before either exporter; preserve its diagnostics and correct the
Git/environment prerequisite rather than bypassing the provider's guards.

### Review or integration: validate the actual candidate

The recipe above leaves the shell in the older baseline checkout. Its results
are **not candidate validation**. The [agent contract](../AGENTS.md) requires
both the Core validator and full suite on the exact candidate under review.

Separately obtain a clean checkout of that reviewed commit. Set `CANDIDATE_DIR`
to its absolute path, `EXPECTED_CANDIDATE_SHA` and `EXPECTED_CANDIDATE_TREE` to
the full commit/tree IDs from the reviewed PR or independently verified receipt,
and `CANDIDATE_PY` to an absolute Python 3.12 executable with that candidate's
declared dependencies installed. Do not copy the baseline pin or derive the
expected IDs from whichever checkout happens to be current. Run this block in
a subshell so the later example-inspection commands keep their baseline context:

```sh
(
  set -eu
  : "${CANDIDATE_DIR:?Set the separate candidate checkout path}"
  : "${EXPECTED_CANDIDATE_SHA:?Set the full reviewed candidate commit SHA}"
  : "${EXPECTED_CANDIDATE_TREE:?Set the full reviewed candidate tree SHA}"
  : "${CANDIDATE_PY:?Set the absolute candidate Python executable}"
  cd "$CANDIDATE_DIR"
  test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
  test "$(git rev-parse HEAD^{tree})" = "$EXPECTED_CANDIDATE_TREE"
  test -z "$(git status --porcelain)"
  export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
  unset PYTHONOPTIMIZE
  git --no-lazy-fetch --version
  "$CANDIDATE_PY" --version
  "$CANDIDATE_PY" -m pip show PyYAML
  printf 'Candidate commit: %s\nCandidate tree: %s\n' \
    "$EXPECTED_CANDIDATE_SHA" "$EXPECTED_CANDIDATE_TREE"
  "$CANDIDATE_PY" scripts/validate_learning_os.py . --core
  "$CANDIDATE_PY" -m unittest discover -s tests -v
  test -z "$(git status --porcelain)"
)
```

Retain both check results with those IDs. An unpublished staged candidate needs
an independently supplied expected tree plus patch identity and a verified
index/worktree match; it must not be represented as this clean-commit workflow.

## 2. Chapter A: answer-conditioned continuity

Start in the source directory
[`tests/fixtures/core34-answer-closure/`](../tests/fixtures/core34-answer-closure/).
Read `initial-provider-state.json`, then each world's row in the exported
`mechanical-replay.json`: `post_state.docs` contains the resulting documents,
and `successor_state` must equal `post_state` after the read-only continuation.
The three worlds start from separate copies of the same initial state.

The initial Knowledge revision 2 supports applying the stated forward
relationship but keeps direction handling `conflicted`, with earlier support
and challenge. Progress revision 2 resumes the question about whether a green
square is allowed when every triangle is green. The completed foundation and
the instruction to avoid retesting it are already present.

| World | Retained observation | Expected durable change | Retained next-action basis |
| --- | --- | --- | --- |
| `elm` | Correct yes with an explicit one-way explanation | New support Evidence `evi_20261009_q03_001`; Knowledge 2 → 3; Progress 2 → 3 | Connect this explanation to the older red/star reversal; the older contradiction is unresolved. |
| `pine` | Explicitly reverses the implication | New challenge Evidence `evi_20261009_q03_direction_01`; Knowledge 2 → 3; Progress 2 → 3 | Continue from the recorded correction with a new tray comparison; no response to that correction has yet been observed. |
| `reed` | Correct bare yes without the requested explanation | Progress 2 → 3 only; no new Evidence; Knowledge remains revision 2 | Ask for an explanation of a fresh example. Tutor-provided explanation is not new learner performance. |

For each world, read the source's `{case}-b-response.json` and
`{case}-c-response.json` beside the new output. Their `reply`, `basis`, and
`persistence_summary` fields explain the observed choice. **These replies are
retained historical artifacts, not newly generated replies; the exporter does
not copy them into its output.** The source's `{case}-{b,c}-trajectory.json`
files retain the exact requests, receipts, and changed paths.

The replay checks the frozen manifest/inventory, regenerates six byte-identical
packets, and replays 18 original requests: B uses 6, 6, and 3 calls for `elm`,
`pine`, and `reed`; each C uses one read and no writes. B makes 3, 3, and 1 owning
writes respectively. Those 18 exclude fixture preparation and the additional
discovery/read recovery calls. All three keep direction handling `conflicted`; changed
references and explanations still carry new information. Old Evidence, the
forward capability, and unrelated Progress fields survive. `reed` is meaningful
checkpoint continuity without a new capability claim.

This chapter uses the synthetic `BrokerProvider`: its head/blob tokens and
`a…a` Core token are fake, not real Git identities. Helpers establish the
synthetic Main/Subtopic and active generation 3, then seed the initial state.
The host receives trusted locator, capability policy, and admission separately
from requests. Recovery discovers Evidence and reads actual durable state; it
does not use the preceding reply as a sidecar. The temporary provider directories
are removed: there is **no exported working `instance/` tree** in this chapter.
The exported JSON is its durable-state representation.

For the original study and its limitations, see
[answer-closure results](evaluations/core34-answer-closure-results.md).

## 3. Chapter B: local-Git recovery

Open `$BASE/git-recovery/result.json`, `source-identity.json`, and
`process-records.json`. Each numbered `.stdout`/`.stderr` pair preserves a child
process's result; its record includes independent Git before/after observations.
The output also contains `core.git`, `control.git`, `instance.git`, their seed
checkouts, and child temporary directories. These repositories are disposable
local fixtures, not your source checkout or a real deployment.
To inspect the authoritative final Instance bytes, read the bare repository's
`refs/heads/main`, for example:

```sh
git --git-dir "$BASE/git-recovery/instance.git" show \
  refs/heads/main:learner/knowledge/synthetic.yaml
```

Do not assume a seed checkout mirrors every provider write.

This chapter starts a **different Instance**: synthetic config, a Main registry,
Branch Runtime at generation 3, and empty revision-1 Knowledge. It has none of
Chapter A's previous answers or Progress. The fixture copies Git-tracked regular
source files into a new local Core commit, pins that test commit in synthetic
Control, and gives each host only its declared read/write capabilities. It does
not initialize real authority, acquire a generation, or take over a session.

| Child process | Required outcome | Reachable Instance commits |
| --- | --- | --- |
| `produce` | Evidence acknowledged applied, then intentional exit 73 | 1 → 2 |
| `read` | One eligible Evidence, `not_referenced_by_current_knowledge` | 2 → 2 |
| `reconcile` | Actual read's Git blob token used; Knowledge applied | 2 → 3 |
| `read` | Same Evidence is now `referenced` | 3 → 3 |
| `retry` | Identical Evidence retry has `applied: false` | 3 → 3 |
| `conflicting_evidence` | Reusing its ID for a different observation gives `guard_rejected` | 3 → 3 |
| `stale_blob` | Wrong Knowledge blob token gives `cas_conflict` | 3 → 3 |
| `race` | Competitor changes README; stale exact-head write gives `cas_conflict` | 3 → 4 |

Only Evidence, then Knowledge, then the competitor's README change enter
Instance history. Core and Control refs/trees remain unchanged. Commit hashes
and timings vary; compare these semantic outcomes and source-file blob identities,
not a historical run's generated commit IDs. The copied Core test commit is
distinct from the recipe's source commit; `source-identity.json` records what
was actually copied.

`not_referenced_by_current_knowledge` describes the present references, not
proof that the Evidence was never considered. Reconciliation here is a scripted
synthetic choice, not independent pedagogical reasoning. The producer exits
**after** its Evidence write was acknowledged; this is not an in-flight write
crash or power-loss test. Each fresh process has its own admission object, so
this does not establish shared multiprocess admission, a real Host/GitHub
durability result, or deployment readiness. See [local-Git recovery results](evaluations/core34-git-recovery-results.md).

## 4. Stop rules and evidence limits

- Preserve the first failure and exact environment. Stop on a source mismatch,
  dirty source, installation/validation error, frozen artifact mismatch,
  unexpected result, timeout, or top-level nonzero exporter exit. Child exit 73
  is expected only at the declared producer cut; the negative controls above
  are expected only in their named steps.
- Keep every output directory new and outside Core. Do not repair frozen inputs,
  weaken assertions, resample a model, or silently switch source versions to get
  a pass. A checkout-byte mismatch may be a conversion problem; diagnose it
  without normalizing the protected fixtures.
- The Git exporter uses `git ls-files` and working-tree bytes. A source archive
  without Git metadata is not equivalent. Record source commit/tree, any candidate
  patch, versions, command exits, output hashes, and the copied source identity.
- Answer replay happens before its output directory is created. Git export may
  retain `failure.json` after a failed journey. Missing output or a missing
  success record never means success. Review machine-specific paths before
  sharing receipts; do not publish raw output blindly.
- A pass establishes the bounded synthetic replay/integration checks above.
  It does not establish a clean install on another machine, novice comprehension,
  model autonomy, teaching effectiveness, a real longitudinal journey, preview
  acceptance, or a release. `scripts/reference_host.py` exposes classes, not a
  runnable service; snapshot validators do not bootstrap write authority.
