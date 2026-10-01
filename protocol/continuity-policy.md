---
protocol: continuity-policy
version: "0.4"
schema_compatibility: "0.3"
---

# Continuity Policy

Continuity preserves safe learning across conversation generations and long periods of inactivity without making chats or scheduled reminders authoritative state.

## 1. Conversation identity

A materialized learning Branch may have:

- Topic;
- optional Subtopic;
- role (`hub`, `main`, `practice`, `deep_dive`);
- stable Branch ID;
- lineage ID;
- integer generation.

Branch identity, physical `Cxx` conversation sequence, and conversation generation are distinct. A handoff may move work from physical `C01` to `C02` while also advancing generation, but the `Cxx` label itself neither determines nor authorizes the generation.

## 2. Lifecycles

Conversation generation lifecycle:

- `active`
- `idle`
- `handoff_pending`
- `archived`
- `deprecated`

Branch registry lifecycle is separate and intentionally smaller: `active`, `idle`, `retired`.

A Topic/Subtopic completion does not itself require the conversation generation to be archived.

## 3. Active-generation guard

Before a materialized Branch performs canonical persistent writes, it MUST verify that its generation is the active generation in Branch runtime.

An archived/non-active generation remains readable and may answer local/meta questions, but it MUST NOT write canonical learning evidence, Knowledge State, Progress, execution records, reports, or plan changes for that lineage.

If a learner supplies meaningful new work in an archived generation, the active generation must absorb it before it becomes canonical.

No heartbeat or TTL lease is required; `active_generation` is sufficient for the V0.3 writer guard.

### Archived-predecessor awareness

After a successor generation has successfully claimed a Branch, the predecessor is no longer an authoritative learning work surface even if its physical chat remains open.

When an archived/non-active predecessor conversation is addressed again for substantive Branch work, it MUST fresh-read the Branch runtime before teaching or attempting a canonical write. If its established `session_generation` is no longer the canonical `active_generation`, it MUST:

- explicitly acknowledge that its generation has completed handoff and is archived/non-active;
- identify the current canonical `active_generation` when available;
- state that canonical writer authority now belongs to the active generation;
- refuse ordinary Branch teaching/write work that would create new canonical Evidence, Knowledge, Progress, Execution, Plan, or Report state in the archived generation;
- remain available for read-only/local/meta questions, historical explanation, handoff inspection, or recovery support.

Meaningful learner work accidentally supplied in an archived predecessor MUST NOT become canonical there; the active generation must absorb/reconcile it first.

This awareness rule does not create a new persistent acknowledgement flag. Canonical Branch runtime remains the authority; an open chat is never evidence that its generation is still active.

## 4. Conversation lifecycle decision and handoff trigger

Conversation handoff is continuity maintenance, not a periodic learning event. The decision is semantic: ask whether continuing to use the current physical conversation creates a material continuity, context-quality, recovery, or writer-surface risk that a successor conversation would reduce.

Runtime lifecycle decisions are:

- `continue_current`: keep the current active generation as the normal work surface;
- `recommend_handoff`: tell the learner that a successor conversation is advisable, but do not change Branch runtime yet;
- `execute_handoff`: perform the handoff transaction only after learner authorization;
- `resume`: after inactivity, keep the same physical conversation when it remains a suitable work surface and use the resume modes in Section 7.

### 4.1 Who may trigger

The active assistant SHOULD detect handoff need and recommend it when appropriate. A learner may also directly request a new conversation or handoff.

A recommendation alone MUST NOT move the generation to `handoff_pending`. Entering `handoff_pending` changes writer availability and therefore requires learner authorization. Authorization is satisfied when the learner explicitly requests or accepts the handoff/new-conversation transition for the current Branch.

Once authorized, the active generation MAY execute the transaction in Section 6. The successor conversation must still independently bootstrap, recover, and claim; learner authorization to hand off does not pre-activate the successor.

### 4.2 When to recommend

Recommend handoff when at least one material condition is present, for example:

- continued use of the current physical conversation is creating meaningful context-capacity or continuity risk;
- the learner explicitly wants a fresh physical conversation while preserving the same logical Branch;
- the conversation has accumulated substantial testing, debugging, administration, or unrelated context that materially degrades its value as the normal learning surface;
- the current physical work surface is otherwise becoming unreliable or cumbersome for preserving the active teaching thread, while a successor can recover cleanly from canonical state plus a small handoff.

