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
    validate_instance_document_trust_boundary,
)

BRANCH_RUNTIME_SCHEMA_VERSION = "0.3"
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
IMMUTABLE_UPDATE_TYPES = frozenset({
    "evidence",
    "execution_session",
    "coordination_event",
    "learning_handoff",
})
PROTOCOL_GOVERNED_UPDATE_TYPES = frozenset({
    "branch_registry",
    "conversation_sequence_registry",
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


class DeploymentWriteAdmission(Protocol):
    """Host admission authority shared by broker writes and promotion."""

    def write_lease(self) -> ContextManager[None]: ...

    def promotion_barrier(self) -> ContextManager[None]: ...


class DeploymentWriteGate:
    """Reference same-process admission/drain gate for Runtime writes.

    Multi-process hosts must provide an equivalent cross-process implementation.
    The promotion path must hold promotion_barrier() across the entire
    freeze/promotion/activation transaction.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._promotion_lock = threading.Lock()
        self._accepting = True
        self._holders = 0

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
                self._accepting = False
                while self._holders:
                    self._condition.wait()
            yield
        finally:
            with self._condition:
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
        *,
        write_admission: DeploymentWriteAdmission | None = None,
    ):
        self.provider = provider
        self.locator = load_locator(locator_source)
        self.guard = DeploymentGuard(provider)
        self.write_admission = write_admission

    @staticmethod
    def _parse_branch_runtime(text: str) -> dict:
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
        for key, value in generations.items():
            if not isinstance(value, dict):
                raise ResolutionError(
                    "Branch runtime generation record is invalid"
                )
            lifecycle = value.get("lifecycle")
            if lifecycle not in BRANCH_GENERATION_LIFECYCLES:
                raise ResolutionError(
                    "Branch runtime generation lifecycle is invalid"
                )
            if lifecycle != "active":
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
        return runtime, commit_sha

    def open_session(
        self,
        *,
        branch_runtime_path: str,
        policy: RuntimeCapabilityPolicy,
        expected_generation: int | None = None,
    ) -> LearningRuntimeSession:
        resolved = DeploymentResolver(self.provider).resolve(self.locator)
        try:
            deployment = resolved.context
        finally:
            for snapshot in (
                resolved.instance,
                resolved.core,
                resolved.control,
            ):
                self.provider.release_materialization(snapshot)
        instance_ref = self.locator["instance"]["canonical_ref"]
        runtime_path = _relative_path(branch_runtime_path, "branch_runtime_path")
        if policy.writable_roots and self.write_admission is None:
            raise GuardRejected(
                "writable learning session requires shared deployment write admission"
            )
        if policy.writable_roots and expected_generation is None:
            raise GuardRejected(
                "writable learning session requires an established generation"
            )
        runtime, _ = self._read_branch_runtime(
            instance_repository_id=deployment.instance_repository_id,
            instance_ref=instance_ref,
            runtime_path=runtime_path,
        )
        generation = runtime["active_generation"]
        if expected_generation is not None and generation != expected_generation:
            raise GuardRejected("requested learning generation is not active")
        return LearningRuntimeSession(
            deployment=deployment,
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
        self.guard.check(session.deployment, require_active=False)
        generation, authority_head = self._fresh_generation(session)
        if generation != session.binding.generation:
            raise GuardRejected("semantic generation changed")
        return authority_head

    def _validate_candidate(
        self,
        session: LearningRuntimeSession,
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
                session.deployment.instance_repository_id,
                authority_head,
            )
            snapshots.append(instance)
            core = self.provider.materialize(
                session.deployment.core_repository_id,
                session.deployment.core_commit,
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
                candidate_path = root.joinpath(
                    *PurePosixPath(path).parts
                )
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
            for snapshot in reversed(snapshots):
                self.provider.release_materialization(snapshot)

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
        types = instance_expected_types(path)
        if len(types) != 1:
            raise GuardRejected(
                "Instance update path is unclassified or ambiguous"
            )
        document_type = types[0]
        if document_type == "branch_runtime":
            raise GuardRejected(
                "ordinary learning session cannot mutate Branch runtime authority"
            )
        if document_type in IMMUTABLE_UPDATE_TYPES:
            raise GuardRejected(
                "ordinary learning session cannot overwrite immutable/create-only "
                f"{document_type} records"
            )
        if document_type in PROTOCOL_GOVERNED_UPDATE_TYPES:
            raise GuardRejected(
                "ordinary learning session must use the dedicated transition "
                f"operation for {document_type}"
            )
        if not session.policy.may_write(path):
            raise GuardRejected("Instance write is outside the session capability policy")
        if self.write_admission is None:
            raise GuardRejected(
                "writable learning session requires shared deployment write admission"
            )
        with self.write_admission.write_lease():
            contract = self.guard.check(session.deployment)
            generation, authority_head = self._fresh_generation(session)
            if generation != session.binding.generation:
                raise GuardRejected("semantic generation changed")
            self._validate_candidate(
                session,
                authority_head=authority_head,
                path=path,
                content=content,
                contract=contract,
            )

            # The shared lease prevents a conforming promotion path from
            # closing/finalizing Runtime-Control while validation is in flight.
            # Re-read both authorities after validation as an additional
            # fail-closed check against an external actor that ignored the gate.
            self.guard.check(session.deployment)
            final_generation, final_authority_head = self._fresh_generation(
                session
            )
            if final_generation != session.binding.generation:
                raise GuardRejected("semantic generation changed")
            if final_authority_head != authority_head:
                raise GuardRejected("Instance authority head changed")

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
                raise CasConflict(
                    f"Instance compare-and-swap failed: {exc}"
                ) from None
        return InstanceWriteAck()
