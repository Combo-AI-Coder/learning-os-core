---
protocol: schema
version: "0.6"
schema_compatibility: "0.3"
---

# Schema Reference

V0.3 separates reusable knowledge structure, learner-specific learning projects, execution, evidence, coordination, conversation continuity, and physical conversation-sequence metadata.

## 1. Common document rules

Every YAML document SHOULD include `schema_version` and `document_type`. Mutable documents SHOULD include `updated_at`.

A semantic `revision` counter is REQUIRED only when another artifact depends on that mutable artifact's semantic version or when coordination/concurrency benefits from an explicit revision. Core revisioned artifacts are:

- `learner_execution`
- `learner_knowledge`
- `topic_goal`
- `topic_plan`
- `topic_progress`
- `subtopic_plan`
- `subtopic_progress`
- `weekly_execution`
- `daily_execution`
- `branch_registry`
- `branch_runtime`
- `branch_report`
- `hub_runtime`
- `topic_report`

Background/model/calibration/cost files do not require migration-only revision counters. Reusable/local curriculum documents use `curriculum_version`; split `curriculum_extension` overlays use the positive integer `extension_revision` semantic counter. Immutable evidence/session/event/handoff artifacts do not use revision counters. `conversation_sequence_registry` is mutable allocation metadata and uses Git blob SHA CAS rather than a semantic `revision` counter. Normal reservations advance monotonically within their scope; the narrowly authorized proven-orphan suffix repair defined in `conversation-naming-policy.md` is the only permitted production-counter decrease.

`updated_at` is not a semantic revision. Git blob SHA is a concurrency/write guard and MUST NOT be stored as canonical semantic version state. A Project Handoff MAY persist a commit SHA as a canonical-settlement snapshot anchor and a blob SHA as immutable packet identity; these are provenance/integrity anchors for that transaction, not semantic revisions of the referenced artifacts.

## 2. Document types

V0.3 canonical document types:

- `project_config`
- `conversation_sequence_registry`
- `lineage_control`
- `learner_background`
- `learner_model`
- `learner_calibration`
- `learner_costs`
- `learner_execution`
- `learner_knowledge`
- `curriculum`
- `topic_goal`
- `topic_plan`
- `topic_progress`
- `topic_deferred`
- `subtopic_definition`
- `subtopic_plan`
- `subtopic_progress`
- `weekly_execution`
- `daily_execution`
- `execution_session`
- `branch_registry`
- `branch_runtime`
- `branch_report`
- `coordination_event`
- `hub_runtime`
- `topic_report`
- `learning_handoff`
- `evidence`

Historical V0.2 learner-project types `domain_goal`, `domain_plan`, `domain_state`, and `domain_deferred` may remain in Git history or historical handoff material only. Current canonical runtime does not bootstrap learner-project state from those document types or storage paths.

### 2.1 V0.4 split Instance canonical storage registry

This section is the complete physical/canonical-home registry for YAML documents that are allowed to be canonical in a V0.4 **split Instance**. It is independent of any legacy monolithic storage registry. For legacy learner/runtime artifacts that remain Instance-owned under V0.4, split migration preserves the existing physical storage family unless this table explicitly defines a V0.4-only replacement or addition. This settlement therefore makes the split storage contract explicit; it does not redesign learner-state semantics.

The registry contains 28 allowed split Instance `document_type` values and 29 path-family rows because `learning_handoff` has two authorized families.

