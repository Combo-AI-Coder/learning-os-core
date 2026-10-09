"""Explicit test-only v3 receipt compatibility with the additive v4 host surface.

Never rewrite frozen receipts or relabel a live host response. Compare every
other field recursively; the only allowed difference is the documented envelope
version on one of the five pre-existing operations. Not a runtime adapter.
"""
from pathlib import Path

LEGACY_OPERATIONS = frozenset({"read_learning_context", "discover_learning_evidence",
                              "save_learning_checkpoint", "create_evidence", "reconcile_knowledge"})
HISTORICAL_SOURCE_ARCHIVES = {
    "tests/test_correction_propagation.py":
        "tests/fixtures/core34-source-history/test_correction_propagation-v3.py.txt",
}


def historical_source_path(root: Path, original: str) -> Path:
    return root / HISTORICAL_SOURCE_ARCHIVES.get(original, original)


def assert_v3_replay(testcase, historical, current, _path=()):
    if isinstance(historical, dict):
        testcase.assertIsInstance(current, dict)
        testcase.assertEqual(set(historical), set(current))
        allowed_position = (
            not _path
            or (len(_path) == 1 and isinstance(_path[0], int))
            or (len(_path) == 4 and _path[0] == "cases" and isinstance(_path[1], int)
                and _path[2:] == ("coverage", "read_result"))
        )
        envelope = "surface_version" in historical and allowed_position
        if envelope:
            testcase.assertIs(type(historical.get("ok")), bool)
            terminal = "result" if historical["ok"] else "error"
            testcase.assertEqual({"surface_version", "ok", "operation", terminal}, set(historical))
            testcase.assertIsInstance(historical[terminal], dict)
            if terminal == "error":
                testcase.assertEqual({"code", "retryable"}, set(historical[terminal]))
                testcase.assertIsInstance(historical[terminal]["code"], str)
                testcase.assertIs(type(historical[terminal]["retryable"]), bool)
            testcase.assertEqual("v3", historical["surface_version"])
            testcase.assertEqual("v4", current["surface_version"])
            testcase.assertIn(historical.get("operation"), LEGACY_OPERATIONS)
            testcase.assertEqual(historical["operation"], current.get("operation"))
        for key in historical:
            if key != "surface_version" or not envelope:
                assert_v3_replay(testcase, historical[key], current[key], _path + (key,))
    elif isinstance(historical, list):
        testcase.assertIsInstance(current, list)
        testcase.assertEqual(len(historical), len(current))
        for index, (old, new) in enumerate(zip(historical, current)):
            assert_v3_replay(testcase, old, new, _path + (index,))
    else:
        testcase.assertIs(type(current), type(historical))
        testcase.assertEqual(historical, current)
