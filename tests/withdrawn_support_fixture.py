"""Prospective semantic fixtures only; no production inference or state migration."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from unittest import mock

import yaml

from tests.correction_propagation_fixture import (
    CorrectionJourney, ROOT, READ_PATH, PERFORMANCE_ID, REPORT_ID, PERFORMANCE_PATH,
    REPORT_PATH, performance, report, initial_knowledge, policies, REQUEST, digest,
)
from tests.test_runtime_broker import BrokerProvider, typed_evidence, RUNTIME_PATH, locator
from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
import scripts.runtime_broker as broker

FIXTURES = ROOT / 'tests/fixtures/core34-withdrawn-support'
BASELINE = 'c2856e94e97190f402cbfec2734e5186993424c5'
RECIPE = 'core34-withdrawn-support-v1'
CASES = ('sole', 'independent', 'negative', 'assisted', 'ambiguous', 'no_report',
         'repeated', 'contradictory', 'mixed', 'denied', 'missing', 'over_budget')


def extra(eid, summary, direction='support', kind='task_response', confidence='high'):
    value = yaml.safe_load(typed_evidence(evidence_id=eid, direction=direction))
    value.update(observed_at='2026-10-07T01:00:00Z',
                 observation={'kind': kind, 'summary': summary},
                 context={'topic': 'synthetic', 'subtopic': 'unit'},
                 source={'round_id': 'synthetic-' + eid})
    value['interpretation'].update(diagnosticity='high', confidence=confidence)
    return value


def inputs(case):
    if case not in CASES:
        raise ValueError('unknown case')
    original, later = yaml.safe_load(performance()), yaml.safe_load(report())
    knowledge = yaml.safe_load(initial_knowledge())
    claims = knowledge['concepts']['token-identity']['capabilities']
    records = [original]
    if case != 'no_report':
        records.append(later)
    if case in {'independent', 'mixed'}:
        eid = 'evi-independent-003'
        records.append(extra(eid, 'On a different sentence pair a day later, without a hint or displayed answer, the learner independently explained why the same vocabulary token ID can have different contextual representations, with a correct contrasting prediction. This was a separate performance.'))
        claims['explanation']['evidence_refs']['support'].append(eid)
        claims['explanation']['basis_summary'] += ' A separate unassisted correct explanation on a different task also supports this claim.'
    if case in {'negative', 'mixed'}:
        for n in (4, 5):
            records.append(extra(f'evi-negative-00{n}', 'On a separate controlled unassisted sentence-pair task, all relevant terms were established and instructions were understood. The learner asserted that a fixed vocabulary ID forces an identical contextual representation and could not explain the differing contexts. No assistance or composite prerequisite barrier was observed. This is evidence against reliable independent explanation on this task type, not a global trait.', 'challenge'))
    if case == 'assisted':
        original['targets'].append(dict(original['targets'][0], capability='explanation_with_hint'))
        claims['explanation_with_hint'] = copy.deepcopy(claims['explanation'])
        claims['explanation_with_hint']['basis_summary'] = 'The one correct sentence-pair explanation supports explanation with the supplied hint only. Independence is not part of this scoped claim.'
    if case == 'ambiguous':
        later['observation']['summary'] = f'The learner said of {PERFORMANCE_ID}: "I may have seen something similar earlier, but I do not remember whether I looked at a hint while answering or what it said." No new performance occurred.'
        later['interpretation']['confidence'] = 'low'
    if case in {'repeated', 'contradictory'}:
        second = copy.deepcopy(later)
        second['id'] = 'evi-report-006'
        second['source']['round_id'] = 'synthetic-round-103'
        second['observed_at'] = '2026-10-06T09:20:00Z'
        second['observation']['summary'] = (
            f'The learner repeated about {PERFORMANCE_ID}: "The explanation used the same hint I already reported. This is the same answer, and I have made no new attempt."'
            if case == 'repeated' else
            f'The learner then said about {PERFORMANCE_ID}: "I did not consult a hint for that answer; my earlier report referred to another exercise." This contradicts the earlier report, and no independent record resolves which report is accurate. No new performance occurred.')
        records.append(second)
    return records, knowledge


class WithdrawalJourney(CorrectionJourney):
    def __init__(self, case='sole'):
        self.case = case
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / 'control').mkdir(); (root / 'instance').mkdir()
        self.provider = BrokerProvider(root / 'control', root / 'instance')
        self.provider.set_branch_registry(role='main', subtopic='unit')
        self.gate, self.hosts = DeploymentWriteGate(), []
        records, knowledge = inputs(case)
        producer = self.open(writable=True)
        self.producer_id = producer._session.session_id
        # Synthetic input creation uses the existing create-only admission path.
        for record in records:
            self.invoke(producer, 'create_evidence', content=yaml.safe_dump(record, sort_keys=False))
        token = self.invoke(producer, 'read_learning_context', required_paths=[READ_PATH])['documents'][0]['version_token']
        self.invoke(producer, 'reconcile_knowledge', content=yaml.safe_dump(knowledge, sort_keys=False), expected_version_token=token)
        producer.close()
        self.before, self.before_blobs = copy.deepcopy(self.provider.docs), copy.deepcopy(self.provider.blobs)

    def gap(self):
        """Actually invoke an inaccessible read; no fabricated success/empty context."""
        host = ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=('learner/knowledge',) if self.case == 'denied' else ('learner/knowledge', 'evidence')),
            write_admission=self.gate, expected_generation=3)
        self.hosts.append(host)
        if self.case == 'missing':
            return host.invoke({'operation': 'read_learning_context', 'arguments': {'required_paths': ['evidence/evi-required-unavailable.yaml']}})
        if self.case == 'over_budget':
            # Exercise the actual pre-read path-count bound, with synthetic names.
            return host.invoke({'operation': 'read_learning_context', 'arguments': {'required_paths': [f'evidence/evi-required-{i:03}.yaml' for i in range(broker.LEARNING_CONTEXT_MAX_DOCUMENTS + 1)]}})
        return host.invoke({'operation': 'read_learning_context', 'arguments': {'required_paths': [REPORT_PATH]}})


def policy_snapshot():
    result = policies()
    result['evidence-integration.md'] = (ROOT / 'protocol/evidence-integration.md').read_text(encoding='utf-8')
    # Preserve the old owner keys while replacing only the owner under test.
    return result


def summary(documents):
    lines = []
    def render(value, label):
        if isinstance(value, dict):
            for key, child in value.items(): render(child, f'{label}.{key}' if label else key)
        else: lines.append(f'The value of {label} is {json.dumps(value, ensure_ascii=False)}.')
    for item in documents:
        lines.append(f"Document {item['path']}, version token {item['version_token']}:")
        render(yaml.safe_load(item['content']), '')
    return '\n'.join(lines)


def case_context(case):
    with WithdrawalJourney(case) as journey:
        documents = journey.recover()
        coverage = {'status': 'complete', 'scope': 'all relevant synthetic case Evidence and current Knowledge; no omitted cross-topic evidence'}
        if case in {'denied', 'missing', 'over_budget'}:
            failure = journey.gap()
            assert not failure['ok']
            # Only Knowledge was successfully supplied. Relevant evidence remains inaccessible.
            documents = [d for d in documents if d['path'] == READ_PATH]
            coverage = {'status': 'incomplete', 'scope': 'relevant Evidence unavailable', 'read_result': failure}
        assert journey.provider.docs == journey.before
        return {'case_id': case, 'context': documents, 'coverage': coverage}


def packet(kind):
    common = {'recipe_version': RECIPE, 'request': REQUEST,
              'policy': json.loads((FIXTURES / 'policy-snapshot.json').read_text(encoding='utf-8')),
              'host_interface': {'operation': 'reconcile_knowledge', 'arguments': {'content': 'complete existing-schema Knowledge YAML text', 'expected_version_token': 'the supplied current Knowledge token'}},
              'output_contract': {'case_id': 'the provided case identifier', 'next_action': 'actual learner-facing instruction', 'basis': 'short checkable basis without hidden reasoning', 'host_requests': 'zero or more justified host request objects'}}
    if kind in {'structured', 'summary'}:
        common.update(case_context('sole'))
        if kind == 'summary': common['context'] = summary(common['context'])
    elif kind == 'boundaries':
        common['request'] += ' Evaluate each separately named synthetic case independently and return a cases list. No case is evidence for another case.'
        common['cases'] = [case_context(case) for case in CASES if case != 'sole']
    else: raise ValueError('unknown packet kind')
    return common
