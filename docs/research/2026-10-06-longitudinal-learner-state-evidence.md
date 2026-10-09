# Longitudinal learner-state evidence for state-sensitive tutoring

Date: 2026-10-06  
Status: external research input for Learning OS Core issue #34; not product acceptance, deployment authority, learner-state mutation authority, or schema authorization.

## Research question

For a long-running, cross-session AI tutor, which learner-state signals are worth persisting because they can reasonably change the next teaching action, and what is an evidence-backed minimal sufficient learner model?

The completed Chat Deep Research covered classic ITS / learner modeling / knowledge tracing / learning science and recent LLM-tutoring work. This writeback does **not** rerun external research. It preserves the completed report's durable findings, project implications, and important source-quality limits.

## Executive synthesis

1. **Persist only state with plausible decision value.** The strongest candidate class is concept/skill-level knowledge state tied to concrete actions such as review vs advance, problem selection, difficulty, and diagnostic probing. A learner model that cannot change a teaching decision should not be retained merely because it is inferable.
2. **Time matters for verification priority, not automatic state decay.** Recency and spacing can inform whether relevant review or natural verification is worthwhile; old demonstrations do not permanently establish current readiness. Preserve those historical observations and existing Knowledge/capability state unless new evidence warrants reassessment: elapsed time alone neither creates forgetting evidence nor authorizes a downgrade. The evidence base for spacing is strong at the learning-science level, though the completed report did not directly verify the primary Cepeda source and instead cited a secondary summary.
3. **Judge observations by diagnostic value and scope.** A single noisy error, guess, typo, or transient affective signal should not establish mastery, a stable misconception, or a broad learner trait. A sufficiently diagnostic observation can nevertheless justify a scoped update, such as provisional support or reassessment of a contradicted claim, under the existing evidence-integration rules. Diverse, independent evidence generally carries more weight than duplicate or highly assisted observations; counts alone do not settle the claim.
4. **Assisted success is not equivalent to independent success.** Hint/scaffolding history is useful mainly as a qualifier on interpretation and next-action choice. In the cited Sao Pedro et al. EDM study, incorporating scaffolding yielded better predictions than classic BKT in that inquiry-skill setting. This is positive but narrow evidence that assistance context can matter; it does not establish that detailed assistance history should always be persisted or that acting on it improves durable learning across domains.
5. **Misconceptions and error patterns can be decision-relevant when they are stable and domain-grounded.** They are most defensible in domains with validated misconception/bug taxonomies; evidence for a generic cross-domain "misconception state" is much weaker.
6. **Confidence, goals, motivation, engagement, and affect have weaker causal evidence as longitudinal teaching-policy inputs.** They may be useful for diagnostics, framing, or session-level intervention, but the completed report did not establish that broadly adapting content sequence to these signals improves durable learning.
7. **Learner preferences are not a general pedagogical control signal.** The completed report supports invariance to claimed learning styles and similarly weakly grounded traits. Preference can still be respected as UX choice, but should not be conflated with evidence that a different pedagogy improves learning.
8. **Over-personalization is a real failure mode.** Personalized environments can narrow information search or reinforce biased knowledge boundaries. Decision-irrelevant or noisy, low-confidence profile/affective traits should therefore not force a visibly personalized detour. Uncertainty in decision-relevant capability evidence can still change the next action, including `continue_with_caution` for provisional support under `protocol/teaching-decision.md`; low confidence alone is not an invariance rule.
9. **For Learning OS, the most important architectural implication is separation of observation, evidence, and interpreted mastery/capability state.** Later correction should prompt claim-scoped reassessment against the relevant portfolio, retaining or revising the interpretation only as justified, without fabricating a second independent performance or erasing history.
10. **Evaluation must test learning, not personalization appearance.** State-sensitive paired cases, irrelevant-state invariance cases, structured-state ablations, strong-summary controls, calibration checks, and delayed retention/transfer are higher-value than exact wording comparisons or LLM-only response ratings.

## Learner-state evidence matrix

