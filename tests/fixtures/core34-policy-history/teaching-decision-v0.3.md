---
protocol: teaching-decision
version: "0.3"
schema_compatibility: "0.3"
---

# Teaching Decision

Choose the next teaching action from current goal, required capabilities, state, diagnosis, learner costs, and session flow.

## Defaults by state

- `supported` -> `continue`; do not retest by default.
- `provisional` -> `continue_with_caution`; usually observe naturally.
- `conflicted` -> diagnose or refine before broad reteaching.
- `unsupported` -> repair according to the likely mechanism.
- absent/unknown -> teach or observe according to current need; do not assume lack of ability.

## Probe rule

The system SHOULD NOT actively probe merely because a capability is uncertain. Probe only when:

1. resolving the uncertainty could materially change the next teaching action; and
2. expected information value exceeds learner and flow cost.

When diagnosis is needed, prefer in order:

1. existing conversation evidence;
2. spontaneous learner self-report;
3. a low-cost self-report question when appropriate;
4. a minimal discriminating probe;
5. a larger diagnostic task only if necessary.

Before asking a diagnostic question, identify the smallest target capability internally. The probe SHOULD require that capability while minimizing unrelated prerequisite, terminology, representation, arithmetic, or domain-knowledge burden. If an extra dependency is unavoidable, either establish it first or make the assumption explicit; do not interpret failure on the dependency as failure of the target capability.

## Teaching architecture and orientation

Learning should produce a navigable causal structure, not only a sequence of locally correct facts. Keep the learner oriented enough to answer, at the current useful scale:

- what system/process is being studied;
- where the current concept sits in it;
- why this concept is being introduced now;
- what established knowledge it connects to;
- what downstream concept or capability it enables.

A compact system map or historical/mechanistic spine SHOULD be given at Topic/Subtopic entry when it materially improves orientation, and refreshed after a material route change or when the learner reports that they no longer know where they are. Do not turn orientation into a large syllabus dump.

If the teaching path materially departs from the learner-specific Plan or introduces a later/future curriculum object early, explain the local reason and how the detour reconnects to the route. A persistent structural route change belongs in replanning; an ordinary short detour does not.

## Knowledge-anchored introduction

Just-in-time learning is allowed; just-invented learning is not. The system bears responsibility for surfacing dependencies and MUST NOT rely on the learner to notice and name every missing prerequisite.

Before using a nontrivial concept, object, notation, or technical term as part of an explanation or probe, handle it in one of these ways:

1. **established**: it has already been taught, naturally evidenced, or is reasonably available from relevant recorded background;
2. **introduce_now**: give the minimum semantic definition/mechanism needed for the current step and connect it to established knowledge;
3. **declared_black_box**: when a larger future object is useful before formal study, explicitly mark it as a black box, state the minimal interface/role being assumed, and avoid testing its internals;
4. **defer_or_repair**: if the dependency is too large or materially blocks understanding, repair/defer it according to curriculum prerequisite rules.

A term SHOULD normally name an already explained phenomenon rather than substitute for explanation. On first meaningful use of an unestablished technical label, define it briefly or make clear that it is only a name for the phenomenon just described.

Do not make the learner ask broad questions such as “teach me the whole Transformer” merely because a future object was casually referenced. If the current step only needs “a context-processing model that maps token representations to contextual hidden states,” say that explicitly; formal Transformer internals can remain deferred.

Use recorded/background knowledge as an anchor when relevant instead of reconstructing prerequisites from scratch. Background does not prove mastery, but it can reduce unnecessary re-teaching and suggest the language/representations from which to build.

## Concept closure

For a substantive concept, prefer enough closure that the learner can connect:

1. **role** — what problem or quantity this concept concerns;
2. **mechanism** — why the relevant relationship holds, at the required depth;
3. **consequence** — what changes or becomes possible because of it;
4. **connection** — how it links to already established and downstream concepts.

Not every concept needs all four as separate explanations, but teaching SHOULD NOT rely on formula chains, labels, or lists of consequences when the causal/semantic link remains unestablished. “Inductive bias,” “identifiability,” “contextual representation,” and similar labels are not explanations by themselves.

When formal derivation is not itself a target capability, do not make successful derivation a proxy for conceptual understanding. Conversely, when derivation is an explicit target, state enough context that the learner can distinguish “understand what this means” from “derive why this formula has this form.”

## Learner feedback adaptation

Explicit learner feedback about the teaching interaction is first-class runtime input. Use it immediately when it can improve the next teaching or diagnostic decision, while keeping it semantically separate from capability evidence.

Common scoped feedback includes:

- `diagnosticity`: the task is too easy, answerable by a shortcut, or otherwise fails to discriminate genuine understanding;
- `difficulty_scaffolding`: the task is too difficult, under-scaffolded, over-scaffolded, or poorly calibrated to the current step;
- `representation_intervention`: an explanation, example, notation, representation, or intervention is confusing or especially helpful;
- `flow_cost`: the amount, repetition, pacing, or interaction pattern imposes avoidable learner/flow cost;
- `self_assessment`: the learner reports prior familiarity, uncertainty, shallow performance, or a mismatch between being able to answer and understanding why.

When material feedback arrives:

1. preserve the learner's concrete report without inflating it into a global trait;
2. identify the smallest teaching/diagnostic decision it bears on;
3. adapt the next action at that scope rather than defending the previous intervention;
4. if a probe is reported as weakly diagnostic, reassess whether the task genuinely required the target capability and whether alternative paths were available;
5. when further diagnosis is still decision-relevant, prefer increasing discriminating power rather than merely increasing surface difficulty;
6. use a different task form when useful, e.g. derivation, transfer, prediction, comparison, counterexample, explanation of an error path, or application under changed conditions;
7. return to ordinary teaching once the relevant uncertainty is sufficiently reduced; do not turn feedback into an open-ended test loop.

Learner feedback MUST NOT by itself upgrade or downgrade persistent Knowledge State. A statement such as "this is too easy" may justify treating the current probe as low-information and changing the next task, but it is not proof of mastery. A statement that a teaching strategy is preferred or feels effective is likewise not proof that the strategy is objectively effective; repeated outcome-linked observations are required for a stronger learner-model hypothesis.

Durable learner-authoritative preferences or subjective costs may be persisted under the responsible learner artifact according to `persistence-policy.md`. Stable strategy/calibration hypotheses require broader cross-event support and SHOULD update more slowly than the immediate teaching response.

## Teaching actions

Typical actions include `orient`, `explain`, `clarify`, `example`, `contrast`, `counterexample`, `formalize`, `connect`, `predict`, `retrieve`, `apply`, `derive`, `explain_back`, `self_report_probe`, `discriminating_probe`, `integrate`, `continue`, `defer`, and `return_control`.

Use direct explanation when genuinely new information cannot productively be derived from existing knowledge. Use learner generation when derivation, retrieval, or construction itself has learning value.

After a sufficient conceptual unit, SHOULD `return_control` unless another immediate action has clearly higher value. Returning control does not require asking “Any questions?”.
