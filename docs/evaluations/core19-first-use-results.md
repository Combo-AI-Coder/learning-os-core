# Trusted blank first-use: synthetic acceptance evidence

## Scope and outcome

This slice supplies a trusted first Topic/Main transition and a meaningful
prediction/context unit from a blank **local synthetic** Instance. Nine existing
schema documents are staged inactive, then one exact registry CAS activates
Main. It is a separate host bootstrap path; the ordinary Host still has seven
operations. A supplied generation number is not first-use authorization.

The implementation is deliberately a synthetic adapter: local bare Git,
in-process trusted grants/deployment admission, private integrity-protected
receipts and prepared object storage. It does not provide production identity,
persistent permission creation, cross-process revocation, deployment or merge.

## Reproducible structural acceptance

Run `python scripts/validate_learning_os.py . --core` and
`python -m unittest discover -s tests -v`. `tests/test_first_use.py` adds 52 tests
with real local Git transports, including:

- N: a zero-file empty-root commit and an optional-README baseline, complete
  nine-document validation, every inactive
  prefix denied by ordinary open, exactly ten single-parent/single-path accepted
  commits, unique generation 1, and an actually saved checkpoint. Exact Git diff
  and complete semantic comparison show only authorized checkpoint fields move;
  no Goal/Plan/registry/runtime reset occurs on prepare/inspect/apply retries.
- L1: all ten publication points lose the response **after** Git has accepted the
  exact candidate and **before** the publisher completion marker. The restarted
  adapter confirms that candidate and continues without duplicate commits.
- L2: an unpublished durable candidate is restored and published unchanged.
- L3: different operations race for one shared target slot; same-operation entry
  is serialized. Another operation's same-byte tree/different commit conflicts.
- L4: external and preparation-bug mode-only changes fail from Git tree evidence,
  including with chmod failure; local filesystem stat is not authority.
- L5: missing/corrupt receipt or objects, unreadable results, unfamiliar successor
  and undrained publisher stop further mutation. Failed durability cannot start
  publication; retained objects are not recreated as a fresh candidate.
- R: forged/revoked/changed binding, deployment freeze/epoch/Core/authority-head
  drift, old policy fingerprint, invalid graph/input, unsupported provider,
  cleanup failure, promotion barrier and unknown writer delivery fail closed.
  Revocation drains an admitted synthetic step and prevents later steps.

Linux local command outcomes and exact candidate identities are recorded in the
owning issue/PR validation receipt. Hosted Linux and Windows outcomes must be
reported separately on that same head; local mode-injection tests are not a
claim of an actual Windows run. Tests cover adapter restart and controlled local
transport failures, not arbitrary machine crashes, power loss or Internet
availability. An orphan operation lock remains blocked until separately proven
drained; no unsafe lock-stealing recovery is provided.

The initial Code review identified that the README-only fixture missed the
zero-file root-tree case. The repair permits an empty root only on dedicated
first-use paths, retains ordinary/nested-empty rejection, and tests both normal
activation and first-acceptance lost-response recovery from that baseline.

The first hosted Windows run exhausted its existing 15-minute job budget; it
did not report an assertion failure before cancellation. Profiling exposed
repeated Git subprocess work. The synthetic provider now reuses the two exact
validated object walks only within one verification/preparation invocation.
There is no cross-call cache: every restore and checkout still reads Git objects,
and remote refs, authority, receipts and publication outcomes remain fresh.
A narrow regression checks both once-per-commit traversal and fresh revalidation
on the next restore. Timings and hosted outcomes are reported separately in the
PR receipt; this change alone does not establish Windows completion or an
interactive performance guarantee.

The subsequent review hardened admission before target-slot reservation. The
Instance ref must identify a writable branch. A conservative transport envelope
limits the baseline commit/root/README objects to 1 MiB, checks all nine documents
and the actual activation document at 64 KiB each, and bounds tree, metadata,
base64/JSON and future receipt overhead without raising the 4 MiB artifact limit.
Oversized README and parent-message cases, exact payload and UTF-8 boundaries,
non-branch refs and corrected-input retries have explicit regressions.

Candidate validation now executes the exact deployed Core CLI's full deployment
surface, with actual snapshot provenance and helper files outside the candidate.
It shares the ordinary broker's bounded runner: ten-second timeout, discarded
output and fail-closed start/nonzero/timeout behavior. A stricter deployed rule
with the same policy fingerprint is tested explicitly, including an active-only
rejection. Both inactive and active candidates are checked before reservation;
the actual active overlay is checked again before the final CAS. Fingerprint equality alone
is not evidence of validator implementation equivalence.

Hosted Windows execution exposed three fixture-specific failures, retained in
the PR's terminal logs. The nested-empty test now creates and checks binary
NUL-delimited Git tree records, so CRLF cannot accidentally test an unsafe name.
The mode test injects failure into an actual exported file's Path.chmod, asserts
that it was exercised, and leaves tempfile's cleanup permissions untouched.
The same-operation race holds the operation lock after one real accepted step,
then requires the contender's local admission to return operation_busy before
release. It does not rely on a second ten-step chain finishing inside a join
window. These repairs preserve the original cases and strengthen their evidence;
actual Windows completion remains a separate hosted result.

## One independent-context A/B behavior sample

The prospective rubric and permissible actions were frozen before dispatch.
Raw packets, verbatim responses, scripted learner text, source freeze and hashes
are retained at `tests/fixtures/core19-first-use/`. The sample uses two isolated
native consumer contexts from the same model family. It is one synthetic pair,
not a real learner trial or independent-provider replication.

A correctly explained conditional next-token prediction, gave the tea/cup
example, distinguished tokens from necessarily whole words, and contrasted
plausible fictional Lyon with real-world Paris. It proposed a small follow-up
without assuming mastery. The fixed synthetic learner then requested a river-
bank versus money-bank context comparison. Only that interaction position and
next action were saved; no Knowledge or Evidence was invented.

After A's Host closed, a new-process read-only Host recovered Goal, Plans and
Progress and performed bound-unit Evidence discovery. A real attempted write was
rejected without changing the canonical head. B received this recovered packet,
not A's transcript or grant/session capability. It selected the allowed bank
comparison, cited the saved return point/current focus/ready-next, avoided a new
mandatory intake, and explicitly distinguished planned/empty Evidence from
mastery or difficulty. A separate native reviewer graded A 5/5 and B all frozen
conditions PASS. This supports the narrow claim that this checkpoint enabled this
next-action decision; it does not establish learning gains or general reliability.

Two evidence qualifications are retained rather than corrected after the fact:

1. The packet includes **fresh reader** `version_token` blob SHAs. The plan's
   “no token” phrase was too broad. No producer session/capability, Evidence ID or
   receipt was transferred, but the packet was not literally token-free.
2. Two original controller summary fields (ten transitions and no Evidence
   created) were constants. Transition count is established by the tests' actual
   Git history/descriptor checks, and empty Evidence by B's actual discovery.
   They are not separate measurements from those controller constants.

The frozen source precedes two small review corrections: remove an undefined
Topic Progress pointer and return `already_applied` from completed prepare.
A further concurrency fix serializes repeated Host close and retains failed grant
revocation drain responsibility for retry; a real checkpoint publication race
covers this boundary. Final-code regression replays the checkpoint and compares recovered semantics
with the immutable B packet; replay is not counted as another model sample.
Fresh consumer recovery remains read-only. A replacement writer requires the
existing separately authorized successor continuity procedure.
