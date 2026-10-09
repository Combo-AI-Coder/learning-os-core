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
    request = {'operation': operation, 'arguments': {}}
    raw = fixture.encoded(request)
    return {'index': index, 'request': request, 'request_raw_base64': base64.b64encode(raw).decode(),
            'request_raw_utf8': raw.decode(), 'request_sha256': fixture.digest(raw),
            'response': {'surface_version': 'v4', 'ok': True, 'operation': operation, 'result': {'applied': True}},
            'provider_calls': [],
            'before_state_sha256': fixture.digest(fixture.encoded(before)),
            'after_state': copy.deepcopy(after), 'after_state_sha256': fixture.digest(fixture.encoded(after)),
            'state_unchanged': before == after, 'changed_paths': fixture.changed_paths(before, after)}


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
        for mutation in ('failed', 'provider', 'state', 'flags', 'missing_optional', 'evidence', 'owner'):
            rows = fixture.read('report-trajectory.json')
            if mutation == 'failed': rows[3]['response']['ok'] = False
            elif mutation == 'provider': rows[0]['provider_calls'].append(['changed'])
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
        for mutation in ('label', 'repeat', 'old_evidence', 'progress', 'extra_claim'):
            altered = copy.deepcopy(after)
            if mutation in ('label', 'extra_claim'):
                value = yaml.safe_load(altered['docs'][fixture.READ_PATH])
                capabilities = value['concepts']['token-identity']['capabilities']
                if mutation == 'label': capabilities['label_recall']['state'] = 'supported'
                else: capabilities['new_claim'] = copy.deepcopy(capabilities['label_recall'])
                altered['docs'][fixture.READ_PATH] = yaml.safe_dump(value)
            elif mutation == 'repeat': altered['docs']['evidence/duplicate.yaml'] = before['docs'][fixture.PERFORMANCE_PATH]
            elif mutation == 'old_evidence': altered['docs'][fixture.PERFORMANCE_PATH] += '# changed\n'
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
            self.assertEqual(provenance['seals'][case]['successor_decision']['status'], decision['status'])
        self.assertEqual('report-post-state.json', fixture.read('publication-manifest.json')['cases']['successor']['initial_state_file'])

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
        self.assertFalse(fixture.successor_decision(before, metadata, [synthetic_row(before, metadata)])['trigger'])
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
        self.assertEqual('anomaly', fixture.successor_decision(before, after,
            [synthetic_row(before, after, operation='read_learning_context')])['status'])

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
        for mode in ('result', 'trace', 'state'):
            @contextmanager
            def corrupted(provider, **kwargs):
                with real_opened(provider, **kwargs) as host:
                    class Proxy:
                        def invoke(self, request):
                            result = host.invoke(request)
                            if mode == 'result': result['result']['documents'][0]['content'] += '\n'
                            elif mode == 'trace': provider.calls.append(('operator-negative',))
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
