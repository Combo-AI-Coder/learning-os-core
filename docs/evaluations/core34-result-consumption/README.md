# Core34 unittest-result text aid

Optional source-only technical preparation for [Core issue34](https://github.com/Combo-AI-Coder/learning-os-core/issues/34). It is not integrated product code, a new acceptance gate, execution authority or a Core validation result. Source identity is frozen in `SOURCE-IDENTITY.json`; changed source/base requires fresh owner interpretation.

## Assets and purpose

- `reconcile_report.py`: compare one supplied verbose unittest block against source-bound expected IDs and receipt assertions, without importing Core or executing tests, Git, network or process operations.
- `EXPECTED-TESTS.json`: preserve 958 static IDs, source/method hashes and skip sites; distinguish 41 additions after the historical 917-ID component and two changed existing methods. The 316-leaf source identity and AST extraction are not dynamic discovery. Inheritance, runtime assignments, imports and platform behavior may differ.
- `RECEIPT.template.json`: empty-fact template for independently verified native evidence. Unknowns must not be guessed.
- `check_fixtures.py`: retained 33-case synthetic helper/CLI recipe for future authorized maintenance; running it overwrites its own fixture/result files.
- `FIXTURE-RESULTS.json`: unchanged historical 33-case helper report bound to the retained helper/inventory hashes. It was not rerun for this publication and is not Core-test evidence.
- `fixtures/synthetic-complete-linux.log`: explicitly invented complete 958-row example demonstrating internal consistency, not real execution.
- `fixtures/synthetic-old917.log`: explicitly invented historical-count example demonstrating missing newer IDs.
- `fixtures/synthetic-receipt.json`: deliberately fabricated receipt facts paired with those examples; no native run or checkout exists.
- `fixtures/malformed-receipt.json`: invalid-shape input example.
- `SOURCE-IDENTITY.json` and `MANIFEST.json` / `MANIFEST.sha256`: technical source binding and exact file-byte integrity.

## Use only after native-evidence verification

First independently verify the actual native run, checkout commit/tree, source/base relation, complete original commands, exits and all relevant failure evidence. Then extract one entire ordinary bare unittest verbose block and preserve its relationship to the original stdout/stderr. Never select a success-looking block while omitting other failures, combine rounds or invent missing lines. Fill the receipt only with independently verified facts. A different merge commit requires independent proof of the same checkout tree.

Optional consumption command:

`python reconcile_report.py --inventory EXPECTED-TESTS.json --receipt actual-receipt.json --log actual-unittest.log`

Exit 0 means only consistency of recognized supplied text/assertions. Invented logs can pass. It proves neither real execution nor complete native-log success. Exit 1 identifies a discrepancy or coverage/skip question for the existing owner, not a Core failure judgment or automatic retry permission. Exit 2 is an input problem.

The parser expects one unprefixed single-line verbose result per test, one count summary and one success summary. CI timestamps, ANSI sequences, multiline descriptions, qualified module names, dynamic IDs and interleaved input are outside this format. **Prefixed failure lines appended to a complete recognized block may be ignored.** When format or extraction completeness is uncertain, rely on native evidence rather than a successful helper result.

Only the exact Windows process-tree test skip is automatically expected on Linux. All other skips require owner interpretation, not automatic failure. Windows cannot borrow Linux skip coverage. Do not modify product code or privileges just to satisfy this static inventory.

## Limits

No helper, synthetic fixture or Core command was run to publish these retained source assets. They do not establish full Core validation, Linux/Windows coverage, hosted synthetic CLI results, cleanup, review, deployment, real learner use or acceptance. No existing attempt budget is extended. The original Core34 owner retains those obligations. Use existing native results and applicable verification rules; this optional aid grants no follow-on execution or integration permission.