| `document_type` | canonical split Instance storage family | settlement basis |
| --- | --- | --- |
| `instance_config` | `config/instance.yaml` | V0.4 split-only contract |
| `curriculum_extension` | `curriculum/extensions/<extension-file>.yaml` | V0.4 split-only additive overlay |
| `curriculum` | `curriculum/local/<domain>/curriculum.yaml` | V0.4 Instance-local Domain curriculum; Core reusable curricula remain under Core `domains/<domain>/curriculum.yaml` |
| `conversation_sequence_registry` | `runtime/ui/conversation-sequences.yaml` | preserved Instance-owned runtime metadata family |
| `learner_background` | `learner/background.yaml` | preserved Instance-owned learner family |
| `learner_model` | `learner/model.yaml` | preserved Instance-owned learner family |
| `learner_calibration` | `learner/calibration.yaml` | preserved Instance-owned learner family |
| `learner_costs` | `learner/costs.yaml` | preserved Instance-owned learner family |
| `learner_execution` | `learner/execution.yaml` | preserved Instance-owned learner family |
| `learner_knowledge` | `learner/knowledge/<domain>.yaml` | preserved Instance-owned learner family |
| `topic_goal` | `topics/<topic>/goal.yaml` | preserved Instance-owned Topic family |
| `topic_plan` | `topics/<topic>/plan.yaml` | preserved Instance-owned Topic family |
| `topic_progress` | `topics/<topic>/progress.yaml` | preserved Instance-owned Topic family |
| `topic_deferred` | `topics/<topic>/deferred.yaml` | preserved Instance-owned Topic family |
| `subtopic_definition` | `topics/<topic>/subtopics/<subtopic>/definition.yaml` | preserved Instance-owned Subtopic family |
| `subtopic_plan` | `topics/<topic>/subtopics/<subtopic>/plan.yaml` | preserved Instance-owned Subtopic family |
| `subtopic_progress` | `topics/<topic>/subtopics/<subtopic>/progress.yaml` | preserved Instance-owned Subtopic family |
| `weekly_execution` | `execution/weekly/<window-id>.yaml` | preserved Instance-owned execution family |
| `daily_execution` | `topics/<topic>/execution/daily/<date>.yaml` | preserved Instance-owned execution family |
| `execution_session` | `topics/<topic>/execution/sessions/<session-id>.yaml` | preserved Instance-owned immutable execution family |
| `branch_registry` | `topics/<topic>/coordination/branches.yaml` | preserved Instance-owned coordination family |
| `branch_runtime` | `topics/<topic>/coordination/branches/<branch>/runtime.yaml` | preserved Instance-owned coordination family |
| `branch_report` | `topics/<topic>/coordination/branches/<branch>/report.yaml` | preserved Instance-owned projection family |
| `coordination_event` | `topics/<topic>/coordination/events/<event-id>.yaml` | preserved Instance-owned immutable coordination family |
| `hub_runtime` | `coordination/hub/runtime.yaml` | preserved global Hub runtime/projection family, explicitly authorized in split Instance |
| `topic_report` | `topics/<topic>/coordination/topic-report.yaml` | preserved Instance-owned Topic-to-Hub projection family |
| `learning_handoff` | `topics/<topic>/handoffs/<lineage-id>/C<from-sequence>-to-C<to-sequence>.yaml` | preserved Topic-level learning Branch continuity family |
| `learning_handoff` | `topics/<topic>/subtopics/<subtopic>/handoffs/<lineage-id>/C<from-sequence>-to-C<to-sequence>.yaml` | preserved Subtopic-bound learning Branch continuity family |
| `evidence` | `evidence/<evidence-id>.yaml` | preserved flat immutable Evidence family |

Every YAML document collected as split Instance canonical state **MUST** match exactly one registered storage family above before document semantics can be accepted:

- `match_count == 0` => invalid and MUST fail closed;
- `match_count > 1` => registry ambiguity and MUST fail closed;
- `match_count == 1` and declared `document_type` differs from the registered type => invalid and MUST fail closed.

A known/allowed `document_type` does not authorize an arbitrary path. In particular, a validator MUST NOT interpret “no registered family matched” as permission to continue silently.

All placeholder forms in this table use POSIX repository-relative syntax. Each of `<topic>`, `<subtopic>`, `<branch>`, `<lineage-id>`, `<event-id>`, `<evidence-id>`, `<session-id>`, `<date>`, `<window-id>`, `<extension-file>`, and `<domain>` represents exactly one non-empty repository path segment. A placeholder value MUST NOT contain `/`, MUST NOT be `.` or `..`, and MUST NOT create an additional storage level. The hardened filesystem-containment resolver remains a separate validation concern; these rules define canonical family syntax.

The two Learning Handoff families are both canonical in split Instance. Their `C<from-sequence>-to-C<to-sequence>.yaml` filename is canonical **navigation** identity for the physical handoff artifact, not lineage-generation authority. Generation/transition authority continues to come from the handoff document's `lineage_id`, `from_generation`, and `to_generation`; validators and runtimes MUST NOT infer generation numbers from the physical `Cxx` filename. This storage settlement does not change Learning Handoff transition-identity semantics.

In split Instance, top-level `coordination/` is an authorized Instance storage root **only for registered split Instance coordination families**. The currently registered top-level family is exactly `coordination/hub/runtime.yaml` for `hub_runtime`. This authorization does not create a general arbitrary `coordination/**` namespace; any other YAML path under top-level `coordination/` has zero registry matches and MUST fail closed unless a later schema revision explicitly registers another family.

