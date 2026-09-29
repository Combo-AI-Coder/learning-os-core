from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from scripts.runtime_adapter import (
    CasConflict,
    DeploymentGuard,
    DeploymentResolver,
    GuardRejected,
    GitCliProvider,
    GitHubApiProvider,
    GitRepositoryBinding,
    MaterializedRepository,
    ResolutionError,
    TransitionRejected,
    pointer_rollback_allowed,
    validate_deployment_transition,
    validate_initial_cutover,
)

ROOT = Path(__file__).resolve().parents[1]
RC_ID, CORE_ID, INSTANCE_ID = 9000000101, 9000000102, 9000000103
CORE_COMMIT = "a" * 40
RC_COMMIT = "b" * 40
INSTANCE_COMMIT = "c" * 40


def locator(**runtime_control):
    return {
        "runtime_control": {
            "repository_id": RC_ID,
            "canonical_ref": "main",
            "contract_path": "deployment.yaml",
            **runtime_control,
        },
        "instance": {"repository_id": INSTANCE_ID, "canonical_ref": "main"},
    }


def contract(*, epoch=1, write_state="active", core_commit=CORE_COMMIT):
    return {
        "schema_version": "0.4",
        "document_type": "deployment_binding",
        "deployment": {
            "id": "dep-runtime-test",
            "topology": "split",
            "epoch": epoch,
            "write_state": write_state,
        },
        "core": {"repository_id": CORE_ID, "commit": core_commit},
    }


class FakeProvider:
    def __init__(self, control: Path, instance: Path):
        self.control = control
        self.instance = instance
        self.contract = contract()
        self.unavailable = False
        self.cas_sha = "d" * 40
        self.calls = []

    def materialize(self, repository_id, ref):
        self.calls.append(("materialize", repository_id, ref))
        if repository_id == RC_ID:
            self._write_contract()
            return MaterializedRepository(self.control, RC_ID, RC_COMMIT, "renamed/rc")
        if repository_id == CORE_ID:
            if ref != CORE_COMMIT:
                raise ResolutionError("exact Core unavailable")
            return MaterializedRepository(ROOT, CORE_ID, CORE_COMMIT, "renamed/core")
        if repository_id == INSTANCE_ID:
            return MaterializedRepository(self.instance, INSTANCE_ID, INSTANCE_COMMIT, "renamed/instance")
        raise ResolutionError("unknown numeric repository identity")

    def _write_contract(self):
        (self.control / "deployment.yaml").write_text(
            yaml.safe_dump(self.contract, sort_keys=False), encoding="utf-8"
        )

    def read_text(self, repository_id, ref, path):
        self.calls.append(("read", repository_id, ref, path))
        if self.unavailable:
            raise ResolutionError("outage")
        if repository_id != RC_ID or ref != "main" or path != "deployment.yaml":
            raise ResolutionError("wrong read")
        return yaml.safe_dump(self.contract, sort_keys=False), "e" * 40, RC_COMMIT

    def update_text(
        self,
        repository_id,
        branch,
        path,
        content,
        expected_blob_sha,
        message,
        expected_ref_sha=None,
    ):
        self.calls.append((
            "update", repository_id, branch, path,
            expected_blob_sha, expected_ref_sha,
        ))
        if expected_blob_sha != self.cas_sha:
            raise CasConflict("stale blob")
        return "f" * 40


class RuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        rc_td = tempfile.TemporaryDirectory()
        inst_td = tempfile.TemporaryDirectory()
        self.addCleanup(rc_td.cleanup)
        self.addCleanup(inst_td.cleanup)
        self.control = Path(rc_td.name)
        self.instance = Path(inst_td.name)
        (self.instance / "config").mkdir(parents=True)
        (self.instance / "README.md").write_text("# Synthetic Instance\n", encoding="utf-8")
        (self.instance / "config/instance.yaml").write_text(
            yaml.safe_dump({
                "schema_version": "0.4",
                "document_type": "instance_config",
                "product": {"id": "learning-os"},
                "instance": {"display_timezone": "Asia/Shanghai"},
            }, sort_keys=False),
            encoding="utf-8",
        )
        self.provider = FakeProvider(self.control, self.instance)

    def session(self):
        return DeploymentResolver(self.provider).resolve(locator()).context

    def test_zero_history_bootstrap_resolves_exact_planes(self):
        resolved = DeploymentResolver(self.provider).resolve(locator())
        self.assertEqual(CORE_COMMIT, resolved.context.core_commit)
        self.assertEqual(INSTANCE_ID, resolved.context.instance_repository_id)

    def test_navigation_rename_does_not_change_identity(self):
        resolved = DeploymentResolver(self.provider).resolve(locator(repository="old/name"))
        self.assertEqual(RC_ID, resolved.context.runtime_control_repository_id)

    def test_core_main_advance_is_not_used(self):
        resolved = DeploymentResolver(self.provider).resolve(locator())
        self.assertIn(("materialize", CORE_ID, CORE_COMMIT), self.provider.calls)
        self.assertEqual(CORE_COMMIT, resolved.context.core_commit)

    def test_exact_core_unavailable_fails_closed(self):
        self.provider.contract = contract(core_commit="9" * 40)
        with self.assertRaises(ResolutionError):
            DeploymentResolver(self.provider).resolve(locator())

    def test_wrong_instance_identity_fails_closed(self):
        bad = locator()
        bad["instance"]["repository_id"] = INSTANCE_ID + 10
        with self.assertRaises(ResolutionError):
            DeploymentResolver(self.provider).resolve(bad)

    def test_malformed_control_fails_closed(self):
        self.provider.read_text = lambda *args: ("[]", "e" * 40, RC_COMMIT)
        with self.assertRaises(ResolutionError):
            DeploymentResolver(self.provider).resolve(locator())

    def test_active_fresh_session_guard_passes(self):
        DeploymentGuard(self.provider).check(self.session())

    def test_frozen_guard_blocks_before_mutation(self):
        session = self.session()
        self.provider.contract = contract(write_state="frozen")
        with self.assertRaises(GuardRejected):
            DeploymentGuard(self.provider).guarded_update(
                session,
                branch="main",
                path="learner/model.yaml",
                content="x",
                expected_blob_sha=self.provider.cas_sha,
                message="test",
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_stale_epoch_blocks_before_mutation(self):
        session = self.session()
        self.provider.contract = contract(epoch=2)
        with self.assertRaises(GuardRejected):
            DeploymentGuard(self.provider).check(session)

    def test_core_promotion_blocks_stale_session(self):
        session = self.session()
        self.provider.contract = contract(epoch=2, core_commit="9" * 40)
        with self.assertRaises(GuardRejected):
            DeploymentGuard(self.provider).check(session)

    def test_control_outage_blocks_write(self):
        session = self.session()
        self.provider.unavailable = True
        with self.assertRaises(GuardRejected):
            DeploymentGuard(self.provider).check(session)

    def test_generation_guard_is_independent(self):
        session = self.session()
        with self.assertRaisesRegex(GuardRejected, "generation"):
            DeploymentGuard(self.provider).guarded_update(
                session,
                branch="main",
                path="learner/model.yaml",
                content="x",
                expected_blob_sha=self.provider.cas_sha,
                message="test",
                expected_generation=9,
                generation_reader=lambda: 10,
            )
        self.assertFalse(any(call[0] == "update" for call in self.provider.calls))

    def test_target_cas_is_independent(self):
        session = self.session()
        with self.assertRaises(CasConflict):
            DeploymentGuard(self.provider).guarded_update(
                session,
                branch="main",
                path="learner/model.yaml",
                content="x",
                expected_blob_sha="0" * 40,
                message="test",
            )

    def test_successful_guarded_update_orders_guard_before_cas(self):
        session = self.session()
        result = DeploymentGuard(self.provider).guarded_update(
            session,
            branch="main",
            path="learner/model.yaml",
            content="x",
            expected_blob_sha=self.provider.cas_sha,
            message="test",
            expected_generation=9,
            generation_reader=lambda: 9,
        )
        self.assertEqual("f" * 40, result)
        names = [call[0] for call in self.provider.calls]
        self.assertLess(names.index("read"), names.index("update"))

    def test_guarded_update_omits_head_keyword_when_not_requested(self):
        session = self.session()
        calls = []

        def legacy_update(
            repository_id, branch, path, content,
            expected_blob_sha, message,
        ):
            calls.append((
                repository_id, branch, path,
                expected_blob_sha, message,
            ))
            return "f" * 40

        self.provider.update_text = legacy_update
        result = DeploymentGuard(self.provider).guarded_update(
            session,
            branch="main",
            path="learner/model.yaml",
            content="x",
            expected_blob_sha=self.provider.cas_sha,
            message="legacy provider",
        )
        self.assertEqual("f" * 40, result)
        self.assertEqual(1, len(calls))

    def test_guarded_update_forwards_exact_branch_head_precondition(self):
        session = self.session()
        authority_head = "a" * 40
        DeploymentGuard(self.provider).guarded_update(
            session,
            branch="main",
            path="learner/model.yaml",
            content="x",
            expected_blob_sha=self.provider.cas_sha,
            message="test branch-head fence",
            expected_ref_sha=authority_head,
        )
        update = next(
            call for call in self.provider.calls if call[0] == "update"
        )
        self.assertEqual(authority_head, update[5])


class GitCliProviderTests(unittest.TestCase):
    REPO_ID = 9000000201

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self._git("init", "--bare", "-q", str(self.remote))
        self._git("init", "-q", str(self.seed))
        self._git("checkout", "-q", "-b", "main", cwd=self.seed)
        self._git("config", "user.name", "Synthetic Runtime Test", cwd=self.seed)
        self._git("config", "user.email", "runtime-test@invalid.local", cwd=self.seed)
        (self.seed / "state.txt").write_text("one\n", encoding="utf-8", newline="\n")
        self._git("add", "state.txt", cwd=self.seed)
        self._git("commit", "-q", "-m", "seed", cwd=self.seed)
        self._git("remote", "add", "origin", str(self.remote), cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        self.initial_commit = self._git("rev-parse", "HEAD", cwd=self.seed)
        self.initial_blob = self._git("rev-parse", "HEAD:state.txt", cwd=self.seed)

    @staticmethod
    def _git(*args, cwd=None, input_text=None):
        env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            raise AssertionError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def provider(self, *, writable=True):
        provider = GitCliProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                str(self.remote),
                "synthetic/instance",
                writable=writable,
            )
        ])
        self.addCleanup(provider.close)
        return provider

    def test_materialize_uses_host_bound_identity_and_strips_git_metadata(self):
        snapshot = self.provider().materialize(self.REPO_ID, "main")
        self.assertEqual(self.REPO_ID, snapshot.repository_id)
        self.assertEqual(self.initial_commit, snapshot.commit_sha)
        self.assertEqual("synthetic/instance", snapshot.full_name)
        self.assertEqual("one\n", (snapshot.root / "state.txt").read_text(encoding="utf-8"))
        self.assertFalse((snapshot.root / ".git").exists())

    def test_materialize_streams_blob_content_instead_of_buffering_it(self):
        provider = self.provider()
        original_git_bytes = provider._git_bytes

        def reject_buffered_blob(*args, **kwargs):
            if len(args) >= 2 and args[0] == "cat-file" and args[1] == "blob":
                raise AssertionError("materialize must stream blob content")
            return original_git_bytes(*args, **kwargs)

        provider._git_bytes = reject_buffered_blob  # type: ignore[method-assign]
        snapshot = provider.materialize(self.REPO_ID, "main")
        self.assertEqual(
            "one\n",
            (snapshot.root / "state.txt").read_text(encoding="utf-8"),
        )

    def test_remote_transport_requires_bounded_filtering(self):
        provider = self.provider()
        self.assertTrue(
            provider._requires_filtered_fetch(
                "https://example.invalid/repo.git"
            )
        )
        self.assertTrue(
            provider._requires_filtered_fetch(
                "git@example.invalid:repo.git"
            )
        )
        self.assertFalse(
            provider._requires_filtered_fetch(str(self.remote))
        )

    def test_fetch_object_store_budget_fails_closed(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_FETCH_OBJECT_BYTES", 1
        ):
            with self.assertRaisesRegex(
                ResolutionError, "object-store budget"
            ):
                provider.materialize(self.REPO_ID, "main")

    def test_transient_git_init_uses_provider_owned_empty_template(self):
        provider = self.provider()
        original_git = provider._git
        init_calls = []

        def recording_git(*args, cwd=None, cas=False, binding=None):
            if args and args[0] == "init":
                init_calls.append(args)
            return original_git(*args, cwd=cwd, cas=cas, binding=binding)

        provider._git = recording_git  # type: ignore[method-assign]
        provider.materialize(self.REPO_ID, "main")
        provider.update_text(
            self.REPO_ID,
            "main",
            "state.txt",
            "two\n",
            self.initial_blob,
            "test: empty template",
        )
        self.assertEqual(2, len(init_calls))
        for args in init_calls:
            template = next(
                value.split("=", 1)[1]
                for value in args
                if value.startswith("--template=")
            )
            self.assertEqual(provider._empty_git_template.name, template)
            self.assertEqual([], list(Path(template).iterdir()))

    def test_exact_commit_materialization_is_fetched_from_bound_remote(self):
        snapshot = self.provider().materialize(self.REPO_ID, self.initial_commit)
        self.assertEqual(self.initial_commit, snapshot.commit_sha)

    def test_unknown_numeric_identity_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "host-trusted"):
            self.provider().materialize(self.REPO_ID + 1, "main")

    def test_read_text_returns_exact_blob_and_commit(self):
        text, blob, commit = self.provider().read_text(
            self.REPO_ID, "main", "state.txt"
        )
        self.assertEqual("one\n", text)
        self.assertEqual(self.initial_blob, blob)
        self.assertEqual(self.initial_commit, commit)

    def test_read_text_rejects_blob_above_runtime_limit_before_buffering(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_TEXT_BLOB_BYTES", 3
        ):
            with self.assertRaisesRegex(ResolutionError, "exceeds"):
                provider.read_text(self.REPO_ID, "main", "state.txt")

    def test_unsafe_path_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "unsafe"):
            self.provider().read_text(self.REPO_ID, "main", "../state.txt")

    def test_ambiguous_dot_segment_path_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "unsafe"):
            self.provider().read_text(self.REPO_ID, "main", "./state.txt")

    def test_unsafe_git_environment_override_fails_closed(self):
        for key in (
            "GIT_DIR", "GIT_CONFIG_COUNT", "GIT_SSH_COMMAND",
            "SSH_AUTH_SOCK", "HOME", "USERPROFILE",
            "git_config_count", "git_ssh_command", "home", "userprofile",
        ):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ResolutionError, "environment"):
                    GitCliProvider(
                        [GitRepositoryBinding(self.REPO_ID, str(self.remote))],
                        git_env={key: "1"},
                    )

    def test_explicit_ssh_transport_disables_user_config_and_disk_identities(self):
        socket = (
            "C:/run/ssh-agent.sock"
            if os.name == "nt"
            else "/run/ssh-agent.sock"
        )
        provider = GitCliProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                str(self.remote),
                ssh_auth_sock=socket,
                ssh_known_hosts_file="/run/github_known_hosts",
            )
        ])
        self.addCleanup(provider.close)
        binding = provider._binding(self.REPO_ID)
        env = provider._env(binding)
        self.assertEqual(socket, env["SSH_AUTH_SOCK"])
        self.assertNotIn("SSH_AGENT_PID", env)
        command = env["GIT_SSH_COMMAND"]
        self.assertIn("-F", command)
        self.assertIn(os.devnull, command)
        self.assertIn("IdentityFile=none", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("PasswordAuthentication=no", command)
        self.assertIn("KbdInteractiveAuthentication=no", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(
            f"UserKnownHostsFile={os.path.abspath('/run/github_known_hosts')}",
            command,
        )
        self.assertIn("GlobalKnownHostsFile=none", command)

    def test_relative_ssh_agent_socket_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "absolute host path"):
            GitCliProvider([
                GitRepositoryBinding(
                    self.REPO_ID,
                    str(self.remote),
                    ssh_auth_sock="agent.sock",
                )
            ])

    def test_host_path_semantics_reject_windows_root_relative_socket(self):
        self.assertFalse(
            GitCliProvider._absolute_host_path(
                "/agent.sock", windows_host=True
            )
        )
        self.assertTrue(
            GitCliProvider._absolute_host_path(
                "C:/agent.sock", windows_host=True
            )
        )
        self.assertTrue(
            GitCliProvider._absolute_host_path(
                "//server/share/agent.sock", windows_host=True
            )
        )
        self.assertTrue(
            GitCliProvider._absolute_host_path(
                "/agent.sock", windows_host=False
            )
        )

    def test_known_hosts_path_with_whitespace_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "whitespace"):
            GitCliProvider([
                GitRepositoryBinding(
                    self.REPO_ID,
                    str(self.remote),
                    ssh_known_hosts_file="known hosts",
                )
            ])

    def test_known_hosts_path_with_openssh_token_fails_closed(self):
        for path in (
            "/run/%h_known_hosts",
            "/run/${USER}/known_hosts",
            "$HOME/known_hosts",
            "/run/'team'/known_hosts",
            '/run/"team"/known_hosts',
            r"/run/team\\known_hosts",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ResolutionError, "OpenSSH tokens"):
                    GitCliProvider([
                        GitRepositoryBinding(
                            self.REPO_ID,
                            str(self.remote),
                            ssh_known_hosts_file=path,
                        )
                    ])

    def test_unpinned_ssh_transport_disables_ambient_known_hosts(self):
        provider = GitCliProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                "ssh://git@example.invalid/instance.git",
            )
        ])
        self.addCleanup(provider.close)
        command = provider._env(
            provider._binding(self.REPO_ID)
        )["GIT_SSH_COMMAND"]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("GlobalKnownHostsFile=none", command)
        self.assertIn("UserKnownHostsFile=none", command)

    def test_relative_known_hosts_path_is_resolved_at_construction(self):
        provider = GitCliProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                str(self.remote),
                ssh_known_hosts_file="relative-known-hosts",
            )
        ])
        self.addCleanup(provider.close)
        expected = os.path.abspath("relative-known-hosts")
        binding = provider._binding(self.REPO_ID)
        self.assertEqual(expected, binding.ssh_known_hosts_file)
        self.assertIn(
            f"UserKnownHostsFile={expected}",
            provider._env(binding)["GIT_SSH_COMMAND"],
        )

    def test_ssh_agent_is_scoped_to_its_repository_binding(self):
        instance_id = self.REPO_ID
        control_id = self.REPO_ID + 1
        socket = (
            "C:/run/instance-agent.sock"
            if os.name == "nt"
            else "/run/instance-agent.sock"
        )
        provider = GitCliProvider([
            GitRepositoryBinding(
                instance_id,
                "ssh://git@example.invalid/instance.git",
                writable=True,
                ssh_auth_sock=socket,
            ),
            GitRepositoryBinding(
                control_id,
                "ssh://git@example.invalid/runtime-control.git",
                writable=False,
            ),
        ])
        self.addCleanup(provider.close)
        instance_env = provider._env(provider._binding(instance_id))
        control_env = provider._env(provider._binding(control_id))
        self.assertEqual(socket, instance_env["SSH_AUTH_SOCK"])
        self.assertNotIn("SSH_AUTH_SOCK", control_env)

    def test_windows_drive_relative_remote_fails_closed(self):
        for remote in ("C:repo.git", "z:relative/repo.git"):
            with self.subTest(remote=remote):
                with self.assertRaisesRegex(ResolutionError, "drive-relative"):
                    GitCliProvider([GitRepositoryBinding(self.REPO_ID, remote)])

    def test_windows_root_relative_remote_fails_closed(self):
        remotes = ["\\repo.git"]
        if os.name == "nt":
            remotes.append("/repo.git")
        for remote in remotes:
            with self.subTest(remote=remote):
                with self.assertRaisesRegex(ResolutionError, "root-relative"):
                    GitCliProvider([
                        GitRepositoryBinding(self.REPO_ID, remote)
                    ])

    def test_relative_filesystem_remote_is_stabilized_at_construction(self):
        relative = os.path.relpath(self.remote, Path.cwd())
        provider = GitCliProvider([
            GitRepositoryBinding(self.REPO_ID, relative)
        ])
        self.addCleanup(provider.close)
        binding = provider._binding(self.REPO_ID)
        self.assertTrue(os.path.isabs(binding.remote))
        self.assertEqual(os.path.abspath(relative), binding.remote)
        snapshot = provider.materialize(self.REPO_ID, "main")
        self.assertEqual(self.initial_commit, snapshot.commit_sha)

    def test_ambient_netrc_credentials_are_not_inherited(self):
        authorizations = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                authorizations.append(self.headers.get("Authorization"))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="runtime-test"')
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        ambient_home = self.root / "ambient-home"
        ambient_home.mkdir()
        netrc = ambient_home / ".netrc"
        netrc.write_text(
            "machine 127.0.0.1 login ambient password secret\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            netrc.chmod(0o600)
        except OSError:
            pass
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(ambient_home)
        provider = GitCliProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                f"http://127.0.0.1:{server.server_port}/repo.git",
            )
        ])
        try:
            isolated_home = provider._env()["HOME"]
            self.assertNotEqual(str(ambient_home), isolated_home)
            self.assertFalse((Path(isolated_home) / ".netrc").exists())
            with self.assertRaises(ResolutionError):
                provider._git(
                    "ls-remote",
                    f"http://127.0.0.1:{server.server_port}/repo.git",
                )
            self.assertTrue(authorizations)
            self.assertTrue(all(value is None for value in authorizations))
        finally:
            provider.close()
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ambient_ssh_agent_is_not_inherited(self):
        old = os.environ.get("SSH_AUTH_SOCK")
        os.environ["SSH_AUTH_SOCK"] = "ambient-agent-must-not-leak"
        try:
            provider = self.provider()
            self.assertNotIn("SSH_AUTH_SOCK", provider._env())
        finally:
            if old is None:
                os.environ.pop("SSH_AUTH_SOCK", None)
            else:
                os.environ["SSH_AUTH_SOCK"] = old

    def test_case_variant_git_and_colon_paths_fail_closed(self):
        provider = self.provider()
        for path in (".GIT/config", "C:state.txt"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ResolutionError, "unsafe"):
                    provider.read_text(self.REPO_ID, "main", path)

    def test_non_boolean_writable_binding_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "boolean"):
            GitCliProvider([
                GitRepositoryBinding(
                    self.REPO_ID,
                    str(self.remote),
                    writable="false",  # type: ignore[arg-type]
                )
            ])

    def test_transient_read_and_update_checkouts_are_cleaned_immediately(self):
        provider = self.provider()
        provider.read_text(self.REPO_ID, "main", "state.txt")
        self.assertEqual([], provider._tempdirs)
        provider.update_text(
            self.REPO_ID,
            "main",
            "state.txt",
            "two\n",
            self.initial_blob,
            "test: transient cleanup",
        )
        self.assertEqual([], provider._tempdirs)

    def test_materialize_does_not_honor_export_ignore(self):
        (self.seed / ".gitattributes").write_text(
            "hidden.txt export-ignore\n", encoding="utf-8", newline="\n"
        )
        (self.seed / "hidden.txt").write_text(
            "must remain visible\n", encoding="utf-8", newline="\n"
        )
        self._git("add", ".gitattributes", "hidden.txt", cwd=self.seed)
        self._git("commit", "-q", "-m", "add export-ignore fixture", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        snapshot = self.provider().materialize(self.REPO_ID, "main")
        self.assertTrue((snapshot.root / ".gitattributes").is_file())
        self.assertEqual(
            "must remain visible\n",
            (snapshot.root / "hidden.txt").read_text(encoding="utf-8"),
        )

    def test_update_bypasses_working_tree_encoding_attributes(self):
        (self.seed / ".gitattributes").write_text(
            "state.txt working-tree-encoding=UTF-16LE\n",
            encoding="utf-8",
            newline="\n",
        )
        self._git("add", ".gitattributes", cwd=self.seed)
        self._git("commit", "-q", "-m", "declare working-tree encoding", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        expected_blob = self._git("rev-parse", "HEAD:state.txt", cwd=self.seed)
        self.provider().update_text(
            self.REPO_ID,
            "main",
            "state.txt",
            "two\n",
            expected_blob,
            "test: preserve requested UTF-8 bytes",
        )
        raw = subprocess.run(
            [
                "git", "--git-dir", str(self.remote),
                "cat-file", "blob", "refs/heads/main:state.txt",
            ],
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(b"two\n", raw)

    def test_unicode_path_update_uses_unquoted_nul_safe_comparison(self):
        target = self.seed / "café.yaml"
        target.write_text("one\n", encoding="utf-8", newline="\n")
        self._git("add", "café.yaml", cwd=self.seed)
        self._git("commit", "-q", "-m", "add unicode path", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        blob = self._git("rev-parse", "HEAD:café.yaml", cwd=self.seed)
        self.provider().update_text(
            self.REPO_ID,
            "main",
            "café.yaml",
            "two\n",
            blob,
            "test: update unicode path",
        )
        self.assertEqual(
            "two",
            self._git(
                "--git-dir", str(self.remote),
                "show", "refs/heads/main:café.yaml",
            ),
        )

    def test_expected_branch_head_rejects_advance_with_same_target_blob(self):
        (self.seed / "other.txt").write_text(
            "advance\n", encoding="utf-8", newline="\n"
        )
        self._git("add", "other.txt", cwd=self.seed)
        self._git("commit", "-q", "-m", "advance branch", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        advanced = self._git("rev-parse", "HEAD", cwd=self.seed)
        with self.assertRaisesRegex(CasConflict, "branch head"):
            self.provider().update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                self.initial_blob,
                "test: stale authority head",
                expected_ref_sha=self.initial_commit,
            )
        self.assertEqual(
            advanced,
            self._git(
                "--git-dir", str(self.remote),
                "rev-parse", "refs/heads/main",
            ),
        )
        self.assertEqual(
            "one",
            self._git(
                "--git-dir", str(self.remote),
                "show", "refs/heads/main:state.txt",
            ),
        )

    def test_exact_lease_rejects_remote_force_reset_to_ancestor(self):
        (self.seed / "second.txt").write_text(
            "second\n", encoding="utf-8", newline="\n"
        )
        self._git("add", "second.txt", cwd=self.seed)
        self._git("commit", "-q", "-m", "second commit", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        expected_blob = self._git("rev-parse", "HEAD:state.txt", cwd=self.seed)
        test = self

        class ResettingProvider(GitCliProvider):
            reset = False

            def _git(self, *args, cwd=None, cas=False, binding=None):
                if args and args[0] == "push" and cas and not self.reset:
                    self.reset = True
                    test._git(
                        "--git-dir", str(test.remote),
                        "update-ref", "refs/heads/main", test.initial_commit,
                    )
                return super()._git(
                    *args, cwd=cwd, cas=cas, binding=binding
                )

        provider = ResettingProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                str(self.remote),
                "synthetic/instance",
                writable=True,
            )
        ])
        self.addCleanup(provider.close)
        with self.assertRaisesRegex(CasConflict, "compare-and-swap"):
            provider.update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                expected_blob,
                "test: exact lease",
            )
        self.assertEqual(
            self.initial_commit,
            self._git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main"),
        )

    def test_fully_qualified_branch_and_tag_refs_resolve(self):
        provider = self.provider()
        branch_snapshot = provider.materialize(self.REPO_ID, "refs/heads/main")
        self.assertEqual(self.initial_commit, branch_snapshot.commit_sha)
        self._git("tag", "v1", self.initial_commit, cwd=self.seed)
        self._git("push", "-q", "origin", "refs/tags/v1", cwd=self.seed)
        tag_snapshot = provider.materialize(self.REPO_ID, "refs/tags/v1")
        self.assertEqual(self.initial_commit, tag_snapshot.commit_sha)
        short_tag_snapshot = provider.materialize(self.REPO_ID, "v1")
        self.assertEqual(self.initial_commit, short_tag_snapshot.commit_sha)

    def test_unsupported_fully_qualified_ref_fails_closed(self):
        with self.assertRaisesRegex(ResolutionError, "unsupported fully qualified"):
            self.provider().materialize(self.REPO_ID, "refs/pull/1/head")

    def test_read_only_binding_cannot_update(self):
        provider = self.provider(writable=False)
        with self.assertRaisesRegex(CasConflict, "read-only"):
            provider.update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                self.initial_blob,
                "test: should not write",
            )
        self.assertEqual(
            self.initial_commit,
            self._git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main"),
        )

    def test_stale_blob_cas_is_rejected(self):
        provider = self.provider()
        with self.assertRaisesRegex(CasConflict, "compare-and-swap"):
            provider.update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                "f" * 40,
                "test: stale",
            )

    def test_update_rejects_text_above_runtime_write_limit(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_TEXT_BLOB_BYTES", 1
        ):
            with self.assertRaisesRegex(CasConflict, "write limit"):
                provider.update_text(
                    self.REPO_ID,
                    "main",
                    "state.txt",
                    "two\n",
                    self.initial_blob,
                    "test: oversized text",
                )
        self.assertEqual(
            self.initial_commit,
            self._git(
                "--git-dir", str(self.remote),
                "rev-parse", "refs/heads/main",
            ),
        )

    def test_update_rejects_result_above_snapshot_total_limit(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_SNAPSHOT_TOTAL_BLOB_BYTES", 7
        ):
            with self.assertRaisesRegex(CasConflict, "total-size budget"):
                provider.update_text(
                    self.REPO_ID,
                    "main",
                    "state.txt",
                    "two-two\n",
                    self.initial_blob,
                    "test: oversized snapshot",
                )
        self.assertEqual(
            self.initial_commit,
            self._git(
                "--git-dir", str(self.remote),
                "rev-parse", "refs/heads/main",
            ),
        )

    def test_successful_update_pushes_one_file_on_bound_branch(self):
        provider = self.provider()
        commit = provider.update_text(
            self.REPO_ID,
            "main",
            "state.txt",
            "two\n",
            self.initial_blob,
            "test: update synthetic state",
        )
        self.assertEqual(
            commit,
            self._git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main"),
        )
        self.assertEqual(
            "two",
            self._git("--git-dir", str(self.remote), "show", "refs/heads/main:state.txt"),
        )

    def test_concurrent_branch_advance_rejects_non_force_push(self):
        test = self

        class RacingProvider(GitCliProvider):
            raced = False

            def _git(self, *args, cwd=None, cas=False, binding=None):
                if args and args[0] == "push" and cas and not self.raced:
                    self.raced = True
                    (test.seed / "other.txt").write_text("race\n", encoding="utf-8")
                    test._git("add", "other.txt", cwd=test.seed)
                    test._git("commit", "-q", "-m", "concurrent advance", cwd=test.seed)
                    test._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=test.seed)
                return super()._git(
                    *args, cwd=cwd, cas=cas, binding=binding
                )

        provider = RacingProvider([
            GitRepositoryBinding(
                self.REPO_ID,
                str(self.remote),
                "synthetic/instance",
                writable=True,
            )
        ])
        self.addCleanup(provider.close)
        with self.assertRaisesRegex(CasConflict, "compare-and-swap"):
            provider.update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                self.initial_blob,
                "test: racing update",
            )
        self.assertEqual(
            "one",
            self._git("--git-dir", str(self.remote), "show", "refs/heads/main:state.txt"),
        )

    def test_symlink_tree_entry_is_rejected_before_checkout(self):
        target_blob = self._git(
            "hash-object", "-w", "--stdin",
            cwd=self.seed,
            input_text="state.txt",
        )
        self._git(
            "update-index", "--add", "--cacheinfo",
            "120000", target_blob, "synthetic-link",
            cwd=self.seed,
        )
        self._git("commit", "-q", "-m", "add synthetic symlink entry", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        with self.assertRaisesRegex(ResolutionError, "non-regular"):
            self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_dos_83_short_name_paths(self):
        blob = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="hidden\n"
        )
        tree_input = f"100644 blob {blob}\trequir~1.txt\0"
        tree = self._git("mktree", "-z", cwd=self.seed, input_text=tree_input)
        commit = self._git(
            "-c", "user.name=Synthetic Runtime Test",
            "-c", "user.email=runtime-test@invalid.local",
            "commit-tree", tree, "-p", self.initial_commit,
            "-m", "dos short-name path tree",
            cwd=self.seed,
        )
        self._git(
            "push", "-q", "--force", "origin",
            f"{commit}:refs/heads/main", cwd=self.seed,
        )
        with self.assertRaisesRegex(ResolutionError, "unsafe"):
            self.provider().materialize(self.REPO_ID, "main")

    def test_tree_rejects_win32_uppercase_directory_aliases(self):
        provider = self.provider()
        objects = [
            ("scripts", "1" * 40, "040000", "tree"),
            ("scripts/a", "2" * 40, "100644", "blob"),
            ("scr\u0131pts", "3" * 40, "040000", "tree"),
            ("scr\u0131pts/b", "4" * 40, "100644", "blob"),
        ]
        first = provider._portable_snapshot_keys("scripts")
        second = provider._portable_snapshot_keys("scr\u0131pts")
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])
        with mock.patch.object(
            provider, "_tree_objects", return_value=objects
        ):
            with self.assertRaisesRegex(
                ResolutionError, "filesystem-equivalent"
            ):
                provider._verify_regular_tree(
                    self.seed, self.initial_commit
                )

    def test_tree_listing_byte_budget_fails_before_materialization(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_SNAPSHOT_TREE_LIST_BYTES", 1
        ):
            with self.assertRaisesRegex(ResolutionError, "byte budget"):
                provider.materialize(self.REPO_ID, "main")

    def test_tree_entry_budget_fails_before_materialization(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_SNAPSHOT_TREE_ENTRIES", 0
        ):
            with self.assertRaisesRegex(ResolutionError, "entry budget"):
                provider.materialize(self.REPO_ID, "main")

    def test_snapshot_total_blob_budget_fails_before_materialization(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_SNAPSHOT_TOTAL_BLOB_BYTES", 1
        ):
            with self.assertRaisesRegex(ResolutionError, "total-size budget"):
                provider.materialize(self.REPO_ID, "main")

    def test_snapshot_single_blob_budget_fails_before_materialization(self):
        provider = self.provider()
        with mock.patch(
            "scripts.runtime_adapter.MAX_SNAPSHOT_BLOB_BYTES", 1
        ):
            with self.assertRaisesRegex(ResolutionError, "blob exceeds"):
                provider.materialize(self.REPO_ID, "main")

    def test_empty_tree_check_does_not_scan_all_blobs_per_tree(self):
        provider = self.provider()
        objects = []
        for index in range(250):
            directory = f"d{index}"
            objects.append((directory, "1" * 40, "040000", "tree"))
            objects.append((f"{directory}/file.txt", "2" * 40, "100644", "blob"))
        with mock.patch.object(provider, "_tree_objects", return_value=objects):
            provider._verify_regular_tree(self.seed, self.initial_commit)

    def test_materialize_rejects_empty_tree_entries(self):
        empty_tree = self._git("mktree", cwd=self.seed, input_text="")
        tree_input = f"040000 tree {empty_tree}\tlearner\0"
        tree = self._git("mktree", "-z", cwd=self.seed, input_text=tree_input)
        commit = self._git(
            "-c", "user.name=Synthetic Runtime Test",
            "-c", "user.email=runtime-test@invalid.local",
            "commit-tree", tree, "-p", self.initial_commit,
            "-m", "empty learner tree",
            cwd=self.seed,
        )
        self._git(
            "push", "-q", "--force", "origin",
            f"{commit}:refs/heads/main", cwd=self.seed,
        )
        with self.assertRaisesRegex(ResolutionError, "empty directory"):
            self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_windows_reserved_device_paths(self):
        blob = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="hidden\n"
        )
        for reserved in (
            "NUL.txt", "NUL .txt", "con.yaml", "CONOUT$",
            "COM1.md", "COM\u00b9.yaml", "COM\u00b2.txt", "COM\u00b3",
            "lpt9.log", "LPT\u00b9.txt", "LPT\u00b2", "LPT\u00b3.bin",
        ):
            with self.subTest(reserved=reserved):
                tree_input = f"100644 blob {blob}\t{reserved}\0"
                tree = self._git(
                    "mktree", "-z", cwd=self.seed, input_text=tree_input
                )
                commit = self._git(
                    "-c", "user.name=Synthetic Runtime Test",
                    "-c", "user.email=runtime-test@invalid.local",
                    "commit-tree", tree, "-p", self.initial_commit,
                    "-m", "reserved device path tree",
                    cwd=self.seed,
                )
                self._git(
                    "push", "-q", "--force", "origin",
                    f"{commit}:refs/heads/main",
                    cwd=self.seed,
                )
                with self.assertRaisesRegex(ResolutionError, "unsafe"):
                    self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_preexisting_resolved_alias_target(self):
        provider = self.provider()
        original_exists = Path.exists

        def alias_exists(path):
            if "learning-os-snapshot-" in str(path) and path.name == "state.txt":
                return True
            return original_exists(path)

        with mock.patch.object(Path, "exists", alias_exists):
            with self.assertRaisesRegex(ResolutionError, "aliases an existing"):
                provider.materialize(self.REPO_ID, "main")

    def test_materialize_verifies_regular_output_after_write(self):
        provider = self.provider()
        original_is_file = Path.is_file

        def fail_snapshot_file(path):
            if "learning-os-snapshot-" in str(path):
                return False
            return original_is_file(path)

        with mock.patch.object(Path, "is_file", fail_snapshot_file):
            with self.assertRaisesRegex(ResolutionError, "regular file"):
                provider.materialize(self.REPO_ID, "main")

    def test_materialize_rejects_win32_trailing_dot_and_space_paths(self):
        blob = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="hidden\n"
        )
        for alias in ("README.md.", "README.md "):
            with self.subTest(alias=alias):
                tree_input = (
                    f"100644 blob {blob}\tREADME.md\0"
                    f"100644 blob {blob}\t{alias}\0"
                )
                alias_tree = self._git(
                    "mktree", "-z", cwd=self.seed, input_text=tree_input
                )
                alias_commit = self._git(
                    "-c", "user.name=Synthetic Runtime Test",
                    "-c", "user.email=runtime-test@invalid.local",
                    "commit-tree", alias_tree, "-p", self.initial_commit,
                    "-m", "win32 path alias tree",
                    cwd=self.seed,
                )
                self._git(
                    "push", "-q", "--force", "origin",
                    f"{alias_commit}:refs/heads/main",
                    cwd=self.seed,
                )
                with self.assertRaisesRegex(ResolutionError, "unsafe"):
                    self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_exact_duplicate_tree_paths(self):
        first = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="first\n"
        )
        second = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="second\n"
        )
        tree_input = (
            f"100644 blob {first}\tREADME.md\0"
            f"100644 blob {second}\tREADME.md\0"
        )
        duplicate_tree = self._git(
            "mktree", "-z", cwd=self.seed, input_text=tree_input
        )
        duplicate_commit = self._git(
            "-c", "user.name=Synthetic Runtime Test",
            "-c", "user.email=runtime-test@invalid.local",
            "commit-tree", duplicate_tree, "-p", self.initial_commit,
            "-m", "duplicate path tree",
            cwd=self.seed,
        )
        self._git(
            "push", "-q", "--force", "origin",
            f"{duplicate_commit}:refs/heads/main",
            cwd=self.seed,
        )
        with self.assertRaisesRegex(ResolutionError, "duplicate"):
            self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_casefold_path_aliases(self):
        self._git("config", "core.ignorecase", "false", cwd=self.seed)
        upper = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="upper\n"
        )
        lower = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="lower\n"
        )
        self._git(
            "update-index", "--add", "--cacheinfo",
            "100644", upper, "README.md", cwd=self.seed,
        )
        self._git(
            "update-index", "--add", "--cacheinfo",
            "100644", lower, "readme.md", cwd=self.seed,
        )
        self._git("commit", "-q", "-m", "add case aliases", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        with self.assertRaisesRegex(ResolutionError, "filesystem-equivalent"):
            self.provider().materialize(self.REPO_ID, "main")

    def test_materialize_rejects_unicode_normalization_aliases(self):
        composed = "caf\u00e9.txt"
        decomposed = "cafe\u0301.txt"
        first = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="first\n"
        )
        second = self._git(
            "hash-object", "-w", "--stdin", cwd=self.seed, input_text="second\n"
        )
        self._git(
            "update-index", "--add", "--cacheinfo",
            "100644", first, composed, cwd=self.seed,
        )
        self._git(
            "update-index", "--add", "--cacheinfo",
            "100644", second, decomposed, cwd=self.seed,
        )
        self._git("commit", "-q", "-m", "add unicode aliases", cwd=self.seed)
        self._git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.seed)
        with self.assertRaisesRegex(ResolutionError, "filesystem-equivalent"):
            self.provider().materialize(self.REPO_ID, "main")


