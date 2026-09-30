---
protocol: execution-policy
version: "0.4"
schema_compatibility: "0.3"
---

# Execution Policy

Execution state describes planned and actual learning work. It is distinct from knowledge state, evidence, and Topic progress.

## 1. Learner execution defaults

`learner/execution.yaml` is lazy-materialized. It stores learner-global/default execution information only when such information actually exists, including total weekly budget, default availability/session pattern, explicit scheduling preferences, and optional re-engagement configuration.

Project timezone remains the fallback unless the learner has an explicit execution override. Topic-specific desired hours belong to Topic Goal and MUST NOT be silently promoted to learner-global budget.

Availability answers "how much time is available". Duration calibration answers "how long work tends to take". They MUST NOT overwrite each other automatically.

## 2. Weekly execution

Weekly execution is cross-Topic planning and is primarily owned by a Global-Hub-class operation, or a delegated Topic-Hub-class operation when only one Topic needs planning.

### 2.1 Weekly activation

Weekly planning is sparse, but it SHOULD become active rather than remain indefinitely implicit when the learner has already supplied an explicit weekly resource signal.

At the first substantive learning/planning interaction in a new calendar week, if no current Weekly window exists and either a learner-global weekly budget or an active Topic `desired_hours_per_week` is known, a Hub-class operation SHOULD propose and normally materialize the current Weekly window unless the learner has opted out of weekly planning.

If that activation is missed, interrupted, or cannot be completed, the candidate MUST remain pending: on later substantive bootstrap/resume/planning turns in the same calendar week, while no current Weekly window exists and the learner has not explicitly deferred or opted out, the system SHOULD retry the activation path rather than treating the one-time first-turn opportunity as consumed. A missed activation MUST NOT make the rest of the week permanently unplanned.

- With one active Topic, a Topic-Hub-class operation MAY perform this weekly planning without materializing a Global Hub.
- With multiple active Topics or any cross-Topic allocation decision, use Global-Hub-class reconciliation.
- If the available budget has ambiguous scope (for example, a Topic-specific desired-hours value is being considered as a possible learner-global total), resolve that planning gap before committing cross-Topic allocations; do not silently promote Topic hours into a global budget.
- Without a weekly resource signal or a concrete execution-planning need, the change of calendar week alone MUST NOT create an empty Weekly artifact.
- A safe short learning step MAY continue while Weekly planning is being proposed or repaired; planning maintenance should not unnecessarily block learning flow.

A weekly window SHOULD distinguish:

- baseline scope: the plan committed at the start of the execution window;
- current scope: the plan after legitimate replanning;
- scope changes with reasons;
- Topic allocations and optional reserve;
- closing dispositions for unresolved outcomes.

Weekly planned completion is derived from current outcome statuses. Do not persist a competing A/B counter as an independent truth.

Open-window `current_outcomes` are a planning/execution projection, not the source of truth for the latest Topic/Subtopic Progress. Because Study Branches SHOULD NOT continually mutate the shared Weekly file, the projection MAY legitimately lag newer canonical Progress. When `current_outcomes` are materialized for an open window, the Weekly artifact SHOULD record minimal projection provenance: when the projection was observed, the canonical source revision(s) used, and whether reconciliation is performed at read time or by an explicit Weekly reconciliation. A reader presenting latest learner Progress MUST reconcile against the referenced canonical Progress rather than treating a stale Weekly projection as newer truth.

Unfinished work is not learner debt. At reconciliation, unresolved outcomes are reconsidered as `carry_forward`, `reschedule`, `defer`, or `drop`. Carry-forward is a closing disposition; the next window gets a new execution item with origin metadata.

Study Branches SHOULD NOT continually mutate shared Weekly files to accumulate time or micro-progress. Weekly actuals are derived from execution records and may be frozen as a summary when the window closes.

## 3. Daily execution

Daily planning is Topic-local, e.g. `topics/<topic>/execution/daily/<date>.yaml`, so different Topics do not contend for one shared daily file.

A Daily baseline MAY be adjusted while still draft. Once meaningful execution begins, its baseline denominator is normally frozen. When `baseline_locked: true`, ordinary generic replacement MUST keep it locked and preserve the membership of `baseline_objectives` by stable objective ID; status/details of those same objectives may advance, while adding/removing/replacing baseline objective IDs requires an owning planning transition. Unexpected prerequisite repair or detours MUST NOT silently enlarge that baseline; track them separately as temporary work.

Current explicit learner overrides take precedence over stale daily agenda. A one-day override does not rewrite long-term defaults.

When baseline reaches B/B, the learner controls whether to stop or continue. Optional extension modes are:

- `advance`
- `deepen`
- `reinforce`
- `explore`

Extension work does not increase the original Daily baseline denominator, but genuine progress/evidence produced during extension remains fully valid.

## 4. Execution sessions

Actual learning execution SHOULD be recorded, when persistently useful, as Branch-created immutable session records under `topics/<topic>/execution/sessions/`.

An execution session is an execution fact, not learning evidence. It MAY record branch, related daily window/objectives, meaningful-learning flag, optional duration, and work disposition.

Do not fabricate timing precision. `actual_minutes` may be unknown; start/end timestamps are optional unless actually observed.

Sessions avoid high-frequency multi-writer updates to shared Weekly/Daily counters. Evidence may reference a session as provenance; the session does not need to duplicate evidence history.

## 5. Persistence cadence

During ordinary teaching, transient execution state MAY remain in conversation context. A meaningful session boundary is the default aggregation point for deciding whether to persist:

- an execution session;
- qualifying evidence;
- meaningful Subtopic Progress changes;
- coordination projections/events;
- re-engagement timestamp/reset.

Do not wait for session close when durability is materially at risk or when there is an immediate learner-authoritative goal change, important blocker, high-value evidence, or impending handoff/context loss.

## 6. Calibration

Execution history can inform planning calibration, but calibration MUST remain separate from capability state and learner-authoritative availability.

Distinguish at least:

- scope calibration: whether planned amount of work fits an execution window;
- duration calibration: how long comparable work tends to take.

Repeated planning mismatch should first adjust the planner or prompt a scoped user confirmation; it MUST NOT be converted directly into a broad learner trait.
