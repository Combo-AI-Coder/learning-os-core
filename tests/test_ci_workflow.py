"""Source-contract checks, not a simulator of GitHub event delivery."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
# Non-trigger workflow mapping at canonical c6ab9db07ac96aa36ec98fbb910d3a7392b93aab.
# A later intentional job change must review and update this preservation guard.
BASELINE_BODY_SHA256 = "4005091853f2848ae7160106b217f3bf80a48c57390d114ebd620ddf550b84ed"


def check_workflow(document):
    # BaseLoader keeps YAML's `on` key as a string, unlike YAML 1.1 booleans.
    expected = {"push": {"branches": ["main"], "tags": ["**"]},
                "pull_request": ""}
    if document.get("on") != expected:
        raise ValueError("CI must retain main/all-tag pushes and default PR events")
    body = {key: value for key, value in document.items() if key != "on"}
    digest = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if digest != BASELINE_BODY_SHA256:
        raise ValueError("Non-trigger CI configuration changed")


class CIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load(
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

    def test_current_workflow_preserves_non_trigger_configuration(self):
        check_workflow(self.workflow)

    def test_event_contract(self):
        events = self.workflow["on"]
        self.assertEqual(set(events), {"push", "pull_request"})
        self.assertEqual(events["push"], {"branches": ["main"], "tags": ["**"]})
        # No PR branch/path/type filters: default opened/synchronize/reopened
        # includes mergeable draft PRs; conflict/approval restrictions are GitHub's.
        self.assertEqual(events["pull_request"], "")

    def test_rejects_lost_tag_coverage_or_restored_feature_pushes(self):
        for push in ({"branches": ["main"]},
                     {"branches": ["main"], "tags": ["*"]},
                     {"branches": ["**"], "tags": ["**"]}):
            with self.subTest(push=push):
                candidate = copy.deepcopy(self.workflow)
                candidate["on"]["push"] = push
                with self.assertRaises(ValueError):
                    check_workflow(candidate)

    def test_rejects_pr_filters_or_extra_dispatch(self):
        for events in (
            {**self.workflow["on"], "pull_request": {"paths": ["scripts/**"]}},
            {**self.workflow["on"], "pull_request": {"types": ["ready_for_review"]}},
            {**self.workflow["on"], "workflow_dispatch": ""},
        ):
            with self.subTest(events=events):
                candidate = copy.deepcopy(self.workflow)
                candidate["on"] = events
                with self.assertRaises(ValueError):
                    check_workflow(candidate)

    def test_rejects_job_timeout_skip_or_cancellation_changes(self):
        candidates = []
        candidate = copy.deepcopy(self.workflow)
        candidate["jobs"]["validate-windows"]["timeout-minutes"] = "120"
        candidates.append(candidate)
        candidate = copy.deepcopy(self.workflow)
        candidate["jobs"]["validate"]["if"] = "false"
        candidates.append(candidate)
        candidate = copy.deepcopy(self.workflow)
        candidate["concurrency"] = {"group": "ci", "cancel-in-progress": "true"}
        candidates.append(candidate)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    check_workflow(candidate)


if __name__ == "__main__":
    unittest.main()
