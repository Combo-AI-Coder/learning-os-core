# Thin reference host surface

Status: development reference surface for the synthetic reference journey.

This document describes the smallest public-safe host boundary needed to place a model or other replaceable conversation surface in front of the existing Learning OS Runtime broker. It is an extraction of already-tested target behavior, not a new host architecture.

## Boundary

The trusted host owns:

- the Runtime-Control / Instance locator;
- repository-provider bindings and any credentials;
- the host-owned `RuntimeCapabilityPolicy`;
- the shared deployment-operation admission object;
- the broker-issued opaque session capability and its lifetime.

The external conversation/model surface receives none of those objects. It can submit only one of seven versioned reference-host operations:

- `read_learning_context`;
- `discover_learning_evidence` (surface v3; no arguments);
- `save_learning_checkpoint`;
- `create_evidence`;
- `reconcile_knowledge`;
- `set_intake_preference` (surface v4);
- `reset_intake_preference` (surface v4).

The host does **not** expose generic Instance replacement, raw repository access, successor claim, deployment promotion, Branch authority mutation, repository credentials, private Instance commit identity, or a raw checkout.

`scripts/reference_host.py` is transport-agnostic. A CLI, MCP adapter, subprocess bridge, or model-provider runner may sit outside it, but that adapter does not become learner-state authority. Transport framing, provider-profile isolation, network egress policy and process supervision remain host concerns and must be independently bounded by the environment that chooses a transport.

## Request / result shape

A request is a mapping with exactly `operation` and `arguments`. Unsupported operations or extra fields fail closed.

The successful `read_learning_context` result contains only canonical document paths, document content, and the broker's bounded version token. It does not expose the private Instance head.

Write operations return only `{"applied": <bool>}`. Commit messages are host-generated rather than model-controlled.

`create_evidence` rejects newly materialized records with empty observation content or an invalid/missing observation date-time. A non-blank string observation or a mapping with non-blank `summary` is accepted. Legacy reads and identical retries of already persisted records remain compatible; the check is not applied as a retroactive whole-Instance validator. This admission change is fenced by the dedicated `create_evidence` operation version `v2`.


`save_learning_checkpoint` is deliberately narrower than generic Progress replacement. It derives the canonical Progress path from the host-bound Main Topic/Subtopic and accepts only the local checkpoint fields `milestone`, `return_point`, and `ready_next`, plus the version token obtained from the prior read. The broker preserves unrelated Progress fields, advances the document revision only when the local checkpoint changes, and retains the existing deployment / generation / head / blob fences.

Expected broker failures use stable public codes:

- `resolution_failed`;
- `guard_rejected`;
- `cas_conflict` (marked retryable).

Internal repository paths, remotes, commits and credential material are not reflected in those failure envelopes. Unexpected implementation failures remain host-operator exceptions rather than being converted into false success.

## Synthetic reference-journey coverage

`tests/test_reference_host.py` composes the thin host surface with the existing synthetic broker fixture and exercises:

1. one bounded learning-context read;
2. one Main-bound local learning checkpoint while preserving unrelated Progress state;
3. create-only typed Evidence persistence;
4. Knowledge reconciliation using the version token returned by the read;
5. idempotent Evidence retry;
6. visible CAS failure;
7. rejection of generic/continuity operations;
8. session close / revocation;
9. producer-session revocation followed by two fresh consumer sessions independently recovering the same durable checkpoint and Knowledge state.

The fresh-consumer journey proves mechanical cross-session recoverability only. It does not by itself prove that a model chooses a better teaching action from that state.

This is mechanical host/broker evidence. It does not decide whether an observation qualifies as Evidence, infer learner capability state, establish teaching quality, deploy a new Core, mutate real learner state, or constitute learner acceptance.

## Relationship to private host evidence

Private target-owned acceptance has already established bounded host-owned writable binding, operation-scoped admission, bounded host-mediated model-to-broker calls, and same-surface readback. The public reference surface intentionally retains only the reusable semantics needed for a synthetic journey.

It does not copy private locators, credential wiring, host paths, production process topology or provider-account configuration. A future public reference release may add a reproducible transport/demo around this surface only after the skeleton is stable; that release work is separate from this W5 extraction.

## Bounded decision-evaluation companion