| State / signal | Reasonable teaching actions | Evidence disposition | Important failure modes / conditions |
| --- | --- | --- | --- |
| Skill/concept knowledge or mastery estimate | review vs advance; problem selection; difficulty; diagnostic probe; prerequisite revisit | **Strong candidate** | Requires valid knowledge decomposition and calibrated uncertainty. The completed report overreached when it used a general tutoring meta-analysis as if it isolated mastery adaptation; mechanism-specific primary evidence still needs direct verification. |
| Recency / spacing / forgetting | relevant review timing; interleaving; verification priority | **Strong candidate** | Broad learning-science support is strong, but this run used an indirect source for Cepeda et al.; exact decay function and thresholds are not established here. Elapsed time alone must not create forgetting evidence, erase observations, or downgrade capability state. |
| Recent independent performance plus longer history | challenge level; pause/advance; request more evidence | **Strong candidate** | One recent outcome is noisy; duplicated items and correlated attempts should not be treated as independent evidence. |
| Hint / assistance usage; assisted vs independent success | scaffold amount; request independent retry; weaken confidence in mastery update | **Conditionally useful** | A cited EDM study found that incorporating scaffolding yielded better predictions than classic BKT in one inquiry-skill setting and suggested the scaffolding was effective. This is positive but narrow evidence that assistance context can matter; it does not establish a universal tutoring-policy benefit or justify retaining detailed hint histories by default. |
| Repeated misconception / error pattern | targeted explanation; contrast case; prerequisite diagnostic | **Conditionally useful** | Strongest only when the domain has a validated misconception taxonomy. Generic inferred "misconceptions" risk overfitting. |
| Self-reported confidence / uncertainty | metacognitive probe; ask learner to justify; targeted diagnostic | **Conditionally useful / often ephemeral** | Self-report can be miscalibrated; the completed report found little direct ITS evidence that confidence-driven adaptation improves learning outcomes. |
| Prior knowledge / prerequisite state | proportionate starting assumptions; skip/review prerequisites; diagnostic targeting | **Conditionally useful** | Relevant learner-reported background may inform provisional starting assumptions and natural observation, but does not establish demonstrated mastery. Additional active verification must satisfy the decision-impact and learner/flow-cost probe gates; do not require default placement testing. |
| Goals | route/content relevance; framing; longer-horizon content choice | **Conditionally useful** | Useful for route intent, but the completed report did not establish broad causal evidence that declared goals should change moment-to-moment pedagogy. |
| Motivation / engagement / affect | tone; pacing; encouragement; whether to diagnose disengagement | **Mostly session-level / conditional** | Detection is noisy; recent evidence in the report was largely correlational/SEM rather than causal learner-state adaptation. Avoid content detours based on a single inferred mood. |
| Learning style / modality preference as pedagogical trait | none for core teaching policy | **Weak / unsupported** | The completed report supports invariance: matching pedagogy to fixed "learning style" lacks evidence. UX preferences should be kept distinct from claims about learning effectiveness. |
| Decision-irrelevant or noisy, low-confidence profile/affective traits | no forced personalized detour | **Invariant with respect to these traits** | Risk of spurious personalization, stereotyping, and unstable behavior. This row does not cover uncertainty in decision-relevant capability evidence, which may warrant proportionate caution or another justified action. |

## Sensitivity vs invariance findings

### State differences that should be candidates to change the next action

For #34 synthetic and later real-learning evaluation, the strongest candidate contrasts are:

- clear difference in sufficiently diagnostic skill/concept evidence, interpreted against the relevant portfolio rather than inferred from one noisy, non-diagnostic outcome;
- independent success vs success only after substantial assistance;
- recent repeated failure vs a stable longer-term record of independent success;
- long time since last demonstrated competence vs recent independent competence;
- repeated, domain-grounded error pattern vs no such pattern;
- materially different prerequisite evidence.

The expected action difference should be evaluated as an **acceptable action set**, not exact prose. Examples include review/probe vs advance, lower vs higher scaffolding, prerequisite diagnosis vs target-skill practice, or scheduled review vs no review.

### State differences that should usually leave teaching invariant

The tutor should normally remain invariant to:

- claimed learning style or fixed modality type;
- unrelated demographic/profile details;
- stale context that no longer bears on the current concept;
- a single typo/guess/anomalous error without claim-relevant diagnostic value;
- a low-confidence LLM inference of boredom, motivation, or personality;
- decorative personalization context that cannot plausibly change the pedagogical decision.

When learner state is uncertain, prefer natural teaching or observation rather than making active diagnosis the default. Under the existing `protocol/teaching-decision.md` probe rule, actively probe only when resolving the uncertainty could materially change the next teaching action **and** expected information value exceeds learner and flow cost. Prefer existing evidence and lower-cost clarification before a discriminating probe; otherwise a proportionate teaching or observation action may leave the uncertainty unresolved.

## Longitudinal update and correction

The research supports the following semantic constraints without specifying a storage schema:

