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

The primary evidence source should now be sustained real learning. Product work should be pulled by failures or opportunities observed in that learning rather than by a desire to expand the framework for its own sake.

The current Modern Language Models learning line is the first natural product surface. Its active `tokens/context/representation` milestone can exercise the current open teaching frontiers:

- proactive diagnostic targeting;
- knowledge-anchored introduction of new concepts and prerequisites;
- concept closure that connects role, mechanism, consequence, and surrounding system;
- Evidence -> Knowledge integration that gradually produces useful learner capability state;
- learner-visible orientation and route coherence during real technical learning.

Do not fabricate additional Topics, Study Branches, mastery evidence, or learning sessions solely to force acceptance coverage. Multi-Branch and cross-Topic behavior should be tested when genuine learner needs create those situations.

## Public early-release direction

The semester-level route calls for a publicly presentable early Learning OS release. Public presentation should demonstrate the product idea above: a reusable Core plus learner-owned private state and a credible real-learning workflow.

Public readiness should not be equated with feature count. A small release that clearly demonstrates trustworthy longitudinal learner state, explainable next-step selection, and useful real-learning behavior is preferable to a broad generic tutor surface that duplicates mature external products.

Packaging, onboarding, licensing, documentation, examples, and deployment ergonomics are productization concerns for the public release and may be shaped separately from the learner-state semantics.

## Boundaries and non-inferences

- This document does not deploy or promote any Core commit. Runtime-Control remains the sole deployment authority.
- Core `main` may be ahead of the deployed Core; development integration and production promotion remain separate decisions.
- This direction does not declare every existing Learning OS mechanism permanently necessary. Historical collaboration mechanisms may still be retired when they lack independent product/runtime value.
- This direction does not make learner modeling exhaustive. Unknown should remain unknown when evidence is insufficient.
- This direction does not make tests, Progress completion, or a fluent tutoring response equivalent to mastery.
- This direction does not require Learning OS to outperform every external tutor feature. The product may deliberately rely on replaceable external capabilities while owning the durable learner-state semantics.
- This document is not a fixed V0.5 roadmap. Real learner outcomes and explicit user decisions determine which implementation slice comes next.
