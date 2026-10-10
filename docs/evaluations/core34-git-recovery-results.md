# Core #34: local-Git producer-loss recovery

Owner: [Core #34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34).
Status: bounded synthetic integration evidence; the wider product issue remains
partial/open. No production implementation was changed for this slice.

## What this adds

The existing journey and discovery checks use `BrokerProvider` with synthetic
head/blob tokens. The real `GitCliProvider` already has individual read, create,
update, stale-head and push-race tests. Those are complementary evidence, but
neither alone verifies the full product-operation composition after the producer
process is gone.

This check composes the unchanged `ReferenceLearningHost`,
`RuntimeSessionBroker` and actual `GitCliProvider` over three temporary local Git
repositories. A producer commits Evidence, then exits abruptly. Fresh Python
processes discover and reconcile it from Git. The controller also checks rejected
mutation and exact-head concurrency paths. This advances #34's existing
partial-persistence/recovery requirement without another model trial or a new
runtime mechanism.

## Source and trust boundary

The original runtime baseline was canonical Core
`83f08b016dd51a777ee91c0c4d77c0b85e046e4e`. All 267 files were checked against
native Git blob identities before use. The ordered file/blob manifest hashes to
`67b40d564a26fc0b2792ec01e2718a9b962fb14bde2b5981707657d585d96be0`
using the helper's documented JSON encoding.

Runs 1–3 used exactly that 267-file input. Run 4 copied a 271-file working
candidate, including the two added controllers and earlier versions of this
report and the retained receipt. Its manifest hash is
`3f9b937542b381af39bd470c1aa48259f569b978720385f6a1ec2abf2e91f382`.
All ordinary runtime implementation bytes remain those of the canonical
baseline. The manifests preserve these different input sets rather than
calling the 271-file candidate canonical `83f08b016`.

The added test controller is separate from that baseline. The final observed
helper SHA-256 is
`0453ada4454388f37428fa66e210338e681796a5ee139bc47e1d0972329cdd40`;
the unittest wrapper is
`79db4de288b5604436b872c6eb840a2347bcb7eb23a703d0fa052a3229061ee3`.
The retained receipt includes their Git blob identities as well. The final PR
file set adds these controllers, this report and the receipt; it must not be
called the unchanged 267-file baseline.

Each run commits the copied Core bytes into a new local test repository, then
pins that actual test commit in the synthetic Runtime-Control contract. These
test commits and the reconstructed development checkout's baseline are not the
canonical GitHub commit or deployed production identities. Staging readback
checks every copied Core blob against the source manifest.

All repository IDs, Instance records, Topic/Main binding and generation 3 are
fixture-established. Generation acquisition, successor takeover and a clean
first installation are not exercised. Core and Runtime-Control bindings are
read-only; the Instance binding is also read-only in reader processes. The
writer session policies expose only the required Evidence or Knowledge path.
Each process has its own `DeploymentWriteGate`, so this is not a test of shared
cross-process admission or deployment draining.

Only three fixed absolute local Git bindings are used. Fixture Git commands use
empty init templates, disabled hooks/signing, isolated configuration and file-only
transport. The production provider constructs its own environment; the fixture's
`GIT_ALLOW_PROTOCOL` setting is not claimed to enforce a provider-wide transport
policy. No network endpoint, account credential, installed user Host or real
learner/Instance data participates.

## Declared interruption and observations

The producer calls `os._exit(73)` only after `create_evidence` has returned an
applied acknowledgement. It bypasses Python cleanup and never reconciles
Knowledge. No Git write is deliberately interrupted in flight. This is a
post-commit producer-loss cut, not a mid-write crash, power-loss or arbitrary
termination guarantee. Child temporary directories are contained in the
disposable test root.

The final run used Python 3.12.14 and Git 2.52.0. Its eight sequential processes
produced these observations:

| Step | Observed product result | Reachable Instance commits |
|---|---|---|
| Producer | Evidence applied, process exit 73 | 1 → 2 |
| Fresh reader | One Evidence, not referenced by current Knowledge | 2 → 2 |
| Fresh writer | Knowledge reconciliation applied | 2 → 3 |
| Fresh reader | Same Evidence is now referenced | 3 → 3 |
| Same Evidence retry | Applied false | 3 → 3 |
| Same ID, changed observation | Guard rejection | 3 → 3 |
| Wrong Knowledge blob token | CAS conflict | 3 → 3 |
| Competing README commit before provider checkout | CAS conflict; competitor retained | 3 → 4 |

Fresh processes receive the fixture root and operation mode, not a producer
response, serialized provider state or Evidence ID argument. The reconciliation
controller uses the discovered record and the blob token it actually reads.
Workers read `source-identity.json` only to check its synthetic fixture-kind
marker; its source/commit fields are not used to discover or interpret Evidence.
It remains a scripted deterministic controller with access to the test module,
not a blinded or autonomous model consumer.

The race advances the branch while retaining the Knowledge target blob. It
specifically checks rejection of an already-advanced head at provider checkout:
`expected_ref_sha` is compared after `_checkout_branch` returns. It
does not add evidence for the later checkout-to-push force-with-lease race;
existing provider tests own that separate coverage.

## Independent Git oracle

The oracle reads the bare repositories through separate Git CLI calls, rather
than accepting the facade's success/error envelope as proof. It checks:

- exact Evidence bytes and a single Evidence path;
- unchanged Knowledge after producer loss and exact expected bytes after
  reconciliation;
- the reader's actual Knowledge version token equal to the independently
  observed Git blob identity used for reconciliation;
- the exact parent, one-path `diff-tree`, reachable commit count and ref delta
  for each admitted Evidence, Knowledge and competing README commit;
- unchanged HEAD, tree and refs after each read, identical retry,
  conflicting-Evidence rejection and stale-blob rejection;
- unchanged Evidence and Knowledge after the race;
- unchanged Core/Runtime-Control refs and current reachable trees between
  the observed initial/final readbacks.

This is an alternate readback path within one test harness. Same-model method
review is internal quality amplification, not independent-provider evidence.
The test does not inspect every unreachable object, reflog or filesystem byte.

## Retained preparation and result evidence

[`observed-runs.json`](../../tests/fixtures/core34-git-recovery/observed-runs.json)
retains all four explicit execution runs, original process stdout/stderr,
before/after Git observations and source identities. Runs 1 and 2 passed their
earlier narrower six-process checks; they are preparation evidence, not extra
independent successes for the final method. Run 1 lacked runner-file hashes.
Method review then added empty Git templates/hooks/signing isolation, exact
commit-parent/diff/all-ref checks, reader binding minimization and two negative
controls. Run 3 passed that eight-process method; run 4 added the explicit
Git-blob/token comparison and passed with the separately identified candidate
input. No production guard or expected product result was weakened to obtain
a pass. No unexpected product-operation failure occurred in these runs.

The preliminary focused test passed; the pre-method-review aggregate completed
810 tests with one Windows-only skip. That historical aggregate is not borrowed
as validation of the later candidate. The PR receipt owns exact final candidate
identity, final focused/Core/full-suite results and hosted review/CI boundaries.
Git commit IDs, timing and source manifests change on a later reproduction;
the semantic assertions, not byte-identical commit IDs, are the replay contract.

## Reproduce

Use the README's Python 3.12/dependency setup and Git on PATH, from a Git checkout:

    python -m unittest tests.test_git_recovery -v
    python -m tests.git_recovery_fixture --output /path/to/new/external-directory
    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The exporter refuses an existing or in-Core output directory. Run without `-O`;
disabled assertions are rejected. It copies only Git-tracked source bytes and
records actual source and runner identities. Reproductions from the final PR
include its added tracked files, unlike the original 267-file input. The retained
output contains synthetic local repositories and operation receipts; it is not
a production setup or migration command.

## Limits and next trigger

This establishes a bounded actual-local-Git composition witness. It does not
establish teaching benefit, model decision quality, large-history selection,
statistical reliability, real GitHub/network durability, platform/Windows
coverage beyond executed checks, mid-write crash safety, shared-process
admission, deployment readiness or learner acceptance. The historical model
trials and #34's stopping conditions remain unchanged.

No new product defect was observed. Stop this slice after the required
validation/review. Reopen it for a concrete provider/operation change or a
matching interruption/CAS failure, rather than generating more same-condition
journeys. Real Host integration, production promotion and real learning remain
with their existing owners and separate authority.