- **Observation is not mastery.** Keep source behavior separate from its interpretation.
- **Evidence should preserve provenance and assistance conditions.** A correct answer after hints and a clean independent answer are different observations even when the visible task result is "correct."
- **Interpretation should remain revisable.** Later correction such as "I used a hint" should trigger reassessment of the exact claim and relevant portfolio, not an automatic downgrade. Retain, refine, or withdraw apparent support only as justified; preserve independent support, unaffected claims, and the original observation without inventing another performance or failure.
- **Readiness may need verification; historical evidence does not expire.** Long-unrefreshed demonstrations may raise verification priority for a relevant next step, but remain preserved observations. Elapsed time alone must not downgrade Knowledge/capability state or imply the learner "never knew" it; reassessment requires new evidence.
- **Repeated evidence is not automatically independent.** Same-item repetition, copied work, or tightly scaffolded retries should add less confidence than varied independent demonstrations.
- **One behavior can support multiple claim-specific interpretations.** A single performance may bear on multiple knowledge components; the system should not force deterministic one-event-to-one-skill attribution.
- **Unknown remains valid.** If the available evidence does not distinguish "forgotten", "never mastered", "misread", "used help", or "conceptually confused", the next action can be a diagnostic rather than a permanent classification.

These constraints align directly with #34's existing recovery/correction requirements and should be treated as research support for those semantics, not as a new implementation mandate.

## Evidence-backed minimal sufficient learner model: recommended disposition

### Strongly justified candidates to persist and allow to affect teaching

- concept/skill-level knowledge or capability estimate **with uncertainty**;
- recency/time since relevant evidence sufficient to inform relevant review or verification priority, without elapsed-time-only state downgrades;
- recent independent performance in context of longer history;
- provenance needed to distinguish independent vs assisted performance.

### Conditionally useful candidates

- stable, domain-grounded misconception/error pattern;
- prerequisite/prior-knowledge evidence;
- goals when they affect route/content selection rather than moment-to-moment teaching;
- confidence when explicitly elicited for a diagnostic/metacognitive purpose;
- engagement/motivation indicators only when reliable and tied to a bounded intervention.

### Ephemeral / session-level by default

- momentary affect;
- one-off hesitation, typo, or isolated anomaly;
- low-confidence engagement/motivation inference;
- transient presentation choices that do not establish learning effectiveness.

### Weak / unsupported as pedagogical state

- fixed "learning style";
- personality-type style labels;
- unrelated profile traits;
- any weakly inferred trait that is not validated against a concrete teaching decision and learning outcome.

## Product impact for Core issue #34

This research does **not** justify a new learner schema. It does sharpen the validation target already declared by #34:

1. Build **state-sensitive paired cases** where only a decision-relevant state changes, especially mastery evidence, assistance provenance, repeated performance, and recency.
2. Build **irrelevant-state invariance cases** where learning-style labels, unrelated profile fields, decision-irrelevant stale context, or noisy inferred affect change but the acceptable next-action set should not.
3. Add a **strong-summary control** containing the same permitted facts in a competent narrative summary so structured state must beat or match a fair baseline rather than a weak control.
4. Add a **structured-state ablation** removing the state under test; require a meaningful decision-quality difference before treating the state as valuable.
5. Include **uncertainty cases** where active probing is acceptable only when both decision-impact and learner/flow-cost gates are met; otherwise natural teaching or observation may preserve uncertainty. Forced personalization is not a substitute for those gates.
6. For later real-user work, measure **delayed retention/transfer**, not only tutor-task correctness, engagement, human-rated response quality, or automated LLM grading.
7. Keep correction semantics consistent with #34: reinterpret one source occurrence without rewriting history or double-counting it.

Disposition:
- **Absorb as test/evaluation constraints:** sensitivity, invariance, uncertainty handling, assisted-vs-independent distinction, observation/evidence/interpretation separation.
- **Keep as candidates:** exact persisted state classes, mastery thresholds, decay functions, misconception taxonomies, motivation/goal persistence rules.
- **Do not absorb as product facts:** any claim that more memory is better, that human-like tutoring is more effective, or that a particular LLM-generated personalization style improves learning.

## Important uncertainty, conflicts, and freshness limits

The completed Deep Research produced useful synthesis, but its citation quality was uneven. These limits are material:

