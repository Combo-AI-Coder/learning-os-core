# Learning OS — Core

Release: 0.4.0

This repository is the reusable Core plane for the Learning OS V0.4
separation architecture. The current product direction is a learner-owned,
longitudinal learning system for advanced self-directed learning; see
[`docs/product-direction.md`](docs/product-direction.md). Core content never
self-asserts deployment status: the public Runtime-Control contract is the
sole authority that deploys an exact Core commit.

The Core plane owns reusable product semantics only: the Core contract
(`config/core.yaml`, schema 0.4), protocol documents, the deterministic
validator, synthetic tests, and stable reusable domain templates. It contains
no learner state, no Evidence, no Instance or deployment authority, and no
credentials. All test fixtures are synthetic.

Validation (offline, deterministic):

    python scripts/validate_learning_os.py . --core
    python -m unittest discover -s tests -v

The V0.4-B2-B split-aware Instance surface validates a materialized Instance
snapshot against a locally materialized Core snapshot plus an explicit
synthetic trusted deployment binding:

    python scripts/validate_learning_os.py <instance> --instance \
        --core-snapshot <core> --deployment-binding <binding.yaml>

The V0.4-B2-C deployment surface validates an offline split deployment. Each
snapshot is a `RepositorySnapshot`: a materialized tree plus caller-supplied
trusted provenance (`repository_id`, and for the deployed Core the exact
pinned 40-hex `commit_sha`). Identity is never read from repository content;
missing or mismatched trusted metadata fails closed:

    python scripts/validate_learning_os.py . --deployment \
        --control-snapshot <control> \
        --core-snapshot <core> \
        --instance-snapshot <instance> \
        --locator <locator.yaml> \
        --provenance <provenance.yaml>

`validate_deployment` reuses `validate_core()` and `validate_instance()`
instead of duplicating them, enforces the allowlist-only Runtime-Control
deployment contract, and rejects Instance identity, lineage, migration
transaction, or credential content in the public contract. See
`protocol/runtime-bootstrap.md` for the trust model and explicit non-goals
(no repository resolution, no GitHub lookup, no live fetch, no platform
permission checks).

All Instance and deployment test fixtures are synthetic programmatic
temporaries; no real learner state, real Evidence, or credentials are ever
materialized.

Core mutation follows PR-required governance. Deployed Runtime resolves and
reads the exact Core commit pinned by Runtime-Control; advancement of this
repository's `main` branch alone does not change the deployed Core.

## Thin reference host

The development reference host façade in `scripts/reference_host.py` exposes
seven bounded operations: learning-context read, Evidence discovery,
Main-bound learning-checkpoint save, Evidence creation, Knowledge reconciliation,
and scoped intake preference set/reset. These serve the synthetic reference
journeys. Trusted locator,
repository/provider bindings, capability policy and broker session authority
remain host-side. See [`docs/reference-host.md`](docs/reference-host.md) for the
public-safe boundary and synthetic coverage; this is not a deployment or a
model-provider/RPC implementation.

## REST provider safety and compatibility

`GitHubApiProvider` is read-only by default and bounds response/archive expansion.
It uses the same portable path policy as the Git CLI provider. Legacy Contents API
blob-CAS callers must explicitly opt into numeric repository identities with the
keyword-only `writable_repository_ids` constructor argument, using host-trusted
configuration. A token with write scope is not this opt-in.

The REST provider still rejects exact branch-head CAS, even with an allowlist;
it is not a replacement for `GitCliProvider` on broker-authorized write paths.
See `docs/audits/2026-10-02-rest-provider.md` for tests and remaining limits.
