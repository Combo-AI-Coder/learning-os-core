---
protocol: new-topic-start
version: "0.3"
schema_compatibility: "0.3"
---

# New Topic Start

Use this protocol when the learner begins a genuinely new learner-specific Topic, when an existing Topic has no usable goal/plan, or when `plan.status: awaiting_intake`.

A Topic is a learner-specific learning project. A Domain is reusable knowledge structure. Starting a new Topic MUST NOT automatically create a new Domain or treat the Topic as a Domain.

## 1. Read before asking

Read only the persistent state that can change the startup decision:

- relevant `learner/background.yaml` entries;
- relevant learner model/calibration/cost information when it has direct routing value;
- `learner/execution.yaml` only if materialized and relevant;
- any existing `topics/<topic>/goal.yaml`, `plan.yaml`, and `progress.yaml`;
- relevant curricula only when needed to build the initial route.

Do not ask again for learner-authoritative information already recorded unless it is ambiguous, stale because of an explicit newer statement, or newly decision-relevant in a different scope.

## 2. Learner-controlled intake depth

Resolve depth in this order: **current explicit instruction > Topic preference >
learner-global preference > Core default (`balanced`)**. The pure reference
resolver is `scripts/intake_policy.py`; it returns depth, source and question scope,
not a teaching plan or permission to write.

- `minimal`: ask only missing route-blocking information; otherwise start a
  provisional route and learn more through natural observation.
- `balanced`: preserve the existing compact intake; ask missing information likely
  to materially change the initial route, preferably in one message.
- `thorough`: invite a deeper but still relevant discussion of background, goals,
  constraints and preferences before settling the route. Do not turn this into an
  exhaustive questionnaire, mandatory placement test, or a gate to starting.

A current request to ask fewer questions can select `minimal` for this intake
without changing a durable default. A request to first understand more background
can select `thorough`. Persist a Topic/global default only when the learner
explicitly chooses that durable scope; a default for future new Topics is global
rather than an inferred Topic preference. Briefly explain the effective choice
when material, and let the learner change or reset it. Remove the scoped
`intake_depth` key to restore inheritance.

`goal.preferences.intake_depth` in the existing Topic Goal owns a Topic override;
`preferences.intake_depth` in the existing lazy Learner Execution document owns a
learner-global default. Never eagerly create either document just to store
`balanced`, copy a one-turn instruction into global state, or store preferences in
Core/Runtime-Control. Existing generation, role, revision and CAS guards still apply.

In every mode, use already-known context instead of asking again. A current request
to start immediately takes precedence over intake depth (`defer_intake`); begin a
provisional route and handle only genuinely blocking uncertainty without a survey.
Depth changes neither evidence/Knowledge semantics nor privacy or write authority.

### Narrow persistence and current interaction agency

The reference host exposes `set_intake_preference(scope, depth,
expected_version_token)` and `reset_intake_preference(scope,
expected_version_token)`; see `docs/reference-host.md` for admission and absence
semantics. Explicit durable scope is necessary before either call. An ambiguous
“少问一点” applies to the current intake only; “这个 Topic 以后少问” selects Topic
scope, while “以后新 Topic 默认深入了解” selects global scope. If durable scope is
unclear, honor the current interaction without silently persisting a guess.

“别问了先讲” or “简短点” during teaching is current interaction agency. Stop or
defer optional questioning and respond appropriately; do not infer an intake
default or a learner capability transition. Intake depth is the interaction cost
of learning relevant unknown context, not learning depth, capability, diagnostic
frequency or a mandatory placement test.

A new conversation is not a new Topic. Resume known Goal/Plan/Progress and
preferences rather than restarting intake. Even `thorough` can require zero
questions when relevant context is already sufficient. A persistent preference
change alone does not rewrite the existing Plan, Progress, Knowledge or Evidence.

Only the bound existing Topic Goal may receive a Topic preference. Explicit global
`minimal`/`thorough` can first-create the minimal lazy Learner Execution owner;
absent-global `balanced` or reset leaves it absent. An existing owner may store an
explicit `balanced`. Reset removes only the scoped key to restore inheritance,
never the owner or unrelated fields. All actions, including no-ops, retain the
existing role/capability, deployment/generation, fresh-read and CAS requirements.

High-value fields, when unknown and relevant:

- purpose / desired outcome;
- target depth or competence;
- horizon or deadline;
- realistic Topic-level resource preference when useful;
- relevant prior courses, projects, tools, or exposure;
- important constraints or content to include/avoid.

Do not use intake as a placement test. Prior exposure is background, not mastery.

If the learner explicitly asks to start immediately, intake MUST NOT block learning. Create a usable provisional route from known information and minimal assumptions; record only unresolved blocking/planning gaps that have a real future trigger.

## 3. Persist by responsibility

- durable history/exposure -> `learner/background.yaml`;
- learner-specific purpose/depth/horizon/constraints/success criteria -> `topics/<topic>/goal.yaml`;
- learner-global execution defaults -> `learner/execution.yaml` only when the learner has actually supplied global/default execution information;
- learner-specific route -> `topics/<topic>/plan.yaml`;
- capability state MUST NOT be created merely from background or intake answers.

Do not create synthetic evidence records merely to justify learner-authoritative goal/background writes.

## 4. Build Topic route separately from knowledge structure

Use existing Domain curricula when available. Create or extend reusable curriculum structure only as much as current teaching requires; do not build an exhaustive Domain taxonomy merely because a new Topic exists.

The initial Topic Plan SHOULD contain:

- a small set of meaningful Topic milestones;
- a coarse suggested sequence;
- explicit completion basis or exit criteria for milestones;
- only decision-relevant intake gaps;
- replanning triggers.

Plan status:

- `awaiting_intake`: route-changing intake is still required and the learner has not chosen to proceed without it;
- `provisional`: a usable route exists but assumptions or future planning gaps remain;
- `active`: the current route is sufficiently grounded;
- `paused`: the route is intentionally paused.

## 5. Materialize Subtopics lazily

A planned future Subtopic MAY exist only as a Topic Plan reference. Materialize `topics/<topic>/subtopics/<subtopic>/` only when it becomes a coherent multi-step unit that benefits from independent progress/continuity.

When materializing, prepare Subtopic Plan and Progress first and create `definition.yaml` last. The definition file acts as the materialization commit marker.

Do not precreate future Subtopics, Practice/Deep-Dive branches, coordination files, execution history, or learner knowledge files with empty state.

## 6. Placement and prerequisites

Do not run a broad placement test by default. Missing knowledge state means unknown, not inability. Use background only to choose a provisional starting point and observe naturally.

Use a targeted diagnostic only when uncertainty about a prerequisite can materially change the next teaching action and is worth the learner/flow cost.

## 7. Show and begin

After intake is answered, left unknown, or explicitly skipped:

1. persist learner-authoritative durable updates;
2. create a usable Topic Goal/Plan;
3. materialize only the current useful Subtopic if needed;
4. show a concise adaptive route;
5. begin full teaching.

Do not present a generic curriculum map as if it were the learner-specific plan.