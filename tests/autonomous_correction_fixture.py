"""Reproduce a retained synthetic acquisition/correction witness without a model.

The source-selection/order checks describe these first trajectories, not a new
product algorithm or a semantic tutor grader. All providers are local fakes.
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
from scripts.runtime_adapter import GuardRejected, ResolutionError
from scripts.runtime_broker import (DeploymentWriteGate, RuntimeCapabilityPolicy, RuntimeSessionBroker,
                                    _load_candidate_yaml, _relative_path)
from scripts.validate_learning_os import (InstanceValidator, instance_path_identity_mismatches,
                                          validate_instance_document_trust_boundary)
from tests.answer_closure_fixture import make_provider, state_of
from tests.cold_resume_fixture import ColdJourney
from tests.correction_propagation_fixture import PERFORMANCE_PATH, REPORT_PATH
from tests.test_runtime_broker import CHECKPOINT_PATH, READ_PATH, RUNTIME_PATH, locator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/core34-autonomous-correction'
MANIFEST_SHA256 = 'bdb76ccd3392043249a03266cbc4c246cec3d9a0dc3853eb6d5fe469c44ee0c0'
EVENT_TIME = '2026-10-09T16:50:00Z'
REAL_CLOCK = datetime.datetime
WRITES = {'reconcile_knowledge', 'save_learning_checkpoint'}
PAYLOAD_OWNERS = {READ_PATH: 'reconcile_knowledge', CHECKPOINT_PATH: 'save_learning_checkpoint'}


class FrozenClock(REAL_CLOCK):
    @classmethod
    def now(cls, tz=None):
        instant = REAL_CLOCK.fromisoformat(EVENT_TIME.replace('Z', '+00:00'))
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(name):
    return json.loads((FIXTURES / name).read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same_record(left, right):
    return RuntimeSessionBroker._type_sensitive_semantic_equal(left, right)


def verify_artifacts(directory=FIXTURES):
    directory = Path(directory)
    raw = (directory / 'publication-manifest.json').read_bytes()
    require(digest(raw) == MANIFEST_SHA256, 'publication manifest changed')
    manifest = json.loads(raw)
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
                'preparation fixture dependency changed: ' + name)


def opened(provider, *, readonly=False, preparation=False):
    writes = ('evidence',) if preparation else (() if readonly else (READ_PATH, CHECKPOINT_PATH))
    return ReferenceLearningHost.open(provider=provider, locator_source=locator(),
        branch_runtime_path=RUNTIME_PATH,
        policy=RuntimeCapabilityPolicy(readable_roots=('learner/knowledge', 'evidence',
            'topics/synthetic/subtopics/unit'), writable_roots=writes),
        write_admission=DeploymentWriteGate(), expected_generation=3)


def changed_paths(before, after):
    return sorted(p for p in set(before['docs']) | set(after['docs'])
                  if before['docs'].get(p) != after['docs'].get(p))


def build_worlds():
    verify_dependencies()
    preparation = read('preparation-provenance.json')['preparation']
    with mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock), ColdJourney(False) as journey:
        require(same_record(state_of(journey.provider), preparation[0]['state']), 'base preparation state changed')
        constructor_calls = json.loads(encoded(journey.provider.calls))
        require(same_record(constructor_calls, preparation[0]['provider_calls'][:len(constructor_calls)]),
                'constructor prefix differs from retained cumulative trace')
        with journey.open(writable=True) as host:
            for row in preparation[1:3]:
                require(same_record(host.invoke(row['request']), row['response']), 'checkpoint preparation changed')
        # Original preparation row retained this mutable call list until all
        # baseline checkpoint operations had finished; compare its full scope.
        require(same_record(json.loads(encoded(journey.provider.calls)), preparation[0]['provider_calls']),
                'cumulative baseline preparation trace changed')
        baseline = state_of(journey.provider)
    row = preparation[3]
    with tempfile.TemporaryDirectory() as temporary:
        provider = make_provider(Path(temporary), baseline)
        with mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock), opened(provider, preparation=True) as host:
            result = host.invoke(row['request'])
        report = state_of(provider)
        require(same_record(result, row['response'])
                and same_record(json.loads(encoded(provider.calls)), row['provider_calls']),
                'report preparation result/trace changed')
    require(changed_paths(baseline, report) == [REPORT_PATH], 'report changed unrelated document')
    for key in ('docs', 'blobs'):
        require(all(report[key].get(p) == value for p, value in baseline[key].items()),
                'report changed existing ' + key)
    require(baseline['instance_head'] == report['instance_head']
            and baseline['snapshot_extra_paths'] == report['snapshot_extra_paths'], 'report changed authority/inventory')
    require(digest(encoded(baseline)) == row['before_state_sha256']
            and digest(encoded(report)) == row['after_state_sha256'], 'report state binding changed')
    require(same_record(baseline, read('baseline-state.json')) and same_record(report, read('report-state.json')),
            'prepared world differs from first state')
    require(yaml.safe_load(report['docs'][REPORT_PATH])['interpretation']['direction'] == 'neutral',
            'report is not neutral')
    return {'baseline': baseline, 'report': report}


def receipt_payloads(state):
    """Validate owned documents with pure source gates over in-memory records."""
    documents = {}
    for path, kind in ((READ_PATH, 'learner_knowledge'), (CHECKPOINT_PATH, 'subtopic_progress')):
        value = _load_candidate_yaml(state['docs'][path], kind)
        require(isinstance(value, dict) and value.get('document_type') == kind, 'invalid payload projection')
        require(isinstance(value.get('schema_version'), str) and value['schema_version'].strip(),
                'missing owned document schema version')
        require(not instance_path_identity_mismatches(path, value, kind)
                and not validate_instance_document_trust_boundary(path, value, kind), 'invalid payload identity/trust')
        if kind == 'learner_knowledge':
            RuntimeSessionBroker._knowledge_evidence_ref_map(value, label='projected', allow_legacy_duplicates=True)
        else:
            require(all(isinstance(value.get(field), dict) for field in ('current', 'resume', 'milestones')),
                    'invalid Progress checkpoint sections')
            checkpoint = RuntimeSessionBroker._normalize_learning_checkpoint({
                'milestone': value['current'].get('milestone'),
                'return_point': value['resume'].get('return_point'),
                'ready_next': value['resume'].get('ready_next')})
            require(all(milestone in value['milestones'] for milestone in checkpoint['milestone']),
                    'Progress names an unknown milestone')
            return_point = checkpoint['return_point']
            if isinstance(return_point, dict) and 'milestone' in return_point:
                require(isinstance(return_point['milestone'], str)
                        and return_point['milestone'] and return_point['milestone'] in value['milestones'],
                        'invalid Progress return-point milestone')
        documents[path] = value
    validator = object.__new__(InstanceValidator)
    validator.docs, validator.findings, validator.evidence = dict(documents), [], set()
    for path, content in state['docs'].items():
        if path.startswith('evidence/'):
            evidence, canonical = RuntimeSessionBroker._evidence_candidate(content)
            require(canonical == path and not validate_instance_document_trust_boundary(path, evidence, 'evidence'),
                    'invalid receipt Evidence identity/trust')
            validator.docs[path] = evidence
            validator.evidence.add(evidence['id'])
    validator.structural()
    validator.refs()
    require(not validator.findings, 'invalid owned document structure/references')
    return documents


def payload_projection(state):
    projected = {}
    for path, document in receipt_payloads(state).items():
        value = copy.deepcopy(document)
        for name in ('revision', 'updated_at'):
            value.pop(name, None)
        if path == READ_PATH:
            for concept in value.get('concepts', {}).values():
                for capability in concept.get('capabilities', {}).values():
                    capability.pop('updated_at', None)
        projected[path] = value
    return projected


def validate_receipt_state(state):
    require(isinstance(state, dict)
            and set(state) == {'docs', 'blobs', 'instance_head', 'snapshot_extra_paths'}, 'invalid state envelope')
    for field in ('docs', 'blobs'):
        require(isinstance(state[field], dict)
                and all(isinstance(path, str) and path and isinstance(value, str)
                        for path, value in state[field].items()), 'invalid state ' + field)
        for path, value in state[field].items():
            require(_relative_path(path, 'receipt state path') == path, 'noncanonical state path')
            require(field != 'blobs' or bool(value), 'empty state blob token')
    require(set(state['docs']) == set(state['blobs']), 'state document/blob inventory mismatch')
    require(isinstance(state['instance_head'], str) and state['instance_head'], 'invalid state head')
    paths = state['snapshot_extra_paths']
    require(isinstance(paths, list) and all(isinstance(path, str) and path for path in paths)
            and paths == sorted(set(paths)) and set(paths) <= set(state['docs']), 'invalid state inventory')



def _load_publication_evidence():
    """Pin the bytes actually parsed, without a verify-then-reread race."""
    raw = (FIXTURES / 'publication-manifest.json').read_bytes()
    require(digest(raw) == MANIFEST_SHA256, 'publication manifest changed')
    manifest = json.loads(raw)
    files = {}
    for case, spec in manifest['cases'].items():
        for name in (spec['initial_state_file'], spec['final_state_file'], case + '-trajectory.json',
                     'mechanical-exact-replay.json'):
            if name not in files:
                content = (FIXTURES / name).read_bytes()
                require(digest(content) == manifest['files'][name]['sha256'], 'publication bytes changed: ' + name)
                files[name] = content
    mechanical = json.loads(files['mechanical-exact-replay.json'])
    return tuple((case, encoded(spec), files[spec['initial_state_file']], files[spec['final_state_file']],
                  files[case + '-trajectory.json'], encoded(mechanical[case]['requests']))
                 for case, spec in manifest['cases'].items())


# Immutable bytes loaded once at module admission; every comparison parses fresh
# objects. This is publication identity, not proof that current source still
# executes these receipts. replay() independently verifies that through the Host.
_PUBLICATION_EVIDENCE = _load_publication_evidence()


def receipt_integrity_errors(before, after, rows, *, case=None):
    """Authenticate only the retained witness, not arbitrary proposed receipts.

    A complete case binds every field, including the full provider trace, raw
    request, result and serialized state. Unknown evidence is explicitly unbound;
    it is not classified as a bad teaching outcome or a failed source operation.
    """
    try:
        for label, _, initial, final, trajectory, _ in _PUBLICATION_EVIDENCE:
            if case is not None and case != label:
                continue
            if (same_record(before, json.loads(initial)) and same_record(after, json.loads(final))
                    and same_record(rows, json.loads(trajectory))):
                return []
    except (KeyError, TypeError, ValueError, RecursionError, GuardRejected, ResolutionError):
        pass
    return [{'error': 'unbound publication evidence'}]


def payload_delta(before, after):
    """Counterfactual projection only; a delta never authenticates an execution."""
    left, right = payload_projection(before), payload_projection(after)
    return [path for path in left if not same_record(left[path], right[path])]


def successor_decision(before, after, rows):
    """Reconstruct the frozen decision only for authenticated retained evidence."""
    errors = receipt_integrity_errors(before, after, rows)
    if errors:
        return {'status': 'blocked', 'trigger': False, 'reason': 'publication binding', 'errors': errors}
    try:
        delta = payload_delta(before, after)
        previous = payload_projection(before)
        for row in rows:
            current = payload_projection(row['after_state'])
            row_delta = [p for p in previous
                         if not RuntimeSessionBroker._type_sensitive_semantic_equal(previous[p], current[p])]
            applied = (row['response'].get('ok') is True
                       and row['response'].get('result', {}).get('applied') is True)
            if any(not applied or row['request']['operation'] != PAYLOAD_OWNERS[path]
                   for path in row_delta):
                return {'status': 'anomaly', 'trigger': False,
                        'reason': 'payload changed without an applied owning write'}
            previous = current
    except (KeyError, ValueError, TypeError, AttributeError, GuardRejected, ResolutionError, yaml.YAMLError) as exc:
        return {'status': 'blocked', 'trigger': False, 'reason': 'invalid payload projection',
                'exception': type(exc).__name__}
    writes = [r['index'] for r in rows if isinstance(r.get('request'), dict)
              and r['request'].get('operation') in WRITES
              and r.get('response', {}).get('ok') is True
              and r['response'].get('result', {}).get('applied') is True]
    return {'status': 'triggered' if delta else 'no_new_payload', 'trigger': bool(delta),
            'payload_changed_paths': delta, 'applied_owning_write_indices': writes,
            'full_state_changed': before != after,
            'rule': 'frozen payload projection; semantic interpretation is separate'}


def verify_successor_decision(decision, frozen):
    fields = {'status', 'trigger', 'payload_changed_paths', 'applied_owning_write_indices',
              'full_state_changed', 'rule'}
    require(isinstance(decision, dict) and set(decision) == fields
            and isinstance(frozen, dict) and set(frozen) == fields,
            'frozen successor decision fields changed')
    require(RuntimeSessionBroker._type_sensitive_semantic_equal(decision, frozen),
            'frozen successor decision changed')


def validate_trajectory(rows, case):
    records = [record for record in _PUBLICATION_EVIDENCE if record[0] == case]
    require(len(records) == 1, 'unbound publication case')
    _, spec_raw, initial_raw, final_raw, _, witness_raw = records[0]
    spec, initial, final = (json.loads(raw) for raw in (spec_raw, initial_raw, final_raw))
    require(isinstance(rows, list) and len(rows) == spec['request_count'], 'missing/duplicate/surplus receipts')
    require(not receipt_integrity_errors(initial, final, rows, case=case), 'receipt integrity/state-chain mismatch')
    retained = json.loads(witness_raw)
    require(len(retained) == len(rows), 'replay coverage changed')
    operations = ['read_learning_context', 'discover_learning_evidence', 'read_learning_context']
    if case == 'report':
        operations += ['reconcile_knowledge', 'read_learning_context']
    require(len(operations) == len(rows), 'retained operation coverage changed')
    owners, tokens, writes = set(), {}, 0
    for row, witness, operation in zip(rows, retained, operations):
        req, result = row['request'], row['response']
        require(set(req) == {'operation', 'arguments'} and req['operation'] == operation,
                'retained operation/envelope changed')
        require(result.get('surface_version') == 'v4' and result.get('ok') is True
                and result.get('operation') == operation, 'retained host result failed or changed')
        require(row.get('omitted_fields') == ['request_file'] and 'request_file' not in row,
                'receipt omission disclosure changed')
        require(row['request_sha256'] == witness['request_sha256'] and row['index'] == witness['index']
                and same_record(result, witness['response']) and same_record(row['provider_calls'], witness['provider_calls'])
                and row['after_state_sha256'] == witness['state_sha256'], 'original replay binding changed')
        if operation == 'discover_learning_evidence':
            owners = {d['path'] for d in result['result']['knowledge_owners'] if d['state'] == 'present'}
            expected = {PERFORMANCE_PATH} | ({REPORT_PATH} if case != 'baseline' else set())
            require({d['path'] for d in result['result']['evidence']} == expected, 'Evidence coverage changed')
        elif operation == 'read_learning_context':
            documents = result['result']['documents']
            if row['index'] == 3:
                args = req['arguments']
                require(set(args.get('required_paths', []) + args.get('optional_paths', [])) == owners,
                        'Knowledge was not obtained from discovery')
            for document in documents:
                tokens[document['path']] = document['version_token']
        elif operation in WRITES:
            writes += 1
            require(case == 'report' and same_record(result['result'], {'applied': True})
                    and req['arguments']['expected_version_token'] == tokens.get(READ_PATH),
                    'owning write lacks actual fresh token/result')
    require(writes == spec['applied_writes'], 'owning-write count changed')
    return rows


def verify_state_invariants(before, after, case):
    validate_receipt_state(before); validate_receipt_state(after)
    if case != 'report':
        require(same_record(before, after), 'nonwriting trajectory changed state')
    else:
        require(changed_paths(before, after) == [READ_PATH], 'correction changed unrelated document')
        old, new = (yaml.safe_load(s['docs'][READ_PATH]) for s in (before, after))
        expected = copy.deepcopy(old)
        del expected['concepts']['token-identity']['capabilities']['explanation']
        expected['revision'] += 1
        expected['updated_at'] = EVENT_TIME
        require(same_record(new, expected), 'retained claim-specific correction/naming preservation changed')
        for field in ('instance_head', 'snapshot_extra_paths'):
            require(before[field] == after[field], 'authority/inventory changed')
        require(all(after['blobs'].get(p) == value for p, value in before['blobs'].items() if p != READ_PATH),
                'unrelated blob changed')
    for path, content in before['docs'].items():
        if path.startswith('evidence/'):
            require(after['docs'].get(path) == content, 'original Evidence changed')
    require({p for p in after['docs'] if p.startswith('evidence/')} ==
            {p for p in before['docs'] if p.startswith('evidence/')}, 'new/repeated source occurrence')
    performances = [yaml.safe_load(v) for p, v in after['docs'].items() if p.startswith('evidence/')
                    and yaml.safe_load(v)['observation']['kind'] == 'task_response']
    require(len(performances) == 1, 'original performance count changed')


def verify_review_bindings(review):
    manifest = read('publication-manifest.json')
    require(same_record(review, read('semantic-method-review.json')), 'review differs from retained artifact')
    bindings = review.get('bound_artifact_sha256', {})
    for name, item in manifest['files'].items():
        if item['kind'] == 'verbatim' and name != 'semantic-method-review.json':
            require(bindings.get(item['source_path']) == item['source_sha256'] == digest((FIXTURES / name).read_bytes()),
                    'review verbatim binding changed: ' + name)
    require({r['world'] for r in review.get('first_world_assessments', [])} == {'baseline', 'report'}
            and len(review['first_world_assessments']) == 2, 'review world coverage changed')
    provenance = read('successor-trigger-provenance.json')
    for case, spec in manifest['cases'].items():
        h = spec['handle']
        rows = read(case + '-trajectory.json')
        for row in rows:
            source_path = f"handles/{h}/receipts/{row['index']:03}.json"
            require(bindings.get(source_path) == row['source_log_sha256'], 'review receipt binding changed')
        require(bindings.get(f'handles/{h}/sealed.json') == provenance['seal_sha256'][case],
                'review seal binding changed')
        require(digest(encoded(provenance['seals'][case])) == provenance['seal_sha256'][case],
                'composed seal values changed')
        require(spec['response_sha256'] == digest((FIXTURES / (case + '-response.json')).read_bytes())
                and spec['packet_sha256'] == digest((FIXTURES / spec['packet_file']).read_bytes()),
                'case input/response bytes changed')
    successor = manifest['cases']['successor']
    require(bindings.get(f"handles/{successor['handle']}/successor-origin.json") == provenance['successor_origin_sha256']
            == digest(encoded(provenance['successor_origin'])), 'successor source binding changed')
    require(provenance['successor_origin']['parent_state_sha256'] == manifest['cases']['report']['final_state_sha256']
            == successor['initial_state_sha256'], 'successor not actual parent readback')
    freeze = read('prospective-freeze.json')
    for case in ('baseline', 'report'):
        spec = manifest['cases'][case]
        for suffix, expected in [('packet.json', spec['packet_sha256']), ('task.txt', spec['task_sha256']),
                                 ('initial-state.json', spec['initial_state_sha256'])]:
            require(freeze['files'][f"handles/{spec['handle']}/{suffix}"] == expected, 'prospective input changed')
    require(freeze['files']['successor-packet-template.json'] == successor['packet_sha256'],
            'successor packet was not frozen before observation')


def replay():
    manifest = verify_artifacts()
    verify_review_bindings(read('semantic-method-review.json'))
    for readonly, name in ((False, 'first-packet.json'), (True, 'successor-packet.json')):
        require(encoded(packet(readonly=readonly)) == (FIXTURES / name).read_bytes(), 'historical packet changed')
    worlds, output = build_worlds(), []
    for case in ('baseline', 'report', 'successor'):
        spec = manifest['cases'][case]
        state = worlds[case] if case != 'successor' else copy.deepcopy(worlds['report_final'])
        require(same_record(state, read(spec['initial_state_file'])), 'successor/initial state binding changed')
        initial = copy.deepcopy(state)
        rows = validate_trajectory(read(case + '-trajectory.json'), case)
        receipts = []
        for row in rows:
            with tempfile.TemporaryDirectory() as temporary:
                provider = make_provider(Path(temporary), state)
                with mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock), opened(provider, readonly=spec['readonly']) as host:
                    result = host.invoke(json.loads(base64.b64decode(row['request_raw_base64'])))
                after = state_of(provider)
                require(same_record(result, row['response'])
                        and same_record(json.loads(encoded(provider.calls)), row['provider_calls']),
                        'actual result/provider trace changed')
                require(same_record(after, row['after_state']), 'actual after-state changed')
                receipts.append({'index': row['index'], 'request_sha256': row['request_sha256'],
                                 'response': result, 'state_sha256': digest(encoded(after))})
                state = after
        require(same_record(state, read(spec['final_state_file'])), 'sealed final state changed')
        verify_state_invariants(initial, state, case)
        decision = successor_decision(initial, state, rows)
        if case != 'successor':
            frozen = read('successor-trigger-provenance.json')['seals'][case]['successor_decision']
            verify_successor_decision(decision, frozen)
        if case == 'report':
            worlds['report_final'] = state
        output.append({'case': case, 'receipts': receipts, 'state_sha256': digest(encoded(state)),
                       'successor_trigger': decision['trigger'] if case != 'successor' else False})
    return output


def export_replay(destination):
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents or destination.exists():
        raise ValueError('output must be a new directory outside the Core snapshot')
    result = replay()
    destination.mkdir(parents=True, exist_ok=False)
    for readonly, name in ((False, 'first-packet.json'), (True, 'successor-packet.json')):
        (destination / name).write_bytes(encoded(packet(readonly=readonly)))
    (destination / 'mechanical-replay.json').write_bytes(encoded(result))


# Public task/interface vocabulary actually supplied, independent of future live policy.
FIRST_FIELDS = {'request': 'Continue where we left off.',
 'session_binding': {'topic': 'synthetic', 'subtopic': 'unit', 'role': 'main'},
 'host_interface': {'surface_version': 'v4',
                    'request_shape': {'operation': 'one named operation',
                                      'arguments': 'exact argument mapping'},
                    'operations': {'read_learning_context': {'required_paths': 'array of canonical '
                                                                               'document paths, at '
                                                                               'least one path across '
                                                                               'both arrays',
                                                             'optional_paths': 'optional array; absent '
                                                                               'entries are returned as '
                                                                               'missing_optional; any '
                                                                               'required missing path '
                                                                               'rejects the complete '
                                                                               'operation',
                                                             'limit': '32 combined paths per call, '
                                                                      'unique across both arrays; no '
                                                                      'truncation'},
                                   'discover_learning_evidence': {'arguments': {},
                                                                  'meaning': 'Returns all matching '
                                                                             'typed Evidence for the '
                                                                             'bound Topic/Subtopic with '
                                                                             'original content and '
                                                                             'same-snapshot Knowledge '
                                                                             'owner/reference metadata. '
                                                                             'Unreferenced does not '
                                                                             'mean unprocessed; path '
                                                                             'order is not event-time '
                                                                             'order.',
                                                                  'limits': '128 inventory '
                                                                            'candidates;16 matching '
                                                                            'records;16 distinct '
                                                                            'Knowledge owners;8 '
                                                                            'targets/record and64 '
                                                                            'total;64KiB per document '
                                                                            'and256KiB scanned '
                                                                            'Evidence/read Knowledge; '
                                                                            'over-limit fails whole '
                                                                            'operation, no pagination'},
                                   'reconcile_knowledge': {'content': 'complete existing-schema '
                                                                      'Knowledge YAML string',
                                                           'expected_version_token': 'current Knowledge '
                                                                                     'token returned by '
                                                                                     'an actual fresh '
                                                                                     'read'},
                                   'save_learning_checkpoint': {'checkpoint': {'milestone': 'array of '
                                                                                            'current '
                                                                                            'milestone '
                                                                                            'IDs',
                                                                               'return_point': 'small '
                                                                                               'semantic '
                                                                                               'object '
                                                                                               'or null',
                                                                               'ready_next': 'array'},
                                                                'expected_version_token': 'current '
                                                                                          'Progress '
                                                                                          'token '
                                                                                          'returned by '
                                                                                          'an actual '
                                                                                          'fresh read'}},
                    'readable_roots': ['learner/knowledge',
                                       'evidence',
                                       'topics/synthetic/subtopics/unit'],
                    'path_vocabulary_note': 'Canonical path templates are interface vocabulary, not a '
                                            'list of existing documents. Topic IDs and Domain IDs are '
                                            'distinct concepts; do not assume they match. No concrete '
                                            'Evidence IDs or Knowledge owner have been supplied.',
                    'failure_contract': {'resolution_failed': 'request/host resolution failed',
                                         'guard_rejected': 'guard rejected; this alone does not '
                                                           'distinguish absent, unauthorized or '
                                                           'over-budget data',
                                         'cas_conflict': 'freshness conflict; retryable'},
                    'transport': 'The handoff provides an opaque handle and host_bridge.py command. '
                                 'Submit your chosen request through it; stdout is the actual result. '
                                 'Do not read provider, bridge implementation, plan, other trajectories '
                                 'or source repository directly. Preserve any failed request; do not '
                                 'claim an unexecuted or rejected write was saved.',
                    'writable_path_templates': ['learner/knowledge/<discovered-domain>.yaml',
                                                'topics/<bound-topic>/subtopics/<bound-subtopic>/progress.yaml'],
                    'fictional_runtime_time': '2026-10-09T16:50:00Z'},
 'response_contract': {'reply': 'The next concise learner-facing continuation grounded only in actual '
                                'retrieved facts, or a proportionate clarification if needed',
                       'basis': 'Short checkable factual basis identifying retrieved facts; no hidden '
                                'reasoning',
                       'acquisition_summary': 'What you actually retrieved and important unavailable or '
                                              'uncertain facts; do not claim operations you did not '
                                              'execute',
                       'persistence_summary': 'Actual owning-operation results, or no writes; do not '
                                              'claim unexecuted actions'}}

SUCCESSOR_FIELDS = {'request': 'Continue where we left off.',
 'session_binding': {'topic': 'synthetic', 'subtopic': 'unit', 'role': 'main'},
 'host_interface': {'surface_version': 'v4',
                    'request_shape': {'operation': 'one named operation',
                                      'arguments': 'exact argument mapping'},
                    'operations': {'read_learning_context': {'required_paths': 'array of canonical '
                                                                               'document paths, at '
                                                                               'least one path across '
                                                                               'both arrays',
                                                             'optional_paths': 'optional array; absent '
                                                                               'entries are returned as '
                                                                               'missing_optional; any '
                                                                               'required missing path '
                                                                               'rejects the complete '
                                                                               'operation',
                                                             'limit': '32 combined paths per call, '
                                                                      'unique across both arrays; no '
                                                                      'truncation'},
                                   'discover_learning_evidence': {'arguments': {},
                                                                  'meaning': 'Returns all matching '
                                                                             'typed Evidence for the '
                                                                             'bound Topic/Subtopic with '
                                                                             'original content and '
                                                                             'same-snapshot Knowledge '
                                                                             'owner/reference metadata. '
                                                                             'Unreferenced does not '
                                                                             'mean unprocessed; path '
                                                                             'order is not event-time '
                                                                             'order.',
                                                                  'limits': '128 inventory '
                                                                            'candidates;16 matching '
                                                                            'records;16 distinct '
                                                                            'Knowledge owners;8 '
                                                                            'targets/record and64 '
                                                                            'total;64KiB per document '
                                                                            'and256KiB scanned '
                                                                            'Evidence/read Knowledge; '
                                                                            'over-limit fails whole '
                                                                            'operation, no pagination'}},
                    'readable_roots': ['learner/knowledge',
                                       'evidence',
                                       'topics/synthetic/subtopics/unit'],
                    'writable_roots': [],
                    'path_vocabulary_note': 'Canonical path templates are interface vocabulary, not a '
                                            'list of existing documents. Topic IDs and Domain IDs are '
                                            'distinct concepts; do not assume they match. No concrete '
                                            'Evidence IDs or Knowledge owner have been supplied.',
                    'failure_contract': {'resolution_failed': 'request/host resolution failed',
                                         'guard_rejected': 'guard rejected; this alone does not '
                                                           'distinguish absent, unauthorized or '
                                                           'over-budget data',
                                         'cas_conflict': 'freshness conflict; retryable'},
                    'transport': 'The handoff provides an opaque handle and host_bridge.py command. '
                                 'Submit your chosen request through it; stdout is the actual result. '
                                 'Do not read provider, bridge implementation, plan, other trajectories '
                                 'or source repository directly. Preserve any failed request; do not '
                                 'claim an unexecuted or rejected write was saved.',
                    'fictional_runtime_time': '2026-10-09T16:50:00Z'},
 'response_contract': {'reply': 'The next concise learner-facing continuation grounded only in actual '
                                'retrieved facts, or a proportionate clarification if needed',
                       'basis': 'Short checkable factual basis identifying retrieved facts; no hidden '
                                'reasoning',
                       'acquisition_summary': 'What you actually retrieved and important unavailable or '
                                              'uncertain facts; do not claim operations you did not '
                                              'execute',
                       'persistence_summary': 'Actual owning-operation results, or no writes; do not '
                                              'claim unexecuted actions'}}


def packet(*, readonly=False, protocols=None):
    fields = copy.deepcopy(SUCCESSOR_FIELDS if readonly else FIRST_FIELDS)
    return {'request': fields['request'], 'session_binding': fields['session_binding'],
            'product_protocols': copy.deepcopy(protocols if protocols is not None else
                read('first-packet.json')['product_protocols']),
            'host_interface': fields['host_interface'], 'response_contract': fields['response_contract']}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    export_replay(parser.parse_args().output)
