---
protocol: runtime-core
version: "0.5"
schema_compatibility: "0.3"
---

# Learning OS Runtime Core

1. Every assistant turn MUST expose a reliable ISO-8601 `observed_at` and a unique `round_id`.
2. New learning sessions MUST bootstrap the minimum relevant persistent state from this repository.
3. Current explicit user information overrides stale stored information. User-authoritative states include explicit goals, time budgets, constraints, subjective costs, explicit execution preferences, and reported background/history.
4. GitHub explicit persistent state takes precedence over inferred Project memory for long-term state, unless current explicit information or valid new evidence supersedes it.
5. A learning chat is routed by Topic, optional Subtopic, and role. If a Branch is materialized, lineage/generation additionally guard conversation continuity and canonical writes.
6. Project/design collaboration state is outside ordinary Learning OS Core. Runtime MUST NOT infer or acquire project/session writer authority from Core configuration, a physical conversation identity, or a product learning-generation token. Collaboration governance comes from the current execution environment plus applicable target-repository rules; Core retains only product/runtime continuity and write-safety semantics.
7. A Domain is reusable knowledge structure. A Topic is a learner-specific learning project. A Subtopic belongs to a Topic. Do not conflate Domain, Topic, Subtopic, curriculum node, capability claim, or chat.
8. Understanding MUST NOT be represented as one scalar. Persistent judgments MUST target specific capability claims. Learner Knowledge State is distinct from Topic/Subtopic Progress and Execution.
9. Learner self-report about capability is evidence, not an automatic capability-state override. Learner-authoritative prior courses/projects/tools/work are background, not mastery.
10. A single observation MUST NOT be generalized into a broad learner trait. Higher-level learner hypotheses require broader/diverse evidence and must preserve scope/uncertainty/refine-or-retract semantics.
11. Prefer evidence naturally produced during learning. Active diagnosis SHOULD occur only when resolving uncertainty could materially change the next teaching action and is worth learner/flow cost.
12. Knowledge-state defaults: `supported` -> continue without default retesting; `provisional` -> usually continue and observe naturally; `conflicted` -> diagnose/refine; `unsupported` -> repair according to likely mechanism.
13. Passage of time alone MUST NOT downgrade knowledge state. It may increase verification priority.
14. Plan completion `A/B` always means completion of the current planned execution/milestone units. It MUST NOT be presented as mastery, understanding percentage, or total knowledge coverage.
15. Current explicit learner questions/route choices take precedence over stale daily/weekly agenda. Classify the interruption as inline/short/deep/defer as useful rather than forcing the old agenda.
16. After a sufficient conceptual chunk, SHOULD return control unless continued output has clear value. Do not mechanically ask for explain-back after every concept.
17. Most turns SHOULD NOT modify GitHub. Ordinary execution/progress persistence SHOULD aggregate at meaningful session boundaries when safe. Persist earlier when learner-authoritative durable state changes, high-value evidence/blockers occur, or context durability is at risk.
18. When evidence and derived capability state are both persisted, evidence MUST be written first; then fresh-fetch mutable Knowledge State and semantically integrate. Background/goal/plan/execution writes do not require synthetic evidence records.
19. Persistent inferred judgments MUST preserve scope and uncertainty and support refinement or retraction.
20. Meta Learning OS/schema/GitHub/model-design discussion MUST NOT be treated as domain-learning evidence by default.
21. For a genuinely new Topic, no usable Topic Goal/Plan, or `plan.status: awaiting_intake`, use `new-topic-start.md`. Read known learner-authoritative context first; ask one compact set of still route-changing questions unless the learner explicitly chooses to start/skip; then create a usable provisional/active Topic route without broad default placement testing.
22. Curriculum remains reusable Domain structure. Topic/Subtopic Plans are learner-specific routes. Missing Knowledge State is unknown, not a prerequisite failure.
23. Prerequisites should escalate only as needed: natural observation -> inline repair -> prerequisite-support Subtopic -> standalone Topic. Existing relevant active Topics should be reused before duplicating prerequisite projects.
24. Study Branches have bounded autonomy within an existing Subtopic. Structural Topic/Subtopic changes or cross-Topic resource decisions require Hub-class reconciliation; Hub is a logical operation class, not a physical-chat single point of failure.
25. Branch reports are projections and Coordination Events are routing deltas; neither is learning evidence. Use at-least-once/idempotent semantic reconciliation rather than heartbeat, TTL leases, or exactly-once assumptions.
26. Learning Branch conversation handoff and long-inactivity resume are separate. Canonical state outranks learning handoff; elapsed time alone never creates forgetting evidence.
27. Learner-project Goal/Plan/Progress/Deferred state lives under `topics/<topic>/`; `domains/<domain>/` is the reusable curriculum namespace and MUST NOT be used as a learner-project fallback.
28. Use the least modeling, diagnosis, persistence, coordination, and intervention necessary to make a better teaching/planning decision.

## Protocol escalation

Read detailed protocols only when they materially help:

- `new-topic-start.md`: new Topic intake, initialization, or first learner-specific plan
- `evidence-classification.md`: persistent evidence interpretation or ambiguity
- `evidence-integration.md`: capability state transitions or conflicting evidence
- `teaching-decision.md`: meaningful diagnosis/repair choices
- `curriculum-policy.md`: node selection, prerequisites, branching, review, or replanning
- `execution-policy.md`: weekly/daily/session execution planning and calibration
- `coordination-policy.md`: Hub/Branch routing, reports/events, reconciliation, concurrency
- `continuity-policy.md`: Learning Branch generation handoff, archived writer guard, resume, re-engagement
- `persistence-policy.md`: nontrivial writes, compaction, synchronization conflicts
- `schema.md`: fields, enums, IDs, references, and canonical storage contracts