Prefer semantic evidence over a fixed threshold. Runtime context-pressure signals MAY inform the recommendation, but they are not learner state and MUST NOT be persisted as canonical learning facts.

### 4.3 Non-triggers

The following MUST NOT by themselves force or automatically execute a handoff:

- elapsed time or inactivity;
- day/week boundaries;
- a fixed message/turn count;
- a fixed token count or context-percentage threshold;
- completion of a concept, milestone, Subtopic, or Topic;
- ordinary Weekly/Daily reconciliation;
- ordinary replan or route adjustment.

These events may coincide with a convenient transition point, but a handoff still requires a material continuity/work-surface reason plus learner authorization.

If handoff is recommended but not authorized, the current active generation remains authoritative and MAY continue normal work. If durability is materially at risk, use ordinary persistence rules to preserve qualifying fragile canonical state; do not use `handoff_pending` as an implicit checkpoint.

## 5. Learning handoff

A Learning Handoff is immutable supplemental continuity for a lineage generation transition. It is not evidence and is not canonical learner state.

A useful handoff should contain only continuity that cannot be recovered naturally from canonical state, such as:

- identity and generation transition;
- canonical revision references;
- current teaching thread;
- last meaningful learner action;
- pending exercise/task;
- unresolved local questions/blockers;
- avoid-retesting/deferred references when relevant;
- next likely action.

Do not copy full Goal, Plan, Knowledge State, or evidence history.

Authority: canonical state > handoff > conversation memory.

## 6. Handoff transaction

Safe order:

1. create and verify the immutable handoff;
2. fresh-fetch Branch runtime;
3. set the outgoing generation to `handoff_pending` and record a pending successor generation;
4. the successor conversation bootstraps canonical state plus the handoff;
5. the successor claims the pending generation, making it active and retiring/archive-locking the predecessor.

Do not mark a successor active before it actually claims the generation.

For a narrow ordinary-Runtime successor, the host may execute the canonical claim on behalf of the successor work surface after the learner-authorized handoff has already produced `handoff_pending` plus its immutable handoff. The claim is a dedicated continuity transition, not a generic learner-state write: it must validate the exact pending authority/handoff, use the current deployed Core and Deployment Guard, apply target-blob plus exact Instance branch-head CAS, and issue the successor session capability only after canonical readback confirms the new active generation. That successful claim establishes the successor session generation; reading the pending or active generation never does. This host execution changes the physical executor of the already-authorized claim, not the learner authorization requirement.

If a pending successor is never created, the old generation MAY be explicitly restored to active by clearing the pending successor. Do not silently continue canonical writes while `handoff_pending`.

## 7. Resume after inactivity

Long inactivity is different from conversation handoff. Resume chooses whether the old route should still be followed.

Runtime resume modes:

- `direct_resume`: goal/route remain valid, no important blocker, next step is coherent;
- `light_reorientation`: brief context-building/natural verification has learning value before continuing;
- `route_reconciliation`: goals, execution windows, priorities, blockers, or route assumptions materially changed.

Elapsed time alone MUST NOT downgrade capability state. It may raise verification priority depending on dependency importance, previous evidence strength, and failure cost.

Do not blanket-review all previously learned material because time passed. Work backward from the next valuable node and naturally verify only relevant prerequisites.

Expired Daily/Weekly execution windows are historical execution records; they must not continue to appear as current day/week plans. Reconcile unresolved work into the new window without debt framing.

If the learner returns with little time and explicitly asks to continue, planning maintenance SHOULD NOT block a safe short learning objective when the route can be resumed conservatively.

## 8. Re-engagement

ChatGPT scheduled tasks/reminders may optionally provide an inactivity watchdog. They are delivery/runtime mechanisms, not Learning OS state authority.

Canonical learner intent may record whether re-engagement is enabled and the learner-chosen inactivity threshold. Do not silently enable it or invent a threshold.

`last_meaningful_learning_at`, if cached in learner execution state, is a recoverable convenience projection; immutable/closed execution records are the stronger factual source. Meta Learning OS design/admin interactions do not count as meaningful domain learning.

A reminder failure or missing provider task ID MUST NOT corrupt learning state. If an important reminder synchronization fails, tell the learner rather than pretending it is active.
