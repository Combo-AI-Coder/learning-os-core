"""Regression tests for a retained bounded witness, not a universal teaching oracle."""
import base64
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

from tests import autonomous_correction_fixture as fixture


def synthetic_row(before, after, index=1, operation='reconcile_knowledge'):
    progress = yaml.safe_load(after['docs'][fixture.CHECKPOINT_PATH])
    arguments = {
        'reconcile_knowledge': {'content': after['docs'].get(fixture.READ_PATH, 'synthetic'),
                                'expected_version_token': 'synthetic-version'},
        'save_learning_checkpoint': {'checkpoint': {'milestone': progress['current']['milestone'],
                                                    'return_point': progress['resume']['return_point'],
                                                    'ready_next': progress['resume']['ready_next']},
                                     'expected_version_token': 'synthetic-version'},
        'read_learning_context': {'required_paths': [fixture.READ_PATH]},
    }.get(operation, {})
    request = {'operation': operation, 'arguments': arguments}
    raw = fixture.encoded(request)
    return {'index': index, 'request': request, 'request_raw_base64': base64.b64encode(raw).decode(),
            'request_raw_utf8': raw.decode(), 'request_sha256': fixture.digest(raw),
            'response': {'surface_version': 'v4', 'ok': True, 'operation': operation, 'result': {'applied': True}},
            'provider_calls': [],
            'before_state_sha256': fixture.digest(fixture.encoded(before)),
            'after_state': copy.deepcopy(after), 'after_state_sha256': fixture.digest(fixture.encoded(after)),
            'state_unchanged': before == after, 'changed_paths': fixture.changed_paths(before, after)}


def replace_request(row, request):
    raw = fixture.encoded(request)
    row.update(request=request, request_raw_base64=base64.b64encode(raw).decode('ascii'),
               request_raw_utf8=raw.decode('utf-8'), request_sha256=fixture.digest(raw))
    row['response']['operation'] = request.get('operation')


