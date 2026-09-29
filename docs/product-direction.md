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

## First post-restart product cycle

The first product cycle follows this **decision order**, not an automatic mutation sequence:

1. **Production-parity assessment** — inspect the complete difference between the currently deployed Core and the current accepted Core, run the applicable promotion/recovery checks, and determine whether promotion is ready.
2. **Separate promotion decision** — changing Runtime-Control or deploying a newer Core remains an explicit deployment action with its own authority and verification. This document does not authorize it.
3. **Sustained real learning** — once the active runtime is appropriate for dogfood, continue ordinary advanced self-directed learning and let real teaching/learning interactions exercise the product.
4. **Evidence-driven product fixes** — prioritize changes that are supported by material real-use evidence, especially around diagnostic targeting, knowledge anchoring, concept closure, and useful capability-state formation.
5. **Public packaging** — package the behavior that has survived real use into the reference-product early release.

This order intentionally rejects two defaults:

- do not package an unvalidated product surface merely because the technical Core is stable;
- do not proactively expand learner-model mechanics, infrastructure, or framework layers merely because they can be designed.

## Boundaries and non-inferences

- This document does not deploy or promote any Core commit. Runtime-Control remains the sole deployment authority.
- Core `main` may be ahead of the deployed Core; development integration and production promotion remain separate decisions.
- This direction does not declare every existing Learning OS mechanism permanently necessary. Historical collaboration mechanisms may still be retired when they lack independent product/runtime value.
- This direction does not make learner modeling exhaustive. Unknown should remain unknown when evidence is insufficient.
- This direction does not make tests, Progress completion, or a fluent tutoring response equivalent to mastery.
- This direction does not require Learning OS to outperform every external tutor feature. The product may deliberately rely on replaceable external capabilities while owning the durable learner-state semantics.
- Synthetic Core fixtures are valid verification evidence for reusable product semantics, but they are not real learner-outcome evidence.
- This document is not a fixed V0.5 roadmap. Real learner outcomes and explicit user decisions determine which implementation slice comes next.
