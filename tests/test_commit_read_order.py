"""Synthetic source-only candidate tests; execution requires separate approval."""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from scripts.runtime_adapter import ResolutionError, SyntheticPreparedGitProvider


def commit_id(raw):
    return hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class CommitReadOrderTests(unittest.TestCase):
    def setUp(self):
        self.provider = object.__new__(SyntheticPreparedGitProvider)
        self.repo = Path("/synthetic-private-checkout")
        self.parent_raw = b"tree " + b"1" * 40 + b"\n\nparent\n"
        self.parent = commit_id(self.parent_raw)
        self.metadata = {"step_index": 0}
        self.message = self.provider._first_use_json(self.metadata) + b"\n"
        self.raw = (b"tree " + b"2" * 40 + b"\nparent " + self.parent.encode()
                    + b"\n\n" + self.message)
        self.candidate = commit_id(self.raw)
        self.descriptor = dict(candidate=self.candidate, parent=self.parent,
                               tree="2" * 40, parent_tree="1" * 40,
                               metadata=self.metadata, path="new.txt", old_blob=None,
                               old_mode=None, new_blob="3" * 40)
        self.calls = []
        self.missing_parent = False
        self.bad_candidate_type = False
        self.transport = mock.patch.object(self.provider, "_git_bytes", side_effect=self.git)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def git(self, *args, **kwargs):
        self.assertEqual(self.repo, kwargs["cwd"])
        self.assertEqual({"GIT_NO_LAZY_FETCH": "1"}, kwargs["extra_env"])
        if args[:2] == ("cat-file", "commit"):
            sha = args[2]
            self.calls.append(("body", sha))
            if sha == self.parent and self.missing_parent:
                raise ResolutionError("missing parent sentinel")
            return self.raw if sha == self.candidate else self.parent_raw
        self.assertEqual(("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"), args)
        sha = kwargs["input_bytes"].decode("ascii").removesuffix("\n")
        self.calls.append(("header", sha))
        if sha == self.parent and self.missing_parent:
            return (sha + " missing\n").encode()
        raw = self.raw if sha == self.candidate else self.parent_raw
        kind = "tree" if sha == self.candidate and self.bad_candidate_type else "commit"
        return f"{sha} {kind} {len(raw)}\n".encode()

    def test_candidate_metadata_precedes_missing_parent_and_bad_candidate_header(self):
        self.missing_parent = self.bad_candidate_type = True
        self.raw = self.raw.replace(self.message, b"wrong metadata\n")
        with self.assertRaisesRegex(ResolutionError, "prepared commit parent or operation metadata differs"):
            self.provider._first_use_verify(self.repo, self.descriptor)
        self.assertEqual([("body", self.candidate)], self.calls)

    def test_candidate_type_precedes_missing_parent(self):
        self.missing_parent = self.bad_candidate_type = True
        with self.assertRaisesRegex(ResolutionError, "Git object is not a commit"):
            self.provider._first_use_verify(self.repo, self.descriptor)
        self.assertEqual([("body", self.candidate), ("header", self.candidate)], self.calls)

    def test_candidate_tree_mismatch_precedes_missing_parent(self):
        self.missing_parent = True
        descriptor = deepcopy(self.descriptor)
        descriptor["tree"] = "4" * 40
        with self.assertRaisesRegex(ResolutionError, "prepared candidate tree differs"):
            self.provider._first_use_verify(self.repo, descriptor)
        self.assertEqual([("body", self.candidate), ("header", self.candidate)], self.calls)

    def test_candidate_malformed_tree_precedes_missing_parent(self):
        self.missing_parent = True
        self.raw = self.raw.replace(b"tree ", b"oops ", 1)
        with self.assertRaisesRegex(ResolutionError, "Git commit is missing a tree"):
            self.provider._first_use_verify(self.repo, self.descriptor)
        self.assertEqual([("body", self.candidate), ("header", self.candidate)], self.calls)

    def test_valid_candidate_reaches_missing_parent_without_body_retry(self):
        self.missing_parent = True
        with self.assertRaisesRegex(ResolutionError, "Git object header is invalid"):
            self.provider._first_use_verify(self.repo, self.descriptor)
        self.assertEqual([("body", self.candidate), ("header", self.candidate),
                          ("header", self.parent)], self.calls)

    def test_fresh_verification_rejects_same_length_corruption_after_success(self):
        # Mock only traversal/budget here. Real traversal is wrapped in the
        # existing exact-object-walk integration regression, never replaced.
        objects = [("new.txt", "3" * 40, "100644", "blob")]
        with mock.patch.object(self.provider, "_tree_objects_from_root",
                               side_effect=[[], objects]) as walks, \
                mock.patch.object(self.provider, "_validate_snapshot_budget"), \
                mock.patch.object(self.provider, "_bounded_blob_fetch_cap", return_value=1):
            self.provider._first_use_verify(self.repo, self.descriptor)
            self.assertEqual(2, walks.call_count)
            self.assertEqual([("body", self.candidate), ("header", self.candidate),
                              ("header", self.parent), ("body", self.parent)], self.calls)
            self.calls.clear()
            self.parent_raw = self.parent_raw.replace(b"parent", b"broken")
            with self.assertRaisesRegex(ResolutionError, "prepared commit content identity differs"):
                self.provider._first_use_verify(self.repo, self.descriptor)
            self.assertEqual([("body", self.candidate), ("header", self.candidate),
                              ("header", self.parent), ("body", self.parent)], self.calls)
            self.assertEqual(2, walks.call_count, "failed fresh read must not reuse or start a walk")

    def test_content_size_and_hash_are_checked(self):
        for size, text in ((len(self.raw) + 1, "content size differs"),
                           (len(self.raw), "content identity differs")):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ResolutionError, text):
                    self.provider._first_use_check_commit_content("0" * 40, self.raw, size)
