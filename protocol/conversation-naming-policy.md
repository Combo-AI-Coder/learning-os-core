---
protocol: conversation-naming-policy
version: "0.2"
schema_compatibility: "0.3"
---

# Conversation Naming Policy

This protocol governs first-turn suggested conversation names. It exists only to allocate physical conversation sequence labels safely and must never be used as learner state, project/session writer authority, or lineage generation identity.

## 1. Separation of concerns

- Production `Cxx` identifies an actual user work conversation sequence within one naming scope.
- `Cxx` is independent of Learning Branch generation and external project/session writer authority.
- A conversation-name allocation never grants writer authority, never changes `active_generation`, and never satisfies a generation guard.
- The sequence registry is durable UI/runtime metadata. It is not learner state, Evidence, Progress, Knowledge, Execution, Coordination, or project-design lineage control.
- Acceptance, test, migration simulation, abandoned bootstrap probes, and other non-user-work surfaces MUST NOT consume production `Cxx` numbers.

## 2. Production vs non-production scopes

Resolve identity and usage before allocating a sequence.

### Production scopes

Production scopes are reserved only for genuine user work conversations:

- `learning_os`
- `learning_hub`
- `model_review`
- `topic_hub:<topic>`
- `learning_main:<topic>:<subtopic>`
- `learning_practice:<topic>:<subtopic>`
- `learning_deep_dive:<topic>:<subtopic>`

A production allocation uses the `C` prefix.

### Non-production scopes

Acceptance/test tooling MUST use a separate non-production scope, for example:

- `acceptance:<production-scope>`
- `test:<production-scope>`

Non-production allocations use the `T` prefix unless a task explicitly defines another non-production marker. They are diagnostic metadata only and MUST NOT be rendered or reported as the production conversation identity.

Sequences are monotonic within each independent scope. A non-production reservation never advances the corresponding production scope.

## 3. What counts as a production conversation

A production `Cxx` is appropriate only when all of the following hold:

1. the surface is an actual user conversation intended for ongoing work, learning, review, or maintenance;
2. the user has created or explicitly selected that physical conversation as the work surface;
3. the assistant is not running a synthetic acceptance/test/migration probe whose purpose is to validate allocation behavior itself.

If the surface is only a test fixture, temporary probe, acceptance harness, or disposable synthetic conversation, it MUST use a non-production scope.

If a user explicitly selects an already-existing physical work conversation with an established valid `Cxx`, reuse that identity and do not reserve another production number merely because the registry has moved ahead.

## 4. Production allocation transaction

On the first assistant turn of a genuinely new user work conversation, after bootstrap and identity resolution:

1. Read the Core `chat_routing.conversation_name_behavior.sequence_registry` setting from `config/core.yaml` and use that repository-relative Instance registry path.
2. If this physical conversation already has an established valid production `Cxx` identity, reuse it and do not reserve another number.
3. Otherwise fresh-fetch the sequence registry and its current blob SHA.
4. Resolve the production naming scope and read `last_allocated`; missing scope means `0`.
5. Compute `n = last_allocated + 1`.
6. Update only the sequence registry, setting that production scope's `last_allocated: n` and a reliable `updated_at`, using the fetched blob SHA as CAS protection.
7. If the CAS conflicts, re-fetch, recompute from the newer value, and retry. Never use last-write-wins.
8. Render `Cxx` as `C` plus the decimal number padded to at least two digits (`C01` ... `C99`, then `C100`, etc.), substitute it into the configured name template, and explicitly report the suggested name.

A valid production reservation is consumed once it corresponds to a real user work conversation, even if that conversation is later abandoned or manually renamed. Confirmed real conversation numbers are never reused.

## 5. Non-production allocation transaction

Acceptance/test/migration-simulation work follows the same CAS discipline but uses an independent non-production scope.

1. Resolve the corresponding non-production scope such as `acceptance:learning_os`.
2. Fresh-fetch the sequence registry and its current blob SHA.
3. Increment only that non-production scope.
4. Use `Txx` as the diagnostic sequence label unless another explicit non-production prefix is specified.
5. Never write the production scope as part of the same test allocation merely to prove that production allocation works.
6. Verify after the test that the production scope did not change.

A non-production reservation may be retained permanently for audit. It has no effect on production numbering.

## 6. Narrow write exception for fresh conversations

A fresh/unbound conversation may perform the production or non-production sequence-registry CAS reservation defined above before it has learner/design lineage generation authority. This is a deliberately narrow exception because the registry carries no learner/project authority.

The exception permits exactly one class of write: a correctly scoped sequence reservation in the configured conversation-sequence registry. It does **not** permit writes to lineage control, learner state, Topic/Subtopic state, Evidence, Execution, Coordination, handoffs, plans, protocols, config, or any other repository path.

After allocation, all ordinary writer-safety rules remain unchanged. Reading or writing the sequence registry does not grant a generation.

## 7. Failure behavior

If the registry cannot be read or the reservation cannot be committed safely, do not guess or claim a unique production `Cxx`. Report that sequence synchronization failed and continue only with work that is safe under the conversation's existing authority.

For test/acceptance work, failure to allocate a non-production sequence MUST NOT be repaired by consuming a production number.

## 8. Orphan reservation repair

An **orphan reservation** is a production reservation for which there is reliable evidence that no real user work conversation ever corresponded to that reserved number. Examples include an acceptance fixture that incorrectly used the production scope or a mistaken reservation made in a conversation that already had a different established production identity.

An explicitly authorized Learning OS maintenance session MAY repair orphan reservations only under all of these conditions:

1. current explicit maintenance authorization is established and all applicable target writer guards pass;
2. the current sequence registry is fresh-read with its blob SHA;
3. the candidate numbers form a contiguous suffix above the highest independently confirmed real user work conversation in that scope;
4. each candidate suffix number is independently established as orphaned from canonical provenance plus current explicit user information or equivalent reliable evidence;
5. no canonical handoff, Branch runtime, user-selected active conversation identity, or other durable current state depends on those candidate numbers as real physical work conversations;
6. the repair preserves an audit note describing the previous counter, repaired counter, reason, and timestamp;
7. the registry update uses CAS.

When all conditions hold, the production `last_allocated` MAY be lowered to the highest confirmed real user work conversation. This is the only permitted counter decrease.

The following are forbidden:

- lowering through or below a confirmed real user conversation;
- reusing a number that belonged to a real user work conversation;
- repairing a non-contiguous interior gap by renumbering later real conversations;
- using Project memory or an ambiguous UI title alone as sufficient proof;
- altering lineage generation or writer authority as part of numbering repair.

If evidence is insufficient, preserve the higher counter and record the ambiguity instead of guessing.

## 9. Migration and audit

An explicitly authorized Learning OS maintenance session may raise a production scope's `last_allocated` floor when a higher already-existing real user work conversation number is independently confirmed.

Historical mistaken test allocations should be migrated into a non-production audit scope when useful, but historical Git commits and acceptance documents need not be rewritten. Current registry state should truthfully distinguish production user-work numbering from non-production test numbering.

Manual UI titles and Project memory are supplemental evidence for migration/repair, not sufficient by themselves to overwrite canonical sequence metadata without corroboration.
