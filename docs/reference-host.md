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

The external conversation/model surface receives none of those objects. It can submit only one of three versioned reference-host operations:

- `read_learning_context`;
- `create_evidence`;
- `reconcile_knowledge`.

The host does **not** expose generic Instance replacement, raw repository access, successor claim, deployment promotion, Branch authority mutation, repository credentials, private Instance commit identity, or a raw checkout.

`scripts/reference_host.py` is transport-agnostic. A CLI, MCP adapter, subprocess bridge, or model-provider runner may sit outside it, but that adapter does not become learner-state authority. Transport framing, provider-profile isolation, network egress policy and process supervision remain host concerns and must be independently bounded by the environment that chooses a transport.

## Request / result shape

A request is a mapping with exactly `operation` and `arguments`. Unsupported operations or extra fields fail closed.

The successful read result contains only canonical document paths, document content, and the broker's bounded version token. It does not expose the private Instance head.

Write operations return only `{"applied": <bool>}`. Commit messages are host-generated rather than model-controlled.

Expected broker failures use stable public codes:

- `resolution_failed`;
- `guard_rejected`;
- `cas_conflict` (marked retryable).

Internal repository paths, remotes, commits and credential material are not reflected in those failure envelopes. Unexpected implementation failures remain host-operator exceptions rather than being converted into false success.

## Synthetic reference-journey coverage

`tests/test_reference_host.py` composes the thin host surface with the existing synthetic broker fixture and exercises:

1. one bounded learning-context read;
2. create-only typed Evidence persistence;
3. Knowledge reconciliation using the version token returned by the read;
4. idempotent Evidence retry;
5. visible CAS failure;
6. rejection of generic/continuity operations;
7. session close / revocation.

This is mechanical host/broker evidence. It does not decide whether an observation qualifies as Evidence, infer learner capability state, establish teaching quality, deploy a new Core, mutate real learner state, or constitute learner acceptance.

## Relationship to private host evidence

Private target-owned acceptance has already established bounded host-owned writable binding, operation-scoped admission, bounded host-mediated model-to-broker calls, and same-surface readback. The public reference surface intentionally retains only the reusable semantics needed for a synthetic journey.

It does not copy private locators, credential wiring, host paths, production process topology or provider-account configuration. A future public reference release may add a reproducible transport/demo around this surface only after the skeleton is stable; that release work is separate from this W5 extraction.
