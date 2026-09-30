"""Model-agnostic Learning OS ordinary-Runtime session broker.

Conversation surfaces never receive repository credentials through this layer.
The host binds a session to one validated deployment, one Instance ref, one
learning Branch generation, and a fixed path capability policy. Canonical
writes always re-check deployment, generation, and target CAS.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import ContextManager, Iterator, Protocol

import yaml
from yaml.tokens import (
    AliasToken,
    AnchorToken,
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
    ScalarToken,
)

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
from scripts.validate_learning_os import (
    DeploymentBinding,
    instance_expected_types,
    instance_generic_write_mode,
    instance_generic_write_role_rule,
    instance_write_policy_fingerprint,
    learning_handoff_identity_mismatches,
    validate_instance_document_trust_boundary,
)

BRANCH_RUNTIME_SCHEMA_VERSION = "0.3"
BRANCH_REGISTRY_SCHEMA_VERSION = "0.3"
BRANCH_ROLES = frozenset({"hub", "main", "practice", "deep_dive"})
BRANCH_LIFECYCLES = frozenset({"active", "idle", "retired"})
BRANCH_GENERATION_LIFECYCLES = frozenset({
    "active",
    "idle",
    "handoff_pending",
    "archived",
    "deprecated",
})
CANDIDATE_YAML_MAX_BYTES = 1024 * 1024
CANDIDATE_YAML_MAX_NODES = 20000
CANDIDATE_YAML_MAX_DEPTH = 64
CANDIDATE_VALIDATION_TIMEOUT_SECONDS = 10
BRANCH_RUNTIME_REQUIRED_FIELDS = frozenset({
    "schema_version",
    "document_type",
    "revision",
    "topic",
    "branch_id",
    "lineage_id",
    "active_generation",
    "pending_successor",
    "generations",
})
BRANCH_REGISTRY_REQUIRED_FIELDS = frozenset({
    "schema_version",
    "document_type",
    "revision",
    "topic",
    "branches",
})
WINDOWS_RESERVED_BASENAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
})

def _relative_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResolutionError(f"{where} must be a non-empty repository path")
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or pure.as_posix() != value
    ):
        raise ResolutionError(f"{where} must be a canonical relative repository path")
    return value


def _candidate_output_path(root: Path, path: str) -> Path:
    pure = PurePosixPath(path)
    if os.name == "nt":
        for part in pure.parts:
            normalized = part.rstrip(" .")
            stem = normalized.split(".", 1)[0].upper()
            if (
                ":" in part
                or normalized != part
                or stem in WINDOWS_RESERVED_BASENAMES
            ):
                raise GuardRejected(
                    "candidate repository path is unsafe on Windows"
                )
    base = root.resolve(strict=True)
    candidate = root.joinpath(*pure.parts).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError:
        raise GuardRejected(
            "candidate repository path escapes the materialized Instance"
        ) from None
    return candidate


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


def _preflight_candidate_yaml(content: str) -> None:
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError:
        raise GuardRejected("candidate YAML is not valid UTF-8 text") from None
    if len(encoded) > CANDIDATE_YAML_MAX_BYTES:
        raise GuardRejected("candidate YAML exceeds the byte limit")

    depth = 0
    nodes = 0
    starts = (
        BlockMappingStartToken,
        BlockSequenceStartToken,
        FlowMappingStartToken,
        FlowSequenceStartToken,
    )
    ends = (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)
    try:
        for token in yaml.scan(content):
            if isinstance(token, (AliasToken, AnchorToken)):
                raise GuardRejected(
                    "candidate YAML aliases and anchors are not allowed"
                )
            if isinstance(token, starts):
                depth += 1
                nodes += 1
                if depth > CANDIDATE_YAML_MAX_DEPTH:
                    raise GuardRejected(
                        "candidate YAML exceeds the nesting-depth limit"
                    )
            elif isinstance(token, ScalarToken):
                nodes += 1
            elif isinstance(token, ends):
                depth = max(0, depth - 1)
            if nodes > CANDIDATE_YAML_MAX_NODES:
                raise GuardRejected("candidate YAML exceeds the node limit")
    except yaml.YAMLError as exc:
        raise GuardRejected(
            f"candidate YAML preflight failed: {exc.__class__.__name__}"
        ) from None


def _preflight_authority_yaml(content: str, where: str) -> None:
    try:
        _preflight_candidate_yaml(content)
    except GuardRejected as exc:
        message = str(exc).replace("candidate YAML", where)
        raise ResolutionError(message) from None


class DeploymentWriteAdmission(Protocol):
    """Host admission authority shared by broker reads/writes and promotion."""

    def read_lease(self) -> ContextManager[None]: ...

    def write_lease(self) -> ContextManager[None]: ...

    def promotion_barrier(self) -> ContextManager[None]: ...


class DeploymentWriteGate:
    """Reference same-process admission/drain gate for Runtime operations.

    Multi-process hosts must provide an equivalent cross-process implementation.
    The promotion path must hold promotion_barrier() across the entire
    freeze/promotion/activation transaction.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._promotion_lock = threading.Lock()
        self._accepting = True
        self._promotion_active = False
        self._holders = 0

    @contextmanager
    def read_lease(self) -> Iterator[None]:
        with self._condition:
            if self._promotion_active:
                raise GuardRejected("deployment promotion is in progress")
            self._holders += 1
        try:
            yield
        finally:
            with self._condition:
                self._holders -= 1
                self._condition.notify_all()

    @contextmanager
    def write_lease(self) -> Iterator[None]:
        with self._condition:
            if not self._accepting:
                raise GuardRejected("deployment write admissions are closed")
            self._holders += 1
        try:
            yield
        finally:
            with self._condition:
                self._holders -= 1
                self._condition.notify_all()

    @contextmanager
    def promotion_barrier(self) -> Iterator[None]:
        self._promotion_lock.acquire()
        try:
            with self._condition:
                self._promotion_active = True
                self._accepting = False
                while self._holders:
                    self._condition.wait()
            yield
        finally:
            with self._condition:
                self._promotion_active = False
                self._accepting = True
                self._condition.notify_all()
            self._promotion_lock.release()


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
    role: str
    subtopic: str | None


