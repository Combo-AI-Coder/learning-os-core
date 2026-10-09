"""Synthetic cold-resume replay; never samples a consumer or accesses a learner."""
import copy
import hashlib
import json
from pathlib import Path

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_broker import RuntimeCapabilityPolicy
from tests.correction_propagation_fixture import CorrectionJourney, READ_PATH, ROOT
from tests.test_runtime_broker import (
    CHECKPOINT_PATH, CHECKPOINT_BLOB, checkpoint_progress, RUNTIME_PATH, locator,
)

FIXTURES = ROOT / "tests/fixtures/core34-cold-resume"


class ColdJourney(CorrectionJourney):
    def open(self, *, writable=False):
        host = ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(),
            branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(
                readable_roots=("learner/knowledge", "evidence", "topics/synthetic/subtopics/unit"),
                writable_roots=(READ_PATH, "evidence", CHECKPOINT_PATH) if writable else ()),
            write_admission=self.gate, expected_generation=3)
        self.hosts.append(host)
        return host

    def __init__(self, include_report=True):
        super().__init__(include_report=include_report)
        self.provider.docs[CHECKPOINT_PATH] = checkpoint_progress()
        self.provider.blobs[CHECKPOINT_PATH] = CHECKPOINT_BLOB
        self.provider.snapshot_extra_paths.add(CHECKPOINT_PATH)
        with self.open(writable=True) as host:
            token = self.invoke(host, "read_learning_context",
                                required_paths=[CHECKPOINT_PATH])["documents"][0]["version_token"]
            self.invoke(host, "save_learning_checkpoint", checkpoint={
                "milestone": ["next-step"],
                "return_point": {
                    "kind": "teaching_thread",
                    "focus": "Token identity and contextual representations",
                    "activity": "Compare two sentence prefixes ending in the same vocabulary token, predict how plausible next words can differ, and explain what information distinguishes the contexts.",
                    "scope": "Conceptual application; no formal derivation is the current target.",
                }, "ready_next": [],
            }, expected_version_token=token)
        self.before = copy.deepcopy(self.provider.docs)
        self.before_blobs = copy.deepcopy(self.provider.blobs)

    def cold_recover(self):
        with self.open() as host:
            discovery = self.invoke(host, "discover_learning_evidence")
            paths = [CHECKPOINT_PATH]
            paths += [x["path"] for x in discovery["knowledge_owners"] if x["state"] == "present"]
            paths += [x["path"] for x in discovery["evidence"]]
            docs = self.invoke(host, "read_learning_context", required_paths=paths)["documents"]
        if self.before != self.provider.docs:
            raise AssertionError("Recovery changed synthetic state")
        return docs


def packet(journey, *, protocols=None):
    return {
        "request": "Continue where we left off.",
        "product_protocols": copy.deepcopy(protocols) if protocols is not None else {
            p: (ROOT / p).read_text(encoding="utf-8") for p in (
                "protocol/teaching-decision.md", "protocol/evidence-integration.md",
                "protocol/persistence-policy.md")},
        "durable_context": journey.cold_recover(),
        "available_host_operation": {
            "operation": "reconcile_knowledge",
            "arguments": {"content": "Complete existing-schema Knowledge YAML text",
                          "expected_version_token": "Current Knowledge version token"}},
        "output_format": {
            "reply": "The next learner-facing response",
            "basis": "A short checkable factual basis, no hidden reasoning",
            "host_requests": "Optional host request objects, or []"},
    }


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def replay():
    """Reproduce saved first requests without writing any frozen artifact.

    The clock is frozen to the timestamp already in each original packet. This
    is retrospective reproduction, not a prospectively fixed sampling clock.
    """
    import datetime
    from unittest import mock

    rows = []
    successor = None
    real_clock = datetime.datetime
    for name, report in [('alpha', True), ('beta', False)]:
        original = read(f'{name}-packet.json')
        response = read(f'{name}-response.json')
        stamp = yaml.safe_load(original['durable_context'][0]['content'])['updated_at']
        instant = real_clock.fromisoformat(stamp.replace('Z', '+00:00'))

        class FrozenClock(real_clock):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        with mock.patch('scripts.runtime_broker.datetime_module.datetime', FrozenClock):
            with ColdJourney(report) as journey:
                actual = packet(journey, protocols=original['product_protocols'])
                if actual != original:
                    raise AssertionError('Current recipe differs from frozen input')
                before = copy.deepcopy(journey.provider.docs)
                negatives = []
                for request in response['host_requests']:
                    with journey.open() as denied_host:
                        denial = denied_host.invoke(request)
                    if denial['ok'] or journey.provider.docs != before:
                        raise AssertionError('Read-only request mutated synthetic state')
                    negatives.append(denial)
                results = [journey.apply(request) for request in response['host_requests']]
                changed = sorted(p for p in set(before) | set(journey.provider.docs)
                                 if before.get(p) != journey.provider.docs.get(p))
                if any(before[p] != journey.provider.docs[p] for p in before
                       if p.startswith('evidence/') or p == CHECKPOINT_PATH):
                    raise AssertionError('Original Evidence or checkpoint changed')
                journey.before = copy.deepcopy(journey.provider.docs)
                after = packet(journey, protocols=original['product_protocols'])
                rows.append({'case': name, 'exact_packet_reproduced': True,
                    'response_sha256': hashlib.sha256((FIXTURES / f'{name}-response.json').read_bytes()).hexdigest(),
                    'readonly_negative_results': negatives,
                    'unchanged_request_results': results, 'changed_paths': changed,
                    'original_evidence_bytes_unchanged': True,
                    'post_context': after['durable_context']})
                if name == 'alpha':
                    successor = after
    return rows, successor


def export_replay(destination):
    """Write derived copies to a new directory only; leave frozen inputs alone."""
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Replay output must be outside the Core snapshot')
    rows, successor = replay()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'mechanical-replay.json').write_bytes(encoded(rows))
    (destination / 'successor-packet.json').write_bytes(encoded(successor))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path,
                        help='New, nonexistent directory outside this Core snapshot')
    export_replay(parser.parse_args().output)
