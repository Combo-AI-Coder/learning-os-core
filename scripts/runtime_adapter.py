"""V0.4 live resolver and guarded Instance mutation adapter.

The validator remains deterministic and offline. This module performs the
live/materialization duties that intentionally do not belong to it.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import unicodedata
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)

from scripts.validate_learning_os import (
    RepositorySnapshot,
    validate_deployment,
    validate_deployment_contract_document,
)

EXACT_COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_TEXT_BLOB_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_TREE_ENTRIES = 50_000
MAX_SNAPSHOT_TREE_OBJECTS = 256
MAX_SNAPSHOT_TREE_LIST_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BLOB_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BLOB_BYTES = 256 * 1024 * 1024
MAX_FETCH_OBJECT_BYTES = 320 * 1024 * 1024
MAX_FETCH_STDERR_BYTES = 1024 * 1024
MAX_GIT_METADATA_OBJECT_BYTES = 8 * 1024 * 1024
MAX_TAG_PEEL_DEPTH = 16
MAX_REMOTE_GIT_STREAM_BYTES = 1024 * 1024
MAX_SNAPSHOT_PATH_COMPONENT_BYTES = 255
MAX_SNAPSHOT_PATH_BYTES = 4096
MAX_SNAPSHOT_PATH_DEPTH = 128
MAX_SNAPSHOT_EXPANDED_PATH_BYTES = 16 * 1024 * 1024
FETCH_POLL_SECONDS = 0.02
BOUNDED_YAML_MAX_BYTES = 1024 * 1024
BOUNDED_YAML_MAX_NODES = 20000
BOUNDED_YAML_MAX_DEPTH = 64


class ResolutionError(RuntimeError):
    pass


class GuardRejected(RuntimeError):
    pass


class CasConflict(RuntimeError):
    pass


class TransitionRejected(RuntimeError):
    pass


def _preflight_bounded_yaml(content: str, where: str) -> None:
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError:
        raise ResolutionError(f"{where} is not valid UTF-8 text") from None
    if len(encoded) > BOUNDED_YAML_MAX_BYTES:
        raise ResolutionError(f"{where} exceeds the byte limit")

    depth = 0
    nodes = 0
    starts = (MappingStartEvent, SequenceStartEvent)
    ends = (MappingEndEvent, SequenceEndEvent)
    try:
        for event in yaml.parse(content):
            if isinstance(event, AliasEvent) or getattr(
                event, "anchor", None
            ) is not None:
                raise ResolutionError(
                    f"{where} aliases and anchors are not allowed"
                )
            if isinstance(event, starts):
                depth += 1
                nodes += 1
                if depth > BOUNDED_YAML_MAX_DEPTH:
                    raise ResolutionError(
                        f"{where} exceeds the nesting-depth limit"
                    )
            elif isinstance(event, ScalarEvent):
                nodes += 1
            elif isinstance(event, ends):
                depth = max(0, depth - 1)
            if nodes > BOUNDED_YAML_MAX_NODES:
                raise ResolutionError(f"{where} exceeds the node limit")
    except yaml.YAMLError as exc:
        raise ResolutionError(
            f"{where} preflight failed: {exc.__class__.__name__}"
        ) from None


@dataclass(frozen=True)
class MaterializedRepository:
    root: Path
    repository_id: int
    commit_sha: str
    full_name: str = ""


@dataclass(frozen=True)
class SessionDeploymentContext:
    deployment_id: str
    epoch: int
    core_repository_id: int
    core_commit: str
    instance_repository_id: int
    runtime_control_repository_id: int
    runtime_control_ref: str
    contract_path: str


@dataclass
class ResolvedDeployment:
    context: SessionDeploymentContext
    control: MaterializedRepository
    core: MaterializedRepository
    instance: MaterializedRepository


class RepositoryProvider(Protocol):
    def materialize(self, repository_id: int, ref: str) -> MaterializedRepository: ...
    def read_text(self, repository_id: int, ref: str, path: str) -> tuple[str, str, str]: ...
    def update_text(
        self,
        repository_id: int,
        branch: str,
        path: str,
        content: str,
        expected_blob_sha: str,
        message: str,
        expected_ref_sha: str | None = None,
    ) -> str: ...
    def release_materialization(
        self, snapshot: MaterializedRepository
    ) -> None: ...


def _positive_id(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ResolutionError(f"{where} must be a positive numeric repository ID")
    return value


def _nonempty(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResolutionError(f"{where} must be a non-empty string")
    return value


def load_locator(source: str | Path | dict) -> dict:
    if isinstance(source, dict):
        data = source
    else:
        try:
            data = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ResolutionError(f"trusted locator is unreadable: {exc.__class__.__name__}") from None
    allowed_top = {"schema_version", "document_type", "runtime_control", "instance"}
    if not isinstance(data, dict) or set(data) - allowed_top:
        raise ResolutionError("trusted locator contains unknown top-level fields")
    if not {"runtime_control", "instance"} <= set(data):
        raise ResolutionError("trusted locator requires runtime_control and instance")
    if data.get("schema_version", "0.4") != "0.4":
        raise ResolutionError("trusted locator schema_version must be '0.4'")
    if data.get("document_type", "trusted_locator") != "trusted_locator":
        raise ResolutionError("trusted locator document_type must be trusted_locator")
    rc, inst = data["runtime_control"], data["instance"]
    if not isinstance(rc, dict) or not isinstance(inst, dict):
        raise ResolutionError("trusted locator sections must be mappings")
    rc_allowed = {"repository_id", "repository", "canonical_ref", "contract_path"}
    inst_allowed = {"repository_id", "repository", "canonical_ref"}
    if set(rc) - rc_allowed or set(inst) - inst_allowed:
        raise ResolutionError("trusted locator contains unknown fields")
    return {
        "runtime_control": {
            **rc,
            "repository_id": _positive_id(rc.get("repository_id"), "runtime_control.repository_id"),
            "canonical_ref": _nonempty(rc.get("canonical_ref", "main"), "runtime_control.canonical_ref"),
            "contract_path": _nonempty(rc.get("contract_path", "deployment.yaml"), "runtime_control.contract_path"),
        },
        "instance": {
            **inst,
            "repository_id": _positive_id(inst.get("repository_id"), "instance.repository_id"),
            "canonical_ref": _nonempty(inst.get("canonical_ref", "main"), "instance.canonical_ref"),
        },
    }


def _load_contract(text: str) -> dict:
    _preflight_bounded_yaml(text, "Runtime-Control YAML")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ResolutionError(f"Runtime-Control contract is malformed YAML: {exc.__class__.__name__}") from None
    if not isinstance(data, dict):
        raise ResolutionError("Runtime-Control contract must be a mapping")
    deployment, core = data.get("deployment"), data.get("core")
    if not isinstance(deployment, dict) or not isinstance(core, dict):
        raise ResolutionError("Runtime-Control deployment/core sections must be mappings")
    return data


def validate_initial_cutover(contract: dict) -> None:
    """Validate the unique LEGACY -> SPLIT semantic cutover contract."""
    dep = contract.get("deployment") if isinstance(contract, dict) else None
    core = contract.get("core") if isinstance(contract, dict) else None
    if not isinstance(dep, dict) or not isinstance(core, dict):
        raise TransitionRejected("initial cutover contract is malformed")
    if dep.get("topology") != "split" or dep.get("write_state") != "frozen":
        raise TransitionRejected("initial split cutover must publish frozen")
    if dep.get("epoch") != 1:
        raise TransitionRejected("initial split cutover epoch must be 1")
    if not isinstance(core.get("repository_id"), int) or not EXACT_COMMIT.fullmatch(str(core.get("commit", ""))):
        raise TransitionRejected("initial split cutover requires an exact Core identity and pin")


def validate_deployment_transition(previous: dict, current: dict) -> None:
    """Validate one Runtime-Control transition independently of snapshots."""
    try:
        pdep, pcore = previous["deployment"], previous["core"]
        cdep, ccore = current["deployment"], current["core"]
    except (KeyError, TypeError):
        raise TransitionRejected("transition contracts are malformed") from None
    if pdep.get("id") != cdep.get("id"):
        raise TransitionRejected("deployment id is immutable")
    if pdep.get("topology") != "split" or cdep.get("topology") != "split":
        raise TransitionRejected("Runtime-Control history is split-only")
    if pcore.get("repository_id") != ccore.get("repository_id"):
        raise TransitionRejected("Core repository identity is immutable within a deployment")
    pe, ce = pdep.get("epoch"), cdep.get("epoch")
    if not isinstance(pe, int) or not isinstance(ce, int) or ce < pe:
        raise TransitionRejected("deployment epoch must never decrease")
    pin_changed = pcore.get("commit") != ccore.get("commit")
    if pin_changed:
        if pdep.get("write_state") != "frozen" or cdep.get("write_state") != "frozen":
            raise TransitionRejected("Core promotion requires frozen -> frozen")
        if ce != pe + 1:
            raise TransitionRejected("Core promotion must increment epoch exactly once")
        if not EXACT_COMMIT.fullmatch(str(ccore.get("commit", ""))):
            raise TransitionRejected("promoted Core pin must be exact")
    elif ce != pe:
        raise TransitionRejected("epoch changes only with an exact Core promotion")
    allowed_states = {("active", "frozen"), ("frozen", "active"),
                      ("active", "active"), ("frozen", "frozen")}
    if (pdep.get("write_state"), cdep.get("write_state")) not in allowed_states:
        raise TransitionRejected("invalid write-state transition")


def pointer_rollback_allowed(*, post_cutover_instance_mutated: bool) -> bool:
    return not post_cutover_instance_mutated


class DeploymentResolver:
    def __init__(self, provider: RepositoryProvider):
        self.provider = provider

    def resolve(self, locator_source: str | Path | dict) -> ResolvedDeployment:
        locator = load_locator(locator_source)
        rc, inst = locator["runtime_control"], locator["instance"]
        snapshots: list[MaterializedRepository] = []
        try:
            control = self.provider.materialize(
                rc["repository_id"], rc["canonical_ref"]
            )
            snapshots.append(control)
            if control.repository_id != rc["repository_id"]:
                raise ResolutionError(
                    "resolved Runtime-Control repository ID mismatch"
                )
            contract_text, _, contract_commit = self.provider.read_text(
                rc["repository_id"],
                control.commit_sha,
                rc["contract_path"],
            )
            if contract_commit != control.commit_sha:
                raise ResolutionError(
                    "Runtime-Control contract provenance changed during bootstrap"
                )
            contract = _load_contract(contract_text)
            core_block = contract["core"]
            core_id = _positive_id(
                core_block.get("repository_id"), "core.repository_id"
            )
            core_commit = _nonempty(
                core_block.get("commit"), "core.commit"
            )
            if not EXACT_COMMIT.fullmatch(core_commit):
                raise ResolutionError(
                    "core.commit must be an exact 40-lowercase-hex commit"
                )
            core = self.provider.materialize(core_id, core_commit)
            snapshots.append(core)
            if (
                core.repository_id != core_id
                or core.commit_sha != core_commit
            ):
                raise ResolutionError(
                    "resolved Core provenance does not match the exact "
                    "deployment pin"
                )
            instance = self.provider.materialize(
                inst["repository_id"], inst["canonical_ref"]
            )
            snapshots.append(instance)
            if instance.repository_id != inst["repository_id"]:
                raise ResolutionError(
                    "resolved Instance repository ID mismatch"
                )
            findings = validate_deployment(
                RepositorySnapshot(
                    control.root, control.repository_id, control.commit_sha
                ),
                RepositorySnapshot(
                    core.root, core.repository_id, core.commit_sha
                ),
                RepositorySnapshot(
                    instance.root,
                    instance.repository_id,
                    instance.commit_sha,
                ),
                locator,
            )
            errors = [
                finding.render()
                for finding in findings
                if finding.severity == "error"
            ]
            if errors:
                raise ResolutionError(
                    "deployment validation failed:\n" + "\n".join(errors)
                )
            dep = contract["deployment"]
            epoch = dep.get("epoch")
            if (
                not isinstance(epoch, int)
                or isinstance(epoch, bool)
                or epoch < 1
            ):
                raise ResolutionError(
                    "deployment.epoch must be a positive integer"
                )
            context = SessionDeploymentContext(
                deployment_id=_nonempty(
                    dep.get("id"), "deployment.id"
                ),
                epoch=epoch,
                core_repository_id=core_id,
                core_commit=core_commit,
                instance_repository_id=inst["repository_id"],
                runtime_control_repository_id=rc["repository_id"],
                runtime_control_ref=rc["canonical_ref"],
                contract_path=rc["contract_path"],
            )
            return ResolvedDeployment(context, control, core, instance)
        except Exception:
            for snapshot in reversed(snapshots):
                try:
                    self.provider.release_materialization(snapshot)
                except Exception:
                    pass
            raise


class DeploymentGuard:
    def __init__(self, provider: RepositoryProvider):
        self.provider = provider

    def check(
        self,
        session: SessionDeploymentContext,
        *,
        require_active: bool = True,
    ) -> dict:
        try:
            text, _, _ = self.provider.read_text(
                session.runtime_control_repository_id,
                session.runtime_control_ref,
                session.contract_path,
            )
            contract = _load_contract(text)
        except (ResolutionError, OSError, RuntimeError) as exc:
            raise GuardRejected(f"Runtime-Control fresh-read failed closed: {exc}") from None
        findings = validate_deployment_contract_document(
            contract,
            path=session.contract_path,
            raw_text=text,
        )
        errors = [
            finding for finding in findings
            if finding.severity == "error"
        ]
        if errors:
            codes = ", ".join(
                sorted({finding.code for finding in errors})
            )
            raise GuardRejected(
                "Runtime-Control contract failed canonical validation: "
                + codes
            )
        dep, core = contract["deployment"], contract["core"]
        write_state = dep["write_state"]
        if require_active and write_state != "active":
            raise GuardRejected("deployment is not active")
        for ok, message in (
            (dep.get("id") == session.deployment_id, "deployment id changed"),
            (dep.get("epoch") == session.epoch, "deployment epoch changed"),
            (core.get("repository_id") == session.core_repository_id, "Core repository changed"),
            (core.get("commit") == session.core_commit, "Core commit changed"),
        ):
            if not ok:
                raise GuardRejected(message)
        return contract

    def guarded_update(
        self,
        session: SessionDeploymentContext,
        *,
        branch: str,
        path: str,
        content: str,
        expected_blob_sha: str,
        message: str,
        expected_generation: int | None = None,
        generation_reader: Callable[[], int] | None = None,
        expected_ref_sha: str | None = None,
    ) -> str:
        self.check(session)
        if expected_generation is not None:
            if generation_reader is None:
                raise GuardRejected("generation guard input is missing")
            if generation_reader() != expected_generation:
                raise GuardRejected("semantic generation changed")
        try:
            if expected_ref_sha is None:
                return self.provider.update_text(
                    session.instance_repository_id, branch, path, content,
                    expected_blob_sha, message,
                )
            return self.provider.update_text(
                session.instance_repository_id, branch, path, content,
                expected_blob_sha, message,
                expected_ref_sha=expected_ref_sha,
            )
        except CasConflict:
            raise
        except Exception as exc:
            raise CasConflict(f"target blob CAS failed: {exc}") from None



@dataclass(frozen=True)
class GitRepositoryBinding:
    """Host-trusted numeric repository identity bound to one Git remote."""

    repository_id: int
    remote: str
    full_name: str = ""
    writable: bool = False
    ssh_auth_sock: str | None = None
    ssh_known_hosts_file: str | None = None


class GitCliProvider:
    """Git transport provider for host-bound runtimes (for example SSH deploy keys).

    Numeric repository identity comes from the host-trusted binding supplied by
    the caller. The remote/name is navigation/capability routing only and is
    never promoted into security identity.
    """

    def __init__(
        self,
        bindings: list[GitRepositoryBinding] | tuple[GitRepositoryBinding, ...],
        *,
        git_env: dict[str, str] | None = None,
    ):
        self.bindings: dict[int, GitRepositoryBinding] = {}
        for binding in bindings:
            repository_id = _positive_id(binding.repository_id, "binding.repository_id")
            if repository_id in self.bindings:
                raise ResolutionError("duplicate Git repository binding")
            remote = _nonempty(binding.remote, "binding.remote")
            if remote.startswith("-") or any(char in remote for char in "\x00\r\n"):
                raise ResolutionError("Git repository remote is unsafe")
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", remote):
                raise ResolutionError(
                    "Git explicit remote-helper syntax is unsupported"
                )
            if "://" in remote:
                parsed_remote = urllib.parse.urlsplit(remote)
                if parsed_remote.scheme.lower() in {"http", "https"} and (
                    parsed_remote.username is not None
                    or parsed_remote.password is not None
                    or bool(parsed_remote.query)
                    or bool(parsed_remote.fragment)
                ):
                    raise ResolutionError(
                        "Git HTTP(S) remote must not embed credentials "
                        "or opaque URL parameters"
                    )
            if re.match(r"^[A-Za-z]:[^/\\]", remote):
                raise ResolutionError("drive-relative Git repository remote is unsafe")
            windows_root_relative = (
                (remote.startswith("\\") and not remote.startswith("\\\\"))
                or (
                    os.name == "nt"
                    and remote.startswith("/")
                    and not remote.startswith("//")
                )
            )
            if windows_root_relative:
                raise ResolutionError(
                    "root-relative Git repository remote is unsafe"
                )
            if (
                "://" not in remote
                and not re.match(r"^[^/\\]+:.+", remote)
                and not os.path.isabs(remote)
            ):
                remote = os.path.abspath(remote)
            if not isinstance(binding.writable, bool):
                raise ResolutionError("binding.writable must be a boolean")
            ssh_auth_sock = None
            if binding.ssh_auth_sock is not None:
                ssh_auth_sock = _nonempty(
                    binding.ssh_auth_sock, "binding.ssh_auth_sock"
                )
                if not self._absolute_host_path(ssh_auth_sock):
                    raise ResolutionError(
                        "binding.ssh_auth_sock must be an absolute host path"
                    )
            ssh_known_hosts_file = None
            if binding.ssh_known_hosts_file is not None:
                known_hosts_input = _nonempty(
                    binding.ssh_known_hosts_file,
                    "binding.ssh_known_hosts_file",
                )
                ssh_known_hosts_file = self._normalize_known_hosts_path(
                    known_hosts_input
                )
            self.bindings[repository_id] = GitRepositoryBinding(
                repository_id=repository_id,
                remote=remote,
                full_name=str(binding.full_name or ""),
                writable=binding.writable is True,
                ssh_auth_sock=ssh_auth_sock,
                ssh_known_hosts_file=ssh_known_hosts_file,
            )
        if not self.bindings:
            raise ResolutionError("at least one Git repository binding is required")
        self.git_env = dict(git_env or {})
        blocked_transport_env = {
            "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT",
            "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS",
            "HOME", "USERPROFILE", "XDG_CONFIG_HOME", "CURL_HOME",
        }
        git_env_keys = {key.upper() for key in self.git_env}
        if blocked_transport_env.intersection(git_env_keys) or any(
            key.startswith("GIT_") for key in git_env_keys
        ):
            raise ResolutionError(
                "Git environment attempts to override repository/config/SSH isolation"
            )
        self._isolated_home = tempfile.TemporaryDirectory(
            prefix="learning-os-git-home-"
        )
        self._empty_git_template = tempfile.TemporaryDirectory(
            prefix="learning-os-git-template-"
        )
        self._tempdirs: list[tempfile.TemporaryDirectory] = []
        self._tempdirs_lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        first_error = None
        with self._tempdirs_lock:
            if self._closed:
                return
            self._closed = True
            while self._tempdirs:
                tempdir = self._tempdirs.pop()
                try:
                    tempdir.cleanup()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        for tempdir in (self._empty_git_template, self._isolated_home):
            try:
                tempdir.cleanup()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise ResolutionError("repository provider cleanup failed") from first_error

    def release_materialization(
        self, snapshot: MaterializedRepository
    ) -> None:
        target = snapshot.root.resolve()
        owned = None
        with self._tempdirs_lock:
            for tempdir in self._tempdirs:
                if Path(tempdir.name).resolve() == target:
                    owned = tempdir
                    self._tempdirs.remove(tempdir)
                    break
        if owned is not None:
            owned.cleanup()

    @staticmethod
    def _absolute_host_path(
        value: str, *, windows_host: bool | None = None
    ) -> bool:
        if windows_host is None:
            windows_host = os.name == "nt"
        if windows_host:
            return (
                value.startswith("\\\\")
                or value.startswith("//")
                or bool(re.match(r"^[A-Za-z]:[/\\]", value))
            )
        return value.startswith("/")

    @classmethod
    def _normalize_known_hosts_path(cls, value: str) -> str:
        def validate(candidate: str) -> None:
            if any(char.isspace() for char in candidate):
                raise ResolutionError(
                    "binding.ssh_known_hosts_file must not contain whitespace"
                )
            if (
                chr(92) in candidate
                or any(token in candidate for token in ("%", "$", "'", '"'))
            ):
                raise ResolutionError(
                    "binding.ssh_known_hosts_file must not contain OpenSSH "
                    "tokens or escapes"
                )

        validate(value)
        if not cls._absolute_host_path(value):
            raise ResolutionError(
                "binding.ssh_known_hosts_file must be an absolute host path"
            )
        normalized = os.path.abspath(value).replace(chr(92), "/")
        if not cls._absolute_host_path(normalized):
            raise ResolutionError(
                "binding.ssh_known_hosts_file normalization is not absolute"
            )
        validate(normalized)
        return normalized

    @staticmethod
    def _fetch_process_kwargs() -> dict[str, object]:
        if os.name == "nt":
            return {
                "creationflags": getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            }
        return {"start_new_session": True}

    @staticmethod
    def _windows_descendant_pids(root_pid: int) -> tuple[int, ...]:
        if os.name != "nt":
            return ()
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.argtypes = [
                wintypes.DWORD, wintypes.DWORD
            ]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
            ]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
            ]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            invalid = ctypes.c_void_p(-1).value
            if snapshot == invalid:
                return ()
            pairs: list[tuple[int, int]] = []
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            try:
                if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    while True:
                        pairs.append((
                            int(entry.th32ProcessID),
                            int(entry.th32ParentProcessID),
                        ))
                        if not kernel32.Process32NextW(
                            snapshot, ctypes.byref(entry)
                        ):
                            break
            finally:
                kernel32.CloseHandle(snapshot)
        except (AttributeError, OSError, ValueError):
            return ()

        descendants: list[int] = []
        frontier = {root_pid}
        seen = {root_pid}
        while frontier:
            next_frontier = {
                pid for pid, parent in pairs
                if parent in frontier
                and pid not in seen
                and pid not in {0, os.getpid()}
            }
            if not next_frontier:
                break
            descendants.extend(sorted(next_frontier))
            seen.update(next_frontier)
            frontier = next_frontier
        return tuple(descendants)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        leader_running = process.poll() is None
        if os.name == "nt":
            descendants = GitCliProvider._windows_descendant_pids(process.pid)
            targets = (process.pid, *reversed(descendants))
            for pid in targets:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
            if leader_running and process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                if leader_running:
                    process.kill()
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.SubprocessError:
                process.kill()
                process.wait()

    def _binding(self, repository_id: int) -> GitRepositoryBinding:
        repository_id = _positive_id(repository_id, "repository_id")
        try:
            return self.bindings[repository_id]
        except KeyError:
            raise ResolutionError(
                "repository ID is absent from host-trusted Git bindings"
            ) from None

    def _isolated_ssh_command(
        self, binding: GitRepositoryBinding | None = None
    ) -> str:
        args = [
            "ssh",
            "-F", os.devnull,
            "-o", "BatchMode=yes",
            "-o", "IdentityFile=none",
            "-o", "IdentitiesOnly=no",
            "-o", "PreferredAuthentications=publickey",
            "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no",
        ]
        known_hosts = (
            None if binding is None else binding.ssh_known_hosts_file
        )
        args.extend([
            "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=none",
            "-o", (
                f"UserKnownHostsFile={known_hosts}"
                if known_hosts is not None
                else "UserKnownHostsFile=none"
            ),
        ])
        return " ".join(shlex.quote(arg) for arg in args)

    def _env(
        self, binding: GitRepositoryBinding | None = None
    ) -> dict[str, str]:
        blocked_ambient = {
            "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS",
            "HOME", "USERPROFILE", "XDG_CONFIG_HOME", "CURL_HOME",
        }
        env = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
            and key.upper() not in blocked_ambient
        }
        env.update(self.git_env)
        if binding is not None and binding.ssh_auth_sock is not None:
            env["SSH_AUTH_SOCK"] = binding.ssh_auth_sock
        isolated_home = self._isolated_home.name
        env.update({
            "HOME": isolated_home,
            "USERPROFILE": isolated_home,
            "XDG_CONFIG_HOME": isolated_home,
            "CURL_HOME": isolated_home,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_VARIANT": "ssh",
            "GIT_SSH_COMMAND": self._isolated_ssh_command(binding),
            "SSH_ASKPASS_REQUIRE": "never",
        })
        return env

    def _run_remote_git_bounded(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: dict[str, str],
        timeout: int,
        cas: bool,
    ) -> bytes:
        try:
            process = subprocess.Popen(
                ["git", *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **self._fetch_process_kwargs(),
            )
        except OSError:
            if cas:
                raise CasConflict(
                    "Git transport failed during compare-and-swap"
                ) from None
            raise ResolutionError("Git remote transport failed") from None

        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        overflow = threading.Event()

        def drain(stream, buffer: bytearray) -> None:
            if stream is None:
                return
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                remaining = MAX_REMOTE_GIT_STREAM_BYTES - len(buffer)
                if remaining > 0:
                    buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()

        stdout_thread = threading.Thread(
            target=drain, args=(process.stdout, stdout_buffer),
            name="learning-os-git-remote-stdout", daemon=True,
        )
        stderr_thread = threading.Thread(
            target=drain, args=(process.stderr, stderr_buffer),
            name="learning-os-git-remote-stderr", daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            if overflow.is_set():
                self._terminate_process_tree(process)
                break
            if time.monotonic() > deadline:
                timed_out = True
                self._terminate_process_tree(process)
                break
            time.sleep(FETCH_POLL_SECONDS)

        if process.poll() is None:
            process.wait()
        stdout_thread.join(timeout=10)
        stderr_thread.join(timeout=10)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            self._terminate_process_tree(process)
            if cas:
                raise CasConflict(
                    "Git remote compare-and-swap output did not drain"
                )
            raise ResolutionError("Git remote output did not drain")
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

        if overflow.is_set():
            if cas:
                raise CasConflict(
                    "Git remote compare-and-swap output exceeded Runtime budget"
                )
            raise ResolutionError("Git remote output exceeded Runtime budget")
        if timed_out:
            if cas:
                raise CasConflict(
                    "Git transport timed out during compare-and-swap"
                )
            raise ResolutionError("Git remote transport timed out")
        if process.returncode:
            if cas:
                raise CasConflict(
                    "Git rejected compare-and-swap update"
                ) from None
            raise ResolutionError("Git transport or ref resolution failed")
        return bytes(stdout_buffer)

    def _run_git(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        cas: bool = False,
        timeout: int = 90,
        binding: GitRepositoryBinding | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> bytes:
        env = self._env(binding)
        if extra_env:
            env.update(extra_env)
        if binding is not None:
            if input_bytes is not None:
                if cas:
                    raise CasConflict(
                        "Git remote compare-and-swap stdin is unsupported"
                    )
                raise ResolutionError("Git remote stdin is unsupported")
            return self._run_remote_git_bounded(
                args, cwd=cwd, env=env, timeout=timeout, cas=cas
            )
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=env,
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            if cas:
                raise CasConflict(
                    "Git transport failed during compare-and-swap"
                ) from None
            raise ResolutionError("Git transport failed") from None
        if result.returncode:
            if cas:
                raise CasConflict("Git rejected compare-and-swap update") from None
            raise ResolutionError("Git transport or ref resolution failed")
        return result.stdout

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        cas: bool = False,
        binding: GitRepositoryBinding | None = None,
    ) -> str:
        return self._run_git(
            tuple(args), cwd=cwd, cas=cas, binding=binding
        ).decode("utf-8", errors="replace").strip()

    def _git_bytes(
        self,
        *args: str,
        cwd: Path | None = None,
        cas: bool = False,
        input_bytes: bytes | None = None,
        binding: GitRepositoryBinding | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> bytes:
        return self._run_git(
            tuple(args), cwd=cwd, cas=cas, input_bytes=input_bytes,
            binding=binding, extra_env=extra_env,
        )

    def _materialize_blobs(
        self,
        repo: Path,
        entries: list[tuple[str, str, str]],
        snapshot: Path,
    ) -> None:
        env = self._env()
        env["GIT_NO_LAZY_FETCH"] = "1"
        try:
            process = subprocess.Popen(
                ["git", "cat-file", "--batch"],
                cwd=repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **self._fetch_process_kwargs(),
            )
        except OSError:
            raise ResolutionError("Git blob materialization failed") from None

        try:
            if process.stdin is None or process.stdout is None:
                raise ResolutionError("Git blob materialization failed")
            for path, sha, mode in entries:
                output = snapshot.joinpath(*PurePosixPath(path).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    raise ResolutionError(
                        "materialized Git path aliases an existing snapshot entry"
                    )

                process.stdin.write((sha + "\n").encode("ascii"))
                process.stdin.flush()
                header = process.stdout.readline().rstrip(b"\n")
                parts = header.split()
                if len(parts) != 3:
                    raise ResolutionError("Git blob materialization failed")
                try:
                    reported_sha = parts[0].decode("ascii")
                    object_type = parts[1].decode("ascii")
                    size_text = parts[2].decode("ascii")
                except UnicodeDecodeError:
                    raise ResolutionError(
                        "Git blob materialization failed"
                    ) from None
                if (
                    reported_sha != sha
                    or object_type != "blob"
                    or not size_text.isdigit()
                ):
                    raise ResolutionError("Git blob materialization failed")
                size = int(size_text)
                if size > MAX_SNAPSHOT_BLOB_BYTES:
                    raise ResolutionError(
                        "Git snapshot blob exceeds Runtime size budget"
                    )

                try:
                    with output.open("xb") as handle:
                        remaining = size
                        while remaining:
                            chunk = process.stdout.read(
                                min(64 * 1024, remaining)
                            )
                            if not chunk:
                                raise ResolutionError(
                                    "Git blob materialization failed"
                                )
                            handle.write(chunk)
                            remaining -= len(chunk)
                except FileExistsError:
                    raise ResolutionError(
                        "materialized Git path aliases an existing snapshot entry"
                    ) from None
                if process.stdout.read(1) != b"\n":
                    raise ResolutionError("Git blob materialization failed")
                if not output.is_file():
                    raise ResolutionError(
                        "materialized Git tree entry is not a regular file"
                    )
                try:
                    output.chmod(0o755 if mode == "100755" else 0o644)
                except OSError:
                    pass

            process.stdin.close()
            process.wait(timeout=90)
            if process.returncode:
                raise ResolutionError("Git blob materialization failed")
        except (OSError, subprocess.SubprocessError):
            raise ResolutionError("Git blob materialization failed") from None
        finally:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                self._terminate_process_tree(process)
            if process.stdout is not None:
                process.stdout.close()

    @staticmethod
    def _windows_reserved_component(part: str) -> bool:
        normalized = part.rstrip(" .")
        stem = normalized.split(".", 1)[0].rstrip(" ").casefold()
        if stem in {"con", "prn", "aux", "nul", "conin$", "conout$"}:
            return True
        if re.fullmatch(r"(?:com|lpt)[1-9\u00b9\u00b2\u00b3]", stem):
            return True
        return bool(
            re.fullmatch(r"[^. ]{1,6}~[0-9]+(?:\.[^. ]{0,3})?", normalized)
        )

    @staticmethod
    def _git_sentinel_component(part: str) -> bool:
        normalized = unicodedata.normalize("NFC", part)
        return (
            normalized.casefold() == ".git"
            or unicodedata.normalize("NFC", normalized.upper()) == ".GIT"
        )

    @classmethod
    def _safe_path(cls, path: str) -> PurePosixPath:
        pure = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
            or any(cls._git_sentinel_component(part) for part in pure.parts)
            or any(part.endswith((" ", ".")) for part in pure.parts)
            or any(cls._windows_reserved_component(part) for part in pure.parts)
            or any(
                any(ord(char) < 32 or char in '<>"|?*' for char in part)
                for part in pure.parts
            )
            or ":" in path
            or pure.as_posix() != path
        ):
            raise ResolutionError("repository path is unsafe")
        return pure

    @classmethod
    def _decode_tree_object_record(
        cls, record: bytes
    ) -> tuple[str, str, str, str]:
        try:
            header, raw_path = record.split(b"\t", 1)
            mode_raw, type_raw, sha_raw = header.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            object_type = type_raw.decode("ascii")
            sha = sha_raw.decode("ascii")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ResolutionError("Git tree contains an undecodable entry") from None
        if not EXACT_COMMIT.fullmatch(sha):
            raise ResolutionError("Git tree returned a non-exact object identity")
        if object_type == "tree":
            if mode != "040000":
                raise ResolutionError("Git tree contains an unsupported tree entry")
        elif object_type == "blob":
            if mode not in {"100644", "100755"}:
                raise ResolutionError(
                    "Git tree contains an unsupported non-regular entry"
                )
        else:
            raise ResolutionError(
                "Git tree contains an unsupported non-regular entry"
            )
        cls._safe_path(path)
        return path, sha, mode, object_type

    @classmethod
    def _decode_tree_record(cls, record: bytes) -> tuple[str, str, str]:
        path, sha, mode, object_type = cls._decode_tree_object_record(record)
        if object_type != "blob":
            raise ResolutionError(
                "Git tree contains an unsupported non-regular entry"
            )
        return path, sha, mode

    def _object_header(
        self, repo: Path, sha: str
    ) -> tuple[str, int]:
        if not EXACT_COMMIT.fullmatch(sha):
            raise ResolutionError("Git object identity is not exact")
        raw = self._git_bytes(
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            cwd=repo,
            input_bytes=(sha + "\n").encode("ascii"),
            extra_env={"GIT_NO_LAZY_FETCH": "1"},
        )
        try:
            line = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise ResolutionError("Git object header is invalid") from None
        parts = line.split()
        if len(parts) != 3 or parts[0] != sha or not parts[2].isdigit():
            raise ResolutionError("Git object header is invalid")
        object_type, size = parts[1], int(parts[2])
        if (
            object_type in {"commit", "tag", "tree"}
            and size > MAX_GIT_METADATA_OBJECT_BYTES
        ):
            raise ResolutionError(
                "Git metadata object exceeds Runtime expanded-size budget"
            )
        return object_type, size

    def _resolve_fetched_commit(self, repo: Path) -> str:
        current = self._git(
            "rev-parse", "--verify", "FETCH_HEAD", cwd=repo
        )
        for _ in range(MAX_TAG_PEEL_DEPTH):
            object_type, _ = self._object_header(repo, current)
            if object_type == "commit":
                return current
            if object_type != "tag":
                raise ResolutionError(
                    "fetched Git object does not resolve to a commit"
                )
            raw = self._git_bytes(
                "cat-file", "tag", current,
                cwd=repo,
                extra_env={"GIT_NO_LAZY_FETCH": "1"},
            )
            first = raw.splitlines()[0] if raw else b""
            if not first.startswith(b"object "):
                raise ResolutionError("Git tag object is malformed")
            try:
                current = first.split(b" ", 1)[1].decode("ascii")
            except (IndexError, UnicodeDecodeError):
                raise ResolutionError("Git tag target is malformed") from None
            if not EXACT_COMMIT.fullmatch(current):
                raise ResolutionError("Git tag target is not exact")
        raise ResolutionError("Git tag chain exceeds Runtime depth budget")

    def _commit_tree_sha(self, repo: Path, commit: str) -> str:
        object_type, _ = self._object_header(repo, commit)
        if object_type != "commit":
            raise ResolutionError("Git object is not a commit")
        raw = self._git_bytes(
            "cat-file", "commit", commit,
            cwd=repo,
            extra_env={"GIT_NO_LAZY_FETCH": "1"},
        )
        first = raw.splitlines()[0] if raw else b""
        if not first.startswith(b"tree "):
            raise ResolutionError("Git commit is missing a tree")
        try:
            tree_sha = first.split(b" ", 1)[1].decode("ascii")
        except (IndexError, UnicodeDecodeError):
            raise ResolutionError("Git commit tree is malformed") from None
        if not EXACT_COMMIT.fullmatch(tree_sha):
            raise ResolutionError("Git commit tree is not exact")
        return tree_sha

    @classmethod
    def _expanded_tree_path(
        cls,
        prefix: str,
        component: str,
        depth: int,
        expanded_bytes: int,
    ) -> tuple[str, int, int]:
        if len(PurePosixPath(component).parts) != 1:
            raise ResolutionError("Git tree entry is not one path component")
        component_size = len(component.encode("utf-8"))
        if component_size > MAX_SNAPSHOT_PATH_COMPONENT_BYTES:
            raise ResolutionError(
                "Git tree path component exceeds Runtime byte budget"
            )
        next_depth = depth + 1
        if next_depth > MAX_SNAPSHOT_PATH_DEPTH:
            raise ResolutionError("Git tree path exceeds Runtime depth budget")
        full_path = f"{prefix}/{component}" if prefix else component
        full_size = len(full_path.encode("utf-8"))
        if full_size > MAX_SNAPSHOT_PATH_BYTES:
            raise ResolutionError("Git tree path exceeds Runtime byte budget")
        next_expanded = expanded_bytes + full_size
        if next_expanded > MAX_SNAPSHOT_EXPANDED_PATH_BYTES:
            raise ResolutionError(
                "Git tree expanded paths exceed Runtime aggregate byte budget"
            )
        cls._safe_path(full_path)
        return full_path, next_depth, next_expanded

    def _tree_objects(
        self, repo: Path, commit: str
    ) -> list[tuple[str, str, str, str]]:
        root_tree = self._commit_tree_sha(repo, commit)
        pending_trees: list[tuple[str, str, int]] = [("", root_tree, 0)]
        objects: list[tuple[str, str, str, str]] = []
        listing_bytes = 0
        expanded_path_bytes = 0
        tree_cache: dict[str, bytes] = {}
        unique_trees = {root_tree}

        while pending_trees:
            prefix, tree_sha, depth = pending_trees.pop()
            raw = tree_cache.get(tree_sha)
            if raw is None:
                object_type, _ = self._object_header(repo, tree_sha)
                if object_type != "tree":
                    raise ResolutionError("Git tree identity is not a tree")
                raw = self._git_bytes(
                    "ls-tree", "-z", tree_sha,
                    cwd=repo,
                    extra_env={"GIT_NO_LAZY_FETCH": "1"},
                )
                if not raw:
                    raise ResolutionError("Git tree contains an empty directory")
                tree_cache[tree_sha] = raw
            listing_bytes += len(raw)
            if listing_bytes > MAX_SNAPSHOT_TREE_LIST_BYTES:
                raise ResolutionError(
                    "Git tree listing exceeds Runtime byte budget"
                )
            for record in (item for item in raw.split(b"\x00") if item):
                path, sha, mode, kind = self._decode_tree_object_record(
                    record
                )
                full_path, entry_depth, expanded_path_bytes = (
                    self._expanded_tree_path(
                        prefix, path, depth, expanded_path_bytes
                    )
                )
                objects.append((full_path, sha, mode, kind))
                if len(objects) > MAX_SNAPSHOT_TREE_ENTRIES:
                    raise ResolutionError(
                        "Git tree exceeds Runtime entry budget"
                    )
                if kind == "tree":
                    if sha not in unique_trees:
                        unique_trees.add(sha)
                        if len(unique_trees) > MAX_SNAPSHOT_TREE_OBJECTS:
                            raise ResolutionError(
                                "Git tree exceeds Runtime subtree budget"
                            )
                    pending_trees.append((full_path, sha, entry_depth))
        return objects

    def _tree_entries(
        self, repo: Path, commit: str
    ) -> list[tuple[str, str, str]]:
        return [
            (path, sha, mode)
            for path, sha, mode, object_type in self._tree_objects(repo, commit)
            if object_type == "blob"
        ]

    @staticmethod
    def _portable_snapshot_keys(path: str) -> tuple[str, str]:
        normalized = [
            unicodedata.normalize("NFC", part)
            for part in PurePosixPath(path).parts
        ]
        casefold_key = "/".join(
            unicodedata.normalize("NFC", part.casefold())
            for part in normalized
        )
        win32_upper_key = "/".join(
            unicodedata.normalize("NFC", part.upper())
            for part in normalized
        )
        return casefold_key, win32_upper_key

    def _validate_snapshot_budget(
        self,
        repo: Path,
        objects: list[tuple[str, str, str, str]],
        *,
        fetched_blob_cap: int | None = None,
    ) -> int:
        blob_shas = [
            sha for _, sha, _, kind in objects if kind == "blob"
        ]
        if not blob_shas:
            return 0
        unique_shas = list(dict.fromkeys(blob_shas))
        query = ("\n".join(unique_shas) + "\n").encode("ascii")
        raw = self._git_bytes(
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            cwd=repo,
            input_bytes=query,
            extra_env={"GIT_NO_LAZY_FETCH": "1"},
        )
        sizes: dict[str, int] = {}
        try:
            lines = raw.decode("ascii").splitlines()
        except UnicodeDecodeError:
            raise ResolutionError("Git object-size response is invalid") from None
        for line in lines:
            parts = line.split()
            if (
                len(parts) == 2
                and EXACT_COMMIT.fullmatch(parts[0])
                and parts[1] == "missing"
            ):
                raise ResolutionError(
                    "Git snapshot blob exceeds Runtime size budget "
                    "or was omitted by bounded fetch"
                )
            if len(parts) != 3:
                raise ResolutionError("Git object-size response is invalid")
            sha, object_type, size_text = parts
            if (
                not EXACT_COMMIT.fullmatch(sha)
                or object_type != "blob"
                or not size_text.isdigit()
            ):
                raise ResolutionError("Git object-size response is invalid")
            sizes[sha] = int(size_text)
        if set(sizes) != set(unique_shas):
            raise ResolutionError("Git object-size response is incomplete")

        total = 0
        for sha in blob_shas:
            size = sizes[sha]
            if size > MAX_SNAPSHOT_BLOB_BYTES:
                raise ResolutionError(
                    "Git snapshot blob exceeds Runtime size budget"
                )
            if fetched_blob_cap is not None and size > fetched_blob_cap:
                raise ResolutionError(
                    "Git snapshot blob exceeds conservative bounded-fetch budget"
                )
            total += size
            if total > MAX_SNAPSHOT_TOTAL_BLOB_BYTES:
                raise ResolutionError(
                    "Git snapshot exceeds Runtime total-size budget"
                )
        return total

    def _verify_regular_tree(
        self, repo: Path, commit: str
    ) -> list[tuple[str, str, str, str]]:
        objects = self._tree_objects(repo, commit)
        seen_casefold: dict[str, str] = {}
        seen_win32_upper: dict[str, str] = {}
        nonempty_directories: set[str] = set()
        for path, _, _, kind in objects:
            if kind != "blob":
                continue
            parts = PurePosixPath(path).parts
            for index in range(1, len(parts)):
                nonempty_directories.add("/".join(parts[:index]))
        for path, _, _, kind in objects:
            casefold_key, win32_upper_key = self._portable_snapshot_keys(path)
            for seen, key in (
                (seen_casefold, casefold_key),
                (seen_win32_upper, win32_upper_key),
            ):
                previous = seen.get(key)
                if previous is not None:
                    raise ResolutionError(
                        "Git tree contains duplicate or "
                        "filesystem-equivalent path aliases"
                    )
                seen[key] = path
            if kind == "tree" and path not in nonempty_directories:
                raise ResolutionError("Git tree contains an empty directory")
        return objects

    def _regular_blob(
        self,
        repo: Path,
        commit: str,
        path: PurePosixPath,
        *,
        cas: bool = False,
    ) -> tuple[str, str] | None:
        raw = self._git_bytes(
            "ls-tree", "-z", commit, "--", path.as_posix(),
            cwd=repo, cas=cas,
        )
        records = [record for record in raw.split(b"\x00") if record]
        if not records:
            return None
        if len(records) != 1:
            if cas:
                raise CasConflict("target path resolved ambiguously")
            raise ResolutionError("Git path resolved ambiguously")
        try:
            resolved_path, sha, mode = self._decode_tree_record(records[0])
        except ResolutionError as exc:
            if cas:
                raise CasConflict(str(exc)) from None
            raise
        if resolved_path != path.as_posix():
            if cas:
                raise CasConflict("target path did not resolve exactly")
            raise ResolutionError("Git path did not resolve exactly")
        return sha, mode

    @staticmethod
    def _parse_ls_remote(output: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                sha, ref = line.split("\t", 1)
            except ValueError:
                raise ResolutionError("Git remote returned malformed ref data") from None
            if not EXACT_COMMIT.fullmatch(sha):
                raise ResolutionError("Git remote returned a non-exact object")
            rows[ref] = sha
        return rows

    def _resolve_fetch_ref(
        self,
        binding: GitRepositoryBinding,
        ref: str,
        *,
        repo: Path | None = None,
    ) -> tuple[str, str | None]:
        ref = _nonempty(ref, "ref")
        if any(char in ref for char in "\x00\r\n") or ref.startswith("-"):
            raise ResolutionError("Git ref is unsafe")
        if EXACT_COMMIT.fullmatch(ref):
            return ref, None
        if ref.startswith("refs/"):
            self._git("check-ref-format", ref)
            if ref.startswith("refs/heads/"):
                return ref, ref
            if ref.startswith("refs/tags/"):
                return ref, None
            raise ResolutionError("unsupported fully qualified Git ref")
        self._git("check-ref-format", "--branch", ref)
        branch_ref = f"refs/heads/{ref}"
        tag_ref = f"refs/tags/{ref}"
        output = self._git(
            "ls-remote", "origin" if repo is not None else binding.remote,
            branch_ref, tag_ref, f"{tag_ref}^{{}}",
            cwd=repo,
            binding=binding,
        )
        rows = self._parse_ls_remote(output)
        has_branch = branch_ref in rows
        has_tag = tag_ref in rows or f"{tag_ref}^{{}}" in rows
        if has_branch and has_tag:
            raise ResolutionError("Git short ref is ambiguous between branch and tag")
        if has_branch:
            return branch_ref, branch_ref
        if has_tag:
            return tag_ref, None
        raise ResolutionError("Git remote did not resolve the requested ref")

    def _branch_ref(self, branch: str) -> str:
        branch = _nonempty(branch, "branch")
        if branch.startswith("refs/heads/"):
            self._git("check-ref-format", branch, cas=True)
            return branch
        if branch.startswith("refs/") or EXACT_COMMIT.fullmatch(branch):
            raise CasConflict("target branch is unsupported")
        try:
            self._git("check-ref-format", "--branch", branch, cas=True)
        except CasConflict:
            raise CasConflict("target branch is unsupported") from None
        return f"refs/heads/{branch}"

    def _init_repo(self, repo: Path) -> None:
        self._git(
            "init", "-q",
            f"--template={self._empty_git_template.name}",
            cwd=repo,
        )

    @staticmethod
    def _requires_filtered_fetch(remote: str) -> bool:
        # Every transport must prove bounded blob filtering. Local paths and
        # file:// remotes can support upload-pack filtering when explicitly
        # configured; otherwise the fetch fails closed instead of inflating an
        # unbounded blob before expanded-size validation can run.
        return True

    @staticmethod
    def _object_store_bytes(repo: Path) -> int:
        root = repo / ".git" / "objects"
        total = 0
        if not root.exists():
            return 0
        for candidate in root.rglob("*"):
            try:
                if candidate.is_file():
                    total += candidate.stat().st_size
                    if total > MAX_FETCH_OBJECT_BYTES:
                        return total
            except OSError:
                continue
        return total

    @staticmethod
    def _bounded_blob_fetch_cap(
        objects: list[tuple[str, str, str, str]],
    ) -> int | None:
        blob_count = sum(1 for _, _, _, kind in objects if kind == "blob")
        if blob_count == 0:
            return None
        cap = min(
            MAX_SNAPSHOT_BLOB_BYTES,
            MAX_SNAPSHOT_TOTAL_BLOB_BYTES // blob_count,
        )
        if cap < 1:
            raise ResolutionError(
                "Git snapshot blob count exceeds Runtime aggregate budget"
            )
        return cap

    def _require_blob_free_metadata_fetch(
        self,
        repo: Path,
        objects: list[tuple[str, str, str, str]],
    ) -> None:
        unique = list(dict.fromkeys(
            sha for _, sha, _, kind in objects if kind == "blob"
        ))
        if not unique:
            return
        raw = self._git_bytes(
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            cwd=repo,
            input_bytes=(("\n".join(unique) + "\n").encode("ascii")),
            extra_env={"GIT_NO_LAZY_FETCH": "1"},
        )
        lines = raw.decode("ascii", errors="strict").splitlines()
        if len(lines) != len(unique):
            raise ResolutionError(
                "Git metadata fetch returned unexpected blob state"
            )
        for sha, line in zip(unique, lines):
            if line != f"{sha} missing":
                raise ResolutionError(
                    "Git remote did not enforce bounded object filtering: "
                    "metadata fetch hydrated a selected blob"
                )

    def _fetch_ref(
        self,
        repo: Path,
        binding: GitRepositoryBinding,
        fetch_ref: str,
        *,
        filter_spec: str,
        refetch: bool = False,
    ) -> None:
        args = [
            "git", "-c", "protocol.version=2",
            "fetch", "-q", "--no-tags", "--depth=1",
        ]
        if refetch:
            args.append("--refetch")
        args.extend([
            f"--filter={filter_spec}",
            "origin", fetch_ref,
        ])
        try:
            process = subprocess.Popen(
                args,
                cwd=repo,
                env=self._env(binding),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **self._fetch_process_kwargs(),
            )
        except OSError:
            raise ResolutionError("Git bounded fetch failed") from None

        stderr_buffer = bytearray()
        stderr_overflow = [False]

        def drain_stderr() -> None:
            if process.stderr is None:
                return
            while True:
                chunk = process.stderr.read(64 * 1024)
                if not chunk:
                    break
                remaining = MAX_FETCH_STDERR_BYTES - len(stderr_buffer)
                if remaining > 0:
                    stderr_buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    stderr_overflow[0] = True

        stderr_thread = threading.Thread(
            target=drain_stderr,
            name="learning-os-git-fetch-stderr",
            daemon=True,
        )
        stderr_thread.start()

        deadline = time.monotonic() + 90
        exceeded = False
        timed_out = False
        while process.poll() is None:
            if self._object_store_bytes(repo) > MAX_FETCH_OBJECT_BYTES:
                exceeded = True
                self._terminate_process_tree(process)
                break
            if time.monotonic() > deadline:
                timed_out = True
                self._terminate_process_tree(process)
                break
            time.sleep(FETCH_POLL_SECONDS)

        if process.poll() is None:
            process.wait()
        stderr_thread.join(timeout=10)
        if stderr_thread.is_alive():
            self._terminate_process_tree(process)
            raise ResolutionError("Git fetch diagnostics did not drain")
        if process.stderr is not None:
            process.stderr.close()
        stderr = bytes(stderr_buffer)
        if stderr_overflow[0]:
            raise ResolutionError("Git fetch diagnostics exceeded Runtime budget")
        if self._object_store_bytes(repo) > MAX_FETCH_OBJECT_BYTES:
            exceeded = True
        if exceeded:
            raise ResolutionError(
                "Git fetch exceeded Runtime object-store budget"
            )
        if timed_out:
            raise ResolutionError("Git bounded fetch timed out")
        if process.returncode:
            raise ResolutionError(
                "Git remote does not support bounded fetch semantics"
            )

    def _checkout(
        self,
        binding: GitRepositoryBinding,
        ref: str,
    ) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        td = tempfile.TemporaryDirectory(prefix="learning-os-git-")
        repo = Path(td.name) / "repo"
        repo.mkdir()
        try:
            self._init_repo(repo)
            self._git("remote", "add", "origin", binding.remote, cwd=repo)
            fetch_ref, _ = self._resolve_fetch_ref(binding, ref, repo=repo)
            self._fetch_ref(
                repo, binding, fetch_ref, filter_spec="blob:none"
            )
            fetched = self._resolve_fetched_commit(repo)
            if EXACT_COMMIT.fullmatch(ref) and fetched != ref:
                raise ResolutionError(
                    "fetched Git commit does not match requested provenance"
                )
            if not EXACT_COMMIT.fullmatch(fetched):
                raise ResolutionError("Git did not resolve an exact commit")
            objects = self._verify_regular_tree(repo, fetched)
            self._require_blob_free_metadata_fetch(repo, objects)
            blob_cap = self._bounded_blob_fetch_cap(objects)
            if blob_cap is not None:
                self._fetch_ref(
                    repo,
                    binding,
                    fetched,
                    filter_spec=f"blob:limit={blob_cap + 1}",
                    refetch=True,
                )
            self._validate_snapshot_budget(
                repo, objects, fetched_blob_cap=blob_cap
            )
            return td, repo, fetched
        except Exception:
            td.cleanup()
            raise

    def _checkout_branch(
        self,
        binding: GitRepositoryBinding,
        branch: str,
    ) -> tuple[
        tempfile.TemporaryDirectory, Path, str, str, int, int | None
    ]:
        branch_ref = self._branch_ref(branch)
        td = tempfile.TemporaryDirectory(prefix="learning-os-git-")
        repo = Path(td.name) / "repo"
        repo.mkdir()
        try:
            self._init_repo(repo)
            self._git("remote", "add", "origin", binding.remote, cwd=repo)
            self._fetch_ref(
                repo, binding, branch_ref, filter_spec="blob:none"
            )
            fetched = self._resolve_fetched_commit(repo)
            if not EXACT_COMMIT.fullmatch(fetched):
                raise ResolutionError("Git did not resolve an exact branch head")
            objects = self._verify_regular_tree(repo, fetched)
            self._require_blob_free_metadata_fetch(repo, objects)
            blob_cap = self._bounded_blob_fetch_cap(objects)
            if blob_cap is not None:
                self._fetch_ref(
                    repo,
                    binding,
                    fetched,
                    filter_spec=f"blob:limit={blob_cap + 1}",
                    refetch=True,
                )
            snapshot_total = self._validate_snapshot_budget(
                repo, objects, fetched_blob_cap=blob_cap
            )
            self._git("reset", "-q", "--mixed", fetched, cwd=repo)
            return (
                td, repo, fetched, branch_ref, snapshot_total, blob_cap
            )
        except Exception:
            td.cleanup()
            raise

    def materialize(
        self, repository_id: int, ref: str
    ) -> MaterializedRepository:
        with self._tempdirs_lock:
            if self._closed:
                raise ResolutionError("repository provider is closed")
        binding = self._binding(repository_id)
        checkout_td, repo, commit = self._checkout(binding, ref)
        snapshot_td = tempfile.TemporaryDirectory(prefix="learning-os-snapshot-")
        snapshot = Path(snapshot_td.name)
        try:
            self._materialize_blobs(
                repo, self._tree_entries(repo, commit), snapshot
            )
        except Exception:
            snapshot_td.cleanup()
            raise
        finally:
            checkout_td.cleanup()
        with self._tempdirs_lock:
            if self._closed:
                snapshot_td.cleanup()
                raise ResolutionError("repository provider closed during materialization")
            self._tempdirs.append(snapshot_td)
        return MaterializedRepository(
            snapshot,
            binding.repository_id,
            commit,
            binding.full_name,
        )

    def read_text(
        self, repository_id: int, ref: str, path: str
    ) -> tuple[str, str, str]:
        binding = self._binding(repository_id)
        pure = self._safe_path(path)
        td, repo, commit = self._checkout(binding, ref)
        try:
            entry = self._regular_blob(repo, commit, pure)
            if entry is None:
                raise ResolutionError("Git path does not exist")
            blob, _ = entry
            size_text = self._git("cat-file", "-s", blob, cwd=repo)
            if not size_text.isdigit():
                raise ResolutionError("Git blob size is invalid")
            blob_size = int(size_text)
            if blob_size > MAX_TEXT_BLOB_BYTES:
                raise ResolutionError(
                    "Git text blob exceeds Runtime read limit"
                )
            raw = self._git_bytes("cat-file", "blob", blob, cwd=repo)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ResolutionError("Git content is not valid UTF-8") from None
            return text, blob, commit
        finally:
            td.cleanup()

    def update_text(
        self,
        repository_id: int,
        branch: str,
        path: str,
        content: str,
        expected_blob_sha: str,
        message: str,
        expected_ref_sha: str | None = None,
    ) -> str:
        binding = self._binding(repository_id)
        if binding.writable is not True:
            raise CasConflict("repository is read-only in this Runtime binding")
        if not EXACT_COMMIT.fullmatch(str(expected_blob_sha)):
            raise CasConflict("expected blob SHA must be exact")
        if expected_ref_sha is not None and not EXACT_COMMIT.fullmatch(
            str(expected_ref_sha)
        ):
            raise CasConflict("expected branch head SHA must be exact")
        pure = self._safe_path(path)
        (
            td, repo, commit, branch_ref, snapshot_total, blob_cap
        ) = self._checkout_branch(binding, branch)
        try:
            if expected_ref_sha is not None and commit != expected_ref_sha:
                raise CasConflict("branch head compare-and-swap mismatch")
            try:
                entry = self._regular_blob(repo, commit, pure, cas=True)
            except CasConflict:
                raise CasConflict(
                    "target path is unsafe, missing, or stale"
                ) from None
            if entry is None:
                raise CasConflict("target path is missing or stale")
            current_blob, mode = entry
            if current_blob != expected_blob_sha:
                raise CasConflict("target blob compare-and-swap mismatch")

            content_bytes = content.encode("utf-8")
            if len(content_bytes) > MAX_TEXT_BLOB_BYTES:
                raise CasConflict(
                    "replacement text exceeds Runtime write limit"
                )
            current_size_text = self._git(
                "cat-file", "-s", current_blob, cwd=repo, cas=True
            )
            if not current_size_text.isdigit():
                raise CasConflict("current blob size is invalid")
            next_snapshot_total = (
                snapshot_total
                - int(current_size_text)
                + len(content_bytes)
            )
            if next_snapshot_total > MAX_SNAPSHOT_TOTAL_BLOB_BYTES:
                raise CasConflict(
                    "updated snapshot exceeds Runtime total-size budget"
                )
            if blob_cap is not None and len(content_bytes) > blob_cap:
                raise CasConflict(
                    "replacement text exceeds conservative bounded-fetch budget"
                )

            new_blob = self._git_bytes(
                "hash-object", "-w", "--stdin",
                cwd=repo, cas=True, input_bytes=content_bytes,
            ).decode("ascii").strip()
            if not EXACT_COMMIT.fullmatch(new_blob):
                raise CasConflict("Git did not return an exact staged blob")

            index_record = (
                f"{mode} {new_blob}\t".encode("ascii")
                + pure.as_posix().encode("utf-8")
                + b"\x00"
            )
            self._git_bytes(
                "update-index", "-z", "--index-info",
                cwd=repo, cas=True, input_bytes=index_record,
            )
            staged = self._git_bytes(
                "diff", "--cached", "--name-only", "-z",
                cwd=repo, cas=True,
            )
            staged_paths = [
                item for item in staged.split(b"\x00") if item
            ]
            expected_path = pure.as_posix().encode("utf-8")
            if staged_paths != [expected_path]:
                raise CasConflict(
                    "target update did not produce exactly one staged path"
                )
            staged_blob = self._git(
                "rev-parse", "--verify", f":{pure.as_posix()}",
                cwd=repo, cas=True,
            )
            if staged_blob != new_blob:
                raise CasConflict("staged blob does not match requested content")
            if self._git_bytes("cat-file", "blob", staged_blob, cwd=repo) != content_bytes:
                raise CasConflict("staged blob bytes do not match requested UTF-8")

            self._git(
                "-c", "user.name=Learning OS Runtime",
                "-c", "user.email=runtime@learning-os.invalid",
                "commit", "-q", "-m", message,
                cwd=repo, cas=True,
            )
            new_commit = self._git(
                "rev-parse", "--verify", "HEAD^{commit}",
                cwd=repo, cas=True,
            )
            if not EXACT_COMMIT.fullmatch(new_commit):
                raise CasConflict("Git update returned no exact commit")

            self._git(
                "push", "-q",
                f"--force-with-lease={branch_ref}:{commit}",
                "origin", f"HEAD:{branch_ref}",
                cwd=repo, cas=True, binding=binding,
            )
            return new_commit
        finally:
            td.cleanup()


class GitHubApiProvider:
    """GitHub REST materializer and Instance CAS writer."""
    def __init__(self, token: str | None = None, api_url: str = "https://api.github.com"):
        self.token = token or os.environ.get("LEARNING_OS_GITHUB_TOKEN")
        self.api_url = api_url.rstrip("/")
        self._tempdirs: list[tempfile.TemporaryDirectory] = []
        self._tempdirs_lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        first_error = None
        with self._tempdirs_lock:
            if self._closed:
                return
            self._closed = True
            while self._tempdirs:
                tempdir = self._tempdirs.pop()
                try:
                    tempdir.cleanup()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise ResolutionError("repository provider cleanup failed") from first_error

    def release_materialization(
        self, snapshot: MaterializedRepository
    ) -> None:
        target = snapshot.root.resolve()
        owned = None
        with self._tempdirs_lock:
            for tempdir in self._tempdirs:
                if Path(tempdir.name).resolve() == target:
                    owned = tempdir
                    self._tempdirs.remove(tempdir)
                    break
        if owned is not None:
            owned.cleanup()

    def _request(self, method: str, path: str, payload: dict | None = None) -> object:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.api_url + path, data=body, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "learning-os-v0.4-runtime")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 412, 422):
                raise CasConflict(f"GitHub rejected compare-and-swap ({exc.code})") from None
            raise ResolutionError(f"GitHub request failed ({exc.code})") from None
        except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResolutionError(f"GitHub request failed: {exc.__class__.__name__}") from None

    def _request_bytes(self, path: str) -> bytes:
        request = urllib.request.Request(self.api_url + path, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "learning-os-v0.4-runtime")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ResolutionError(f"GitHub archive request failed ({exc.code})") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ResolutionError(f"GitHub archive request failed: {exc.__class__.__name__}") from None

    def _repo(self, repository_id: int) -> dict:
        data = self._request("GET", f"/repositories/{repository_id}")
        if not isinstance(data, dict) or data.get("id") != repository_id:
            raise ResolutionError("GitHub repository numeric identity mismatch")
        return data

    def _commit(self, full_name: str, ref: str) -> str:
        quoted = urllib.parse.quote(ref, safe="")
        data = self._request("GET", f"/repos/{full_name}/commits/{quoted}")
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not EXACT_COMMIT.fullmatch(sha):
            raise ResolutionError("GitHub did not return an exact commit")
        return sha

    @staticmethod
    def _safe_output(root: Path, repository_path: str) -> Path:
        pure = PurePosixPath(repository_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ResolutionError("repository tree contains an unsafe path")
        output = root.joinpath(*pure.parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        return output

    def materialize(self, repository_id: int, ref: str) -> MaterializedRepository:
        with self._tempdirs_lock:
            if self._closed:
                raise ResolutionError("repository provider is closed")
        repo = self._repo(repository_id)
        full_name = _nonempty(repo.get("full_name"), "repository.full_name")
        commit = self._commit(full_name, ref)
        archive = self._request_bytes(f"/repos/{full_name}/zipball/{commit}")
        td = tempfile.TemporaryDirectory(prefix="learning-os-snapshot-")
        root = Path(td.name)
        try:
            with zipfile.ZipFile(BytesIO(archive)) as bundle:
                files = [entry for entry in bundle.infolist() if not entry.is_dir()]
                prefixes = {PurePosixPath(entry.filename).parts[0] for entry in files}
                if len(prefixes) != 1:
                    raise ResolutionError("GitHub archive has an ambiguous root")
                prefix = prefixes.pop()
                for entry in files:
                    pure = PurePosixPath(entry.filename)
                    if len(pure.parts) < 2 or pure.parts[0] != prefix:
                        raise ResolutionError("GitHub archive path is malformed")
                    unix_mode = (entry.external_attr >> 16) & 0o170000
                    if unix_mode not in (0, 0o100000):
                        raise ResolutionError("snapshot archive contains a non-regular file")
                    rel = PurePosixPath(*pure.parts[1:]).as_posix()
                    self._safe_output(root, rel).write_bytes(bundle.read(entry))
        except zipfile.BadZipFile:
            td.cleanup()
            raise ResolutionError("GitHub archive is not a valid ZIP") from None
        except Exception:
            td.cleanup()
            raise
        with self._tempdirs_lock:
            if self._closed:
                td.cleanup()
                raise ResolutionError("repository provider closed during materialization")
            self._tempdirs.append(td)
        return MaterializedRepository(root, repository_id, commit, full_name)

    def read_text(self, repository_id: int, ref: str, path: str) -> tuple[str, str, str]:
        repo = self._repo(repository_id)
        full_name = _nonempty(repo.get("full_name"), "repository.full_name")
        commit = self._commit(full_name, ref)
        quoted_path = urllib.parse.quote(path, safe="/")
        quoted_commit = urllib.parse.quote(commit, safe="")
        data = self._request(
            "GET",
            f"/repos/{full_name}/contents/{quoted_path}?ref={quoted_commit}",
        )
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            raise ResolutionError("GitHub content response is malformed")
        try:
            text = base64.b64decode(data["content"]).decode("utf-8")
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise ResolutionError("GitHub content is not valid UTF-8") from None
        return text, _nonempty(data.get("sha"), "content.sha"), commit

    def update_text(
        self,
        repository_id: int,
        branch: str,
        path: str,
        content: str,
        expected_blob_sha: str,
        message: str,
        expected_ref_sha: str | None = None,
    ) -> str:
        if expected_ref_sha is not None:
            raise CasConflict(
                "exact branch-head CAS is unsupported by GitHub REST provider"
            )
        repo = self._repo(repository_id)
        full_name = _nonempty(repo.get("full_name"), "repository.full_name")
        if not EXACT_COMMIT.fullmatch(str(expected_blob_sha)):
            raise CasConflict("expected blob SHA must be exact")

        quoted_path = urllib.parse.quote(path, safe="/")
        data = self._request(
            "PUT",
            f"/repos/{full_name}/contents/{quoted_path}",
            {
                "message": message,
                "content": base64.b64encode(
                    content.encode("utf-8")
                ).decode("ascii"),
                "sha": expected_blob_sha,
                "branch": branch,
            },
        )
        commit = data.get("commit") if isinstance(data, dict) else None
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not EXACT_COMMIT.fullmatch(sha):
            raise CasConflict("GitHub update returned no exact commit")
        return sha
