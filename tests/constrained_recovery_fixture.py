"""Reproduce frozen constrained reads with the existing synthetic host only.

No model is invoked. Read selection is a fixed recipe, not consumer autonomy.
The missing arm deliberately changes fake state after bootstrap; subsequent
read operations preserve the post-injection state, not the pre-injection state.
"""
import copy
import datetime
import hashlib
import json
from pathlib import Path
from unittest import mock

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import RuntimeCapabilityPolicy, EVIDENCE_DISCOVERY_MAX_RECORDS
from tests.cold_resume_fixture import ColdJourney
from tests.correction_propagation_fixture import PERFORMANCE_PATH, ROOT
from tests.test_runtime_broker import (
    READ_PATH, CHECKPOINT_PATH, RUNTIME_PATH, locator, typed_evidence,
)

FIXTURES = ROOT / 'tests/fixtures/core34-constrained-recovery'
ARMS = (('full', 'cedar'), ('denied', 'iris'), ('missing', 'juniper'),
        ('budget', 'laurel'), ('no_report', 'maple'))
FROZEN_FILES = frozenset({
    'audit-errata.json', 'cedar-packet.json', 'cedar-response.json',
    'complementary-review.json', 'consumer-bindings.json', 'exact-replay.json',
    'iris-packet.json', 'iris-response.json', 'juniper-packet.json',
    'juniper-response.json', 'laurel-packet.json', 'maple-packet.json',
    'maple-response.json', 'mechanical-input-check.json', 'preconsumer-manifest.json',
    'predispatch-clock-receipt.json', 'prospective-plan.json', 'result-summary.json',
    'scope-clarifications.json', 'initial-prospective-plan.json',
    'initial-preparation-failure.json',
})


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def verify_artifacts(directory=FIXTURES):
    directory = Path(directory)
    manifest = json.loads((directory / 'publication-manifest.json').read_text(encoding='utf-8'))
    if set(manifest['files']) != FROZEN_FILES:
        raise AssertionError('Frozen manifest inventory differs')
    expected = FROZEN_FILES | {'publication-manifest.json', '.gitattributes'}
    if {p.name for p in directory.iterdir()} != expected:
        raise AssertionError('Frozen fixture inventory differs')
    for name, expected_hash in manifest['files'].items():
        if digest((directory / name).read_bytes()) != expected_hash:
            raise AssertionError('Frozen artifact changed: ' + name)


def verify_replay_rows(rows):
    if rows != read('exact-replay.json'):
        raise AssertionError('Replay differs from preserved first-request outcomes')
    if [row['arm'] for row in rows] != [arm for arm, _ in ARMS]:
        raise AssertionError('Replay arm inventory differs')
    if any(row.get('evidence_and_progress_unchanged') is False for row in rows):
        raise AssertionError('Replay changed protected synthetic state')


def frozen_clock():
    real_clock = datetime.datetime
    instant = real_clock.fromisoformat(
        read('prospective-plan.json')['synthetic_clock'].replace('Z', '+00:00'))

    class FrozenClock(real_clock):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    return mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock)


def setup(arm, *, budget_excess=1):
    if arm not in dict(ARMS):
        raise ValueError('Unknown synthetic arm')
    if budget_excess not in (0, 1):
        raise ValueError('Budget control supports only at-limit and one-over')
    journey = ColdJourney(include_report=arm != 'no_report')
    if arm == 'budget':
        count = sum(p.startswith('evidence/') for p in journey.provider.docs)
        # Exactly one over the existing matching-record limit. These are neutral
        # synthetic metadata, not fabricated performances or capability evidence.
        for i in range(EVIDENCE_DISCOVERY_MAX_RECORDS - count + budget_excess):
            item = yaml.safe_load(typed_evidence(
                evidence_id=f'evi-neutral-padding-{i:03}', direction='neutral'))
            item['observed_at'] = '2026-10-08T00:00:00Z'
            item['observation'] = {'kind': 'context_note', 'summary':
                'Synthetic metadata note only. It records no learner performance, '
                'assistance report, failure, success, or capability evidence.'}
            item['context'] = {'topic': 'synthetic', 'subtopic': 'unit'}
            item['source'] = {'round_id': f'synthetic-metadata-{i:03}'}
            with journey.open(writable=True) as host:
                journey.invoke(host, 'create_evidence',
                               content=yaml.safe_dump(item, sort_keys=False))
    journey.before = copy.deepcopy(journey.provider.docs)
    journey.before_blobs = copy.deepcopy(journey.provider.blobs)
    journey.provider.calls.clear()
    return journey


def remove_original(journey, *, advance_head):
    """Deliberate fictional retrieval-loss intervention, never a host operation."""
    del journey.provider.docs[PERFORMANCE_PATH]
    del journey.provider.blobs[PERFORMANCE_PATH]
    (journey.provider.instance / PERFORMANCE_PATH).unlink()
    if advance_head:
        journey.provider.instance_head = (
            f'{int(journey.provider.instance_head, 16) + 1:040x}')


def open_reader(journey, arm):
    roots = ('learner/knowledge', 'topics/synthetic/subtopics/unit')
    if arm != 'denied':
        roots += ('evidence',)
    host = ReferenceLearningHost.open(
        provider=journey.provider, locator_source=locator(),
        branch_runtime_path=RUNTIME_PATH,
        policy=RuntimeCapabilityPolicy(readable_roots=roots),
        write_admission=journey.gate, expected_generation=3)
    journey.hosts.append(host)
    return host


