# Learning OS product direction

Status: current public-safe product direction for the reusable Learning OS product. This document is not deployment authority, learner state, a release plan, or an implementation authorization ledger.

## North Star

Learning OS is a **learner-owned longitudinal learning system**.

Its durable value is not that one particular model can tutor well in one conversation. Models, tutors, retrieval systems, quiz generators, visualization tools, and other execution surfaces may improve or be replaced over time.

Learning OS should preserve the learner's durable learning world across those changes:

- what the learner is trying to learn and why;
- the current route and meaningful progress through it;
- evidence about demonstrated capabilities, uncertainty, and weak spots;
- the distinction between plan completion and demonstrated understanding;
- why the system recommends the next learning move;
- durable continuity across replaceable conversations, models, and execution surfaces;
- learner ownership, privacy, auditability, and recoverability of that state.

The system should make the learning state more trustworthy and useful over time, not merely make an individual tutoring exchange feel intelligent.

## Initial target user

The first public product should primarily serve an **advanced self-directed learner** studying complex technical or research-oriented subjects over long periods.

Representative needs include:

- building precise conceptual understanding rather than only preparing for a short-term test;
- moving from foundations toward paper reading, critique, reproduction, and eventually research capability;
- maintaining a coherent route through topics that may branch, detour, or resume after long gaps;
- receiving targeted diagnostics without confusing missing prerequisites, terminology, or representation familiarity with failure of the target concept;
- accumulating evidence-backed capability state without requiring exhaustive testing or constant manual bookkeeping.

Ordinary university coursework may be supported, but the first product direction should not be shaped primarily as a generic homework, exam-prep, LMS, flashcard, or quiz application.

## Product ownership boundary

Learning OS should **own** the durable semantics that need to survive model/tool replacement:

- Goal / Plan / Progress relationships;
- learner-specific Knowledge and capability evidence;
- Evidence provenance and integration rules;
- learner-specific execution and continuity state;
- long-horizon route reconciliation and resume semantics;
- privacy/trust boundaries between reusable product semantics and private learner state;
- auditable reasons for important learning-state transitions.

Learning OS should **compose or reuse** strong external capabilities when ownership is unnecessary, including:

- general conversational tutoring and explanation;
- source-grounded question answering;
- generic quiz/flashcard generation;
- generic visual explanation or interactive media;
- model-specific reasoning, search, or tool-use capabilities.

A useful external capability may become an execution surface for Learning OS without becoming the authority for learner state.

## Current product phase

V0.4 established the split Core / Instance / Runtime-Control trust model and closed the main technical migration/validation frontier. The next phase is **learning-outcome-first** rather than infrastructure-first.

The primary product evidence source should now be sustained real learning. Product work should be pulled by failures or opportunities observed in that learning rather than by a desire to expand the framework for its own sake.

The most important open teaching/product frontiers are reusable concerns, not a copy of any one learner's current route:

- proactive diagnostic targeting;
- knowledge-anchored introduction of new concepts and prerequisites;
- concept closure that connects role, mechanism, consequence, and surrounding system;
- Evidence -> Knowledge integration that gradually produces useful learner capability state;
- learner-visible orientation and route coherence during real technical learning.

Real learner progress, active Topics/Subtopics, and the current milestone belong only in the private Instance and must not be copied into Core product-direction documents.

Do not fabricate learner progress, mastery, real Evidence, or real learning sessions solely to claim product acceptance. This does **not** prohibit synthetic Core fixtures: reusable multi-Branch, cross-Topic, validator, protocol, and failure semantics should continue to receive deterministic synthetic unit/acceptance coverage where useful. Real-learning evidence and synthetic product verification are separate evidence classes.

## Initial public-release shape

The initial public release should be a **reference product / technical early release**, not a standalone mass-market app and not architecture documentation alone.

A credible first release should make the product direction understandable and runnable through a small, public-safe surface such as:

- the reusable Core;
- a clear quickstart;
- synthetic/example learner and Instance material that contains no real learner data;
- one end-to-end demonstration showing how durable learner state, Evidence/Knowledge transitions, and explainable next-step selection fit together;
- enough deployment/setup documentation for a technical self-directed learner to understand the trust and ownership model.

Public readiness should not be equated with feature count or UI polish. A small release that clearly demonstrates trustworthy longitudinal learner state and useful learning behavior is preferable to a broad generic tutor surface that duplicates mature external products.