class AutonomousCorrectionTests(unittest.TestCase):
    def test_inventory_and_dependent_trajectory_accounting(self):
        manifest = fixture.verify_artifacts()
        self.assertEqual(20, len(manifest['files']))
        self.assertEqual(12, sum(v['kind'] == 'verbatim' for v in manifest['files'].values()))
        self.assertEqual((1, 2, 1, 0, 11), tuple(manifest[k] for k in (
            'first_packet_count', 'first_world_count', 'dependent_successor_count', 'model_resamples', 'total_requests')))
        self.assertEqual(0, manifest['new_performance_occurrences'])
        self.assertEqual(manifest['cases']['baseline']['packet_sha256'], manifest['cases']['report']['packet_sha256'])

    def test_frozen_inputs_do_not_supply_concrete_state_or_answer(self):
        packet = fixture.packet()
        self.assertEqual(fixture.encoded(packet), (fixture.FIXTURES / 'first-packet.json').read_bytes())
        raw = fixture.encoded(packet)
        for forbidden in (fixture.PERFORMANCE_PATH, fixture.REPORT_PATH, fixture.READ_PATH, fixture.CHECKPOINT_PATH):
            self.assertNotIn(forbidden.encode(), raw)
        self.assertNotIn('durable_context', packet)
        self.assertNotIn('current_turn', packet)
        self.assertEqual([], fixture.packet(readonly=True)['host_interface']['writable_roots'])
        self.assertEqual({'read_learning_context', 'discover_learning_evidence'},
                         set(fixture.packet(readonly=True)['host_interface']['operations']))

    def test_historical_packet_does_not_read_future_live_guidance(self):
        before = fixture.packet()
        with mock.patch.object(Path, 'read_text', return_value='later live guidance'):
            self.assertEqual(before, fixture.packet())
        self.assertNotEqual(before, fixture.packet(protocols={'future': 'changed'}))
        self.assertEqual(fixture.encoded(fixture.packet(readonly=True)),
                         (fixture.FIXTURES / 'successor-packet.json').read_bytes())

    def test_world_preparation_and_one_neutral_report_delta(self):
        worlds = fixture.build_worlds()
        self.assertEqual([fixture.REPORT_PATH], fixture.changed_paths(worlds['baseline'], worlds['report']))
        for path in (fixture.READ_PATH, fixture.CHECKPOINT_PATH, fixture.PERFORMANCE_PATH):
            self.assertEqual(worlds['baseline']['docs'][path], worlds['report']['docs'][path])
        progress = yaml.safe_load(worlds['baseline']['docs'][fixture.CHECKPOINT_PATH])
        self.assertEqual('Compare two sentence prefixes ending in the same vocabulary token.',
                         progress['resume']['return_point']['activity'])

    def test_all_retained_receipts_and_review_bindings(self):
        for case in ('baseline', 'report', 'successor'):
            fixture.validate_trajectory(fixture.read(case + '-trajectory.json'), case)
        fixture.verify_review_bindings(fixture.read('semantic-method-review.json'))

    def test_missing_duplicate_reordered_and_changed_raw_receipts_fail(self):
        source = fixture.read('report-trajectory.json')
        variants = [source[:-1], source + [source[-1]], list(reversed(source))]
        for key in ('request_raw_base64', 'request_sha256', 'request_raw_utf8', 'request'):
            rows = copy.deepcopy(source); rows[0][key] = {} if key == 'request' else 'changed'; variants.append(rows)
        for rows in variants:
            with self.subTest(length=len(rows)), self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'report')

    def test_failed_result_provider_trace_and_state_concealment_fail(self):
        for mutation in ('failed', 'provider', 'provider_number_type', 'state', 'flags', 'missing_optional', 'evidence', 'owner'):
            rows = fixture.read('report-trajectory.json')
            if mutation == 'failed': rows[3]['response']['ok'] = False
            elif mutation == 'provider': rows[0]['provider_calls'].append(['changed'])
            elif mutation == 'provider_number_type':
                rows[0]['provider_calls'][0][1] = float(rows[0]['provider_calls'][0][1])
            elif mutation == 'state': rows[3]['after_state']['docs'][fixture.READ_PATH] += '\n'
            elif mutation == 'flags': rows[3]['state_unchanged'] = True
            elif mutation == 'missing_optional': rows[0]['response']['result']['missing_optional'] = []
            elif mutation == 'evidence': rows[1]['response']['result']['evidence'].pop()
            else: rows[1]['response']['result']['knowledge_owners'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'report')

    def test_stale_or_transplanted_semantic_review_fails(self):
        for key in ('verdict', 'bound_artifact_sha256', 'first_world_assessments'):
            review = fixture.read('semantic-method-review.json')
            review[key] = [] if key == 'first_world_assessments' else 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                fixture.verify_review_bindings(review)

    def test_correction_preserves_naming_original_evidence_and_one_occurrence(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        fixture.verify_state_invariants(before, after, 'report')
        for mutation in ('label', 'repeat', 'old_evidence', 'progress', 'extra_claim', 'extra_blob', 'missing_blob'):
            altered = copy.deepcopy(after)
            if mutation in ('label', 'extra_claim'):
                value = yaml.safe_load(altered['docs'][fixture.READ_PATH])
                capabilities = value['concepts']['token-identity']['capabilities']
                if mutation == 'label': capabilities['label_recall']['state'] = 'supported'
                else: capabilities['new_claim'] = copy.deepcopy(capabilities['label_recall'])
                altered['docs'][fixture.READ_PATH] = yaml.safe_dump(value)
            elif mutation == 'repeat': altered['docs']['evidence/duplicate.yaml'] = before['docs'][fixture.PERFORMANCE_PATH]
            elif mutation == 'old_evidence': altered['docs'][fixture.PERFORMANCE_PATH] += '# changed\n'
            elif mutation == 'extra_blob': altered['blobs']['evidence/unrelated.yaml'] = 'synthetic-token'
            elif mutation == 'missing_blob': altered['blobs'].pop(fixture.READ_PATH)
            else: altered['docs'][fixture.CHECKPOINT_PATH] += '# changed\n'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                fixture.verify_state_invariants(before, altered, 'report')

    def test_successor_rule_matches_actual_write_and_nonpersistent_control(self):
        provenance = fixture.read('successor-trigger-provenance.json')
        for case in ('baseline', 'report'):
            spec = fixture.read('publication-manifest.json')['cases'][case]
            decision = fixture.successor_decision(fixture.read(spec['initial_state_file']),
                fixture.read(spec['final_state_file']), fixture.read(case + '-trajectory.json'))
            self.assertEqual(case == 'report', decision['trigger'])
            frozen = provenance['seals'][case]['successor_decision']
            self.assertEqual(frozen, decision)
            fixture.verify_successor_decision(decision, frozen)
        self.assertEqual('report-post-state.json', fixture.read('publication-manifest.json')['cases']['successor']['initial_state_file'])

    def test_reconstructed_successor_decision_preserves_the_whole_frozen_contract(self):
        fields = {'status', 'trigger', 'payload_changed_paths', 'applied_owning_write_indices',
                  'full_state_changed', 'rule'}
        for case in ('baseline', 'report'):
            frozen = fixture.read('successor-trigger-provenance.json')['seals'][case]['successor_decision']
            self.assertEqual(fields, set(frozen))
            fixture.verify_successor_decision(copy.deepcopy(frozen), frozen)
            for field in fields:
                missing = copy.deepcopy(frozen); missing.pop(field)
                changed = copy.deepcopy(frozen); changed[field] = None
                for candidate in (missing, changed):
                    with self.subTest(case=case, field=field, candidate=candidate), self.assertRaises(ValueError):
                        fixture.verify_successor_decision(candidate, frozen)
                    with self.assertRaises(ValueError): fixture.verify_successor_decision(frozen, candidate)
                with self.assertRaises(ValueError): fixture.verify_successor_decision(missing, missing)
            for field in ('trigger', 'full_state_changed'):
                aliased = copy.deepcopy(frozen); aliased[field] = int(aliased[field])
                with self.assertRaises(ValueError): fixture.verify_successor_decision(aliased, frozen)
            extra = dict(frozen, unrecognized=True)
            with self.assertRaises(ValueError): fixture.verify_successor_decision(extra, frozen)
            for invalid in (None, [], 'invalid'):
                with self.assertRaises(ValueError): fixture.verify_successor_decision(invalid, frozen)
        frozen = fixture.read('successor-trigger-provenance.json')['seals']['report']['successor_decision']
        aliased = copy.deepcopy(frozen); aliased['applied_owning_write_indices'] = [4.0]
        with self.assertRaises(ValueError): fixture.verify_successor_decision(aliased, frozen)

    def test_payload_wording_changes_trigger_but_metadata_and_reversion_do_not(self):
        before = fixture.read('baseline-state.json')
        wording = copy.deepcopy(before)
        value = yaml.safe_load(wording['docs'][fixture.READ_PATH])
        value['concepts']['token-identity']['capabilities']['explanation']['basis_summary'] += ' Reworded.'
        wording['docs'][fixture.READ_PATH] = yaml.safe_dump(value, sort_keys=False)
        self.assertTrue(fixture.successor_decision(before, wording, [synthetic_row(before, wording)])['trigger'])
        metadata = copy.deepcopy(before)
        for path in (fixture.READ_PATH, fixture.CHECKPOINT_PATH):
            value = yaml.safe_load(metadata['docs'][path]); value['revision'] += 1; value['updated_at'] = '2026-10-10T00:00:00Z'
            if path == fixture.READ_PATH:
                value['concepts']['token-identity']['capabilities']['explanation']['updated_at'] = '2026-10-10T00:00:00Z'
            metadata['docs'][path] = yaml.safe_dump(value, sort_keys=False)
        knowledge_metadata = copy.deepcopy(before)
        knowledge_metadata['docs'][fixture.READ_PATH] = metadata['docs'][fixture.READ_PATH]
        rows = [synthetic_row(before, knowledge_metadata),
                synthetic_row(knowledge_metadata, metadata, 2, 'save_learning_checkpoint')]
        self.assertFalse(fixture.successor_decision(before, metadata, rows)['trigger'])
        reverted = [synthetic_row(before, wording), synthetic_row(wording, before, 2)]
        self.assertFalse(fixture.successor_decision(before, before, reverted)['trigger'])

    def test_integrity_and_malformed_payload_block_successor(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        for field in ('response', 'provider_calls', 'request_raw_base64', 'after_state', 'before_state_sha256'):
            rows = [synthetic_row(before, after)]; rows[0].pop(field)
            with self.subTest(field=field):
                decision = fixture.successor_decision(before, after, rows)
                self.assertEqual('blocked', decision['status']); self.assertFalse(decision['trigger'])
        for response in ({}, None, {'ok': True}, {'runner_exception': 'ValueError'}):
            rows = fixture.read('report-trajectory.json'); rows[0]['response'] = response
            with self.subTest(incomplete_response=response):
                self.assertEqual('blocked', fixture.successor_decision(before, after, rows)['status'])
        rows = fixture.read('report-trajectory.json')
        rows[3]['response'] = {'runner_exception': 'Injected', 'message': 'Failed before write',
                               'ok': True, 'result': {'applied': True}}
        self.assertEqual('blocked', fixture.successor_decision(before, after, rows)['status'])
        for request in (None, [], 'invalid', 3):
            rows = fixture.read('report-trajectory.json'); raw = fixture.encoded(request)
            rows[0].update(request=request, request_raw_base64=base64.b64encode(raw).decode('ascii'),
                           request_raw_utf8=raw.decode('utf-8'), request_sha256=fixture.digest(raw))
            with self.subTest(malformed_request=request):
                self.assertEqual('blocked', fixture.successor_decision(before, after, rows)['status'])
        altered = copy.deepcopy(after); altered['docs'][fixture.READ_PATH] = '['
        self.assertEqual('blocked', fixture.successor_decision(before, altered, [synthetic_row(before, altered)])['status'])
        self.assertEqual('blocked', fixture.successor_decision(before, after,
            [synthetic_row(before, after, operation='read_learning_context')])['status'])

    def test_host_response_has_exactly_one_result_branch(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        responses = [
            {'surface_version': 'v4', 'ok': True, 'operation': 'reconcile_knowledge',
             'result': {'applied': True}, 'error': {'code': 'guard_rejected'}},
            {'surface_version': 'v4', 'ok': False, 'operation': 'reconcile_knowledge',
             'result': {'applied': True}, 'error': {'code': 'guard_rejected'}},
            {'surface_version': 'v4', 'ok': True, 'operation': 'reconcile_knowledge',
             'result': {'applied': True}, 'extra': 'ambiguous'},
        ]
        for response in responses:
            rows = fixture.read('report-trajectory.json'); rows[3]['response'] = response
            with self.subTest(response=response):
                decision = fixture.successor_decision(before, after, rows)
                self.assertEqual('blocked', decision['status']); self.assertFalse(decision['trigger'])
        for response in (
            {'surface_version': 'v4', 'ok': False, 'operation': 'reconcile_knowledge',
             'error': {'code': 'guard_rejected', 'retryable': False}},
            {'runner_exception': 'Injected', 'message': 'Failed before write'},
        ):
            row = synthetic_row(before, before); row['response'] = response
            with self.subTest(valid_nonwriting_response=response):
                self.assertEqual('no_new_payload', fixture.successor_decision(before, before, [row])['status'])

    def test_request_shape_checks_source_dispatch_without_runtime_io(self):
        valid = {
            'read_learning_context': {'required_paths': [fixture.READ_PATH], 'optional_paths': []},
            'discover_learning_evidence': {},
            'reconcile_knowledge': {'content': fixture.read('report-state.json')['docs'][fixture.READ_PATH],
                                    'expected_version_token': None},
            'save_learning_checkpoint': {'checkpoint': {'milestone': [], 'return_point': None, 'ready_next': []},
                                         'expected_version_token': 'synthetic-version'},
            'create_evidence': {'content': fixture.read('report-state.json')['docs'][fixture.PERFORMANCE_PATH]},
            'set_intake_preference': {'scope': 'topic', 'depth': 'balanced', 'expected_version_token': None},
            'reset_intake_preference': {'scope': 'topic', 'expected_version_token': 'synthetic-version'},
        }
        self.assertEqual(set(valid), fixture.REFERENCE_HOST_OPERATIONS)
        with mock.patch.object(fixture.RuntimeSessionBroker, '__init__', side_effect=AssertionError('real broker')), \
                mock.patch.object(fixture.ReferenceLearningHost, 'open', side_effect=AssertionError('host session')), \
                mock.patch.object(fixture, 'make_provider', side_effect=AssertionError('provider')), \
                mock.patch.object(fixture.RuntimeSessionBroker, '_normalize_learning_checkpoint',
                    wraps=fixture.RuntimeSessionBroker._normalize_learning_checkpoint) as normalize:
            for operation, arguments in valid.items():
                request = {'operation': operation, 'arguments': arguments}
                original = copy.deepcopy(request)
                with self.subTest(operation=operation):
                    self.assertTrue(fixture.request_shape_admitted(request))
                    self.assertEqual(original, request)
            normalize.assert_called_once_with(valid['save_learning_checkpoint']['checkpoint'])

    def test_malformed_host_requests_cannot_trigger_successor(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        write = fixture.read('report-trajectory.json')[3]['request']
        invalid = [dict(copy.deepcopy(write), extra='unsupported'),
                   {'operation': 'unknown_operation', 'arguments': {}},
                   {'operation': '', 'arguments': {}},
                   {'operation': 'reconcile_knowledge', 'arguments': []}]
        for operation, arguments in (
            ('read_learning_context', {}),
            ('read_learning_context', {'required_paths': 'not an array'}),
            ('read_learning_context', {'required_paths': ['']}),
            ('read_learning_context', {'required_paths': [], 'optional_paths': [17]}),
            ('discover_learning_evidence', {'extra': True}),
            ('reconcile_knowledge', {'expected_version_token': None}),
            ('reconcile_knowledge', {'content': [], 'expected_version_token': None}),
            ('reconcile_knowledge', {'content': 'x', 'expected_version_token': 7}),
            ('reconcile_knowledge', {'content': 'x', 'expected_version_token': ''}),
            ('reconcile_knowledge', {'content': 'x', 'expected_version_token': None, 'extra': True}),
            ('create_evidence', {'content': []}),
            ('set_intake_preference', {'scope': 'x', 'expected_version_token': None}),
            ('reset_intake_preference', {'scope': 'x', 'expected_version_token': None, 'depth': 'x'}),
        ):
            invalid.append({'operation': operation, 'arguments': arguments})
        checkpoint = {'milestone': [], 'return_point': None, 'ready_next': []}
        for key, value in (('milestone', 'scalar'), ('milestone', ['duplicate', 'duplicate']),
                           ('ready_next', 'scalar'), ('ready_next', [7]), ('return_point', 'scalar'),
                           ('return_point', {'': 'invalid key'}), ('extra', True)):
            altered = dict(copy.deepcopy(checkpoint), **{key: value})
            invalid.append({'operation': 'save_learning_checkpoint',
                            'arguments': {'checkpoint': altered, 'expected_version_token': 'synthetic-version'}})
        for request in invalid:
            rows = fixture.read('report-trajectory.json')
            # Keep the real later owning write, so an invalid earlier read cannot
            # be hidden merely by the absence of an applied operation.
            replace_request(rows[0], request)
            with self.subTest(request=request):
                self.assertFalse(fixture.request_shape_admitted(request))
                decision = fixture.successor_decision(before, after, rows)
                self.assertEqual('blocked', decision['status']); self.assertFalse(decision['trigger'])

    def test_every_payload_delta_requires_its_same_receipt_owner(self):
        before, knowledge = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        progress = copy.deepcopy(before)
        value = yaml.safe_load(progress['docs'][fixture.CHECKPOINT_PATH])
        value['resume']['return_point']['activity'] += ' Changed.'
        progress['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(value, sort_keys=False)
        both = copy.deepcopy(knowledge); both['docs'][fixture.CHECKPOINT_PATH] = progress['docs'][fixture.CHECKPOINT_PATH]
        for after, operation in ((knowledge, 'save_learning_checkpoint'), (progress, 'reconcile_knowledge'),
                                  (both, 'reconcile_knowledge'), (both, 'save_learning_checkpoint')):
            with self.subTest(operation=operation, paths=fixture.changed_paths(before, after)):
                decision = fixture.successor_decision(before, after, [synthetic_row(before, after, operation=operation)])
                self.assertEqual('blocked', decision['status']); self.assertFalse(decision['trigger'])
        for after, operation in ((knowledge, 'reconcile_knowledge'), (progress, 'save_learning_checkpoint')):
            row = synthetic_row(before, after, operation=operation)
            self.assertTrue(fixture.successor_decision(before, after, [row])['trigger'])
            row['response']['result']['applied'] = False
            self.assertEqual('blocked', fixture.successor_decision(before, after, [row])['status'])
        rows = [synthetic_row(before, knowledge), synthetic_row(knowledge, both, 2, 'save_learning_checkpoint')]
        decision = fixture.successor_decision(before, both, rows)
        self.assertTrue(decision['trigger']); self.assertEqual([1, 2], decision['applied_owning_write_indices'])
        borrowed = [synthetic_row(before, before), synthetic_row(before, knowledge, 2, 'read_learning_context')]
        self.assertEqual('blocked', fixture.successor_decision(before, knowledge, borrowed)['status'])

    def test_malformed_full_states_are_blocked_without_exceptions(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        malformed = [None, [], 'state', {'docs': []}]
        for field, value in (('docs', []), ('docs', None), ('blobs', []), ('blobs', None),
                             ('instance_head', 7), ('snapshot_extra_paths', {}),
                             ('snapshot_extra_paths', ['missing-path']), ('extra', 'unrecognized')):
            altered = copy.deepcopy(after); altered[field] = value; malformed.append(altered)
        for field, value in (('docs', 7), ('blobs', None)):
            altered = copy.deepcopy(after); altered[field][fixture.READ_PATH] = value; malformed.append(altered)
        altered = copy.deepcopy(after); altered['blobs'].pop(fixture.READ_PATH); malformed.append(altered)
        altered = copy.deepcopy(after); altered['blobs'][fixture.READ_PATH] = ''; malformed.append(altered)
        for path in ('evidence/../../outside.yaml', '/tmp/outside.yaml'):
            altered = copy.deepcopy(after)
            altered['docs'][path] = 'content'; altered['blobs'][path] = 'synthetic-token'
            malformed.append(altered)
        altered = copy.deepcopy(after); altered.pop('instance_head'); malformed.append(altered)
        for state in malformed:
            row = synthetic_row(before, after); row['after_state'] = state
            row['after_state_sha256'] = fixture.digest(fixture.encoded(state))
            with self.subTest(state=state):
                self.assertEqual('blocked', fixture.successor_decision(state, state, [])['status'])
                self.assertEqual('blocked', fixture.successor_decision(before, state, [row])['status'])
                restored = synthetic_row(before, after, 2)
                restored['before_state_sha256'] = row['after_state_sha256']
                self.assertEqual('blocked', fixture.successor_decision(before, after, [row, restored])['status'])

    def test_full_state_side_effects_cannot_hide_behind_owned_payload(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        variants = []
        for field, path in (('docs', fixture.PERFORMANCE_PATH), ('blobs', fixture.PERFORMANCE_PATH),
                            ('blobs', fixture.CHECKPOINT_PATH)):
            changed = copy.deepcopy(after); changed[field][path] += 'changed'; variants.append(changed)
        for mode in ('add', 'delete'):
            changed = copy.deepcopy(after)
            for field in ('docs', 'blobs'):
                if mode == 'add': changed[field]['unknown/document.yaml'] = 'new'
                else: changed[field].pop(fixture.PERFORMANCE_PATH)
            variants.append(changed)
        changed = copy.deepcopy(after); changed['instance_head'] = 'changed'; variants.append(changed)
        changed = copy.deepcopy(after); changed['snapshot_extra_paths'] = []; variants.append(changed)
        for changed in variants:
            with self.subTest(changed=fixture.changed_paths(before, changed)):
                self.assertEqual('blocked', fixture.successor_decision(before, changed,
                    [synthetic_row(before, changed)])['status'])
        metadata = copy.deepcopy(before); metadata['docs'][fixture.READ_PATH] += '\n'
        for operation, response in (
            ('read_learning_context', {'surface_version': 'v4', 'ok': True,
                                      'operation': 'read_learning_context', 'result': {}}),
            ('reconcile_knowledge', {'surface_version': 'v4', 'ok': True,
                                    'operation': 'reconcile_knowledge', 'result': {'applied': False}}),
            ('reconcile_knowledge', {'surface_version': 'v4', 'ok': False,
                                    'operation': 'reconcile_knowledge', 'error': {'code': 'guard_rejected', 'retryable': False}}),
            ('reconcile_knowledge', {'runner_exception': 'Injected', 'message': 'failed'}),
        ):
            row = synthetic_row(before, metadata, operation=operation); row['response'] = response
            with self.subTest(operation=operation, response=response):
                self.assertEqual('blocked', fixture.successor_decision(before, metadata, [row])['status'])

    def test_broker_payload_gates_and_frozen_consumer_scope(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        for content in ('syntactic content only', '[]', 'domain: x\ndomain: y\n',
                        'document_type: learner_knowledge\ndomain: synthetic\nconcepts: []\n'):
            row = synthetic_row(before, after); request = copy.deepcopy(row['request'])
            request['arguments']['content'] = content; replace_request(row, request)
            with self.subTest(content=content):
                self.assertFalse(fixture.request_shape_admitted(request))
                self.assertEqual('blocked', fixture.successor_decision(before, after, [row])['status'])
        for operation, arguments in (
            ('create_evidence', {'content': before['docs'][fixture.PERFORMANCE_PATH]}),
            ('set_intake_preference', {'scope': 'topic', 'depth': 'balanced', 'expected_version_token': None}),
            ('reset_intake_preference', {'scope': 'topic', 'expected_version_token': None}),
        ):
            row = synthetic_row(before, before); request = {'operation': operation, 'arguments': arguments}
            replace_request(row, request)
            with self.subTest(operation=operation):
                self.assertTrue(fixture.request_shape_admitted(request))
                self.assertEqual('blocked', fixture.successor_decision(before, before, [row])['status'])
        self.assertFalse(fixture.request_shape_admitted({'operation': 'create_evidence', 'arguments': {'content': 'scalar'}}))
        for paths in ([], [fixture.READ_PATH, fixture.READ_PATH], ['../outside.yaml']):
            self.assertFalse(fixture.request_shape_admitted({'operation': 'read_learning_context',
                                                            'arguments': {'required_paths': paths}}))

    def test_write_candidates_are_bound_to_owned_outputs(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        for mismatch in ('unchanged_content', 'wrong_domain'):
            row = synthetic_row(before, after); request = copy.deepcopy(row['request'])
            if mismatch == 'unchanged_content': request['arguments']['content'] = before['docs'][fixture.READ_PATH]
            else:
                candidate = yaml.safe_load(request['arguments']['content']); candidate['domain'] = 'elsewhere'
                request['arguments']['content'] = yaml.safe_dump(candidate)
            replace_request(row, request)
            with self.subTest(mismatch=mismatch):
                self.assertEqual('blocked', fixture.successor_decision(before, after, [row])['status'])
        progress = copy.deepcopy(before); document = yaml.safe_load(progress['docs'][fixture.CHECKPOINT_PATH])
        document['resume']['return_point']['activity'] += ' changed'
        progress['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        row = synthetic_row(before, progress, operation='save_learning_checkpoint')
        row['request']['arguments']['checkpoint']['return_point']['activity'] += ' other'
        replace_request(row, row['request'])
        self.assertEqual('blocked', fixture.successor_decision(before, progress, [row])['status'])
        document['milestones']['injected'] = {'state': 'invented'}
        progress['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        self.assertEqual('blocked', fixture.successor_decision(before, progress,
            [synthetic_row(before, progress, operation='save_learning_checkpoint')])['status'])

    def test_integrity_preserves_scalar_types_and_bounded_yaml(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        for field, value in (('current', []), ('resume', []), ('milestones', []), ('ready_next', 'scalar'),
                             ('empty_milestone', None)):
            malformed = yaml.safe_load(before['docs'][fixture.CHECKPOINT_PATH])
            if field == 'ready_next': malformed['resume'][field] = value
            elif field == 'empty_milestone':
                malformed['milestones'][''] = {}
                malformed['resume']['return_point'] = {'milestone': ''}
            else: malformed[field] = value
            left, right = copy.deepcopy(before), copy.deepcopy(after)
            left['docs'][fixture.CHECKPOINT_PATH] = right['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(malformed)
            row = synthetic_row(before, after)
            row.update(before_state_sha256=fixture.digest(fixture.encoded(left)), after_state=right,
                       after_state_sha256=fixture.digest(fixture.encoded(right)))
            with self.subTest(malformed_unchanged_progress=field):
                self.assertEqual('blocked', fixture.successor_decision(left, left, [])['status'])
                self.assertEqual('blocked', fixture.successor_decision(left, right, [row])['status'])
        for field in ('index', 'state_unchanged'):
            row = synthetic_row(before, after); row[field] = True if field == 'index' else 0
            self.assertEqual('blocked', fixture.successor_decision(before, after, [row])['status'])
        for suffix in ('\nconcepts: {}\n', '\nrecursive: &loop [*loop]\n'):
            changed = copy.deepcopy(after); changed['docs'][fixture.READ_PATH] += suffix
            self.assertEqual('blocked', fixture.successor_decision(before, changed,
                [synthetic_row(before, changed)])['status'])
        left = copy.deepcopy(before); document = yaml.safe_load(left['docs'][fixture.CHECKPOINT_PATH])
        document['resume']['return_point']['typed_marker'] = True
        left['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        right = copy.deepcopy(left); document['resume']['return_point']['typed_marker'] = 1
        right['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        row = synthetic_row(left, right, operation='save_learning_checkpoint')
        self.assertTrue(fixture.successor_decision(left, right, [row])['trigger'])
        row['request']['arguments']['checkpoint']['return_point']['typed_marker'] = True
        self.assertEqual('blocked', fixture.successor_decision(left, right, [row])['status'])

    def test_changed_source_fixture_dependency_fails(self):
        fixture.verify_dependencies()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = fixture.read('publication-manifest.json')['dependencies']
            for name in names:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((fixture.ROOT / name).read_bytes())
            fixture.verify_dependencies(root)
            (root / next(iter(names))).write_text('changed')
            with self.assertRaises(ValueError): fixture.verify_dependencies(root)

    def test_modified_or_extra_fixture_file_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'fixtures'; shutil.copytree(fixture.FIXTURES, target)
            path = target / 'baseline-response.json'; original = path.read_bytes(); path.write_bytes(original + b'\n')
            with self.assertRaises(ValueError): fixture.verify_artifacts(target)
            path.write_bytes(original); (target / 'surplus.json').write_text('{}')
            with self.assertRaises(ValueError): fixture.verify_artifacts(target)

    def test_all_eleven_actual_requests_replay_without_repair(self):
        rows = fixture.replay()
        self.assertEqual([3, 5, 3], [len(row['receipts']) for row in rows])
        self.assertEqual([False, True, False], [row['successor_trigger'] for row in rows])

    def test_actual_replay_rejects_result_trace_and_state_divergence(self):
        real_opened = fixture.opened
        worlds = {'baseline': fixture.read('baseline-state.json'), 'report': fixture.read('report-state.json')}
        for mode in ('result', 'result_bool_type', 'trace', 'trace_number_type', 'state'):
            @contextmanager
            def corrupted(provider, **kwargs):
                with real_opened(provider, **kwargs) as host:
                    class Proxy:
                        def invoke(self, request):
                            result = host.invoke(request)
                            if mode == 'result': result['result']['documents'][0]['content'] += '\n'
                            elif mode == 'result_bool_type': result['ok'] = 1
                            elif mode == 'trace': provider.calls.append(('operator-negative',))
                            elif mode == 'trace_number_type':
                                call = list(provider.calls[-1]); call[1] = float(call[1]); provider.calls[-1] = tuple(call)
                            else: provider.docs[fixture.READ_PATH] += '# operator-negative\n'
                            return result
                    yield Proxy()
            with self.subTest(mode=mode), mock.patch.object(fixture, 'build_worlds', return_value=copy.deepcopy(worlds)), \
                    mock.patch.object(fixture, 'opened', corrupted), self.assertRaises(ValueError):
                fixture.replay()

    def test_export_rejects_existing_and_resolved_in_core_before_replay(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(fixture, 'replay') as replay:
            existing = Path(temporary) / 'file'; existing.write_text('keep')
            for destination in (existing, fixture.ROOT, fixture.ROOT / 'not-created' / '..' / 'another-output'):
                with self.subTest(destination=str(destination)), self.assertRaises(ValueError): fixture.export_replay(destination)
            replay.assert_not_called()
            self.assertEqual('keep', existing.read_text())

    def test_export_rejects_symlink_alias_into_core(self):
        with tempfile.TemporaryDirectory() as temporary:
            alias = Path(temporary) / 'alias'
            try: alias.symlink_to(fixture.ROOT, target_is_directory=True)
            except (OSError, NotImplementedError): self.skipTest('symlink creation is unavailable')
            with mock.patch.object(fixture, 'replay') as replay, self.assertRaises(ValueError):
                fixture.export_replay(alias / 'not-created')
            replay.assert_not_called()


if __name__ == '__main__':
    unittest.main()
