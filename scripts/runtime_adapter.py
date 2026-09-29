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
import subprocess
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

from scripts.validate_learning_os import RepositorySnapshot, validate_deployment

EXACT_COMMIT = re.compile(r"[0-9a-f]{40}")


class ResolutionError(RuntimeError):
    pass


class GuardRejected(RuntimeError):
    pass


class CasConflict(RuntimeError):
    pass


class TransitionRejected(RuntimeError):
    pass


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
        control = self.provider.materialize(rc["repository_id"], rc["canonical_ref"])
        if control.repository_id != rc["repository_id"]:
            raise ResolutionError("resolved Runtime-Control repository ID mismatch")
        contract_text, _, _ = self.provider.read_text(
            rc["repository_id"], rc["canonical_ref"], rc["contract_path"]
        )
        contract = _load_contract(contract_text)
        core_block = contract["core"]
        core_id = _positive_id(core_block.get("repository_id"), "core.repository_id")
        core_commit = _nonempty(core_block.get("commit"), "core.commit")
        if not EXACT_COMMIT.fullmatch(core_commit):
            raise ResolutionError("core.commit must be an exact 40-lowercase-hex commit")
        core = self.provider.materialize(core_id, core_commit)
        if core.repository_id != core_id or core.commit_sha != core_commit:
            raise ResolutionError("resolved Core provenance does not match the exact deployment pin")
        instance = self.provider.materialize(inst["repository_id"], inst["canonical_ref"])
        if instance.repository_id != inst["repository_id"]:
            raise ResolutionError("resolved Instance repository ID mismatch")
        findings = validate_deployment(
            RepositorySnapshot(control.root, control.repository_id, control.commit_sha),
            RepositorySnapshot(core.root, core.repository_id, core.commit_sha),
            RepositorySnapshot(instance.root, instance.repository_id, instance.commit_sha),
            locator,
        )
        errors = [finding.render() for finding in findings if finding.severity == "error"]
        if errors:
            raise ResolutionError("deployment validation failed:\n" + "\n".join(errors))
        dep = contract["deployment"]
        epoch = dep.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ResolutionError("deployment.epoch must be a positive integer")
        context = SessionDeploymentContext(
            deployment_id=_nonempty(dep.get("id"), "deployment.id"),
            epoch=epoch,
            core_repository_id=core_id,
            core_commit=core_commit,
            instance_repository_id=inst["repository_id"],
            runtime_control_repository_id=rc["repository_id"],
            runtime_control_ref=rc["canonical_ref"],
            contract_path=rc["contract_path"],
        )
        return ResolvedDeployment(context, control, core, instance)


