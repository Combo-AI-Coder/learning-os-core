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

For `GitCliProvider`, the numeric repository ID in the host binding is the trusted security identity. Repository names and remote URLs are routing/navigation metadata and MUST NOT be inferred from repository content or treated as identity. Unknown numeric IDs fail closed, and write capability must be explicitly enabled per binding. The provider isolates ambient Git config and home-based credential/config sources, rejects non-regular entries, empty trees, duplicate/filesystem-equivalent aliases (including Unicode casefold aliases, Win32 uppercase aliases, Windows device names, and DOS short-name forms), and unsafe paths, and verifies that each tracked blob materializes to one distinct regular filesystem entry before validation. Recursive tree listing, entry count, single-blob size, and aggregate materialized blob bytes are bounded before snapshot construction. Remote smart transports must support depth-limited, blob-filtered fetches, and every fetch is monitored against a host object-store byte budget; unsupported bounded-fetch semantics fail closed rather than falling back to unbounded history transfer. Local filesystem remotes are still subject to the same object-store budget. Together these fences prevent a selected commit from turning bootstrap into unbounded memory, inode, network-history, or disk consumption. Local filesystem remotes and SSH known-host paths are stabilized to absolute host paths at binding construction; SSH agent sockets must already be absolute host paths. SSH agent sockets and host-key pins are scoped to the specific host-trusted repository binding that declares them; a credential on the writable Instance binding is not injected into Core or Runtime-Control Git operations. SSH remotes require an explicit host-key pin to authenticate: both user and global known-host fallback are always disabled, and an unpinned SSH binding fails closed under strict host-key checking. Pin paths containing whitespace, quotes, backslashes, or OpenSSH `%`/`$` expansion tokens are rejected so OpenSSH cannot reinterpret the trusted filename. Transient Git repositories are initialized from a provider-owned empty template so ambient/default hooks cannot enter the Runtime write path. Text reads inspect the blob size before buffering and reject blobs above the Runtime read limit; full-tree materialization streams blob bytes directly to snapshot files. Writes may additionally require an exact expected Instance branch head. Git CLI combines that precondition with the exact `--force-with-lease=<ref>:<fetched-head>` push. GitHub REST does not expose an atomic expected-old-head precondition for ref updates, so `GitHubApiProvider` fails closed when exact branch-head CAS is requested; its legacy contents-API write remains available only when no exact head precondition is required. Branch-head CAS and target-blob CAS are independent fences.

Provider choice does not change the Deployment Guard, learning-generation guard, or target-CAS semantics above. A credential or transport that can reach a repository is capability evidence, not semantic authority to mutate that repository.