The Evidence family is flat: `evidence/<evidence-id>.yaml`; nested Evidence directories are not an authorized storage family. Existing Evidence identity and immutability semantics continue unchanged. This settlement does not create a new semantic Evidence identity rule; where an existing identity check binds filename stem to document `id`, that invariant continues to apply. Ordinary runtime MUST NOT rename or relocate immutable Evidence merely to reorganize storage.

Sparse or currently unmaterialized document types still retain their canonical storage contract. Absence of `daily_execution`, `branch_report`, `coordination_event`, `hub_runtime`, `topic_report`, a handoff family, or any other lazy type does not remove or weaken its registry entry.

Split Instance storage registration is a physical/canonical-home contract and does **not** independently define accepted Instance state `schema_version` values. Storage-family authorization and schema-version authorization are separate checks. V0.4 Instance state-version compatibility remains governed by the deployed Core manifest and split compatibility contract (`manifest.supported_instance_state_schema_versions`); this section does not modify that field or the state-version axis.

The split Instance registry does **not** authorize `project_config`, `lineage_control`, `deployment_binding`, `migration_transaction`, or `core_config` as Instance canonical state. Core protocols/scripts/tests/reusable `domains/**` remain Core-plane material; the Runtime-Control deployment contract remains in Runtime-Control; private project-design lineage remains in Private Control.

Repository operational metadata is outside this canonical YAML state registry. `.github/workflows/*.yml` and `.github/workflows/*.yaml` are operational metadata and do not need to match a split canonical state family; split Instance canonical-document collection SHOULD continue to exclude `.github/**`. `README.md`, `.gitignore`, and `LICENSE` are likewise not canonical YAML state families.

Current `InstanceValidator` path dispatch does not yet conform to this complete registry. That implementation debt is intentional at this specification-only settlement boundary and requires a separate implementation package; the normative registry above MUST NOT be weakened merely to match current code.

## 3. Core ontology

- **Domain**: reusable objective knowledge namespace. A Domain may own `curriculum.yaml`.
- **Topic**: learner-specific high-level learning project/goal. A Topic may use one or more Domains.
- **Subtopic**: coherent learner-specific unit inside one Topic, materialized only when useful.
- **Role**: orthogonal chat function (`hub`, `main`, `practice`, `deep_dive`).

A curriculum node is not a Subtopic. A Subtopic is not a chat. Topic and Domain MAY share the same slug because they are different namespaces.

## 4. Common enums

Confidence: `low`, `medium`, `high`.

Authority: `user_authoritative`, `system_inferred`, `co_diagnosed`.

Capability state: `provisional`, `supported`, `conflicted`, `unsupported`. Missing capability state means unknown.

Evidence direction: `support`, `challenge`, `neutral`, `deferred`.

Diagnosticity / novelty: `low`, `medium`, `high`.

Teaching readiness is a runtime decision, normally `continue`, `continue_with_caution`, `diagnose`, `repair`, or `defer`; it is not persistent learner Knowledge State.

Plan status: `awaiting_intake`, `provisional`, `active`, `paused`.

Planned-item status: `planned`, `in_progress`, `completed`, `blocked`, `deferred`, `dropped`.

Topic lifecycle: `active`, `paused`, `completed`, `cancelled`.

Subtopic lifecycle: `active`, `paused`, `completed`, `merged`, `split`, `discarded`.

Subtopic kind: `standard`, `prerequisite_support`, `integration`.

Branch role: `hub`, `main`, `practice`, `deep_dive`.

Branch lifecycle: `active`, `idle`, `retired`.

Conversation-generation lifecycle: `active`, `idle`, `handoff_pending`, `archived`, `deprecated`.

Hub attention: `none`, `review`, `required`.

Extension mode: `advance`, `deepen`, `reinforce`, `explore`.

Deferred status: `active`, `reactivated`, `resolved`, `dropped`.

Deferred kind: `depth_defer`, `question`, `prerequisite_debt`.

Watch kind: `prerequisite`, `node`, `capability`.

Background kind: `course`, `education`, `project`, `work`, `tool`, `topic_exposure`, `other`.

Curriculum node kind: `concept`, `procedure`, `theorem`, `skill`, `representation`, `application`, `integration`.

Curriculum capability expectation: `expected`, `useful`, `not_expected`.

