"""Mechanical checks, not automated grading of model semantic judgments."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import yaml

from tests.host_replay_compatibility import assert_v3_replay, historical_source_path

from tests.withdrawn_support_fixture import (
    FIXTURES, ROOT, CASES, WithdrawalJourney, packet, inputs, digest,
    READ_PATH, PERFORMANCE_PATH, REPORT_PATH, PERFORMANCE_ID, REPORT_ID, remediation_packet,
)


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class WithdrawnSupportTests(unittest.TestCase):
    def test_frozen_packets_and_policy_reproduce(self):
        manifest = read('prospective-manifest.json')
        self.assertEqual(manifest['policy_bytes_sha256'], hashlib.sha256((FIXTURES / 'policy-snapshot.json').read_bytes()).hexdigest())
        policy = read('policy-snapshot.json')
        self.assertEqual(5, len(policy))
        for row in manifest['consumers']:
            raw = (FIXTURES / (row['id'] + '-packet.json')).read_bytes()
            self.assertEqual(row['packet_bytes_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['packet_hash'], digest(json.loads(raw)))
            assert_v3_replay(self, json.loads(raw), packet(row['id']))
            self.assertNotIn('criteria', json.loads(raw))
            self.assertNotIn('expected', json.loads(raw))

    def test_historical_packet_policy_ignores_later_live_protocol_revision(self):
        original = Path.read_text
        live_reads = []

        def revised_live_policy(path, *args, **kwargs):
            content = original(path, *args, **kwargs)
            if path == ROOT / 'protocol/evidence-integration.md':
                live_reads.append(path)
                return content + '\nSynthetic later protocol revision for this control.\n'
            return content

        # Host startup may still read live Core files for safety validation;
        # those reads must not rewrite the historical model-policy input.
        with mock.patch.object(Path, 'read_text', revised_live_policy):
            for kind in ('structured', 'summary', 'boundaries'):
                assert_v3_replay(self, read(kind + '-packet.json'), packet(kind))
        self.assertTrue(live_reads)

    def test_remediation_packet_preserves_all_prior_artifacts(self):
        manifest = read('remediation-manifest.json')
        for kind in ('packet', 'policy-snapshot'):
            raw = (FIXTURES / ('remediation-' + kind + '.json')).read_bytes()
            key = 'packet_bytes_sha256' if kind == 'packet' else 'policy_bytes_sha256'
            self.assertEqual(manifest[key], hashlib.sha256(raw).hexdigest())
        self.assertEqual(read('remediation-packet.json'), remediation_packet())
        for name, expected in manifest['prior_frozen_artifacts'].items():
            self.assertEqual(expected, hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest())
        policy = read('remediation-policy-snapshot.json')
        self.assertIn('## 3. Learner feedback persistence routing', policy['persistence-policy.md'])
        self.assertIn('Learner feedback MUST NOT by itself', policy['teaching-decision.md'])
        self.assertNotIn('criteria', read('remediation-packet.json'))

    def test_remediation_first_requests_replay_without_repair(self):
        raw = (FIXTURES / 'remediation-response.json').read_bytes()
        receipt = read('remediation-replay.json')
        self.assertEqual(receipt['response_bytes_sha256'], hashlib.sha256(raw).hexdigest())
        answers = json.loads(raw)['cases']
        self.assertEqual(2, len(answers))
        self.assertEqual({'sole', 'preference_only'}, {x['case_id'] for x in answers})
        self.assertEqual(2, len(receipt['cases']))
        self.assertEqual({'sole', 'preference_only'}, {x['case_id'] for x in receipt['cases']})
        for row in receipt['cases']:
            answer = next(x for x in answers if x['case_id'] == row['case_id'])
            with WithdrawalJourney('sole' if row['case_id'] == 'sole' else 'no_report') as journey:
                results = [journey.apply(request) for request in answer['host_requests']]
                assert_v3_replay(self, row['host_results'], results)
                self.assertEqual(row['result_documents'], journey.recover())
                before = {p: b for p, b in journey.before.items() if p.startswith('evidence/')}
                after = {p: b for p, b in journey.provider.docs.items() if p.startswith('evidence/')}
                self.assertEqual(before, after)

    def test_current_feedback_owners_link_to_reassessment_without_direct_transition(self):
        link = 'evidence-integration.md#withdrawing-an-apparent-support-basis'
        for name in ('persistence-policy.md', 'teaching-decision.md'):
            content = (ROOT / 'protocol' / name).read_text(encoding='utf-8')
            self.assertIn(link, content)
            self.assertIn('MUST NOT', content)
            self.assertIn('original', content)
        # This is a source-binding guard, not semantic model compliance.

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
                self.assertEqual(expected, hashlib.sha256(historical_source_path(ROOT, path).read_bytes()).hexdigest())

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
        rows = read('mechanical-replay.json')
        expected = {('structured', 'sole'), ('summary', 'sole')}
        expected.update(('boundaries', case) for case in CASES if case != 'sole')
        self.assertEqual(len(expected), len(rows))
        self.assertEqual(expected, {(r['consumer_id'], r['case_id']) for r in rows})
        for row in rows:
            raw = (FIXTURES / (row['consumer_id'] + '-response.json')).read_bytes()
            self.assertEqual(row['response_bytes_sha256'], hashlib.sha256(raw).hexdigest())
            response = json.loads(raw)
            answer = next(c for c in response['cases'] if c['case_id'] == row['case_id']) if 'cases' in response else response
            with self.subTest(consumer=row['consumer_id'], case=row['case_id']), WithdrawalJourney(row['case_id']) as journey:
                results = [journey.apply(r) for r in answer['host_requests']]
                assert_v3_replay(self, row['host_results'], results)
                self.assertEqual(row['result_documents'], journey.recover())
                before = {p: b for p, b in journey.before.items() if p.startswith('evidence/')}
                after = {p: b for p, b in journey.provider.docs.items() if p.startswith('evidence/')}
                self.assertEqual(before, after)

    def test_missing_or_duplicate_replay_rows_cannot_skip_verification(self):
        original = read
        checks = (
            ('mechanical-replay.json', self.test_first_requests_replay_exactly_and_preserve_evidence),
            ('remediation-replay.json', self.test_remediation_first_requests_replay_without_repair),
        )
        for name, check in checks:
            good = original(name)
            rows = good if isinstance(good, list) else good['cases']
            for changed in ([], rows[:-1], [rows[0]] * len(rows)):
                altered = changed if isinstance(good, list) else dict(good, cases=changed)
                def replacement(filename, target=name, value=altered):
                    return value if filename == target else original(filename)
                with self.subTest(name=name, count=len(changed)):
                    with mock.patch(__name__ + '.read', side_effect=replacement):
                        with self.assertRaises(AssertionError):
                            check()

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
