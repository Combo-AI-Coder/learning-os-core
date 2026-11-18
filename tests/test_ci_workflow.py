"""Structural CI workflow contract tests."""
import copy
import re
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"@[0-9a-f]{40}\b")


def check_workflow(document):
    # BaseLoader keeps YAML's `on` key as a string, unlike YAML 1.1 booleans.
    expected_on = {"push": {"branches": ["main"], "tags": ["**"]}, "pull_request": ""}
    if document.get("on") != expected_on:
        raise ValueError("CI must retain main/all-tag pushes and default PR events")
    if document.get("permissions") != {"contents": "read"}:
        raise ValueError("CI permissions must remain read-only")
    concurrency = document.get("concurrency")
    if isinstance(concurrency, dict) and concurrency.get("cancel-in-progress") == "true":
        raise ValueError("CI concurrency must not cancel in-progress main/PR checks")

    jobs = document.get("jobs") or {}
    if set(jobs) != {"validate", "validate-windows"}:
        raise ValueError("CI must define both Linux and Windows validation jobs")
    if jobs["validate"].get("runs-on") != "ubuntu-latest":
        raise ValueError("Linux validation job must target ubuntu-latest")
    if jobs["validate-windows"].get("runs-on") != "windows-latest":
        raise ValueError("Windows validation job must target windows-latest")

    for name, job in jobs.items():
        if job.get("if") == "false":
            raise ValueError(f"Job {name} cannot be disabled")
        timeout = int(job.get("timeout-minutes", "0"))
        if not 1 <= timeout <= 90:
            raise ValueError(f"Job {name} timeout-minutes must remain bounded (1..90)")
        steps = job.get("steps") or []
        runs = "\n".join(s.get("run", "") for s in steps)
        for required in ("scripts/validate_learning_os.py . --core", "unittest discover -s tests"):
            if required not in runs:
                raise ValueError(f"Job {name} must run {required}")
        for step in steps:
            if step.get("continue-on-error") == "true":
                raise ValueError(f"Job {name} cannot use continue-on-error")
            uses = step.get("uses")
            if uses and not HEX40.search(uses):
                raise ValueError(f"Action {uses} must be pinned to a full 40-hex commit SHA")
            if uses and uses.startswith("actions/checkout@"):
                if (step.get("with") or {}).get("persist-credentials") != "false":
                    raise ValueError("actions/checkout must set persist-credentials: false")

    linux_runs = "\n".join(s.get("run", "") for s in jobs["validate"]["steps"])
    for flag in ("--instance", "--deployment"):
        if flag not in linux_runs:
            raise ValueError(f"Linux job must exercise {flag} CLI surface")
    win_runs = "\n".join(s.get("run", "") for s in jobs["validate-windows"]["steps"])
    if "git diff --check" not in win_runs:
        raise ValueError("Windows job must run git diff --check")


class CIWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load(
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

    def test_current_workflow_satisfies_structural_contract(self):
        check_workflow(self.workflow)

    def test_event_contract(self):
        events = self.workflow["on"]
        self.assertEqual(set(events), {"push", "pull_request"})
        self.assertEqual(events["push"], {"branches": ["main"], "tags": ["**"]})
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
