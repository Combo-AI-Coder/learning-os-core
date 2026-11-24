"""Synthetic binary Git tree traversal and batch-process lifecycle regressions."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from scripts.runtime_adapter import GitCliProvider, GitRepositoryBinding, ResolutionError


def tree_id(raw):
    return hashlib.sha1(b"tree " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def entry(mode=b"100644", name=b"file.txt", sha="1" * 40):
    return mode + b" " + name + b"\0" + bytes.fromhex(sha)


def response(raw, *, sha=None, kind="tree"):
    return f"{sha or tree_id(raw)} {kind} {len(raw)}\n".encode("ascii") + raw + b"\n"


class RecordingInput(io.BytesIO):
    def close(self):
        if not self.closed:
            self.requests = self.getvalue().decode("ascii").splitlines()
        super().close()


class FakeBatchProcess:
    def __init__(self, payload, *, block=None):
        self.stdin = RecordingInput()
        self.stdout = io.BytesIO(payload)
        self.returncode = None
        self.terminated = self.waited = False
        self.unblocked = threading.Event()
        if block is not None:
            setattr(self.stdout, block, self._blocked_read)

    def _blocked_read(self, size):
        self.unblocked.wait(5)
        return b""

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.unblocked.set()

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class BatchTreeWalkTests(unittest.TestCase):
    def setUp(self):
        self.provider = GitCliProvider([GitRepositoryBinding(1, "synthetic-batch-tree-repo")])
        self.addCleanup(self.provider.close)
        self.repo = Path("synthetic-batch-tree-repo")

    def assert_reaped(self, process):
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.waited)
        self.assertIsNotNone(process.poll())

    def reject(self, payload, root, message, **kwargs):
        process = FakeBatchProcess(payload)
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", return_value=process) as spawn:
            with self.assertRaisesRegex(ResolutionError, message):
                self.provider._tree_objects_from_root(self.repo, root, **kwargs)
        spawn.assert_called_once()
        self.assertTrue(process.terminated)
        self.assert_reaped(process)
        return process

    def test_nested_regular_modes_match_ls_tree_and_each_call_starts_one_batch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)

        def git(*args, raw=None):
            return subprocess.run(["git", *args], cwd=self.repo, input=raw,
                                  capture_output=True, check=True,
                                  env=self.provider._env()).stdout

        git("init", "-q", "--bare")

        def store(kind, raw):
            return git("hash-object", "-w", "-t", kind, "--stdin", raw=raw).decode("ascii").strip()

        blob = store("blob", b"synthetic\n")
        leaf = store("tree", entry(b"100755", b"run.sh", blob))
        child = store("tree", entry(name=b"plain.txt", sha=blob) + entry(b"40000", b"sub", leaf))
        root = store("tree", entry(b"40000", b"a", child) + entry(b"40000", b"z", child))
        expected = [self.provider._decode_tree_record(record)
                    for record in git("ls-tree", "-rz", root).split(b"\0") if record]
        expected_objects = [self.provider._decode_tree_object_record(record)
                            for record in git("ls-tree", "-rtz", root).split(b"\0") if record]
        real_popen, processes = subprocess.Popen, []

        def spawn(*args, **kwargs):
            self.assertEqual((["git", "cat-file", "--batch"],), args)
            self.assertEqual("1", kwargs["env"]["GIT_NO_LAZY_FETCH"])
            self.assertEqual(self.repo, kwargs["cwd"])
            self.assertEqual(subprocess.PIPE, kwargs["stdin"])
            self.assertEqual(subprocess.PIPE, kwargs["stdout"])
            self.assertEqual(subprocess.DEVNULL, kwargs["stderr"])
            for key, value in self.provider._fetch_process_kwargs().items():
                self.assertEqual(value, kwargs[key])
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with mock.patch("scripts.runtime_adapter.subprocess.Popen", side_effect=spawn), \
                mock.patch.object(self.provider, "_object_header", side_effect=AssertionError("per-tree header")), \
                mock.patch.object(self.provider, "_git_bytes", side_effect=AssertionError("per-tree command")):
            for _ in range(2):
                objects = self.provider._tree_objects_from_root(self.repo, root)
                self.assertEqual(sorted(expected_objects), sorted(objects))
                self.assertEqual(sorted(expected), sorted(item[:3] for item in objects if item[3] == "blob"))
                self.assertEqual(["a", "z", "z/plain.txt", "z/sub", "z/sub/run.sh",
                                  "a/plain.txt", "a/sub", "a/sub/run.sh"], [item[0] for item in objects])
                self.assertEqual(objects, self.provider._verify_regular_tree_objects(objects))
        self.assertEqual(2, len(processes), "a fresh invocation must start a fresh process")
        for process in processes:
            self.assertTrue(process.stdin.closed and process.stdout.closed)
            self.assertEqual(0, process.poll())

    def test_non_tree_object_is_rejected(self):
        raw = entry()
        for kind in ("blob", "commit", "tag"):
            with self.subTest(kind=kind):
                self.reject(response(raw, kind=kind), tree_id(raw), "identity is not a tree")

    def test_invalid_headers_are_rejected(self):
        raw, root = entry(), tree_id(entry())
        for header in (b"", b"missing\n", f"{root} missing\n".encode(),
                       f"{'0' * 40} tree {len(raw)}\n".encode(),
                       f"{root} tree -1\n".encode(), f"{root} tree 1.0\n".encode(),
                       f"{root} tree {len(raw)} extra\n".encode(),
                       f"{root} tree {len(raw)}".encode(), b"\xff tree 1\n"):
            with self.subTest(header=header):
                self.reject(header, root, "Git object header is invalid")

    def test_same_size_content_tampering_is_rejected(self):
        raw = entry()
        changed = raw.replace(b"file.txt", b"evil.txt")
        self.reject(response(changed, sha=tree_id(raw)), tree_id(raw), "content identity differs")

    def test_truncated_payload_and_missing_or_extra_delimiter_are_rejected(self):
        raw = entry()
        packet = response(raw)
        for payload in (packet[:-2], packet[:-1], packet[:-1] + b"x\n"):
            with self.subTest(payload=payload):
                self.reject(payload, tree_id(raw), "content size differs")

    def test_empty_root_is_only_allowed_explicitly(self):
        self.reject(response(b""), tree_id(b""), "Git tree contains an empty directory")
        process = FakeBatchProcess(response(b""))
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", return_value=process):
            self.assertEqual([], self.provider._tree_objects_from_root(
                self.repo, tree_id(b""), allow_empty_root=True))
        self.assert_reaped(process)
        self.assertFalse(process.terminated)

    def test_non_root_empty_directory_is_rejected_even_with_root_exception(self):
        raw = entry(b"40000", b"empty", tree_id(b""))
        self.reject(response(raw) + response(b""), tree_id(raw),
                    "Git tree contains an empty directory", allow_empty_root=True)

    def test_symlink_and_gitlink_keep_non_regular_error(self):
        for mode in (b"120000", b"160000"):
            with self.subTest(mode=mode):
                raw = entry(mode)
                self.reject(response(raw), tree_id(raw), "unsupported non-regular entry")

    def test_only_canonical_modes_are_accepted(self):
        for mode in (b"040000", b"0100644", b"100664", b"100600", b"100777", b"40001", b"100648", b"+100644", b""):
            with self.subTest(mode=mode):
                raw = entry(mode)
                self.reject(response(raw), tree_id(raw), "unsupported non-regular entry")

    def test_malformed_binary_entries_are_rejected(self):
        for raw in (b"\0", b"\0" + b"x" * 20, b"100644 file.txt",
                    entry()[:-1], entry() + b"\0", b"100644\0" + b"x" * 20):
            with self.subTest(raw=raw):
                self.reject(response(raw), tree_id(raw), "undecodable entry")

    def test_unsafe_or_multi_component_names_are_rejected(self):
        for name in (b"", b"/absolute", b"a/b", b"a/", b"..", b"../escape", b".", b".git", b"a\\b", b"\xff"):
            with self.subTest(name=name):
                raw = entry(name=name)
                self.reject(response(raw), tree_id(raw), "unsafe|one path component|undecodable")

    def test_metadata_and_listing_budgets_reject_before_body_read(self):
        raw = entry()
        for budget, message in (("MAX_GIT_METADATA_OBJECT_BYTES", "metadata object exceeds"),
                                ("MAX_SNAPSHOT_TREE_LIST_BYTES", "listing exceeds")):
            with self.subTest(budget=budget), mock.patch("scripts.runtime_adapter." + budget, len(raw) - 1):
                # Deliberately omit the body: the budget error must precede EOF.
                self.reject(response(raw).split(b"\n", 1)[0] + b"\n", tree_id(raw), message)

    def test_listing_budget_counts_cached_tree_each_time_it_is_expanded(self):
        child = entry()
        root = entry(b"40000", b"a", tree_id(child)) + entry(b"40000", b"b", tree_id(child))
        with mock.patch("scripts.runtime_adapter.MAX_SNAPSHOT_TREE_LIST_BYTES", len(root) + len(child)):
            process = self.reject(response(root) + response(child), tree_id(root), "listing exceeds")
        self.assertEqual([tree_id(root), tree_id(child)], process.stdin.requests)

    def test_entry_subtree_and_path_budgets_are_preserved(self):
        raw = entry(b"40000", b"dir", "2" * 40)
        for budget in ("MAX_SNAPSHOT_TREE_ENTRIES", "MAX_SNAPSHOT_TREE_OBJECTS",
                       "MAX_SNAPSHOT_PATH_DEPTH", "MAX_SNAPSHOT_PATH_COMPONENT_BYTES",
                       "MAX_SNAPSHOT_PATH_BYTES", "MAX_SNAPSHOT_EXPANDED_PATH_BYTES"):
            with self.subTest(budget=budget), mock.patch("scripts.runtime_adapter." + budget, 0):
                self.reject(response(raw), tree_id(raw), "budget")

    def test_invalid_root_fails_before_starting_batch(self):
        with mock.patch("scripts.runtime_adapter.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ResolutionError, "Git object identity is not exact"):
                self.provider._tree_objects_from_root(self.repo, "HEAD")
        spawn.assert_not_called()

    def test_start_and_pipe_errors_remain_resolution_errors(self):
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", side_effect=OSError("synthetic")):
            with self.assertRaisesRegex(ResolutionError, "Git tree read failed"):
                self.provider._tree_objects_from_root(self.repo, tree_id(entry()))
        process = FakeBatchProcess(b"")
        with mock.patch.object(process.stdin, "write", side_effect=BrokenPipeError("synthetic")), \
                mock.patch("scripts.runtime_adapter.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(ResolutionError, "Git tree read failed"):
                self.provider._tree_objects_from_root(self.repo, tree_id(entry()))
        self.assertTrue(process.terminated)
        self.assert_reaped(process)

    def test_blocked_header_read_times_out_and_reaps_process(self):
        process = FakeBatchProcess(b"", block="readline")
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", return_value=process), \
                mock.patch("scripts.runtime_adapter.TREE_BATCH_READ_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(ResolutionError, "^Git tree read timed out$"):
                self.provider._tree_objects_from_root(self.repo, tree_id(entry()))
        self.assertTrue(process.terminated)
        self.assert_reaped(process)

    def test_blocked_body_read_times_out_and_reaps_process(self):
        raw = entry()
        process = FakeBatchProcess(response(raw), block="read")
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", return_value=process), \
                mock.patch("scripts.runtime_adapter.TREE_BATCH_READ_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(ResolutionError, "^Git tree read timed out$"):
                self.provider._tree_objects_from_root(self.repo, tree_id(raw))
        self.assertTrue(process.terminated)
        self.assert_reaped(process)

    def test_stalled_os_process_times_out_and_exits(self):
        real_popen, processes = subprocess.Popen, []

        def spawn(args, **kwargs):
            if args != ["git", "cat-file", "--batch"]:
                return real_popen(args, **kwargs)
            process = real_popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **self.provider._fetch_process_kwargs(),
            )
            processes.append(process)
            self.addCleanup(process.wait)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            return process

        start = time.monotonic()
        with mock.patch("scripts.runtime_adapter.subprocess.Popen", side_effect=spawn), \
                mock.patch("scripts.runtime_adapter.TREE_BATCH_READ_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(ResolutionError, "^Git tree read timed out$"):
                self.provider._tree_objects_from_root(self.repo, tree_id(entry()))
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(1, len(processes))
        self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
