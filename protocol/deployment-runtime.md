# Deployment Runtime

Runtime-Control is the only live deployment authority. The external trusted
locator supplies the numeric Runtime-Control and Instance repository IDs.
Repository names are navigation metadata and never security identity.

## Bootstrap

The host resolver must:

1. load the trusted locator from a host-controlled surface;
2. resolve Runtime-Control by numeric repository ID and materialize its
   canonical ref;
3. read the allowlist-only deployment contract;
4. resolve and materialize the Core by numeric repository ID at the exact
   40-hex commit pinned by that contract;
5. resolve and materialize Instance by numeric repository ID;
6. call the deterministic offline deployment validator with trusted
   provenance;
7. create a session context containing deployment ID, epoch, Core repository
   ID/commit, and Instance repository ID.

Any lookup, materialization, provenance, parsing, or validation failure is a
fail-closed bootstrap failure. A cached Core is usable only when its repository
ID and exact commit are independently verified.

## Mutation fencing

Before every canonical Instance mutation, the Deployment Guard fresh-reads
Runtime-Control and requires:

    write_state == active
    current deployment id == session deployment id
    current epoch == session epoch
    current Core repository ID/commit == session Core pin

The guard runs before Instance generation validation and before target blob
compare-and-swap. These three fences are intentionally independent:

- deployment epoch rejects writers from an older deployment;
- generation rejects stale semantic/lineage writers;
- target blob CAS rejects concurrent writes to the same artifact.

Runtime-Control outage or malformed state blocks canonical writes. Core
`main` moving without a Runtime-Control promotion does not change a running
deployment. Promotion freezes writes, validates the new exact Core, increments
the epoch while changing the pin, and only then returns to active.

The reference host implementation lives in `scripts/runtime_adapter.py`.


## Reference repository providers

The reference host exposes the same `RepositoryProvider` contract through two transport realizations:

- `GitHubApiProvider` uses GitHub REST with an optional bearer token;

- `GitCliProvider` uses a host-trusted mapping from stable numeric repository IDs to Git remotes, so a narrow host may use public HTTPS reads plus an Instance-scoped SSH/deploy-key write capability without granting that credential to Core or Runtime-Control.

For `GitCliProvider`, the host-trusted numeric repository ID is the security identity; repository names and remote URLs are routing metadata and MUST NOT be inferred from repository content. Unknown numeric IDs fail closed and writes require explicit per-binding opt-in.

Required provider guarantees:
- Isolate ambient Git/SSH configuration and reject non-regular entries, empty trees, path traversal, and filesystem/Unicode/Win32-alias collisions before validation.
- Use two-stage bounded fetch (`blob:none` metadata check verified under `GIT_NO_LAZY_FETCH=1`, followed by commit-pinned `blob:limit=<ceiling+1>`) and enforce per-blob, total-snapshot, tree-entry, metadata-size, and object-store byte budgets, failing closed when filtering is unsupported or diagnostics/buffers overflow.
- Drain subprocess output concurrently in isolated process groups, terminating/reaping helper trees (including Windows descendant processes) on timeout or abort.
- Require strict host-key pinning (no user/global fallback), reject embedded HTTP URL credentials or remote-helper syntax (`https::...`), and bind credentials/SSH sockets strictly per repository ID.
- Enforce optional expected Instance branch-head CAS via `--force-with-lease=<ref>:<fetched-head>` alongside target-blob CAS; `GitHubApiProvider` MUST fail closed whenever exact branch-head CAS is required.

Provider choice does not weaken Deployment Guard, learning-generation, or target-CAS checks; transport reachability is never write authorization.

## Ordinary learning-session binding

A conversation/model surface is a replaceable execution surface, not learner-state authority. The host MUST keep trusted locator data, repository credentials, and repository transport objects outside that surface.

