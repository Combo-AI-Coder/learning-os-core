"""Model-agnostic Learning OS ordinary-Runtime session broker.

Conversation surfaces never receive repository credentials through this layer.
The host binds a session to one validated deployment, one Instance ref, one
learning Branch generation, and a fixed path capability policy. Canonical
writes always re-check deployment, generation, and target CAS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

import yaml

from scripts.runtime_adapter import (
    CasConflict,
    DeploymentGuard,
    DeploymentResolver,
    GuardRejected,
    RepositoryProvider,
    ResolutionError,
    SessionDeploymentContext,
    load_locator,
)

BRANCH_RUNTIME_PATH = re.compile(
    r"topics/[^/]+/coordination/branches/[^/]+/runtime\.yaml"
)


def _relative_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResolutionError(f"{where} must be a non-empty repository path")
    pure = PurePosixPath(value)
    if (
        "\\\\" in value
        or pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or pure.as_posix() != value
    ):
        raise ResolutionError(f"{where} must be a canonical relative repository path")
    return value


def _normalized_roots(values: tuple[str, ...], where: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for index, value in enumerate(values):
        path = _relative_path(value, f"{where}[{index}]")
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def _under(path: str, root: str) -> bool:
    candidate = PurePosixPath(path)
    base = PurePosixPath(root)
    return candidate == base or base in candidate.parents


@dataclass(frozen=True)
class RuntimeCapabilityPolicy:
    """Host-owned Instance path capabilities exposed to a conversation surface."""

    readable_roots: tuple[str, ...]
    writable_roots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        readable = _normalized_roots(self.readable_roots, "readable_roots")
        writable = _normalized_roots(self.writable_roots, "writable_roots")
        for path in writable:
            if not any(_under(path, root) for root in readable):
                raise ResolutionError(
                    "every writable root must be contained by a readable root"
                )
        object.__setattr__(self, "readable_roots", readable)
        object.__setattr__(self, "writable_roots", writable)

    def may_read(self, path: str) -> bool:
        return any(_under(path, root) for root in self.readable_roots)

    def may_write(self, path: str) -> bool:
        return any(_under(path, root) for root in self.writable_roots)


@dataclass(frozen=True)
class InstanceText:
    content: str
    version_token: str


@dataclass(frozen=True)
class InstanceWriteAck:
    applied: bool = True


@dataclass(frozen=True)
class LearningSessionBinding:
    instance_ref: str
    branch_runtime_path: str
    topic: str
    branch_id: str
    lineage_id: str
    generation: int


@dataclass(frozen=True)
class LearningRuntimeSession:
    deployment: SessionDeploymentContext
    binding: LearningSessionBinding
    policy: RuntimeCapabilityPolicy


class RuntimeSessionBroker:
    """Bind a replaceable conversation surface to narrow Learning OS authority."""

    def __init__(
        self,
        provider: RepositoryProvider,
        locator_source: str | dict,
    ):
        self.provider = provider
        self.locator = load_locator(locator_source)
        self.guard = DeploymentGuard(provider)

    @staticmethod
    def _parse_branch_runtime(text: str) -> dict:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ResolutionError(
                f"Branch runtime is malformed YAML: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict) or data.get("document_type") != "branch_runtime":
            raise ResolutionError("Branch runtime has the wrong document type")
        for field in ("topic", "branch_id", "lineage_id"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise ResolutionError(f"Branch runtime {field} is invalid")
        generation = data.get("active_generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ResolutionError("Branch runtime active_generation is invalid")
        generations = data.get("generations")
        if not isinstance(generations, dict):
            raise ResolutionError("Branch runtime generations is invalid")
        record = generations.get(generation)
        if record is None:
            record = generations.get(str(generation))
        if not isinstance(record, dict) or record.get("lifecycle") != "active":
            raise ResolutionError("Branch runtime active generation is not active")
        if data.get("pending_successor") is not None:
            raise ResolutionError("Branch runtime is handing off")
        active_generations: list[int] = []
        for key, value in generations.items():
            if not isinstance(value, dict) or value.get("lifecycle") != "active":
                continue
            if isinstance(key, int) and not isinstance(key, bool) and key >= 1:
                active_generations.append(key)
            elif isinstance(key, str) and key.isdigit() and int(key) >= 1:
                active_generations.append(int(key))
            else:
                raise ResolutionError(
                    "Branch runtime contains an invalid active generation key"
                )
        if active_generations != [generation]:
            raise ResolutionError(
                "Branch runtime must contain exactly one active generation"
            )
        return data

    def _read_branch_runtime(
        self,
        *,
        instance_repository_id: int,
        instance_ref: str,
        runtime_path: str,
    ) -> tuple[dict, str]:
        text, _, commit_sha = self.provider.read_text(
            instance_repository_id, instance_ref, runtime_path
        )
        return self._parse_branch_runtime(text), commit_sha

    def open_session(
        self,
        *,
        branch_runtime_path: str,
        policy: RuntimeCapabilityPolicy,
        expected_generation: int | None = None,
    ) -> LearningRuntimeSession:
        resolved = DeploymentResolver(self.provider).resolve(self.locator)
        instance_ref = self.locator["instance"]["canonical_ref"]
        runtime_path = _relative_path(branch_runtime_path, "branch_runtime_path")
        if policy.writable_roots and expected_generation is None:
            raise GuardRejected(
                "writable learning session requires an established generation"
            )
        runtime, _ = self._read_branch_runtime(
            instance_repository_id=resolved.context.instance_repository_id,
            instance_ref=instance_ref,
            runtime_path=runtime_path,
        )
        generation = runtime["active_generation"]
        if expected_generation is not None and generation != expected_generation:
            raise GuardRejected("requested learning generation is not active")
        return LearningRuntimeSession(
            deployment=resolved.context,
            binding=LearningSessionBinding(
                instance_ref=instance_ref,
                branch_runtime_path=runtime_path,
                topic=runtime["topic"],
                branch_id=runtime["branch_id"],
                lineage_id=runtime["lineage_id"],
                generation=generation,
            ),
            policy=policy,
        )

    def _fresh_generation(
        self, session: LearningRuntimeSession
    ) -> tuple[int, str]:
        try:
            runtime, commit_sha = self._read_branch_runtime(
                instance_repository_id=session.deployment.instance_repository_id,
                instance_ref=session.binding.instance_ref,
                runtime_path=session.binding.branch_runtime_path,
            )
        except ResolutionError as exc:
            raise GuardRejected(
                f"Branch runtime fresh-read failed closed: {exc}"
            ) from None
        for field, expected in (
            ("topic", session.binding.topic),
            ("branch_id", session.binding.branch_id),
            ("lineage_id", session.binding.lineage_id),
        ):
            if runtime[field] != expected:
                raise GuardRejected(f"Branch runtime {field} changed")
        return runtime["active_generation"], commit_sha

    def assert_current(self, session: LearningRuntimeSession) -> str:
        self.guard.check(session.deployment)
        generation, authority_head = self._fresh_generation(session)
        if generation != session.binding.generation:
            raise GuardRejected("semantic generation changed")
        return authority_head

    def read_instance_text(
        self, session: LearningRuntimeSession, path: str
    ) -> InstanceText:
        path = _relative_path(path, "path")
        if not session.policy.may_read(path):
            raise GuardRejected("Instance read is outside the session capability policy")
        authority_head = self.assert_current(session)
        content, blob_sha, _ = self.provider.read_text(
            session.deployment.instance_repository_id,
            authority_head,
            path,
        )
        return InstanceText(content=content, version_token=blob_sha)

    def guarded_update(
        self,
        session: LearningRuntimeSession,
        *,
        path: str,
        content: str,
        expected_blob_sha: str,
        message: str,
    ) -> InstanceWriteAck:
        path = _relative_path(path, "path")
        if BRANCH_RUNTIME_PATH.fullmatch(path):
            raise GuardRejected(
                "ordinary learning session cannot mutate Branch runtime authority"
            )
        if not session.policy.may_write(path):
            raise GuardRejected("Instance write is outside the session capability policy")
        self.guard.check(session.deployment)
        generation, authority_head = self._fresh_generation(session)
        if generation != session.binding.generation:
            raise GuardRejected("semantic generation changed")
        try:
            self.provider.update_text(
                session.deployment.instance_repository_id,
                session.binding.instance_ref,
                path,
                content,
                expected_blob_sha,
                message,
                expected_ref_sha=authority_head,
            )
        except CasConflict:
            raise
        except Exception as exc:
            raise CasConflict(f"Instance compare-and-swap failed: {exc}") from None
        return InstanceWriteAck()