class GitHubApiProviderTests(unittest.TestCase):
    REPO_ID = 9000000301
    HEAD = "1" * 40
    TREE = "2" * 40
    TARGET_BLOB = "3" * 40
    NEW_BLOB = "4" * 40
    NEW_TREE = "5" * 40
    NEW_COMMIT = "6" * 40

    def provider(self, *, current_head=None):
        provider = GitHubApiProvider(
            token="synthetic-token",
            api_url="https://example.invalid",
        )
        self.addCleanup(provider.close)
        provider.calls = []
        provider._repo = lambda repository_id: {
            "id": repository_id,
            "full_name": "synthetic/instance",
        }
        head = self.HEAD if current_head is None else current_head

        def request(method, path, payload=None):
            provider.calls.append((method, path, payload))
            if method == "GET" and "/git/ref/heads/" in path:
                return {"object": {"sha": head}}
            if method == "GET" and "/git/commits/" in path:
                return {"tree": {"sha": self.TREE}}
            if method == "GET" and "/git/trees/" in path:
                return {
                    "truncated": False,
                    "tree": [{
                        "path": "state.txt",
                        "mode": "100644",
                        "type": "blob",
                        "sha": self.TARGET_BLOB,
                    }],
                }
            if method == "POST" and path.endswith("/git/blobs"):
                return {"sha": self.NEW_BLOB}
            if method == "POST" and path.endswith("/git/trees"):
                return {"sha": self.NEW_TREE}
            if method == "POST" and path.endswith("/git/commits"):
                return {"sha": self.NEW_COMMIT}
            if method == "PATCH" and "/git/refs/heads/" in path:
                return {"object": {"sha": self.NEW_COMMIT}}
            if method == "PUT" and "/contents/" in path:
                return {"commit": {"sha": self.NEW_COMMIT}}
            raise AssertionError(f"unexpected request: {method} {path}")

        provider._request = request
        return provider

    def test_read_text_pins_content_to_resolved_commit(self):
        provider = GitHubApiProvider(
            token="synthetic-token",
            api_url="https://example.invalid",
        )
        self.addCleanup(provider.close)
        provider._repo = lambda repository_id: {
            "id": repository_id,
            "full_name": "synthetic/instance",
        }
        calls = []

        def request(method, path, payload=None):
            calls.append((method, path, payload))
            if method == "GET" and path.endswith("/commits/main"):
                return {"sha": self.HEAD}
            expected = f"/contents/state.txt?ref={self.HEAD}"
            if method == "GET" and expected in path:
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(b"state-v1\n").decode("ascii"),
                    "sha": self.TARGET_BLOB,
                }
            raise AssertionError(f"unexpected request: {method} {path}")

        provider._request = request
        text_value, blob, commit = provider.read_text(
            self.REPO_ID, "main", "state.txt"
        )
        self.assertEqual("state-v1\n", text_value)
        self.assertEqual(self.TARGET_BLOB, blob)
        self.assertEqual(self.HEAD, commit)
        self.assertFalse(any("?ref=main" in call[1] for call in calls))

    def test_exact_branch_head_cas_is_rejected_before_requests(self):
        provider = self.provider()
        with self.assertRaisesRegex(CasConflict, "unsupported"):
            provider.update_text(
                self.REPO_ID,
                "main",
                "state.txt",
                "two\n",
                self.TARGET_BLOB,
                "test: exact authority write",
                expected_ref_sha=self.HEAD,
            )
        self.assertEqual([], provider.calls)

    def test_legacy_contents_update_remains_available_without_head_cas(self):
        provider = self.provider()
        result = provider.update_text(
            self.REPO_ID,
            "main",
            "state.txt",
            "two\n",
            self.TARGET_BLOB,
            "test: legacy contents update",
        )
        self.assertEqual(self.NEW_COMMIT, result)
        self.assertEqual(1, len(provider.calls))
        method, path, payload = provider.calls[0]
        self.assertEqual("PUT", method)
        self.assertIn("/contents/state.txt", path)
        self.assertEqual(self.TARGET_BLOB, payload["sha"])
        self.assertEqual("main", payload["branch"])


