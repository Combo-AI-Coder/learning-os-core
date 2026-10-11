# Core #34: durable write without a caller acknowledgement

This is a recoverable archive of one already completed synthetic observation,
not a new experiment or production change. The original result is
[Core #34's bounded witness](https://github.com/Combo-AI-Coder/learning-os-core/issues/34#issuecomment-6102461208).

The producer allowed the unchanged GitCliProvider's local Git push to succeed,
then exited with code 74 before GitCliProvider.create_text returned through the
broker and Host.
Its stdout and stderr were empty. Independent Git readback found one durable
Evidence commit; fresh processes discovered that Evidence, retried identical
content without another commit, reconciled Knowledge once, and recovered the
new reference. Only the durable-result branch was exercised.

## Retained evidence

`lost_ack_probe.py`, the source manifest, `result.json` and retained raw streams
are byte-identical to their original artifacts. `ARCHIVE.json` binds their sizes
and SHA-256 values and explains omitted duplicates. The result includes the
original source/runtime identity, injection receipt, all five process records,
before/after Git observations and exact claim limits. Raw streams retain their
original bytes, including the empty producer response and all empty stderr.
Original temporary bare repositories are not included: readers can inspect the
hashed historical observations and perform a new replay, but cannot reopen the
original Git objects from this archive alone.

The five processes ran on Linux from 2026-10-10T21:38:06.968975Z through
21:38:17.072135Z. Their summed wall duration was 9.9521 seconds, excluding source
preparation and controller observations. Python 3.12.14, Git 2.52.0 and PyYAML
6.0.3 were used. Canonical Core validation reported zero errors. No old full
suite or second experimental sample is implied by this archive.

The earlier acknowledged-write witness is already available through
[PR #50](https://github.com/Combo-AI-Coder/learning-os-core/pull/50). It cuts after
a successful Host response. This archive retains the distinct pre-response
cut; it does not duplicate those four files, refactor their helpers or add
another automatic CI experiment.

## Exact source and manual replay

The unchanged script requires `source/` beside it containing exactly the 267
tracked files of canonical Core commit
`83f08b016dd51a777ee91c0c4d77c0b85e046e4e`, tree
`1995f7807375044aa08873e309bee61e704abc3c`.
The source manifest records each path, mode, size and Git blob identity. That
public source is retained in the same repository's immutable history rather
than copied into this archive. Current main has additional files and must not
be substituted for the fixed source snapshot.

For a separately intended replay, use a clean local checkout containing that
commit and an empty disposable staging directory. Copy this archive there.
Verify the exact upstream commit/tree, export only its tracked files into the
staging archive's `source/` directory, and compare all source files and modes
with `evidence/source-manifest.json` before invoking the script. Also verify the
script SHA-256 against `ARCHIVE.json`: imports occur before the runner's later
manifest assertions, so those assertions are not a pre-import trust boundary.
Do not put a
`.git` directory, cache files or this archive itself inside `source/`.
No dependency installation, network access, real Host or private Instance is
part of the replay. Use already available Python, Git and PyYAML.

From the prepared staging archive directory, the original command is:

```sh
PYTHONDONTWRITEBYTECODE=1 python lost_ack_probe.py --output evidence/new-run
```

Run without `-O`. The output path must not exist and must be outside `source/`.
The script checks all 267 source blobs and the synthetic Core tree. Later
fixture commit IDs and timing may differ; semantic assertions, exact input
bytes and source-tree identity are the replay contract. This command is not
invoked by publication, a unittest wrapper or a new workflow.

## Boundaries

This is a scripted deterministic seam with shared fixture helpers, not blinded
model behavior, a global exactly-once guarantee, an in-flight Git crash,
power-loss safety or real-network acknowledgement loss. It does not cover the
non-durable branch, concurrent writers, Windows, shared admission/drain, large
history, generation acquisition, real deployment, learner benefit or teaching
acceptance. The independent Git oracle is a separate path within the harness,
not independent-provider audit.

Fixture Git calls have 30-second timeouts; each worker has a 120-second timeout.
Output acceptance is limited to 1 MiB per stream after process exit, not a hard
disk-write cap. Timeout kills the immediate child only; this run did not test
process-tree termination. Intentional exit bypasses provider cleanup, and its
disposable-run leftovers were retained for inspection rather than proved clean.
Keep all new replay artifacts within the explicitly selected disposable scope.

An empty caller response alone establishes neither persistence nor absence.
Actual readback is required. This archive changes no runtime, deployment pin,
permission, credential, gate, learner state or project acceptance.
