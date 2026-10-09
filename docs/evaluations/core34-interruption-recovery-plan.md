# Core #34 P2: bounded interruption discovery

Prospective design recorded on 2026-10-06 before adding the operation and tests.
The crash cut below is fixed for this deterministic test; this is not an
independently preregistered behavioral experiment. Baseline: Core main
`2a9fc6ab3dabc8c8c9a4ba20896bcf6c609bfa9b`, including #39.

## Synthetic cut and outcome

A normal Main-bound producer saves one typed Evidence record with explicit
Topic/Subtopic context, then loses its session **before any Knowledge
reconciliation**. A newly opened read-only consumer receives only its normal
host Topic/Subtopic binding. It has no producer conversation, Evidence ID,
Evidence path or injected recovery hint. Calling the bounded discovery operation
must recover the durable observation and same-snapshot reference facts. The
producer's retained ID is available only to test assertions.

The interruption is deliberately synthetic session revocation, not a simulated
OS crash or a claim about distributed failure timing. Repeat discovery and a
second fresh consumer must preserve the same record, all bytes/revisions and zero
write calls. A countercase with current Knowledge referencing the observation,
and one with multiple typed targets and partial referencing, prevents equating
absence of a reference with unprocessed work.

## Smallest sufficient path

Reuse the existing broker's immutable inventory, pinned Instance authority,
deployment read lease, capability checks, bounded YAML reader and final freshness
checks. The existing read_learning_context operation requires known paths and
cannot find this orphan. A host-side repository walk would duplicate those
boundaries or expose inventory. Add one no-argument read-only operation instead;
no second index, queue, learner store, event model or reconciliation path.

Discovery requires a bound Main Topic/Subtopic and read authority for the whole
Evidence root. It scans at most 128 canonical Evidence paths, returns at most 16
matching observations, follows at most 16 Knowledge owners, and admits at most 8
typed targets per observation / 64 total targets. Per-document content is at most
64 KiB; all scanned Evidence (including nonmatching records) and loaded Knowledge
content share a 256 KiB budget. Exceeding a bound rejects the whole operation,
without partial success or silent truncation. Existing provider snapshot limits
still apply before this narrower selector.

Relevance is exact explicit Evidence context Topic/Subtopic equality. Missing
legacy context is not selected; ordinary reads/retries remain compatible. This
is a bounded context-discovery envelope, not semantic relevance to every current
teaching question. Return every eligible match within the bounded snapshot in
canonical path order, retaining original observed_at bytes. Do not claim newest,
wall-clock recency or chronological order: canonical historical timestamps may
lack timezones. This first slice discovers the newly durable observation but
does not provide a global recent-Evidence search service.

For each exact typed capability target, report whether its current Knowledge
owner is absent/present and whether that owner references this ID on support or
challenge. Knowledge references are representative, so not referenced does not
mean unprocessed, failed, safe to replay or independent new performance. One
Evidence record remains one occurrence even when it has several targets.

## Prospective checks and boundaries

Exercise the crash cut, referenced/unreferenced/partly-referenced controls,
missing owners, unrelated/missing context, malformed YAML/identities/targets and
Knowledge, denied reads, bounds and bound+1, stale/revoked/forged sessions,
Instance/deployment drift, cleanup failure, and repeated no-write reads. Verify
all returned reference facts derive from the same immutable snapshot. Complete
Core validator and full tests, plus complementary native boundary review and an
exact-content pre-Codex gate before any review-triggering publication.

No Evidence creation/retry, reconciliation, automatic capability promotion,
correction event, real learner mutation, deployment or merge occurs during
recovery. Correction propagation remains a later #34 slice. #39's same-model,
synthetic, blinding and teaching-effect limits, all failed outputs, and the 28
historical not_executed scenarios remain unchanged.
