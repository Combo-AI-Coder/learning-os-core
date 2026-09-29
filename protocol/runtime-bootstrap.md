---
protocol: runtime-bootstrap
version: "0.4"
schema_compatibility: "0.4"
---

# Learning OS Runtime Bootstrap (V0.4 Split Topology)

This protocol defines how a runtime (or any caller) deterministically validates
a V0.4 split deployment offline before trusting it. It defines the validation
input model only; it does not create deployment authority and does not define
any live resolver, fetch, or permission surface.

## 1. Validation surface

    validate_deployment(control_snapshot, deployed_core, instance_snapshot, trusted_locator)

The surface composes, and MUST NOT duplicate, the two sub-surfaces:

- `validate_core(deployed_core)` — Core plane contract.
- `validate_instance(instance_snapshot, deployed_core, deployment_binding)` —
  Instance plane contract, driven by a binding projected from the validated
  contract (see §4).

## 2. RepositorySnapshot — trusted provenance

Every snapshot argument is a `RepositorySnapshot(root, repository_id, commit_sha)`:

- `repository_id` and `commit_sha` MUST come from the caller as trusted
  resolver output (for example, the platform API answer to "what did I
  actually fetch"). The validator never reads repository content as a source
  of identity: any self-declared identity inside a repository is untrusted.
- A non-integer, non-positive `repository_id` fails closed at construction.
- `commit_sha` must be a full 40-hex commit or absent; abbreviated SHAs and
  branch/tag/ref names are never valid pins.
- `deployed_core` additionally requires the exact pinned `commit_sha`
  (missing provenance fails closed).
- The trusted locator (§3) and snapshot provenance are cross-checked; a
  mismatch is a trust failure, not a warning.

## 3. Trusted locator — external trust root

The locator is the single external trust root:

    runtime_control:
      repository_id   # security identity (positive integer)
      repository      # navigation only, never trusted
      canonical_ref   # navigation only
      contract_path   # where the public contract lives (default deployment.yaml)
    instance:
      repository_id   # security identity
      repository      # navigation only

`contract_path` is a repository-relative POSIX file path inside the materialized
Runtime-Control snapshot. It MUST remain contained by that snapshot and MUST NOT
use absolute/traversal/backslash path forms or traverse symlinks.

Instance identity lives only in the locator. The public Runtime-Control
contract MUST NOT carry Instance identity, lineage fields, migration
transaction fields, or credentials.

## 4. Deployment contract and binding

The public Runtime-Control contract (`deployment.yaml`) is allowlist-only:

- top-level keys: `schema_version`, `document_type`, `updated_at`,
  `deployment`, `core`.
- `deployment`: `id`, `topology` (`split`), `epoch` (positive integer
  fencing token), `write_state` (`active` | `frozen`).
- `core`: `repository_id`, `commit` (exact 40-hex), optional
  `repository_full_name` (navigation only).

Failures — missing contract, wrong `schema_version`/`document_type`, unknown
fields at any level, non-integer IDs, abbreviated SHA or ref-name pin,
identity mismatch against locator/provenance, Instance identity, lineage,
migration, or credential/token content — are all errors (fail closed).

`DeploymentBinding` has exactly two forms and no second schema:

1. `synthetic` — the fixture form (full 7-key mapping or YAML file,
   `context_type: synthetic`), the only form accepted by the standalone
   `validate_instance` surface.
2. `contract` — a projection built from an already structurally validated
   contract plus the trusted locator. `validate_deployment` uses this form;
   deployment authority stays in the contract validator, never in the
   binding.

## 5. Bootstrap order

1. Materialize the three snapshots offline (exact commits; caller records
   provenance from the resolver it used).
2. Obtain the trusted locator from the deployment authority.
3. Run `validate_deployment`; only an error-free result may be treated as a
   validated deployment candidate.

## 6. Explicit non-goals

This surface MUST NOT include `resolve_repository()`, `lookup_github()`,
`fetch_live_deployment()`, or `check_platform_permissions()`. Fetching,
resolution, permissions, epoch enforcement, and write-state routing belong to
future resolver/runtime surfaces. Validation here is deterministic, offline,
and structural; it is not complete secret detection.

## 7. Deployment-status boundary

This protocol does not own or assert live deployment status. Whether Learning OS is currently deployed, the active topology, deployment epoch, write state, and exact Core pin must be read from the current Runtime-Control deployment contract through the trusted locator.

A historical or development copy of this protocol MUST NOT be used to infer current deployment state. Nothing in this protocol authorizes migration, activation, promotion, freeze, rehearsal, or cutover.
