"""Deterministically replay retained consumer-selected synthetic read trajectories.

No model, real repository provider, new product selector or learner is invoked.
The order checks describe retained observations, not a general read algorithm.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.answer_closure_fixture import make_provider, open_host, state_of
from tests.test_runtime_broker import CHECKPOINT_PATH, RUNTIME_PATH, locator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/core34-context-selection'
MANIFEST_SHA256 = 'd48e0bce31961436f50a727cd9da4a47b646a5079e2b5f736b55804d62c1b8bf'
DISTRACTOR = 'evidence/evi_20261007_arrangement_01.yaml'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(name):
    return json.loads((FIXTURES / name).read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_artifacts(directory=FIXTURES):
    directory = Path(directory)
    manifest_raw = (directory / 'publication-manifest.json').read_bytes()
    require(digest(manifest_raw) == MANIFEST_SHA256, 'publication manifest changed')
    manifest = json.loads(manifest_raw)
    require({p.name for p in directory.iterdir()} == set(manifest['files']) | {'publication-manifest.json'},
            'publication inventory changed')
    for name, item in manifest['files'].items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == item['sha256'],
                'publication bytes changed: ' + name)
    return manifest


def verify_dependencies(root=ROOT):
    for name, expected in read('publication-manifest.json')['dependencies'].items():
        path = Path(root) / name
        require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == expected,
                'q03 source-state dependency changed: ' + name)


def assert_unchanged(before, after):
    require(before == after, 'read trajectory changed state or source occurrence')


def validate_trajectory(rows, case):
    spec = read('publication-manifest.json')['cases'][case]
    require(isinstance(rows, list) and len(rows) == spec['request_count'],
            'missing, duplicate or surplus receipts')
    operations = ['read_learning_context', 'discover_learning_evidence', 'read_learning_context']
    discovered_owners = set()
    for index, row in enumerate(rows, 1):
        require(row.get('index') == index, 'receipt order changed')
        try:
            raw = base64.b64decode(row['request_raw_base64'], validate=True)
            request = json.loads(raw)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError('invalid retained request bytes') from exc
        require(digest(raw) == row.get('request_sha256') and raw.decode('utf-8') == row.get('request_raw_utf8')
                and request == row.get('request'), 'raw/parsed request bindings changed')
        require(isinstance(request, dict) and set(request) == {'operation', 'arguments'},
                'request envelope changed')
        require(request['operation'] == operations[index - 1], 'retained operation order changed')
        result = row.get('response', {})
        require(result.get('ok') is True and result.get('surface_version') == 'v4'
                and result.get('operation') == request['operation'], 'retained host result failed or changed')
        require(row.get('state_unchanged') is True and row.get('changed_paths') == [],
                'retained state changed')
        require(row.get('omitted_fields') == ['request_file'] and 'request_file' not in row,
                'receipt omission disclosure changed')
        if index == 1:
            args = request['arguments']
            paths = args['required_paths'] + args.get('optional_paths', [])
            require(CHECKPOINT_PATH in paths, 'initial checkpoint was not requested')
            require(result['result']['missing_optional'] == spec['missing_optional'],
                    'optional absence changed')
            require({d['path'] for d in result['result']['documents']} == {CHECKPOINT_PATH},
                    'initial returned documents changed')
        elif index == 2:
            require(request['arguments'] == {}, 'discovery arguments changed')
            evidence_paths = [d['path'] for d in result['result']['evidence']]
            require(evidence_paths == spec['evidence_paths'], 'discovered Evidence omitted or changed')
            discovered_owners = {d['path'] for d in result['result']['knowledge_owners'] if d['state'] == 'present'}
        else:
            args = request['arguments']
            requested = set(args['required_paths'] + args.get('optional_paths', []))
            require(requested and requested == discovered_owners,
                    'Knowledge locator not obtained from preceding discovery')
            require({d['path'] for d in result['result']['documents']} == requested,
                    'Knowledge content was not returned')
    return rows


def verify_review_bindings(review):
    manifest = read('publication-manifest.json')
    mappings = {'prospective-plan.json': 'prospective-plan.json',
                'prospective-freeze.json': 'prospective-freeze.json',
                'method-review-final-prospective.json': 'prospective-method-review.json',
                'mechanical-exact-replay.json': 'mechanical-exact-replay.json',
                'source-state-bindings.json': 'source-state-bindings.json'}
    for original, published in mappings.items():
        require(review.get('bindings', {}).get(original) == digest((FIXTURES / published).read_bytes()),
                'review artifact binding changed')
    rows = review.get('rows', [])
    require(len(rows) == len(manifest['cases'])
            and {r.get('condition') for r in rows} == {s['original_condition'] for s in manifest['cases'].values()},
            'review coverage changed')
    for case, spec in manifest['cases'].items():
        row = next(r for r in rows if r['condition'] == spec['original_condition'])
        for key in ('handle', 'first_response_sha256', 'packet_sha256', 'submitted_task_sha256',
                    'sealed_sha256', 'provider_state_sha256'):
            require(row.get(key) == spec[key], 'review case binding changed: ' + key)
        require(row.get('original_task_sha256') == spec['task_sha256'], 'review task binding changed')
        receipts = read(case + '-trajectory.json')
        require(row.get('request_receipts_sha256') == {f"{r['index']:03}.json": r['source_log_sha256'] for r in receipts},
                'review original receipt bindings changed')
        require(row.get('operation_trace') == [{'index': r['index'], 'request_sha256': r['request_sha256'],
                'operation': r['request']['operation'], 'started_at': r['at_started'], 'finished_at': r['at_finished']}
                for r in receipts], 'review operation trace changed')
        require(spec['first_response_sha256'] == digest((FIXTURES / (case + '-response.json')).read_bytes())
                and spec['packet_sha256'] == digest((FIXTURES / 'common-packet.json').read_bytes()),
                'first input/output binding changed')


def build_worlds():
    verify_dependencies()
    originals = {name: json.loads((ROOT / f'tests/fixtures/core34-answer-closure/{name}-post-state.json').read_bytes())
                 for name in ('elm', 'pine')}
    baseline = originals['elm']
    record = read('irrelevant-preparation.json')
    with tempfile.TemporaryDirectory() as temporary:
        provider = make_provider(Path(temporary), baseline)
        with ReferenceLearningHost.open(provider=provider, locator_source=locator(),
                branch_runtime_path=RUNTIME_PATH,
                policy=RuntimeCapabilityPolicy(readable_roots=('learner/knowledge', 'evidence',
                    'topics/synthetic/subtopics/unit'), writable_roots=('evidence',)),
                write_admission=DeploymentWriteGate(), expected_generation=3) as host:
            result = host.invoke(record['request'])
        irrelevant = state_of(provider)
        require(result == record['response'] and json.loads(encoded(provider.calls)) == record['provider_calls'],
                'irrelevant preparation result/trace changed')
    changed = {p for p in set(baseline['docs']) | set(irrelevant['docs'])
               if baseline['docs'].get(p) != irrelevant['docs'].get(p)}
    require(changed == {DISTRACTOR}, 'irrelevant world changed more than one record')
    for key in ('docs', 'blobs'):
        require(all(irrelevant[key].get(p) == value for p, value in baseline[key].items()),
                'irrelevant world changed original ' + key)
    require(irrelevant['instance_head'] == baseline['instance_head']
            and irrelevant['snapshot_extra_paths'] == baseline['snapshot_extra_paths'],
            'irrelevant world authority or inventory changed')
    worlds = {'baseline': baseline, 'relevant': originals['pine'], 'irrelevant': irrelevant}
    for case, state in worlds.items():
        require(digest(encoded(state)) == read('publication-manifest.json')['cases'][case]['provider_state_sha256'],
                'prepared world hash changed')
    require(digest(encoded(baseline)) == record['before_state_sha256']
            and digest(encoded(irrelevant)) == record['after_state_sha256'], 'preparation state binding changed')
    return worlds


def replay():
    verify_artifacts()
    verify_review_bindings(read('semantic-method-review.json'))
    require(encoded(packet()) == (FIXTURES / 'common-packet.json').read_bytes(), 'common packet changed')
    worlds = build_worlds()
    retained = {r['condition']: r for r in read('mechanical-exact-replay.json')['rows']}
    output = []
    for case, state in worlds.items():
        spec = read('publication-manifest.json')['cases'][case]
        rows = validate_trajectory(read(case + '-trajectory.json'), case)
        original_replay = retained[spec['original_condition']]
        require(len(original_replay['receipts']) == len(rows), 'original replay coverage changed')
        receipts = []
        for row, replay_row in zip(rows, original_replay['receipts']):
            with tempfile.TemporaryDirectory() as temporary:
                provider = make_provider(Path(temporary), state)
                with open_host(provider) as host:
                    # Parse the original exact byte string, never repair a request.
                    result = host.invoke(json.loads(base64.b64decode(row['request_raw_base64'])))
                assert_unchanged(state, state_of(provider))
                require(result == row['response'] == replay_row['actual_response']
                        and json.loads(encoded(provider.calls)) == row['provider_calls'] == replay_row['provider_calls'],
                        'actual replay result/provider trace changed')
                require(row['index'] == replay_row['index'] and row['request_sha256'] == replay_row['request_sha256'],
                        'original replay request binding changed')
                receipts.append({'index': row['index'], 'request_sha256': row['request_sha256'],
                                 'response': result, 'state_unchanged': True})
        output.append({'case': case, 'receipts': receipts, 'state_unchanged': True})
    return output


def export_replay(destination):
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents or destination.exists():
        raise ValueError('output must be a new directory outside the Core snapshot')
    result = replay()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'common-packet.json').write_bytes(encoded(packet()))
    (destination / 'mechanical-replay.json').write_bytes(encoded(result))


# Historical interface vocabulary is frozen independently of later live policy.
# This is not an algorithm handed to the consumers; it reproduces their input.
PACKET_FIELDS = {'request': 'Continue where we left off.',
 'session_binding': {'topic': 'synthetic', 'subtopic': 'unit', 'role': 'main'},
 'host_read_interface': {'surface_version': 'v4',
                         'request_shape': {'operation': 'one named operation',
                                           'arguments': 'exact argument mapping'},
                         'operations': {'read_learning_context': {'required_paths': 'array of '
                                                                                    'canonical '
                                                                                    'document '
                                                                                    'paths, at '
                                                                                    'least one '
                                                                                    'path across '
                                                                                    'both arrays',
                                                                  'optional_paths': 'optional '
                                                                                    'array; absent '
                                                                                    'entries are '
                                                                                    'returned as '
                                                                                    'missing_optional; '
                                                                                    'any required '
                                                                                    'missing path '
                                                                                    'rejects the '
                                                                                    'complete '
                                                                                    'operation',
                                                                  'limit': '32 combined paths per '
                                                                           'call, unique across '
                                                                           'both arrays; no '
                                                                           'truncation'},
                                        'discover_learning_evidence': {'arguments': {},
                                                                       'meaning': 'Returns all '
                                                                                  'matching typed '
                                                                                  'Evidence for '
                                                                                  'the bound '
                                                                                  'Topic/Subtopic '
                                                                                  'with original '
                                                                                  'content and '
                                                                                  'same-snapshot '
                                                                                  'Knowledge '
                                                                                  'owner/reference '
                                                                                  'metadata. '
                                                                                  'Unreferenced '
                                                                                  'does not mean '
                                                                                  'unprocessed; '
                                                                                  'path order is '
                                                                                  'not event-time '
                                                                                  'order.',
                                                                       'limits': '128 inventory '
                                                                                 'candidates;16 '
                                                                                 'matching '
                                                                                 'records;16 '
                                                                                 'distinct '
                                                                                 'Knowledge '
                                                                                 'owners;8 '
                                                                                 'targets/record '
                                                                                 'and64 '
                                                                                 'total;64KiB per '
                                                                                 'document '
                                                                                 'and256KiB '
                                                                                 'scanned '
                                                                                 'Evidence/read '
                                                                                 'Knowledge; '
                                                                                 'over-limit fails '
                                                                                 'whole operation, '
                                                                                 'no pagination'}},
                         'readable_roots': ['learner/knowledge',
                                            'evidence',
                                            'topics/synthetic/subtopics/unit'],
                         'writable_roots': [],
                         'path_vocabulary_note': 'Canonical path templates are interface '
                                                 'vocabulary, not a list of existing documents. '
                                                 'Topic IDs and Domain IDs are distinct concepts; '
                                                 'do not assume they match. No concrete Evidence '
                                                 'IDs or Knowledge owner have been supplied.',
                         'failure_contract': {'resolution_failed': 'request/host resolution failed',
                                              'guard_rejected': 'guard rejected; this alone does '
                                                                'not distinguish absent, '
                                                                'unauthorized or over-budget data',
                                              'cas_conflict': 'freshness conflict; retryable'},
                         'transport': 'The handoff provides an opaque handle and host_bridge.py '
                                      'command. Submit your chosen request through it; stdout is '
                                      'the actual result. Do not read provider, bridge '
                                      'implementation, plan, other trajectories or source '
                                      'repository directly.'},
 'response_contract': {'reply': 'The next concise learner-facing continuation grounded only in '
                                'actual retrieved facts, or a proportionate clarification if '
                                'needed',
                       'basis': 'Short checkable factual basis identifying which retrieved facts '
                                'were used; no hidden reasoning',
                       'acquisition_summary': 'What you actually retrieved, any important '
                                              'missing/uncertain facts, and whether your basis is '
                                              'checkpoint-only or independently verified against '
                                              'Knowledge/Evidence. Do not claim operations you did '
                                              'not execute.'}}


def packet(protocols=None):
    fields = copy.deepcopy(PACKET_FIELDS)
    return {'request': fields['request'], 'session_binding': fields['session_binding'],
            'product_protocols': copy.deepcopy(protocols if protocols is not None else
                read('common-packet.json')['product_protocols']),
            'host_read_interface': fields['host_read_interface'],
            'response_contract': fields['response_contract']}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    export_replay(parser.parse_args().output)