Curriculum edge relation: `requires`, `supports`, `extends`, `contrasts_with`, `applies_to`, `integrates_with`, `generalizes`, `motivates`, `reinforces`.

Curriculum edge strength: `weak`, `medium`, `strong`.

## 5. IDs and references

Use stable identifiers. IDs represent identity, not display order, status, version, or UI title.

Recommended prefixes:

- round: `rnd_<timestamp>_<suffix>`
- new evidence: `evi_<timestamp>_<suffix>`; legacy `evt_*` evidence remains valid
- coordination event: `coord_<timestamp>_<suffix>`
- execution session: `ses_<timestamp>_<suffix>`
- project handoff: `hnd_<timestamp>_<suffix>`
- hypothesis: `hyp_<semantic-slug>_<suffix>`
- deferred: `def_<semantic-slug>_<suffix>`
- intervention: `int_<timestamp>_<suffix>`
- background: `bg_<semantic-slug>_<suffix>`

Reference invariants:

1. Different entity types MAY share a slug.
2. Cross-scope references MUST be typed/qualified; parent-local references MAY use local IDs.
3. Capability identity is `domain + concept/node + capability`.
4. Topic/Subtopic restructuring MUST NOT rewrite historical evidence identity/context.
5. Branch ID, lineage ID, and generation are distinct.
6. Execution objectives are provenance/execution identities, not capability identities.
7. Historical immutable refs retain old IDs; migration uses aliases/replacement metadata.
8. Display names MUST NOT be canonical references.
9. Current mutable references SHOULD resolve to a current canonical entity or explicit successor.
10. Storage path is not itself the semantic ID.
11. For a materialized learning Branch lineage, reading `active_generation` does not grant a fresh conversation that product-writing generation; acquisition follows the learning continuity protocol.
12. A physical conversation `Cxx` sequence is independent of learning Branch generation and never grants learner/runtime writer authority.

Curriculum node IDs SHOULD remain semantic and stable, e.g. `probability.random_variable.function_view`. Curriculum edge IDs MUST be stable and unique within a curriculum, normally `edge_<semantic-slug>`.

## 6. Learner background

`learner/background.yaml` stores learner-authoritative history/exposure, not capability state.

```yaml
- id: bg_<semantic-slug>_<suffix>
  kind: course | education | project | work | tool | topic_exposure | other
  subject: <domain-or-topic>
  description: <learner-reported history>
  status: <optional learner-described status>
  authority: user_authoritative
  reported_at: <timestamp>
```

Background MUST NOT automatically create `supported` capability state. It MAY influence provisional starting assumptions and natural observation choices.

## 7. Learner execution

`learner/execution.yaml` is lazy-materialized. Missing file means no learner-specific global/default execution state is persisted; project timezone/config remains the fallback.

It MAY store:

```yaml
schema_version: "0.3"
document_type: learner_execution
revision: 1
updated_at: <timestamp>

weekly_budget:
  hours: <number>
  authority: user_authoritative
  reported_at: <timestamp>

availability:
  mode: <flexible-or-other>
  default_session_minutes:
    value: <number-or-null>
    authority: user_authoritative

preferences: {}

reengagement:
  enabled: <bool>
  inactivity_threshold_days: <number-or-null>

engagement:
  last_meaningful_learning_at: <timestamp-or-null>
```

`weekly_budget` here means learner-global total budget. Topic-specific desired hours belong to Topic Goal and MUST NOT be silently promoted to global budget. Inferred duration/scope calibration belongs in learner calibration, not learner execution defaults.

`last_meaningful_learning_at` is a recoverable convenience projection; execution records are stronger factual sources.

## 8. Learner knowledge

Persistent capability judgments live under `learner/knowledge/<domain>.yaml` and are learner-global for a semantically shared capability claim.

```yaml
schema_version: "0.3"
document_type: learner_knowledge
revision: 1
updated_at: <timestamp>

domain: <domain-id>
concepts:
  <concept-or-node-id>:
    capabilities:
      <capability-name>:
        state: provisional | supported | conflicted | unsupported
        confidence: low | medium | high
        updated_at: <timestamp>
        evidence_refs:
          support: []
          challenge: []
        basis_summary: <optional short semantic summary>
```

Unknown capability claims MUST remain absent rather than pre-populated. Evidence refs SHOULD remain representative/decision-relevant rather than an unbounded duplicate history.

No mastery percentage, automatic forgetting probability, or persistent teaching-readiness field is canonical in V0.3 Core.

