"""Synthetic-only trusted first Topic/Main owning transition.

This is a host bootstrap surface, never an eighth ordinary Runtime operation.
The reference authority, receipt store and local Git transport are test adapters;
production authentication, persistent permissions and deployment are not supplied.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import threading
from typing import Any, Callable

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import (
    CasConflict, DeploymentGuard, GuardRejected, PreparedFirstUseStep,
    ResolutionError, SessionDeploymentContext, SyntheticPreparedGitProvider,
    _load_contract, load_locator,
)
from scripts.runtime_broker import (
    CANDIDATE_YAML_MAX_BYTES, RuntimeSessionBroker,
    _compute_bounded_write_policy_fingerprint, _run_pinned_core_validator,
)
from scripts.validate_learning_os import (
    instance_write_policy_fingerprint,
    validate_deployment_contract_document,
)

FIRST_USE_VERSION = "trusted-nine-file-prepared-v1"


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


class FirstUseRejected(GuardRejected):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utf8_size(value, rejection_code):
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise FirstUseRejected(rejection_code) from None


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise FirstUseRejected("invalid_intent")
    SyntheticPreparedGitProvider._safe_path(value)
    return value


@dataclass(frozen=True)
class FirstUseIntent:
    """Typed proposal: no YAML, path, repository, generation or capability input.

    This first synthetic slice uses the existing prediction/context curriculum.
    A different teaching domain needs its own reviewed template, not arbitrary
    serialized learner state through this entry.
    """
    topic: str
    title: str
    goal: str
    unit: str = "prediction-and-context"

    def __post_init__(self):
        _slug(self.topic)
        _slug(self.unit)
        for value in (self.title, self.goal):
            if not isinstance(value, str) or not value.strip() or _utf8_size(value, "invalid_intent") > 4096:
                raise FirstUseRejected("invalid_intent")


@dataclass(frozen=True)
class SyntheticReceiver:
    token: str


class SyntheticFirstUseAuthority:
    """Trusted synthetic auth/revocation fixture, independent of Host restarts.

    Calling authorize is a fixture-owner action, not an endpoint for learners.
    Token, receipt key and writer handles never enter canonical state/receipts.
    The lock models grant lease/drain in one process. Production multi-process
    revocation and persistent credential creation are explicitly unsupported.
    """
    def __init__(self):
        self.integrity_key = secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._grants = {}
        self._sessions = {}
        self._revocations = {}

    def authorize(self, *, principal, surface, operation_id, intent, locator):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", operation_id):
            raise FirstUseRejected("invalid_operation")
        if not all(isinstance(x, str) and 0 < len(x) <= 128 for x in (principal, surface)):
            raise FirstUseRejected("invalid_receiver")
        for value in (principal, surface):
            _utf8_size(value, "invalid_receiver")
        grant = dict(principal=principal, surface=surface, operation_id=operation_id,
                     intent_digest=_digest(asdict(intent)), locator_digest=_digest(load_locator(locator)),
                     version=FIRST_USE_VERSION)
        receiver = SyntheticReceiver(secrets.token_urlsafe(32))
        with self._lock:
            self._grants[receiver.token] = grant
        return receiver

    def check(self, receiver, binding):
        with self._lock:
            if (not isinstance(receiver, SyntheticReceiver) or
                    self._grants.get(receiver.token) != binding):
                raise FirstUseRejected("authorization_denied")

    def binding(self, receiver):
        with self._lock:
            if not isinstance(receiver, SyntheticReceiver) or receiver.token not in self._grants:
                raise FirstUseRejected("authorization_denied")
            return dict(self._grants[receiver.token])

    @contextmanager
    def lease(self, receiver, binding):
        with self._lock:
            self.check(receiver, binding)
            yield

    def revoke(self, receiver):
        with self._lock:
            token = getattr(receiver, "token", None)
            grant = self._grants.pop(token, None)
            if grant is not None:
                self._revocations[token] = grant
            pending = self._revocations.get(token)
            if pending is not None:
                key = _digest(pending)
                entry = self._sessions.get(key)
                if entry is not None:
                    # Retain the drain responsibility if close fails. A retry
                    # must not report success while an old writer still exists.
                    entry.close()
                self._sessions.pop(key, None)
                self._revocations.pop(token, None)


class SyntheticFirstUseStore:
    """Bounded integrity-protected private receipts + atomic target-slot CAS.

    The root belongs to one trusted installation, with one first-use slot.
    File locks serialize same-op calls across store objects/processes. An orphan
    lock is not stolen: a crashed process requires independently proven drain.
    HMAC key availability is provided by the synthetic authority, not by receipt
    content. This is adapter-restart evidence, not a production key store.
    """
    LIMIT = 1024 * 1024

    def __init__(self, root: Path, integrity_key: bytes):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or len(integrity_key) < 32:
            raise FirstUseRejected("invalid_store")
        self._key = integrity_key

    def _read(self, name):
        path = self.root / name
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file() or path.stat().st_size > self.LIMIT:
            raise FirstUseRejected("receipt_unavailable")
        try:
            wrapper = json.loads(path.read_bytes())
            data = wrapper["data"]
            expected = hmac.new(self._key, _json(data), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, wrapper["mac"]):
                raise ValueError()
            return data
        except (OSError, ValueError, TypeError, KeyError):
            raise FirstUseRejected("receipt_integrity_failed") from None

    def _encoded(self, data):
        raw = _json(dict(data=data, mac=hmac.new(self._key, _json(data), hashlib.sha256).hexdigest()))
        if len(raw) > self.LIMIT:
            raise FirstUseRejected("receipt_oversized")
        return raw

    def _write(self, name, data):
        SyntheticPreparedGitProvider._first_use_atomic(self.root / name, self._encoded(data))

    def preflight_receipt(self, receipt):
        # Ten future descriptors plus wrapper/status growth must also fit.
        if len(self._encoded(receipt)) + 10 * (8192 + 256) + 1024 > self.LIMIT:
            raise FirstUseRejected("receipt_oversized")

    @contextmanager
    def operation(self):
        lock = self.root / "operation.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise FirstUseRejected("operation_busy") from None
        os.close(fd)
        try:
            yield
        finally:
            lock.unlink()

    def inspect_slot(self, slot, binding):
        current = self._read("slot.json")
        if current is not None and (current["slot"] != slot or current["binding"] != binding):
            raise FirstUseRejected("target_slot_conflict")
        return current

    def reserve(self, slot, binding):
        current = self.inspect_slot(slot, binding)
        if current is None:
            # operation.lock covers creation; slot is never released on failure.
            current = dict(slot=slot, binding=binding, initialized=False)
            self._write("slot.json", current)
        if current["slot"] != slot or current["binding"] != binding:
            raise FirstUseRejected("target_slot_conflict")
        return current

    def read(self):
        return self._read("receipt.json")

    def write(self, receipt):
        self._write("receipt.json", receipt)

    def mark_initialized(self):
        slot = self._read("slot.json")
        slot["initialized"] = True
        self._write("slot.json", slot)


def _documents(intent, *, stamp, lineage):
    t, u = intent.topic, intent.unit
    tp, sp = f"topics/{t}", f"topics/{t}/subtopics/{u}"
    curriculum = [{"domain": "modern-language-models", "curriculum_version": "0.2"}]
    def document(kind, **fields):
        return dict(schema_version="0.3", document_type=kind, updated_at=stamp, **fields)
    criteria = ["Explain next-token prediction using a short sentence.",
                "Compare two contexts and explain why a likely token need not be a true fact."]
    milestones = [
        dict(id="next-token", title="Next-token prediction", exit_criteria=[criteria[0]],
             curriculum_refs=[dict(type="curriculum_node", domain="modern-language-models",
                                  id="language_modeling.next_token_prediction")]),
        dict(id="tokens-context", title="Tokens, context and limits", exit_criteria=[criteria[1]],
             curriculum_refs=[dict(type="curriculum_node", domain="modern-language-models",
                                  id="representation.tokens_and_context")]),
    ]
    docs = [
        ("config/instance.yaml", dict(schema_version="0.4", document_type="instance_config",
            updated_at=stamp, product={"id": "learning-os"}, instance={"display_timezone": "Etc/UTC"}, nonproduction=True)),
        (f"{tp}/goal.yaml", document("topic_goal", revision=1, topic={"id": t, "title": intent.title},
            goal=dict(purpose={"value": intent.goal, "authority": "user_authoritative"},
                      horizon={"type": None, "deadline": None}, depth_profile={},
                      success_criteria=criteria, constraints={}, preferences={"include": [], "avoid": []}))),
        (f"{tp}/plan.yaml", document("topic_plan", revision=1, topic=t,
            plan=dict(status="provisional", intake={"status": "sufficient", "gaps": []},
                      based_on={"goal_revision": 1, "curricula": curriculum},
                      milestones=[dict(id="explain-boundaries", title=intent.title, objective=intent.goal,
                                       exit_criteria=criteria, suggested_subtopic=u)],
                      suggested_sequence=["explain-boundaries"], replanning_triggers=[]))),
        (f"{tp}/progress.yaml", document("topic_progress", revision=1, topic=t, plan_revision=1,
            lifecycle="active", milestones={"explain-boundaries": {"status": "planned"}},
            active_subtopics=[u], blockers=[], watch=[],
            resume={"primary_subtopic": u})),
        (f"{sp}/plan.yaml", document("subtopic_plan", revision=1, topic=t, subtopic=u,
            plan=dict(status="provisional", based_on={"topic_plan_revision": 1, "curricula": curriculum},
                      objective=intent.goal, milestones=milestones,
                      suggested_sequence=["next-token", "tokens-context"]))),
        (f"{sp}/progress.yaml", document("subtopic_progress", revision=1, topic=t, subtopic=u,
            plan_revision=1, milestones={key: {"status": "planned"} for key in ("next-token", "tokens-context")},
            current={"milestone": ["next-token"]}, blockers=[],
            resume={"return_point": None, "ready_next": [criteria[0]]}, watch=[], avoid_retesting=[])),
        (f"{sp}/definition.yaml", document("subtopic_definition",
            subtopic=dict(id=u, topic=t, title="Prediction and context examples", kind="standard",
                          lifecycle="active", source_domains=["modern-language-models"], replaced_by=[]))),
        (f"{tp}/coordination/branches.yaml", document("branch_registry", revision=1, topic=t,
            branches={"main": {"role": "main", "subtopic": u, "lifecycle": "idle"}})),
        (f"{tp}/coordination/branches/main/runtime.yaml", document("branch_runtime", revision=1,
            topic=t, branch_id="main", lineage_id=lineage, active_generation=1,
            pending_successor=None, generations={1: {"lifecycle": "active"}})),
    ]
    return [[path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True)] for path, data in docs]


def _activation_document(documents):
    path, original = documents[7]
    active = yaml.safe_load(original)
    active["revision"] = 2
    active["branches"]["main"]["lifecycle"] = "active"
    return path, yaml.safe_dump(active, sort_keys=False, allow_unicode=True)


STARTER_SETUP_STATES = frozenset({"not_established", "established", "unknown"})
_ESTABLISHED_RESULT_STATUSES = frozenset({"active", "already_applied", "delivery_pending", "activated_unissued"})
_UNKNOWN_RESULT_STATUSES = frozenset({"outcome_unknown", "conflict"})
_PREPARED_RESULT_STATUSES = frozenset({"prepared", "applied", "not_applied"})


def _bound_progress_path(host: object) -> str | None:
    """Return the bound Main Subtopic progress path if host holds an active Main session."""
    if not isinstance(host, ReferenceLearningHost) or getattr(host, "_closed", False):
        return None
    try:
        state = host._broker._session_state(host._session)
    except GuardRejected:
        return None
    if state.binding.role != "main" or not state.binding.subtopic:
        return None
    return f"topics/{state.binding.topic}/subtopics/{state.binding.subtopic}/progress.yaml"


def _host_can_save_checkpoint(host: object) -> bool:
    """Return True only when host holds an open, unrevoked Main Subtopic write capability."""
    if not isinstance(host, ReferenceLearningHost) or getattr(host, "_closed", False):
        return False
    try:
        with host._broker._session_operation(host._session) as state:
            if state.binding.role != "main" or not state.binding.subtopic:
                return False
            progress_path = f"topics/{state.binding.topic}/subtopics/{state.binding.subtopic}/progress.yaml"
            if not (state.policy.may_write(progress_path) and state.policy.may_read(progress_path)):
                return False
            host._broker.guard.check(state.deployment, require_active=True)
            generation, _ = host._broker._fresh_generation(state)
            return generation == state.binding.generation
    except (GuardRejected, ResolutionError, OSError):
        return False


def _default_starter_state_for_status(status: str) -> str:
    if status in _ESTABLISHED_RESULT_STATUSES:
        return "established"
    if status in _UNKNOWN_RESULT_STATUSES:
        return "unknown"
    if status in _PREPARED_RESULT_STATUSES or status.startswith("staging_"):
        return "not_established"
    return "unknown"


def _starter_state_for_receipt(receipt: Mapping[str, Any], *, observed_status: str | None = None) -> str:
    if receipt.get("completed") or receipt.get("confirmed", 0) >= 10:
        return "established"
    confirmed = receipt.get("confirmed", 0)
    steps = receipt.get("steps", ())
    if confirmed == 9 and len(steps) > 9:
        status = observed_status if observed_status is not None else receipt.get("status")
        if status == "applied":
            return "established"
        if status == "not_applied":
            return "not_established"
        return "unknown"
    return "not_established"


def _parse_subtopic_progress(progress_document: Mapping[str, Any] | str) -> dict[str, Any]:
    """Validate and normalize a Subtopic Progress document or YAML string."""
    if isinstance(progress_document, str):
        if _utf8_size(progress_document, "invalid_progress_document") > CANDIDATE_YAML_MAX_BYTES:
            raise FirstUseRejected("invalid_progress_document")
        try:
            parsed = yaml.safe_load(progress_document)
        except (yaml.YAMLError, ValueError, OverflowError):
            raise FirstUseRejected("invalid_progress_document") from None
    elif isinstance(progress_document, Mapping):
        parsed = dict(progress_document)
    else:
        raise FirstUseRejected("invalid_progress_document")
    if not isinstance(parsed, dict) or parsed.get("document_type") != "subtopic_progress":
        raise FirstUseRejected("invalid_progress_document")
    revision = parsed.get("revision")
    if type(revision) is not int or revision < 1:
        raise FirstUseRejected("invalid_progress_document")
    if not all(isinstance(parsed.get(section), Mapping) for section in ("milestones", "current", "resume")):
        raise FirstUseRejected("invalid_progress_document")
    return parsed


def has_saved_learning_position(progress_document: Mapping[str, Any] | str) -> bool:
    """Return True only when Progress reflects a persisted change beyond starter setup."""
    parsed = _parse_subtopic_progress(progress_document)
    if parsed["revision"] > 1:
        return True
    return_point = parsed["resume"].get("return_point")
    if return_point is not None and return_point != {}:
        return True
    return any(
        isinstance(entry, Mapping) and entry.get("status") not in (None, "planned")
        for entry in parsed["milestones"].values()
    )


@dataclass(frozen=True)
class CheckpointSaveResult:
    """Outcome of attempting and readback-verifying save_learning_checkpoint."""

    saved: bool
    verified: bool
    status: str
    can_save_checkpoint: bool
    applied: bool = False
    progress_path: str | None = None
    revision: int | None = None
    version_token: str | None = None
    error_code: str | None = None
    retryable: bool = False
    mastery_established: bool = False
    requires_recreation: bool = False


def save_and_verify_learning_checkpoint(
    host: ReferenceLearningHost | None,
    *,
    progress_path: str,
    checkpoint: dict[str, object],
    expected_version_token: str,
) -> CheckpointSaveResult:
    """Save a session learning checkpoint through host and verify persisted readback."""
    if not isinstance(progress_path, str) or not progress_path:
        raise FirstUseRejected("invalid_progress_path")
    if host is None:
        return CheckpointSaveResult(
            saved=False,
            verified=False,
            status="host_unavailable",
            can_save_checkpoint=False,
            progress_path=progress_path,
            error_code="host_unavailable",
        )
    if not _host_can_save_checkpoint(host):
        return CheckpointSaveResult(
            saved=False,
            verified=False,
            status="rejected",
            can_save_checkpoint=False,
            progress_path=progress_path,
            error_code="guard_rejected",
        )
    if progress_path != _bound_progress_path(host):
        return CheckpointSaveResult(
            saved=False,
            verified=False,
            status="rejected",
            can_save_checkpoint=_host_can_save_checkpoint(host),
            progress_path=progress_path,
            error_code="guard_rejected",
        )
    write_response = host.invoke({
        "operation": "save_learning_checkpoint",
        "arguments": {
            "checkpoint": checkpoint,
            "expected_version_token": expected_version_token,
        },
    })
    write_error_code = None
    if not write_response.get("ok"):
        error = write_response.get("error") or {}
        write_error_code = error.get("code")
        if write_error_code != "write_published_cleanup_failed":
            status = {
                "cas_conflict": "conflict",
                "write_outcome_unknown": "outcome_unknown",
            }.get(write_error_code, "rejected")
            return CheckpointSaveResult(
                saved=False,
                verified=False,
                status=status,
                can_save_checkpoint=_host_can_save_checkpoint(host),
                progress_path=progress_path,
                error_code=write_error_code,
                retryable=bool(error.get("retryable", False)),
            )
    applied = write_error_code is not None or bool((write_response.get("result") or {}).get("applied", False))
    try:
        read_response = host.invoke({
            "operation": "read_learning_context",
            "arguments": {"required_paths": [progress_path]},
        })
    except OSError:
        if not applied:
            raise
        read_response = {
            "ok": False,
            "error": {"code": write_error_code or "readback_io_error", "retryable": False},
        }
    if not read_response.get("ok"):
        error = read_response.get("error") or {}
        return CheckpointSaveResult(
            saved=applied,
            verified=False,
            status="saved_unverified" if applied else "unverified",
            can_save_checkpoint=_host_can_save_checkpoint(host),
            applied=applied,
            progress_path=progress_path,
            error_code=error.get("code") or write_error_code,
            retryable=write_error_code is None and bool(error.get("retryable", False)),
        )
    documents = (read_response.get("result") or {}).get("documents") or []
    document = next((item for item in documents if item.get("path") == progress_path), None)
    if document is None:
        return CheckpointSaveResult(
            saved=applied,
            verified=False,
            status="saved_unverified" if applied else "unverified",
            can_save_checkpoint=_host_can_save_checkpoint(host),
            applied=applied,
            progress_path=progress_path,
            error_code="readback_missing",
        )
    try:
        content = document.get("content")
        if not isinstance(content, str):
            raise FirstUseRejected("invalid_progress_document")
        parsed = _parse_subtopic_progress(content)
    except FirstUseRejected:
        return CheckpointSaveResult(
            saved=applied,
            verified=False,
            status="saved_unverified" if applied else "unverified",
            can_save_checkpoint=_host_can_save_checkpoint(host),
            applied=applied,
            progress_path=progress_path,
            error_code="invalid_progress_document",
        )
    persisted = {
        "milestone": parsed["current"].get("milestone"),
        "return_point": parsed["resume"].get("return_point"),
        "ready_next": parsed["resume"].get("ready_next"),
    }
    if not RuntimeSessionBroker._type_sensitive_semantic_equal(persisted, checkpoint):
        return CheckpointSaveResult(
            saved=applied,
            verified=False,
            status="saved_unverified" if write_error_code else "verification_mismatch",
            can_save_checkpoint=_host_can_save_checkpoint(host),
            applied=applied,
            progress_path=progress_path,
            revision=parsed["revision"],
            version_token=document.get("version_token"),
            error_code="verification_mismatch",
        )
    if applied:
        status, saved = "saved_verified", True
    elif has_saved_learning_position(parsed):
        status, saved = "already_saved_verified", True
    else:
        status, saved = "unchanged_starter_only", False
    return CheckpointSaveResult(
        saved=saved,
        verified=True,
        status=status,
        can_save_checkpoint=_host_can_save_checkpoint(host),
        applied=applied,
        progress_path=progress_path,
        revision=parsed["revision"],
        version_token=document.get("version_token"),
        error_code=write_error_code,
    )


@dataclass(frozen=True)
class FirstUseResult:
    status: str
    host: ReferenceLearningHost | None = None
    setup_preparation_recorded: bool = True
    starter_setup_state: str | None = None

    def __post_init__(self):
        if self.starter_setup_state is None:
            object.__setattr__(
                self, "starter_setup_state", _default_starter_state_for_status(self.status)
            )
        if self.starter_setup_state not in STARTER_SETUP_STATES:
            raise FirstUseRejected("invalid_state")

    @property
    def starter_setup_established(self) -> bool:
        return self.starter_setup_state == "established"

    @property
    def can_save_checkpoint(self) -> bool:
        return _host_can_save_checkpoint(self.host)

    @property
    def requires_recreation(self) -> bool:
        return False

    @property
    def auto_overwrite_allowed(self) -> bool:
        return False


@dataclass(frozen=True)
class FirstUseUxState:
    """Explicit separation of preparation, starter setup, checkpoint saving, and mastery."""

    phase: str
    setup_preparation_recorded: bool
    starter_setup_state: str
    learning_position_state: str
    can_save_checkpoint: bool
    can_continue_teaching: bool = True
    requires_recreation: bool = False
    auto_overwrite_allowed: bool = False
    mastery_established: bool = False


def assess_first_use_ux(
    result: FirstUseResult | None = None,
    *,
    declined: bool = False,
    host: ReferenceLearningHost | None = None,
    progress_document: Mapping[str, Any] | str | None = None,
    checkpoint_result: CheckpointSaveResult | None = None,
) -> FirstUseUxState:
    """Compute an unambiguous UX state separating profile creation from checkpoint saving."""
    if type(declined) is not bool:
        raise FirstUseRejected("invalid_state")
    if declined and (
        result is not None or checkpoint_result is not None or progress_document is not None
    ):
        raise FirstUseRejected("invalid_state")
    if declined:
        return FirstUseUxState(
            phase="declined",
            setup_preparation_recorded=False,
            starter_setup_state="not_established",
            learning_position_state="not_saved",
            can_save_checkpoint=False,
        )

    parsed_progress = (
        _parse_subtopic_progress(progress_document) if progress_document is not None else None
    )
    effective_host = host if host is not None else (result.host if result is not None else None)
    can_save = _host_can_save_checkpoint(effective_host)

    if result is not None:
        setup_preparation_recorded = bool(result.setup_preparation_recorded)
        starter_setup_state = result.starter_setup_state
    else:
        setup_preparation_recorded = False
        if parsed_progress is not None or (
            checkpoint_result is not None and (
                checkpoint_result.saved
                or checkpoint_result.verified
                or checkpoint_result.status == "outcome_unknown"
            )
        ):
            starter_setup_state = "established"
        else:
            starter_setup_state = "not_established"

    if starter_setup_state == "unknown":
        learning_position_state = "unknown"
    elif starter_setup_state == "not_established":
        learning_position_state = "not_saved"
    elif checkpoint_result is not None:
        if checkpoint_result.status in {"saved_verified", "already_saved_verified"}:
            learning_position_state = "saved_verified"
        elif checkpoint_result.status in {"saved_unverified", "unverified", "verification_mismatch"}:
            learning_position_state = "saved_unverified" if checkpoint_result.saved else "unknown"
        elif checkpoint_result.status == "unchanged_starter_only":
            learning_position_state = "not_saved"
        elif parsed_progress is not None:
            learning_position_state = (
                "saved_verified" if has_saved_learning_position(parsed_progress) else "not_saved"
            )
        else:
            learning_position_state = "unknown"
    elif parsed_progress is not None:
        learning_position_state = (
            "saved_verified" if has_saved_learning_position(parsed_progress) else "not_saved"
        )
    elif result is not None and result.status == "active":
        learning_position_state = "not_saved"
    else:
        learning_position_state = "unknown"

    if (result is not None and result.status == "outcome_unknown") or starter_setup_state == "unknown":
        phase = "outcome_unknown"
    elif starter_setup_state == "not_established":
        phase = "preparation_recorded" if setup_preparation_recorded else "not_started"
    elif learning_position_state == "saved_verified":
        phase = "checkpoint_saved_verified"
    else:
        phase = "starter_setup_established"

    return FirstUseUxState(
        phase=phase,
        setup_preparation_recorded=setup_preparation_recorded,
        starter_setup_state=starter_setup_state,
        learning_position_state=learning_position_state,
        can_save_checkpoint=can_save,
    )


class SyntheticFirstUseService:
    """Trusted installer reference for local synthetic repositories only."""
    def __init__(self, *, provider, locator, authority, store, write_admission,
                 event: Callable[[str, dict], None] | None = None):
        if not isinstance(provider, SyntheticPreparedGitProvider):
            raise FirstUseRejected("unsupported_provider")
        if not isinstance(authority, SyntheticFirstUseAuthority) or write_admission is None:
            raise FirstUseRejected("trusted_adapters_required")
        self.provider, self.locator = provider, load_locator(locator)
        self.authority, self.store, self.admission = authority, store, write_admission
        self.event = event or (lambda _name, _receipt: None)
        self.broker = RuntimeSessionBroker(provider, self.locator, write_admission=write_admission)
        self.guard = DeploymentGuard(provider)

    def _binding(self, receiver, operation_id, intent):
        binding = self.authority.binding(receiver)
        if binding["operation_id"] != operation_id or binding["intent_digest"] != _digest(asdict(intent)):
            raise FirstUseRejected("intent_conflict")
        if binding["locator_digest"] != _digest(self.locator) or binding["version"] != FIRST_USE_VERSION:
            raise FirstUseRejected("target_identity_conflict")
        return binding

    def _slot(self):
        return dict(repository_id=self.locator["instance"]["repository_id"],
                    canonical_ref=self.locator["instance"]["canonical_ref"], slot="first-topic-main")

    def _current(self, receipt, receiver):
        self.authority.check(receiver, receipt["binding"])
        self.store.reserve(self._slot(), receipt["binding"])
        if receipt["locator_digest"] != _digest(self.locator):
            raise FirstUseRejected("target_identity_conflict")
        context = SessionDeploymentContext(**receipt["deployment"])
        snapshot = self.guard.snapshot(context)
        if snapshot.control_head != receipt["control_head"]:
            raise FirstUseRejected("stale_deployment")
        self.guard.assert_snapshot_current(context, snapshot)

    def _validate(self, documents, *, receipt=None, validate_activation=False):
        rc, inst = self.locator["runtime_control"], self.locator["instance"]
        owned = []
        try:
            control = self.provider.materialize(rc["repository_id"], rc["canonical_ref"])
            owned.append(control)
            if control.repository_id != rc["repository_id"]:
                raise FirstUseRejected("target_identity_conflict")
            text, _, head = self.provider.read_text(rc["repository_id"], control.commit_sha, rc["contract_path"])
            if head != control.commit_sha:
                raise FirstUseRejected("stale_deployment")
            contract = _load_contract(text)
            if any(f.severity == "error" for f in validate_deployment_contract_document(contract, raw_text=text)):
                raise FirstUseRejected("invalid_deployment")
            dep, pin = contract["deployment"], contract["core"]
            if dep["write_state"] != "active":
                raise FirstUseRejected("deployment_inactive")
            core = self.provider.materialize(pin["repository_id"], pin["commit"])
            owned.append(core)
            if core.repository_id != pin["repository_id"] or core.commit_sha != pin["commit"]:
                raise FirstUseRejected("target_identity_conflict")
            declared = yaml.safe_load((core.root / "config/core.yaml").read_text(encoding="utf-8"))["manifest"].get("runtime_session_write_policy_fingerprint")
            actual = _compute_bounded_write_policy_fingerprint(
                [sys.executable, str(core.root / "scripts/validate_learning_os.py"), "--write-policy-fingerprint"], cwd=core.root)
            if declared != actual or actual != instance_write_policy_fingerprint():
                raise FirstUseRejected("stale_deployment")
            materialize_instance = (self.provider.materialize_first_use_baseline if receipt is None
                                    else self.provider.materialize)
            instance = materialize_instance(inst["repository_id"], inst["canonical_ref"])
            owned.append(instance)
            if instance.repository_id != inst["repository_id"]:
                raise FirstUseRejected("target_identity_conflict")
            with tempfile.TemporaryDirectory(prefix="learning-os-first-use-candidate-") as name:
                candidate = Path(name) / "instance"
                shutil.copytree(instance.root, candidate, dirs_exist_ok=True)
                for path, content in documents:
                    target = candidate / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8", newline="")
                # Helpers remain outside the candidate Instance. Provenance is
                # the actual materialization identity, never candidate YAML.
                locator_path = Path(name) / "locator.yaml"
                provenance_path = Path(name) / "provenance.yaml"
                locator_path.write_text(yaml.safe_dump(self.locator), encoding="utf-8")
                provenance_path.write_text(yaml.safe_dump({
                    key: dict(repository_id=snapshot.repository_id, commit_sha=snapshot.commit_sha)
                    for key, snapshot in (("control", control), ("core", core), ("instance", instance))
                }), encoding="utf-8")
                command = [
                    "--deployment", "--control-snapshot", str(control.root),
                    "--core-snapshot", str(core.root), "--instance-snapshot", str(candidate),
                    "--locator", str(locator_path), "--provenance", str(provenance_path),
                ]
                try:
                    _run_pinned_core_validator(core.root, command)
                    if validate_activation:
                        path, content = _activation_document(documents)
                        (candidate / path).write_text(content, encoding="utf-8", newline="")
                        _run_pinned_core_validator(core.root, command)
                except GuardRejected as exc:
                    raise FirstUseRejected("candidate_invalid: " + str(exc)) from None
            context = SessionDeploymentContext(dep["id"], dep["epoch"], pin["repository_id"], pin["commit"],
                inst["repository_id"], rc["repository_id"], rc["canonical_ref"], rc["contract_path"])
            if receipt and (asdict(context) != receipt["deployment"] or control.commit_sha != receipt["control_head"]):
                raise FirstUseRejected("stale_deployment")
            self.guard.assert_snapshot_current(context, self.guard.snapshot(context))
            return context, control.commit_sha, instance.commit_sha
        finally:
            # Try every owned cleanup; cleanup failure never permits activation.
            self.broker._release_materializations(owned)

    def prepare(self, operation_id, receiver, intent):
        binding = self._binding(receiver, operation_id, intent)
        with self.store.operation(), self.authority.lease(receiver, binding), self.admission.write_lease():
            slot = self.store.inspect_slot(self._slot(), binding)
            previous = self.store.read()
            if previous is not None:
                if previous["binding"] != binding:
                    raise FirstUseRejected("intent_conflict")
                self._current(previous, receiver)
                return FirstUseResult(
                    "already_applied" if previous["completed"] else previous["status"],
                    starter_setup_state=_starter_state_for_receipt(previous),
                )
            if slot is not None and slot["initialized"]:
                raise FirstUseRejected("receipt_missing")
            now = datetime.now(timezone.utc)
            stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
            lineage = "first-use-" + secrets.token_hex(16)
            documents = _documents(intent, stamp=stamp, lineage=lineage)
            identity = self.provider.preflight_first_use(
                self._slot()["repository_id"], self._slot()["canonical_ref"],
                [*documents, _activation_document(documents)], operation_id=operation_id)
            context, control_head, instance_head = self._validate(documents, validate_activation=True)
            if instance_head != identity["head"]:
                raise FirstUseRejected("conflict")
            manifest = dict(documents=documents, lineage=lineage, intent=asdict(intent),
                            timestamp=f"{int(now.timestamp())} +0000", version=FIRST_USE_VERSION,
                            deployment=asdict(context), baseline=identity)
            receipt = dict(binding=binding, locator_digest=_digest(self.locator), manifest=manifest,
                           manifest_digest=_digest(manifest), deployment=asdict(context), control_head=control_head,
                           status="prepared", confirmed=0, steps=[], completed=False, delivery="not_started")
            self.store.preflight_receipt(receipt)
            self.store.reserve(self._slot(), binding)
            self.store.write(receipt)
            self.store.mark_initialized()
            self.event("manifest_durable", receipt)
            return FirstUseResult("prepared")

    def inspect(self, operation_id, receiver, intent):
        binding = self._binding(receiver, operation_id, intent)
        with self.store.operation(), self.authority.lease(receiver, binding), self.admission.read_lease():
            receipt = self.store.read()
            if receipt is None or receipt["binding"] != binding:
                raise FirstUseRejected("receipt_missing")
            self._current(receipt, receiver)
            if receipt["completed"]:
                return FirstUseResult("already_applied")
            if len(receipt["steps"]) > receipt["confirmed"]:
                try:
                    status = self.provider.inspect_first_use_step(PreparedFirstUseStep(**receipt["steps"][-1]))
                except (OSError, ResolutionError, CasConflict):
                    status = "outcome_unknown"
                return FirstUseResult(
                    status,
                    starter_setup_state=_starter_state_for_receipt(receipt, observed_status=status),
                )
            return FirstUseResult(
                receipt["status"],
                starter_setup_state=_starter_state_for_receipt(receipt),
            )

    def apply(self, operation_id, receiver, intent):
        binding = self._binding(receiver, operation_id, intent)
        with self.store.operation():
            receipt = self.store.read()
            if receipt is None or receipt["binding"] != binding:
                raise FirstUseRejected("receipt_missing")
            if _digest(receipt["manifest"]) != receipt["manifest_digest"]:
                raise FirstUseRejected("receipt_integrity_failed")
            while receipt["confirmed"] < 10:
                index = receipt["confirmed"]
                with self.authority.lease(receiver, binding), self.admission.write_lease():
                    self._current(receipt, receiver)
                    if index == 9:
                        self._validate([_activation_document(receipt["manifest"]["documents"])], receipt=receipt)
                    if len(receipt["steps"]) == index:
                        docs = receipt["manifest"]["documents"]
                        if index < 9:
                            path, content = docs[index]
                            expected_blob = None
                        else:
                            path, content = _activation_document(docs)
                            expected_blob = receipt["steps"][7]["descriptor"]["new_blob"]
                        parent = (receipt["manifest"]["baseline"]["head"] if index == 0
                                  else receipt["steps"][index - 1]["candidate"])
                        step = self.provider.prepare_first_use_step(
                            repository_id=self._slot()["repository_id"], branch=self._slot()["canonical_ref"],
                            path=path, content=content, expected_parent=parent, expected_blob=expected_blob,
                            operation_id=operation_id, step_index=index, intent_digest=binding["intent_digest"],
                            manifest_digest=receipt["manifest_digest"], timestamp=receipt["manifest"]["timestamp"])
                        receipt["steps"].append(asdict(step))
                        self.store.write(receipt)
                        self.provider.first_use_trace.append(("receipt_durable", index, step.candidate))
                        self.event("receipt_durable", receipt)
                    step = PreparedFirstUseStep(**receipt["steps"][index])
                    try:
                        outcome = self.provider.inspect_first_use_step(step)
                        if outcome == "not_applied":
                            self.provider.publish_first_use_step(step, authorize=lambda: self._current(receipt, receiver))
                            self.event("published", receipt)
                            outcome = self.provider.inspect_first_use_step(step)
                    except (OSError, ResolutionError, CasConflict):
                        outcome = "outcome_unknown"
                    if outcome != "applied":
                        receipt["status"] = outcome
                        self.store.write(receipt)
                        return FirstUseResult(
                            outcome,
                            starter_setup_state=_starter_state_for_receipt(receipt, observed_status=outcome),
                        )
                    receipt["confirmed"] = index + 1
                    receipt["status"] = "activated_unissued" if index == 9 else f"staging_{index + 1}"
                    self.store.write(receipt)
                self.event("step_confirmed", receipt)
            with self.authority.lease(receiver, binding), self.admission.write_lease():
                self._current(receipt, receiver)
                if not receipt["completed"]:
                    if self.provider.inspect_first_use_step(PreparedFirstUseStep(**receipt["steps"][9])) != "applied":
                        return FirstUseResult("conflict")
                    self._validate([], receipt=receipt)
                    receipt["completed"] = True
                    receipt["status"] = "active"
                    self.store.write(receipt)
                return self._deliver(receipt, receiver)

    def _deliver(self, receipt, receiver):
        self._current(receipt, receiver)
        current_head = self.provider.resolve_ref(self._slot()["repository_id"], self._slot()["canonical_ref"])
        if current_head != receipt["steps"][9]["candidate"]:
            return FirstUseResult("already_applied" if receipt["delivery"] == "delivered" else "delivery_pending")
        operation = _digest(receipt["binding"])
        existing = self.authority._sessions.get(operation)
        if existing is not None and not existing._closed:
            try:
                existing._broker.assert_current(existing._session)
            except (GuardRejected, ResolutionError, OSError):
                return FirstUseResult("delivery_pending")
            receipt["delivery"] = "delivered"
            self.store.write(receipt)
            return FirstUseResult("already_applied", existing)
        if receipt["delivery"] != "not_started":
            return FirstUseResult("delivery_pending" if receipt["delivery"] == "pending" else "already_applied")
        receipt["delivery"] = "pending"
        self.store.write(receipt)
        self.event("delivery_pending", receipt)
        intent = receipt["manifest"]["intent"]
        topic = intent["topic"]
        host = self.broker._open_first_use_session(
            topic=topic, subtopic=intent["unit"], lineage_id=receipt["manifest"]["lineage"],
            expected_instance_head=current_head, authorize=lambda: self._current(receipt, receiver))
        result = ReferenceLearningHost(self.broker, host)
        self.authority._sessions[operation] = result
        receipt["delivery"] = "delivered"
        self.store.write(receipt)
        return FirstUseResult("active", result)
