---
protocol: persistence-policy
version: "0.7"
schema_compatibility: "0.3"
---

# Persistence Policy

GitHub stores durable state, not full chat transcripts. Schema support does not imply every turn should write every artifact.

Persistence levels:

- `P0`: no persistence
- `P1`: persist qualifying evidence only
- `P2`: persist evidence plus a meaningful derived capability/model transition

Learner-authoritative background/goal/execution updates, planning maintenance, execution records, and coordination artifacts are persistent writes outside the P1/P2 evidence shorthand; they do not require synthetic evidence records merely to justify storage.

## 1. Persistence gate

Persist only when at least one is true:

- persistent Knowledge State changes;
- a high-value diagnostic/learning observation qualifies as evidence;
- an existing persistent learner hypothesis is materially supported/challenged;
- a learner-authoritative durable update occurs, including background, Topic Goal, learner-global execution defaults, constraints, or subjective costs;
- Topic/Subtopic route/progress changes have future recovery value;
- deferred state/prerequisite debt changes;
- a meaningful calibration event occurs;
- a meaningful execution session should be retained;
- a routing/coordination change is significant enough to affect Hub decisions;
- continuity/handoff persistence is needed to prevent context loss.

Otherwise use `P0`.

## 2. Learner-authoritative direct writes

Explicit learner reports about goals, time budgets, constraints, subjective costs, completed courses/projects/tools/work, prior exposure, or explicit execution preferences MAY be written directly to their responsible canonical files.

Do NOT create fake evidence merely to store these facts. If the same report is also used to support/challenge a capability claim, capability evidence classification/integration still follows the evidence protocol.

Current explicit learner information overrides stale stored learner-authoritative values.

## 3. Learner feedback persistence routing

Most teaching feedback is runtime guidance first and SHOULD remain `P0` when its useful effect is exhausted within the current interaction.

Route feedback by semantics rather than storing a generic feedback log:

- a one-off statement such as "this question is too easy" or "this explanation is confusing" normally changes the immediate teaching/probe decision and is not persisted by itself;
- an explicit durable preference or subjective cost MAY be written directly to the responsible learner preference/cost artifact when the learner is clearly expressing a future-relevant preference, not merely reacting to one local example;
- repeated scoped observations about task difficulty, duration, diagnostic value, or interaction calibration MAY become learner calibration signals when they have future decision value;
- a higher-level learner-model hypothesis about which representations, probes, or strategies tend to work requires broader cross-event evidence and SHOULD update more slowly than immediate adaptation;
- capability Knowledge State MUST NOT change solely because of teaching feedback or preference; capability evidence still follows evidence classification and integration. Concrete reports about how an existing performance was produced follow the reinterpretation rule below, not a direct feedback-to-state transition;
- do not create an Evidence record merely because feedback occurred. Persist Evidence only when the feedback is part of a qualifying diagnostic/learning observation whose future interpretation matters.

When a learner's feedback materially changes the interpretation of an already observed task in the same interaction, classify the resulting observation with the feedback included as context. If historical immutable Evidence later needs reinterpretation, normally add a new evidence/interpretation event rather than rewriting the old record.

A concrete report that a hint supplied an earlier explanation is interpretive
context for that performance, not another performance or evidence of inability.
Reassess the report together with the original Evidence, current Knowledge and
relevant portfolio under `evidence-integration.md#withdrawing-an-apparent-support-basis`.
That justified integration may persist refinement or withdrawal of stale support;
the report alone does not authorize a state transition or bypass this policy's
persistence, fresh-read and CAS gates. Vague task feedback and preferences retain
the non-transition boundary above.

Avoid unbounded accumulation of local likes/dislikes. Persist only scoped patterns or learner-authoritative durable preferences/costs that are likely to improve future decisions.

## 4. Planning/progress/execution writes

Topic/Subtopic Plans and Weekly/Daily plans are derived routing/execution artifacts, not capability state. They MAY be created/updated from Goal, Curriculum, relevant Knowledge State/Progress, and execution context without an evidence record.

Replan only when the route/scope materially changes. Ordinary Progress movement does not require rewriting the Plan.

