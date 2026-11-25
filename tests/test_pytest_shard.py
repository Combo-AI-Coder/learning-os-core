"""Tests for deterministic pytest sharding and xdist execution."""

from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from scripts import pytest_shard


ROOT = Path(__file__).resolve().parents[1]


class PytestShardTests(unittest.TestCase):
    def run_pytest(self, *args):
        return subprocess.run(
            [
                sys.executable, "-X", "utf8", "-m", "pytest",
                "-p", "no:cacheprovider", "-p", "scripts.pytest_shard", *args,
            ],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

    def test_partition_is_deterministic_disjoint_and_complete(self):
        all_items = [
            SimpleNamespace(nodeid=f"test_sample.py::test_{i:02d}") for i in range(11)
        ]
        for order in (all_items, all_items[::-1]):
            covered = []
            for index in range(1, 5):
                with self.subTest(index=index):
                    options = {"--shard-index": index, "--shard-count": 4}
                    config = SimpleNamespace(getoption=options.__getitem__, hook=Mock())
                    expected = all_items[index - 1::4]
                    items = order.copy()
                    pytest_shard.pytest_collection_modifyitems(config, items)
                    self.assertEqual(items, expected)
                    config.hook.pytest_deselected.assert_called_once_with(
                        items=[item for item in all_items if item not in expected]
                    )
                    covered.extend(item.nodeid for item in items)
            self.assertCountEqual(covered, [item.nodeid for item in all_items])

    def test_invalid_arguments_fail_closed(self):
        for args in (
            ("--shard-index=0", "--shard-count=4"),
            ("--shard-index=5", "--shard-count=4"),
            ("--shard-index=1", "--shard-count=0"),
            ("--shard-index=one", "--shard-count=4"),
            ("--shard-count=4",),
            ("--shard-index=1",),
        ):
            with self.subTest(args=args):
                result = self.run_pytest(*args, "--collect-only", __file__)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertIn("--shard-index", result.stderr)

    def test_xdist_runs_each_selected_node_once(self):
        names = [f"test_{i:02d}" for i in range(11)]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "test_sample.py"
            report = Path(directory) / "report.xml"
            fixture.write_text(
                "\n".join(f"def {name}():\n    pass\n" for name in reversed(names)),
                encoding="utf-8",
            )
            result = self.run_pytest(
                "--shard-index=2", "--shard-count=4", "-n", "2", "--dist", "worksteal",
                f"--basetemp={Path(directory) / 'workers'}", f"--junitxml={report}", str(fixture),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cases = ET.parse(report).findall(".//testcase")
            self.assertCountEqual([case.attrib["name"] for case in cases], names[1::4])


if __name__ == "__main__":
    unittest.main()
