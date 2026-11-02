"""User-facing offline exporter entry points; no native Host or model calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests import answer_closure_fixture as answer
from tests import git_recovery_fixture as git


class ReferenceCliTests(unittest.TestCase):
    def invoke(self, module, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            module.main(args)
        return out.getvalue(), err.getvalue()

    def test_help_explains_new_directory_without_running(self):
        for module in (answer, git):
            with self.subTest(module=module.__name__), mock.patch.object(answer, 'export_replay') as export, mock.patch.object(git, 'GitRecoveryJourney') as journey:
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(module, ['--help'])
                self.assertEqual(0, raised.exception.code)
                export.assert_not_called()
                journey.assert_not_called()

    def test_existing_output_preserved_before_any_replay(self):
        for module in (answer, git):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temp:
                marker = Path(temp) / 'retained.json'
                marker.write_bytes(b'old result')
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(answer, 'export_replay') as export, mock.patch.object(git, 'GitRecoveryJourney') as journey, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as raised:
                        module.main(['--output', temp])
                self.assertEqual(2, raised.exception.code)
                self.assertIn('preserve it and choose a new directory', err.getvalue())
                self.assertNotIn('Traceback', err.getvalue())
                self.assertEqual('', out.getvalue())
                self.assertEqual(b'old result', marker.read_bytes())
                export.assert_not_called()
                journey.assert_not_called()

    def test_core_and_nested_paths_rejected_without_creation(self):
        for module in (answer, git):
            for target in (module.ROOT, module.ROOT / 'new-cli-output'):
                with self.subTest(module=module.__name__, target=target), mock.patch.object(answer, 'export_replay') as export, mock.patch.object(git, 'GitRecoveryJourney') as journey:
                    with self.assertRaises(SystemExit) as raised:
                        self.invoke(module, ['--output', str(target)])
                    self.assertEqual(2, raised.exception.code)
                    export.assert_not_called()
                    journey.assert_not_called()
        self.assertFalse((answer.ROOT / 'new-cli-output').exists())

    def test_real_answer_export_summary_matches_written_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'answer'
            out, err = self.invoke(answer, ['--output', str(target)])
            self.assertEqual('', err)
            self.assertEqual({'status': 'passed', 'packet_count': 6,
                              'output_file_count': 7, 'model_resampled': False}, json.loads(out))
            self.assertEqual(7, len(list(target.iterdir())))
            self.assertTrue((target / 'mechanical-replay.json').is_file())
            self.assertEqual(6, len(list(target.glob('*-packet.json'))))

    def test_answer_replay_failure_remains_original_without_success(self):
        with tempfile.TemporaryDirectory() as temp:
            failure = ValueError('frozen artifact mismatch')
            out = io.StringIO()
            with mock.patch.object(answer, 'replay', side_effect=failure), contextlib.redirect_stdout(out):
                with self.assertRaises(ValueError) as raised:
                    answer.main(['--output', str(Path(temp) / 'new')])
            self.assertIs(failure, raised.exception)
            self.assertEqual('', out.getvalue())
            self.assertFalse((Path(temp) / 'new').exists())

    def test_answer_write_failure_is_not_relabelled_usage_or_success(self):
        with tempfile.TemporaryDirectory() as temp:
            failure = OSError('synthetic write failure')
            out = io.StringIO()
            with mock.patch.object(answer, 'export_replay', side_effect=failure), contextlib.redirect_stdout(out):
                with self.assertRaises(OSError) as raised:
                    answer.main(['--output', str(Path(temp) / 'new')])
            self.assertIs(failure, raised.exception)
            self.assertEqual('', out.getvalue())

    def test_git_failure_keeps_partial_receipt_and_original_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'git'
            failure = RuntimeError('synthetic journey failure')
            with mock.patch.object(git, 'GitRecoveryJourney') as factory:
                factory.return_value.records = [{'mode': 'produce'}]
                factory.return_value.run.side_effect = failure
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(RuntimeError) as raised:
                    git.main(['--output', str(target)])
                self.assertIs(failure, raised.exception)
                self.assertEqual('', out.getvalue())
                receipt = json.loads((target / 'failure.json').read_text())
                self.assertEqual('RuntimeError', receipt['type'])
                self.assertEqual([{'mode': 'produce'}], receipt['completed_processes'])

    def test_git_success_keeps_existing_result_contract(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(git, 'GitRecoveryJourney') as factory:
            factory.return_value.run.return_value = {'status': 'passed', 'processes': [{}] * 8,
                                                     'source_identity': {'source_identity_sha256': 'synthetic'}}
            out, err = self.invoke(git, ['--output', str(Path(temp) / 'git')])
            self.assertEqual('', err)
            self.assertEqual({'status': 'passed', 'process_count': 8,
                              'source_identity_sha256': 'synthetic'}, json.loads(out))

    def test_git_worker_route_not_reclassified_as_export(self):
        with mock.patch.object(git, 'worker') as worker:
            out, err = self.invoke(git, ['--worker', '/tmp/synthetic-unused', 'read'])
            worker.assert_called_once_with(Path('/tmp/synthetic-unused').resolve(), 'read')
            self.assertEqual(('', ''), (out, err))


if __name__ == '__main__':
    unittest.main()
