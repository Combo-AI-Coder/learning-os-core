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
import datetime as datetime_module
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import ContextManager, Iterator, Protocol

import yaml

from scripts.runtime_adapter import (
    BOUNDED_YAML_MAX_BYTES,
    BOUNDED_YAML_MAX_DEPTH,
    BOUNDED_YAML_MAX_NODES,
    MAX_SNAPSHOT_PATH_BYTES,
    MAX_SNAPSHOT_PATH_COMPONENT_BYTES,
    MAX_SNAPSHOT_PATH_DEPTH,
    CasConflict,
    DeploymentGuard,
    DeploymentResolver,
    GuardRejected,
    RepositoryProvider,
    ResolutionError,
    SessionDeploymentContext,
    _preflight_bounded_yaml,
    load_locator,
)
from scripts.validate_learning_os import (
    DeploymentBinding,
    instance_expected_types,
    instance_generic_write_mode,
    instance_generic_write_role_rule,
    instance_generic_write_transition_rule,
    instance_generic_write_version_rule,
    instance_path_identity_mismatches,
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
CANDIDATE_YAML_MAX_BYTES = BOUNDED_YAML_MAX_BYTES
CANDIDATE_YAML_MAX_NODES = BOUNDED_YAML_MAX_NODES
CANDIDATE_YAML_MAX_DEPTH = BOUNDED_YAML_MAX_DEPTH
CANDIDATE_VALIDATION_TIMEOUT_SECONDS = 10
LEARNING_CONTEXT_MAX_DOCUMENTS = 32
KNOWLEDGE_RECONCILE_MAX_NEW_EVIDENCE_REFS = 32
FINGERPRINT_STDOUT_MAX_BYTES = 128
FINGERPRINT_POLL_SECONDS = 0.01
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
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ResolutionError(f"{where} must be valid UTF-8") from None
    if len(encoded) > MAX_SNAPSHOT_PATH_BYTES:
        raise ResolutionError(f"{where} exceeds the repository path byte limit")
    raw_parts = value.split("/")
    if len(raw_parts) > MAX_SNAPSHOT_PATH_DEPTH:
        raise ResolutionError(f"{where} exceeds the repository path depth limit")
    if any(
        len(part.encode("utf-8")) > MAX_SNAPSHOT_PATH_COMPONENT_BYTES
        for part in raw_parts
    ):
        raise ResolutionError(
            f"{where} exceeds the repository path component byte limit"
        )
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or not pure.parts
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
        _preflight_bounded_yaml(content, "candidate YAML")
    except ResolutionError as exc:
        raise GuardRejected(str(exc)) from None


def _preflight_authority_yaml(content: str, where: str) -> None:
    _preflight_bounded_yaml(content, where)


def _compute_bounded_write_policy_fingerprint(
    command: list[str], *, cwd: Path
) -> str:
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise GuardRejected(
            "deployed Core write policy computation failed to start"
        ) from None

    output = bytearray()
    overflow = threading.Event()

    def drain_stdout() -> None:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(64)
            if not chunk:
                break
            remaining = FINGERPRINT_STDOUT_MAX_BYTES - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()

    reader = threading.Thread(
        target=drain_stdout,
        name="learning-os-write-policy-fingerprint-stdout",
        daemon=True,
    )
    reader.start()

    timed_out = False
    try:
        deadline = time.monotonic() + CANDIDATE_VALIDATION_TIMEOUT_SECONDS
        while process.poll() is None:
            if overflow.is_set():
                process.kill()
                break
            if time.monotonic() > deadline:
                timed_out = True
                process.kill()
                break
            time.sleep(FINGERPRINT_POLL_SECONDS)

        if process.poll() is None:
            process.wait()
        reader.join(timeout=1)
        if reader.is_alive():
            raise GuardRejected(
                "deployed Core write policy output did not drain"
            )
        if overflow.is_set():
            raise GuardRejected(
                "deployed Core write policy output exceeded Runtime budget"
            )
        if timed_out:
            raise GuardRejected(
                "deployed Core write policy computation timed out"
            )
        if process.returncode != 0:
            raise GuardRejected(
                "deployed Core write policy computation failed closed"
            )
        try:
            return bytes(output).decode("ascii").strip()
        except UnicodeDecodeError:
            raise GuardRejected(
                "deployed Core write policy computation failed closed"
            ) from None
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if process.stdout is not None:
            process.stdout.close()
        reader.join(timeout=1)


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
class LearningContextBundle:
    documents: tuple[tuple[str, InstanceText], ...]
    missing_optional: tuple[str, ...] = ()


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


@dataclass
class _PinnedInstanceAuthority:
    snapshot: MaterializedRepository
    runtime: dict
    branch_record: dict
    head: str


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
        self._write_policy_cache_lock = threading.Lock()
        self._verified_core_write_policy_fingerprints: dict[
            tuple[int, str], str
        ] = {}

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
    def _parse_branch_runtime(
        text: str, *, allow_handoff_pending: bool = False
    ) -> dict:
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
        if not isinstance(record, dict):
            raise ResolutionError("Branch runtime active generation record is invalid")
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
        pending = data.get("pending_successor")
        if allow_handoff_pending:
            pending_value = (
                pending.get("generation") if isinstance(pending, dict) else pending
            )
            if (
                not isinstance(pending_value, int)
                or isinstance(pending_value, bool)
                or pending_value <= generation
            ):
                raise ResolutionError(
                    "Branch runtime pending successor generation is invalid"
                )
            if record.get("lifecycle") != "handoff_pending":
                raise ResolutionError(
                    "Branch runtime source generation is not handoff_pending"
                )
            if active_generations:
                raise ResolutionError(
                    "Branch runtime handing off must not contain an active generation"
                )
        else:
            if record.get("lifecycle") != "active":
                raise ResolutionError(
                    "Branch runtime active generation is not active"
                )
            if pending is not None:
                raise ResolutionError("Branch runtime is handing off")
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

    @staticmethod
    def _read_materialized_authority_yaml(
        snapshot: MaterializedRepository,
        ref: str,
        where: str,
    ) -> str:
        target = snapshot.root.joinpath(*PurePosixPath(ref).parts)
        try:
            if not target.is_file():
                raise ResolutionError(
                    f"{where} target is missing from the exact Instance "
                    "authority snapshot"
                )
            text = target.read_text(encoding="utf-8")
        except ResolutionError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ResolutionError(
                f"{where} target is unreadable: {exc.__class__.__name__}"
            ) from None
        _preflight_authority_yaml(text, where)
        return text

    def _read_branch_registry_from_snapshot(
        self,
        *,
        snapshot: MaterializedRepository,
        topic: str,
        branch_id: str,
    ) -> dict:
        registry_path = f"topics/{topic}/coordination/branches.yaml"
        if instance_expected_types(registry_path) != ("branch_registry",):
            raise ResolutionError("Branch registry path is not canonical")
        text = self._read_materialized_authority_yaml(
            snapshot, registry_path, "Branch registry YAML"
        )
        registry = self._parse_branch_registry(text)
        if registry["topic"] != topic:
            raise ResolutionError(
                "Branch registry topic does not match Branch runtime"
            )
        record = registry["branches"].get(branch_id)
        if not isinstance(record, dict):
            raise ResolutionError(
                "Branch is missing from the canonical registry"
            )
        if record.get("lifecycle") != "active":
            raise ResolutionError(
                "Branch registry lifecycle is not active"
            )
        return record

    def _validate_branch_handoff_refs(
        self,
        *,
        runtime: dict,
        instance_repository_id: int,
        authority_head: str,
        snapshot: MaterializedRepository | None = None,
    ) -> None:
        refs = []
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
            refs.append((generation_key, generation_record, ref))

        if not refs:
            return

        owns_snapshot = snapshot is None
        if snapshot is None:
            try:
                snapshot = self.provider.materialize(
                    instance_repository_id,
                    authority_head,
                )
            except ResolutionError as exc:
                raise ResolutionError(
                    "Branch runtime handoff authority snapshot cannot be "
                    f"resolved: {exc}"
                ) from None
        try:
            if (
                snapshot.repository_id != instance_repository_id
                or snapshot.commit_sha != authority_head
            ):
                raise ResolutionError(
                    "Branch runtime handoff authority snapshot provenance changed"
                )
            for generation_key, generation_record, ref in refs:
                text = self._read_materialized_authority_yaml(
                    snapshot,
                    ref,
                    "Branch runtime handoff_ref",
                )
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
                mismatches = [
                    *instance_path_identity_mismatches(
                        ref, handoff, "learning_handoff"
                    ),
                    *learning_handoff_identity_mismatches(
                        runtime,
                        generation_key,
                        generation_record,
                        handoff,
                    ),
                ]
                if mismatches:
                    raise ResolutionError(
                        "Branch runtime handoff_ref identity is inconsistent: "
                        + "; ".join(mismatches)
                    )
        finally:
            if owns_snapshot:
                self._release_materializations([snapshot])

    def _read_pending_branch_runtime(
        self,
        *,
        instance_repository_id: int,
        instance_ref: str,
        runtime_path: str,
    ) -> tuple[dict, str, str]:
        types = instance_expected_types(runtime_path)
        if types != ("branch_runtime",):
            raise ResolutionError(
                "Branch runtime authority path is not canonical"
            )
        text, blob_sha, commit_sha = self.provider.read_text(
            instance_repository_id, instance_ref, runtime_path
        )
        runtime = self._parse_branch_runtime(
            text, allow_handoff_pending=True
        )
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
        return runtime, blob_sha, commit_sha

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
        # bool and float compare equal to some integers in Python. A caller
        # must supply the actual positive-integer generation identity, not an
        # equality-compatible value. Reject before repository/provider effects.
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation < 1
        ):
            raise ResolutionError("expected generation must be a positive integer")
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
        return self._issue_session(state)

    def _issue_session(
        self, state: _LearningRuntimeSessionState
    ) -> LearningRuntimeSession:
        with self._session_condition:
            session_id = secrets.token_urlsafe(32)
            while session_id in self._issued_sessions:
                session_id = secrets.token_urlsafe(32)
            self._issued_sessions[session_id] = _IssuedSessionRecord(state=state)
        return LearningRuntimeSession(session_id=session_id)

    def claim_successor_session(
        self,
        *,
        branch_runtime_path: str,
        policy: RuntimeCapabilityPolicy,
        expected_successor_generation: int,
    ) -> LearningRuntimeSession:
        """Claim an authorized pending Branch successor and bind its session.

        This is a host-side continuity transition, not a conversation-supplied
        generic Instance write. Canonical handoff_pending state is required;
        successful claim establishes the returned session generation identity.
        """
        if self.write_admission is None:
            raise GuardRejected(
                "successor claim requires shared deployment write admission"
            )
        if (
            not isinstance(expected_successor_generation, int)
            or isinstance(expected_successor_generation, bool)
            or expected_successor_generation < 1
        ):
            raise ResolutionError(
                "expected successor generation must be a positive integer"
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
        runtime_path = _relative_path(
            branch_runtime_path, "branch_runtime_path"
        )

        with self.write_admission.write_lease():
            contract = self.guard.check(deployment)
            runtime, runtime_blob, authority_head = (
                self._read_pending_branch_runtime(
                    instance_repository_id=deployment.instance_repository_id,
                    instance_ref=instance_ref,
                    runtime_path=runtime_path,
                )
            )
            source_generation = runtime["active_generation"]
            pending = runtime["pending_successor"]
            pending_generation = (
                pending.get("generation")
                if isinstance(pending, dict)
                else pending
            )
            if pending_generation != expected_successor_generation:
                raise GuardRejected(
                    "requested successor generation is not pending"
                )

            generations = runtime["generations"]
            source_key = (
                source_generation
                if source_generation in generations
                else str(source_generation)
            )
            source_record = generations[source_key]
            if not source_record.get("handoff_ref"):
                raise GuardRejected(
                    "pending successor claim requires a canonical learning handoff"
                )
            if (
                expected_successor_generation in generations
                or str(expected_successor_generation) in generations
            ):
                raise GuardRejected(
                    "pending successor generation is already materialized"
                )

            branch_record = self._read_branch_registry(
                instance_repository_id=deployment.instance_repository_id,
                authority_head=authority_head,
                topic=runtime["topic"],
                branch_id=runtime["branch_id"],
            )

            candidate = yaml.safe_load(
                yaml.safe_dump(runtime, sort_keys=False)
            )
            candidate["revision"] = runtime["revision"] + 1
            candidate["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            candidate["active_generation"] = expected_successor_generation
            candidate["pending_successor"] = None
            candidate["generations"][source_key]["lifecycle"] = "archived"
            candidate["generations"][expected_successor_generation] = {
                "lifecycle": "active"
            }
            candidate_text = yaml.safe_dump(candidate, sort_keys=False)

            validation_state = _LearningRuntimeSessionState(
                deployment=deployment,
                binding=LearningSessionBinding(
                    instance_ref=instance_ref,
                    branch_runtime_path=runtime_path,
                    topic=runtime["topic"],
                    branch_id=runtime["branch_id"],
                    lineage_id=runtime["lineage_id"],
                    generation=source_generation,
                    role=branch_record["role"],
                    subtopic=branch_record.get("subtopic"),
                ),
                policy=policy,
            )
            self._validate_candidate(
                validation_state,
                authority_head=authority_head,
                path=runtime_path,
                content=candidate_text,
                contract=contract,
            )
            self.guard.check(deployment)
            try:
                self.provider.update_text(
                    deployment.instance_repository_id,
                    instance_ref,
                    runtime_path,
                    candidate_text,
                    runtime_blob,
                    (
                        "Claim learning Branch successor generation "
                        f"{expected_successor_generation}"
                    ),
                    expected_ref_sha=authority_head,
                )
            except CasConflict:
                raise
            except Exception as exc:
                raise CasConflict(
                    f"successor claim compare-and-swap failed: {exc}"
                ) from None

            self.guard.check(deployment)
            claimed_runtime, claimed_head = self._read_branch_runtime(
                instance_repository_id=deployment.instance_repository_id,
                instance_ref=instance_ref,
                runtime_path=runtime_path,
            )
            if (
                claimed_runtime["active_generation"]
                != expected_successor_generation
            ):
                raise GuardRejected(
                    "successor claim did not become canonical"
                )
            claimed_branch = self._read_branch_registry(
                instance_repository_id=deployment.instance_repository_id,
                authority_head=claimed_head,
                topic=claimed_runtime["topic"],
                branch_id=claimed_runtime["branch_id"],
            )
            state = _LearningRuntimeSessionState(
                deployment=deployment,
                binding=LearningSessionBinding(
                    instance_ref=instance_ref,
                    branch_runtime_path=runtime_path,
                    topic=claimed_runtime["topic"],
                    branch_id=claimed_runtime["branch_id"],
                    lineage_id=claimed_runtime["lineage_id"],
                    generation=expected_successor_generation,
                    role=claimed_branch["role"],
                    subtopic=claimed_branch.get("subtopic"),
                ),
                policy=policy,
            )
            return self._issue_session(state)

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

    def _pin_instance_authority(
        self, state: _LearningRuntimeSessionState
    ) -> _PinnedInstanceAuthority:
        snapshot = None
        keep_snapshot = False
        try:
            try:
                authority_head = self.provider.resolve_ref(
                    state.deployment.instance_repository_id,
                    state.binding.instance_ref,
                )
                snapshot = self.provider.materialize(
                    state.deployment.instance_repository_id,
                    authority_head,
                )
                if (
                    snapshot.repository_id
                    != state.deployment.instance_repository_id
                    or snapshot.commit_sha != authority_head
                ):
                    raise ResolutionError(
                        "Instance authority snapshot provenance changed"
                    )
                runtime_text = self._read_materialized_authority_yaml(
                    snapshot,
                    state.binding.branch_runtime_path,
                    "Branch runtime YAML",
                )
                runtime = self._parse_branch_runtime(runtime_text)
                parts = PurePosixPath(
                    state.binding.branch_runtime_path
                ).parts
                if (
                    runtime["topic"] != parts[1]
                    or runtime["branch_id"] != parts[4]
                ):
                    raise ResolutionError(
                        "Branch runtime identity does not match its canonical path"
                    )
                self._validate_branch_handoff_refs(
                    runtime=runtime,
                    instance_repository_id=state.deployment.instance_repository_id,
                    authority_head=authority_head,
                    snapshot=snapshot,
                )
            except ResolutionError as exc:
                raise GuardRejected(
                    f"Branch runtime fresh-read failed closed: {exc}"
                ) from None

            try:
                branch_record = self._read_branch_registry_from_snapshot(
                    snapshot=snapshot,
                    topic=state.binding.topic,
                    branch_id=state.binding.branch_id,
                )
            except ResolutionError as exc:
                raise GuardRejected(
                    f"Branch registry fresh-read failed closed: {exc}"
                ) from None

            for field, expected in (
                ("topic", state.binding.topic),
                ("branch_id", state.binding.branch_id),
                ("lineage_id", state.binding.lineage_id),
            ):
                if runtime[field] != expected:
                    raise GuardRejected(
                        f"Branch runtime {field} changed"
                    )
            if runtime["active_generation"] != state.binding.generation:
                raise GuardRejected("semantic generation changed")
            if branch_record.get("role") != state.binding.role:
                raise GuardRejected("Branch registry role changed")
            if branch_record.get("subtopic") != state.binding.subtopic:
                raise GuardRejected("Branch registry subtopic changed")

            result = _PinnedInstanceAuthority(
                snapshot=snapshot,
                runtime=runtime,
                branch_record=branch_record,
                head=authority_head,
            )
            keep_snapshot = True
            return result
        finally:
            if snapshot is not None and not keep_snapshot:
                self._release_materializations([snapshot])

    def _assert_pinned_instance_current(
        self,
        state: _LearningRuntimeSessionState,
        authority: _PinnedInstanceAuthority,
    ) -> None:
        try:
            current_head = self.provider.resolve_ref(
                state.deployment.instance_repository_id,
                state.binding.instance_ref,
            )
        except ResolutionError as exc:
            raise GuardRejected(
                f"Instance authority head recheck failed closed: {exc}"
            ) from None
        if current_head == authority.head:
            return
        # Drift is exceptional. Re-run the semantic check only on drift so
        # generation/registry errors retain their existing diagnostics.
        generation, _ = self._fresh_generation(state)
        if generation != state.binding.generation:
            raise GuardRejected("semantic generation changed")
        raise GuardRejected(
            "Instance authority head changed during operation"
        )

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

    def _verified_deployed_core_write_policy_fingerprint(
        self,
        state: _LearningRuntimeSessionState,
        *,
        core_snapshot: MaterializedRepository | None = None,
    ) -> str:
        key = (
            state.deployment.core_repository_id,
            state.deployment.core_commit,
        )
        if core_snapshot is not None and (
            core_snapshot.repository_id != key[0]
            or core_snapshot.commit_sha != key[1]
        ):
            raise GuardRejected(
                "deployed Core write policy provenance changed during validation"
            )

        with self._write_policy_cache_lock:
            cached = self._verified_core_write_policy_fingerprints.get(key)
            if cached is not None:
                return cached

            snapshots: list[MaterializedRepository] = []
            core = core_snapshot
            if core is None:
                core = self.provider.materialize(key[0], key[1])
                snapshots.append(core)
            try:
                if core.repository_id != key[0] or core.commit_sha != key[1]:
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
                implementation_fingerprint = (
                    _compute_bounded_write_policy_fingerprint(
                        command, cwd=core.root
                    )
                )
                if (
                    len(implementation_fingerprint) != 64
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
            finally:
                self._release_materializations(snapshots)

            # One RuntimeSessionBroker follows one Runtime-Control deployment.
            # Retain only the most recently verified exact Core identity so
            # long-lived hosts do not accumulate historical promotion entries.
            self._verified_core_write_policy_fingerprints.clear()
            self._verified_core_write_policy_fingerprints[key] = (
                implementation_fingerprint
            )
            return implementation_fingerprint

    def _assert_deployed_write_policy_compatible(
        self,
        state: _LearningRuntimeSessionState,
        *,
        core_snapshot: MaterializedRepository | None = None,
    ) -> None:
        implementation_fingerprint = (
            self._verified_deployed_core_write_policy_fingerprint(
                state, core_snapshot=core_snapshot
            )
        )
        if implementation_fingerprint != instance_write_policy_fingerprint():
            raise GuardRejected(
                "Runtime broker write policy does not match the exact deployed Core"
            )

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
        if rule["scope"] == "bound_branch":
            parts = PurePosixPath(path).parts
            target_topic = parts[1]
            target_branch = parts[4]
            if (
                state.binding.topic != target_topic
                or state.binding.branch_id != target_branch
            ):
                raise GuardRejected(
                    f"{document_type} replacement is outside the bound Branch"
                )
            return
        raise GuardRejected("deployed Core role-write scope is unsupported")

    @staticmethod
    def _load_transition_documents(
        instance_root: Path, path: str, content: str
    ) -> tuple[dict, dict]:
        current_path = instance_root.joinpath(*PurePosixPath(path).parts)
        try:
            if not current_path.is_file():
                raise GuardRejected(
                    "replacement target is missing from the exact "
                    "Instance authority snapshot"
                )
            current_text = current_path.read_text(encoding="utf-8")
        except GuardRejected:
            raise
        except (OSError, UnicodeError) as exc:
            raise GuardRejected(
                "replacement target is unreadable: "
                f"{exc.__class__.__name__}"
            ) from None

        try:
            _preflight_bounded_yaml(
                current_text, "current replacement Instance YAML"
            )
        except ResolutionError as exc:
            raise GuardRejected(str(exc)) from None
        try:
            current = yaml.safe_load(current_text)
            candidate = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise GuardRejected(
                "replacement YAML is malformed: "
                f"{exc.__class__.__name__}"
            ) from None
        if not isinstance(current, dict) or not isinstance(candidate, dict):
            raise GuardRejected(
                "replacement documents must be mappings"
            )
        return current, candidate

    @staticmethod
    def _assert_semantic_version_transition(
        instance_root: Path,
        *,
        path: str,
        document_type: str,
        content: str,
    ) -> None:
        version_rule = instance_generic_write_version_rule(document_type)
        if version_rule is None:
            return
        revision_field = version_rule["field"]

        current, candidate = RuntimeSessionBroker._load_transition_documents(
            instance_root, path, content
        )
        current_revision = current.get(revision_field)
        candidate_revision = candidate.get(revision_field)
        ordering = version_rule["ordering"]
        if ordering == "positive_int":
            for label, revision in (
                ("current", current_revision),
                ("candidate", candidate_revision),
            ):
                if (
                    not isinstance(revision, int)
                    or isinstance(revision, bool)
                    or revision < 1
                ):
                    if revision_field == "revision":
                        raise GuardRejected(
                            f"{label} revisioned replacement revision is invalid"
                        )
                    raise GuardRejected(
                        f"{label} replacement {revision_field} is invalid"
                    )
            current_order = (current_revision,)
            candidate_order = (candidate_revision,)
        else:
            raise GuardRejected(
                "deployed Core semantic-version ordering is unsupported"
            )
        if candidate_order <= current_order:
            raise GuardRejected(
                f"replacement must advance semantic {revision_field}"
            )

    @staticmethod
    def _daily_baseline_ids(value: object, label: str) -> frozenset[str]:
        if value is None:
            return frozenset()
        if not isinstance(value, list):
            raise GuardRejected(
                f"{label} locked Daily baseline_objectives must be a list"
            )
        ids = []
        for item in value:
            if isinstance(item, str):
                objective_id = item
            elif isinstance(item, dict):
                objective_id = item.get("id")
            else:
                objective_id = None
            if not isinstance(objective_id, str) or not objective_id.strip():
                raise GuardRejected(
                    f"{label} locked Daily baseline objective lacks an id"
                )
            ids.append(objective_id)
        if len(set(ids)) != len(ids):
            raise GuardRejected(
                f"{label} locked Daily baseline objective ids are duplicated"
            )
        return frozenset(ids)

    @staticmethod
    def _assert_document_transition(
        instance_root: Path,
        *,
        path: str,
        document_type: str,
        content: str,
    ) -> None:
        rule = instance_generic_write_transition_rule(document_type)
        if rule is None:
            return
        if rule != "preserve_locked_daily_baseline_v1":
            raise GuardRejected(
                "deployed Core document-transition rule is unsupported"
            )
        current, candidate = RuntimeSessionBroker._load_transition_documents(
            instance_root, path, content
        )
        if current.get("baseline_locked") is True:
            if candidate.get("baseline_locked") is not True:
                raise GuardRejected(
                    "locked Daily baseline cannot be unlocked by generic replacement"
                )
            current_ids = RuntimeSessionBroker._daily_baseline_ids(
                current.get("baseline_objectives"), "current"
            )
            candidate_ids = RuntimeSessionBroker._daily_baseline_ids(
                candidate.get("baseline_objectives"), "candidate"
            )
            if candidate_ids != current_ids:
                raise GuardRejected(
                    "locked Daily baseline objective membership cannot change"
                )

    def _validate_candidate(
        self,
        state: _LearningRuntimeSessionState,
        *,
        authority_head: str,
        path: str,
        content: str,
        contract: dict,
        instance_snapshot: MaterializedRepository | None = None,
        core_snapshot: MaterializedRepository | None = None,
        allow_create: bool = False,
    ) -> None:
        _preflight_candidate_yaml(content)
        snapshots: list[MaterializedRepository] = []
        try:
            instance = instance_snapshot
            if instance is None:
                instance = self.provider.materialize(
                    state.deployment.instance_repository_id,
                    authority_head,
                )
                snapshots.append(instance)
            if (
                instance.repository_id
                != state.deployment.instance_repository_id
                or instance.commit_sha != authority_head
            ):
                raise GuardRejected(
                    "candidate Instance snapshot provenance changed"
                )
            types = instance_expected_types(path)
            if len(types) != 1:
                raise GuardRejected(
                    "candidate Instance update path is unclassified or ambiguous"
                )
            current_path = _candidate_output_path(instance.root, path)
            creating = allow_create and not current_path.exists()
            if not creating:
                self._assert_semantic_version_transition(
                    instance.root,
                    path=path,
                    document_type=types[0],
                    content=content,
                )
                self._assert_document_transition(
                    instance.root,
                    path=path,
                    document_type=types[0],
                    content=content,
                )
            core = core_snapshot
            if core is None:
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
                    "candidate Core validator provenance changed"
                )
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
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
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
                deployment = self.guard.snapshot(
                    state.deployment, require_active=False
                )
                authority = self._pin_instance_authority(state)
                authority_release_attempted = False
                try:
                    try:
                        content, blob_sha = self.provider.read_materialized_text(
                            authority.snapshot, path
                        )
                    except ResolutionError as exc:
                        raise GuardRejected(
                            f"Instance target read failed closed: {exc}"
                        ) from None
                    # No later step needs the materialized tree. Release it
                    # before returning so cleanup failure still fails closed.
                    authority_release_attempted = True
                    self._release_materializations([authority.snapshot])
                    self._assert_pinned_instance_current(
                        state, authority
                    )
                    self.guard.assert_snapshot_current(
                        state.deployment,
                        deployment,
                        require_active=False,
                    )
                    return InstanceText(
                        content=content, version_token=blob_sha
                    )
                finally:
                    if not authority_release_attempted:
                        self._release_materializations(
                            [authority.snapshot]
                        )

    @staticmethod
    def _normalize_learning_context_paths(
        required_paths: tuple[str, ...] | list[str],
        optional_paths: tuple[str, ...] | list[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not isinstance(required_paths, (tuple, list)):
            raise ResolutionError("required_paths must be a list or tuple")
        if not isinstance(optional_paths, (tuple, list)):
            raise ResolutionError("optional_paths must be a list or tuple")
        if len(required_paths) + len(optional_paths) > LEARNING_CONTEXT_MAX_DOCUMENTS:
            raise ResolutionError(
                "learning context path count exceeds the bounded limit"
            )
        required = tuple(
            _relative_path(path, "required learning context path")
            for path in required_paths
        )
        optional = tuple(
            _relative_path(path, "optional learning context path")
            for path in optional_paths
        )
        combined = required + optional
        if not combined:
            raise ResolutionError("learning context requires at least one path")
        if len(set(combined)) != len(combined):
            raise ResolutionError(
                "learning context paths must be unique across required and optional sets"
            )
        return required, optional

    @staticmethod
    def _snapshot_path_inventory(snapshot) -> frozenset[str]:
        if snapshot.blob_shas is not None:
            return frozenset(path for path, _ in snapshot.blob_shas)
        if snapshot.paths is not None:
            return frozenset(snapshot.paths)
        raise GuardRejected(
            "materialized Instance snapshot lacks immutable path inventory"
        )

    def read_learning_context(
        self,
        session: LearningRuntimeSession,
        *,
        required_paths: tuple[str, ...] | list[str],
        optional_paths: tuple[str, ...] | list[str] = (),
    ) -> LearningContextBundle:
        """Read a bounded set of Instance documents from one exact authority snapshot."""
        required, optional = self._normalize_learning_context_paths(
            required_paths, optional_paths
        )
        optional_set = frozenset(optional)
        with self._session_operation(session) as state:
            for path in required + optional:
                if not state.policy.may_read(path):
                    raise GuardRejected(
                        "learning context read is outside the session capability policy"
                    )
            if self.write_admission is None:
                raise GuardRejected(
                    "learning session requires shared deployment operation admission"
                )
            with self.write_admission.read_lease():
                deployment = self.guard.snapshot(
                    state.deployment, require_active=False
                )
                authority = self._pin_instance_authority(state)
                authority_release_attempted = False
                try:
                    inventory = self._snapshot_path_inventory(
                        authority.snapshot
                    )
                    documents: list[tuple[str, InstanceText]] = []
                    missing_optional: list[str] = []
                    for path in required + optional:
                        if path not in inventory:
                            if path in optional_set:
                                missing_optional.append(path)
                                continue
                            raise GuardRejected(
                                f"required learning context path is missing: {path}"
                            )
                        try:
                            content, blob_sha = self.provider.read_materialized_text(
                                authority.snapshot, path
                            )
                        except ResolutionError as exc:
                            raise GuardRejected(
                                f"learning context read failed closed for {path}: {exc}"
                            ) from None
                        documents.append(
                            (path, InstanceText(content=content, version_token=blob_sha))
                        )
                    authority_release_attempted = True
                    self._release_materializations([authority.snapshot])
                    self._assert_pinned_instance_current(state, authority)
                    self.guard.assert_snapshot_current(
                        state.deployment,
                        deployment,
                        require_active=False,
                    )
                    return LearningContextBundle(
                        documents=tuple(documents),
                        missing_optional=tuple(missing_optional),
                    )
                finally:
                    if not authority_release_attempted:
                        self._release_materializations([authority.snapshot])

    @staticmethod
    def _type_sensitive_semantic_key(value: object) -> tuple:
        """Build an order-independent typed key for bounded parsed YAML."""
        if value is None:
            return ("null",)
        if isinstance(value, bool):
            return ("bool", value)
        if isinstance(value, int):
            return ("int", value)
        if isinstance(value, float):
            # Preserve Python/YAML equality semantics without stringifying
            # arbitrarily large numeric values. NaN needs one stable key,
            # while signed zero compares equal by design.
            if value != value:
                return ("float-nan",)
            return ("float", 0.0 if value == 0.0 else value)
        if isinstance(value, str):
            return ("str", value)
        if isinstance(value, bytes):
            return ("bytes", value)
        if isinstance(value, datetime_module.datetime):
            offset = value.utcoffset() if value.tzinfo is not None else None
            if offset is None:
                return ("datetime-naive", value.isoformat())
            local_microseconds = (
                (
                    value.toordinal() * 86400
                    + value.hour * 3600
                    + value.minute * 60
                    + value.second
                )
                * 1_000_000
                + value.microsecond
            )
            offset_microseconds = (
                (offset.days * 86400 + offset.seconds) * 1_000_000
                + offset.microseconds
            )
            return (
                "datetime-aware",
                local_microseconds - offset_microseconds,
            )
        if isinstance(value, datetime_module.date):
            return ("date", value.isoformat())
        if isinstance(value, dict):
            return (
                "dict",
                frozenset(
                    (
                        RuntimeSessionBroker._type_sensitive_semantic_key(key),
                        RuntimeSessionBroker._type_sensitive_semantic_key(item),
                    )
                    for key, item in value.items()
                ),
            )
        if isinstance(value, (list, tuple)):
            sequence_type = "list" if type(value) is list else "tuple"
            return (
                sequence_type,
                tuple(
                    RuntimeSessionBroker._type_sensitive_semantic_key(item)
                    for item in value
                ),
            )
        if isinstance(value, (set, frozenset)):
            set_type = "set" if type(value) is set else "frozenset"
            return (
                set_type,
                frozenset(
                    RuntimeSessionBroker._type_sensitive_semantic_key(item)
                    for item in value
                ),
            )
        raise GuardRejected(
            "Evidence semantic comparison encountered an unsupported YAML scalar type"
        )

    @staticmethod
    def _type_sensitive_semantic_equal(left: object, right: object) -> bool:
        """Compare bounded YAML semantics without bool/int aliasing or O(n^2) maps."""
        return (
            RuntimeSessionBroker._type_sensitive_semantic_key(left)
            == RuntimeSessionBroker._type_sensitive_semantic_key(right)
        )

    @staticmethod
    def _evidence_candidate(content: str) -> tuple[dict, str]:
        _preflight_candidate_yaml(content)
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise GuardRejected(
                f"Evidence YAML is malformed: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict):
            raise GuardRejected("Evidence candidate must be a mapping")
        if data.get("document_type") != "evidence":
            raise GuardRejected("Evidence create requires document_type evidence")
        evidence_id = data.get("id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise GuardRejected("Evidence create requires a non-empty id")
        path = _relative_path(
            f"evidence/{evidence_id}.yaml", "Evidence canonical path"
        )
        if instance_expected_types(path) != ("evidence",):
            raise GuardRejected(
                "Evidence id does not map to one canonical Evidence path"
            )
        return data, path

    def create_evidence(
        self,
        session: LearningRuntimeSession,
        *,
        content: str,
        message: str,
    ) -> InstanceWriteAck:
        """Create one immutable Evidence record under exact deployment/head CAS."""
        candidate, path = self._evidence_candidate(content)
        with self._session_operation(session) as state:
            if not state.policy.may_write(path):
                raise GuardRejected(
                    "Evidence create is outside the session capability policy"
                )
            if self.write_admission is None:
                raise GuardRejected(
                    "writable learning session requires shared deployment write admission"
                )
            with self.write_admission.write_lease():
                deployment = self.guard.snapshot(state.deployment)
                authority = self._pin_instance_authority(state)
                core = None
                release_attempted = False
                try:
                    existing_path = _candidate_output_path(
                        authority.snapshot.root, path
                    )
                    existing_matches = False
                    if existing_path.exists():
                        try:
                            existing_text, _ = self.provider.read_materialized_text(
                                authority.snapshot, path
                            )
                            _preflight_candidate_yaml(existing_text)
                            existing = yaml.safe_load(existing_text)
                        except (ResolutionError, yaml.YAMLError) as exc:
                            raise GuardRejected(
                                "existing Evidence record is unreadable"
                            ) from exc
                        if not self._type_sensitive_semantic_equal(
                            existing, candidate
                        ):
                            raise GuardRejected(
                                "Evidence id already exists with different content"
                            )
                        existing_matches = True

                    core = self.provider.materialize(
                        state.deployment.core_repository_id,
                        state.deployment.core_commit,
                    )
                    if (
                        core.repository_id
                        != state.deployment.core_repository_id
                        or core.commit_sha
                        != state.deployment.core_commit
                    ):
                        raise GuardRejected(
                            "deployed Core provenance changed during Evidence create"
                        )
                    self._assert_deployed_write_policy_compatible(
                        state, core_snapshot=core
                    )
                    self._validate_candidate(
                        state,
                        authority_head=authority.head,
                        path=path,
                        content=content,
                        contract=deployment.contract,
                        instance_snapshot=authority.snapshot,
                        core_snapshot=core,
                    )
                    self._assert_pinned_instance_current(state, authority)
                    self.guard.assert_snapshot_current(
                        state.deployment, deployment
                    )
                    release_attempted = True
                    self._release_materializations(
                        [core, authority.snapshot]
                    )
                    if existing_matches:
                        return InstanceWriteAck(applied=False)
                    try:
                        self.provider.create_text(
                            state.deployment.instance_repository_id,
                            state.binding.instance_ref,
                            path,
                            content,
                            message,
                            expected_ref_sha=authority.head,
                        )
                    except CasConflict:
                        raise
                    except Exception as exc:
                        raise CasConflict(
                            f"Instance create-only compare-and-swap failed: {exc}"
                        ) from None
                finally:
                    if not release_attempted:
                        pending = [
                            snapshot
                            for snapshot in (core, authority.snapshot)
                            if snapshot is not None
                        ]
                        if pending:
                            self._release_materializations(pending)
            return InstanceWriteAck()

    @staticmethod
    def _knowledge_candidate(content: str) -> tuple[dict, str]:
        _preflight_candidate_yaml(content)
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise GuardRejected(
                f"Knowledge YAML is malformed: {exc.__class__.__name__}"
            ) from None
        if not isinstance(data, dict):
            raise GuardRejected("Knowledge candidate must be a mapping")
        if data.get("document_type") != "learner_knowledge":
            raise GuardRejected(
                "Knowledge reconciliation requires document_type learner_knowledge"
            )
        domain = data.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise GuardRejected(
                "Knowledge reconciliation requires a non-empty domain"
            )
        path = _relative_path(
            f"learner/knowledge/{domain}.yaml",
            "Knowledge canonical path",
        )
        if instance_expected_types(path) != ("learner_knowledge",):
            raise GuardRejected(
                "Knowledge domain does not map to one canonical Knowledge path"
            )
        return data, path

    @staticmethod
    def _knowledge_evidence_ref_map(
        document: dict,
        *,
        label: str,
        allow_legacy_duplicates: bool = False,
    ) -> dict[tuple[str, str, str, str], tuple[str, ...]]:
        domain = document.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise GuardRejected(f"{label} Knowledge domain is invalid")
        concepts = document.get("concepts")
        if not isinstance(concepts, dict):
            raise GuardRejected(f"{label} Knowledge concepts must be a mapping")
        result: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
        for concept, concept_record in concepts.items():
            if not isinstance(concept, str) or not concept.strip():
                raise GuardRejected(
                    f"{label} Knowledge concept identity is invalid"
                )
            if not isinstance(concept_record, dict):
                raise GuardRejected(
                    f"{label} Knowledge concept record must be a mapping"
                )
            capabilities = concept_record.get("capabilities", {})
            if not isinstance(capabilities, dict):
                raise GuardRejected(
                    f"{label} Knowledge capabilities must be a mapping"
                )
            for capability, capability_record in capabilities.items():
                if not isinstance(capability, str) or not capability.strip():
                    raise GuardRejected(
                        f"{label} Knowledge capability identity is invalid"
                    )
                if not isinstance(capability_record, dict):
                    raise GuardRejected(
                        f"{label} Knowledge capability record must be a mapping"
                    )
                refs = capability_record.get("evidence_refs", {})
                if refs is None:
                    refs = {}
                if not isinstance(refs, dict):
                    raise GuardRejected(
                        f"{label} Knowledge evidence_refs must be a mapping"
                    )
                for side in ("support", "challenge"):
                    values = refs.get(side, [])
                    if values is None:
                        values = []
                    if (
                        not isinstance(values, list)
                        or any(
                            not isinstance(item, str) or not item.strip()
                            for item in values
                        )
                    ):
                        raise GuardRejected(
                            f"{label} Knowledge {side} evidence refs must be non-empty strings"
                        )
                    if len(set(values)) != len(values):
                        if not allow_legacy_duplicates:
                            raise GuardRejected(
                                f"{label} Knowledge {side} evidence refs are duplicated"
                            )
                        values = list(dict.fromkeys(values))
                    result[(domain, concept, capability, side)] = tuple(values)
        return result

    def _assert_new_knowledge_evidence_relevant(
        self,
        *,
        state: _LearningRuntimeSessionState,
        snapshot: MaterializedRepository,
        current: dict,
        candidate: dict,
    ) -> None:
        current_refs = self._knowledge_evidence_ref_map(
            current,
            label="current",
            allow_legacy_duplicates=True,
        )
        candidate_refs = self._knowledge_evidence_ref_map(
            candidate, label="candidate"
        )
        pending: list[tuple[tuple[str, str, str, str], str]] = []
        for key, refs in candidate_refs.items():
            previous = frozenset(current_refs.get(key, ()))
            for evidence_id in refs:
                if evidence_id not in previous:
                    pending.append((key, evidence_id))
        if len(pending) > KNOWLEDGE_RECONCILE_MAX_NEW_EVIDENCE_REFS:
            raise GuardRejected(
                "Knowledge reconciliation adds too many new Evidence references"
            )

        evidence_cache: dict[str, dict] = {}
        for key, evidence_id in pending:
            domain, concept, capability, side = key
            evidence_path = _relative_path(
                f"evidence/{evidence_id}.yaml",
                "Knowledge Evidence reference",
            )
            if instance_expected_types(evidence_path) != ("evidence",):
                raise GuardRejected(
                    "Knowledge Evidence reference is not a canonical Evidence path"
                )
            if not state.policy.may_read(evidence_path):
                raise GuardRejected(
                    "Knowledge Evidence reference is outside the session read capability policy"
                )
            evidence = evidence_cache.get(evidence_id)
            if evidence is None:
                try:
                    evidence_text, _ = self.provider.read_materialized_text(
                        snapshot, evidence_path
                    )
                    _preflight_candidate_yaml(evidence_text)
                    evidence = yaml.safe_load(evidence_text)
                except (ResolutionError, yaml.YAMLError) as exc:
                    raise GuardRejected(
                        f"Knowledge Evidence reference {evidence_id!r} is unreadable"
                    ) from exc
                if not isinstance(evidence, dict):
                    raise GuardRejected(
                        f"Knowledge Evidence reference {evidence_id!r} has invalid identity"
                    )
                evidence_cache[evidence_id] = evidence
            if (
                evidence.get("document_type") != "evidence"
                or evidence.get("id") != evidence_id
            ):
                raise GuardRejected(
                    f"Knowledge Evidence reference {evidence_id!r} has invalid identity"
                )
            expected_target = {
                "type": "capability",
                "domain": domain,
                "concept": concept,
                "capability": capability,
            }
            targets = evidence.get("targets")
            if not isinstance(targets, list) or not any(
                isinstance(target, dict)
                and all(
                    target.get(field) == value
                    for field, value in expected_target.items()
                )
                for target in targets
            ):
                raise GuardRejected(
                    f"Knowledge Evidence reference {evidence_id!r} does not exactly target "
                    f"{domain}/{concept}/{capability}"
                )
            interpretation = evidence.get("interpretation")
            direction = (
                interpretation.get("direction")
                if isinstance(interpretation, dict)
                else None
            )
            expected_direction = (
                "support" if side == "support" else "challenge"
            )
            if direction != expected_direction:
                raise GuardRejected(
                    f"Knowledge Evidence reference {evidence_id!r} direction "
                    f"{direction!r} cannot populate {side}"
                )

    def reconcile_knowledge(
        self,
        session: LearningRuntimeSession,
        *,
        content: str,
        expected_blob_sha: str | None,
        message: str,
    ) -> InstanceWriteAck:
        """Create/update one Knowledge owner after validating newly cited Evidence."""
        candidate, path = self._knowledge_candidate(content)
        with self._session_operation(session) as state:
            if not state.policy.may_write(path):
                raise GuardRejected(
                    "Knowledge reconciliation is outside the session capability policy"
                )
            if self.write_admission is None:
                raise GuardRejected(
                    "writable learning session requires shared deployment write admission"
                )
            with self.write_admission.write_lease():
                deployment = self.guard.snapshot(state.deployment)
                authority = self._pin_instance_authority(state)
                core = None
                release_attempted = False
                try:
                    current_path = _candidate_output_path(
                        authority.snapshot.root, path
                    )
                    creating = not current_path.exists()
                    if creating:
                        if expected_blob_sha is not None:
                            raise CasConflict(
                                "first Knowledge materialization requires no expected blob"
                            )
                        revision = candidate.get("revision")
                        if (
                            not isinstance(revision, int)
                            or isinstance(revision, bool)
                            or revision != 1
                        ):
                            raise GuardRejected(
                                "first Knowledge materialization must use revision 1"
                            )
                        current = {
                            "domain": candidate.get("domain"),
                            "concepts": {},
                        }
                    else:
                        if expected_blob_sha is None:
                            raise CasConflict(
                                "existing Knowledge reconciliation requires expected blob SHA"
                            )
                        try:
                            current_text, current_blob_sha = (
                                self.provider.read_materialized_text(
                                    authority.snapshot, path
                                )
                            )
                            _preflight_candidate_yaml(current_text)
                            current = yaml.safe_load(current_text)
                        except (ResolutionError, yaml.YAMLError) as exc:
                            raise GuardRejected(
                                "current Knowledge owner is unreadable"
                            ) from exc
                        if not isinstance(current, dict):
                            raise GuardRejected(
                                "current Knowledge owner must be a mapping"
                            )
                        if current_blob_sha != expected_blob_sha:
                            raise CasConflict(
                                "Knowledge target blob compare-and-swap mismatch"
                            )

                    self._assert_new_knowledge_evidence_relevant(
                        state=state,
                        snapshot=authority.snapshot,
                        current=current,
                        candidate=candidate,
                    )
                    core = self.provider.materialize(
                        state.deployment.core_repository_id,
                        state.deployment.core_commit,
                    )
                    if (
                        core.repository_id
                        != state.deployment.core_repository_id
                        or core.commit_sha
                        != state.deployment.core_commit
                    ):
                        raise GuardRejected(
                            "deployed Core provenance changed during Knowledge reconciliation"
                        )
                    self._assert_deployed_write_policy_compatible(
                        state, core_snapshot=core
                    )
                    self._validate_candidate(
                        state,
                        authority_head=authority.head,
                        path=path,
                        content=content,
                        contract=deployment.contract,
                        instance_snapshot=authority.snapshot,
                        core_snapshot=core,
                        allow_create=creating,
                    )
                    self._assert_pinned_instance_current(state, authority)
                    self.guard.assert_snapshot_current(
                        state.deployment, deployment
                    )
                    release_attempted = True
                    self._release_materializations(
                        [core, authority.snapshot]
                    )
                    try:
                        if creating:
                            self.provider.create_text(
                                state.deployment.instance_repository_id,
                                state.binding.instance_ref,
                                path,
                                content,
                                message,
                                expected_ref_sha=authority.head,
                            )
                        else:
                            self.provider.update_text(
                                state.deployment.instance_repository_id,
                                state.binding.instance_ref,
                                path,
                                content,
                                expected_blob_sha,
                                message,
                                expected_ref_sha=authority.head,
                            )
                    except CasConflict:
                        raise
                    except Exception as exc:
                        raise CasConflict(
                            f"Knowledge compare-and-swap failed: {exc}"
                        ) from None
                finally:
                    if not release_attempted:
                        pending = [
                            snapshot
                            for snapshot in (core, authority.snapshot)
                            if snapshot is not None
                        ]
                        if pending:
                            self._release_materializations(pending)
            return InstanceWriteAck()

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
                deployment = self.guard.snapshot(state.deployment)

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
                if write_mode == "knowledge_transition":
                    raise GuardRejected(
                        "ordinary learning session must use the dedicated Knowledge "
                        "reconciliation operation"
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

                authority = self._pin_instance_authority(state)
                core = None
                release_attempted = False
                try:
                    core = self.provider.materialize(
                        state.deployment.core_repository_id,
                        state.deployment.core_commit,
                    )
                    if (
                        core.repository_id
                        != state.deployment.core_repository_id
                        or core.commit_sha
                        != state.deployment.core_commit
                    ):
                        raise GuardRejected(
                            "deployed Core provenance changed during operation"
                        )
                    self._assert_deployed_write_policy_compatible(
                        state, core_snapshot=core
                    )
                    self._validate_candidate(
                        state,
                        authority_head=authority.head,
                        path=path,
                        content=content,
                        contract=deployment.contract,
                        instance_snapshot=authority.snapshot,
                        core_snapshot=core,
                    )
                    self._assert_pinned_instance_current(
                        state, authority
                    )
                    self.guard.assert_snapshot_current(
                        state.deployment, deployment
                    )

                    # Cleanup is part of the pre-write fence: a failure here
                    # must reject before canonical mutation, not create an
                    # ambiguous "write applied but cleanup failed" result.
                    release_attempted = True
                    self._release_materializations(
                        [core, authority.snapshot]
                    )

                    try:
                        self.provider.update_text(
                            state.deployment.instance_repository_id,
                            state.binding.instance_ref,
                            path,
                            content,
                            expected_blob_sha,
                            message,
                            expected_ref_sha=authority.head,
                        )
                    except CasConflict:
                        raise
                    except Exception as exc:
                        raise CasConflict(
                            f"Instance compare-and-swap failed: {exc}"
                        ) from None
                finally:
                    if not release_attempted:
                        pending = [
                            snapshot
                            for snapshot in (core, authority.snapshot)
                            if snapshot is not None
                        ]
                        if pending:
                            self._release_materializations(pending)
            return InstanceWriteAck()
