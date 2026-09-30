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

For `GitCliProvider`, the numeric repository ID in the host binding is the trusted security identity. Repository names and remote URLs are routing/navigation metadata and MUST NOT be inferred from repository content or treated as identity. Unknown numeric IDs fail closed, and write capability must be explicitly enabled per binding. The provider isolates ambient Git config and home-based credential/config sources, rejects non-regular entries, empty trees, duplicate/filesystem-equivalent aliases (including Unicode casefold aliases, Win32 uppercase aliases, Windows device names, and DOS short-name forms), and unsafe paths, and verifies that each tracked blob materializes to one distinct regular filesystem entry before validation. Recursive tree listing, entry count, unique subtree count, single-blob size, and aggregate materialized blob bytes are bounded before snapshot construction. All Git transports, including local filesystem paths and `file://` remotes, must honor blob filtering; fetch commands request protocol v2, but acceptance is behavioral rather than diagnostic-text based: after the initial `blob:none` metadata fetch, every selected blob must still be absent under `GIT_NO_LAZY_FETCH=1`. A server that ignores or cannot apply the filter therefore fails closed, and local upload-pack servers need filtering explicitly enabled. Bootstrap uses a two-stage fetch: first `blob:none` retrieves only commit/tree metadata and the provider proves selected blobs remain omitted; after tree validation it derives a conservative per-blob ceiling from `aggregate_blob_budget / selected_blob_count` and refetches the already validated exact commit with `blob:limit=<ceiling+1>`. Pinning hydration to that commit prevents a moving branch/tag from changing the hydrated tree after the ceiling is derived. Writes must also keep replacement content within the same conservative per-blob ceiling so a successful mutation cannot create a snapshot that the bounded Runtime can no longer hydrate. This bounds the expanded blob payload before hydration even when compression is extreme; repositories whose shape cannot be proven within that conservative envelope fail closed. Every fetch is also monitored against a host object-store byte budget, and unsupported bounded-fetch semantics fail closed rather than falling back to unbounded blob/history transfer. Fetch diagnostics are drained concurrently into a bounded buffer, fetches run in an isolated process group, and every abort terminates/reaps the complete Git/SSH helper process tree. On Windows, cleanup snapshots descendant PIDs so helpers are still terminated even if the direct Git leader has already exited before cleanup runs. Other network-facing Git commands such as ref discovery and push use the same bounded stdout/stderr discipline rather than capture_output buffering. Local filesystem remotes remain subject to the same object-store budget in addition to the required blob filter. Together these fences prevent a selected commit from turning bootstrap into unbounded memory, inode, network-history, or disk consumption. HTTP(S) remotes with embedded URL userinfo are rejected so credentials cannot enter Git subprocess argv; explicit Git remote-helper syntax such as `https::...` is unsupported and fails closed because it can hide an inner credential-bearing URL from ordinary URL parsing. Local filesystem remotes and SSH known-host paths are stabilized to absolute host paths at binding construction; SSH agent sockets must already be absolute host paths. SSH agent sockets and host-key pins are scoped to the specific host-trusted repository binding that declares them; a credential on the writable Instance binding is not injected into Core or Runtime-Control Git operations. SSH remotes require an explicit host-key pin to authenticate: both user and global known-host fallback are always disabled, and an unpinned SSH binding fails closed under strict host-key checking. Pin paths containing whitespace, quotes, backslashes, or OpenSSH `%`/`$` expansion tokens are rejected so OpenSSH cannot reinterpret the trusted filename. Transient Git repositories are initialized from a provider-owned empty template so ambient/default hooks cannot enter the Runtime write path. Text reads inspect the blob size before buffering and reject blobs above the Runtime read limit; full-tree materialization streams blob bytes directly to snapshot files. Partial-clone object-size probes set `GIT_NO_LAZY_FETCH=1`, so omitted/promised blobs cannot be demand-fetched merely to discover their size. Commit/tag/tree metadata objects are expanded-size checked before Git is allowed to parse their contents, and tree traversal proceeds one capped tree at a time rather than recursively inflating unbounded metadata. Empty subtrees fail on first traversal; component bytes, path depth, full-path bytes, and cumulative expanded-path bytes are all bounded before paths are retained. The forbidden `.git` sentinel uses both Unicode casefold and Win32-uppercase equivalence, matching ordinary snapshot alias rules. Runtime-Control bootstrap reads the deployment contract from the exact control commit that was materialized and validated, never from a moving control ref. SSH known-host pins must already be absolute host paths. They are normalized to forward-slash form and then revalidated against whitespace, quoting, backslash, and OpenSSH expansion-token restrictions, so accepted Windows drive/UNC paths remain literal filenames and working-directory content cannot alter trust-store interpretation. Writes may additionally require an exact expected Instance branch head. Git CLI combines that precondition with the exact `--force-with-lease=<ref>:<fetched-head>` push. GitHub REST does not expose an atomic expected-old-head precondition for ref updates, so `GitHubApiProvider` fails closed when exact branch-head CAS is requested; its legacy contents-API write remains available only when no exact head precondition is required. Branch-head CAS and target-blob CAS are independent fences.

Provider choice does not change the Deployment Guard, learning-generation guard, or target-CAS semantics above. A credential or transport that can reach a repository is capability evidence, not semantic authority to mutate that repository.