During ordinary learning, prefer a meaningful session boundary as the aggregation point for execution/progress persistence when safe. Do not wait for session close if context durability is at risk or an immediate goal change, important blocker, or high-value evidence needs persistence.

An `execution_session` is an execution fact, not Evidence. Do not infer capability state merely because an execution objective/session was completed.

## 5. Evidence and Knowledge State write order

When evidence and derived Knowledge State are both persisted:

1. create the immutable evidence record first;
2. fresh-fetch the mutable learner Knowledge artifact and current blob SHA;
3. semantically integrate all relevant evidence/current state;
4. write the Knowledge State;
5. update higher-level learner model/calibration only if separately justified.

Evidence SHOULD be create-only. Reinterpretation should normally create a new evidence event rather than rewrite history.

For competing updates to the same capability, fresh-fetch and reintegrate semantically. Do not use blind retries or last-write-wins.

## 6. Mutable-write concurrency

Before modifying any existing mutable file, fresh-fetch its current contents and blob SHA. For a derived decision, also validate the semantic revisions of upstream artifacts that were genuinely material to that decision.

If those inputs changed, abort the stale derived write, re-read, and semantically reconcile. Do not bind Plans to every ordinary Knowledge/Progress revision when those changes do not alter the route.

Different independent fields MAY be semantically merged. User-authoritative fields preserve the latest explicit learner update.

Project/session collaboration authority is external to ordinary Learning OS Core and does not replace product write safety. When the current execution environment or target repository requires an authority check, satisfy that boundary before the product-state write; the target mutable file still requires the fresh-fetch/semantic-merge/blob-SHA flow above. Learning Branch generation guards remain separately governed by the learning continuity protocol.

## 7. Sparse materialization

Do not create empty artifacts merely for schema completeness. In particular, absent learner Knowledge, Topic Deferred, learner Execution, coordination, Global Hub, handoff, or evidence trees may legitimately mean that no such durable state has yet materialized.

When materializing a Subtopic, prepare its Plan and Progress first and create `definition.yaml` last. The definition file is the materialization commit marker. Runtime MUST NOT discover a Subtopic merely by scanning orphan draft files.

## 8. Coordination persistence

A Branch Report is a rebuildable projection; a Coordination Event is an immutable routing/planning delta. Neither is Evidence.

Reports/events do not require an atomic multi-file transaction. Recovery order favors canonical local state over projection/notification.

If a reconciliation changes canonical Plan/Progress/Weekly decisions, write and verify those decisions before advancing any Hub cursor/runtime optimization. Use at-least-once/idempotent reconciliation rather than exactly-once assumptions.

## 9. Learning handoff and external project collaboration

Learning handoffs for materialized learning Branch conversation generations follow `continuity-policy.md` and live with the relevant Topic/Subtopic lineage. They are supplemental product continuity, not canonical learner state or Evidence.

Project/design Agent collaboration is outside ordinary Learning OS Core. Core does not define a project-design generation, claim/takeover transaction, packet publication protocol, or session writer credential. A target repository or current execution environment may maintain its own recovery/control artifacts, but those rules neither become learner/runtime state nor bypass the product fresh-read, deployment, learning-lineage, or target-CAS guards defined here.

Do not archive full transcripts by default and do not persist private chain-of-thought in product state.

## 10. Visibility

Normal learning chats:

- `P0`: no persistence log;
- `P1`: normally no learner-facing log;
- capability `P2`: compact semantic state change only when useful;
- ordinary goal/plan/execution/coordination maintenance: mention only when materially relevant or requested.

Low-level GitHub details belong in Learning OS/audit contexts unless synchronization fails.

If an important write fails, MUST disclose that durable state was not synchronized. Do not claim success.

## 11. Historical V0.2 learner-project artifacts

Canonical `main` no longer supports V0.2 Domain Goal/Plan/State/Deferred as a runtime learner-project fallback. Learner-project state belongs under `topics/<topic>/`; `domains/<domain>/curriculum.yaml` remains the reusable curriculum home.

Historical V0.2 learner-project artifacts may still appear in Git history or dated handoff documentation for audit/recovery archaeology. They MUST NOT be recreated or dual-written as current learner state.
