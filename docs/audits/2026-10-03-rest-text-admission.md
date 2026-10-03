# REST text-read admission

## Purpose

GitHubApiProvider text reads must reject malformed or oversized inputs rather than expose a weaker admission boundary than the local Git text reader. Transport JSON size limits do not independently limit decoded text bytes; permissive base64 decoding may silently discard corrupt input.

## Admission contract

1. Validate the requested path with the shared portable-path validator before repository lookup or any network call; require a file path rather than a root directory.
2. Resolve the requested ref to an exact commit and request content at that immutable commit.
3. Require an object describing a file with base64 encoding and an exact 40-character hexadecimal blob identity.
4. Accept GitHub's CR/LF line wrapping, but reject other non-base64 bytes and malformed encodings rather than silently discarding them. Compare the decoded bytes' canonical re-encoding to reject nonzero unused pad bits as well as alphabet/padding-placement errors.
5. Check both the encoded upper bound and actual decoded byte length against the text budget, then decode UTF-8 strictly.
6. Preserve empty files, valid Unicode, exact-budget inputs and immutable content addressing.

## Regression coverage

`tests/test_rest_text.py` exercises unsafe paths before network activity, decoded byte-limit enforcement independent of the JSON envelope, malformed base64 and SHA values, non-file objects, line wrapping, empty/exact-limit text and invalid UTF-8. The content-pin regression in `tests/test_runtime_adapter.py` uses the documented file-response shape and forbids a mutable ref in the content request.

## Compatibility and limits

The official GitHub Contents API response includes type, encoding and sha; its base64 examples contain line breaks. See https://docs.github.com/en/rest/repos/contents .

The Contents API can transparently dereference a symlink. A file-shaped response therefore does not prove the requested path is a regular Git-tree entry. This admission boundary validates format and size, not cryptographic authenticity or complete equivalence with GitCliProvider. Exact branch-head CAS remains unsupported, and this reader grants no write authority.
