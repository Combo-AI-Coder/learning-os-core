# One synthetic first-use consumer sample

These are immutable, public-safe synthetic packets and verbatim consumer
responses collected on 2026-10-17. `sha256.json` identifies their bytes.
`frozen-plan.md` was frozen before either consumer dispatch; `freeze.json`
identifies the implementation and producer packet used for that run.

- A was a fresh native consumer context given only `a-packet.json`.
- The learner response was a predeclared script, not a human observation.
- After a real checkpoint and closing A's Host, a new Python process opened a
  read-only Host from bindings/locator/topic/unit and produced `b-packet.json`.
- B was a second native context given only that packet, without A's transcript,
  session/capability, receipt or Evidence ID. The same model family was used;
  this is context isolation, not independent-provider validation.
- An independent-context reviewer graded the pair against the original rubric.
  See `docs/evaluations/core19-first-use-results.md` for scope and caveats.

The original packets retain fresh reader `version_token` blob identities.
The frozen plan's phrase "no token" was too broad: no producer capability or
session token was transferred, but the packet is not literally token-free.
This deviation is disclosed; neither packet nor frozen plan was rewritten.

`runtime-result.json` preserves the original controller output. Its
`accepted_initial_transitions` and `no_evidence_created` fields were controller
constants, not independently measured counters. Use the actual Git assertions
in `test_first_use.py` for transition count, and B's actual discovery result for
empty Evidence. The checkpoint result and fresh read-only rejection/head check
were actual Host results.

The source hashes predate the review fix removing an unused Topic Progress
`current.subtopic` field and normalizing completed `prepare` results. Neither
field was in consumer input. The subsequent concurrent-close/drain fix also
predates the final regression, without changing consumer input semantics. The final-code regression mechanically replays the
checkpoint and compares all recovered document semantics with the preserved B
packet (except timestamps), plus the exact Evidence response. Replay does not
add a new consumer sample. Frozen artifacts contain no real learner data.
