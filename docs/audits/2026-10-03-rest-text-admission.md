# REST text-read admission audit

Baseline: `d1ab86a4d0192bcdbf5bed84405f07af401d848c`.
Scope: GitHubApiProvider.read_text; no deployment, write-authority, learner-state or intake-policy changes.

The Git CLI text path already checked portable paths and decoded byte size. The REST text path accepted unsafe path strings before lookup, permissively discarded non-base64 bytes, admitted non-file-shaped synthetic responses, and accepted a merely nonempty blob identity. Its transport envelope limit did not independently enforce the decoded text limit.

Seven new offline tests yielded 16 failing subtests on baseline. The focused suite passes after adding pre-network portable-path admission, explicit file/base64 response checks, exact blob-SHA syntax, strict base64 with only CR/LF line wrapping, and encoded/decoded size limits. Empty files, valid UTF-8, exact-size inputs and commit-pinned content requests remain supported.

The first full 510-test run found one old mock response omitting GitHub's documented `type: file`. That fixture is corrected without weakening production validation or its exact-commit assertion. The failed run is not counted as passing; final full reruns and hosted CI are recorded on the PR.

API contract source: https://docs.github.com/en/rest/repos/contents . The official response includes type, encoding and sha, and base64 examples contain line breaks. This endpoint can transparently dereference a symlink, so checking response type is NOT proof of a regular Git-tree entry. This patch validates admission/format, not cryptographic authenticity or full GitCliProvider equivalence; exact branch-head CAS remains unsupported.

Earlier independent-review attempts produced no usable report (timeout, rejected CLI effort option, then denied read tool/empty output). They are not approval. This report is self-review and red/green evidence; PR review and product acceptance remain separate. Core issue #19 still needs learner-effect evidence and must not be closed by infrastructure tests; issue #21 already had archive/write-admission repairs before this additional read-path work.
