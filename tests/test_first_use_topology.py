"""Exact topology evidence for first-use assertions; no Host or learner state."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_first_use as fixture
from tests.test_first_use import exact_commit_topology, git, repository


class FirstUseTopologyTests(unittest.TestCase):
    def test_real_git_exact_order_and_parent_tree_match_old_reads(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / 'fixture'
            remote = repository(root, {'README.md': 'synthetic\n'})
            candidates = []
            for index in range(10):
                (root / 'README.md').write_text(f'synthetic {index}\n', encoding='utf-8')
                git('add', '.', cwd=root)
                git('commit', '-qm', f'fixture {index}', cwd=root)
                candidates.append(git('rev-parse', 'HEAD', cwd=root))
            git('push', '-q', str(remote), 'HEAD:main', cwd=root)
            # Deliberately non-topological order: exact requested identities matter.
            candidates = [candidates[1], candidates[0], *candidates[2:]]
            with mock.patch(__name__ + '.git', wraps=git) as old_calls:
                expected = [(git('--git-dir', str(remote), 'rev-parse', c + '^'),
                             git('--git-dir', str(remote), 'rev-parse', c + '^{tree}'))
                            for c in candidates]
            self.assertEqual(20, old_calls.call_count)
            with mock.patch('tests.test_first_use.git', wraps=git) as calls:
                self.assertEqual(expected, exact_commit_topology(remote, candidates))
            self.assertEqual(1, calls.call_count)

    def test_missing_extra_reordered_or_non_single_parent_rows_rejected(self):
        a, b, p, t = 'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40
        valid = f'{a} {p} {t}\n{b} {a} {t}'
        bad = [f'{a} {p} {t}', valid + f'\n{b} {a} {t}',
               f'{b} {a} {t}\n{a} {p} {t}',
               f'{a} {t}\n{b} {a} {t}',
               f'{a} {p} {b} {t}\n{b} {a} {t}',
               f'{a} {p} {t}\n\n{b} {a} {t}',
               f'{a} nope {t}\n{b} {a} {t}',
               f'{a} {p} invalid\n{b} {a} {t}']
        for output in bad:
            with self.subTest(output=output), mock.patch('tests.test_first_use.git', return_value=output):
                with self.assertRaises(AssertionError):
                    exact_commit_topology(Path('synthetic.git'), [a, b])

    def test_invalid_requested_identities_rejected_before_git(self):
        for candidates in ([], ['a' * 40] * 2, ['HEAD'], ['a' * 39], ['A' * 40], [[]]):
            with self.subTest(candidates=candidates), mock.patch('tests.test_first_use.git') as run:
                with self.assertRaises(AssertionError):
                    exact_commit_topology(Path('synthetic.git'), candidates)
                run.assert_not_called()

    def test_assertion_still_checks_every_parent_and_tree(self):
        candidates = [f'{i + 1:040x}' for i in range(10)]
        baseline, tree = 'b' * 40, 'c' * 40
        parents = [baseline] + candidates[:-1]
        steps = [dict(candidate=c, descriptor=dict(parent=p, tree=tree))
                 for c, p in zip(candidates, parents)]
        case = fixture.FirstUseTests('test_blank_normal_staging_activation_and_checkpoint')
        case.baseline, case.instance_remote = baseline, Path('synthetic.git')
        case.store = SimpleNamespace(read=lambda: dict(confirmed=10, steps=steps))
        good = list(zip(parents, [tree] * 10))
        with mock.patch('tests.test_first_use.git', return_value='10'):
            with mock.patch('tests.test_first_use.exact_commit_topology', return_value=good):
                case.assert_ten_transitions()
            for index in range(10):
                for field in (0, 1):
                    bad = [list(row) for row in good]
                    bad[index][field] = 'e' * 40
                    with self.subTest(index=index, field=field), mock.patch(
                            'tests.test_first_use.exact_commit_topology', return_value=bad):
                        with self.assertRaises(AssertionError):
                            case.assert_ten_transitions()
