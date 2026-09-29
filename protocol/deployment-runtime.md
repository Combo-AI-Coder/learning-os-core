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

For `GitCliProvider`, the numeric repository ID in the host binding is the trusted security identity. Repository names and remote URLs are routing/navigation metadata and MUST NOT be inferred from repository content or treated as identity. Unknown numeric IDs fail closed, and write capability must be explicitly enabled per binding. The provider isolates ambient Git config, rejects non-regular tree entries and unsafe paths, checks the expected target blob before mutation, and uses an ordinary non-force branch push so a concurrent branch advance rejects the write rather than being overwritten.

Provider choice does not change the Deployment Guard, learning-generation guard, or target-CAS semantics above. A credential or transport that can reach a repository is capability evidence, not semantic authority to mutate that repository.