- The cited Nickow et al. tutoring meta-analysis supports tutoring outcomes in general; it does **not by itself isolate skill-level mastery adaptation**. Any stronger use would require mechanism-specific primary sources.
- The spacing conclusion is well established in learning science, but the completed report cited a Wikipedia summary of Cepeda et al. rather than directly verifying the primary paper.
- The learning-styles conclusion is consistent with a large skeptical literature, but the completed run cited a secondary "Studies Suggest" summary rather than directly verifying Pashler et al. 2008.
- The assistance/scaffolding result is positive but narrow: in the cited BKT/EDM study, incorporating scaffolding improved prediction over classic BKT in a specific inquiry-skill setting. It should not be generalized into a universal value for all hint history, a universal persistence rule, or causal proof that longitudinal assistance-state adaptation improves durable learning.
- The 2026 gamification evidence is structural-equation/correlational evidence about engagement/motivation mediation, not a randomized test showing that momentary affect state should drive teaching actions.
- Recent LLM-tutor evidence in the report is sparse, often small-sample or preprint, and does not robustly isolate the causal value of longitudinal learner-state personalization.
- The reported personalization-bias study is relevant as a warning, but generalization from its task/environment to longitudinal AI tutoring remains uncertain.
- No evidence in the completed report establishes one universal mastery threshold, one forgetting function, or one optimal "minimal model" across domains.

These gaps mean the recommended model is an **evidence-backed candidate minimum**, not a settled universal learner model.

## Sources preserved from the completed Deep Research

### Reviews / meta-analysis / secondary synthesis

- Nickow, Oreopoulos & Quan, *The Impressive Effects of Tutoring on PreK-12 Learning: A Systematic Review and Meta-Analysis of the Experimental Evidence*  
  https://edworkingpapers.com/sites/default/files/ai20-267.pdf  
  Use here only for broad tutoring-outcome context; it does not isolate learner-state adaptation.

- Spacing-effect summary citing Cepeda et al. (secondary source used by the completed run)  
  https://en.wikipedia.org/wiki/Spacing_effect  
  Primary-source verification remains a follow-up gap.

- "Learning styles" evidence summary used by the completed run (secondary source)  
  https://studiessuggest.org/stories/learning-styles-no-evidence-matching-instruction  
  The report named Pashler et al. 2008, but that primary source was not directly verified in the completed run.

### Primary / empirical or conference evidence used by the completed run

- Sao Pedro et al., EDM 2013 paper on scaffolding and Bayesian Knowledge Tracing  
  https://learninganalytics.upenn.edu/ryanbaker/SaoPedroetalEDM2013_Final.pdf

- Personalization-bias study PDF cited by the completed run  
  https://turner-mbcn.com/wp-content/uploads/2025/11/2026-31272-001-1.pdf  
  Bibliographic metadata was incomplete in the returned report; treat as a warning signal pending source reconciliation.

- Frontiers 2026, *AI-driven gamification and inclusive learning outcomes in higher education institutions: a structural equation modeling approach*  
  https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2026.1876544/full  
  Treat as observational/SEM evidence, not causal proof of affect-state adaptation.

### Recent LLM / learner-model evidence

- Stanford SCALE summary, *Faster, Cheaper, More Accurate: Specialised Knowledge Tracing Models Outperform LLMs*  
  https://scale.stanford.edu/ai/repository/faster-cheaper-more-accurate-specialised-knowledge-tracing-models-outperform-llms  
  Relevant to prediction-task model choice; not direct evidence that a specific tutoring action improves learning.

- KG-RAG adaptive tutor paper/preprint summary, controlled experiment with 76 students  
  https://www.alphaxiv.org/abs/2311.17696  
  Promising but small and not sufficient to establish the value of each longitudinal learner-state class.

## Remaining highest-value research gaps

1. Direct primary-source evidence isolating **mastery-state-driven action selection** rather than overall tutoring effectiveness.
2. RCT/field evidence separating the value of **assistance history** from generic performance history.
3. Controlled evidence for **misconception-state adaptation** and whether the benefit generalizes beyond domains with explicit bug libraries.
4. Causal evidence for acting on **confidence, motivation, engagement, or affect** rather than merely predicting outcomes from them.
5. Longitudinal LLM-tutor experiments with state ablations, strong-summary controls, delayed retention/transfer, and calibration.
6. Evidence for robust update rules under **correlated observations, self-correction, multi-skill ambiguity, and stale state**.

Until those gaps are closed, #34 should use the research primarily to constrain **what must be sensitive, what must remain invariant, and what should be evaluated under uncertainty**, not to freeze a universal learner-model design.
