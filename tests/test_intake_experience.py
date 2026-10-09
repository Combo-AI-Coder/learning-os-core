"""Reproduce frozen synthetic evidence, not automatically grade teaching quality."""
import copy
import hashlib
import json
import unittest
from unittest import mock

from tests.intake_controls_fixture import FIXTURES, load, sha, replay


class IntakeExperienceTests(unittest.TestCase):
    def test_prospective_input_hashes_and_case_inventory(self):
        manifest = load("prospective-manifest.json")
        self.assertEqual(manifest["packet_sha256"], sha("consumer-packet.json"))
        self.assertEqual(manifest["policy_sha256"], sha("policy-snapshot.json"))
        packet = load("consumer-packet.json")
        self.assertEqual(load("policy-snapshot.json"), packet["policy_snapshot"])
        ids = [x["id"] for x in packet["cases"]]
        self.assertEqual(8, len(ids)); self.assertEqual(8, len(set(ids)))
        self.assertEqual(set(ids), set(manifest["criteria"]))
        self.assertNotIn("criteria", packet)
        self.assertNotIn("expected", packet)

    def test_first_requests_replay_exactly(self):
        self.assertEqual(load("mechanical-replay.json"), replay())
        rows = load("mechanical-replay.json")["cases"]
        self.assertEqual({f"i{i:02}" for i in range(1, 9)}, {x["id"] for x in rows})
        self.assertEqual(8, len(rows))
        # Frozen admission results are observations; they do not prove semantics.

    def test_missing_and_duplicate_response_cases_cannot_skip_replay(self):
        original = load("consumer-response.json")
        for decisions in ([], original["decisions"][:-1], [original["decisions"][0]] * 8):
            modified = dict(original, decisions=decisions)
            from tests import intake_controls_fixture as fixture
            reader = fixture.load
            with mock.patch.object(fixture, "load", side_effect=lambda name:
                                   modified if name == "consumer-response.json" else reader(name)):
                with self.assertRaises(AssertionError):
                    replay()

    def test_successor_sees_only_actual_durable_result(self):
        freeze = load("successor-freeze.json")
        self.assertEqual(freeze["packet_sha256"], sha("successor-packet.json"))
        self.assertEqual(freeze["replay_sha256"], sha("mechanical-replay.json"))
        rows = {x["id"]: x for x in load("mechanical-replay.json")["cases"]}
        cases = load("successor-packet.json")["cases"]
        self.assertEqual({"i03", "i04", "i05"}, {x["id"] for x in cases})
        self.assertEqual(3, len(cases))
        for case in cases:
            self.assertEqual(rows[case["id"]]["durable_context"], case["durable_context"])
            self.assertNotIn("host_results", case)
            self.assertNotIn("previous_response", case)
        self.assertEqual(load("policy-snapshot.json"), load("successor-packet.json")["policy_snapshot"])

    def test_frozen_model_policy_does_not_follow_future_live_docs(self):
        from pathlib import Path
        from tests.intake_controls_fixture import ROOT
        original = Path.read_text
        def changed(path, *args, **kwargs):
            content = original(path, *args, **kwargs)
            if path == ROOT / "protocol/new-topic-start.md":
                return content + "\nSynthetic future live policy.\n"
            return content
        with mock.patch.object(Path, "read_text", changed):
            self.assertEqual(load("policy-snapshot.json"), load("consumer-packet.json")["policy_snapshot"])

    def test_semantic_review_is_bound_to_the_original_artifacts(self):
        review = load("semantic-review.json")
        for name, digest in review["artifact_sha256"].items():
            self.assertEqual(digest, sha(name), name)
        self.assertIn("consumer-response.json", review["artifact_sha256"])
        self.assertIn("successor-response.json", review["artifact_sha256"])
        self.assertIn("prospective-manifest.json", review["artifact_sha256"])
        expected = {("primary", f"i{i:02}") for i in range(1, 9)} | {
            ("successor", case) for case in ("i03", "i04", "i05")}
        self.assertEqual(11, len(review["judgments"]))
        self.assertEqual(expected, {(row["consumer"], row["id"]) for row in review["judgments"]})
        for row in review["judgments"]:
            self.assertIsInstance(row["decision"], str)
            self.assertTrue(row["reason"])
        # A hash-bound human/model judgment is not an automated semantic oracle.


if __name__ == "__main__":
    unittest.main()
