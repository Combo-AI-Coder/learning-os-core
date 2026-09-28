---
protocol: curriculum-policy
version: "0.4"
schema_compatibility: "0.3"
---

# Curriculum Policy

Curriculum is reusable Domain knowledge structure. Topic/Subtopic Plans are learner-specific routes. Curriculum selection may inform a route, but curriculum nodes are not learner progress units or Subtopics by default.

Allowed high-level curriculum actions: `advance`, `deepen`, `reinforce`, `repair_prerequisite`, `branch`, `defer`.

## Priority order

1. current explicit learner question;
2. current blocker;
3. high-value ready node aligned with the Topic goal;
4. node that naturally resolves a high-value evidence gap;
5. useful integration or reinforcement;
6. valuable branch;
7. low-priority review;
8. syllabus completeness.

## Initial Topic planning

For a new learner-specific Topic, consult `new-topic-start.md`. Use learner-authoritative Goal, relevant execution constraints/preferences, background, current Knowledge State, and reusable curriculum structure to create a coarse adaptive Topic route.

Prefer a few meaningful Topic milestones and materialize only the current useful Subtopic. Do not generate a large fixed syllabus merely for completeness. A Topic Plan MAY remain `provisional` while learning begins.

Creating a Topic does not require creating a new Domain. Reuse existing curricula when possible and create/extend reusable knowledge structure only as much as current teaching needs.

## Orientation and route coherence

Curriculum structure is partly responsible for keeping teaching navigable. At Topic/Subtopic entry, and again after a material route change or learner-reported disorientation, present a compact orientation spine when useful:

- the current learner goal/milestone;
- the local upstream -> current -> downstream relation;
- the relevant system/process map at a level appropriate to the learner;
- the reason the current node is being studied now.

This is not a requirement to expose the entire curriculum graph or a fixed syllabus. The purpose is to prevent locally correct teaching from becoming an unstructured sequence of facts.

A learner-specific Plan MAY depart from curriculum suggested order, but a material departure SHOULD have a learner-relevant reason. If runtime teaching temporarily enters a later node or a side path, preserve the conceptual return path. If the departure becomes structural or persistent, replan rather than allowing silent route drift.

Curriculum edges are not only scheduler metadata: when a connection is pedagogically important, teaching SHOULD make the dependency/motivation visible in learner language instead of relying on hidden graph structure.

## Prerequisites and just-in-time learning

Missing state is unknown, not evidence of inability. Prior exposure from `learner/background.yaml` may justify a provisional starting assumption, but not capability state.

For curriculum edges:

- `requires`: treat as a hard prerequisite only when the dependency is meaningfully unsupported/conflicted and would block the target work;
- `supports`: never make it a hard blocker by itself;
- `provisional`: usually continue with caution unless failure cost/dependency importance justifies verification;
- prefer natural observation over automatic pretesting.

Use the minimum escalation required:

1. **natural observation**: unknown/background/low-risk dependency; continue and observe naturally;
2. **inline repair**: small actual blocker; repair only what is needed;
3. **prerequisite-support Subtopic**: a coherent multi-session repair that still serves the current Topic;
4. **standalone Topic**: the learner explicitly wants independent systematic study, or the scope is large/reusable enough to justify its own learning project.

Just-in-time learning SHOULD be used for local dependencies when it reduces unnecessary front-loading, but it MUST remain anchored to established knowledge. Do not casually introduce many undeclared concepts and expect the learner to identify which ones are prerequisites they should ask about.

A future curriculum object MAY be used before formal study only when the current step needs a small, declared interface. Treat the object as a black box at that interface and do not require internal knowledge that belongs to the future node. If the current work actually requires the internals, route to an appropriate repair/detour instead of hiding the dependency.

If an existing active Topic already covers the prerequisite, prefer reusing/reprioritizing it rather than creating duplicate support structure.

A `prerequisite_debt` is deferred routing knowledge, not evidence of inability. Reactivate it only when its consumer/trigger becomes relevant.

## Reusable diagnostic probes

The objective Domain structure is a good home for reusable diagnostic design, but a probe's actual diagnosticity is learner- and context-sensitive. Therefore reusable probes SHOULD be treated as **candidate anchors**, not universal mastery tests.

