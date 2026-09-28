---
protocol: repository-governance-policy
version: "0.2"
schema_compatibility: "0.4"
---

# Repository Governance Policy

This policy defines product/repository mutation boundaries for the split Learning OS architecture. It does not define Agent/session/project-design writer generations. Current execution-environment authority and target-repository rules govern project collaboration; this Core owns reusable product semantics and product/runtime write-safety rules.

## 1. Principles

1. Core, Instance, Runtime-Control, and Private Control are distinct authority surfaces.
2. Core product changes use feature branch -> pull request -> validation/review -> merge -> readback. Ordinary Runtime never writes Core.
3. Runtime-Control owns the deployed Core pin, deployment epoch, topology, and write state; Core never self-deploys.
4. Instance owns learner/runtime state. Direct Instance writes are allowed only where the responsible product protocol permits them and all applicable deployment, learning-lineage, fresh-read, semantic-reconciliation, and target-CAS guards pass.
5. Learning Branch generation/handoff semantics remain product continuity where they protect learner-state continuity. Project/session collaboration continuity is external to Core.
6. Private Control may retain project-design lineage/history for its own target governance, but Core does not define or grant that authority.
7. Green validation is evidence, not deployment or acceptance.

## 2. Write classes

### CORE_PROTECTED
Reusable Core material: configuration, product protocols, validators, tests, CI, reusable Domain assets, and public-safe product documentation. Mutation uses the protected-branch / pull-request path.

### INSTANCE_CAS
Mutable learner/runtime canonical state in the private Instance. Direct persistence is permitted only under the responsible product protocol plus fresh-read and target CAS, with deployment and learning-lineage guards where applicable.

### IMMUTABLE_APPEND
Create-only product/runtime history such as Evidence, execution facts, coordination events, and learning handoffs where the owning protocol permits append.

### GENERATED_OR_PROJECTION
Rebuildable or derived runtime state. Source canonical state outranks stale projections.

### EXTERNAL_CONTROL
State owned by Runtime-Control or Private Control rather than Core/Instance product persistence. Mutation follows that repository's own rules and applicable current execution authority.

### UNKNOWN
No write is permitted until current canonical semantics determine the correct owner, class, and authority.

## 3. Current split-plane inventory

| Path / object | Plane / class | Normal mutation path | Notes |
| --- | --- | --- | --- |
| Core `config/**`, `protocol/**`, `scripts/**`, `tests/**`, `.github/**` | Core / CORE_PROTECTED | PR + required validation/review | reusable product/governance semantics |
| Core `README.md`, `AGENTS.md`, `requirements-dev.txt`, public-safe `docs/**` | Core / CORE_PROTECTED | PR + required validation/review | documentation cannot assert deployment |
| Core `domains/_template/**` | Core / CORE_PROTECTED | PR + required validation/review | reusable template |
| Core `domains/<domain>/curriculum.yaml` | Core / CORE_PROTECTED | PR + required validation/review | reusable curriculum base |
| Core `domains/<domain>/probes.md` when declared by `domains.optional_probe_file` | Core / CORE_PROTECTED | PR + required validation/review | optional pull-loaded reusable teaching/diagnostic asset; absence is normal |
| Instance learner / Topic / Subtopic mutable state | Instance / INSTANCE_CAS | product-authorized fresh-read + CAS | deployment and learning-lineage guards where applicable |
| Instance Evidence, execution facts, coordination events, learning handoffs | Instance / IMMUTABLE_APPEND | owning product protocol | real learner/private data stays out of Core |
| Instance reports / selected execution projections | Instance / GENERATED_OR_PROJECTION | owning product protocol | reconcile against canonical sources |
| Instance `runtime/ui/**` sequence metadata | Instance / INSTANCE_CAS | naming-policy CAS | conversation naming is not collaboration writer authority |
| Runtime-Control `deployment.yaml` | Runtime-Control / EXTERNAL_CONTROL | narrow maintenance CAS under target rules | sole deployed Core pin / epoch / write-state authority |
| Private Control project-design lineage, receipts, migrations, legacy collaboration history | Private Control / EXTERNAL_CONTROL | current execution authority + target rules | not ordinary Core product/runtime state |
| unclassified new path | UNKNOWN | none until classified | fail closed |

A reusable Domain directory is not classified only by its parent. Current declared Core assets are `curriculum.yaml` plus the optional probe asset named by `domains.optional_probe_file`; any other new Domain path is `UNKNOWN` until explicitly classified.

## 4. Core-change rule

Before proposing Core integration:
1. recover current explicit user instruction and applicable target-repository rules;
2. fresh-read canonical Core `main` and decision-relevant product files;
3. create a feature branch from the observed baseline;
4. keep the change within product Core scope and use only synthetic/public-safe fixtures;
5. run `python scripts/validate_learning_os.py . --core` and the full unit suite;
6. inspect the exact PR diff and current platform checks/reviews;
7. merge only through the repository's protected-branch path;
8. fresh-read merged `main`.

A project/session generation is neither required nor accepted as a Core mutation credential.

## 5. Instance runtime-state rule

Core product protocols may authorize direct Instance persistence, but only for Instance-owned state. Deployment fencing, learning Branch generation guards, fresh-read reconciliation, semantic validation, target CAS, and create-only immutable semantics remain distinct and must not be weakened because project collaboration moved outside Core.

## 6. Runtime-Control and Private Control

Runtime-Control is product deployment authority and stays minimal. Private Control is not a second deployed-state authority; it may hold private project-design/migration/receipt material according to its own target governance. Core must not import private collaboration state merely to make an Agent work surface self-contained.

## 7. Prohibited behavior

- direct ordinary mutation of protected Core `main`;
- importing real learner/private data into Core development or CI;
- using Core configuration, a physical conversation, or a product learning generation as project/session writer authority;
- treating a green PR as deployment or user acceptance;
- blind overwrite / last-write-wins on mutable Instance state;
- weakening validators, tests, or branch protections merely to obtain green status;
- force push/history rewrite as ordinary repair.

## 8. Platform enforcement and evolution

The split architecture exists so Core can use strict protected-branch governance without blocking ordinary Instance persistence. The semantic Core contract requires pull-request mutation, no force push/deletion, and the checks declared in `config/core.yaml`. Current platform settings are runtime evidence and must be fresh-checked when they matter.

Add new write classes or paths only when a real product/operational need appears. Do not recreate a generic project/session workflow database inside Core. If a future collaboration mechanism has independent product/runtime value, justify it by that value rather than by continuity of a particular Agent implementation.
