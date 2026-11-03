# Opt-in first-use attribution

This diagnostic reuses existing synthetic first-use tests without changing their
assertions, fixtures or product implementation. It is not part of the default CI
command. Importing it does not run a journey. The cheap `test_first_use_profile`
unit tests use fake operations and test instrumentation, not first-use behavior.

## Explicit execution only

From the repository root, in an isolated development checkout with the declared
development dependencies, an authorized operator may select **one** whole case:

```sh
python -m tests.first_use_profile --case lost-ack --execute
python -m tests.first_use_profile --case empty-tree --execute
```

These are alternatives, not instructions to automatically run both. They can be
expensive, especially on Windows. The first runs the unchanged ten interruption
positions with ten transitions apiece; the second runs all three existing
empty-tree interruption branches. There is no reduced-coverage mode. Omitting
`--execute` refuses before test execution. This interface grants no permission to
run on a real Host, invoke CI, change timeouts or spend additional resources.

Use the normal unittest suite separately for integration validation. A selected
profile is not a replacement for that suite. No cache, provider validation,
timeout, test assertions or default workflow is changed by this diagnostic.

## What the output means

JSON reports whether the original selected unittest succeeded, test/failure/error/
skip counts, platform/Python version and SHA-256 identities of relevant source
files. A failed or skipped selected test produces nonzero exit status. Source
hashes identify the files read at report time; keep the checkout unchanged during
the run and retain the exact commit/tree separately when comparing results.

Phase rows count calls, exceptions escaping the wrapped call, and **inclusive
in-process wall seconds** from `perf_counter`. They cover class fixture setup and
cleanup, per-fixture setup and cleanup, prepare, apply, recovery inspect, topology
assertion and host close. The complete selected-test total includes unittest
class setup/teardown and fixture cleanup. Apply combines initial/interrupted and
remaining applications; counts and timings do not identify individual loss
positions. These coarse aggregates are deliberately not a new event ledger.

Git rows count the test helper and provider `_run_git` separately, classified by
an allowlisted command family and the innermost active phase. Provider counting
includes both text and binary helper paths; it includes wrapper/environment work,
not just time inside a Git subprocess. Unknown command syntax is `other`; operands
are never scanned to guess command identity or emitted. The chosen cases run
sequentially; wrappers are process-global during the diagnostic, so do not embed
this runner in concurrent unrelated test execution. A call in another context
has an `unscoped` phase, not an inferred parent attribution.

Nested phases overlap (for example Git inside apply, inspect inside apply, or
cleanup inside the selected total). **Do not sum inclusive rows into a total.**
Instrumentation overhead is included. These measurements differ from adjacent
CI log-delivery brackets, which may include output buffering. Neither a single
profile nor before/after timings on unmatched hosts prove a causal speedup or
the cause of an earlier CI cancellation. Linux results do not establish Windows
behavior. Separate fixture, validation, Git and cleanup costs before proposing
another optimization; no runtime cache or relaxed safety check follows.

Only aggregate allowlisted labels, counters and durations are emitted by the
report: no Git operands, source contents, local paths or raw exception messages.
Normal unittest failure details are deliberately omitted from the JSON. Keep any
separately captured process stderr private until inspected/minimized; the report
is not a general-purpose output scrubber. Patches are scoped with `ExitStack` and
restored even when the selected test fails or the diagnostic context exits
abnormally. Abrupt process termination can prevent a report and cleanup, so absence
of output is unknown coverage rather than success.
