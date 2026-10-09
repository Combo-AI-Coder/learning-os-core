"""Protect a retained bounded acquisition witness, not a general semantic grader."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests import context_selection_fixture as fixture


class ContextSelectionTests(unittest.TestCase):
    def test_exact_inventory_and_single_common_packet(self):
        manifest = fixture.verify_artifacts()
        self.assertEqual(18, len(manifest['files']))
        self.assertEqual(1, manifest['initial_semantic_packets'])
        self.assertEqual(3, manifest['first_interactive_trajectories'])
        self.assertEqual(0, manifest['model_resamples'])
        self.assertEqual(1, len({s['packet_sha256'] for s in manifest['cases'].values()}))
        self.assertEqual(3, len({s['first_response_sha256'] for s in manifest['cases'].values()}))

    def test_no_curated_state_or_concrete_evidence_locator_in_initial_packet(self):
        packet = fixture.packet()
        self.assertEqual(fixture.encoded(packet), (fixture.FIXTURES / 'common-packet.json').read_bytes())
        self.assertEqual({'request', 'session_binding', 'product_protocols', 'host_read_interface', 'response_contract'}, set(packet))
        self.assertEqual([], packet['host_read_interface']['writable_roots'])
        self.assertEqual({'read_learning_context', 'discover_learning_evidence'}, set(packet['host_read_interface']['operations']))
        encoded = fixture.encoded(packet)
        self.assertNotIn(b'learner/knowledge/synthetic.yaml', encoded)
        self.assertNotIn(fixture.CHECKPOINT_PATH.encode(), encoded)
        for spec in fixture.read('publication-manifest.json')['cases'].values():
            for path in spec['evidence_paths']:
                self.assertNotIn(path.encode(), encoded)

    def test_packet_uses_frozen_not_future_live_product_guidance(self):
        before = fixture.packet()
        with mock.patch.object(Path, 'read_text', return_value='unrelated future protocol'):
            self.assertEqual(before, fixture.packet())
        altered = copy.deepcopy(before['product_protocols'])
        altered['protocol/teaching-decision.md'] += '\nfuture guidance'
        self.assertNotEqual(before, fixture.packet(protocols=altered))

    def test_all_original_trajectory_bindings(self):
        for case in ('baseline', 'relevant', 'irrelevant'):
            with self.subTest(case=case):
                fixture.validate_trajectory(fixture.read(case + '-trajectory.json'), case)

    def test_missing_duplicate_or_reordered_receipts_fail(self):
        rows = fixture.read('baseline-trajectory.json')
        variants = [rows[:-1], rows + [rows[-1]], [rows[0], rows[0], rows[2]], list(reversed(rows))]
        for altered in variants:
            with self.subTest(indices=[x['index'] for x in altered]), self.assertRaises(ValueError):
                fixture.validate_trajectory(altered, 'baseline')

    def test_raw_and_parsed_request_mutations_fail(self):
        for key in ('request_raw_base64', 'request_raw_utf8', 'request_sha256', 'request'):
            rows = fixture.read('baseline-trajectory.json')
            rows[0][key] = {} if key == 'request' else 'changed'
            with self.subTest(key=key), self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'baseline')

    def test_failed_result_or_state_change_cannot_be_hidden(self):
        for change in ('failed', 'changed', 'paths'):
            rows = fixture.read('baseline-trajectory.json')
            if change == 'failed':
                rows[0]['response']['ok'] = False
            elif change == 'changed':
                rows[0]['state_unchanged'] = False
            else:
                rows[0]['changed_paths'] = ['evidence/extra.yaml']
            with self.subTest(change=change), self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'baseline')

    def test_discovered_owner_must_precede_its_actual_read(self):
        rows = fixture.read('baseline-trajectory.json')
        rows[1]['response']['result']['knowledge_owners'] = []
        with self.assertRaisesRegex(ValueError, 'locator'):
            fixture.validate_trajectory(rows, 'baseline')

    def test_optional_absence_and_visible_distractor_cannot_be_omitted(self):
        rows = fixture.read('baseline-trajectory.json')
        rows[0]['response']['result']['missing_optional'] = []
        with self.assertRaisesRegex(ValueError, 'optional'):
            fixture.validate_trajectory(rows, 'baseline')
        rows = fixture.read('irrelevant-trajectory.json')
        rows[1]['response']['result']['evidence'] = [d for d in rows[1]['response']['result']['evidence']
                                                  if d['path'] != fixture.DISTRACTOR]
        with self.assertRaisesRegex(ValueError, 'Evidence'):
            fixture.validate_trajectory(rows, 'irrelevant')

    def test_review_is_bound_to_all_first_outputs_and_source_receipts(self):
        review = fixture.read('semantic-method-review.json')
        fixture.verify_review_bindings(review)
        mutations = []
        changed = copy.deepcopy(review); changed['rows'][0]['first_response_sha256'] = '0' * 64; mutations.append(changed)
        changed = copy.deepcopy(review); changed['rows'][0]['request_receipts_sha256'].pop('001.json'); mutations.append(changed)
        changed = copy.deepcopy(review); changed['rows'] = [changed['rows'][0]] * 3; mutations.append(changed)
        changed = copy.deepcopy(review); changed['bindings']['prospective-plan.json'] = '0' * 64; mutations.append(changed)
        for altered in mutations:
            with self.assertRaises(ValueError):
                fixture.verify_review_bindings(altered)

    def test_source_dependency_hashes_reject_changed_q03_state(self):
        fixture.verify_dependencies()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dependencies = fixture.read('publication-manifest.json')['dependencies']
            for name in dependencies:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((fixture.ROOT / name).read_bytes())
            fixture.verify_dependencies(root)
            path = root / next(iter(dependencies))
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'dependency'):
                fixture.verify_dependencies(root)

    def test_worlds_reuse_original_states_and_only_add_one_irrelevant_record(self):
        worlds = fixture.build_worlds()
        baseline, irrelevant = worlds['baseline'], worlds['irrelevant']
        self.assertEqual({fixture.DISTRACTOR}, set(irrelevant['docs']) - set(baseline['docs']))
        self.assertEqual(baseline['snapshot_extra_paths'], irrelevant['snapshot_extra_paths'])
        for path, value in baseline['docs'].items():
            self.assertEqual(value, irrelevant['docs'][path])
        self.assertEqual(baseline['instance_head'], irrelevant['instance_head'])

    def test_full_state_guard_protects_source_occurrences_and_other_fields(self):
        state = {'docs': {'evidence/old.yaml': 'one source occurrence', 'progress.yaml': 'unchanged'},
                 'blobs': {'evidence/old.yaml': 'token'}, 'instance_head': 'head', 'snapshot_extra_paths': []}
        fixture.assert_unchanged(state, copy.deepcopy(state))
        for key in state:
            altered = copy.deepcopy(state)
            altered[key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                fixture.assert_unchanged(state, altered)
        altered = copy.deepcopy(state); altered['docs']['evidence/duplicate.yaml'] = 'one source occurrence'
        with self.assertRaises(ValueError):
            fixture.assert_unchanged(state, altered)

    def test_inventory_rejects_changed_surplus_or_symlink_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'fixtures'; shutil.copytree(fixture.FIXTURES, target)
            path = target / 'baseline-response.json'; original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.assertRaises(ValueError):
                fixture.verify_artifacts(target)
            path.write_bytes(original)
            extra = target / 'surplus.json'; extra.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                fixture.verify_artifacts(target)
            extra.unlink()
            outside = Path(temporary) / 'outside.json'; outside.write_bytes(original)
            path.unlink()
            try:
                path.symlink_to(outside)
            except OSError:
                return  # Windows without symlink privilege: byte/inventory checks above still ran.
            with self.assertRaises(ValueError):
                fixture.verify_artifacts(target)

    def test_exact_replay_and_actual_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'export'
            fixture.export_replay(destination)
            self.assertEqual((fixture.FIXTURES / 'common-packet.json').read_bytes(),
                             (destination / 'common-packet.json').read_bytes())
            rows = json.loads((destination / 'mechanical-replay.json').read_bytes())
            self.assertEqual(['baseline', 'relevant', 'irrelevant'], [r['case'] for r in rows])
            self.assertEqual(9, sum(len(r['receipts']) for r in rows))
            self.assertTrue(all(r['state_unchanged'] for r in rows))

    def test_export_refuses_existing_or_in_core_destination_before_replay(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(fixture, 'replay') as replay:
            for path in (Path(temporary), fixture.ROOT, fixture.ROOT / 'synthetic-export-not-created'):
                with self.assertRaises(ValueError):
                    fixture.export_replay(path)
            replay.assert_not_called()

    def test_preparation_failure_and_method_repairs_remain_historical(self):
        diagnostic = fixture.read('preparation-diagnostics.json')
        self.assertEqual('ValueError', diagnostic['first_error_type'])
        self.assertTrue(diagnostic['preceding_create_evidence']['response']['ok'])
        self.assertTrue(diagnostic['first_and_second_derived_state_match'])
        methods = fixture.read('method-change-record.json')
        self.assertTrue(methods['mechanical_transport']['previous_receipts_preserved'])
        self.assertFalse(methods['mechanical_sealer']['model_called'])
        self.assertTrue(methods['mechanical_sealer']['missing_request_bytes_retained_as_limitations'])


if __name__ == '__main__':
    unittest.main()