def recover(journey, arm):
    trace = []
    with open_reader(journey, arm) as host:
        def invoke(operation, **arguments):
            request = {'operation': operation, 'arguments': arguments}
            result = host.invoke(request)
            trace.append({'request': request, 'response': result})
            return result

        base = [CHECKPOINT_PATH, READ_PATH]
        first = invoke('read_learning_context', required_paths=base)
        if not first['ok']:
            raise AssertionError(first)
        if arm == 'missing':
            remove_original(journey, advance_head=True)
            # Preservation below is relative to this explicit fault injection.
            journey.before = copy.deepcopy(journey.provider.docs)
            journey.before_blobs = copy.deepcopy(journey.provider.blobs)
        discovered = invoke('discover_learning_evidence')
        if discovered['ok']:
            paths = base + [x['path'] for x in discovered['result']['evidence']]
            knowledge = yaml.safe_load(next(d['content'] for d in
                first['result']['documents'] if d['path'] == READ_PATH))
            for concept in knowledge['concepts'].values():
                for claim in concept.get('capabilities', {}).values():
                    for refs in claim.get('evidence_refs', {}).values():
                        paths.extend(f'evidence/{ref}.yaml' for ref in refs)
            invoke('read_learning_context', required_paths=list(dict.fromkeys(paths)))
    if journey.provider.docs != journey.before or journey.provider.blobs != journey.before_blobs:
        raise AssertionError('Read operations changed post-injection synthetic state')
    return trace


def packet(trace):
    # Never regenerate historical model policy from later live product guidance.
    return {
        'request': 'Continue where we left off.',
        'product_protocols': read('cedar-packet.json')['product_protocols'],
        'recovery_operations': trace,
        'available_host_operation': {'operation': 'reconcile_knowledge', 'arguments': {
            'content': 'Complete existing-schema Knowledge YAML text',
            'expected_version_token': 'Current Knowledge version token'}},
        'output_format': {'reply': 'The next learner-facing response',
            'basis': 'A short checkable factual basis, no hidden reasoning',
            'coverage': 'A short statement of what was or was not recoverable',
            'host_requests': 'Optional host request objects, or []'},
    }


def replay():
    """Return derived copies only; preserved first responses are never sampled."""
    verify_artifacts()
    packets, mechanics, rows = {}, [], []
    for arm, alias in ARMS:
        response_alias = 'iris' if alias == 'laurel' else alias
        response = read(response_alias + '-response.json')
        with frozen_clock():
            with setup(arm) as journey:
                actual = packet(recover(journey, arm))
                if encoded(actual) != (FIXTURES / (alias + '-packet.json')).read_bytes():
                    raise AssertionError('Recovery differs from frozen input: ' + arm)
                packets[alias] = actual
                mechanics.append({'arm': arm, 'provider_calls': copy.deepcopy(journey.provider.calls),
                    'underlying_evidence_count': sum(p.startswith('evidence/') for p in journey.before),
                    'post_injection_state_unchanged_by_reads': True})
                row = {'arm': arm, 'response_artifact': response_alias + '-response.json',
                    'response_sha256': digest((FIXTURES / (response_alias + '-response.json')).read_bytes()),
                    'requests': response['host_requests'], 'request_results': [], 'changed_paths': []}
                if response['host_requests']:
                    before = copy.deepcopy(journey.provider.docs)
                    # Separate post-observation mechanical negative control.
                    # A proposed request never creates authority to execute it.
                    with open_reader(journey, arm) as reader:
                        mechanics[-1]['readonly_request_results'] = [
                            reader.invoke(r) for r in response['host_requests']]
                    if journey.provider.docs != before:
                        raise AssertionError('Read-only replay mutated synthetic state')
                    row['request_results'] = [journey.apply(r) for r in response['host_requests']]
                    row['changed_paths'] = sorted(p for p in set(before) | set(journey.provider.docs)
                        if before.get(p) != journey.provider.docs.get(p))
                    row['evidence_and_progress_unchanged'] = all(before[p] == journey.provider.docs.get(p)
                        for p in before if p.startswith('evidence/') or p == CHECKPOINT_PATH)
                    if not row['evidence_and_progress_unchanged']:
                        raise AssertionError('Replay changed Evidence or Progress')
                    row['final_knowledge'] = journey.provider.docs[READ_PATH]
                    row['exact_input_reproduced_before_replay'] = True
                else:
                    row['replay_status'] = 'No request proposed; no mutation invoked.'
                rows.append(row)
    verify_replay_rows(rows)
    return {'packets': packets, 'mechanics': mechanics, 'exact_replay': rows}


def export_replay(destination):
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Replay output must be outside Core')
    if destination.exists():
        raise FileExistsError(destination)
    result = replay()
    destination.mkdir(parents=True, exist_ok=False)
    for alias, value in result['packets'].items():
        (destination / (alias + '-packet.json')).write_bytes(encoded(value))
    (destination / 'exact-replay.json').write_bytes(encoded(result['exact_replay']))
    (destination / 'mechanics.json').write_bytes(encoded(result['mechanics']))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    export_replay(parser.parse_args().output)
