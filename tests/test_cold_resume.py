"""Integrity and mechanical replay, not an automated teaching-quality oracle."""
import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from unittest import mock

from tests.cold_resume_fixture import FIXTURES, ROOT, READ_PATH, encoded, read, replay, export_replay


class ColdResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.successor = replay()

    def test_frozen_artifact_bytes_and_exact_inventory(self):
        manifest = read('artifact-manifest.json')
        expected = {'alpha-packet.json', 'alpha-response.json', 'beta-packet.json',
                    'beta-response.json', 'successor-packet.json', 'successor-response.json',
                    'mechanical-replay.json', 'prospective-plan.json', 'successor-plan.json',
                    'semantic-review.json'}
        self.assertEqual(expected, set(manifest['files']))
        self.assertEqual(expected | {'artifact-manifest.json', '.gitattributes'}, {p.name for p in FIXTURES.iterdir()})
        for name, sha in manifest['files'].items():
            with self.subTest(name=name):
                self.assertEqual(sha, hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest())

    def test_prospective_and_successor_packet_bindings(self):
        plan = read('prospective-plan.json')
        self.assertEqual({'alpha', 'beta'}, set(plan['input_sha256']))
        for name, sha in plan['input_sha256'].items():
            self.assertEqual(sha, hashlib.sha256((FIXTURES / f'{name}-packet.json').read_bytes()).hexdigest())
        self.assertEqual(read('successor-plan.json')['packet_sha256'],
                         hashlib.sha256(encoded(self.successor)).hexdigest())

    def test_exact_first_requests_and_successor_reproduce(self):
        self.assertEqual((FIXTURES / 'mechanical-replay.json').read_bytes(), encoded(self.rows))
        self.assertEqual((FIXTURES / 'successor-packet.json').read_bytes(), encoded(self.successor))
        self.assertEqual(['alpha', 'beta'], [r['case'] for r in self.rows])
        self.assertEqual([READ_PATH], self.rows[0]['changed_paths'])
        self.assertEqual([], self.rows[1]['changed_paths'])
        self.assertEqual('guard_rejected', self.rows[0]['readonly_negative_results'][0]['error']['code'])
        self.assertTrue(self.rows[0]['unchanged_request_results'][0]['result']['applied'])
        self.assertEqual([], read('beta-response.json')['host_requests'])
        self.assertEqual([], read('successor-response.json')['host_requests'])

    def test_successor_is_only_actual_readback_with_normal_protocols(self):
        self.assertEqual(self.rows[0]['post_context'], self.successor['durable_context'])
        self.assertEqual('Continue where we left off.', self.successor['request'])
        self.assertEqual({'request', 'product_protocols', 'durable_context',
                          'available_host_operation', 'output_format'}, set(self.successor))
        for name in ('alpha', 'beta'):
            self.assertEqual(read(f'{name}-packet.json')['product_protocols'], self.successor['product_protocols'])
        for p, content in self.successor['product_protocols'].items():
            self.assertEqual(read('semantic-review.json')['hashes']['product_protocol_content_utf8'][p],
                             hashlib.sha256(content.encode('utf-8')).hexdigest())

    def test_review_retains_partial_not_uniform_pass(self):
        review = read('semantic-review.json')
        self.assertEqual('partial', review['overall']['status'])
        self.assertEqual('partial', review['artifact_bindings']['generation_time_binding_status'])
        self.assertEqual(hashlib.sha256((FIXTURES / 'successor-response.json').read_bytes()).hexdigest(),
                         review['artifact_bindings']['successor_response_hash_recorded_by_this_review'])

    def test_historical_replay_preserves_inputs_when_live_policy_evolves(self):
        original_read = Path.read_text
        live_reads = []
        def revised_current_policy(path, *args, **kwargs):
            text = original_read(path, *args, **kwargs)
            if path.parent == ROOT / 'protocol':
                live_reads.append(path)
                return text + '\nAdditional ordinary documentation note.\n'
            return text
        with mock.patch.object(Path, 'read_text', revised_current_policy):
            rows, successor = replay()
        self.assertTrue(live_reads)  # Current Core validation still reads live policy.
        self.assertEqual(self.rows, rows)
        self.assertEqual(self.successor, successor)

    def test_exporter_refuses_core_and_existing_destinations(self):
        for destination in (ROOT, FIXTURES / "derived-output"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                export_replay(destination)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            sentinel = destination / "sentinel.txt"
            sentinel.write_bytes(b"unchanged")
            with mock.patch("tests.cold_resume_fixture.replay", return_value=(self.rows, self.successor)):
                with self.assertRaises(FileExistsError):
                    export_replay(destination)
                self.assertEqual(b"unchanged", sentinel.read_bytes())
                self.assertEqual([sentinel], list(destination.iterdir()))
                output = destination / "new"
                export_replay(output)
                self.assertEqual(encoded(self.rows), (output / "mechanical-replay.json").read_bytes())
                self.assertEqual(encoded(self.successor), (output / "successor-packet.json").read_bytes())

    def test_changed_first_request_is_not_silently_repaired(self):
        original = read
        def changed(name):
            value = original(name)
            if name == 'alpha-response.json':
                value['host_requests'][0]['arguments']['expected_version_token'] = '0' * 40
            return value
        with mock.patch('tests.cold_resume_fixture.read', side_effect=changed):
            rows, successor = replay()
        self.assertFalse(rows[0]['unchanged_request_results'][0]['ok'])
        self.assertEqual([], rows[0]['changed_paths'])
        self.assertNotEqual(read('successor-packet.json'), successor)


if __name__ == '__main__':
    unittest.main()
