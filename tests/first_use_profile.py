"""Opt-in, aggregate wall-time attribution of existing synthetic first-use tests.

Importing this module never runs a journey. See docs/evaluations/core34-first-use-profile.md.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import unittest
from unittest import mock


CASES = {
    "lost-ack": "test_all_ten_accepted_lost_ack_restart_exact_candidates",
    "empty-tree": "test_truly_empty_tree_baseline_activates_and_recovers_first_ack",
}
GIT_FAMILIES = frozenset({
    "init", "checkout", "config", "add", "commit", "clone", "rev-parse",
    "show", "rev-list", "ls-tree", "fetch", "push", "cat-file", "hash-object",
    "update-index", "write-tree", "commit-tree", "ls-remote", "symbolic-ref",
    "status", "diff", "update-ref", "read-tree", "merge-base", "remote",
})


def git_family(args):
    """Only emit allowlisted command names; never scan operands for a match."""
    args = tuple(args)
    i = 0
    while i < len(args):
        value = args[i]
        if value in ("-c", "-C", "--git-dir", "--work-tree"):
            i += 2
        elif isinstance(value, str) and value.startswith(("--git-dir=", "--work-tree=")):
            i += 1
        elif value in ("--no-lazy-fetch", "--no-optional-locks"):
            i += 1
        else:
            return value if isinstance(value, str) and value in GIT_FAMILIES else "other"
    return "other"


class Measurements:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.phases = {}
        self.git = {}
        self.current = ContextVar("first_use_profile_phase", default="unscoped")

    @contextmanager
    def measure(self, table, key):
        start = self.clock()
        raised = False
        try:
            yield
        except BaseException:
            raised = True
            raise
        finally:
            elapsed = self.clock() - start
            row = table.setdefault(key, {"calls": 0, "raised": 0, "wall_seconds": 0.0})
            row["calls"] += 1
            row["raised"] += int(raised)
            row["wall_seconds"] += elapsed

    def phase(self, name, function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            token = self.current.set(name)
            try:
                with self.measure(self.phases, name):
                    return function(*args, **kwargs)
            finally:
                self.current.reset(token)
        return wrapped

    def git_call(self, layer, function, argument_offset=0, packed=False):
        @wraps(function)
        def wrapped(*args, **kwargs):
            command = (args[argument_offset] if len(args) > argument_offset else kwargs.get("args", ())) if packed else args[argument_offset:]
            key = (layer, self.current.get(), git_family(command))
            with self.measure(self.git, key):
                return function(*args, **kwargs)
        return wrapped

    def report(self):
        return {
            "phases": {key: dict(value) for key, value in sorted(self.phases.items())},
            "git": [dict(layer=key[0], phase=key[1], family=key[2], **value)
                    for key, value in sorted(self.git.items())],
            "measurement": "in_process_inclusive_wall_seconds",
            "limits": ["nested phases overlap; do not sum", "instrumentation overhead included",
                       "Git wall time includes provider wrapper work, not only process execution",
                       "not log-delivery timing or causal speedup evidence",
                       "only selected test and current platform; not whole-suite or Windows proof"],
        }


def patch_phase(stack, metrics, owner, attribute, name):
    descriptor = inspect.getattr_static(owner, attribute)
    if isinstance(descriptor, classmethod):
        replacement = classmethod(metrics.phase(name, descriptor.__func__))
    else:
        replacement = metrics.phase(name, getattr(owner, attribute))
    stack.enter_context(mock.patch.object(owner, attribute, replacement))


@contextmanager
def instrument(target, metrics):
    """Patch only for this explicit single-threaded diagnostic; restore on all exits."""
    with ExitStack() as stack:
        for attribute, name in (
            ("setUpClass", "class_fixture_setup"), ("tearDownClass", "class_fixture_cleanup"),
            ("setUp", "fixture_setup"), ("doCleanups", "fixture_cleanup"),
            ("prepare", "prepare"), ("apply", "apply"),
            ("assert_ten_transitions", "topology_assertion"),
        ):
            patch_phase(stack, metrics, target.FirstUseTests, attribute, name)
        patch_phase(stack, metrics, target.SyntheticFirstUseService, "inspect", "recovery_inspect")
        patch_phase(stack, metrics, target.ReferenceLearningHost, "close", "host_close")
        stack.enter_context(mock.patch.object(target, "git", metrics.git_call("test_helper", target.git)))
        provider = target.GitCliProvider
        stack.enter_context(mock.patch.object(provider, "_run_git",
            metrics.git_call("provider", provider._run_git, argument_offset=1, packed=True)))
        yield


def run_case(case, target):
    """Run the original complete test through unittest, preserving setup and cleanup."""
    if case not in CASES:
        raise ValueError("unsupported profile case")
    metrics = Measurements()
    suite = unittest.TestSuite([target.FirstUseTests(CASES[case])])
    result = unittest.TestResult()  # No raw exception text or filesystem paths in JSON.
    with instrument(target, metrics):
        with metrics.measure(metrics.phases, "selected_test_total"):
            suite.run(result)
    passed = result.wasSuccessful() and result.testsRun == 1 and not result.skipped
    return dict(schema_version=1, case=case, passed=passed, tests_run=result.testsRun,
                failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
                **metrics.report())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--execute", action="store_true", help="explicitly run the potentially expensive synthetic case")
    options = parser.parse_args(argv)
    if not options.execute:
        parser.error("--execute is required; no journey was run")
    from tests import test_first_use
    report = run_case(options.case, test_first_use)
    root = Path(__file__).resolve().parents[1]
    report["environment"] = {"platform": sys.platform, "python": platform.python_version()}
    report["source_sha256"] = {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in ("tests/first_use_profile.py", "tests/test_first_use.py",
                     "scripts/runtime_adapter.py", "scripts/first_use.py",
                     "scripts/reference_host.py")
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