## 9. Topic Goal

Canonical path: `topics/<topic>/goal.yaml`.

```yaml
schema_version: "0.3"
document_type: topic_goal
revision: 1
updated_at: <timestamp>

topic:
  id: <topic-id>
  title: <display title>

goal:
  purpose:
    value: <text-or-null>
    authority: user_authoritative
  horizon:
    type: open_ended | deadline | other | null
    deadline: <date-or-null>
  depth_profile: {}
  success_criteria: []
  constraints: {}
  preferences:
    include: []
    avoid: []
  resource_preferences:
    desired_hours_per_week:
      value: <number-or-null>
      scope: topic
      authority: user_authoritative | null
```

Topic Goal stores learner-specific goal facts, not lifecycle, current progress, cross-Topic relative priority, or execution allocation.

## 10. Topic Plan

Canonical path: `topics/<topic>/plan.yaml`.

```yaml
schema_version: "0.3"
document_type: topic_plan
revision: 1
updated_at: <timestamp>

topic: <topic-id>

plan:
  status: awaiting_intake | provisional | active | paused
  intake:
    status: incomplete | sufficient
    gaps:
      - id: <gap-id>
        kind: blocking | planning
        trigger: <optional semantic trigger>
        question: <optional future question>
  based_on:
    goal_revision: <revision>
    curricula:
      - domain: <domain-id>
        curriculum_version: <version>
  milestones:
    - id: <topic-milestone-id>
      title: <text>
      objective: <text>
      exit_criteria: []
      completion_basis: <optional small explicit object>
      suggested_subtopic: <subtopic-id-or-null>
  suggested_sequence: []
  replanning_triggers: []
```

Only blocking/planning gaps belong in Plan. Observational gaps are discovered during learning. A Topic Plan does not store runtime active Subtopic/current pointer. Do not bind it to every ordinary Progress/Knowledge revision; create a new plan revision only for a material replan.

Topic milestone completion basis SHOULD be simple text/refs or a small explicit object. Do not create a general rule language.

## 11. Topic Progress

Canonical path: `topics/<topic>/progress.yaml`.

```yaml
schema_version: "0.3"
document_type: topic_progress
revision: 1
updated_at: <timestamp>

topic: <topic-id>
plan_revision: <revision>
lifecycle: active | paused | completed | cancelled

milestones:
  <milestone-id>:
    status: planned | in_progress | completed | blocked | deferred | dropped

active_subtopics: []
blockers: []
watch: []
resume:
  primary_subtopic: <subtopic-id-or-null>
```

Topic Progress is route progress, not capability state and not weekly execution participation/allocation.

`watch` is a small Topic-level future-observation queue. A watch item is not evidence, a blocker, prerequisite debt, or proof of inability. Use it for downstream natural-observation hints that do not belong only to the current Subtopic.

A/B progress is derived from current planned-item statuses. Do not store an independent canonical A/B counter.

## 12. Subtopic definition/materialization

Canonical path: `topics/<topic>/subtopics/<subtopic>/definition.yaml`.

```yaml
schema_version: "0.3"
document_type: subtopic_definition
updated_at: <timestamp>

subtopic:
  id: <subtopic-id>
  topic: <topic-id>
  title: <display title>
  kind: standard | prerequisite_support | integration
  lifecycle: active | paused | completed | merged | split | discarded
  source_domains: []
  created_from: <optional typed ref>
  replaced_by: []
```

`source_domains` identifies knowledge sources only; it does not mean every listed Domain is a prerequisite.

A Subtopic is considered materialized only after its definition exists. Prepare Plan/Progress first and create definition last as the commit marker. Candidate future Subtopics MAY exist only in Topic Plan and need no files.

## 13. Subtopic Plan

Canonical path: `topics/<topic>/subtopics/<subtopic>/plan.yaml`.

```yaml
schema_version: "0.3"
document_type: subtopic_plan
revision: 1
updated_at: <timestamp>

topic: <topic-id>
subtopic: <subtopic-id>

plan:
  status: provisional | active | paused
  based_on:
    topic_plan_revision: <revision>
    curricula: []
  objective: <text>
  milestones:
    - id: <milestone-id>
      title: <text>
      exit_criteria: []
      curriculum_refs: []
  suggested_sequence: []
```

Exit criteria define what counts as completing the learner-specific milestone. The Plan does not store current runtime position.

## 14. Subtopic Progress

