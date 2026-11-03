"""Malformed persisted authority is a stable rejection, not a host crash."""
import copy
import unittest
import yaml
from unittest import mock

from scripts.runtime_adapter import ResolutionError, _load_bounded_yaml, _load_contract
from scripts.runtime_broker import RuntimeSessionBroker
import tests.test_reference_host as host_fixture
from tests.test_runtime_broker import (
    RUNTIME_PATH, REGISTRY_PATH, HANDOFF_PATH, READ_PATH, typed_evidence,
    branch_runtime, learning_handoff,
)


BAD_SCALARS = ('9999-99-99', '!!timestamp not-a-time', '!!bool not-a-bool', '!!int ""')


class AuthorityYamlErrorsTests(unittest.TestCase):
    def test_authority_parsers_reject_malformed_scalar_values_and_keys(self):
        parsers = (_load_contract, RuntimeSessionBroker._parse_branch_runtime,
                   RuntimeSessionBroker._parse_branch_registry)
        for parser in parsers:
            for scalar in BAD_SCALARS:
                for content in (f'invalid: {scalar}\n', f'{scalar}: invalid\n'):
                    with self.subTest(parser=parser.__name__, content=content):
                        with self.assertRaises(ResolutionError):
                            parser(content)

    def test_existing_host_read_and_write_reject_corrupt_authority_without_writes(self):
        for path in (RUNTIME_PATH, REGISTRY_PATH, HANDOFF_PATH):
            for scalar in BAD_SCALARS:
                for position in ('key', 'value'):
                    with self.subTest(path=path, scalar=scalar, position=position):
                        fixture = host_fixture.ReferenceLearningHostTests()
                        fixture.setUp()
                        try:
                            suffix = (f'{scalar}: invalid\n' if position == 'key'
                                      else f'invalid: {scalar}\n')
                            if path == HANDOFF_PATH:
                                runtime = branch_runtime()
                                runtime['generations'][1]['handoff_ref'] = HANDOFF_PATH
                                fixture.provider.docs[RUNTIME_PATH] = yaml.safe_dump(runtime, sort_keys=False)
                                fixture.provider.docs[HANDOFF_PATH] = yaml.safe_dump(learning_handoff(source=1, target=2))
                                fixture.provider.blobs[HANDOFF_PATH] = '3' * 40
                            fixture.provider.docs[path] += '\n' + suffix
                            before = copy.deepcopy(fixture.provider.docs)
                            fixture.provider.calls.clear()
                            for operation, arguments in (
                                ('read_learning_context', {'required_paths': [READ_PATH]}),
                                ('create_evidence', {'content': typed_evidence()}),
                            ):
                                result = fixture.invoke(operation, **arguments)
                                self.assertEqual({'code': 'guard_rejected', 'retryable': False}, result['error'])
                                self.assertFalse(result['ok'])
                                self.assertNotIn('result', result)
                            self.assertEqual(before, fixture.provider.docs)
                            self.assertFalse(any(call[0] in {'create', 'update'}
                                                 for call in fixture.provider.calls))
                        finally:
                            fixture.doCleanups()

    def test_scalar_constructor_overflow_is_input_failure(self):
        with mock.patch('scripts.runtime_adapter.yaml.safe_load',
                        side_effect=OverflowError('private details')):
            with self.assertRaises(ResolutionError) as caught:
                _load_bounded_yaml('key: value\n', 'authority')
            self.assertNotIn('private details', str(caught.exception))

    def test_unexpected_loader_failure_is_not_swallowed(self):
        with mock.patch('scripts.runtime_adapter.yaml.safe_load',
                        side_effect=RuntimeError('operator failure')):
            with self.assertRaisesRegex(RuntimeError, 'operator failure'):
                _load_bounded_yaml('key: value\n', 'authority')

    def test_provider_failure_is_not_reclassified_as_yaml(self):
        fixture = host_fixture.ReferenceLearningHostTests()
        fixture.setUp()
        try:
            with mock.patch.object(fixture.provider, 'materialize',
                                   side_effect=ValueError('provider defect')):
                with self.assertRaisesRegex(ValueError, 'provider defect'):
                    fixture.invoke('read_learning_context', required_paths=[READ_PATH])
        finally:
            fixture.doCleanups()

    def test_valid_scalars_keep_existing_types(self):
        import datetime
        self.assertEqual({'stamp': datetime.date(2026, 11, 3), 'flag': True, 'count': 7},
                         _load_bounded_yaml('stamp: 2026-11-03\nflag: true\ncount: 7\n', 'authority'))


if __name__ == '__main__':
    unittest.main()