For high-value curriculum nodes/capabilities, a small curated probe bank or reusable probe specification MAY be maintained. Prefer sparse high-quality anchors and templates over a large undifferentiated question set.

A reusable probe specification SHOULD identify, when material:

- `target_node` and `target_capability` (for example conceptual structure, explanation, standard application, transfer, or formal derivation);
- the prompt or parameterized template;
- required prerequisites/representations/terminology beyond the target capability;
- diagnostic rationale: what distinction the probe is intended to reveal;
- known shortcuts, confounds, or alternative solution paths that can reduce diagnosticity;
- expected reasoning/rubric, not merely a final answer;
- approximate learner/flow cost and difficulty characteristics;
- useful follow-up actions for characteristic response patterns;
- provenance and licensing/usage constraints for externally sourced material.

Probe selection at runtime MUST still consider current learner Knowledge State/background, prerequisite burden, recent teaching exposure, avoid-retesting, learner cost, and the decision that the probe is supposed to inform. A Domain-level anchor can be low-diagnostic for a particular learner if an unrelated prerequisite dominates the task.

Runtime-generated probes remain valid and are often useful for adaptation, but they SHOULD satisfy the same target-capability and confound checks as curated anchors. When a high-quality reusable anchor is available and fits the learner/context, prefer it over improvising a weaker diagnostic merely for novelty.

Externally sourced questions MAY be used as candidate material, but web/source availability does not make them canonical. Before reuse, check correctness, target-capability alignment, hidden prerequisites, likely shortcuts/confounds, adaptation needs, and provenance/license. Do not bulk-ingest large scraped question sets merely to increase volume.

A reusable probe bank is Domain teaching infrastructure, not learner Evidence or Knowledge State. Answering a bank item creates no capability transition by itself; observed performance is still classified and integrated through the Evidence protocols.

Core does not require every Domain to materialize a persistent probe bank. Materialize durable probe assets only when repeated teaching benefit justifies their maintenance; storage/schema may remain sparse and domain-specific until a stable core representation is warranted.

### Optional Domain probe assets

A Domain MAY keep sparse reusable anchors in
`domains/<domain>/probes.md`. The filename is declared by
`domains.optional_probe_file` in Core configuration and is relative to
the selected Domain directory. This is an optional Markdown convention,
not a new canonical document type or a required asset for every Domain.

Runtime MUST NOT load Domain probe assets by default.

Runtime MAY load the selected Domain's asset only when a probe-selection
decision is relevant under Teaching Decision's probe rule. Orientation,
Topic entry, an in-progress node, or unknown capability state alone does
not trigger loading. Reading an asset does not require administering it.

Resolve the optional file within the same trusted Core snapshot used
for that Domain's curriculum. Do not substitute a newer branch version.
A missing optional file is normal: continue with existing evidence,
ordinary teaching, or a suitably designed runtime-generated probe.

Select anchors by target node and capability, then evaluate prerequisites,
recent exposure, confounds, learner cost, and the decision to be informed.
Load only the relevant anchor where selective reading is available.
Keep responses and learner-specific interpretations outside Domain assets.

## Branching and Subtopic materialization

Classify side paths as `inline`, `short_detour`, `deep_detour`, or `defer`. Consider blocker relief, integration value, prerequisite burden, learner interest, diversion cost, and expected multi-session value.

A deep detour may become a Subtopic candidate, but candidate status does not require immediate materialization. Preserve a return point for meaningful detours.

Subtopic materialization is a Topic planning decision, not a curriculum-node transformation. Curriculum node != Subtopic != chat.

## Replanning

Replan only when the learner-specific route would materially improve, for example after:

- explicit Topic Goal or meaningful time/resource change;
- a real prerequisite blocker;
- strong evidence that planned material is redundant or too advanced;
- a valuable learner-interest branch;
- meaningful progress evidence that changes readiness;
- a structural Subtopic change or cross-Topic dependency.

Do not rewrite Topic/Subtopic Plans after ordinary turns. Progress moves without requiring Plan revision unless the route itself changes.

## Review

Do not downgrade Knowledge State solely because time passed. Review priority should consider evidence strength/age, downstream importance, lack of natural reuse opportunities, and failure cost. Prefer natural reuse in upcoming curriculum over separate review when practical.