class DeploymentGuard:
    def __init__(self, provider: RepositoryProvider):
        self.provider = provider

    def check(self, session: SessionDeploymentContext) -> dict:
        try:
            text, _, _ = self.provider.read_text(
                session.runtime_control_repository_id,
                session.runtime_control_ref,
                session.contract_path,
            )
            contract = _load_contract(text)
        except (ResolutionError, OSError, RuntimeError) as exc:
            raise GuardRejected(f"Runtime-Control fresh-read failed closed: {exc}") from None
        dep, core = contract["deployment"], contract["core"]
        for ok, message in (
            (dep.get("write_state") == "active", "deployment is not active"),
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
                absolute_socket = (
                    ssh_auth_sock.startswith("/")
                    or ssh_auth_sock.startswith("\\\\")
                    or bool(re.match(r"^[A-Za-z]:[/\\]", ssh_auth_sock))
                )
                if not absolute_socket:
                    raise ResolutionError(
                        "binding.ssh_auth_sock must be an absolute host path"
                    )
            ssh_known_hosts_file = None
            if binding.ssh_known_hosts_file is not None:
                known_hosts_input = _nonempty(
                    binding.ssh_known_hosts_file,
                    "binding.ssh_known_hosts_file",
                )
                if any(char.isspace() for char in known_hosts_input):
                    raise ResolutionError(
                        "binding.ssh_known_hosts_file must not contain whitespace"
                    )
                if any(
                    token in known_hosts_input
                    for token in ("%", "$", "'", '"')
                ):
                    raise ResolutionError(
                        "binding.ssh_known_hosts_file must not contain OpenSSH tokens"
                    )
                ssh_known_hosts_file = os.path.abspath(known_hosts_input)
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

    def close(self) -> None:
        while self._tempdirs:
            self._tempdirs.pop().cleanup()
        self._empty_git_template.cleanup()
        self._isolated_home.cleanup()

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

    def _run_git(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        cas: bool = False,
        timeout: int = 90,
        binding: GitRepositoryBinding | None = None,
    ) -> bytes:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=self._env(binding),
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
    ) -> bytes:
        return self._run_git(
            tuple(args), cwd=cwd, cas=cas, input_bytes=input_bytes,
            binding=binding,
        )

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

    @classmethod
    def _safe_path(cls, path: str) -> PurePosixPath:
        pure = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or pure.is_absolute()
            or ".." in pure.parts
            or any(part.lower() == ".git" for part in pure.parts)
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

    def _tree_objects(
        self, repo: Path, commit: str
    ) -> list[tuple[str, str, str, str]]:
        raw = self._git_bytes("ls-tree", "-r", "-t", "-z", commit, cwd=repo)
        return [
            self._decode_tree_object_record(record)
            for record in raw.split(b"\x00")
            if record
        ]

    def _tree_entries(
        self, repo: Path, commit: str
    ) -> list[tuple[str, str, str]]:
        return [
            (path, sha, mode)
            for path, sha, mode, object_type in self._tree_objects(repo, commit)
            if object_type == "blob"
        ]

    @staticmethod
    def _portable_snapshot_key(path: str) -> str:
        return unicodedata.normalize("NFC", path).casefold()

    def _verify_regular_tree(self, repo: Path, commit: str) -> None:
        objects = self._tree_objects(repo, commit)
        seen: dict[str, str] = {}
        nonempty_directories: set[str] = set()
        for path, _, _, kind in objects:
            if kind != "blob":
                continue
            parts = PurePosixPath(path).parts
            for index in range(1, len(parts)):
                nonempty_directories.add("/".join(parts[:index]))
        for path, _, _, kind in objects:
            key = self._portable_snapshot_key(path)
            previous = seen.get(key)
            if previous is not None:
                raise ResolutionError(
                    "Git tree contains duplicate or filesystem-equivalent path aliases"
                )
            seen[key] = path
            if kind == "tree" and path not in nonempty_directories:
                raise ResolutionError("Git tree contains an empty directory")

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
            "ls-remote", binding.remote,
            branch_ref, tag_ref, f"{tag_ref}^{{}}",
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

    def _fetch_ref(
        self,
        repo: Path,
        binding: GitRepositoryBinding,
        fetch_ref: str,
    ) -> None:
        try:
            self._git(
                "fetch", "-q", "--depth=1", "origin", fetch_ref,
                cwd=repo, binding=binding,
            )
        except ResolutionError:
            self._git(
                "fetch", "-q", "origin", fetch_ref,
                cwd=repo, binding=binding,
            )

    def _checkout(
        self,
        binding: GitRepositoryBinding,
        ref: str,
    ) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        fetch_ref, _ = self._resolve_fetch_ref(binding, ref)
        td = tempfile.TemporaryDirectory(prefix="learning-os-git-")
        repo = Path(td.name) / "repo"
        repo.mkdir()
        try:
            self._init_repo(repo)
            self._git("remote", "add", "origin", binding.remote, cwd=repo)
            self._fetch_ref(repo, binding, fetch_ref)
            fetched = self._git(
                "rev-parse", "--verify", "FETCH_HEAD^{commit}", cwd=repo
            )
            if EXACT_COMMIT.fullmatch(ref) and fetched != ref:
                raise ResolutionError(
                    "fetched Git commit does not match requested provenance"
                )
            if not EXACT_COMMIT.fullmatch(fetched):
                raise ResolutionError("Git did not resolve an exact commit")
            self._verify_regular_tree(repo, fetched)
            return td, repo, fetched
        except Exception:
            td.cleanup()
            raise

    def _checkout_branch(
        self,
        binding: GitRepositoryBinding,
        branch: str,
    ) -> tuple[tempfile.TemporaryDirectory, Path, str, str]:
        branch_ref = self._branch_ref(branch)
        td = tempfile.TemporaryDirectory(prefix="learning-os-git-")
        repo = Path(td.name) / "repo"
        repo.mkdir()
        try:
            self._init_repo(repo)
            self._git("remote", "add", "origin", binding.remote, cwd=repo)
            self._fetch_ref(repo, binding, branch_ref)
            fetched = self._git(
                "rev-parse", "--verify", "FETCH_HEAD^{commit}", cwd=repo
            )
            if not EXACT_COMMIT.fullmatch(fetched):
                raise ResolutionError("Git did not resolve an exact branch head")
            self._verify_regular_tree(repo, fetched)
            self._git("reset", "-q", "--mixed", fetched, cwd=repo)
            return td, repo, fetched, branch_ref
        except Exception:
            td.cleanup()
            raise

    def materialize(
        self, repository_id: int, ref: str
    ) -> MaterializedRepository:
        binding = self._binding(repository_id)
        checkout_td, repo, commit = self._checkout(binding, ref)
        snapshot_td = tempfile.TemporaryDirectory(prefix="learning-os-snapshot-")
        snapshot = Path(snapshot_td.name)
        try:
            for path, sha, mode in self._tree_entries(repo, commit):
                output = snapshot.joinpath(*PurePosixPath(path).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    raise ResolutionError(
                        "materialized Git path aliases an existing snapshot entry"
                    )
                output.write_bytes(
                    self._git_bytes("cat-file", "blob", sha, cwd=repo)
                )
                if not output.is_file():
                    raise ResolutionError(
                        "materialized Git tree entry is not a regular file"
                    )
                try:
                    output.chmod(0o755 if mode == "100755" else 0o644)
                except OSError:
                    pass
        except Exception:
            snapshot_td.cleanup()
            raise
        finally:
            checkout_td.cleanup()
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
        td, repo, commit, branch_ref = self._checkout_branch(binding, branch)
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

    def close(self) -> None:
        while self._tempdirs:
            self._tempdirs.pop().cleanup()

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
        repo = self._repo(repository_id)
        full_name = _nonempty(repo.get("full_name"), "repository.full_name")
        commit = self._commit(full_name, ref)
        archive = self._request_bytes(f"/repos/{full_name}/zipball/{commit}")
        td = tempfile.TemporaryDirectory(prefix="learning-os-snapshot-")
        self._tempdirs.append(td)
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
            raise ResolutionError("GitHub archive is not a valid ZIP") from None
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
        repo = self._repo(repository_id)
        full_name = _nonempty(repo.get("full_name"), "repository.full_name")
        if not EXACT_COMMIT.fullmatch(str(expected_blob_sha)):
            raise CasConflict("expected blob SHA must be exact")
        if expected_ref_sha is not None:
            raise CasConflict(
                "exact branch-head CAS is unsupported by GitHub REST provider"
            )

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
