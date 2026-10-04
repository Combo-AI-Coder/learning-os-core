# 2026-10-03: session identity and capability-root admission

## Baseline and reproduced defects

Baseline: `d6b7e44d636ecfa465259c849b7c646e89110107`; its 511 tests passed locally
(10 existing platform skips). Synthetic broker tests reproduced two defects:

- expected_generation=True and 1.0 were accepted for generation 1, and 3.0 for
  generation 3, because Python equality was used without integer admission.
- PurePosixPath('.') has no parts, so the canonical-path check admitted '.' as
  a capability root. That root contains every relative repository path.

The broker is a host-side boundary. These tests do not demonstrate that a real
learner or remote attacker obtained unauthorized access, nor that the deployed
version was exercised or changed.

## Repair and verification

Require an exact positive integer for an explicitly supplied expected_generation,
before provider I/O. Keep None for read-only sessions and keep the existing rule
that writable sessions need an established generation. Reject empty path parts
so '.' cannot accidentally become a whole-repository capability root. The
successor API's existing generation checks and all deployment/CAS/generation
fencing remain intact.

Four added tests exercise equality-compatible aliases, invalid values before any
provider operation, read-only omission, and dot-root rejection. The full local suite passed: 515 tests, with 10 existing platform skips.
Hosted results are attached to the PR. Fixtures are synthetic only.

## Unfinished product and release work

This development change is not a deployment promotion. Runtime-Control remains
at its accepted exact Core pin and epoch; promotion still requires its separate
freeze/drain/pin/activate/verification sequence. The active Instance and learner
state are untouched.

Issue #19's real intake-experience evidence is still outstanding: on an authorized
learner surface, evaluate minimal/balanced/thorough, immediate-start with deferred
questions, a current-instruction override, and removing a durable preference to
inherit again. Existing deterministic policy tests cannot settle teaching quality
or learner experience. Do not manufacture a learner trial for this maintenance PR.

Persistence scope: target code, regression tests and this factual receipt.
The reproduced admission defects are repaid at the deterministic-code scope;
real learner acceptance and deployment remain separate, open obligations.

A minimized, tool-free independent static patch review returned
NO_BLOCKING_FINDINGS for the changed Core functions and four tests. It did not
review unrelated PCN/Harness changes or perform live Runtime operations.