Canonical path: `topics/<topic>/subtopics/<subtopic>/progress.yaml`.

```yaml
schema_version: "0.3"
document_type: subtopic_progress
revision: 1
updated_at: <timestamp>

topic: <topic-id>
subtopic: <subtopic-id>
plan_revision: <revision>

milestones: {}
current:
  milestone: []
blockers: []
resume:
  return_point: <small semantic object-or-null>
  ready_next: []
watch: []
avoid_retesting: []
```

Subtopic Main is the primary writer for route position. Practice/Deep Dive MAY generate evidence/role-local execution, but MUST NOT directly move Main's canonical current position. If their results require structural route changes, escalate through coordination.

## 15. Watch object

Topic/Subtopic watch items SHOULD use typed targets:

```yaml
- kind: prerequisite | node | capability
  target:
    type: domain | curriculum_node | capability
    id: <id-if-applicable>
    domain: <domain-if-applicable>
    concept: <concept-if-applicable>
    capability: <capability-if-applicable>
  reason: <short decision-relevant text>
  priority: low | medium | high
```

A watch MUST NOT itself downgrade knowledge state or trigger automatic pretesting.

## 16. Topic Deferred

Canonical path: `topics/<topic>/deferred.yaml`, lazy-materialized. Missing file means no persisted active deferred items.

Items use stable IDs and `kind: depth_defer | question | prerequisite_debt` with `status: active | reactivated | resolved | dropped`.

Prerequisite debt is a routing/deferred item, not an `unsupported` capability claim. It SHOULD identify a dependency and future consumer/reactivation trigger where useful.

## 17. Curriculum

`domains/<domain>/curriculum.yaml` remains reusable knowledge structure in V0.3. V0.3 does not require moving it to a new directory.

Top-level fields:

- `domain.id`, `domain.title`, optional `domain.aliases`;
- `curriculum_version`;
- `capability_profiles`;
- `nodes`;
- `edges`;
- optional `tracks`, `entry_paths`;
- top-level `aliases` reserved for node-ID migration.

Each active node MUST include `title`, `kind`, and `capability_profile`. `naturally_observes` describes observation opportunities only and MUST NOT automatically update learner state.

Every edge MUST include stable `id`, `from`, `to`, `relation`, and `strength`. `active_when` may be a small explicit condition object; do not invent a general rule language.

Tracks use `members`. Entry paths are objects with `starting_nodes`. Domain nicknames belong in `domain.aliases`, not top-level node-ID aliases.

Do not silently delete referenced curriculum nodes; deprecate/alias them during knowledge-structure migration. If a capability is later extracted to a different canonical Domain, one semantic capability should eventually have one canonical knowledge identity; historical evidence context is not rewritten.

## 18. Weekly execution

Canonical cross-Topic path: `execution/weekly/<window-id>.yaml`, materialized only when weekly planning is used.

A weekly artifact MAY contain budget, Topic allocations, baseline outcomes, current outcomes, scope changes, and closing dispositions. Baseline scope is frozen for audit; current scope may change through legitimate replanning.

Weekly outcome completion MUST have an explicit enough completion basis/ref to avoid subjective "did some work" completion. Current A/B is derived from current outcome statuses.

For an open Weekly window, `current_outcomes` are a rebuildable planning/execution projection and MAY lag newer canonical Topic/Subtopic Progress. When `current_outcomes` are present, use minimal provenance such as:

```yaml
projection:
  observed_at: <timestamp>
  source_revisions:
    - ref: <canonical-progress-path>
      revision: <revision-observed>
  reconciliation: read_time | explicit_reconciliation
```

`projection.source_revisions` records the canonical snapshot used to populate the projection; it does not require Branches to update the shared Weekly artifact after every Progress change. A reader that needs latest Progress MUST reconcile against the referenced canonical Progress. Stale projection content does not override newer canonical Progress.

Branches SHOULD NOT use the Weekly file as a realtime shared counter. Actual time/progress is derived from execution records and may be summarized/frozen when the window closes.

## 19. Daily execution

Canonical path: `topics/<topic>/execution/daily/<date>.yaml`.

A Daily artifact MAY contain availability/default source/override, baseline objectives, `baseline_locked`, temporary-work planning, extension planning, and closing metadata.

The baseline MAY change while draft. Once meaningful execution begins, baseline denominator is normally frozen. Temporary prerequisite repair/detours and learner-controlled extensions MUST NOT silently change the original baseline denominator.

