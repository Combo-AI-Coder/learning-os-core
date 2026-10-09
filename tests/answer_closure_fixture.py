"""Replay retained fictional q03 trajectories through existing host operations.

No model/provider invocation, product transport, or real learner state is used.
Historical policy/input bytes and every original request remain unchanged.
"""
import base64
import copy
import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from unittest import mock

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    BrokerProvider, CHECKPOINT_BLOB, CHECKPOINT_PATH, READ_PATH, RUNTIME_PATH,
    checkpoint_progress, locator,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/core34-answer-closure'
MANIFEST_SHA256 = '0c94ad9d99fd879a4db2137cfd083728c48f096713343f2669b367bc357934f6'
WRITES = {'create_evidence', 'reconcile_knowledge', 'save_learning_checkpoint'}
READS = {'read_learning_context', 'discover_learning_evidence'}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(name):
    return json.loads((FIXTURES / name).read_bytes())


def verify_artifacts(directory=FIXTURES):
    directory = Path(directory)
    raw = (directory / 'publication-manifest.json').read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError('publication manifest changed')
    manifest = json.loads(raw)
    expected = set(manifest['files']) | {'publication-manifest.json'}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('frozen artifact inventory changed')
    for name, item in manifest['files'].items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != item['sha256']:
            raise ValueError('frozen artifact bytes changed: ' + name)
    return manifest


def validate_trajectory(rows, case, stage):
    """Protect this retained receipt, not a general pedagogical grading rule."""
    spec = read('publication-manifest.json')['cases'][case]
    if not isinstance(rows, list) or len(rows) != spec[stage + '_request_count']:
        raise ValueError('missing or surplus request receipts')
    seen_writes = set()
    last_write = 0
    reads = {}
    for index, row in enumerate(rows, 1):
        if row.get('index') != index:
            raise ValueError('request receipt order or uniqueness changed')
        try:
            raw = base64.b64decode(row['request_raw_base64'], validate=True)
            request = json.loads(raw)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('invalid retained request bytes') from exc
        if (digest(raw) != row.get('request_sha256') or request != row.get('request')
                or raw.decode('utf-8') != row.get('request_raw_utf8')):
            raise ValueError('raw and parsed request bindings differ')
        if not isinstance(request, dict) or set(request) != {'operation', 'arguments'}:
            raise ValueError('invalid retained request envelope')
        operation = request['operation']
        response = row.get('response', {})
        if (operation not in WRITES | READS or response.get('operation') != operation
                or response.get('surface_version') != 'v4' or response.get('ok') is not True):
            raise ValueError('retained operation result changed or failed')
        if operation == 'read_learning_context':
            for document in response['result']['documents']:
                reads[document['path']] = (index, document['version_token'])
        if operation in WRITES:
            if stage == 'c' or operation in seen_writes or response.get('result') != {'applied': True}:
                raise ValueError('observed owning-write outcome changed')
            if operation in {'reconcile_knowledge', 'save_learning_checkpoint'}:
                target = READ_PATH if operation == 'reconcile_knowledge' else CHECKPOINT_PATH
                fresh = reads.get(target)
                if (not fresh or fresh[0] <= last_write
                        or request['arguments']['expected_version_token'] != fresh[1]):
                    raise ValueError('dependent write lacks actual fresh read')
            seen_writes.add(operation)
            last_write = index
    expected_writes = spec['owning_write_count'] if stage == 'b' else 0
    if len(seen_writes) != expected_writes:
        raise ValueError('missing owning-write receipt')
    return rows


def verify_review_bindings(review):
    """Check retained review attribution, not the truth of its judgment."""
    manifest = read('publication-manifest.json')
    if (review.get('prospective_plan_sha256') != digest((FIXTURES / 'prospective-plan.json').read_bytes())
            or review.get('prospective_freeze_sha256') != digest((FIXTURES / 'prospective-freeze.json').read_bytes())):
        raise ValueError('review plan binding changed')
    for stage, key in (('b', 'rows'), ('c', 'successor_rows')):
        rows = review.get(key, [])
        if (len(rows) != len(manifest['cases'])
                or {row.get('arm') for row in rows} != set(manifest['cases'])):
            raise ValueError('review coverage changed')
        for row in rows:
            case = row['arm']
            expected = manifest['cases'][case]
            if (row.get('handle') != expected[stage + '_handle']
                    or stage == 'c' and row.get('parent_handle') != expected['b_handle']):
                raise ValueError('review handle or parent binding changed')
            bindings = {'packet.json': f'{case}-{stage}-packet.json',
                        'first-response.json': f'{case}-{stage}-response.json',
                        'sealed-state.json': f'{case}-post-state.json'}
            if stage == 'b':
                bindings['successor-recovery.json'] = f'{case}-successor-recovery.json'
            if any(row.get('bindings', {}).get(old) != digest((FIXTURES / new).read_bytes())
                   for old, new in bindings.items()):
                raise ValueError('review artifact binding changed')
            logs = read(f'{case}-{stage}-trajectory.json')
            originals = {f"{x['index']:03}.json": x['source_log_sha256'] for x in logs}
            if originals != row.get('request_log_sha256'):
                raise ValueError('review source-log binding changed')


