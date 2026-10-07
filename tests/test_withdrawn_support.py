"""Mechanical checks, not automated grading of model semantic judgments."""
import copy
import hashlib
import json
import unittest

import yaml

from tests.withdrawn_support_fixture import (
    FIXTURES, ROOT, CASES, WithdrawalJourney, packet, inputs, digest,
    READ_PATH, PERFORMANCE_PATH, REPORT_PATH, PERFORMANCE_ID, REPORT_ID,
)


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class WithdrawnSupportTests(unittest.TestCase):
    def test_frozen_packets_and_policy_reproduce(self):
        manifest = read('prospective-manifest.json')
        self.assertEqual(manifest['policy_bytes_sha256'], hashlib.sha256((FIXTURES / 'policy-snapshot.json').read_bytes()).hexdigest())
        policy = read('policy-snapshot.json')
        self.assertEqual((ROOT / 'protocol/evidence-integration.md').read_text(encoding='utf-8'), policy['evidence-integration.md'])
        self.assertEqual(5, len(policy))
        for row in manifest['consumers']:
            raw = (FIXTURES / (row['id'] + '-packet.json')).read_bytes()
            self.assertEqual(row['packet_bytes_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['packet_hash'], digest(json.loads(raw)))
            self.assertEqual(json.loads(raw), packet(row['id']))
            self.assertNotIn('criteria', json.loads(raw))
            self.assertNotIn('expected', json.loads(raw))

    def test_summary_changes_representation_only(self):
        structured = read('structured-packet.json')
        prose = read('summary-packet.json')
        for key in structured.keys() - {'context'}:
            self.assertEqual(structured[key], prose[key])
        from tests.withdrawn_support_fixture import summary
        self.assertEqual(summary(structured['context']), prose['context'])

    def test_original_experiment_bytes_remain_unchanged(self):
        for path, expected in read('prospective-manifest.json')['historical_files'].items():
            with self.subTest(path=path):
                self.assertEqual(expected, hashlib.sha256((ROOT / path).read_bytes()).hexdigest())

    def test_complete_boundary_inventory_without_new_product_fields(self):
        rows = read('boundaries-packet.json')['cases']
        self.assertEqual(set(CASES) - {'sole'}, {r['case_id'] for r in rows})
        for case in CASES:
            records, knowledge = inputs(case)
            self.assertEqual({'schema_version', 'document_type', 'revision', 'domain', 'concepts'}, set(knowledge))
            for record in records:
                self.assertNotIn('supersedes', record)
                self.assertNotIn('correction_of', record)
                self.assertNotIn('occurrence_id', record)

    def test_scope_controls_are_real_distinct_evidence_not_expected_answers(self):
        independent, _ = inputs('independent')
        self.assertEqual(2, sum(e['observation']['kind'] == 'task_response' for e in independent))
        negative, _ = inputs('negative')
        self.assertEqual(2, sum(e['interpretation']['direction'] == 'challenge' for e in negative))
        repeated, _ = inputs('repeated')
        self.assertEqual(1, sum(e['observation']['kind'] == 'task_response' for e in repeated))
        self.assertEqual(2, sum(e['observation']['kind'] == 'learner_self_report' for e in repeated))
        ambiguous, _ = inputs('ambiguous')
        self.assertEqual('low', ambiguous[1]['interpretation']['confidence'])
        none, _ = inputs('no_report')
        self.assertEqual(1, len(none))
        _, assisted = inputs('assisted')
        self.assertIn('explanation_with_hint', assisted['concepts']['token-identity']['capabilities'])
        for case in CASES:
            for record in inputs(case)[0]:
                if record['observation']['kind'] == 'learner_self_report':
                    self.assertEqual('neutral', record['interpretation']['direction'])

    def test_inaccessible_context_really_fails_without_writes(self):
        for case in ('denied', 'missing', 'over_budget'):
            with self.subTest(case=case), WithdrawalJourney(case) as journey:
                journey.provider.calls.clear()
                before = copy.deepcopy(journey.provider.docs)
                result = journey.gap()
                self.assertFalse(result['ok'])
                self.assertNotIn('result', result)
                self.assertEqual(before, journey.provider.docs)
                self.assertFalse(any(c[0] in {'create', 'update'} for c in journey.provider.calls))

    def test_first_requests_replay_exactly_and_preserve_evidence(self):
        for row in read('mechanical-replay.json'):
            raw = (FIXTURES / (row['consumer_id'] + '-response.json')).read_bytes()
            self.assertEqual(row['response_bytes_sha256'], hashlib.sha256(raw).hexdigest())
            response = json.loads(raw)
            answer = next(c for c in response['cases'] if c['case_id'] == row['case_id']) if 'cases' in response else response
            with self.subTest(consumer=row['consumer_id'], case=row['case_id']), WithdrawalJourney(row['case_id']) as journey:
                results = [journey.apply(r) for r in answer['host_requests']]
                self.assertEqual(row['host_results'], results)
                self.assertEqual(row['result_documents'], journey.recover())
                before = {p: b for p, b in journey.before.items() if p.startswith('evidence/')}
                after = {p: b for p, b in journey.provider.docs.items() if p.startswith('evidence/')}
                self.assertEqual(before, after)

    def test_semantic_review_is_bound_to_first_output_bytes(self):
        review = read('semantic-review.json')
        self.assertEqual(review['manifest_sha256'], hashlib.sha256((FIXTURES / 'prospective-manifest.json').read_bytes()).hexdigest())
        self.assertEqual(14, len(review['judgments']))
        for row in review['judgments']:
            raw = (FIXTURES / (row['consumer_id'] + '-response.json')).read_bytes()
            self.assertEqual(row['response_sha256'], hashlib.sha256(raw).hexdigest())
        # Hash binding reproduces the recorded review, not a new semantic grade.

    def test_host_admission_is_not_semantic_enforcement(self):
        # Preserve a useful negative control: the unchanged host accepts this
        # schema-valid but now semantically wrong unsupported interpretation.
        with WithdrawalJourney() as journey:
            knowledge = yaml.safe_load(journey.provider.docs[READ_PATH])
            knowledge['revision'] += 1
            knowledge['concepts']['token-identity']['capabilities']['explanation']['state'] = 'unsupported'
            result = journey.apply({'operation': 'reconcile_knowledge', 'arguments': {'content': yaml.safe_dump(knowledge), 'expected_version_token': journey.provider.blobs[READ_PATH]}})
            self.assertTrue(result['ok'], result)

    def test_neutral_report_reference_rejected_and_history_preserved(self):
        for side in ('support', 'challenge'):
            with self.subTest(side=side), WithdrawalJourney() as journey:
                knowledge = yaml.safe_load(journey.provider.docs[READ_PATH])
                knowledge['revision'] += 1
                knowledge['concepts']['token-identity']['capabilities']['explanation']['evidence_refs'][side].append(REPORT_ID)
                result = journey.apply({'operation': 'reconcile_knowledge', 'arguments': {'content': yaml.safe_dump(knowledge), 'expected_version_token': journey.provider.blobs[READ_PATH]}})
                self.assertFalse(result['ok'])
                self.assertEqual(journey.before, journey.provider.docs)

    def test_successor_has_only_actual_durable_result(self):
        row = next(r for r in read('mechanical-replay.json') if r['consumer_id'] == 'structured')
        value = read('successor-packet.json')
        first = read('structured-packet.json')
        self.assertEqual(row['result_documents'], value['context'])
        for key in first.keys() - {'context'}:
            self.assertEqual(first[key], value[key])
        receipt = read('successor-receipt.json')
        for kind in ('packet', 'response'):
            self.assertEqual(receipt[kind + '_bytes_sha256'], hashlib.sha256((FIXTURES / ('successor-' + kind + '.json')).read_bytes()).hexdigest())
        # Subsequent semantic judgment is reviewed separately; do not silently
        # edit a first successor output to make this storage check pass.


if __name__ == '__main__':
    unittest.main()