There is no second global canonical Daily file. Cross-Topic "today" totals are projections.

## 20. Execution session

Canonical path: `topics/<topic>/execution/sessions/<session-id>.yaml`.

Sessions are normally immutable execution facts:

```yaml
schema_version: "0.3"
document_type: execution_session
id: ses_<timestamp>_<suffix>
topic: <topic-id>
branch:
  id: <branch-id>
daily_window: <date-or-null>
meaningful_learning: <bool>
actual_minutes: <number-or-null>
work: []
extension:
  mode: advance | deepen | reinforce | explore | null
```

Do not fabricate timing precision. Execution Session is not Evidence.

## 21. Evidence V0.3

Evidence is normally immutable/create-only. Reinterpretation should create a new event rather than rewrite observation history.

```yaml
schema_version: "0.3"
document_type: evidence
id: evi_<timestamp>_<suffix>
observed_at: <timestamp>

observation:
  kind: <semantic-kind>
  summary: <what happened>

interpretation:
  direction: support | challenge | neutral | deferred
  diagnosticity: low | medium | high
  novelty: low | medium | high
  confidence: low | medium | high

targets:
  - type: capability
    domain: <domain-id>
    concept: <concept-or-node-id>
    capability: <capability-name>

context:
  topic: <topic-or-null>
  subtopic: <subtopic-or-null>
  branch: <optional typed branch ref>
  execution:
    session: <session-id-or-null>
    daily_window: <date-or-null>
    objective: <objective-id-or-null>

source:
  round_id: <round-id-or-null>
```

Observation, interpretation, capability target, and learning context are distinct. Milestone completion is not evidence by itself. One observation MAY target multiple capabilities only when it has real diagnostic value for each.

Legacy `evt_*` evidence remains immutable and valid.

## 22. Branch registry/runtime/report

Topic coordination lives under `topics/<topic>/coordination/` and is lazy-materialized.

`branches.yaml` is Topic-Hub-class owned and defines Branch identity (`id`, optional Subtopic, role, lifecycle).

`branches/<branch>/runtime.yaml` records lineage, active generation, optional pending successor, conversation lifecycle, and handoff reference. Before canonical Branch writes, the generation MUST pass the active-generation guard.

`branches/<branch>/report.yaml` is a rebuildable projection. It MAY contain consumed Plan revisions, compact progress/readiness/blocker summary, `hub_attention`, relevant execution context, and canonical source revisions. `revision` itself is the report sequence; do not add a competing `report_seq`.

## 23. Coordination events

Coordination events are immutable routing/planning inputs, not evidence.

Core families:

- `progress_transition`
- `blocker_change`
- `route_request`
- `resource_request`
- `learner_directive`
- `reconciliation_needed`

```yaml
schema_version: "0.3"
document_type: coordination_event
id: coord_<timestamp>_<suffix>
observed_at: <timestamp>
producer: <typed ref>
target_scope: topic | global
type: <family>
subtype: <type-specific value>
hub_attention:
  level: none | review | required
payload: {}
refs: {}
```

Payloads are family-specific. Events are inputs to batch semantic reconciliation, not commands.

## 24. Hub runtime and Topic report

`hub_runtime` stores only reconciliation/runtime optimization such as consumed report revisions or a lightweight cursor. It is not learner-state truth. Canonical decision writes MUST succeed before cursor advancement.

`topic_report` is a rebuildable Topic-to-Global projection and should remain compressed. Global Hub should drill into Branch detail only when necessary.

Global coordination artifacts SHOULD NOT be materialized while cross-Topic resource coordination is unnecessary.

## 25. Learning handoff

A Learning Handoff is immutable supplemental continuity under the relevant Topic/Subtopic lineage, e.g. `topics/<topic>/subtopics/<subtopic>/handoffs/<lineage>/C01-to-C02.yaml`.

It may contain identity/generation transition, canonical revision refs, teaching thread, last meaningful learner action, pending task, unresolved local questions/blockers, avoid-retesting/deferred refs, and next likely action.

It MUST NOT duplicate full Goal, Plan, Knowledge State, or evidence history. Canonical state outranks handoff.

## 26. Progress vs knowledge invariants

