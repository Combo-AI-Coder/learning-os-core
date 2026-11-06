"""Synthetic-only Instance discovery regressions; no Git or external state."""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile
import unittest
from unittest import mock

import yaml

from scripts.validate_learning_os import InstanceValidator, validate_instance


class InstanceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="instance-discovery-"))
        self.directories = [self.root]
        self.files = {}
        self.removed = {}
        self.instance = self.root / "instance"
        self.core = self.root / "core"
        self.binding = {
            "context_type": "synthetic", "core_repository_id": 11,
            "core_commit": "a" * 40, "instance_repository_id": 12,
            "topology": "noncanonical-split", "epoch": 1,
            "write_state": "not_deployed",
        }
        self.write("instance/README.md", "# Synthetic Instance\n")
        self.write("instance/config/instance.yaml", {
            "schema_version": "0.4", "document_type": "instance_config",
            "product": {"id": "learning-os"},
            "instance": {"display_timezone": "UTC"}, "nonproduction": True,
        })
        self.write("core/config/core.yaml", {
            "schema_version": "0.4", "document_type": "core_config",
            "product": {"id": "learning-os"},
            "manifest": {"supported_instance_state_schema_versions": ["0.3"]},
        })

    def mkdir(self, path):
        if path.exists():
            return
        self.mkdir(path.parent)
        path.mkdir()
        self.directories.append(path)

    def write(self, rel, value):
        path = self.root / rel
        self.mkdir(path.parent)
        data = (value if isinstance(value, bytes) else
                (value if isinstance(value, str) else yaml.safe_dump(value)).encode())
        path.write_bytes(data)
        self.files[path] = hashlib.sha256(data).hexdigest()
        return path

    def tearDown(self):
        # Delete only recorded task-owned regular files with the expected bytes.
        for path, expected in self.files.items():
            self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
        for path in reversed(self.files):
            path.unlink()
        for path in reversed(self.directories):
            self.assertTrue(stat.S_ISDIR(path.lstat().st_mode))
            path.rmdir()

    def validator(self):
        return InstanceValidator(self.instance, self.core, self.binding)

    def findings(self):
        return validate_instance(self.instance, self.core, self.binding)

    def add_evidence(self):
        return self.write("instance/evidence/hidden.yaml", {
            "schema_version": "0.3", "document_type": "evidence", "id": "hidden",
            "observed_at": "2026-11-06T00:00:00+00:00", "observation": "synthetic",
            "interpretation": {"direction": "support", "diagnosticity": "low",
                               "novelty": "low", "confidence": "low"},
            "targets": [], "deployment_epoch": 1,
        })

    def scan_fault(self, target, kind, error=PermissionError):
        original = os.scandir
        failure = error(errno.EACCES, "synthetic private exception text")

        class Entry:
            def __init__(self, entry):
                self.entry = entry
                self.name = entry.name

            def is_dir(self, *, follow_symlinks=True):
                if self.name == "blocked":
                    raise failure
                return self.entry.is_dir(follow_symlinks=follow_symlinks)

        class Entries:
            def __enter__(self):
                with original(target) as stream:
                    self.entries = sorted(stream, key=lambda entry: entry.name)
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                for index, entry in enumerate(self.entries):
                    if kind == "iterate" and index == 1:
                        raise failure
                    yield Entry(entry) if kind == "classify" else entry

        def scandir(path):
            if Path(path) != target:
                return original(path)
            if kind == "open":
                raise failure
            return Entries()

        return mock.patch("os.scandir", scandir)

    def assert_enumeration(self, findings, path):
        errors = [f for f in findings if f.code == "instance.enumeration"]
        self.assertEqual([(f.severity, f.code, f.path, f.message) for f in errors], [
            ("error", "instance.enumeration", path,
             "Instance documents could not be enumerated (fail closed)"),
        ])
        self.assertNotIn("synthetic private exception text", repr(errors))

    def test_minimal_and_absent_optional_trees_remain_clean(self):
        self.assertEqual(self.findings(), [])

    def test_optional_open_failure_cannot_hide_invalid_evidence(self):
        hidden = self.add_evidence()
        sibling = self.write("instance/learner/execution.yaml", {
            "schema_version": "0.3", "document_type": "learner_execution",
            "revision": 1, "deployment_epoch": 2,
        })
        control = self.findings()
        self.assertEqual([f.code for f in control], ["instance.authority_key"] * 2)
        original = Path.read_text
        opened = []

        def read(path, *args, **kwargs):
            opened.append(path)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read), self.scan_fault(hidden.parent, "open"):
            findings = self.findings()
        self.assert_enumeration(findings, "evidence")
        self.assertNotIn(hidden, opened)
        self.assertIn(sibling, opened)
        self.assertIn(self.instance / "config/instance.yaml", opened)
        self.assertEqual([f.path for f in findings if f.code == "instance.authority_key"],
                         ["learner/execution.yaml:deployment_epoch"])
        self.assertNotIn("instance.config_missing", {f.code for f in findings})

    def test_other_scan_oserrors_are_not_absence(self):
        hidden = self.add_evidence()
        for error in (FileNotFoundError, NotADirectoryError):
            with self.subTest(error=error.__name__), self.scan_fault(hidden.parent, "open", error):
                self.assert_enumeration(self.findings(), "evidence")

    def test_iterator_error_retains_yielded_paths_and_siblings(self):
        first = self.write("instance/evidence/a.yaml", "{}")
        self.write("instance/evidence/b.yaml", "{}")
        validator = self.validator()
        with self.scan_fault(first.parent, "iterate"):
            paths = validator.discover_documents("*.yaml")
        self.assert_enumeration(validator.findings, "evidence")
        self.assertIn(first, paths)
        self.assertIn(self.instance / "config/instance.yaml", paths)

    def test_entry_classification_error_is_visible_with_siblings(self):
        self.write("instance/evidence/blocked/hidden.yaml", "{}")
        visible = self.write("instance/evidence/visible.yaml", "{}")
        validator = self.validator()
        with self.scan_fault(visible.parent, "classify"):
            paths = validator.discover_documents("*.yaml")
        self.assert_enumeration(validator.findings, "evidence/blocked")
        self.assertIn(visible, paths)
        self.assertNotIn(visible.parent / "blocked/hidden.yaml", paths)

    def test_markdown_only_subtree_failure_is_not_clean(self):
        self.write("instance/evidence/hidden.md", "synthetic text")
        validator = self.validator()
        with self.scan_fault(self.instance / "evidence", "open"):
            validator.check_documents()
        self.assert_enumeration(validator.findings, "evidence")

    def test_yaml_exclusions_and_markdown_inclusion_remain_distinct(self):
        token = "ghp_" + "x" * 20  # Deliberately synthetic structural marker.
        for top in (".git", ".github"):
            self.write(f"instance/{top}/excluded.yaml", "[")
            self.write(f"instance/{top}/included.md", token)
        findings = self.findings()
        self.assertEqual({f.code for f in findings}, {"instance.credential_value"})
        self.assertEqual({f.path for f in findings}, {".git/included.md", ".github/included.md"})

    def test_matching_file_directory_hidden_and_case_names_match_rglob(self):
        for name in ("a.yaml", "b.YAML", ".hidden.yaml", "c.md", "d.MD", "e.yml", "tail.yamlx"):
            self.write("instance/evidence/" + name, "{}")
        self.write("instance/evidence/directory.yaml/inside.md", "text")
        validator = self.validator()
        for pattern in ("*.yaml", "*.md"):
            with self.subTest(pattern=pattern):
                self.assertEqual(validator.discover_documents(pattern),
                                 sorted(self.instance.rglob(pattern)))
        self.assertEqual(validator.findings, [])

    def test_windows_flavor_case_matching_is_preserved_as_a_model(self):
        upper = self.write("instance/evidence/UPPER.YAML", "{}")
        validator = self.validator()
        with mock.patch("scripts.validate_learning_os.Path", PureWindowsPath):
            self.assertIn(upper, validator.discover_documents("*.yaml"))

    def test_symlink_entries_match_without_recursive_descent_as_a_model(self):
        calls = []

        class Entry:
            def __init__(self, name):
                self.name = name

            def is_dir(self, *, follow_symlinks=True):
                calls.append((self.name, follow_symlinks))
                return follow_symlinks

        stream = mock.MagicMock()
        stream.__enter__.return_value = iter([Entry("file.yaml"), Entry("directory.md"), Entry("link")])
        validator = self.validator()
        with mock.patch("os.scandir", return_value=stream) as scandir:
            self.assertEqual(validator.discover_documents("*.yaml"), [self.instance / "file.yaml"])
        self.assertEqual(scandir.call_count, 1)
        self.assertEqual(calls, [("file.yaml", False), ("directory.md", False), ("link", False)])

    def test_root_and_config_scan_top_failures_remain_visible(self):
        original = Path.iterdir
        for target in (self.instance, self.instance / "config"):
            def iterdir(path):
                if path == target:
                    raise PermissionError("synthetic scan_top failure")
                return original(path)

            with self.subTest(target=target.name), mock.patch.object(Path, "iterdir", iterdir):
                with self.assertRaises(PermissionError):
                    self.findings()

    def test_root_discovery_failure_has_fixed_error_and_config_missing(self):
        validator = self.validator()
        with self.scan_fault(self.instance, "open"):
            validator.collect()
        validator.check_instance_config()
        self.assert_enumeration(validator.findings, ".")
        self.assertIn("instance.config_missing", {f.code for f in validator.findings})

    def test_required_config_missing_is_unchanged(self):
        path = self.instance / "config/instance.yaml"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.files[path])
        self.assertTrue(stat.S_ISREG(path.lstat().st_mode))
        self.removed[path] = self.files[path]
        path.unlink()
        del self.files[path]
        self.assertEqual([f.code for f in self.findings()], ["instance.config_missing"])

    def test_yaml_read_error_remains_parse_error(self):
        target = self.add_evidence()
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == target:
                raise PermissionError("synthetic read failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read):
            self.assertEqual([f.code for f in self.findings()], ["yaml.parse"])

    def test_markdown_read_error_remains_visible(self):
        target = self.write("instance/evidence/read.md", "text")
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == target:
                raise PermissionError("synthetic read failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read), self.assertRaises(PermissionError):
            self.findings()

    def test_matching_directory_still_reaches_yaml_read_error(self):
        self.mkdir(self.instance / "evidence/directory.yaml")
        self.assertEqual([f.code for f in self.findings()], ["yaml.parse"])
