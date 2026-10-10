"""Regression tests for a retained bounded witness, not a universal teaching oracle."""
import copy
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

from tests import autonomous_correction_fixture as fixture


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

    def test_counterfactual_projection_preserves_wording_metadata_and_reversion_rule(self):
        before = fixture.read('baseline-state.json')
        wording = copy.deepcopy(before)
        value = yaml.safe_load(wording['docs'][fixture.READ_PATH])
        value['concepts']['token-identity']['capabilities']['explanation']['basis_summary'] += ' Reworded.'
        value['revision'] += 1
        wording['docs'][fixture.READ_PATH] = yaml.safe_dump(value, sort_keys=False)
        self.assertEqual([fixture.READ_PATH], fixture.payload_delta(before, wording))
        metadata = copy.deepcopy(before)
        for path in (fixture.READ_PATH, fixture.CHECKPOINT_PATH):
            value = yaml.safe_load(metadata['docs'][path]); value['revision'] += 1
            value['updated_at'] = '2026-10-10T00:00:00Z'
            if path == fixture.READ_PATH:
                value['concepts']['token-identity']['capabilities']['explanation']['updated_at'] = '2026-10-10T00:00:00Z'
            metadata['docs'][path] = yaml.safe_dump(value, sort_keys=False)
        self.assertEqual([], fixture.payload_delta(before, metadata))
        reverted = copy.deepcopy(before); value = yaml.safe_load(reverted['docs'][fixture.READ_PATH])
        value['revision'] += 2; reverted['docs'][fixture.READ_PATH] = yaml.safe_dump(value, sort_keys=False)
        self.assertEqual([], fixture.payload_delta(before, reverted)); self.assertNotEqual(before, reverted)
        progress = copy.deepcopy(before); value = yaml.safe_load(progress['docs'][fixture.CHECKPOINT_PATH])
        value['resume']['ready_next'] = ['hypothetical next activity']
        progress['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(value, sort_keys=False)
        self.assertEqual([fixture.CHECKPOINT_PATH], fixture.payload_delta(before, progress))
        # These are proposed states, not observed/authorized operation receipts.
        for after in (wording, metadata, reverted, progress):
            decision = fixture.successor_decision(before, after, [])
            self.assertEqual('publication binding', decision['reason']); self.assertFalse(decision['trigger'])

    def test_counterfactual_projection_is_typed_and_rejects_malformed_payloads(self):
        before = fixture.read('report-state.json')
        left = copy.deepcopy(before); document = yaml.safe_load(left['docs'][fixture.CHECKPOINT_PATH])
        document['resume']['return_point']['typed_marker'] = True
        left['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        right = copy.deepcopy(left); document['resume']['return_point']['typed_marker'] = 1
        right['docs'][fixture.CHECKPOINT_PATH] = yaml.safe_dump(document)
        self.assertEqual([fixture.CHECKPOINT_PATH], fixture.payload_delta(left, right))
        for content in ('[', 'scalar', 'document_type: learner_knowledge\nconcepts: []\n',
                        before['docs'][fixture.READ_PATH] + '\nconcepts: {}\n'):
            malformed = copy.deepcopy(before); malformed['docs'][fixture.READ_PATH] = content
            with self.subTest(content=content), self.assertRaises((ValueError, fixture.GuardRejected,
                                                                  fixture.ResolutionError, yaml.YAMLError)):
                fixture.payload_delta(before, malformed)

    def test_retained_case_decisions_and_successor_recursion_boundary(self):
        fields = {'status', 'trigger', 'payload_changed_paths', 'applied_owning_write_indices',
                  'full_state_changed', 'rule'}
        manifest = fixture.read('publication-manifest.json')
        provenance = fixture.read('successor-trigger-provenance.json')
        count = 0
        for case, spec in manifest['cases'].items():
            before, after = fixture.read(spec['initial_state_file']), fixture.read(spec['final_state_file'])
            rows = fixture.read(case + '-trajectory.json'); count += len(rows)
            self.assertEqual([], fixture.receipt_integrity_errors(before, after, rows))
            self.assertEqual(rows, fixture.validate_trajectory(rows, case))
            decision = fixture.successor_decision(before, after, rows)
            frozen = provenance['seals'][case]['successor_decision']
            self.assertEqual(fields, set(decision))
            if case == 'successor':
                # The original controller seals a two-field no-recursion result;
                # the publication helper's unchanged projection has six fields.
                self.assertEqual({'status': 'successor_no_recursion', 'trigger': False}, frozen)
                self.assertEqual({'status': 'no_new_payload', 'trigger': False, 'payload_changed_paths': [],
                                  'applied_owning_write_indices': [], 'full_state_changed': False,
                                  'rule': 'frozen payload projection; semantic interpretation is separate'}, decision)
            else:
                self.assertTrue(fixture.same_record(frozen, decision))
        self.assertEqual(11, count)

    def test_every_receipt_field_is_bound_without_an_accepted_subset(self):
        manifest = fixture.read('publication-manifest.json')
        expected = {'index', 'at_started', 'before_state_sha256', 'request_raw_base64', 'request_sha256',
                    'request_raw_utf8', 'request', 'provider_calls', 'after_state', 'after_state_sha256',
                    'state_unchanged', 'changed_paths', 'response', 'at_finished', 'source_log_sha256', 'omitted_fields'}
        for case, spec in manifest['cases'].items():
            before, after = fixture.read(spec['initial_state_file']), fixture.read(spec['final_state_file'])
            source = fixture.read(case + '-trajectory.json')
            for index, original in enumerate(source):
                self.assertEqual(expected, set(original))
                for field in expected | {'extra'}:
                    for mutation in ('omit', 'replace'):
                        rows = copy.deepcopy(source)
                        if mutation == 'omit' and field != 'extra': rows[index].pop(field)
                        else: rows[index][field] = None
                        with self.subTest(case=case, index=index, field=field, mutation=mutation):
                            self.assertTrue(fixture.receipt_integrity_errors(before, after, rows))
                            decision = fixture.successor_decision(before, after, rows)
                            self.assertEqual('publication binding', decision['reason']); self.assertFalse(decision['trigger'])
                            with self.assertRaises(ValueError): fixture.validate_trajectory(rows, case)

    def test_traces_and_exact_state_bytes_cannot_be_forged_or_laundered(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        source = fixture.read('report-trajectory.json')
        for mutation in ('empty_trace', 'unrelated_trace', 'wrong_target', 'stale_token', 'omit_cas',
                         'reorder_trace', 'extra_trace', 'provider_number_type', 'result_bool_type',
                         'checkpoint_comments', 'checkpoint_key_order', 'knowledge_comments', 'raw_request'):
            rows = copy.deepcopy(source); final = copy.deepcopy(after)
            trace = rows[3]['provider_calls']
            if mutation == 'empty_trace': rows[3]['provider_calls'] = []
            elif mutation == 'unrelated_trace': rows[3]['provider_calls'] = [['unrelated']]
            elif mutation == 'wrong_target': trace[-1][3] = fixture.CHECKPOINT_PATH
            elif mutation == 'stale_token': trace[-1][4] = 'forged'
            elif mutation == 'omit_cas': trace[-1].pop()
            elif mutation == 'reorder_trace': trace.reverse()
            elif mutation == 'extra_trace': trace.append(copy.deepcopy(trace[-1]))
            elif mutation == 'provider_number_type': trace[-1][1] = float(trace[-1][1])
            elif mutation == 'result_bool_type': rows[3]['response']['ok'] = 1
            elif mutation == 'raw_request': rows[3]['request_raw_utf8'] += ' '
            else:
                path = fixture.READ_PATH if mutation == 'knowledge_comments' else fixture.CHECKPOINT_PATH
                for row in rows:
                    if mutation == 'checkpoint_key_order':
                        row['after_state']['docs'][path] = yaml.safe_dump(yaml.safe_load(row['after_state']['docs'][path]), sort_keys=True)
                    else: row['after_state']['docs'][path] += '\n# fabricated serialization\n'
                    row['after_state_sha256'] = fixture.digest(fixture.encoded(row['after_state']))
                final = rows[-1]['after_state']
                # Even rehashing a complete forged chain cannot authenticate it.
                current = before
                for row in rows:
                    row['before_state_sha256'] = fixture.digest(fixture.encoded(current))
                    row['changed_paths'] = fixture.changed_paths(current, row['after_state'])
                    row['state_unchanged'] = current == row['after_state']; current = row['after_state']
            with self.subTest(mutation=mutation):
                self.assertTrue(fixture.receipt_integrity_errors(before, final, rows))
                self.assertEqual('blocked', fixture.successor_decision(before, final, rows)['status'])
                with self.assertRaises(ValueError): fixture.validate_trajectory(rows, 'report')

    def test_unknown_or_malformed_transcripts_are_unbound_not_observed_failures(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        source = fixture.read('report-trajectory.json')
        for value in (None, [], 'invalid', 3, {'docs': []}):
            for left, right, rows in ((value, after, source), (before, value, source), (before, after, value)):
                decision = fixture.successor_decision(left, right, rows)
                self.assertEqual('blocked', decision['status']); self.assertEqual('publication binding', decision['reason'])
        for rows in (source[:1], source[:-1], source + [source[-1]], list(reversed(source))):
            self.assertTrue(fixture.receipt_integrity_errors(before, after, rows))
        for response in ({'runner_exception': 'Injected', 'message': 'not an observed consumer failure'},
                         {'surface_version': 'v4', 'ok': False, 'operation': 'reconcile_knowledge',
                          'error': {'code': 'guard_rejected', 'retryable': False}}):
            rows = copy.deepcopy(source); rows[3]['response'] = response
            self.assertEqual('publication binding', fixture.successor_decision(before, after, rows)['reason'])

    def test_pinned_publication_admission_hashes_the_same_bytes_it_parses(self):
        real_read = Path.read_bytes
        for filename in ('publication-manifest.json', 'report-trajectory.json', 'report-state.json', 'report-post-state.json',
                         'mechanical-exact-replay.json'):
            def changed(path):
                raw = real_read(path)
                return raw + b' ' if path.name == filename else raw
            with self.subTest(filename=filename), mock.patch.object(Path, 'read_bytes', changed), self.assertRaises(ValueError):
                fixture._load_publication_evidence()
        calls = []
        def counted(path): calls.append(path.name); return real_read(path)
        with mock.patch.object(Path, 'read_bytes', counted): fixture._load_publication_evidence()
        self.assertEqual(len(calls), len(set(calls)))

    def test_case_identity_cannot_be_relabelled_by_unpinned_metadata(self):
        source = fixture.read('successor-trajectory.json')
        state = fixture.read('report-post-state.json')
        real_read = fixture.read
        def invented(name):
            result = real_read(name)
            if name == 'publication-manifest.json': result['cases']['invented'] = copy.deepcopy(result['cases']['successor'])
            elif name == 'mechanical-exact-replay.json': result['invented'] = copy.deepcopy(result['successor'])
            return result
        with mock.patch.object(fixture, 'read', invented):
            self.assertTrue(fixture.receipt_integrity_errors(state, state, source, case='invented'))
            with self.assertRaises(ValueError): fixture.validate_trajectory(source, 'invented')
        for case in ('baseline', 'report'):
            self.assertTrue(fixture.receipt_integrity_errors(state, state, source, case=case))
            with self.assertRaises(ValueError): fixture.validate_trajectory(source, case)

    def test_publication_binding_and_projection_are_pure_and_preserve_inputs(self):
        before, after = fixture.read('report-state.json'), fixture.read('report-post-state.json')
        rows = fixture.read('report-trajectory.json'); original = copy.deepcopy((before, after, rows))
        with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('filesystem read')), \
                mock.patch('builtins.open', side_effect=AssertionError('filesystem open')), \
                mock.patch.object(fixture, 'opened', side_effect=AssertionError('runtime opening')), \
                mock.patch.object(fixture, 'make_provider', side_effect=AssertionError('provider')):
            self.assertEqual([], fixture.receipt_integrity_errors(before, after, rows))
            self.assertEqual(rows, fixture.validate_trajectory(rows, 'report'))
            self.assertEqual([fixture.READ_PATH], fixture.payload_delta(before, after))
            self.assertEqual('triggered', fixture.successor_decision(before, after, rows)['status'])
            rows[3]['provider_calls'] = []
            self.assertEqual('blocked', fixture.successor_decision(before, after, rows)['status'])
        rows[3]['provider_calls'] = original[2][3]['provider_calls']
        self.assertEqual(original, (before, after, rows))

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
