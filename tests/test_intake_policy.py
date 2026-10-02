"""Synthetic learner controls: precedence, reversible inheritance and validation."""
import copy
import itertools
import unittest
import tempfile
from pathlib import Path

from scripts.intake_policy import resolve_intake_policy, validate_intake_preferences
from scripts.validate_learning_os import validate_instance
from test_instance_validator import make_core, make_instance, deployment_binding, write_yaml


class IntakePolicyTests(unittest.TestCase):
    def test_precedence_matrix(self):
        choices = (None, 'minimal', 'balanced', 'thorough')
        for current, topic, global_depth in itertools.product(choices, repeat=3):
            with self.subTest(current=current, topic=topic, global_depth=global_depth):
                policy = resolve_intake_policy(
                    current_instruction=current,
                    topic_preferences={} if topic is None else {'intake_depth': topic},
                    learner_preferences={} if global_depth is None else {'intake_depth': global_depth})
                expected = next((pair for pair in (
                    (current, 'current_instruction'), (topic, 'topic'),
                    (global_depth, 'learner_global')) if pair[0] is not None),
                    ('balanced', 'core_default'))
                self.assertEqual((policy.depth, policy.source), expected)

    def test_immediate_start_never_becomes_an_intake_gate(self):
        for depth in ('minimal', 'balanced', 'thorough'):
            policy = resolve_intake_policy(current_instruction=depth, start_immediately=True)
            self.assertEqual(policy.question_scope, 'defer_intake')
            self.assertEqual(policy.depth, depth)

    def test_modes_have_distinct_question_scope(self):
        for depth, scope in (('minimal', 'route_blocking'), ('balanced', 'route_changing'),
                             ('thorough', 'relevant_background')):
            self.assertEqual(resolve_intake_policy(current_instruction=depth).question_scope, scope)

    def test_invalid_selected_values_fail_explicitly(self):
        for bad in ('unknown', '', True, 1, [], {}, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_intake_preferences({'intake_depth': bad})
        for bad in ('', True, 1, []):
            with self.subTest(current=bad), self.assertRaises(ValueError):
                resolve_intake_policy(current_instruction=bad)
        with self.assertRaises(ValueError):
            resolve_intake_policy(start_immediately='yes')

    def test_current_instruction_does_not_depend_on_stale_lower_preferences(self):
        policy = resolve_intake_policy(current_instruction='minimal',
            topic_preferences={'intake_depth': 'obsolete'}, learner_preferences=[])
        self.assertEqual(policy.source, 'current_instruction')

    def test_legacy_preferences_and_missing_files_keep_balanced_default(self):
        policy = resolve_intake_policy(topic_preferences={'include': ['synthetic'], 'avoid': []})
        self.assertEqual((policy.depth, policy.source), ('balanced', 'core_default'))

    def test_no_mutation_and_reset_is_removal_not_new_state(self):
        topic = {'intake_depth': 'minimal', 'include': ['synthetic']}
        global_preferences = {'intake_depth': 'thorough'}
        before = copy.deepcopy((topic, global_preferences))
        resolve_intake_policy(topic_preferences=topic, learner_preferences=global_preferences)
        self.assertEqual((topic, global_preferences), before)
        del topic['intake_depth']
        self.assertEqual(resolve_intake_policy(topic_preferences=topic,
            learner_preferences=global_preferences).depth, 'thorough')


class IntakeInstanceValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.core = make_core(root / 'core')
        self.instance = make_instance(root / 'instance')

    def findings(self, kind, depth):
        preferences = {'intake_depth': depth}
        doc = {'schema_version': '0.3', 'document_type': kind, 'revision': 1}
        if kind == 'topic_goal':
            doc.update(topic={'id': 'synthetic-topic'}, goal={'preferences': preferences})
            path = 'topics/synthetic-topic/goal.yaml'
        else:
            doc['preferences'] = preferences
            path = 'learner/execution.yaml'
        write_yaml(self.instance, path, doc)
        return validate_instance(self.instance, self.core, deployment_binding())

    def test_invalid_persisted_preferences_are_rejected_in_both_owners(self):
        for kind in ('topic_goal', 'learner_execution'):
            for bad in ('unknown', '', True, 7, None, [], {}):
                with self.subTest(kind=kind, bad=bad):
                    self.assertIn('intake.preference', {f.code for f in self.findings(kind, bad)})

    def test_all_modes_are_valid_in_both_existing_owners(self):
        for kind in ('topic_goal', 'learner_execution'):
            for depth in ('minimal', 'balanced', 'thorough'):
                with self.subTest(kind=kind, depth=depth):
                    self.assertEqual(self.findings(kind, depth), [])
