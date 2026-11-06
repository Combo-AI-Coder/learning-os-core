"""Only helper/JSON/string fixtures; never imports or runs Core."""
from copy import deepcopy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from reconcile_report import analyze, main as cli_main, WINDOWS_ONLY

ROOT = Path(__file__).resolve().parent
STARTED = time.monotonic()
inv = json.loads((ROOT / 'EXPECTED-TESTS.json').read_text())
ids = [r['id'] for r in inv['tests']]
assert len(ids) == 958 and inv['historical_component_id_count'] == 917
assert len(inv['added_after_fingerprint']) == 41 and WINDOWS_ONLY in ids
receipt = {key: inv[key] for key in ('source_commit', 'source_tree', 'base_commit')}
receipt.update(checked_out_commit=inv['source_commit'], checked_out_tree=inv['source_tree'], platform='linux', python_major_minor='3.12', core_validator_rc=0, unittest_rc=0, test_command='python -m unittest discover -s tests -v', native_run_ref='FIXTURE ONLY - no native run', identity_evidence_ref='FIXTURE ONLY - no actual checkout')

def line(i, status='ok'):
    return f"{i.rsplit('.', 1)[1]} ({i}) ... {status}"

def log_for(selected=ids, platform='linux', replacements=None):
    replacements = replacements or {}
    status = {i: 'ok' for i in selected}
    if platform == 'linux' and WINDOWS_ONLY in status:
        status[WINDOWS_ONLY] = "skipped 'Windows process-tree behavior'"
    status.update(replacements)
    skipped = sum(s.startswith('skipped ') for s in status.values())
    lines = ['SYNTHETIC FIXTURE ONLY; no Core commands were executed.'] + [line(i, status[i]) for i in selected]
    lines += ['', f'Ran {len(selected)} tests in 1.000s', '', f'OK (skipped={skipped})' if skipped else 'OK']
    return '\n'.join(lines) + '\n'

linux_log = log_for()
rows = []
def check(name, log=linux_log, changes=None, require=None, clean=False):
    r = deepcopy(receipt)
    r.update(changes or {})
    result = analyze(inv, r, log)
    ok = not result['problems'] if clean else require in result['problems']
    rows.append({'fixture': name, 'assertion_passed': ok, 'expected_problem': require, 'actual_problems': result['problems'], 'observed_count': result['observed_result_lines'], 'missing_count': len(result['missing_ids']), 'evidence_class': 'synthetic helper fixture, not Core validation'})
    assert ok, (name, result['problems'])

