"""Offline Core19 first-request replay. Synthetic fixed clock/provider only."""
import copy
import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from unittest import mock

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_intake_operations import TOPIC, GLOBAL, goal
from tests.test_runtime_broker import BrokerProvider, RUNTIME_PATH, locator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/core19-intake"
REAL_DATETIME = datetime.datetime


class FrozenClock(REAL_DATETIME):
    @classmethod
    def now(cls, tz=None):
        return REAL_DATETIME(2026, 10, 8, 4, 0, 0, tzinfo=datetime.timezone.utc)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sha(name):
    return hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()


class IntakeJourney:
    def __init__(self, state):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "control").mkdir(); (root / "instance").mkdir()
        self.provider = BrokerProvider(root / "control", root / "instance")
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.provider.snapshot_extra_paths.update({TOPIC, GLOBAL})
        topic = goal()
        topic["goal"]["purpose"] = state["known_context"]["purpose"]
        topic["goal"]["preferences"].update(state["topic_goal"]["preferences"])
        self.put(TOPIC, topic, state["topic_goal"]["version_token"])
        global_owner = state["learner_execution"]
        if global_owner["state"] == "present":
            self.put(GLOBAL, {"schema_version": "0.3", "document_type": "learner_execution",
                             "revision": 1, "preferences": global_owner["preferences"],
                             "weekly_budget": {"hours": 3}}, global_owner["version_token"])
        for suffix, document in (
            ("plan", {"revision": 1, "plan": {"status": "active", "description": state["known_context"]["plan"]}}),
            ("progress", {"revision": 1, "plan_revision": 1, "lifecycle": "active", "milestones": {}}),
        ):
            path = f"topics/synthetic/{suffix}.yaml"
            self.provider.snapshot_extra_paths.add(path)
            self.put(path, {"schema_version": "0.3", "document_type": "topic_" + suffix,
                            "topic": "synthetic", **document}, "c" * 40)
        self.gate = DeploymentWriteGate()
        self.host = self.open()
        self.before = dict(self.provider.docs)

    def put(self, path, value, token):
        self.provider.docs[path] = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
        self.provider.blobs[path] = token

    def open(self):
        return ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner", "topics/synthetic"),
                                           writable_roots=(TOPIC, GLOBAL)),
            write_admission=self.gate, expected_generation=3,
        )

    def apply(self, request):
        with mock.patch("scripts.runtime_broker.datetime_module.datetime", FrozenClock):
            return self.host.invoke(request)

    def recover(self):
        # A newly opened session reads durable state, not the previous response.
        with self.open() as fresh:
            result = fresh.invoke({"operation": "read_learning_context", "arguments": {
                "required_paths": [TOPIC, "topics/synthetic/plan.yaml", "topics/synthetic/progress.yaml"],
                "optional_paths": [GLOBAL]}})
        if not result["ok"]:
            raise AssertionError(result)
        return result["result"]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.host.close(); self.temporary.cleanup()


def replay():
    answers = load("consumer-response.json")["decisions"]
    cases = load("consumer-packet.json")["cases"]
    if len(answers) != len(cases) or {x["id"] for x in answers} != {x["id"] for x in cases}:
        raise AssertionError("first response case inventory mismatch")
    rows = []
    for case in cases:
        answer = next(x for x in answers if x["id"] == case["id"])
        with IntakeJourney(case["state"]) as journey:
            results = [journey.apply(request) for request in answer["host_requests"]]
            rows.append({"id": case["id"], "host_results": results,
                         "changed_paths": sorted(p for p in set(journey.before) | set(journey.provider.docs)
                                                 if journey.before.get(p) != journey.provider.docs.get(p)),
                         "durable_context": journey.recover()})
    return {"response_sha256": sha("consumer-response.json"), "cases": rows}
