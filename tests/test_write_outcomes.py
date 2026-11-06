"""Ordinary Git write outcome tests; all Git, checkout and cleanup are doubles."""
from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest

from scripts.runtime_adapter import (
    CasConflict,
    DeploymentGuard,
    GitCliProvider,
    WriteCleanupFailed,
    WriteOutcomeUnknown,
    WritePublishedCleanupFailed,
)
from scripts.reference_host import ReferenceLearningHost


BASE = "a" * 40
BLOB = "b" * 40
COMMIT = "c" * 40
PATH = "evidence/synthetic.yaml"
CONTENT = "synthetic content"


class FakeGitWriter(GitCliProvider):
    """Use real update/create orchestration, but no filesystem or Git process."""

    def __init__(self, *, create, push_error=None, cleanup_error=None, stale=False):
        self.create = create
        self.push_error = push_error
        self.cleanup_error = cleanup_error
        self.stale = stale
        self.pushes = 0
        self.cleanups = 0

    def _binding(self, repository_id):
        return SimpleNamespace(writable=True)

    def _safe_path(self, path):
        return PurePosixPath(path)

    def _checkout_branch(self, binding, branch):
        return object(), object(), ("d" * 40 if self.stale else BASE), "refs/heads/main", 100, 100000

    def _regular_blob(self, *args, **kwargs):
        return None if self.create else (BLOB, "100644")

    def _git_bytes(self, *args, **kwargs):
        if args[0] == "hash-object":
            return BLOB.encode()
        if args[0] == "update-index":
            return b""
        if args[0] == "diff":
            return PATH.encode() + b"\0"
        if args[:2] == ("cat-file", "blob"):
            return CONTENT.encode()
        raise AssertionError("unexpected Git bytes call")

    def _git(self, *args, **kwargs):
        if args[:2] == ("cat-file", "-s"):
            return "1"
        if args[:2] == ("rev-parse", "--verify"):
            return COMMIT if args[2] == "HEAD^{commit}" else BLOB
        if args[0] == "-c":
            return ""
        if args[0] == "push":
            assert args[2] == "--force-with-lease=refs/heads/main:" + BASE
            self.pushes += 1
            if self.push_error is not None:
                raise self.push_error
            return ""
        raise AssertionError("unexpected Git call")

    def _verify_regular_tree(self, *args):
        return ()

    def _bounded_blob_fetch_cap(self, *args):
        return 100000

    def _validate_snapshot_budget(self, *args, **kwargs):
        return None

    def _cleanup_checkout_tempdir(self, td):
        self.cleanups += 1
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def write(self):
        if self.create:
            return self.create_text(1, "main", PATH, CONTENT, "synthetic", expected_ref_sha=BASE)
        return self.update_text(1, "main", PATH, CONTENT, BLOB, "synthetic", expected_ref_sha=BASE)


class OrdinaryWriteOutcomeTests(unittest.TestCase):
    def test_acknowledged_publish_is_not_retryable_cas_after_cleanup_failure(self):
        for create in (False, True):
            with self.subTest(create=create):
                error = PermissionError("operator-only cleanup detail")
                provider = FakeGitWriter(create=create, cleanup_error=error)
                with self.assertRaises(WritePublishedCleanupFailed) as caught:
                    provider.write()
                self.assertEqual(caught.exception.publication_state, "published")
                self.assertEqual(caught.exception.published_commit, COMMIT)
                self.assertTrue(caught.exception.cleanup_failed)
                self.assertIs(caught.exception.__cause__, error)
                self.assertEqual((provider.pushes, provider.cleanups), (1, 1))

    def test_push_exception_remains_unknown_even_when_cleanup_also_fails(self):
        for create in (False, True):
            for cleanup in (False, True):
                with self.subTest(create=create, cleanup=cleanup):
                    provider = FakeGitWriter(
                        create=create,
                        push_error=CasConflict("transport has no trusted publication receipt"),
                        cleanup_error=OSError(39, "directory not empty") if cleanup else None,
                    )
                    with self.assertRaises(WriteOutcomeUnknown) as caught:
                        provider.write()
                    self.assertEqual(caught.exception.publication_state, "unknown")
                    self.assertEqual(caught.exception.cleanup_failed, cleanup)
                    self.assertFalse(hasattr(caught.exception, "published_commit"))
                    self.assertEqual((provider.pushes, provider.cleanups), (1, 1))

    def test_prepublication_cas_and_cleanup_failure_are_distinct(self):
        for create in (False, True):
            for cleanup in (False, True):
                with self.subTest(create=create, cleanup=cleanup):
                    provider = FakeGitWriter(create=create, stale=True, cleanup_error=PermissionError() if cleanup else None)
                    expected = WriteCleanupFailed if cleanup else CasConflict
                    with self.assertRaises(expected):
                        provider.write()
                    self.assertEqual((provider.pushes, provider.cleanups), (0, 1))

    def test_normal_success_preserves_commit_return(self):
        for create in (False, True):
            with self.subTest(create=create):
                provider = FakeGitWriter(create=create)
                self.assertEqual(provider.write(), COMMIT)
                self.assertEqual((provider.pushes, provider.cleanups), (1, 1))

    def test_host_envelopes_remain_bounded_and_nonretryable(self):
        for error, code in (
            (WritePublishedCleanupFailed(COMMIT), "write_published_cleanup_failed"),
            (WriteOutcomeUnknown("operator-only detail"), "write_outcome_unknown"),
            (WriteCleanupFailed("operator-only detail"), "write_cleanup_failed"),
            (CasConflict("operator-only detail"), "cas_conflict"),
        ):
            with self.subTest(code=code):
                def create(*args, **kwargs):
                    raise error
                host = ReferenceLearningHost(SimpleNamespace(create_evidence=create), object())
                result = host.invoke({"operation": "create_evidence", "arguments": {"content": CONTENT}})
                self.assertEqual(result, {
                    "surface_version": "v4", "ok": False, "operation": "create_evidence",
                    "error": {"code": code, "retryable": code == "cas_conflict"},
                })
                self.assertNotIn(COMMIT, repr(result))
                self.assertNotIn("operator-only", repr(result))

    def test_deployment_guard_preserves_typed_outcomes(self):
        for error in (CasConflict("refused"), WriteOutcomeUnknown("unknown"), WriteCleanupFailed("cleanup"), WritePublishedCleanupFailed(COMMIT)):
            with self.subTest(error=type(error).__name__):
                def update(*args, **kwargs):
                    raise error
                guard = object.__new__(DeploymentGuard)
                guard.provider = SimpleNamespace(update_text=update)
                guard.check = lambda session: None
                with self.assertRaises(type(error)) as caught:
                    guard.guarded_update(SimpleNamespace(instance_repository_id=1), branch="main", path=PATH, content=CONTENT, expected_blob_sha=BLOB, message="synthetic", expected_ref_sha=BASE)
                self.assertIs(caught.exception, error)

    def test_deployment_guard_does_not_assume_untyped_provider_error_is_refusal(self):
        def update(*args, **kwargs):
            raise OSError("unclassified provider failure")
        guard = object.__new__(DeploymentGuard)
        guard.provider = SimpleNamespace(update_text=update)
        guard.check = lambda session: None
        with self.assertRaises(WriteOutcomeUnknown):
            guard.guarded_update(SimpleNamespace(instance_repository_id=1), branch="main", path=PATH, content=CONTENT, expected_blob_sha=BLOB, message="synthetic", expected_ref_sha=BASE)


if __name__ == "__main__":
    unittest.main()