@dataclass(frozen=True)
class LearningRuntimeSession:
    """Opaque handle issued by RuntimeSessionBroker."""

    session_id: str


@dataclass(frozen=True)
class _LearningRuntimeSessionState:
    deployment: SessionDeploymentContext
    binding: LearningSessionBinding
    policy: RuntimeCapabilityPolicy


@dataclass
class _IssuedSessionRecord:
    state: _LearningRuntimeSessionState
    holders: int = 0
    revoked: bool = False


class RuntimeSessionBroker:
    """Bind a replaceable conversation surface to narrow Learning OS authority."""

    def __init__(
        self,
        provider: RepositoryProvider,
        locator_source: str | dict,
        *,
        write_admission: DeploymentWriteAdmission | None = None,
    ):
        self.provider = provider
        self.locator = load_locator(locator_source)
        self.guard = DeploymentGuard(provider)
        self.write_admission = write_admission
        self._session_condition = threading.Condition()
        self._issued_sessions: dict[str, _IssuedSessionRecord] = {}

    def close_session(self, session: LearningRuntimeSession) -> None:
        """Revoke one capability and drain operations already using it."""
        if not isinstance(session, LearningRuntimeSession):
            raise GuardRejected("session capability is not broker-issued")
        with self._session_condition:
            record = self._issued_sessions.get(session.session_id)
            if record is None or record.revoked:
                raise GuardRejected("session capability is not broker-issued")
            record.revoked = True
            while record.holders:
                self._session_condition.wait()
            self._issued_sessions.pop(session.session_id, None)
            self._session_condition.notify_all()

    def _session_state(
        self, session: LearningRuntimeSession
    ) -> _LearningRuntimeSessionState:
        if not isinstance(session, LearningRuntimeSession):
            raise GuardRejected("session capability is not broker-issued")
        with self._session_condition:
            record = self._issued_sessions.get(session.session_id)
            if record is None or record.revoked:
                raise GuardRejected("session capability is not broker-issued")
            return record.state

    @contextmanager
    def _session_operation(
        self, session: LearningRuntimeSession
    ) -> Iterator[_LearningRuntimeSessionState]:
        if not isinstance(session, LearningRuntimeSession):
            raise GuardRejected("session capability is not broker-issued")
        with self._session_condition:
            record = self._issued_sessions.get(session.session_id)
            if record is None or record.revoked:
                raise GuardRejected("session capability is not broker-issued")
            record.holders += 1
        try:
            yield record.state
        finally:
            with self._session_condition:
                record.holders -= 1
                self._session_condition.notify_all()

    @staticmethod
    def _parse_branch_runtime(text: str) -> dict:
        _preflight_authority_yaml(text, "Branch runtime YAML")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ResolutionError(
                f"Branch runtime is malformed YAML: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict):
            raise ResolutionError("Branch runtime must be a mapping")
        missing = BRANCH_RUNTIME_REQUIRED_FIELDS - set(data)
        if missing:
            raise ResolutionError(
                "Branch runtime is missing required fields: "
                + ", ".join(sorted(missing))
            )
        if data.get("schema_version") != BRANCH_RUNTIME_SCHEMA_VERSION:
            raise ResolutionError("Branch runtime schema_version is unsupported")
        if data.get("document_type") != "branch_runtime":
            raise ResolutionError("Branch runtime has the wrong document type")
        trust_findings = validate_instance_document_trust_boundary(
            "<branch-runtime>", data, "branch_runtime"
        )
        if trust_findings:
            raise ResolutionError(
                "Branch runtime violates the canonical Instance trust boundary: "
                + "; ".join(
                    finding.render() for finding in trust_findings
                )
            )
        revision = data.get("revision")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise ResolutionError("Branch runtime revision is invalid")
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
        seen_generations: set[int] = set()
        for key, value in generations.items():
            if isinstance(key, int) and not isinstance(key, bool) and key >= 1:
                normalized_generation = key
            elif (
                isinstance(key, str)
                and key.isdigit()
                and not key.startswith("0")
                and int(key) >= 1
            ):
                normalized_generation = int(key)
            else:
                raise ResolutionError(
                    "Branch runtime contains an invalid generation key"
                )
            if normalized_generation in seen_generations:
                raise ResolutionError(
                    "Branch runtime contains duplicate generation identities"
                )
            seen_generations.add(normalized_generation)
            if not isinstance(value, dict):
                raise ResolutionError(
                    "Branch runtime generation record is invalid"
                )
            lifecycle = value.get("lifecycle")
            if lifecycle not in BRANCH_GENERATION_LIFECYCLES:
                raise ResolutionError(
                    "Branch runtime generation lifecycle is invalid"
                )
            if lifecycle == "active":
                active_generations.append(normalized_generation)
        if active_generations != [generation]:
            raise ResolutionError(
                "Branch runtime must contain exactly one active generation"
            )
        return data

    @staticmethod
    def _parse_branch_registry(text: str) -> dict:
        _preflight_authority_yaml(text, "Branch registry YAML")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ResolutionError(
                f"Branch registry is malformed YAML: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict):
            raise ResolutionError("Branch registry must be a mapping")
        missing = BRANCH_REGISTRY_REQUIRED_FIELDS - set(data)
        if missing:
            raise ResolutionError(
                "Branch registry is missing required fields: "
                + ", ".join(sorted(missing))
            )
        if data.get("schema_version") != BRANCH_REGISTRY_SCHEMA_VERSION:
            raise ResolutionError("Branch registry schema_version is unsupported")
        if data.get("document_type") != "branch_registry":
            raise ResolutionError("Branch registry has the wrong document type")
        trust_findings = validate_instance_document_trust_boundary(
            "<branch-registry>", data, "branch_registry"
        )
        if trust_findings:
            raise ResolutionError(
                "Branch registry violates the canonical Instance trust boundary: "
                + "; ".join(finding.render() for finding in trust_findings)
            )
        revision = data.get("revision")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise ResolutionError("Branch registry revision is invalid")
        topic = data.get("topic")
        if not isinstance(topic, str) or not topic:
            raise ResolutionError("Branch registry topic is invalid")
        branches = data.get("branches")
        if not isinstance(branches, dict):
            raise ResolutionError("Branch registry branches is invalid")
        for branch_id, record in branches.items():
            if (
                not isinstance(branch_id, str)
                or not branch_id
                or "/" in branch_id
                or "\\" in branch_id
                or branch_id in {".", ".."}
            ):
                raise ResolutionError("Branch registry branch id is invalid")
            if not isinstance(record, dict):
                raise ResolutionError("Branch registry branch record is invalid")
            if record.get("role") not in BRANCH_ROLES:
                raise ResolutionError("Branch registry role is invalid")
            if record.get("lifecycle") not in BRANCH_LIFECYCLES:
                raise ResolutionError("Branch registry lifecycle is invalid")
            subtopic = record.get("subtopic")
            if subtopic is not None and (
                not isinstance(subtopic, str) or not subtopic
            ):
                raise ResolutionError("Branch registry subtopic is invalid")
        return data

    def _read_branch_registry(
        self,
        *,
        instance_repository_id: int,
        authority_head: str,
        topic: str,
        branch_id: str,
    ) -> dict:
        registry_path = f"topics/{topic}/coordination/branches.yaml"
        if instance_expected_types(registry_path) != ("branch_registry",):
            raise ResolutionError("Branch registry path is not canonical")
        text, _, registry_head = self.provider.read_text(
            instance_repository_id,
            authority_head,
            registry_path,
        )
        if registry_head != authority_head:
            raise ResolutionError(
                "Branch registry provenance changed during validation"
            )
        registry = self._parse_branch_registry(text)
        if registry["topic"] != topic:
            raise ResolutionError("Branch registry topic does not match Branch runtime")
        record = registry["branches"].get(branch_id)
        if not isinstance(record, dict):
            raise ResolutionError("Branch is missing from the canonical registry")
        if record.get("lifecycle") != "active":
            raise ResolutionError("Branch registry lifecycle is not active")
        return record

    def _validate_branch_handoff_refs(
        self,
        *,
        runtime: dict,
        instance_repository_id: int,
        authority_head: str,
    ) -> None:
        generations = runtime["generations"]
        for generation_key, generation_record in generations.items():
            if "handoff_ref" not in generation_record:
                continue
            try:
                ref = _relative_path(
                    generation_record.get("handoff_ref"),
                    f"generations.{generation_key}.handoff_ref",
                )
            except ResolutionError as exc:
                raise ResolutionError(
                    f"Branch runtime handoff_ref is invalid: {exc}"
                ) from None
            if instance_expected_types(ref) != ("learning_handoff",):
                raise ResolutionError(
                    "Branch runtime handoff_ref is not a canonical learning_handoff path"
                )
            try:
                text, _, handoff_head = self.provider.read_text(
                    instance_repository_id,
                    authority_head,
                    ref,
                )
            except ResolutionError as exc:
                raise ResolutionError(
                    f"Branch runtime handoff_ref cannot be resolved: {exc}"
                ) from None
            if handoff_head != authority_head:
                raise ResolutionError(
                    "Branch runtime handoff_ref provenance changed during validation"
                )
            _preflight_authority_yaml(text, "Learning handoff YAML")
            try:
                handoff = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ResolutionError(
                    f"Branch runtime handoff_ref is malformed YAML: "
                    f"{exc.__class__.__name__}"
                ) from None
            if not isinstance(handoff, dict):
                raise ResolutionError(
                    "Branch runtime handoff_ref target must be a mapping"
                )
            if handoff.get("schema_version") != BRANCH_RUNTIME_SCHEMA_VERSION:
                raise ResolutionError(
                    "Branch runtime handoff_ref schema_version is unsupported"
                )
            if handoff.get("document_type") != "learning_handoff":
                raise ResolutionError(
                    "Branch runtime handoff_ref has the wrong document type"
                )
            trust_findings = validate_instance_document_trust_boundary(
                ref, handoff, "learning_handoff"
            )
            if trust_findings:
                raise ResolutionError(
                    "Branch runtime handoff_ref violates the canonical Instance "
                    "trust boundary: "
                    + "; ".join(
                        finding.render() for finding in trust_findings
                    )
                )
            mismatches = learning_handoff_identity_mismatches(
                runtime,
                generation_key,
                generation_record,
                handoff,
            )
            if mismatches:
                raise ResolutionError(
                    "Branch runtime handoff_ref identity is inconsistent: "
                    + "; ".join(mismatches)
                )

    def _read_branch_runtime(
        self,
        *,
        instance_repository_id: int,
        instance_ref: str,
        runtime_path: str,
    ) -> tuple[dict, str]:
        types = instance_expected_types(runtime_path)
        if types != ("branch_runtime",):
            raise ResolutionError(
                "Branch runtime authority path is not canonical"
            )
        text, _, commit_sha = self.provider.read_text(
            instance_repository_id, instance_ref, runtime_path
        )
        runtime = self._parse_branch_runtime(text)
        parts = PurePosixPath(runtime_path).parts
        if runtime["topic"] != parts[1] or runtime["branch_id"] != parts[4]:
            raise ResolutionError(
                "Branch runtime identity does not match its canonical path"
            )
        self._validate_branch_handoff_refs(
            runtime=runtime,
            instance_repository_id=instance_repository_id,
            authority_head=commit_sha,
        )
        return runtime, commit_sha

    def open_session(
        self,
        *,
        branch_runtime_path: str,
        policy: RuntimeCapabilityPolicy,
        expected_generation: int | None = None,
    ) -> LearningRuntimeSession:
        if self.write_admission is None:
            raise GuardRejected(
                "learning session requires shared deployment operation admission"
            )
        resolved = DeploymentResolver(self.provider).resolve(self.locator)
        try:
            deployment = resolved.context
        finally:
            self._release_materializations([
                resolved.control,
                resolved.core,
                resolved.instance,
            ])
        instance_ref = self.locator["instance"]["canonical_ref"]
        runtime_path = _relative_path(branch_runtime_path, "branch_runtime_path")
        if policy.writable_roots and expected_generation is None:
            raise GuardRejected(
                "writable learning session requires an established generation"
            )
        runtime, authority_head = self._read_branch_runtime(
            instance_repository_id=deployment.instance_repository_id,
            instance_ref=instance_ref,
            runtime_path=runtime_path,
        )
        branch_record = self._read_branch_registry(
            instance_repository_id=deployment.instance_repository_id,
            authority_head=authority_head,
            topic=runtime["topic"],
            branch_id=runtime["branch_id"],
        )
        generation = runtime["active_generation"]
        if expected_generation is not None and generation != expected_generation:
            raise GuardRejected("requested learning generation is not active")
        binding = LearningSessionBinding(
            instance_ref=instance_ref,
            branch_runtime_path=runtime_path,
            topic=runtime["topic"],
            branch_id=runtime["branch_id"],
            lineage_id=runtime["lineage_id"],
            generation=generation,
            role=branch_record["role"],
            subtopic=branch_record.get("subtopic"),
        )
        state = _LearningRuntimeSessionState(
            deployment=deployment,
            binding=binding,
            policy=policy,
        )
        with self._session_condition:
            session_id = secrets.token_urlsafe(32)
            while session_id in self._issued_sessions:
                session_id = secrets.token_urlsafe(32)
            self._issued_sessions[session_id] = _IssuedSessionRecord(state=state)
        return LearningRuntimeSession(session_id=session_id)

    def _fresh_generation(
        self, state: _LearningRuntimeSessionState
    ) -> tuple[int, str]:
        try:
            runtime, commit_sha = self._read_branch_runtime(
                instance_repository_id=state.deployment.instance_repository_id,
                instance_ref=state.binding.instance_ref,
                runtime_path=state.binding.branch_runtime_path,
            )
        except ResolutionError as exc:
            raise GuardRejected(
                f"Branch runtime fresh-read failed closed: {exc}"
            ) from None
        for field, expected in (
            ("topic", state.binding.topic),
            ("branch_id", state.binding.branch_id),
            ("lineage_id", state.binding.lineage_id),
        ):
            if runtime[field] != expected:
                raise GuardRejected(f"Branch runtime {field} changed")
        try:
            branch_record = self._read_branch_registry(
                instance_repository_id=state.deployment.instance_repository_id,
                authority_head=commit_sha,
                topic=state.binding.topic,
                branch_id=state.binding.branch_id,
            )
        except ResolutionError as exc:
            raise GuardRejected(
                f"Branch registry fresh-read failed closed: {exc}"
            ) from None
        if branch_record.get("role") != state.binding.role:
            raise GuardRejected("Branch registry role changed")
        if branch_record.get("subtopic") != state.binding.subtopic:
            raise GuardRejected("Branch registry subtopic changed")
        return runtime["active_generation"], commit_sha

    def _assert_state_current(
        self, state: _LearningRuntimeSessionState
    ) -> str:
        self.guard.check(state.deployment, require_active=False)
        generation, authority_head = self._fresh_generation(state)
        if generation != state.binding.generation:
            raise GuardRejected("semantic generation changed")
        return authority_head

    def assert_current(self, session: LearningRuntimeSession) -> None:
        """Verify session freshness without exposing private Instance commit identity."""
        with self._session_operation(session) as state:
            self._assert_state_current(state)

    def _assert_instance_authority_head_current(
        self,
        state: _LearningRuntimeSessionState,
        expected_head: str,
    ) -> None:
        try:
            _, _, current_head = self.provider.read_text(
                state.deployment.instance_repository_id,
                state.binding.instance_ref,
                state.binding.branch_runtime_path,
            )
        except ResolutionError as exc:
            raise GuardRejected(
                f"Instance authority head recheck failed closed: {exc}"
            ) from None
        if current_head != expected_head:
            raise GuardRejected("Instance authority head changed during read")

    def _release_materializations(
        self, snapshots: list[MaterializedRepository]
    ) -> None:
        first_error = None
        for snapshot in reversed(snapshots):
            try:
                self.provider.release_materialization(snapshot)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise GuardRejected(
                "repository materialization cleanup failed"
            ) from first_error

    def _assert_deployed_write_policy_compatible(
        self, state: _LearningRuntimeSessionState
    ) -> None:
        snapshots: list[MaterializedRepository] = []
        try:
            core = self.provider.materialize(
                state.deployment.core_repository_id,
                state.deployment.core_commit,
            )
            snapshots.append(core)
            if (
                core.repository_id != state.deployment.core_repository_id
                or core.commit_sha != state.deployment.core_commit
            ):
                raise GuardRejected(
                    "deployed Core write policy provenance changed during validation"
                )
            core_config_path = core.root / "config" / "core.yaml"
            validator_path = core.root / "scripts" / "validate_learning_os.py"
            if not core_config_path.is_file() or not validator_path.is_file():
                raise GuardRejected(
                    "deployed Core write policy implementation is unavailable"
                )
            try:
                core_config = yaml.safe_load(
                    core_config_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise GuardRejected(
                    "deployed Core write policy declaration is unreadable: "
                    f"{exc.__class__.__name__}"
                ) from None
            manifest = (
                core_config.get("manifest")
                if isinstance(core_config, dict)
                else None
            )
            declared_fingerprint = (
                manifest.get("runtime_session_write_policy_fingerprint")
                if isinstance(manifest, dict)
                else None
            )
            command = [
                sys.executable,
                str(validator_path),
                "--write-policy-fingerprint",
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=CANDIDATE_VALIDATION_TIMEOUT_SECONDS,
                    check=False,
                    cwd=core.root,
                )
            except subprocess.TimeoutExpired:
                raise GuardRejected(
                    "deployed Core write policy computation timed out"
                ) from None
            except OSError:
                raise GuardRejected(
                    "deployed Core write policy computation failed to start"
                ) from None
            implementation_fingerprint = result.stdout.strip()
            if (
                result.returncode != 0
                or len(implementation_fingerprint) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in implementation_fingerprint
                )
            ):
                raise GuardRejected(
                    "deployed Core write policy computation failed closed"
                )
            if implementation_fingerprint != declared_fingerprint:
                raise GuardRejected(
                    "deployed Core manifest write policy does not match "
                    "the exact deployed implementation"
                )
            if implementation_fingerprint != instance_write_policy_fingerprint():
                raise GuardRejected(
                    "Runtime broker write policy does not match the exact deployed Core"
                )
        finally:
            self._release_materializations(snapshots)

    @staticmethod
    def _assert_replace_role_compatible(
        state: _LearningRuntimeSessionState,
        *,
        document_type: str,
        path: str,
    ) -> None:
        rule = instance_generic_write_role_rule(document_type)
        if rule is None:
            return
        if state.binding.role not in rule["roles"]:
            raise GuardRejected(
                f"Branch role {state.binding.role!r} cannot replace {document_type}"
            )
        if rule["scope"] == "bound_subtopic":
            parts = PurePosixPath(path).parts
            target_topic = parts[1]
            target_subtopic = parts[3]
            if (
                state.binding.topic != target_topic
                or state.binding.subtopic != target_subtopic
            ):
                raise GuardRejected(
                    f"{document_type} replacement is outside the bound Subtopic"
                )
            return
        raise GuardRejected("deployed Core role-write scope is unsupported")

    def _validate_candidate(
        self,
        state: _LearningRuntimeSessionState,
        *,
        authority_head: str,
        path: str,
        content: str,
        contract: dict,
    ) -> None:
        _preflight_candidate_yaml(content)
        snapshots: list = []
        try:
            instance = self.provider.materialize(
                state.deployment.instance_repository_id,
                authority_head,
            )
            snapshots.append(instance)
            core = self.provider.materialize(
                state.deployment.core_repository_id,
                state.deployment.core_commit,
            )
            snapshots.append(core)
            binding = DeploymentBinding.from_contract(
                contract, self.locator
            )
            if not isinstance(binding.fields, dict):
                raise GuardRejected(
                    "candidate deployment binding is unavailable"
                )

            with tempfile.TemporaryDirectory(
                prefix="learning-os-candidate-"
            ) as candidate_dir:
                temp_root = Path(candidate_dir)
                root = temp_root / "instance"
                shutil.copytree(instance.root, root)
                candidate_path = _candidate_output_path(root, path)
                candidate_path.parent.mkdir(
                    parents=True, exist_ok=True
                )
                candidate_path.write_text(
                    content, encoding="utf-8", newline="\n"
                )
                binding_path = temp_root / "deployment-binding.yaml"
                binding_path.write_text(
                    yaml.safe_dump(
                        {"context_type": "synthetic", **binding.fields},
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
                validator_path = (
                    core.root / "scripts" / "validate_learning_os.py"
                )
                if not validator_path.is_file():
                    raise GuardRejected(
                        "deployed Core validator is unavailable"
                    )
                command = [
                    sys.executable,
                    str(validator_path),
                    str(root),
                    "--instance",
                    "--core-snapshot",
                    str(core.root),
                    "--deployment-binding",
                    str(binding_path),
                ]
                try:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=CANDIDATE_VALIDATION_TIMEOUT_SECONDS,
                        check=False,
                        cwd=core.root,
                    )
                except subprocess.TimeoutExpired:
                    raise GuardRejected(
                        "candidate Instance validation timed out"
                    ) from None
                except OSError:
                    raise GuardRejected(
                        "candidate Instance validation failed to start"
                    ) from None
            if result.returncode != 0:
                raise GuardRejected(
                    "candidate Instance state failed canonical validation"
                )
        finally:
            self._release_materializations(snapshots)

    def read_instance_text(
        self, session: LearningRuntimeSession, path: str
    ) -> InstanceText:
        with self._session_operation(session) as state:
            path = _relative_path(path, "path")
            if not state.policy.may_read(path):
                raise GuardRejected(
                    "Instance read is outside the session capability policy"
                )
            if self.write_admission is None:
                raise GuardRejected(
                    "learning session requires shared deployment operation admission"
                )
            with self.write_admission.read_lease():
                authority_head = self._assert_state_current(state)
                content, blob_sha, read_head = self.provider.read_text(
                    state.deployment.instance_repository_id,
                    authority_head,
                    path,
                )
                if read_head != authority_head:
                    raise GuardRejected(
                        "Instance read provenance changed during the operation"
                    )
                self.guard.check(state.deployment, require_active=False)
                final_generation, final_authority_head = self._fresh_generation(state)
                if final_generation != state.binding.generation:
                    raise GuardRejected(
                        "semantic generation changed during Instance read"
                    )
                if final_authority_head != authority_head:
                    raise GuardRejected(
                        "Instance authority head changed during read"
                    )
                self._assert_instance_authority_head_current(
                    state, authority_head
                )
                self.guard.check(state.deployment, require_active=False)
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
        with self._session_operation(session) as state:
            path = _relative_path(path, "path")
            if not state.policy.may_write(path):
                raise GuardRejected(
                    "Instance write is outside the session capability policy"
                )
            if self.write_admission is None:
                raise GuardRejected(
                    "writable learning session requires shared deployment write admission"
                )
            with self.write_admission.write_lease():
                contract = self.guard.check(state.deployment)
                self._assert_deployed_write_policy_compatible(state)

                types = instance_expected_types(path)
                if len(types) != 1:
                    raise GuardRejected(
                        "Instance update path is unclassified or ambiguous"
                    )
                document_type = types[0]
                write_mode = instance_generic_write_mode(document_type)
                if write_mode == "branch_authority":
                    raise GuardRejected(
                        "ordinary learning session cannot mutate Branch runtime authority"
                    )
                if write_mode == "hub_authority":
                    raise GuardRejected(
                        "ordinary learning session cannot mutate Global Hub runtime state"
                    )
                if write_mode == "immutable_create_only":
                    raise GuardRejected(
                        "ordinary learning session cannot overwrite immutable/create-only "
                        f"{document_type} records"
                    )
                if write_mode == "protocol_transition":
                    raise GuardRejected(
                        "ordinary learning session must use the dedicated transition "
                        f"operation for {document_type}"
                    )
                if write_mode == "hub_transition":
                    raise GuardRejected(
                        "ordinary learning session must use a dedicated Hub-class "
                        f"transition for {document_type}"
                    )
                if write_mode != "replace":
                    raise GuardRejected(
                        "deployed Core generic write mode is unsupported"
                    )
                self._assert_replace_role_compatible(
                    state,
                    document_type=document_type,
                    path=path,
                )

                generation, authority_head = self._fresh_generation(state)
                if generation != state.binding.generation:
                    raise GuardRejected("semantic generation changed")
                self._validate_candidate(
                    state,
                    authority_head=authority_head,
                    path=path,
                    content=content,
                    contract=contract,
                )
                self.guard.check(state.deployment)
                final_generation, final_authority_head = self._fresh_generation(
                    state
                )
                if final_generation != state.binding.generation:
                    raise GuardRejected("semantic generation changed")
                if final_authority_head != authority_head:
                    raise GuardRejected("Instance authority head changed")

                try:
                    self.provider.update_text(
                        state.deployment.instance_repository_id,
                        state.binding.instance_ref,
                        path,
                        content,
                        expected_blob_sha,
                        message,
                        expected_ref_sha=authority_head,
                    )
                except CasConflict:
                    raise
                except Exception as exc:
                    raise CasConflict(
                        f"Instance compare-and-swap failed: {exc}"
                    ) from None
            return InstanceWriteAck()