class DeploymentTransitionTests(unittest.TestCase):
    def test_initial_cutover_is_split_frozen_epoch_one(self):
        validate_initial_cutover(contract(write_state="frozen"))

    def test_initial_cutover_active_is_rejected(self):
        with self.assertRaises(TransitionRejected):
            validate_initial_cutover(contract(write_state="active"))

    def test_normal_freeze_and_activate(self):
        active = contract(write_state="active")
        frozen = contract(write_state="frozen")
        validate_deployment_transition(active, frozen)
        validate_deployment_transition(frozen, active)

    def test_core_promotion_is_frozen_exact_epoch_increment(self):
        old = contract(epoch=4, write_state="frozen", core_commit="8" * 40)
        new = contract(epoch=5, write_state="frozen", core_commit="9" * 40)
        validate_deployment_transition(old, new)

    def test_active_core_pin_change_is_rejected(self):
        old = contract(epoch=4, core_commit="8" * 40)
        new = contract(epoch=5, core_commit="9" * 40)
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(old, new)

    def test_epoch_decrease_is_rejected(self):
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(contract(epoch=4), contract(epoch=3))

    def test_epoch_skip_is_rejected(self):
        old = contract(epoch=4, write_state="frozen", core_commit="8" * 40)
        new = contract(epoch=6, write_state="frozen", core_commit="9" * 40)
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(old, new)

    def test_epoch_change_without_promotion_is_rejected(self):
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(contract(epoch=4), contract(epoch=5))

    def test_core_repository_identity_change_is_rejected(self):
        old, new = contract(), contract()
        new["core"]["repository_id"] += 1
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(old, new)

    def test_deployment_id_change_is_rejected(self):
        old, new = contract(), contract()
        new["deployment"]["id"] = "other"
        with self.assertRaises(TransitionRejected):
            validate_deployment_transition(old, new)

    def test_interrupted_frozen_transaction_can_resume_forward(self):
        frozen = contract(epoch=2, write_state="frozen", core_commit="9" * 40)
        active = contract(epoch=2, write_state="active", core_commit="9" * 40)
        validate_deployment_transition(frozen, frozen)
        validate_deployment_transition(frozen, active)

    def test_prewrite_pointer_rollback_is_allowed(self):
        self.assertTrue(pointer_rollback_allowed(post_cutover_instance_mutated=False))

    def test_postwrite_pointer_rollback_is_rejected(self):
        self.assertFalse(pointer_rollback_allowed(post_cutover_instance_mutated=True))


if __name__ == "__main__":
    unittest.main()