The reference session broker binds a conversation session to one validated deployment, one Instance canonical ref, one Branch identity/lineage, one active generation, the Branch Registry's current role/Subtopic binding, and a host-owned Instance path capability policy. The Branch must be active in the exact authority-head Branch Registry when the session opens; later role/Subtopic/lifecycle drift fails the session closed on fresh authority checks. Deployment/binding/policy state remains broker-side; the conversation surface receives only an opaque randomly issued session handle, and every public broker operation resolves that handle back to the exact broker-issued state before applying capability checks. Copying a handle preserves the same capability but cannot widen it; unknown or foreign-broker handles fail closed. Capability lifetime is broker-side and explicit: dropping one local handle object does not revoke copied or serialized representations, while the broker's explicit close operation marks the session revoked, rejects new operations, drains operations that already acquired the capability, and only then returns; after close returns no representation of that session ID can complete another broker operation. The trusted locator is fixed by the host when the broker is constructed; a conversation surface MUST NOT supply or redirect Runtime-Control or Instance trust roots. A writable session additionally requires a generation that was already established through the continuity protocol; merely reading the current active generation can open a read-only session but does not grant writer authority. Generic replacement of subtopic_progress follows the canonical role rule: only the Main Branch bound to that exact Subtopic may move its route position; Practice/Deep Dive remain evidence/role-local-execution surfaces and cannot directly advance Main's canonical current position. A Branch may generically replace a branch_report only when the report path names that session's exact bound Topic and Branch; no Branch role may spoof another Branch's report.

A pending learning handoff is not an ordinary active-session state. Normal session open continues to fail closed while `pending_successor` is present. For a learner-authorized handoff, the host may instead use the broker's dedicated successor-claim transition. That transition reads the exact pending Branch authority and referenced canonical learning handoff, requires the requested generation to match the pending successor, validates the candidate claimed Branch runtime with the exact deployed Core, and applies the Branch-runtime mutation with both target-blob and exact Instance branch-head CAS. Only after canonical readback shows the successor active does the broker issue the opaque session handle; successful claim is what establishes that returned session's generation identity. The conversation surface cannot invoke this transition as a generic Instance write, cannot choose an arbitrary generation, and cannot turn a mere read of `active_generation` into writer authority. The host MUST invoke successor claim only for a successor work surface covered by the learner authorization that created the pending handoff.

Before any session read or canonical write, the broker runs inside a shared deployment-operation lease (`DeploymentWriteGate` for same-process hosts; promotion barriers drain all readers/writers before modifying Runtime-Control):

- **Fresh authority & bounded parsing**: Pin Runtime-Control and the Instance canonical ref (`head`), validate Runtime-Control against the complete schema/allowlist, and validate Branch runtime, Branch Registry, and `handoff_ref`s from one immutable Instance snapshot. Bounded YAML parsing rejects anchors/aliases and duplicate keys. Invalid `write_state`, deployment/generation/role drift, pending successors, multiple active generations, forbidden keys, or malformed authority fail closed; `frozen` allows reads only and rejects all writes.
- **Read fencing**: Fetch requested paths from the pinned immutable Instance snapshot, then re-verify both Runtime-Control and Instance moving refs unchanged before returning (discarding on drift).
- **Write fencing & Core-validated candidates**: Before any canonical CAS:
  1. Verify compatibility between host write-policy fingerprint and the deployed Core manifest's `runtime_session_write_policy_fingerprint` (caching verified results strictly under `(core_repo_id, core_commit)`, never under TTL/moving ref).
  2. Validate model-supplied YAML bounds/paths (rejecting backslashes, Win32 drive/ADS/device names, trailing dot/space, and root escapes) and execute the pinned Core snapshot's `validate_learning_os` on the candidate snapshot in an isolated timed-out subprocess with discarded stdout/stderr.
  3. Attempt release of all temporary materializations on every exit path and abort before canonical mutation if any pre-write cleanup fails. Providers must protect retain/release/close under synchronization.
  4. Mutate through a provider enforcing **both** target-blob CAS and exact Instance branch-head CAS (`GitHubApiProvider` fails closed), while prohibiting generic writes to authority (`branch_runtime`, `branch_registry`, `hub_runtime`) or Hub-owned route/sequence/curriculum families. Every revisioned family MUST strictly increment `revision` (or `extension_revision`).

