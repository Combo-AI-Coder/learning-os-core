"""Cheap instrumentation tests: never invoke the expensive first-use journey."""
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import first_use_profile as profile


class ProfileTests(unittest.TestCase):
    def test_git_family_never_emits_operands(self):
        cases = [(("--git-dir", "/private/path", "show", "secret"), "show"),
                 (("-c", "credential=secret", "cat-file", "blob", "secret"), "cat-file"),
                 (("--git-dir=/private/path", "push", "secret"), "push"),
                 (("unknown", "push"), "other"), (("-c",), "other"),
                 (("--unknown=secret", "push"), "other"), ((), "other")]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(expected, profile.git_family(args))

    def test_return_exception_count_timing_and_redaction(self):
        metrics = profile.Measurements(clock=iter([0, 2, 3, 8]).__next__)
        value = object()
        success = metrics.git_call("test_helper", lambda *a, **kw: value)
        self.assertIs(value, success("show", "private-path", token="secret"))
        error = RuntimeError("private-path secret")
        def fail(*args):
            raise error
        with self.assertRaises(RuntimeError) as caught:
            metrics.git_call("provider", fail)("cat-file", "private-path")
        self.assertIs(error, caught.exception)
        rows = metrics.report()["git"]
        self.assertEqual([("cat-file", 5, 1), ("show", 2, 0)],
                         [(x["family"], x["wall_seconds"], x["raised"]) for x in rows])
        self.assertNotIn("private-path", json.dumps(metrics.report()))
        self.assertNotIn("secret", json.dumps(metrics.report()))

    def test_nested_phase_is_inclusive_and_context_restored_on_failure(self):
        metrics = profile.Measurements(clock=iter([0, 1, 3, 5]).__next__)
        def inner():
            self.assertEqual("inner", metrics.current.get())
            raise ValueError("not emitted")
        with self.assertRaises(ValueError):
            metrics.phase("outer", metrics.phase("inner", inner))()
        self.assertEqual("unscoped", metrics.current.get())
        self.assertEqual(5, metrics.phases["outer"]["wall_seconds"])
        self.assertEqual(2, metrics.phases["inner"]["wall_seconds"])

    def test_packed_keyword_arguments_preserve_original_binding(self):
        metrics = profile.Measurements()
        class Provider:
            def execute(self, args, *, binding=None):
                return args, binding
        original = Provider.execute
        Provider.execute = metrics.git_call("provider", original, argument_offset=1, packed=True)
        binding = object()
        self.assertEqual((("show",), binding), Provider().execute(args=("show",), binding=binding))
        with self.assertRaises(TypeError):
            Provider().execute()
        self.assertEqual({"show", "other"}, {x["family"] for x in metrics.report()["git"]})

    def target(self, fail=False):
        events = []
        class Provider:
            def _run_git(self, args, **kwargs):
                events.append(tuple(args))
                return b"exact-bytes"
            def text(self):
                return self._run_git(("show", "secret-path"))
            def binary(self):
                return self._run_git(("cat-file", "blob", "secret-object"))
        class Host:
            def close(self):
                events.append("host-close")
        class Service:
            def inspect(self):
                return 7
        class Case(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                events.append("class-setup")
            @classmethod
            def tearDownClass(cls):
                events.append("class-cleanup")
            def setUp(self):
                self.addCleanup(events.append, "cleanup")
                events.append("setup")
            def prepare(self):
                return 3
            def apply(self):
                self.assertEqual(b"exact-bytes", Provider().text())
                self.assertEqual(b"exact-bytes", Provider().binary())
            def assert_ten_transitions(self):
                events.append("all-original-assertions")
            def journey(self):
                self.assertEqual(3, self.prepare())
                self.apply()
                self.assertEqual(7, Service().inspect())
                self.assert_ten_transitions()
                Host().close()
                if fail:
                    self.fail("private-exception-path")
        setattr(Case, profile.CASES["lost-ack"], Case.journey)
        target = SimpleNamespace(FirstUseTests=Case, GitCliProvider=Provider,
                                 ReferenceLearningHost=Host, SyntheticFirstUseService=Service,
                                 git=lambda *args: "unchanged")
        return target, events

    def test_full_fake_runner_preserves_calls_assertions_cleanup_and_restores(self):
        for fail in (False, True):
            target, events = self.target(fail)
            original = target.GitCliProvider._run_git
            setup_descriptor = target.FirstUseTests.__dict__["setUpClass"]
            report = profile.run_case("lost-ack", target)
            self.assertEqual(not fail, report["passed"])
            self.assertEqual(1, report["tests_run"])
            self.assertEqual(int(fail), report["failures"])
            self.assertEqual(2, sum(row["calls"] for row in report["git"]))
            self.assertIn("all-original-assertions", events)
            self.assertIn("cleanup", events)
            self.assertIn("class-cleanup", events)
            self.assertIs(original, target.GitCliProvider._run_git)
            self.assertIs(setup_descriptor, target.FirstUseTests.__dict__["setUpClass"])
            self.assertNotIn("doCleanups", target.FirstUseTests.__dict__)
            self.assertNotIn("secret", json.dumps(report))
            self.assertNotIn("private-exception", json.dumps(report))

    def test_patches_restore_on_abnormal_context_exit(self):
        target, _ = self.target()
        original = target.git
        with self.assertRaises(KeyboardInterrupt):
            with profile.instrument(target, profile.Measurements()):
                raise KeyboardInterrupt()
        self.assertIs(original, target.git)

    def test_cli_requires_explicit_opt_in_and_unknown_case_fails_before_execution(self):
        with mock.patch.object(profile, "run_case") as run, mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                profile.main(["--case", "lost-ack"])
            with self.assertRaises(SystemExit):
                profile.main(["--case", "other", "--execute"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
