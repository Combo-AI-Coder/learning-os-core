"""Offline synthetic regressions for the REST provider trust boundary."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest import mock
import zipfile

from scripts import runtime_adapter as adapter

REPO_ID = 9000000401
HEAD = "a" * 40
BLOB = "b" * 40


def archive(entries):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries:
            if isinstance(name, str):
                # Preserve adversarial raw names on Windows: ZipInfo's
                # constructor otherwise normalizes the host separator.
                entry = zipfile.ZipInfo("placeholder")
                entry.filename = entry.orig_filename = name
            else:
                entry = name
            bundle.writestr(entry, content)
    return stream.getvalue()


class GitHubBoundaryTests(unittest.TestCase):
    def provider(self, entries=(), **kwargs):
        provider = adapter.GitHubApiProvider(token="synthetic-unused", **kwargs)
        self.addCleanup(provider.close)
        provider._repo = mock.Mock(return_value={"id": REPO_ID, "full_name": "synthetic/repo"})
        provider._commit = mock.Mock(return_value=HEAD)
        provider._request_bytes = mock.Mock(return_value=archive(entries))
        return provider

    def test_normal_archive_preserves_content_and_provenance(self):
        provider = self.provider([("root/", b""), ("root/docs/", b""), ("root/docs/readme.md", b"hello")])
        snapshot = provider.materialize(REPO_ID, "main")
        self.assertEqual(HEAD, snapshot.commit_sha)
        self.assertEqual(REPO_ID, snapshot.repository_id)
        self.assertEqual(("docs/readme.md",), snapshot.paths)
        self.assertEqual(b"hello", (snapshot.root / "docs/readme.md").read_bytes())
        provider.release_materialization(snapshot)
        self.assertFalse(snapshot.root.exists())

    def test_unsafe_portable_paths_are_rejected(self):
        for path in [".git/config", "CON", "a:b", "folder/../bad", "trailing.", "a//b", "a/./b", "a\\b", "LPT1.txt"]:
            with self.subTest(path=path):
                provider = self.provider([("root/" + path, b"x")])
                with self.assertRaises(adapter.ResolutionError):
                    provider.materialize(REPO_ID, "main")
                self.assertEqual([], provider._tempdirs)

    def test_duplicate_and_portable_alias_paths_are_rejected(self):
        for left, right in [("same", "same"), ("Readme", "README"), ("e\u0301", "\u00e9"), ("d/a", "D/b"), ("a", "a/b")]:
            with self.subTest(left=left, right=right):
                provider = self.provider([("root/" + left, b"x"), ("root/" + right, b"y")])
                with self.assertRaises(adapter.ResolutionError):
                    provider.materialize(REPO_ID, "main")
                self.assertEqual([], provider._tempdirs)

    def test_archive_entry_count_is_bounded(self):
        provider = self.provider([("root/a", b"a"), ("root/b", b"b")])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_TREE_ENTRIES", 1):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_individual_uncompressed_file_is_bounded(self):
        provider = self.provider([("root/a", b"x" * 32)])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_BLOB_BYTES", 8):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_total_uncompressed_size_is_bounded(self):
        provider = self.provider([("root/a", b"12345"), ("root/b", b"12345")])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_TOTAL_BLOB_BYTES", 9):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_path_depth_is_bounded(self):
        provider = self.provider([("root/a/b/c", b"x")])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_PATH_DEPTH", 2):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_implicit_directories_count_toward_tree_budget(self):
        provider = self.provider([("root/a/b/c", b"x")])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_TREE_ENTRIES", 2):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_implicit_namespace_expansion_is_bounded(self):
        # Raw relative path is five bytes, but ancestor names total nine.
        provider = self.provider([("root/a/b/c", b"x")])
        with mock.patch.object(adapter, "MAX_SNAPSHOT_EXPANDED_PATH_BYTES", 8):
            with self.assertRaises(adapter.ResolutionError):
                provider.materialize(REPO_ID, "main")

    def test_symlink_and_ambiguous_roots_are_rejected(self):
        provider = self.provider([("one/a", b"x"), ("two/b", b"y")])
        with self.assertRaises(adapter.ResolutionError):
            provider.materialize(REPO_ID, "main")
        entry = zipfile.ZipInfo("root/link")
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        provider = self.provider([(entry, b"target")])
        with self.assertRaises(adapter.ResolutionError):
            provider.materialize(REPO_ID, "main")

    def test_network_archive_read_is_bounded_without_content_length(self):
        provider = adapter.GitHubApiProvider(token="synthetic-unused")
        self.addCleanup(provider.close)
        response = BytesIO(b"x" * 32)
        with mock.patch.object(adapter, "MAX_FETCH_OBJECT_BYTES", 8), mock.patch.object(adapter.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(adapter.ResolutionError):
                provider._request_bytes("/synthetic")
        self.assertTrue(response.closed)

    def test_network_json_read_is_bounded(self):
        provider = adapter.GitHubApiProvider(token="synthetic-unused")
        self.addCleanup(provider.close)
        response = BytesIO(b'{"x":"' + b"a" * 64 + b'"}')
        with mock.patch.object(adapter, "MAX_TEXT_BLOB_BYTES", 8), mock.patch.object(adapter.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(adapter.ResolutionError):
                provider._request("GET", "/synthetic")

    def test_rest_writes_require_explicit_repository_allowlist(self):
        provider = self.provider()
        provider._request = mock.Mock(return_value={"commit": {"sha": HEAD}})
        with self.assertRaises(adapter.GuardRejected):
            provider.update_text(REPO_ID, "main", "state.txt", "x", BLOB, "synthetic")
        provider._repo.assert_not_called()
        provider._request.assert_not_called()

    def test_explicit_allowlist_preserves_legacy_blob_cas_only(self):
        provider = self.provider(writable_repository_ids=(REPO_ID,))
        provider._request = mock.Mock(return_value={"commit": {"sha": HEAD}})
        self.assertEqual(HEAD, provider.update_text(REPO_ID, "main", "state.txt", "x", BLOB, "synthetic"))
        provider._request.reset_mock()
        with self.assertRaises(adapter.CasConflict):
            provider.update_text(REPO_ID, "main", "state.txt", "x", BLOB, "synthetic", expected_ref_sha=HEAD)
        provider._request.assert_not_called()

    def test_allowlist_does_not_authorize_another_repository(self):
        provider = self.provider(writable_repository_ids=(REPO_ID + 1,))
        with self.assertRaises(adapter.GuardRejected):
            provider.update_text(REPO_ID, "main", "state.txt", "x", BLOB, "synthetic")
        provider._repo.assert_not_called()

    def test_allowlist_rejects_boolean_and_nonpositive_identities(self):
        for value in (True, False, 0, -1, "1"):
            with self.subTest(value=value), self.assertRaises(adapter.ResolutionError):
                adapter.GitHubApiProvider(token="synthetic-unused", writable_repository_ids=(value,))


if __name__ == "__main__":
    unittest.main()
