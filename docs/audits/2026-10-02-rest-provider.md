# REST provider hardening audit — 2026-10-02

Status: proposed implementation evidence, not deployment or acceptance.

## Scope and baseline

This slice addresses issue #21 against Core baseline
`964e4aedaeba3e1454ea7976b79196165b71f227`.
The REST provider was weaker than the Git CLI provider at archive materialization
and did not require a host-owned write opt-in for legacy Contents API calls.
Current broker writes already fail closed on REST exact-branch-head CAS; this
finding is not evidence that that broker guard was bypassed in production.

## Changes

- Reuse the existing portable Git path policy, including Windows aliases,
  reserved names, Git sentinels, non-canonical separators and traversal rejection.
- Preflight the complete archive namespace before writing: reject duplicate
  entries, Unicode/case aliases, file/directory collisions, ambiguous roots,
  non-regular entries and encrypted entries.
- Bound actual HTTP response bytes, archive entry count, path expansion, individual
  uncompressed files and total uncompressed bytes; copy files in bounded chunks.
- Default REST writes to read-only. Legacy blob-CAS callers must explicitly pass
  `writable_repository_ids=(...)` from host-trusted configuration. This is a
  transport guard, not authorization inferred from token permissions.
- Keep exact branch-head CAS unsupported and rejected before HTTP requests.

## Verification

Environment: Windows, Python 3.13.14, Git 2.54.0.windows.1.
Baseline: 476 tests, 10 platform skips, no failures.
Added 16 synthetic REST boundary tests, including implicit-directory count and
expanded-namespace byte budgets. Existing missing guards were reproduced
before the implementation; new allowlist API cases initially failed because that
API did not exist. A Windows ZIP fixture was corrected to preserve raw backslash
names rather than normalizing them before the provider saw them.
Final local result: 492 tests, 482 passed, 10 platform skips; Core validation
reported zero errors. The seven existing REST-provider tests also passed.

Reproduce:

```sh
python scripts/validate_learning_os.py . --core
python -m unittest discover -s tests -v
```

## Limits and integration boundary

This is deterministic implementation evidence plus same-agent source review,
not a blind independent security assessment, formal proof, production promotion,
or learner acceptance. No real learner data is used in these fixtures.
Archive byte/expansion limits do not establish an absolute process-memory or
whole-transfer wall-clock limit; parser-level fuzzing and representative resource
measurements remain useful follow-up work. Git CLI policy extraction into a
separate module is deferred until a consumer needs more than these pure helpers.
The default-read-only change intentionally requires explicit migration of legacy
REST write callers; broker-authorized writes should continue using exact-head-CAS
capable transport. Runtime-Control and Instance are not modified by this PR.
