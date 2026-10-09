import hashlib
import json
from pathlib import Path
import unittest
from tests.host_replay_compatibility import assert_v3_replay, HISTORICAL_SOURCE_ARCHIVES, historical_source_path


class HostReplayCompatibilityTests(unittest.TestCase):
    def test_only_documented_version_difference_is_admitted(self):
        old = {"surface_version": "v3", "ok": False, "operation": "read_learning_context",
               "error": {"code": "guard_rejected", "retryable": False}}
        new = dict(old, surface_version="v4")
        assert_v3_replay(self, old, new)
        for mutation in ({"surface_version": "v3"}, {"surface_version": "v5"},
                         {"operation": "reset_intake_preference"}, {"ok": True},
                         {"error": {"code": "cas_conflict", "retryable": False}},
                         {"error": {"code": "guard_rejected", "retryable": 0}},
                         {"extra": "unreviewed"}):
            bad = dict(new, **mutation)
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                assert_v3_replay(self, old, bad)
        with self.assertRaises(AssertionError):
            assert_v3_replay(self, [old], [])
        with self.assertRaises(AssertionError):
            assert_v3_replay(self, {"surface_version": "v3", "operation": "set_intake_preference"},
                             {"surface_version": "v4", "operation": "set_intake_preference"})

    def test_archive_mapping_is_explicit_and_preserves_original_hash(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual({"tests/test_correction_propagation.py"}, set(HISTORICAL_SOURCE_ARCHIVES))
        manifest = json.loads((root / "tests/fixtures/core34-withdrawn-support/prospective-manifest.json").read_text(encoding="utf-8"))
        original = "tests/test_correction_propagation.py"
        self.assertEqual(manifest["historical_files"][original],
                         hashlib.sha256(historical_source_path(root, original).read_bytes()).hexdigest())
        self.assertEqual(root / "missing-file.py", historical_source_path(root, "missing-file.py"))

    def test_nested_payload_cannot_masquerade_as_version_envelope(self):
        old = {"surface_version": "v3", "operation": "read_learning_context", "ok": True, "result": {}}
        new = dict(old, surface_version="v4")
        with self.assertRaises(AssertionError):
            assert_v3_replay(self, {"payload": old}, {"payload": new})
        nested_old = dict(old, result={"payload": {"surface_version": "v3", "operation": "read_learning_context"}})
        nested_new = dict(new, result={"payload": {"surface_version": "v4", "operation": "read_learning_context"}})
        with self.assertRaises(AssertionError):
            assert_v3_replay(self, nested_old, nested_new)
        with self.assertRaises(AssertionError):
            assert_v3_replay(self, {"surface_version": "v3", "operation": "read_learning_context"},
                             {"surface_version": "v4", "operation": "read_learning_context"})
        assert_v3_replay(self, {"cases": [{"coverage": {"read_result": old}}]},
                         {"cases": [{"coverage": {"read_result": new}}]})
