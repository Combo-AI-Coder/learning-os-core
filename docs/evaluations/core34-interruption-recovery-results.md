# Core #34 P2: interruption-discovery result and limits

## Bounded product result

A fresh read-only consumer can discover an existing typed Evidence occurrence
saved before producer-session loss and before Knowledge reconciliation. It needs
only the normal host-bound Main Topic/Subtopic, not the producer's transcript,
Evidence ID or repository inventory. The `discover_learning_evidence` operation
(surface v3, recipe `bound-evidence-discovery-v1`) returns that occurrence and
current-Knowledge reference facts from one pinned snapshot.

The [prospective cut and limits](core34-interruption-recovery-plan.md) are the
assignment and test contract. Core #34 remains the longitudinal outcome owner.
This result covers its first interruption-discovery slice; it does not close P2,
correction propagation or the broader longitudinal journey.

## Implementation choice

The existing `read_learning_context` needs caller-known paths. Reusing it alone
would not find an orphaned observation; putting an inventory walk outside the
broker would duplicate session/authority checking or reveal raw inventory.
The new operation therefore reuses the existing snapshot inventory, capability
checks, deployment read lease, bounded YAML reader, generation/binding checks and
final Instance/deployment freshness checks in the broker itself. There is no new
index, queue, second learner store, event architecture or write operation.

The selector requires whole-root Evidence read authority and checks every
Evidence-derived Knowledge owner before access or absence reporting. One source
occurrence has one result, even with multiple capability targets; harmless
bounded target metadata is preserved. Owner absence, owner presence and
claim-specific support/challenge references stay distinct.

Discovery returns all eligible context matches in deterministic path order under
the documented caps. It preserves original timestamps rather than inventing an
ordering for timezone-less historical data. It does not claim an age window,
latest-first search or completeness outside this bounded snapshot. Missing legacy
context is outside selection, and observation/time admission is not retroactively
applied as a new-write rule. Typed matching Evidence interpretation/target
structure and the read Knowledge structure are checked before returning facts.

## Reproduction and negative controls

Run:

- `python -m unittest tests.test_evidence_discovery tests.test_reference_host -v`
- `python scripts/validate_learning_os.py . --core`
- `python -m unittest discover -s tests -v`

`tests/test_evidence_discovery.py` exercises the broker’s create-only
operation over a synthetic repository provider, producer close at the declared cut, fresh consumer handles,
repeatability and zero recovery writes. It checks referenced/unreferenced/partly
referenced targets, missing Knowledge owners, neutral/deferred interpretations,
context and capability rejection, malformed data, explicit bounds and bound+1,
UTF-8 byte accounting, same-snapshot reads, missing inventory, session/binding
staleness, injected mid-operation Instance/deployment drift and cleanup failure. A save with
harmless target metadata is a regression case because the initial draft's
closed-key selector rejected a record that the existing create operation accepts.

Complementary native review also identified missing explicit scalar-ID and
interpretation validation in the first draft. Those cases were reproduced,
repaired and added as negative controls. An accidental test-call insertion was
caught by the first focused run and repaired. Drift-fixture failures were repaired
by advancing the synthetic commit with the changed generation/binding, respecting
the provider's immutable-commit contract; required production guards were not
weakened. The exact candidate identity, final command results and review
findings/dispositions must be retained in the target-native issue/PR receipt before
Codex review entry. Same-model native review is internal quality amplification,
not independent audit.

## Explicit limits and continuation

- Bounded deterministic mechanical witness only (session revocation, not OS-crash durability, semantic judgment, or real learner outcomes); scan caps fail closed rather than truncating silently; Core #39 and the 28 historical `not_executed` cases remain unchanged.

Correction/reinterpretation preserving single source occurrence remains the next slice under Core #34.
