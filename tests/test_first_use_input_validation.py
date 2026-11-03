"""Synthetic first-use text admission, before grants or persisted effects."""
import json
import unittest
from unittest import mock

from scripts.first_use import FirstUseIntent, FirstUseRejected, SyntheticFirstUseAuthority
from scripts.runtime_adapter import load_locator
from tests.test_runtime_broker import locator


class FirstUseInputValidationTests(unittest.TestCase):
    def test_json_surrogate_intents_return_stable_rejection(self):
        for field in ('title', 'goal'):
            for escape in ('\\ud800', '\\udfff', 'before\\ud800after'):
                data = dict(topic='synthetic', title='Title', goal='Goal')
                data[field] = json.loads('"' + escape + '"')
                with self.subTest(field=field, escape=escape):
                    with self.assertRaises(FirstUseRejected) as caught:
                        FirstUseIntent(**data)
                    self.assertEqual('invalid_intent', caught.exception.code)

    def test_json_surrogate_receivers_are_rejected_before_grant(self):
        intent = FirstUseIntent('synthetic', 'Title', 'Goal')
        for field in ('principal', 'surface'):
            for escape in ('\\ud800', '\\udfff', 'before\\ud800after'):
                authority = SyntheticFirstUseAuthority()
                arguments = dict(principal='synthetic', surface='fixture', operation_id='first',
                                 intent=intent, locator=locator())
                arguments[field] = json.loads('"' + escape + '"')
                with self.subTest(field=field, escape=escape):
                    with mock.patch('scripts.first_use.load_locator', wraps=load_locator) as load:
                        with self.assertRaises(FirstUseRejected) as caught:
                            authority.authorize(**arguments)
                        self.assertEqual('invalid_receiver', caught.exception.code)
                        load.assert_not_called()
                    self.assertEqual({}, authority._grants)
                    self.assertEqual({}, authority._sessions)

    def test_existing_receiver_character_and_intent_validation_are_preserved(self):
        intent = FirstUseIntent('synthetic', 'Title', 'Goal')
        for field in ('principal', 'surface'):
            for value in ('', 'x' * 129, None, 42):
                authority = SyntheticFirstUseAuthority()
                args = dict(principal='fixture', surface='fixture', operation_id='first',
                            intent=intent, locator=locator())
                args[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(FirstUseRejected) as caught:
                        authority.authorize(**args)
                    self.assertEqual('invalid_receiver', caught.exception.code)
                    self.assertEqual({}, authority._grants)
        authority = SyntheticFirstUseAuthority()
        receiver = authority.authorize(principal='界' * 128, surface='界' * 128,
                                       operation_id='first', intent=intent, locator=locator())
        authority.revoke(receiver)
        for field in ('title', 'goal'):
            for value in ('', '  ', None, 42):
                args = dict(topic='synthetic', title='Title', goal='Goal')
                args[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(FirstUseRejected) as caught:
                        FirstUseIntent(**args)
                    self.assertEqual('invalid_intent', caught.exception.code)

    def test_valid_unicode_and_existing_byte_limit_are_preserved(self):
        emoji = json.loads('"\\ud83d\\ude00"')
        intent = FirstUseIntent('synthetic', emoji * 1024, '理解模型')
        self.assertEqual(4096, len(intent.title.encode('utf-8')))
        with self.assertRaises(FirstUseRejected) as caught:
            FirstUseIntent('synthetic', emoji * 1024 + 'x', 'Goal')
        self.assertEqual('invalid_intent', caught.exception.code)
        authority = SyntheticFirstUseAuthority()
        receiver = authority.authorize(principal='学习者' + emoji, surface='合成界面',
                                       operation_id='first', intent=intent, locator=locator())
        binding = authority.binding(receiver)
        authority.check(receiver, binding)
        authority.revoke(receiver)
        self.assertEqual({}, authority._grants)
        self.assertEqual({}, authority._revocations)
