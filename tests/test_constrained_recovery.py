"""Frozen first observations and host mechanics, not a teaching-quality oracle."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

from scripts.runtime_adapter import ResolutionError
from scripts.runtime_broker import EVIDENCE_DISCOVERY_MAX_RECORDS
from tests.constrained_recovery_fixture import (
    ARMS, FIXTURES, ROOT, READ_PATH, CHECKPOINT_PATH, PERFORMANCE_PATH,
    encoded, read, frozen_clock, setup, remove_original, open_reader,
    recover, packet, replay, export_replay, verify_artifacts, verify_replay_rows,
)


class ConstrainedRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual = replay()

    def test_exact_frozen_inventory_and_all_bytes(self):
        verify_artifacts(FIXTURES)
        manifest = read('publication-manifest.json')
        expected = {
            'audit-errata.json', 'cedar-packet.json', 'cedar-response.json',
            'complementary-review.json', 'consumer-bindings.json', 'exact-replay.json',
            'iris-packet.json', 'iris-response.json', 'juniper-packet.json',
            'juniper-response.json', 'laurel-packet.json', 'maple-packet.json',
            'maple-response.json', 'mechanical-input-check.json', 'preconsumer-manifest.json',
            'predispatch-clock-receipt.json', 'prospective-plan.json', 'result-summary.json',
            'scope-clarifications.json', 'initial-prospective-plan.json',
            'initial-preparation-failure.json',
        }
        self.assertEqual(expected, set(manifest['files']))
        self.assertEqual(expected - {'initial-preparation-failure.json'}, set(manifest['byte_copied']))

    def test_nested_tampering_and_surplus_inventory_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for p in FIXTURES.iterdir():
                (root / p.name).write_bytes(p.read_bytes())
            for name in ('iris-packet.json', 'cedar-response.json'):
                original = (root / name).read_bytes()
                changed = json.loads(original)
                if name.endswith('packet.json'):
                    changed['recovery_operations'][-1]['response']['ok'] = True
                else:
                    changed['host_requests'][0]['arguments']['expected_version_token'] = '0' * 40
                (root / name).write_bytes(encoded(changed))
                with self.assertRaisesRegex(AssertionError, 'Frozen artifact changed'):
                    verify_artifacts(root)
                (root / name).write_bytes(original)
            (root / 'surplus.json').write_bytes(b'{}')
            with self.assertRaisesRegex(AssertionError, 'inventory'):
                verify_artifacts(root)

    def test_replay_receipt_coverage_and_failure_are_not_silently_accepted(self):
        original = self.actual['exact_replay']
        variants = [[], original[:-1], original + [original[0]]]
        changed = copy.deepcopy(original)
        changed[0]['request_results'][0]['ok'] = False
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed[0]['evidence_and_progress_unchanged'] = False
        variants.append(changed)
        for rows in variants:
            with self.subTest(count=len(rows)), self.assertRaises(AssertionError):
                verify_replay_rows(rows)

    def test_five_conditions_have_four_unique_inputs_and_first_outputs(self):
        manifest = read('preconsumer-manifest.json')
        self.assertEqual(list(dict(ARMS)), [r['arm'] for r in manifest['rows']])
        self.assertEqual(['cedar', 'iris', 'juniper', 'maple'], manifest['unique_packets'])
        self.assertEqual(4, len(read('consumer-bindings.json')['rows']))
        self.assertEqual((FIXTURES / 'iris-packet.json').read_bytes(),
                         (FIXTURES / 'laurel-packet.json').read_bytes())
        for row in manifest['rows']:
            self.assertEqual(row['packet_sha256'], hashlib.sha256(
                (FIXTURES / (row['alias'] + '-packet.json')).read_bytes()).hexdigest())
        for row in read('consumer-bindings.json')['rows']:
            self.assertEqual(row['output_sha256'], hashlib.sha256(
                (FIXTURES / row['output']).read_bytes()).hexdigest())

    def test_actual_nested_traces_and_common_facts_reproduce(self):
        historical = {r['arm']: r for r in read('preconsumer-manifest.json')['rows']}
        for row in self.actual['mechanics']:
            self.assertEqual(historical[row['arm']]['provider_calls'],
                             json.loads(encoded(row['provider_calls'])))
            self.assertEqual(historical[row['arm']]['underlying_evidence_count'],
                             row['underlying_evidence_count'])
        for arm, alias in ARMS:
            actual = self.actual['packets'][alias]
            self.assertEqual((FIXTURES / (alias + '-packet.json')).read_bytes(), encoded(actual))
            self.assertEqual(actual['recovery_operations'][0],
                             self.actual['packets']['cedar']['recovery_operations'][0])
            self.assertEqual(actual['product_protocols'],
                             self.actual['packets']['cedar']['product_protocols'])
            self.assertTrue(all(t['request']['operation'] in
                {'read_learning_context', 'discover_learning_evidence'}
                for t in actual['recovery_operations']))

    def test_denied_and_actual_matching_record_budget_fail_closed(self):
        mechanics = {row['arm']: row for row in self.actual['mechanics']}
        evidence_reads = lambda row: [c[-1] for c in row['provider_calls']
            if c[0] == 'snapshot_read' and c[-1].startswith('evidence/')]
        self.assertEqual([], evidence_reads(mechanics['denied']))
        self.assertEqual(EVIDENCE_DISCOVERY_MAX_RECORDS + 1,
                         mechanics['budget']['underlying_evidence_count'])
        self.assertEqual(EVIDENCE_DISCOVERY_MAX_RECORDS + 1,
                         len(evidence_reads(mechanics['budget'])))
        for alias in ('iris', 'laurel'):
            trace = self.actual['packets'][alias]['recovery_operations']
            self.assertEqual(2, len(trace))
            self.assertEqual({'surface_version': 'v4', 'ok': False,
                'operation': 'discover_learning_evidence',
                'error': {'code': 'guard_rejected', 'retryable': False}}, trace[-1]['response'])

    def test_at_limit_discovery_control_succeeds_without_model_sampling(self):
        with frozen_clock(), setup('budget', budget_excess=0) as journey:
            with open_reader(journey, 'budget') as host:
                result = host.invoke({'operation': 'discover_learning_evidence', 'arguments': {}})
            self.assertTrue(result['ok'], result)
            self.assertEqual(EVIDENCE_DISCOVERY_MAX_RECORDS, len(result['result']['evidence']))
            self.assertEqual(journey.before, journey.provider.docs)
            self.assertEqual(journey.before_blobs, journey.provider.blobs)

    def test_initial_missing_reference_blocks_bootstrap(self):
        with frozen_clock(), setup('missing') as journey:
            remove_original(journey, advance_head=False)
            with self.assertRaisesRegex(ResolutionError, 'reference.evidence_missing'):
                open_reader(journey, 'missing')

    def test_post_bootstrap_loss_keeps_report_but_fails_required_original_read(self):
        trace = self.actual['packets']['juniper']['recovery_operations']
        self.assertEqual([True, True, False], [r['response']['ok'] for r in trace])
        discovery = trace[1]['response']['result']
        self.assertEqual(['evidence/evi-synthetic-report-002.yaml'],
                         [r['path'] for r in discovery['evidence']])
        self.assertIn(PERFORMANCE_PATH, trace[2]['request']['arguments']['required_paths'])
        self.assertEqual('guard_rejected', trace[2]['response']['error']['code'])
        self.assertNotIn('result', trace[2]['response'])
        self.assertTrue(all(r['post_injection_state_unchanged_by_reads'] for r in self.actual['mechanics']))

    def test_successful_no_report_is_explicitly_scoped(self):
        trace = self.actual['packets']['maple']['recovery_operations']
        self.assertTrue(all(r['response']['ok'] for r in trace))
        discovery = trace[1]['response']['result']
        self.assertEqual({'topic': 'synthetic', 'subtopic': 'unit'}, discovery['scope'])
        self.assertEqual('all_context_matches_in_bounded_snapshot', discovery['coverage'])
        self.assertEqual([PERFORMANCE_PATH], [r['path'] for r in discovery['evidence']])

    def test_unchanged_first_requests_replay_and_preserve_unaffected_state(self):
        rows = self.actual['exact_replay']
        self.assertEqual(read('exact-replay.json'), rows)
        self.assertEqual(5, len(rows))
        for row in rows:
            self.assertEqual(read(row['response_artifact'])['host_requests'], row['requests'])
        self.assertEqual([READ_PATH], rows[0]['changed_paths'])
        self.assertTrue(rows[0]['evidence_and_progress_unchanged'])
        self.assertTrue(rows[0]['request_results'][0]['result']['applied'])
        self.assertEqual([{'surface_version': 'v4', 'ok': False,
            'operation': 'reconcile_knowledge',
            'error': {'code': 'guard_rejected', 'retryable': False}}],
            self.actual['mechanics'][0]['readonly_request_results'])
        initial = yaml.safe_load(next(d['content'] for d in
            self.actual['packets']['cedar']['recovery_operations'][0]['response']['result']['documents']
            if d['path'] == READ_PATH))
        final = yaml.safe_load(rows[0]['final_knowledge'])
        prior = initial['concepts']['token-identity']['capabilities']
        claims = final['concepts']['token-identity']['capabilities']
        self.assertEqual({'label_recall'}, set(claims))
        self.assertEqual(prior['label_recall'], claims['label_recall'])
        self.assertTrue(all(not r['requests'] and not r['changed_paths'] for r in rows[1:]))

    def test_historical_policy_is_not_reloaded_from_live_owner(self):
        original = Path.read_text
        def changed(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return value + '\nLater live guidance.\n' if path.parent == ROOT / 'protocol' else value
        with mock.patch.object(Path, 'read_text', changed):
            self.assertEqual(self.actual['packets']['cedar'],
                             packet(self.actual['packets']['cedar']['recovery_operations']))

    def test_failed_preparation_and_annotation_erratum_remain_visible(self):
        self.assertIn('before any consumers', read('initial-preparation-failure.json')['status'])
        self.assertIn('reference.evidence_missing', read('initial-preparation-failure.json')['observed_error'])
        self.assertIn('seven hours', read('predispatch-clock-receipt.json')['note'])
        self.assertIn('incorrect', read('audit-errata.json')['finding'])
        review = read('complementary-review.json')
        self.assertTrue(any(x['kind'] == 'receipt_annotation_error' for x in review['audit_findings']))
        self.assertIn('partial/open', read('result-summary.json')['status'])

    def test_mutated_request_is_rejected_without_repair(self):
        with frozen_clock(), setup('full') as journey:
            recover(journey, 'full')
            request = copy.deepcopy(read('cedar-response.json')['host_requests'][0])
            request['arguments']['expected_version_token'] = '0' * 40
            before = copy.deepcopy(journey.provider.docs)
            result = journey.apply(request)
            self.assertFalse(result['ok'])
            self.assertEqual(before, journey.provider.docs)

    def test_export_refuses_existing_and_core_destinations_without_replay(self):
        for destination in (ROOT, FIXTURES / 'generated'):
            with self.subTest(destination=destination), mock.patch(
                    'tests.constrained_recovery_fixture.replay') as execute:
                with self.assertRaises(ValueError):
                    export_replay(destination)
                execute.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / 'sentinel').write_bytes(b'unchanged')
            with mock.patch('tests.constrained_recovery_fixture.replay') as execute:
                with self.assertRaises(FileExistsError):
                    export_replay(destination)
                execute.assert_not_called()
            self.assertEqual(b'unchanged', (destination / 'sentinel').read_bytes())
            with mock.patch('tests.constrained_recovery_fixture.replay', return_value=self.actual):
                export_replay(destination / 'new')
            self.assertEqual(encoded(self.actual['exact_replay']),
                             (destination / 'new/exact-replay.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
