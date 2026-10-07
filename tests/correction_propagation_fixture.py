"""Synthetic prose-linked correction evaluation; no production schema or reasoner.

Only the initial/stale Knowledge is fixture-authored. A fresh consumer must
propose any repair; its exact request is replayed through the existing host.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    BrokerProvider, READ_PATH, RUNTIME_PATH, knowledge_candidate, locator,
    typed_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE_ID = "evi-synthetic-answer-001"
REPORT_ID = "evi-synthetic-report-002"
PERFORMANCE_PATH = f"evidence/{PERFORMANCE_ID}.yaml"
REPORT_PATH = f"evidence/{REPORT_ID}.yaml"
RECIPE = "core34-prose-correction-v1"
REQUEST = (
    "Resume the existing synthetic Topic. The next activity compares two sentence "
    "prefixes ending in the same vocabulary token, predicts how plausible next "
    "words can differ, and explains what information distinguishes the contexts. "
    "Choose one immediate learner-facing action and give a short checkable basis. "
    "Use the supplied durable state and product protocols. If a material durable "
    "Knowledge update is justified, return its host request; otherwise return no "
    "host requests. No new learner performance has occurred in this turn."
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def performance():
    value = yaml.safe_load(typed_evidence(evidence_id=PERFORMANCE_ID))
    value["observed_at"] = "2026-10-06T09:00:00Z"
    value["observation"] = {
        "kind": "task_response",
        "summary": "On one sentence-pair task, the learner correctly named token ID and contextual representation and explained that the vocabulary ID stays fixed while the contextual representation can vary. The visible response contained no mention of outside assistance.",
    }
    value["targets"].append(dict(value["targets"][0], capability="label_recall"))
    value["context"] = {"topic": "synthetic", "subtopic": "unit"}
    value["source"] = {"round_id": "synthetic-round-101"}
    return yaml.safe_dump(value, sort_keys=False)


def report():
    value = yaml.safe_load(typed_evidence(evidence_id=REPORT_ID, direction="neutral"))
    value["observed_at"] = "2026-10-06T09:15:00Z"
    value["observation"] = {
        "kind": "learner_self_report",
        "summary": f'The learner later said about the answer recorded in {PERFORMANCE_ID}: "I recalled the two names myself, but for the explanation I looked at a hint saying the vocabulary ID is fixed and the contextual representation varies with context. I have not attempted another answer since then."',
    }
    value["interpretation"].update(diagnosticity="medium", novelty="medium", confidence="medium")
    value["context"] = {"topic": "synthetic", "subtopic": "unit"}
    value["source"] = {"round_id": "synthetic-round-102"}
    return yaml.safe_dump(value, sort_keys=False)


def initial_knowledge():
    value = yaml.safe_load(knowledge_candidate(evidence_id=PERFORMANCE_ID))
    claims = value["concepts"]["token-identity"]["capabilities"]
    claims["explanation"].update(confidence="medium", basis_summary=(
        "One correct explanation appeared unassisted. Independent explanation is provisionally supported for the next conceptual application; this is not mastery or a derivation claim."))
    claims["label_recall"] = copy.deepcopy(claims["explanation"])
    claims["label_recall"]["basis_summary"] = "One observed correct naming of token ID and contextual representation provisionally supports recalling these two labels. No broader vocabulary claim is made."
    return yaml.safe_dump(value, sort_keys=False)


class CorrectionJourney:
    def __init__(self, *, include_report=True):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "control").mkdir()
        (root / "instance").mkdir()
        self.provider = BrokerProvider(root / "control", root / "instance")
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.gate = DeploymentWriteGate()
        self.hosts = []
        producer = self.open(writable=True)
        self.producer_id = producer._session.session_id
        self.invoke(producer, "create_evidence", content=performance())
        token = self.invoke(producer, "read_learning_context", required_paths=[READ_PATH])["documents"][0]["version_token"]
        self.invoke(producer, "reconcile_knowledge", content=initial_knowledge(), expected_version_token=token)
        producer.close()
        if include_report:
            reporter = self.open(writable=True)
            self.invoke(reporter, "create_evidence", content=report())
            reporter.close()  # Cut: report durable, Knowledge still stale.
        self.before = copy.deepcopy(self.provider.docs)
        self.before_blobs = copy.deepcopy(self.provider.blobs)

    def open(self, *, writable=False):
        host = ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=("learner/knowledge", "evidence"),
                writable_roots=(READ_PATH, "evidence") if writable else ()),
            write_admission=self.gate, expected_generation=3)
        self.hosts.append(host)
        return host

    @staticmethod
    def invoke(host, operation, **arguments):
        response = host.invoke({"operation": operation, "arguments": arguments})
        if not response["ok"]:
            raise AssertionError(response)
        return response["result"]

    def recover(self):
        consumer = self.open()
        discovery = self.invoke(consumer, "discover_learning_evidence")
        # Discovery identifies the owner. It supplies no consumer Evidence IDs.
        paths = [owner["path"] for owner in discovery["knowledge_owners"] if owner["state"] == "present"]
        bundle = self.invoke(consumer, "read_learning_context", required_paths=paths)
        expected = {owner["path"]: owner["version_token"] for owner in discovery["knowledge_owners"]}
        if any(expected[item["path"]] != item["version_token"] for item in bundle["documents"]):
            raise AssertionError("Knowledge changed between discovery and final read")
        # A final combined read binds original Evidence and Knowledge to one
        # snapshot without exporting the private Instance head to the consumer.
        all_paths = paths + [item["path"] for item in discovery["evidence"]]
        final = self.invoke(consumer, "read_learning_context", required_paths=all_paths)
        prior = {item["path"]: item["version_token"] for item in discovery["evidence"]}
        prior.update(expected)
        if any(prior[item["path"]] != item["version_token"] for item in final["documents"]):
            raise AssertionError("Selected state changed before final snapshot read")
        consumer.close()
        return final["documents"]

    def apply(self, request):
        # Evaluate the returned request, without repairing its content or token.
        return self.open(writable=True).invoke(request)

    def close(self):
        for host in self.hosts:
            host.close()
        self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def policies():
    # Reproduction retains the policy actually presented. A later accepted
    # product-policy change must not rewrite a historical model experiment.
    return json.loads((ROOT / "tests/fixtures/core34-correction/policy-snapshot.json").read_text(encoding="utf-8"))


def packet(arm="structured"):
    if arm not in {"structured", "summary", "ablated"}:
        raise ValueError("unknown arm")
    with CorrectionJourney(include_report=arm != "ablated") as journey:
        documents = journey.recover()
        assert journey.provider.docs == journey.before
    # The summary deliberately keeps every selected document value and token.
    # Each value is rendered as prose; no recommendation or relationship field
    # is invented. The prose relationship is already in the observation itself.
    if arm == "summary":
        lines = []
        for item in documents:
            lines.append(f"Document {item['path']}, version token {item['version_token']}:")
            value = yaml.safe_load(item["content"])
            def render(value, label):
                if isinstance(value, dict):
                    for key, child in value.items():
                        render(child, f"{label}.{key}" if label else key)
                else:
                    lines.append(f"The value of {label} is {json.dumps(value, ensure_ascii=False)}.")
            render(value, "")
        context = "\n".join(lines)
    else:
        context = documents
    return {
        "recipe_version": RECIPE,
        "request": REQUEST,
        "policy": policies(),
        "context": context,
        "host_interface": {"operation": "reconcile_knowledge", "arguments": {
            "content": "complete existing-schema Knowledge YAML text", "expected_version_token": "the supplied current Knowledge token"}},
        "output_contract": {"next_action": "actual learner-facing instruction", "basis": "short checkable basis without hidden reasoning", "host_requests": "zero or more justified host request objects"},
    }