- Topic/Subtopic `completed` means current plan completion, not permanent mastery.
- Milestone completion MUST NOT directly create `supported` capability state.
- Capability challenge MUST NOT mechanically erase historically valid milestone completion; add repair/reverification work unless the old completion record itself was erroneous.
- Progress may consult Knowledge State as an input to exit/blocker decisions, but Knowledge State changes only through evidence integration.
- Weekly/Daily execution completion MUST NOT automatically mark Subtopic milestones complete unless the execution item explicitly uses the same semantic completion basis and the semantic decision is valid.

## 27. Sparse materialization

Do not create empty artifacts merely for structural completeness.

Examples:

- no persistent capability claims -> `learner/knowledge/<domain>.yaml` may be absent;
- no active deferred items -> Topic deferred may be absent;
- no learner-global execution defaults -> `learner/execution.yaml` may be absent;
- no materialized Branch -> no coordination directory required;
- no second/cross-Topic coordination need -> no Global Hub required;
- no handoff -> no learning handoff file;
- no qualifying evidence -> evidence tree may remain absent.

Structured absence MUST NOT be filled by guesswork.

## 28. Conversation sequence registry

Canonical path: `runtime/ui/conversation-sequences.yaml`.

The registry is durable UI/runtime metadata for physical conversation naming only. It is not learner state, Evidence, Knowledge, Progress, Execution, Coordination, handoff state, Branch runtime, or project-design lineage control.

Production and non-production scopes are distinct. A representative registry is:

```yaml
schema_version: "0.3"
document_type: conversation_sequence_registry
updated_at: <timestamp>

sequence_format:
  prefix: C
  minimum_width: 2
  allocation: monotonic_reservation
  usage: production_user_work_conversations

nonproduction_sequence_format:
  prefix: T
  minimum_width: 2
  allocation: monotonic_reservation
  usage: acceptance_test_migration_simulation

scopes:
  learning_os:
    last_allocated: <non-negative-integer>
  acceptance:learning_os:
    last_allocated: <non-negative-integer>

repair_history: []
```

Rules:

1. Normal reservation is monotonic within each scope and uses blob-SHA CAS according to `conversation-naming-policy.md`.
2. Production `Cxx` numbers correspond to real user work conversations. A number confirmed to have belonged to a real user work conversation MUST NOT be reused, even if that conversation is later abandoned or renamed.
3. Acceptance, test, and migration-simulation allocations MUST use independent non-production scopes (normally `acceptance:<production-scope>` / `test:<production-scope>`) and MUST NOT advance the corresponding production counter.
4. A separately authorized maintenance transaction MAY lower a production `last_allocated` only to remove a proven contiguous orphan suffix above the highest confirmed real production conversation. This is not a general rollback mechanism.
5. Every such orphan repair MUST preserve `repair_history` with the previous counter, repaired counter, exact contiguous orphan suffix, reason, reliable timestamp, and authority provenance, and MUST use CAS.
6. Interior gaps MUST NOT be repaired by renumbering later real conversations; confirmed real production numbers remain consumed.
7. CAS conflict requires re-read and recomputation; last-write-wins is invalid.
8. `Cxx`/`Txx` sequence metadata is independent of learning Branch generation and external project/session collaboration authority. Neither reservation nor authorized repair grants, changes, or proves lineage writer authority.
9. A fresh/unbound conversation may use only the narrow correctly scoped reservation exception. Orphan repair is separately authorized maintenance and is not available through the fresh/unbound reservation exception.

Historical V0.2 learner-project Domain Goal/Plan/State/Deferred files and their document types are not current runtime schema. They may remain visible only through Git history or historical handoff/acceptance material. `domains/<domain>/curriculum.yaml` remains the V0.3 Knowledge Plane curriculum home.

## 29. Audit rule

Any persistent inferred learner state should be able to answer:

1. What exact claim is being made?
2. What evidence supports or challenges it?
3. What teaching behavior can it change?

Any derived planning artifact should be able to identify the learner goal/plan revision and other upstream assumptions that were genuinely material to the decision without becoming coupled to every ordinary learning-state revision.

## 30. Private-Control lineage compatibility

Legacy/private project-design `lineage_control` documents may remain supported by compatibility validation and may remain canonical in the Private Control repository under that target's own governance. They are not ordinary Core or Instance product state.

Core does not define Agent/session/project-design generation acquisition, handoff publication, claim, takeover, or writer credentials. Current execution-environment authority and the applicable target repository own those collaboration semantics. A legacy lineage document therefore cannot be used by ordinary Runtime to infer or acquire project/session authority, and it never bypasses deployment, learning-Branch, fresh-read, or target-CAS guards.