Generic update also resolves paths through the canonical split storage registry, binds semantic IDs (`topic`, `branch`, `domain`, `lineage_id`) to their canonical path segments, and blocks overwriting `IMMUTABLE_APPEND` families.

The conversation surface receives only the content and bounded version tokens required by its exposed operations. Freshness/assertion operations return no private Instance commit identity; exact authority heads remain broker-internal fencing state. Successful writes return an opaque acknowledgement rather than a private Instance commit identity. The surface does not receive Git credentials, SSH-agent access, repository remotes, private Instance commit identity, or a raw Instance checkout. A future MCP/CLI/chat adapter is only a transport adapter over this broker boundary and does not become learner-state authority.

### Bounded learning-context recovery

The broker exposes a bounded multi-document learning-context read for a host that already knows which canonical documents are relevant to the current learning task. Required and optional paths are capability-checked before repository I/O, duplicates are rejected, and the total path count is bounded. The broker pins one exact Instance authority snapshot, validates Branch authority from that snapshot, reads every returned document from that same snapshot, and rechecks both Instance and Runtime-Control moving refs before returning. Optional sparse state may be reported missing without fabricating an empty canonical artifact. The result contains per-document content/version tokens but does not expose the private Instance head. This operation is a consistency/latency primitive, not a general private-checkout export or a replacement for relevance filtering.

### Evidence create-only operation

Evidence remains an immutable/create-only family and is still forbidden to generic replacement. The broker therefore exposes a dedicated Evidence creation operation. `create_evidence` is versioned in the Runtime-session write-policy fingerprint, so a host binary cannot activate it against an older deployed Core that does not define that operation. The Evidence path is derived from the candidate's own `id`, must classify as the canonical Evidence family, and must be inside the host-issued write capability. The operation uses the same deployment lease, exact Instance authority snapshot, deployed-Core write-policy compatibility check, candidate Instance validation, final deployment/generation freshness checks, and exact Instance branch-head CAS used by ordinary writes. A matching existing Evidence ID with semantically identical content is an idempotent no-op; the same ID with different content is rejected and never overwritten. The reference Git CLI provider implements exact-head create-only mutation with a one-path commit and force-with-lease. GitHub REST cannot prove the broker-required expected-old branch head and therefore fails closed when that precondition is requested.

`create_evidence` classifies observations and persists immutable Evidence records under host write capabilities and CAS; semantic Knowledge integration remains governed by `evidence-integration.md`.


### Knowledge reconciliation operation

Learner Knowledge (`reconcile_knowledge:v2`) uses a dedicated versioned Runtime operation rather than generic replacement. It supports revision-1 creation or exact-token updates under all standard fences, checking up to 32 newly referenced Evidence entries (readable under session policy, matching target domain/concept/capability and interpretation direction) before per-Evidence I/O. Semantic capability judgment, confidence, and alternative-explanation checks remain governed by `evidence-integration.md`.

## Synthetic trusted first-use owning transition

The reference `scripts/first_use.py` implements a separate, synthetic-only Host
bootstrap operation. It is not part of the ordinary seven-operation facade.
Existing repository supply, an existing canonical branch, and an active, exact
Core deployment are prerequisites. A real authentication/grant/receipt provider,
production permission creation, rollout and real learner initialization are not
implemented by this reference.

The trusted Host fixes numeric repository identities, locator, canonical ref,
receiving principal/surface and the permitted teaching template. Typed proposal
content cannot choose roots, generation, Core pin, YAML or arbitrary paths. The
synthetic grant covers its exact normalized intent and target. Reading a runtime
generation, an operation ID, repository bytes or a receipt is not authorization.

