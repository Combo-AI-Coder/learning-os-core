"""New-process read-only recovery harness for synthetic first-use evaluation.

Input contains only fresh Host setup plus current route, never producer
transcript, token, receipt or Evidence ID. Output is safe canonical context.
It performs no model inference; a separate isolated consumer evaluates this packet.
"""
from __future__ import annotations

import json
import sys

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import GitCliProvider, GitRepositoryBinding
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy


def recover(packet):
    if set(packet) != {"bindings", "locator", "topic", "unit"}:
        raise ValueError("unexpected producer/context input")
    topic, unit = packet["topic"], packet["unit"]
    prefix = f"topics/{topic}"
    progress = f"{prefix}/subtopics/{unit}/progress.yaml"
    required = [f"{prefix}/goal.yaml", f"{prefix}/plan.yaml",
                f"{prefix}/subtopics/{unit}/plan.yaml", progress]
    provider = GitCliProvider([GitRepositoryBinding(**binding) for binding in packet["bindings"]])
    try:
        instance = packet["locator"]["instance"]
        before = provider.resolve_ref(instance["repository_id"], instance["canonical_ref"])
        with ReferenceLearningHost.open(provider=provider, locator_source=packet["locator"],
            branch_runtime_path=f"{prefix}/coordination/branches/main/runtime.yaml",
            policy=RuntimeCapabilityPolicy(readable_roots=(prefix, "evidence")),
            expected_generation=None, write_admission=DeploymentWriteGate()) as host:
            context = host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": required}})
            if not context["ok"]:
                raise RuntimeError("fresh context read failed")
            evidence = host.invoke({"operation": "discover_learning_evidence", "arguments": {}})
            token = next(item["version_token"] for item in context["result"]["documents"] if item["path"] == progress)
            rejected = host.invoke({"operation": "save_learning_checkpoint", "arguments": {
                "expected_version_token": token,
                "checkpoint": {"milestone": ["tokens-context"], "return_point": {"focus": "unauthorized fresh write"},
                               "ready_next": ["unauthorized replacement"]}}})
            after = provider.resolve_ref(instance["repository_id"], instance["canonical_ref"])
            if rejected["ok"] or before != after:
                raise RuntimeError("fresh read-only write boundary failed")
            return {"input_fields": sorted(packet), "context": context["result"],
                    "evidence": evidence, "fresh_write_rejected": True,
                    "canonical_head_unchanged_by_consumer": True}
    finally:
        provider.close()


if __name__ == "__main__":
    print(json.dumps(recover(json.load(sys.stdin)), ensure_ascii=False, sort_keys=True))
