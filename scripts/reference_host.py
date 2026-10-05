"""Public-safe thin host facade for the Learning OS reference journey.

This module is intentionally transport-agnostic. It does not implement an RPC
protocol, model-provider runner, repository credential store, or deployment
authority. The host supplies all trusted bindings and exposes only the bounded
ordinary-Runtime operations needed by the synthetic reference journey.
"""
from __future__ import annotations

from typing import Any

from scripts.runtime_adapter import (
    CasConflict,
    GuardRejected,
    RepositoryProvider,
    ResolutionError,
)
from scripts.runtime_broker import (
    DeploymentWriteAdmission,
    LearningRuntimeSession,
    RuntimeCapabilityPolicy,
    RuntimeSessionBroker,
)

REFERENCE_HOST_SURFACE_VERSION = "v2"
REFERENCE_HOST_OPERATIONS = frozenset({
    "read_learning_context",
    "save_learning_checkpoint",
    "create_evidence",
    "reconcile_knowledge",
})


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionError(f"{where} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ResolutionError(f"{where} keys must be strings")
    return value


def _exact_keys(
    value: dict[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    where: str,
) -> None:
    keys = frozenset(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ResolutionError(f"{where} is missing required fields")
    if unknown:
        raise ResolutionError(f"{where} contains unsupported fields")


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResolutionError(f"{where} must be a non-empty string")
    return value


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ResolutionError(f"{where} must be an array")
    if any(not isinstance(item, str) or not item for item in value):
        raise ResolutionError(f"{where} entries must be non-empty strings")
    return tuple(value)


class ReferenceLearningHost:
    """Bind one broker session to a fixed, public-safe operation allowlist.

    The host owns the locator, capability policy, repository provider and
    deployment-operation admission. Requests cannot redirect any of them.
    Repository/provider lifetime remains owned by the caller.
    """

    def __init__(
        self,
        broker: RuntimeSessionBroker,
        session: LearningRuntimeSession,
    ):
        self._broker = broker
        self._session = session
        self._closed = False

    @classmethod
    def open(
        cls,
        *,
        provider: RepositoryProvider,
        locator_source: str | dict,
        branch_runtime_path: str,
        policy: RuntimeCapabilityPolicy,
        write_admission: DeploymentWriteAdmission,
        expected_generation: int | None = None,
    ) -> "ReferenceLearningHost":
        broker = RuntimeSessionBroker(
            provider,
            locator_source,
            write_admission=write_admission,
        )
        session = broker.open_session(
            branch_runtime_path=branch_runtime_path,
            policy=policy,
            expected_generation=expected_generation,
        )
        return cls(broker, session)

    def close(self) -> None:
        if self._closed:
            return
        self._broker.close_session(self._session)
        self._closed = True

    def __enter__(self) -> "ReferenceLearningHost":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _failure(operation: str | None, code: str) -> dict[str, object]:
        return {
            "surface_version": REFERENCE_HOST_SURFACE_VERSION,
            "ok": False,
            "operation": operation,
            "error": {
                "code": code,
                "retryable": code == "cas_conflict",
            },
        }

    @staticmethod
    def _success(operation: str, result: object) -> dict[str, object]:
        return {
            "surface_version": REFERENCE_HOST_SURFACE_VERSION,
            "ok": True,
            "operation": operation,
            "result": result,
        }

    def invoke(self, request: object) -> dict[str, object]:
        """Execute one allowlisted operation and return a bounded result envelope.

        Expected broker/authority failures are converted into stable error codes
        rather than reflecting internal paths, remotes, commits or credentials.
        Unexpected implementation failures remain host-operator exceptions.
        """
        operation: str | None = None
        try:
            if self._closed:
                raise GuardRejected("reference host session is closed")
            envelope = _mapping(request, "reference host request")
            _exact_keys(
                envelope,
                required=frozenset({"operation", "arguments"}),
                where="reference host request",
            )
            operation = _string(
                envelope["operation"], "reference host operation"
            )
            if operation not in REFERENCE_HOST_OPERATIONS:
                raise GuardRejected(
                    "reference host operation is not allowlisted"
                )
            arguments = _mapping(
                envelope["arguments"], "reference host arguments"
            )

            if operation == "read_learning_context":
                _exact_keys(
                    arguments,
                    required=frozenset({"required_paths"}),
                    optional=frozenset({"optional_paths"}),
                    where="read_learning_context arguments",
                )
                required_paths = _string_list(
                    arguments["required_paths"], "required_paths"
                )
                optional_paths = _string_list(
                    arguments.get("optional_paths", []), "optional_paths"
                )
                bundle = self._broker.read_learning_context(
                    self._session,
                    required_paths=required_paths,
                    optional_paths=optional_paths,
                )
                result = {
                    "documents": [
                        {
                            "path": path,
                            "content": item.content,
                            "version_token": item.version_token,
                        }
                        for path, item in bundle.documents
                    ],
                    "missing_optional": list(bundle.missing_optional),
                }
                return self._success(operation, result)

            if operation == "save_learning_checkpoint":
                _exact_keys(
                    arguments,
                    required=frozenset({
                        "checkpoint",
                        "expected_version_token",
                    }),
                    where="save_learning_checkpoint arguments",
                )
                checkpoint = _mapping(
                    arguments["checkpoint"], "learning checkpoint"
                )
                _exact_keys(
                    checkpoint,
                    required=frozenset({
                        "milestone",
                        "return_point",
                        "ready_next",
                    }),
                    where="learning checkpoint",
                )
                checkpoint = {
                    "milestone": list(_string_list(
                        checkpoint["milestone"],
                        "learning checkpoint milestone",
                    )),
                    "return_point": (
                        None
                        if checkpoint["return_point"] is None
                        else _mapping(
                            checkpoint["return_point"],
                            "learning checkpoint return_point",
                        )
                    ),
                    "ready_next": list(_string_list(
                        checkpoint["ready_next"],
                        "learning checkpoint ready_next",
                    )),
                }
                expected = _string(
                    arguments["expected_version_token"],
                    "expected_version_token",
                )
                ack = self._broker.save_learning_checkpoint(
                    self._session,
                    checkpoint=checkpoint,
                    expected_blob_sha=expected,
                    message="reference-host: save learning checkpoint",
                )
                return self._success(
                    operation, {"applied": ack.applied}
                )
            if operation == "create_evidence":
                _exact_keys(
                    arguments,
                    required=frozenset({"content"}),
                    where="create_evidence arguments",
                )
                content = _string(arguments["content"], "Evidence content")
                ack = self._broker.create_evidence(
                    self._session,
                    content=content,
                    message="reference-host: create Evidence",
                )
                return self._success(
                    operation, {"applied": ack.applied}
                )

            if operation == "reconcile_knowledge":
                _exact_keys(
                    arguments,
                    required=frozenset({
                        "content",
                        "expected_version_token",
                    }),
                    where="reconcile_knowledge arguments",
                )
                content = _string(
                    arguments["content"], "Knowledge content"
                )
                expected = arguments["expected_version_token"]
                if expected is not None:
                    expected = _string(
                        expected, "expected_version_token"
                    )
                ack = self._broker.reconcile_knowledge(
                    self._session,
                    content=content,
                    expected_blob_sha=expected,
                    message="reference-host: reconcile Knowledge",
                )
                return self._success(
                    operation, {"applied": ack.applied}
                )

            raise GuardRejected(
                "reference host operation has no dispatcher"
            )
        except CasConflict:
            return self._failure(operation, "cas_conflict")
        except GuardRejected:
            return self._failure(operation, "guard_rejected")
        except ResolutionError:
            return self._failure(operation, "resolution_failed")