check('complete_linux_one_expected_skip', clean=True)
check('complete_windows_no_skips', log_for(platform='windows'), {'platform': 'windows'}, clean=True)
old = [i for i in ids if i not in set(inv['added_after_fingerprint'])]
check('historical_component_917_cannot_cover_union', log_for(old), require='missing_test_ids')
check('summary_only_958_is_insufficient', 'Ran 958 tests in 1.000s\nOK\n', require='missing_test_ids')
check('same_count_duplicate_replaces_missing', log_for([ids[0], *ids[:-1]]), require='duplicate_test_ids')
check('incomplete_log_with_correct_summary', linux_log.replace(line(ids[0]) + '\n', ''), require='missing_test_ids')
check('old_component_commit', changes={'source_commit': '292dde1fbf5a515660a93146f78051565b29f98a'}, require='identity_mismatch:source_commit')
check('old_local_attempt_not_native_head', changes={'source_commit': '41140035d6d544073cc00dd2719a4bcde167e952'}, require='identity_mismatch:source_commit')
check('changed_base', changes={'base_commit': 'f' * 40}, require='identity_mismatch:base_commit')
check('checked_out_merge_tree_changed', changes={'checked_out_tree': 'f' * 40}, require='checked_out_tree_mismatch_or_missing')
check('same_tree_distinct_native_merge_commit', changes={'checked_out_commit': 'e' * 40}, clean=True)
check('missing_checkout_identity', changes={'checked_out_commit': None}, require='checked_out_commit_missing_or_malformed')
check('missing_core_exit', changes={'core_validator_rc': None}, require='nonzero_or_missing:core_validator_rc')
check('boolean_exit_is_not_zero', changes={'unittest_rc': False}, require='nonzero_or_missing:unittest_rc')
check('focused_command_is_not_full_discovery', changes={'test_command': 'python -m unittest test_runtime_broker'}, require='test_command_mismatch_or_missing')
check('python_baseline_not_met', changes={'python_major_minor': '3.11'}, require='python_baseline_mismatch_or_missing')
check('no_native_evidence_ref', changes={'native_run_ref': None}, require='native_evidence_reference_missing')
check('unexpected_skip_requires_disposition', log_for(replacements={ids[0]: "skipped 'disabled'"}), require='skip_requires_owner_review')
check('windows_cleanup_test_skipped', linux_log, {'platform': 'windows'}, require='windows_cleanup_coverage_missing')
check('linux_expected_skip_removed', log_for(platform='windows'), require='linux_windows_only_skip_missing_or_changed')
check('failed_method_cannot_hide_behind_OK', log_for(replacements={ids[0]: 'FAIL'}), require='non_success_test_status')
check('expected_failure_not_new_success', log_for(replacements={ids[0]: 'expected failure'}), require='non_success_test_status')
check('multiple_runs_cannot_concatenate', linux_log + linux_log, require='summary_count_missing_multiple_or_mismatch')
check('CI_prefix_requires_explicit_extract', '\n'.join('2026-11-06T00:00:00Z ' + l for l in linux_log.splitlines()), require='missing_test_ids')
check('setup_refusal_zero_target_commands', 'bwrap: loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted\n', {'core_validator_rc': None, 'unittest_rc': None}, require='setup_refusal_or_not_started_marker')
check('malformed_id_label', linux_log.replace(line(ids[0]), 'test_wrong (' + ids[0] + ') ... ok'), require='method_id_mismatch')
check('unknown_test_instead_of_expected', linux_log.replace(ids[0], 'test_unknown.Unknown.test_unknown').replace(ids[0].rsplit('.', 1)[1], 'test_unknown'), require='unknown_test_ids')
check('skip_summary_count_mismatch', linux_log.replace('OK (skipped=1)', 'OK (skipped=2)'), require='success_summary_missing_multiple_or_skip_mismatch')
check('embedded_failure_summary', linux_log + 'FAILED (failures=1)\n', require='failure_marker')

(ROOT / 'fixtures/synthetic-complete-linux.log').write_text(linux_log)
(ROOT / 'fixtures/synthetic-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
(ROOT / 'fixtures/synthetic-old917.log').write_text(log_for(old))
(ROOT / 'fixtures/malformed-receipt.json').write_text('[]\n')
for name, log_name, receipt_name, expected_exit in [
    ('CLI_complete_synthetic', 'synthetic-complete-linux.log', 'synthetic-receipt.json', 0),
    ('CLI_old917_rejected', 'synthetic-old917.log', 'synthetic-receipt.json', 1),
    ('CLI_missing_input', 'nonexistent.log', 'synthetic-receipt.json', 2),
    ('CLI_malformed_receipt', 'synthetic-complete-linux.log', 'malformed-receipt.json', 2),
]:
    previous_argv = sys.argv
    buffer = io.StringIO()
    try:
        sys.argv = ['reconcile_report.py', '--inventory', str(ROOT / 'EXPECTED-TESTS.json'), '--receipt', str(ROOT / 'fixtures' / receipt_name), '--log', str(ROOT / 'fixtures' / log_name)]
        with contextlib.redirect_stdout(buffer):
            code = cli_main()
    finally:
        sys.argv = previous_argv
    payload = json.loads(buffer.getvalue())
    passed = code == expected_exit
    rows.append({'fixture': name, 'assertion_passed': passed, 'expected_exit': expected_exit, 'actual_exit': code, 'verdict': payload['verdict'], 'evidence_class': 'synthetic helper CLI fixture in-process; not Core validation'})
    assert passed, (name, code)
out = {'scope': 'standalone text consumer only; no target tests, imports, subprocesses, network, sockets or probes', 'fixture_cases': len(rows), 'all_assertions_passed': all(r['assertion_passed'] for r in rows), 'elapsed_seconds': time.monotonic() - STARTED, 'inventory_sha256': hashlib.sha256((ROOT / 'EXPECTED-TESTS.json').read_bytes()).hexdigest(), 'helper_sha256': hashlib.sha256((ROOT / 'reconcile_report.py').read_bytes()).hexdigest(), 'results': rows}
(ROOT / 'FIXTURE-RESULTS.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({k: out[k] for k in ('scope', 'fixture_cases', 'all_assertions_passed', 'elapsed_seconds')}))
