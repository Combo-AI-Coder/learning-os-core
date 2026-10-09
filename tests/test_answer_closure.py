"""Frozen q03 observations and exact host mechanics, not a tutor-quality grader."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

from tests import answer_closure_fixture as fixture


class AnswerClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual = fixture.replay()

    def test_exact_inventory_and_verbatim_artifacts(self):
        manifest = fixture.verify_artifacts()
        self.assertEqual(33, len(manifest['files']))
        for case in manifest['cases']:
            for stage in ('b', 'c'):
                for kind in ('packet', 'response'):
                    name = f'{case}-{stage}-{kind}.json'
                    self.assertEqual('verbatim', manifest['files'][name]['kind'])
        self.assertFalse(manifest['model_resampled'])
        self.assertEqual(18, manifest['original_host_requests'])

    def test_nested_tampering_and_surplus_inventory_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for file in fixture.FIXTURES.iterdir():
                shutil.copyfile(file, root / file.name)
            name = 'elm-b-trajectory.json'
            original = (root / name).read_bytes()
            value = json.loads(original)
            value[0]['response']['result']['applied'] = False
            (root / name).write_bytes(fixture.encoded(value))
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                fixture.verify_artifacts(root)
            (root / name).write_bytes(original)
            (root / 'surplus.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                fixture.verify_artifacts(root)

    def test_manifest_itself_is_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for file in fixture.FIXTURES.iterdir():
                shutil.copyfile(file, root / file.name)
            path = root / 'publication-manifest.json'
            value = json.loads(path.read_bytes())
            value['unique_b_inputs'] = 1
            path.write_bytes(fixture.encoded(value))
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                fixture.verify_artifacts(root)

    def test_three_answers_are_the_only_first_input_difference(self):
        common = []
        hashes = []
        for case in ('elm', 'pine', 'reed'):
            packet = fixture.read(case + '-b-packet.json')
            hashes.append(hashlib.sha256(fixture.encoded(packet)).hexdigest())
            del packet['current_turn']['learner_answer']
            common.append(packet)
        self.assertEqual(3, len(set(hashes)))
        self.assertTrue(all(packet == common[0] for packet in common))
        self.assertEqual('synthetic-q03-turn-001', common[0]['current_turn']['source_round_id'])

    def test_six_inputs_reproduce_exactly_from_actual_host_results(self):
        self.assertEqual(6, len(self.actual['packets']))
        self.assertEqual(6, len({fixture.digest(fixture.encoded(p))
                                for p in self.actual['packets'].values()}))
        for name, packet in self.actual['packets'].items():
            self.assertEqual((fixture.FIXTURES / (name + '-packet.json')).read_bytes(),
                             fixture.encoded(packet))
        self.assertFalse(self.actual['model_resampled'])

    def test_all_raw_requests_results_and_provider_calls_are_retained(self):
        count = 0
        for row in self.actual['rows']:
            for stage in ('b', 'c'):
                original = fixture.read(row['case'] + '-' + stage + '-trajectory.json')
                actual = row[stage + '_receipts']
                self.assertEqual(len(original), len(actual))
                for saved, reproduced in zip(original, actual):
                    raw = base64.b64decode(saved['request_raw_base64'], validate=True)
                    self.assertEqual(saved['request_sha256'], fixture.digest(raw))
                    self.assertEqual(saved['request'], json.loads(raw))
                    self.assertEqual(saved['request_raw_utf8'], raw.decode())
                    self.assertEqual(saved['response'], reproduced['response'])
                    self.assertEqual(saved['provider_calls'], json.loads(fixture.encoded(reproduced['provider_calls'])))
                    self.assertEqual(saved['changed_paths'], reproduced['changed_paths'])
                    self.assertNotIn('request_file', saved)
                    self.assertEqual(['request_file'], saved['omitted_source_fields'])
                    self.assertEqual(64, len(saved['source_log_sha256']))
                    count += 1
        self.assertEqual(18, count)

    def test_empty_missing_duplicate_and_failed_receipts_are_rejected(self):
        original = fixture.read('elm-b-trajectory.json')
        variants = [[], original[:-1], original + [original[0]]]
        variant = copy.deepcopy(original)
        variant[1] = copy.deepcopy(variant[0])
        variants.append(variant)
        variant = copy.deepcopy(original)
        variant[0]['response']['ok'] = False
        variants.append(variant)
        variant = copy.deepcopy(original)
        variant[0]['response']['result']['applied'] = False
        variants.append(variant)
        for rows in variants:
            with self.subTest(length=len(rows)), self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'elm', 'b')

    def test_raw_request_or_operation_binding_mismatch_is_rejected(self):
        original = fixture.read('elm-b-trajectory.json')
        for mutate in (
            lambda x: x[0].__setitem__('request_raw_base64', '!!'),
            lambda x: x[0].__setitem__('request_sha256', '0' * 64),
            lambda x: x[0]['request'].__setitem__('operation', 'read_learning_context'),
            lambda x: x[0]['response'].__setitem__('operation', 'save_learning_checkpoint'),
        ):
            rows = copy.deepcopy(original)
            mutate(rows)
            with self.assertRaises(ValueError):
                fixture.validate_trajectory(rows, 'elm', 'b')

    def test_static_fake_tokens_do_not_substitute_for_a_fresh_read(self):
        rows = fixture.read('elm-b-trajectory.json')
        rows[0], rows[1] = rows[1], rows[0]
        for i, row in enumerate(rows, 1):
            row['index'] = i
        with self.assertRaisesRegex(ValueError, 'fresh read'):
            fixture.validate_trajectory(rows, 'elm', 'b')

    def test_source_occurrence_and_unaffected_state_are_preserved(self):
        initial = fixture.read('initial-provider-state.json')
        for row in self.actual['rows']:
            fixture.verify_state_invariants(initial, row['post_state'], row['case'])
            claims = yaml.safe_load(row['post_state']['docs'][fixture.READ_PATH])['concepts']['category_statements']['capabilities']
            self.assertEqual('conflicted', claims['respect_the_direction_of_an_every_statement']['state'])
            self.assertEqual(row['post_state'], row['successor_state'])
        reed = next(row for row in self.actual['rows'] if row['case'] == 'reed')
        self.assertEqual(initial['docs'][fixture.READ_PATH], reed['post_state']['docs'][fixture.READ_PATH])
        self.assertEqual([fixture.CHECKPOINT_PATH], fixture.changed_paths(initial['docs'], reed['post_state']['docs']))

    def test_duplicate_current_occurrence_or_old_evidence_rewrite_is_rejected(self):
        initial = fixture.read('initial-provider-state.json')
        post = copy.deepcopy(self.actual['rows'][0]['post_state'])
        extra = next(p for p in post['docs'] if p.startswith('evidence/') and p not in initial['docs'])
        post['docs']['evidence/duplicate.yaml'] = post['docs'][extra]
        with self.assertRaisesRegex(AssertionError, 'occurrence'):
            fixture.verify_state_invariants(initial, post, 'elm')
        post = copy.deepcopy(self.actual['rows'][0]['post_state'])
        old = next(p for p in initial['docs'] if p.startswith('evidence/'))
        post['docs'][old] += '\nchanged\n'
        with self.assertRaisesRegex(AssertionError, 'original Evidence'):
            fixture.verify_state_invariants(initial, post, 'elm')

    def test_successors_receive_only_actual_durable_readback(self):
        allowed = {'request', 'product_protocols', 'recovery_trace',
                   'available_host_operations', 'response_contract'}
        for row in self.actual['rows']:
            packet = self.actual['packets'][row['case'] + '-c']
            self.assertEqual(allowed, set(packet))
            for document in packet['recovery_trace'][-1]['response']['result']['documents']:
                self.assertEqual(row['post_state']['docs'][document['path']], document['content'])
                self.assertEqual(row['post_state']['blobs'][document['path']], document['version_token'])
            self.assertTrue(all(x['response']['operation'] == 'read_learning_context'
                                for x in row['c_receipts']))

    def test_semantic_review_is_hash_bound_and_qualified(self):
        review = fixture.read('semantic-review-final.json')
        self.assertEqual('bounded_pass_against_frozen_criteria', review['all_study_disposition'])
        self.assertEqual(0, review['outcome_counts']['capability_state_enum_transitions'])
        self.assertEqual(0, review['outcome_counts']['C_owning_writes'])
        self.assertTrue(any('same model family' in item for item in review['scope_and_method_limits']))
        self.assertTrue(any('not a reliability rate' in item for item in review['scope_and_method_limits']))
        self.assertEqual(fixture.digest((fixture.FIXTURES / 'prospective-plan.json').read_bytes()),
                         review['prospective_plan_sha256'])

    def test_review_transplant_missing_duplicate_and_stale_bindings_are_rejected(self):
        original = fixture.read('semantic-review-final.json')
        fixture.verify_review_bindings(original)
        variants = []
        changed = copy.deepcopy(original)
        changed['rows'] = changed['rows'][:-1]
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['successor_rows'][1] = copy.deepcopy(changed['successor_rows'][0])
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['rows'][0]['bindings']['first-response.json'] = changed['rows'][1]['bindings']['first-response.json']
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['successor_rows'][0]['parent_handle'] = 'different-parent'
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['rows'][0]['request_log_sha256']['001.json'] = '0' * 64
        variants.append(changed)
        for review in variants:
            with self.assertRaises(ValueError):
                fixture.verify_review_bindings(review)

    def test_successor_builder_uses_frozen_policy_and_ignores_parent_reply(self):
        original = fixture.read('elm-b-packet.json')
        original['reply'] = 'A forbidden transcript sidecar'
        trace = fixture.read('elm-successor-recovery.json')['trace']
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('no live policy load')):
            actual = fixture.successor_packet(original, trace)
        self.assertEqual(fixture.read('elm-c-packet.json'), actual)
        self.assertNotIn('reply', actual)
        self.assertNotIn('current_turn', actual)

    def test_export_refuses_existing_or_core_destinations_before_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            for destination in (Path(temporary), fixture.ROOT, fixture.FIXTURES / 'new'):
                with self.subTest(destination=destination), mock.patch.object(fixture, 'replay') as execute:
                    with self.assertRaises(ValueError):
                        fixture.export_replay(destination)
                    execute.assert_not_called()
            destination = Path(temporary) / 'new'
            with mock.patch.object(fixture, 'replay', return_value=self.actual):
                fixture.export_replay(destination)
            self.assertEqual(fixture.encoded(self.actual['packets']['elm-c']),
                             (destination / 'elm-c-packet.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