def successor_packet(original_b, trace):
    # Recover only from actual state, never a parent response/answer sidecar.
    return {'request': 'Continue where we left off.',
        'product_protocols': original_b['product_protocols'], 'recovery_trace': trace,
        'available_host_operations': original_b['available_host_operations'],
        'response_contract': original_b['response_contract']}


def state_of(provider):
    return {'docs': copy.deepcopy(provider.docs), 'blobs': copy.deepcopy(provider.blobs),
            'instance_head': provider.instance_head,
            'snapshot_extra_paths': sorted(provider.snapshot_extra_paths)}


def make_provider(root, state=None):
    (root / 'control').mkdir()
    (root / 'instance').mkdir()
    provider = BrokerProvider(root / 'control', root / 'instance')
    if state is None:
        provider.set_branch_registry(role='main', subtopic='unit')
        provider.docs[CHECKPOINT_PATH] = checkpoint_progress()
        provider.blobs[CHECKPOINT_PATH] = CHECKPOINT_BLOB
        provider.snapshot_extra_paths.add(CHECKPOINT_PATH)
    else:
        provider.docs = copy.deepcopy(state['docs'])
        provider.blobs = copy.deepcopy(state['blobs'])
        provider.instance_head = state['instance_head']
        provider.snapshot_extra_paths = set(state['snapshot_extra_paths'])
    return provider


def open_host(provider, *, writable=False):
    return ReferenceLearningHost.open(provider=provider, locator_source=locator(),
        branch_runtime_path=RUNTIME_PATH,
        policy=RuntimeCapabilityPolicy(
            readable_roots=('learner/knowledge', 'evidence', 'topics/synthetic/subtopics/unit'),
            writable_roots=(READ_PATH, 'evidence', CHECKPOINT_PATH) if writable else ()),
        write_admission=DeploymentWriteGate(), expected_generation=3)


def changed_paths(before, after):
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def recover(provider):
    before = copy.deepcopy(provider.docs)
    trace = []
    with open_host(provider) as host:
        request = {'operation': 'discover_learning_evidence', 'arguments': {}}
        result = host.invoke(request)
        trace.append({'request': request, 'response': result})
        if not result['ok']:
            raise AssertionError(result)
        discovery = result['result']
        paths = [CHECKPOINT_PATH]
        paths += [x['path'] for x in discovery['knowledge_owners'] if x['state'] == 'present']
        paths += [x['path'] for x in discovery['evidence']]
        request = {'operation': 'read_learning_context', 'arguments': {'required_paths': paths}}
        result = host.invoke(request)
        trace.append({'request': request, 'response': result})
        if not result['ok']:
            raise AssertionError(result)
    if provider.docs != before:
        raise AssertionError('read-only recovery changed state')
    return trace


def verify_state_invariants(initial, after, case):
    before_docs, after_docs = initial['docs'], after['docs']
    for path, content in before_docs.items():
        if path.startswith('evidence/') and after_docs.get(path) != content:
            raise AssertionError('original Evidence changed')
    old = yaml.safe_load(before_docs[READ_PATH])['concepts']['category_statements']['capabilities']
    new = yaml.safe_load(after_docs[READ_PATH])['concepts']['category_statements']['capabilities']
    if old['apply_the_stated_forward_relationship'] != new['apply_the_stated_forward_relationship']:
        raise AssertionError('forward capability changed')
    old_progress, new_progress = (yaml.safe_load(d[CHECKPOINT_PATH]) for d in (before_docs, after_docs))
    if any(new_progress[k] != value for k, value in old_progress.items()
           if k not in {'revision', 'updated_at', 'current', 'resume'}):
        raise AssertionError('unrelated Progress fields changed')
    new_evidence = [yaml.safe_load(after_docs[p]) for p in set(after_docs) - set(before_docs)
                    if p.startswith('evidence/')]
    expected = read('publication-manifest.json')['cases'][case]['current_occurrence_count']
    round_id = read(case + '-b-packet.json')['current_turn']['source_round_id']
    if (len(new_evidence) != expected
            or any(item.get('source', {}).get('round_id') != round_id for item in new_evidence)):
        raise AssertionError('retained source occurrence count changed')