### Publication, recovery and activation

The installation's shared private store reserves one target slot for the exact
operation and binding before preparation. Its operation lock serializes entries
across store objects. A competing operation fails before prepare/publish; an
orphan lock is not stolen. The store must be common to all Hosts controlling the
same target. A separate store is not an independent right to claim that target.

The fixed manifest creates nine existing canonical documents in this order:
Instance config; Topic Goal, Plan, Progress; current Subtopic Plan, Progress,
Definition; branch registry; Main runtime. Subtopic Definition remains last for
its unit. The registry remains `idle`, so even read-only ordinary sessions cannot
open. Runtime generation 1 may be visible without constituting a usable writer.
Candidate validation covers the full graph before any publication.

Each single-file step uses the optional prepared provider surface. Before push,
its private store durably retains exact commit bytes, the single expected parent,
Git trees/blobs, path/blob/mode identities and operation/step/intent/manifest
metadata. The integrity-protected receipt then durably records that descriptor.
Only after both steps does the provider push the exact candidate SHA using an
exact-head lease. The candidate is a normal single-parent successor, not a
history rewrite. No GET-then-PUT or blob-only fallback is permitted. The REST
provider remains unsupported.

Git tree-object evidence proves modes and unchanged paths. Filesystem stat,
chmod success and equal text do not prove this. Unrelated mode changes or
same-byte commits with different identities fail closed. Fixed candidate bytes
survive adapter restart; retry never regenerates a commit with a fresh timestamp.

After each publish, only `head == prepared candidate` with verified objects is
`applied`. `head == expected parent` is `not_applied` only before any publisher
started or after independent proof it has ended. A timeout or killed local
transport does not prove remote drain. Other heads, including an unfamiliar
successor or same-byte rival commit, are conflicts. Missing/corrupt artifacts,
unavailable reads or an undrained publisher retain `outcome_unknown` and stop
later mutation. Receipt failure cannot authorize publishing.

After all nine documents are verified and full deployment validation succeeds,
one prepared exact-head/blob CAS changes only the registry to revision 2 and
Main lifecycle `active`. This is activation's linearization point. Full readback,
current authorization and exact deployed write-policy compatibility precede
internal generation-1 capability issuance. No generation is accepted from the
receiver. The Host scopes this reference writer to the bound Subtopic checkpoint.

### Admission, revocation, delivery and exit

Each step and issuance holds the shared deployment admission and current grant
lease. Revocation drains admitted synthetic operations and closes issued handles;
no later step/issuance begins after its linearization. Promotion must use the
same admission/drain mechanism. Instance CAS cannot provide cross-repository
atomicity or protect against external administrators ignoring that mechanism.
The included gates model one process; production cross-process gates require
separate implementation and validation.

Unacknowledged activation is inspected against its prepared identity, never
repeated. Redelivery is confined to the original authenticated binding and the
unchanged activation window. A live cached handle is checked and reused. An
unknown prior Host/handle is `delivery_pending`, not permission to issue a second
writer. A closed/delivered handle is not replaced. Fresh writable continuity must
use the existing learner-authorized successor handoff; fresh read-only recovery
does not establish it. After normal learning advances, a completed initialization
returns `already_applied` without restoring old progress or issuing a new writer.

Transient materializations are released on every path, with all cleanup attempts
made. Cleanup failure before activation blocks it; failure after a mutation does
not prove that mutation failed. Prepared artifacts remain available for unknown
results. The reference retains at most the fixed operation's ten bounded prepared
packages plus its receipt/tombstone, including after delivery, rather than
silently discarding recovery evidence. Production retention/key management is a
separate adapter concern. No remote cleanup, reset, force rollback or deletion is
provided. Inactive partial state remains visible to existing repository readers;
this is admission isolation, not confidentiality isolation.

The reference filesystem uses bounded HMAC-protected receipts and atomic replacement with `fsync` (cross-host crash/network guarantees depend on the production host implementation).
