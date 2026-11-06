"""Compare one supplied unittest verbose log to frozen IDs; no target execution.

Exit 0: internally consistent, complete reported success; not execution proof.
Exit 1: discrepancy, failure, missing coverage or skip requiring human/owner review.
Exit 2: input problem. Output never grants execution, retry, review or acceptance.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

LIMIT = 64 * 1024 * 1024
TEST = re.compile(r'^(test\w*) \(([^()]+)\) \.\.\. (ok|FAIL|ERROR|skipped .+|expected failure|unexpected success)$')
SUMMARY = re.compile(r'^Ran (\d+) tests? in [0-9.]+s$')
OK = re.compile(r'^OK(?: \(skipped=(\d+)\))?$')
WINDOWS_ONLY = 'test_runtime_adapter.GitCliProviderTests.test_windows_cleanup_kills_child_after_leader_exit'

def analyze(inventory, receipt, log):
    if not isinstance(inventory, dict) or not isinstance(receipt, dict):
        raise ValueError('inventory and receipt must be objects')
    if type(inventory.get('expected_id_count')) is not int or inventory['expected_id_count'] < 1:
        raise ValueError('invalid inventory count')
    if not isinstance(inventory.get('tests'), list) or not all(isinstance(r, dict) and isinstance(r.get('id'), str) for r in inventory['tests']):
        raise ValueError('invalid inventory test list')
    problems = []
    expected = {row['id'] for row in inventory['tests']}
    if len(expected) != inventory['expected_id_count']:
        problems.append('inventory_duplicate_or_count_mismatch')
    for key in ('source_commit', 'source_tree', 'base_commit'):
        if receipt.get(key) != inventory.get(key):
            problems.append('identity_mismatch:' + key)
    if receipt.get('checked_out_tree') != inventory['source_tree']:
        problems.append('checked_out_tree_mismatch_or_missing')
    if not re.fullmatch(r'[0-9a-f]{40}', str(receipt.get('checked_out_commit', ''))):
        problems.append('checked_out_commit_missing_or_malformed')
    if receipt.get('platform') not in ('linux', 'windows'):
        problems.append('unknown_platform')
    if receipt.get('python_major_minor') != '3.12':
        problems.append('python_baseline_mismatch_or_missing')
    if receipt.get('test_command') != 'python -m unittest discover -s tests -v':
        problems.append('test_command_mismatch_or_missing')
    for key in ('core_validator_rc', 'unittest_rc'):
        if type(receipt.get(key)) is not int or receipt[key] != 0:
            problems.append('nonzero_or_missing:' + key)
    if not receipt.get('native_run_ref') or not receipt.get('identity_evidence_ref'):
        problems.append('native_evidence_reference_missing')
    if 'NETLINK_ROUTE' in log or 'COMMAND_NOT_STARTED' in log:
        problems.append('setup_refusal_or_not_started_marker')
    observed, summaries, ok_summaries, unknown_ids, unparsed_test_lines = [], [], [], [], []
    for line in log.splitlines():
        line = line.rstrip('\r')
        match = TEST.fullmatch(line)
        if match:
            method, test_id, status = match.groups()
            # Python's module may be qualified as tests.test_x by an explicit loader.
            # Preserve that different launch shape as unknown rather than silently aliasing it.
            observed.append((test_id, status))
            if not test_id.endswith('.' + method):
                problems.append('method_id_mismatch')
            if test_id not in expected:
                unknown_ids.append(test_id)
        elif line.startswith('test') and ' ... ' in line:
            unparsed_test_lines.append(line[:250])
        if SUMMARY.fullmatch(line):
            summaries.append(int(SUMMARY.fullmatch(line).group(1)))
        if OK.fullmatch(line):
            ok_summaries.append(int(OK.fullmatch(line).group(1) or 0))
        if line.startswith(('FAILED (', 'FAILED', 'ERROR:', 'FAIL:')):
            problems.append('failure_marker')
    counts = Counter(i for i, _ in observed)
    duplicates = sorted(i for i, n in counts.items() if n != 1)
    missing = sorted(expected - set(counts))
    statuses = Counter(status.split(' ', 1)[0] for _, status in observed)
    skipped = [{'id': i, 'status': status} for i, status in observed if status.startswith('skipped ')]
    skip_review = [r for r in skipped if not (receipt.get('platform') == 'linux' and r['id'] == WINDOWS_ONLY and r['status'] == "skipped 'Windows process-tree behavior'")]
    if missing: problems.append('missing_test_ids')
    if duplicates: problems.append('duplicate_test_ids')
    if unknown_ids: problems.append('unknown_test_ids')
    if unparsed_test_lines: problems.append('unparsed_test_lines')
    if summaries != [len(expected)]: problems.append('summary_count_missing_multiple_or_mismatch')
    if ok_summaries != [len(skipped)]: problems.append('success_summary_missing_multiple_or_skip_mismatch')
    if any(s not in ('ok',) and not s.startswith('skipped ') for _, s in observed): problems.append('non_success_test_status')
    if skip_review: problems.append('skip_requires_owner_review')
    if receipt.get('platform') == 'windows' and any(r['id'] == WINDOWS_ONLY for r in skipped): problems.append('windows_cleanup_coverage_missing')
    if receipt.get('platform') == 'linux' and (WINDOWS_ONLY, "skipped 'Windows process-tree behavior'") not in observed:
        problems.append('linux_windows_only_skip_missing_or_changed')
    return {
        'verdict': 'discrepancy_or_incomplete' if problems else 'internally_consistent_complete_reported_success',
        'execution_authenticity': 'NOT established by this text checker; verify native run/checkout/command evidence independently',
        'does_not_establish': ['cleanup', 'no descendants', 'Windows evidence from Linux', 'hosted optional synthetic CLI steps', 'review', 'deployment', 'real learner use', 'acceptance', 'authority to retry'],
        'problems': sorted(set(problems)), 'expected_count': len(expected), 'observed_result_lines': len(observed), 'missing_ids': missing, 'duplicate_ids': duplicates, 'unknown_ids': sorted(set(unknown_ids)), 'unparsed_test_lines': unparsed_test_lines, 'reported_counts': summaries, 'statuses': dict(statuses), 'skipped': skipped, 'skips_requiring_owner_review': skip_review,
        'log_sha256': hashlib.sha256(log.encode('utf-8')).hexdigest(),
    }

def read_text(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size > LIMIT:
        raise ValueError('missing, non-file or oversized input')
    return p.read_bytes().decode('utf-8')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--log', required=True, help='One extracted raw unittest verbose log without CI timestamps/prefixes')
    args = parser.parse_args()
    try:
        result = analyze(json.loads(read_text(args.inventory)), json.loads(read_text(args.receipt)), read_text(args.log))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'verdict': 'input_error', 'category': type(error).__name__, 'execution_authenticity': 'not established'}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if not result['problems'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