def replay_requests(state, rows):
    receipts = []
    for row in rows:
        with tempfile.TemporaryDirectory() as temporary:
            provider = make_provider(Path(temporary), state)
            before = copy.deepcopy(provider.docs)
            with open_host(provider, writable=True) as host:
                # Decode exactly the retained bytes; never repair a returned request.
                result = host.invoke(json.loads(base64.b64decode(row['request_raw_base64'])))
            changed = changed_paths(before, provider.docs)
            if (result != row['response'] or json.loads(encoded(provider.calls)) != row['provider_calls']
                    or changed != row['changed_paths']):
                raise AssertionError('exact host replay differs at request ' + str(row['index']))
            state = state_of(provider)
            receipts.append({'index': row['index'], 'request_sha256': row['request_sha256'],
                'response': result, 'provider_calls': provider.calls, 'changed_paths': changed})
    return state, receipts


def replay():
    verify_artifacts()
    verify_review_bindings(read('semantic-review-final.json'))
    real_clock = datetime.datetime
    instant = real_clock.fromisoformat(read('elm-b-packet.json')['available_host_operations']
                                      ['fictional_runtime_time'].replace('Z', '+00:00'))

    class FrozenClock(real_clock):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    packets, rows = {}, []
    with mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock):
        with tempfile.TemporaryDirectory() as temporary:
            provider = make_provider(Path(temporary))
            with open_host(provider, writable=True) as host:
                for row in read('preparation-attempt-1.json'):
                    before = copy.deepcopy(provider.docs)
                    result = host.invoke(row['request'])
                    if result != row['response'] or changed_paths(before, provider.docs) != row['changed_paths']:
                        raise AssertionError('original preparation differs')
            initial_trace = recover(provider)
            initial = state_of(provider)
            if initial != read('initial-provider-state.json') or json.loads(encoded(provider.calls)) != read('initial-provider-calls.json'):
                raise AssertionError('original initial state/call trace differs')
        for case in read('publication-manifest.json')['cases']:
            original_b = read(case + '-b-packet.json')
            packet_b = dict(original_b, recovery_trace=initial_trace)
            if encoded(packet_b) != (FIXTURES / (case + '-b-packet.json')).read_bytes():
                raise AssertionError('first consumer packet differs')
            packets[case + '-b'] = packet_b
            b_rows = validate_trajectory(read(case + '-b-trajectory.json'), case, 'b')
            post, b_receipts = replay_requests(copy.deepcopy(initial), b_rows)
            if post != read(case + '-post-state.json'):
                raise AssertionError('first trajectory final state differs')
            verify_state_invariants(initial, post, case)
            with tempfile.TemporaryDirectory() as temporary:
                provider = make_provider(Path(temporary), post)
                trace = recover(provider)
                recovery = {'trace': trace, 'provider_calls': json.loads(encoded(provider.calls)), 'no_mutation_by_read': True}
                if recovery != read(case + '-successor-recovery.json'):
                    raise AssertionError('actual successor recovery differs')
            packet_c = successor_packet(original_b, trace)
            if encoded(packet_c) != (FIXTURES / (case + '-c-packet.json')).read_bytes():
                raise AssertionError('successor packet differs or contains a sidecar')
            packets[case + '-c'] = packet_c
            c_rows = validate_trajectory(read(case + '-c-trajectory.json'), case, 'c')
            final, c_receipts = replay_requests(copy.deepcopy(post), c_rows)
            if final != post:
                raise AssertionError('observed successor unexpectedly changed state')
            rows.append({'case': case, 'b_receipts': b_receipts, 'c_receipts': c_receipts,
                         'post_state': post, 'successor_state': final})
    return {'packets': packets, 'rows': rows, 'model_resampled': False}


def export_replay(destination):
    destination = Path(destination).resolve()
    if destination.exists() or destination == ROOT or ROOT in destination.parents:
        raise ValueError('output must be a new directory outside Core')
    result = replay()  # Includes inventory, exact input, receipt and state guards.
    destination.mkdir(parents=True, exist_ok=False)
    for name, packet in result['packets'].items():
        (destination / (name + '-packet.json')).write_bytes(encoded(packet))
    (destination / 'mechanical-replay.json').write_bytes(encoded(result['rows']))
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    export_replay(parser.parse_args().output)