Packaging, onboarding, licensing, documentation, examples, and deployment ergonomics are productization concerns. They should support the reference product without becoming a second learner-state authority.

### First technical-preview readiness

The first public technical preview requires all four of the following: the three requirements confirmed in the [Core #34 product-direction decision](https://github.com/Combo-AI-Coder/learning-os-core/issues/34#issuecomment-6186452535), plus the [Q15 trusted first-use requirement](https://github.com/Combo-AI-Coder/learning-os-core/issues/19#issuecomment-6215540425). [Core #34 retains overall readiness ownership](https://github.com/Combo-AI-Coder/learning-os-core/issues/34#issuecomment-6215549264); Core #19 owns the narrower first-entry experience/initialization gap.

1. **Real longitudinal use:** at least one real, recoverable, longitudinal learning journey must demonstrate meaningful actual use of durable learner state for both continuity and a defensible next teaching/learning action. Retain the actual results and limitations with the appropriate private owner; do not copy real learner state, sensitive source data, or private transcripts into public Core.
2. **A separate reproducible public reference:** a technical self-directed learner must be able to run a public-safe synthetic/example Instance and Core journey through documented setup/quickstart instructions, without private data. The example must explain durable-state and Evidence/Knowledge transitions and the basis for the next action.
3. **Evidence-bounded claims:** clearly distinguish mechanically verified behavior, observations from real learning, and teaching/learning effects that remain unproven. Public claims must match the actual evidence and identify its limitations.

4. **Minimal trusted first use:** a new technical learner must be able to start from a blank learner Instance through supported setup, identity and permission checks, and a trusted first-use owning transition establishing the minimum Topic Goal/provisional Plan, Main Branch authority/initial generation and runtime binding. The learner can express a goal, conduct useful learning, save a meaningful authorized checkpoint or Evidence without fabricated mastery, and resume from durable state in a fresh consumer context. A maintainer must not need to pre-seed Goal/Main/generation by editing YAML. An interactive CLI plus a narrow trusted host may suffice; a standalone UI, full course catalog or broad platform is not required.

At first entry, make the intake-depth choice discoverable through a concise optional invitation, without blocking an explicit request to begin. Both the experience choice and making a choice are optional. Ignoring or skipping the invitation uses the applicable inherited/product default; silence is not a personalized preference or a request for minimal intake. Current explicit instructions retain precedence over applicable scoped preferences and the Core default. Core's implemented `balanced` intake fallback is not evidence of representative learner preference; future default calibration requires appropriate intended-user evidence. Learner choice never waives correctness, provenance, consent, identity, deployment/generation fencing or target CAS.

The separate seeded public reference remains useful, but cannot alone satisfy trusted first use. Initial-authority acquisition must be a supported owning transition, not inferred from reading a generation or supplied by test setup. A bounded synthetic verification proposal should cover a genuinely blank Instance, missing/revoked authority, stale deployment, interrupted/retried initialization without duplicates, initial Topic creation, an authorized meaningful save, and fresh-consumer resume. These are verification requirements, not executed evidence, initialization implementation permission, or a claim of cross-crash atomicity/universal rollback. Actual learner/Host verification remains separate.

A large controlled experiment is **not a prerequisite** for this initial technical preview; establishing causal, sustained learning benefit remains a later research frontier. A one-off fluent tutoring answer or synthetic test PASS does not substitute for the real journey.

## First post-restart product cycle

The first product cycle follows this decision order:

1. **Production-parity assessment** — inspect the diff between the currently deployed Core and the accepted Core, and run promotion/recovery checks.
2. **Separate promotion decision** — changing Runtime-Control or deploying a newer Core remains a separate deployment action with its own authority and verification.
3. **Sustained real learning** — once the active runtime is ready for dogfooding, let real interactions exercise the product.
4. **Evidence-driven product fixes** — prioritize fixes backed by real-use evidence (diagnostic targeting, knowledge anchoring, concept closure, capability-state formation).
5. **Public packaging** — package behavior that has survived real use into the reference early release.

This order rejects packaging an unvalidated product surface solely because Core is stable, or expanding learner-model/framework mechanics speculatively.

## Scope

- Runtime-Control remains the sole deployment authority; Core `main` alone does not deploy.
- Unknown remains unknown when evidence is insufficient; tests, Progress completion, and fluent responses do not equal mastery.
- Synthetic Core fixtures verify reusable product semantics, separate from real learner outcomes.