The offline [Core #34 sensitivity/invariance evaluation](evaluations/core34-state-sensitivity-results.md)
composes this host with synthetic fixtures, fresh read-only recovery, blind consumer
packets, acceptable-action sets, strong-summary/ablation controls and explicit
semantic review. It preserves inconclusive/failed runs and does not turn a
mechanical or declared-action pass into teaching-effect acceptance.

## Bounded interruption discovery (surface v3)

`discover_learning_evidence` returns durable Evidence whose explicit context
matches the trusted session's Main Topic/Subtopic, without requiring the consumer
to know producer Evidence IDs or paths. It reuses the broker's immutable snapshot
inventory and authority/deployment guards; no raw inventory is exposed. Whole-root
Evidence read authority is required. Every derived Knowledge owner must also be
read-authorized, even if absent. It is read-only and cannot create/retry Evidence,
reconcile Knowledge or promote learner claims.

The v1 recipe returns all eligible matching records in deterministic path order,
with original content/version tokens, per-target `knowledge_references` and
same-snapshot `knowledge_owners` (present/absent plus version token). The response
never calls anything unprocessed: current Knowledge retains representative refs,
so `not_referenced_by_current_knowledge` is not evidence that a record was never
considered or that replay is safe. Multiple typed targets remain attached to one
source occurrence; a partly referenced record is not duplicated.

The explicit safety envelope is 128 Evidence inventory candidates, 16 matching
records, 8 typed targets per record / 64 total, and 16 distinct Knowledge owners.
Each document may contain at most 64 KiB of UTF-8 content; all scanned Evidence
(including unrelated records) and read Knowledge share 256 KiB. Over-budget,
malformed, unauthorized or stale reads reject the entire operation. There is no
truncation, pagination or global scan exposed to the model. Limits apply after
the repository provider's own bounded snapshot materialization.

Matching records use the existing typed V0.3 shape. Context-free legacy records
are outside this selector and remain readable through existing operations.
Discovery does not reapply new-record observation/timestamp admission to history:
weak/missing old observation values and timezone-less timestamps are preserved,
not repaired or promoted. Path order does not assert wall-clock recency, and no
age cutoff is inferred. This first P2 slice discovers an interrupted durable
observation within a bounded snapshot, not unrestricted recent-history search.

The [prospective cut and limits](evaluations/core34-interruption-recovery-plan.md)
and `tests/test_evidence_discovery.py` own the synthetic verification. Producer
loss is session revocation after save, not an OS/process-crash claim. Correction
propagation, real learner benefit, deployment and product acceptance remain open.


## Scoped intake controls (surface v4)

`set_intake_preference` takes exactly `scope` (`topic` or `global`), `depth`
(`minimal`, `balanced`, `thorough`), and `expected_version_token`.
`reset_intake_preference` takes exactly `scope` and `expected_version_token`.
No path, Topic ID, arbitrary YAML/key, revision or commit message is accepted.
A current-only instruction uses the existing resolver and makes no host write.
The conversation consumer, not a keyword classifier in the broker, interprets
explicit durable scope under `protocol/new-topic-start.md`.

Both operations initially require a bound Main Branch plus existing host-granted
read and write capabilities for the target. They do not grant or broaden either
capability. Topic scope patches only `goal.preferences.intake_depth` in the bound
Topic's existing Goal; it cannot initialize a Goal or rewrite a Plan. Global
scope patches only `preferences.intake_depth` in `learner/execution.yaml`.

Read the owner using `read_learning_context` (global may be an optional path).
An existing owner requires its current version token. An absent global owner
requires an explicit null token; only `minimal` or `thorough` creates a minimal
schema-0.3 Learner Execution with revision 1, timestamp and scoped preference.
Absent-global `balanced` and reset are no-ops with `applied: false`; an existing
owner may explicitly store `balanced`. Reset deletes only the scoped key,
preserving the owner and all other preferences. Repeating a set or resetting a
missing key is also a no-op. Successful changes advance revision and timestamp;
other owner fields preserve semantic values, not YAML comments/formatting.

Create/update/no-op are decided from one fresh pinned authority snapshot inside
the shared deployment write lease. All use current session, role, path policy,
deployment, generation, deployed Core provenance/validator and write-policy
fingerprint checks, including no-ops. Updates use blob plus exact ref-head CAS;
first creation uses create-only plus exact ref-head CAS. A conflict means reread
and reconsider the request; do not overwrite concurrent changes or automatically
reapply stale intent. A missing Topic Goal is rejected.

The dedicated operation registry and Core manifest fingerprint changed together.
A new host against an old deployed write policy fails closed for ordinary writes,
not only these operations. This is a deployment compatibility change, not a
Core promotion or permission to update a real host. See
`tests/test_intake_operations.py` and the separate synthetic experience report.
