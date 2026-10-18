"""Synthetic blank-first-use, exact Git effect and restart acceptance tests.

All IDs, grants and repositories are synthetic. Core is frozen once from the
candidate working tree; Instance and Control use actual local Git transports.
"""
from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import yaml

from scripts.first_use import (
    FirstUseIntent, FirstUseRejected, SyntheticFirstUseAuthority,
    SyntheticFirstUseService, SyntheticFirstUseStore, SyntheticReceiver,
)
from scripts.reference_host import REFERENCE_HOST_OPERATIONS, ReferenceLearningHost
from scripts.runtime_adapter import (
    CasConflict, GitCliProvider, GitRepositoryBinding, GuardRejected,
    MaterializedRepository, PreparedFirstUseStep, ResolutionError,
    SyntheticPreparedGitProvider,
)
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy

ROOT = Path(__file__).resolve().parents[1]
RC_ID, CORE_ID, INSTANCE_ID = 9900000701, 9900000702, 9900000703
TOPIC = "llm-first-use"
UNIT = "prediction-and-context"
RUNTIME = f"topics/{TOPIC}/coordination/branches/main/runtime.yaml"
PROGRESS = f"topics/{TOPIC}/subtopics/{UNIT}/progress.yaml"


def git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", check=True, env={**os.environ,
                            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    return result.stdout.strip()


def repository(root, files):
    root.mkdir()
    git("init", "-q", str(root))
    git("checkout", "-q", "-b", "main", cwd=root)
    git("config", "user.name", "Synthetic First Use", cwd=root)
    git("config", "user.email", "fixture@learning-os.invalid", cwd=root)
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    git("add", ".", cwd=root)
    git("commit", "--allow-empty", "-qm", "Synthetic initial transport", cwd=root)
    remote = root.with_suffix(".git")
    git("clone", "-q", "--bare", str(root), str(remote))
    git("--git-dir", str(remote), "config", "uploadpack.allowFilter", "true")
    return remote


class Restart(Exception):
    pass


class LocalFixtureProvider(SyntheticPreparedGitProvider):
    """Use a frozen exact Core fixture without repeated large Git downloads."""
    def __init__(self, *args, core_root, core_commit, **kwargs):
        super().__init__(*args, **kwargs)
        self.core_root, self.core_commit = core_root, core_commit

    def materialize(self, repository_id, ref):
        if repository_id == CORE_ID:
            if ref != self.core_commit:
                raise ResolutionError("unavailable synthetic Core pin")
            return MaterializedRepository(self.core_root, CORE_ID, self.core_commit, "synthetic/core")
        return super().materialize(repository_id, ref)

    def release_materialization(self, snapshot):
        if snapshot.repository_id != CORE_ID:
            super().release_materialization(snapshot)


class FirstUseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_temp = tempfile.TemporaryDirectory()
        cls.core_root = Path(cls.core_temp.name) / "core"
        files = {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
                 for directory in ("config", "protocol", "scripts", "domains")
                 for path in (ROOT / directory).rglob("*")
                 if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
        # validate_core requires these public project root assets.
        for name in ("README.md", "AGENTS.md", "requirements-dev.txt"):
            files[name] = (ROOT / name).read_text(encoding="utf-8")
        cls.core_remote = repository(cls.core_root, files)
        cls.core_commit = git("rev-parse", "HEAD", cwd=cls.core_root)

    @classmethod
    def tearDownClass(cls):
        cls.core_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.instance_remote = repository(self.root / "instance", {"README.md": "# Synthetic blank Instance\n"})
        self.baseline = git("--git-dir", str(self.instance_remote), "rev-parse", "main")
        self.contract = dict(schema_version="0.4", document_type="deployment_binding",
            deployment=dict(id="synthetic-first-use", topology="split", epoch=1, write_state="active"),
            core=dict(repository_id=CORE_ID, commit=self.core_commit))
        self.control_remote = repository(self.root / "control", {
            "deployment.yaml": yaml.safe_dump(self.contract, sort_keys=False)})
        self.locator = dict(runtime_control=dict(repository_id=RC_ID, canonical_ref="main", contract_path="deployment.yaml"),
                            instance=dict(repository_id=INSTANCE_ID, canonical_ref="main"))
        self.bindings = [GitRepositoryBinding(RC_ID, str(self.control_remote), "synthetic/control"),
                         GitRepositoryBinding(CORE_ID, str(self.core_remote), "synthetic/core"),
                         GitRepositoryBinding(INSTANCE_ID, str(self.instance_remote), "synthetic/instance", writable=True)]
        self.authority = SyntheticFirstUseAuthority()
        self.intent = FirstUseIntent(TOPIC, "Prediction and context", "Understand next-token prediction and tokens/context through two examples.")
        self.receiver = self.authority.authorize(principal="synthetic-learner", surface="synthetic-cli",
            operation_id="operation-a", intent=self.intent, locator=self.locator)
        self.gate = DeploymentWriteGate()
        self.store = SyntheticFirstUseStore(self.root / "receipts", self.authority.integrity_key)
        self.provider = self.new_provider()
        self.service = self.new_service()

    def new_provider(self):
        provider = LocalFixtureProvider(self.bindings, prepared_store=self.root / "prepared",
                                       core_root=self.core_root, core_commit=self.core_commit)
        self.addCleanup(provider.close)
        return provider

    def new_service(self, event=None, provider=None):
        return SyntheticFirstUseService(provider=provider or self.provider, locator=self.locator,
            authority=self.authority, store=SyntheticFirstUseStore(self.root / "receipts", self.authority.integrity_key),
            write_admission=self.gate, event=event)

    def prepare(self):
        return self.service.prepare("operation-a", self.receiver, self.intent)

    def apply(self, service=None):
        return (service or self.service).apply("operation-a", self.receiver, self.intent)

    def head(self):
        return git("--git-dir", str(self.instance_remote), "rev-parse", "main")

    def read(self, path):
        return yaml.safe_load(git("--git-dir", str(self.instance_remote), "show", f"main:{path}"))

    def assert_ten_transitions(self):
        receipt = self.store.read()
        self.assertEqual(10, receipt["confirmed"])
        self.assertEqual(10, len(receipt["steps"]))
        self.assertEqual("10", git("--git-dir", str(self.instance_remote), "rev-list", "--count", f"{self.baseline}..main"))
        parent = self.baseline
        for step in receipt["steps"]:
            self.assertEqual(parent, step["descriptor"]["parent"])
            self.assertEqual(parent, git("--git-dir", str(self.instance_remote), "rev-parse", f'{step["candidate"]}^'))
            self.assertEqual(step["descriptor"]["tree"], git("--git-dir", str(self.instance_remote), "rev-parse", f'{step["candidate"]}^{{tree}}'))
            parent = step["candidate"]

    def test_blank_normal_staging_activation_and_checkpoint(self):
        self.assertEqual("README.md", git("--git-dir", str(self.instance_remote), "ls-tree", "--name-only", "-r", "main"))
        denied = []
        def observe(event, receipt):
            if event == "step_confirmed" and receipt["confirmed"] < 10:
                for writable in (False, True):
                    with self.assertRaises((GuardRejected, ResolutionError)):
                        ReferenceLearningHost.open(provider=self.provider, locator_source=self.locator,
                            branch_runtime_path=RUNTIME, policy=RuntimeCapabilityPolicy(
                                readable_roots=(f"topics/{TOPIC}",),
                                writable_roots=(PROGRESS,) if writable else ()),
                            expected_generation=1 if writable else None, write_admission=self.gate)
                denied.append(receipt["confirmed"])
        self.service.event = observe
        self.assertEqual("prepared", self.prepare().status)
        self.assertEqual(self.baseline, self.head())
        result = self.apply()
        self.assertEqual("active", result.status)
        self.assertEqual(list(range(1, 10)), denied)
        self.assert_ten_transitions()
        self.assertEqual({1: {"lifecycle": "active"}}, self.read(RUNTIME)["generations"])
        self.assertEqual("active", self.read(f"topics/{TOPIC}/coordination/branches.yaml")["branches"]["main"]["lifecycle"])
        self.assertEqual(7, len(REFERENCE_HOST_OPERATIONS))
        response = result.host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [PROGRESS]}})
        self.assertTrue(response["ok"], response)
        document = response["result"]["documents"][0]
        checkpoint = dict(milestone=["tokens-context"], return_point={"kind": "teaching_thread", "focus": "Next-token example explained; compare contexts next."},
                          ready_next=["Compare a river bank and a money bank as contexts."])
        before = self.read(PROGRESS)
        before_head = self.head()
        topic_progress = self.read(f"topics/{TOPIC}/progress.yaml")
        self.assertEqual({"schema_version", "document_type", "updated_at", "revision", "topic", "plan_revision",
                          "lifecycle", "milestones", "active_subtopics", "blockers", "watch", "resume"},
                         set(topic_progress))
        saved = result.host.invoke({"operation": "save_learning_checkpoint", "arguments": {
            "checkpoint": checkpoint, "expected_version_token": document["version_token"]}})
        self.assertTrue(saved["ok"], saved)
        after = self.read(PROGRESS)
        self.assertEqual(before["milestones"], after["milestones"])
        self.assertEqual(2, after["revision"])
        narrowed = deepcopy(after)
        for key in ("revision", "updated_at"):
            narrowed[key] = before[key]
        narrowed["current"]["milestone"] = before["current"]["milestone"]
        for key in ("return_point", "ready_next"):
            narrowed["resume"][key] = before["resume"][key]
        self.assertEqual(before, narrowed)
        self.assertEqual(PROGRESS, git("--git-dir", str(self.instance_remote), "diff", "--name-only", before_head, "main"))
        head = self.head()
        for retry in (self.prepare(), self.service.inspect("operation-a", self.receiver, self.intent), self.apply()):
            self.assertEqual("already_applied", retry.status)
            self.assertIsNone(retry.host)
            self.assertEqual(head, self.head())
        result.host.close()

    def test_truly_empty_tree_baseline_activates_and_recovers_first_ack(self):
        for interruption in ("none", "prepared", "accepted"):
            with self.subTest(interruption=interruption):
                if interruption != "none":
                    self.doCleanups()
                    self.setUp()
                self.instance_remote = repository(self.root / "empty-baseline", {})
                self.baseline = git("--git-dir", str(self.instance_remote), "rev-parse", "main")
                self.assertEqual("", git("--git-dir", str(self.instance_remote), "ls-tree", "-r", "main"))
                self.provider.bindings[INSTANCE_ID] = GitRepositoryBinding(INSTANCE_ID, str(self.instance_remote),
                                                                          "synthetic/instance", writable=True)
                self.bindings = [binding if binding.repository_id != INSTANCE_ID else self.provider.bindings[INSTANCE_ID]
                                 for binding in self.bindings]
                # Public ordinary transport remains strict, even on the synthetic subclass.
                with self.assertRaisesRegex(ResolutionError, "empty directory"):
                    self.provider.materialize(INSTANCE_ID, "main")
                with self.assertRaisesRegex(ResolutionError, "empty directory"):
                    self.provider.create_text(INSTANCE_ID, "main", "ordinary.txt", "not allowed", "synthetic ordinary create",
                                              expected_ref_sha=self.baseline)
                self.prepare()
                self.assertEqual([], self.store.read()["manifest"]["baseline"]["entries"])
                real_git = self.provider._git
                def lost_response(*args, **kwargs):
                    value = real_git(*args, **kwargs)
                    if args and args[0] == "push" and self.store.read()["confirmed"] == 0:
                        raise CasConflict("synthetic empty baseline first acknowledgement lost")
                    return value
                if interruption == "prepared":
                    step = self.stop_after_receipt()
                    self.assertEqual(self.baseline, self.head())
                    result = self.apply(self.new_service(provider=self.new_provider()))
                    self.assertEqual(step.candidate, self.store.read()["steps"][0]["candidate"])
                elif interruption == "accepted":
                    with mock.patch.object(self.provider, "_git", side_effect=lost_response):
                        self.assertEqual("outcome_unknown", self.apply().status)
                    candidate = self.store.read()["steps"][0]["candidate"]
                    self.assertEqual(candidate, self.head())
                    result = self.apply(self.new_service(provider=self.new_provider()))
                    self.assertEqual(candidate, self.store.read()["steps"][0]["candidate"])
                else:
                    result = self.apply()
                self.assertEqual("active", result.status)
                self.assert_ten_transitions()
                result.host.close()

    def test_bootstrap_empty_root_exception_still_rejects_nested_empty_tree(self):
        clone = self.root / "instance"
        empty_tree = subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], cwd=clone,
                                    input="", text=True, capture_output=True, check=True).stdout.strip()
        raw = f"040000 tree {empty_tree}\tempty\n"
        tree = subprocess.run(["git", "mktree"], cwd=clone, input=raw, text=True,
                              capture_output=True, check=True).stdout.strip()
        commit = git("commit-tree", tree, "-p", self.baseline, "-m", "Synthetic nested empty directory", cwd=clone)
        git("push", "-q", str(self.instance_remote), f"{commit}:main", cwd=clone)
        with self.assertRaisesRegex(ResolutionError, "empty directory"):
            self.prepare()
        self.assertEqual(commit, self.head())
        self.assertFalse(self.provider.first_use_trace)

    def test_all_ten_accepted_lost_ack_restart_exact_candidates(self):
        # Each subtest uses a fresh blank fixture. Never seed learner documents.
        for index in range(10):
            with self.subTest(step=index):
                if index:
                    self.doCleanups()
                    self.setUp()
                real_git = self.provider._git
                def lose_transport_ack(*args, **kwargs):
                    value = real_git(*args, **kwargs)
                    if args and args[0] == "push" and self.store.read()["confirmed"] == index:
                        candidate = self.store.read()["steps"][-1]["candidate"]
                        self.provider.first_use_trace.append(("remote_accepted", index, candidate))
                        self.provider.first_use_trace.append(("response_lost", index, candidate))
                        raise CasConflict("synthetic ack lost after local remote accepted")
                    return value
                self.prepare()
                with mock.patch.object(self.provider, "_git", side_effect=lose_transport_ack):
                    self.assertEqual("outcome_unknown", self.apply().status)
                pending = self.store.read()["steps"][-1]
                self.assertEqual(pending["candidate"], self.head())
                self.assertEqual(b"started", (self.root / "prepared" / f'{pending["candidate"]}.publisher').read_bytes())
                self.assertTrue((self.root / "prepared" / f'{pending["candidate"]}.json').exists())
                old_trace = self.provider.first_use_trace[:]
                provider = self.new_provider()
                restarted = self.new_service(provider=provider)
                self.assertEqual("applied", restarted.inspect("operation-a", self.receiver, self.intent).status)
                result = self.apply(restarted)
                self.assertEqual("active", result.status)
                self.assert_ten_transitions()
                self.assertEqual(pending, self.store.read()["steps"][index])
                events = [entry[0] for entry in old_trace if entry[1] == index]
                self.assertLess(events.index("objects_durable"), events.index("receipt_durable"))
                self.assertLess(events.index("receipt_durable"), events.index("publish_start"))
                self.assertLess(events.index("publish_start"), events.index("remote_accepted"))
                result.host.close()


    def stop_after_receipt(self, index=0):
        def stop(event, receipt):
            if event == "receipt_durable" and receipt["confirmed"] == index:
                raise Restart("prepared receipt checkpoint")
        self.service.event = stop
        self.prepare()
        with self.assertRaises(Restart):
            self.apply()
        return PreparedFirstUseStep(**self.store.read()["steps"][-1])

    def test_prepared_unpublished_restart_uses_same_commit(self):
        step = self.stop_after_receipt()
        self.assertEqual(self.baseline, self.head())
        provider = self.new_provider()
        self.assertEqual("not_applied", provider.inspect_first_use_step(step))
        result = self.apply(self.new_service(provider=provider))
        self.assertEqual("active", result.status)
        self.assertEqual(step.candidate, self.store.read()["steps"][0]["candidate"])
        self.assert_ten_transitions()
        result.host.close()

    def test_prepared_verification_reuses_only_current_exact_object_walks(self):
        step = self.stop_after_receipt()
        provider = self.new_provider()
        real_walk = provider._tree_objects
        # Each restore must re-read both exact trees; a prior successful call
        # must never substitute for the next restore's Git object evidence.
        for _ in range(2):
            with mock.patch.object(provider, "_tree_objects", wraps=real_walk) as walk:
                with provider._first_use_restore(step):
                    pass
            self.assertEqual([step.descriptor["parent"], step.candidate],
                             [call.args[1] for call in walk.call_args_list])
        self.assertEqual(self.baseline, self.head())

    def test_unknown_old_publisher_blocks_republish(self):
        step = self.stop_after_receipt()
        (self.root / "prepared" / f"{step.candidate}.publisher").write_bytes(b"started")
        provider = self.new_provider()
        result = self.apply(self.new_service(provider=provider))
        self.assertEqual("outcome_unknown", result.status)
        self.assertFalse(any(event[0] == "publish_start" for event in provider.first_use_trace))
        self.assertEqual(self.baseline, self.head())
        self.assertTrue((self.root / "prepared" / f"{step.candidate}.json").exists())

    def test_receipt_and_prepared_corruption_fail_closed(self):
        step = self.stop_after_receipt()
        artifact = self.root / "prepared" / f"{step.candidate}.json"
        original = artifact.read_bytes()
        artifact.write_bytes(original + b" ")
        self.assertEqual("outcome_unknown", self.apply(self.new_service()).status)
        self.assertEqual(self.baseline, self.head())
        artifact.write_bytes(original)
        receipt = self.root / "receipts" / "receipt.json"
        data = json.loads(receipt.read_bytes())
        data["data"]["confirmed"] = 1
        receipt.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(FirstUseRejected, "receipt_integrity_failed"):
            self.apply(self.new_service())
        self.assertEqual(self.baseline, self.head())

    def test_missing_receipt_cannot_rebuild_from_same_bytes(self):
        self.stop_after_receipt()
        (self.root / "receipts" / "receipt.json").unlink()
        with self.assertRaisesRegex(FirstUseRejected, "receipt_missing"):
            self.prepare()
        self.assertEqual(self.baseline, self.head())

    def test_receipt_failure_never_starts_publish(self):
        self.prepare()
        original = self.service.store.write
        def failing(receipt):
            if receipt["steps"]:
                raise OSError("synthetic durable receipt unavailable")
            original(receipt)
        with mock.patch.object(self.service.store, "write", side_effect=failing):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.baseline, self.head())
        self.assertFalse(any(event[0] == "publish_start" for event in self.provider.first_use_trace))
        self.assertEqual(1, len(list((self.root / "prepared").glob("*.json"))))

    def test_prepare_object_failure_never_starts_publish(self):
        self.prepare()
        with mock.patch.object(self.provider, "_first_use_atomic", side_effect=OSError("synthetic store unavailable")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.baseline, self.head())
        self.assertEqual([], self.store.read()["steps"])

    def test_different_operations_share_one_slot(self):
        self.prepare()
        other = self.authority.authorize(principal="synthetic-learner", surface="synthetic-cli",
            operation_id="operation-b", intent=self.intent, locator=self.locator)
        with self.assertRaisesRegex(FirstUseRejected, "target_slot_conflict"):
            self.new_service().prepare("operation-b", other, self.intent)
        self.assertEqual([], self.provider.first_use_trace)
        self.assertEqual(self.baseline, self.head())

    def test_different_operations_race_one_target_slot(self):
        other = self.authority.authorize(principal="synthetic-learner", surface="synthetic-cli",
            operation_id="operation-b", intent=self.intent, locator=self.locator)
        services = [self.service, self.new_service()]
        barrier, outcomes = threading.Barrier(2), []
        def run(service, operation, receiver):
            barrier.wait(timeout=10)
            try:
                outcomes.append((operation, service.prepare(operation, receiver, self.intent)))
            except FirstUseRejected as error:
                outcomes.append((operation, error))
        threads = [threading.Thread(target=run, args=args) for args in
                   [(services[0], "operation-a", self.receiver), (services[1], "operation-b", other)]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
            self.assertFalse(thread.is_alive())
        winners = [(operation, result) for operation, result in outcomes if not isinstance(result, Exception)]
        losers = [result for _, result in outcomes if isinstance(result, Exception)]
        self.assertEqual(1, len(winners))
        self.assertEqual("prepared", winners[0][1].status)
        self.assertEqual(1, len(losers))
        self.assertRegex(str(losers[0]), "operation_busy|target_slot_conflict")
        self.assertEqual(winners[0][0], self.store.read()["binding"]["operation_id"])
        self.assertEqual([], self.store.read()["steps"])
        self.assertEqual([], self.provider.first_use_trace)
        self.assertEqual(self.baseline, self.head())

    def test_same_operation_parallel_entry_is_serialized(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def hold(event, receipt):
            if event == "receipt_durable":
                entered.set()
                release.wait(20)
                raise Restart()
        self.service.event = hold
        self.prepare()
        def run(service):
            try:
                outcomes.append(self.apply(service))
            except BaseException as error:
                outcomes.append(error)
        first = threading.Thread(target=run, args=(self.service,))
        second = threading.Thread(target=run, args=(self.new_service(),))
        first.start()
        try:
            self.assertTrue(entered.wait(10))
            second.start()
            # The shared authority may serialize before the file lock. Neither
            # path is allowed to prepare a second candidate while one is pending.
            self.assertEqual(1, len(self.store.read()["steps"]))
            self.assertEqual(self.baseline, self.head())
        finally:
            release.set()
            first.join(30)
            second.join(30)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertTrue(any(isinstance(item, Restart) for item in outcomes))
        steps = self.store.read()["steps"]
        self.assertEqual(len(steps), len({step["candidate"] for step in steps}))
        self.assertEqual(1, len([e for e in self.provider.first_use_trace if e[0] == "objects_durable" and e[1] == 0]))
        for outcome in outcomes:
            if getattr(outcome, "host", None):
                outcome.host.close()

    def test_external_same_bytes_different_commit_is_not_our_effect(self):
        step = self.stop_after_receipt()
        rival = self.provider.prepare_first_use_step(
            repository_id=INSTANCE_ID, branch="main", path=step.descriptor["path"],
            content=self.store.read()["manifest"]["documents"][0][1],
            expected_parent=self.baseline, expected_blob=None, operation_id="external-rival",
            step_index=0, intent_digest="a" * 64, manifest_digest="b" * 64,
            timestamp=self.store.read()["manifest"]["timestamp"])
        self.assertEqual(step.descriptor["tree"], rival.descriptor["tree"])
        self.assertNotEqual(step.candidate, rival.candidate)
        # Explicit local fault injection bypasses the trusted service only here.
        self.provider.publish_first_use_step(rival, authorize=lambda: None)
        before = self.head()
        self.assertEqual("conflict", self.apply(self.new_service()).status)
        self.assertEqual(before, self.head())
        self.assertEqual(0, self.store.read()["confirmed"])

    def external_mode_commit(self):
        clone = self.root / "external-mode"
        git("clone", "-q", "-b", "main", str(self.instance_remote), str(clone))
        git("config", "user.name", "Synthetic External Writer", cwd=clone)
        git("config", "user.email", "external@learning-os.invalid", cwd=clone)
        git("update-index", "--chmod=+x", "README.md", cwd=clone)
        git("commit", "-qm", "Synthetic external mode-only change", cwd=clone)
        git("push", "-q", "origin", "HEAD:main", cwd=clone)

    def test_mode_only_external_change_rejected_using_git_identity(self):
        self.stop_after_receipt()
        old_blob = git("--git-dir", str(self.instance_remote), "rev-parse", "main:README.md")
        self.external_mode_commit()
        self.assertEqual(old_blob, git("--git-dir", str(self.instance_remote), "rev-parse", "main:README.md"))
        self.assertTrue(git("--git-dir", str(self.instance_remote), "ls-tree", "main", "README.md").startswith("100755"))
        self.assertEqual("conflict", self.apply(self.new_service()).status)

    def test_preparation_unrelated_mode_bug_rejected_before_publish(self):
        self.prepare()
        original = self.provider._git
        def buggy(*args, **kwargs):
            if args and args[0] == "write-tree":
                blob = original("rev-parse", ":README.md", cwd=kwargs["cwd"])
                original("update-index", "--cacheinfo", f"100755,{blob},README.md", cwd=kwargs["cwd"])
            return original(*args, **kwargs)
        with mock.patch.object(self.provider, "_git", side_effect=buggy):
            with self.assertRaisesRegex(ResolutionError, "exact single-path effect"):
                self.apply()
        self.assertEqual(self.baseline, self.head())
        self.assertEqual([], self.store.read()["steps"])

    def test_candidate_unfamiliar_successor_is_conflict(self):
        def lose(event, receipt):
            if event == "published":
                raise Restart()
        self.service.event = lose
        self.prepare()
        with self.assertRaises(Restart):
            self.apply()
        self.external_mode_commit()
        self.assertEqual("conflict", self.apply(self.new_service()).status)

    def test_authentication_intent_and_target_are_not_claimable(self):
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            self.service.prepare("operation-a", SyntheticReceiver("guessed-operation-a"), self.intent)
        with self.assertRaisesRegex(FirstUseRejected, "intent_conflict"):
            self.service.prepare("operation-a", self.receiver, FirstUseIntent(TOPIC, "Changed", self.intent.goal))
        other = self.authority.authorize(principal="another-learner", surface="another-surface",
            operation_id="operation-a", intent=self.intent, locator=self.locator)
        self.prepare()
        with self.assertRaisesRegex(FirstUseRejected, "receipt_missing"):
            self.service.apply("operation-a", other, self.intent)
        self.assertEqual(self.baseline, self.head())
        self.assertEqual([], self.provider.first_use_trace)

    def test_initial_revoke_and_staging_revoke_block_new_effect(self):
        self.authority.revoke(self.receiver)
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            self.prepare()
        self.assertEqual(self.baseline, self.head())
        self.receiver = self.authority.authorize(principal="synthetic-learner", surface="synthetic-cli",
            operation_id="operation-a", intent=self.intent, locator=self.locator)
        def revoke(event, receipt):
            if event == "step_confirmed":
                self.authority.revoke(self.receiver)
        self.service.event = revoke
        self.prepare()
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            self.apply()
        self.assertEqual(1, self.store.read()["confirmed"])
        self.assertEqual("1", git("--git-dir", str(self.instance_remote), "rev-list", "--count", f"{self.baseline}..main"))
        self.assertFalse(self.authority._sessions)

    def test_activation_accepted_then_revoke_does_not_rollback_or_issue(self):
        def revoke(event, receipt):
            if event == "step_confirmed" and receipt["confirmed"] == 10:
                self.authority.revoke(self.receiver)
        self.service.event = revoke
        self.prepare()
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            self.apply()
        self.assert_ten_transitions()
        self.assertEqual("activated_unissued", self.store.read()["status"])
        self.assertFalse(self.authority._sessions)
        self.assertEqual("active", self.read(f"topics/{TOPIC}/coordination/branches.yaml")["branches"]["main"]["lifecycle"])

    def test_deployment_change_before_activation_stops_inactive(self):
        def freeze(event, receipt):
            if event == "step_confirmed" and receipt["confirmed"] == 9:
                clone = self.root / "control"
                self.contract["deployment"]["write_state"] = "frozen"
                (clone / "deployment.yaml").write_text(yaml.safe_dump(self.contract), encoding="utf-8")
                git("add", ".", cwd=clone)
                git("commit", "-qm", "Synthetic freeze", cwd=clone)
                git("push", "-q", str(self.control_remote), "HEAD:main", cwd=clone)
        self.service.event = freeze
        self.prepare()
        with self.assertRaisesRegex(GuardRejected, "not active"):
            self.apply()
        self.assertEqual(9, self.store.read()["confirmed"])
        self.assertEqual("idle", self.read(f"topics/{TOPIC}/coordination/branches.yaml")["branches"]["main"]["lifecycle"])
        self.assertFalse(self.authority._sessions)

    def test_pending_delivery_cannot_issue_new_writer_after_unknown_host_loss(self):
        def stop(event, receipt):
            if event == "delivery_pending":
                raise Restart()
        self.service.event = stop
        self.prepare()
        with self.assertRaises(Restart):
            self.apply()
        self.assertTrue(self.store.read()["completed"])
        self.assertEqual("delivery_pending", self.apply(self.new_service()).status)
        self.assertFalse(self.authority._sessions)
        self.assert_ten_transitions()

    def test_completed_delivery_reuses_live_handle_and_never_reissues_closed_one(self):
        self.prepare()
        first = self.apply()
        again = self.apply(self.new_service())
        self.assertIs(first.host, again.host)
        first.host.close()
        closed = self.apply(self.new_service())
        self.assertEqual("already_applied", closed.status)
        self.assertIsNone(closed.host)
        self.assert_ten_transitions()

    def test_unsupported_provider_and_unborn_branch_fail_without_changes(self):
        with self.assertRaisesRegex(FirstUseRejected, "unsupported_provider"):
            SyntheticFirstUseService(provider=object(), locator=self.locator, authority=self.authority,
                store=self.store, write_admission=self.gate)
        empty = self.root / "empty.git"
        git("init", "-q", "--bare", str(empty))
        self.provider.bindings[INSTANCE_ID] = GitRepositoryBinding(INSTANCE_ID, str(empty), writable=True)
        with self.assertRaises(ResolutionError):
            self.prepare()
        self.assertFalse(any(e[0] == "publish_start" for e in self.provider.first_use_trace))
        self.assertEqual(self.baseline, self.head())

    def test_unsafe_and_oversized_intents_are_rejected(self):
        for topic in ("../outside", "CON", ".git", "unit/child", "nul"):
            with self.subTest(topic=topic), self.assertRaises((ResolutionError, FirstUseRejected)):
                FirstUseIntent(topic, "title", "goal")
        with self.assertRaises(FirstUseRejected):
            FirstUseIntent(TOPIC, "title", "x" * 5000)
        with self.assertRaises(TypeError):
            FirstUseIntent(TOPIC, "title", "goal", generation=1)


    def test_old_core_fingerprint_denies_before_prepare(self):
        with mock.patch("scripts.first_use.instance_write_policy_fingerprint", return_value="0" * 64):
            with self.assertRaisesRegex(FirstUseRejected, "stale_deployment"):
                self.prepare()
        self.assertEqual(self.baseline, self.head())
        self.assertFalse(self.provider.first_use_trace)

    def test_missing_prepared_objects_and_unreadable_remote_are_unknown(self):
        step = self.stop_after_receipt()
        path = self.root / "prepared" / f"{step.candidate}.json"
        original = path.read_bytes()
        path.unlink()
        self.assertEqual("outcome_unknown", self.apply(self.new_service()).status)
        self.assertEqual(self.baseline, self.head())
        path.write_bytes(original)
        with mock.patch.object(self.provider, "inspect_first_use_step", side_effect=ResolutionError("unreadable")):
            self.assertEqual("outcome_unknown", self.apply(self.new_service()).status)
        self.assertEqual(self.baseline, self.head())

    def test_bounded_invalid_document_candidate_never_publishes(self):
        import scripts.first_use as first_use
        original = first_use._documents
        def malformed(*args, **kwargs):
            docs = original(*args, **kwargs)
            docs[0][1] = "schema_version: [\n"
            return docs
        with mock.patch("scripts.first_use._documents", side_effect=malformed):
            with self.assertRaises((FirstUseRejected, ResolutionError)):
                self.prepare()
        self.assertEqual(self.baseline, self.head())
        self.assertFalse(any(event[0] == "publish_start" for event in self.provider.first_use_trace))

    def test_cleanup_failure_before_publish_cannot_activate(self):
        self.prepare()
        real_cleanup = self.provider._cleanup_checkout_tempdir
        count = 0
        def cleanup(td, **kwargs):
            nonlocal count
            real_cleanup(td, **kwargs)
            count += 1
            if count == 2:  # after the Control read, prepared checkout cleanup
                raise OSError("synthetic cleanup failure")
        with mock.patch.object(self.provider, "_cleanup_checkout_tempdir", side_effect=cleanup):
            with self.assertRaises((OSError, GuardRejected)):
                self.apply()
        self.assertEqual(self.baseline, self.head())
        self.assertFalse(self.authority._sessions)

    def test_chmod_failure_cannot_hide_git_mode_drift(self):
        step = self.stop_after_receipt()
        self.external_mode_commit()
        with mock.patch("scripts.runtime_adapter.os.chmod", side_effect=OSError("unsupported chmod")):
            self.assertEqual("conflict", self.provider.inspect_first_use_step(step))
        self.assertEqual(0, self.store.read()["confirmed"])

    def test_closed_provider_cannot_redeliver_dead_cached_writer(self):
        self.prepare()
        result = self.apply()
        self.provider.close()
        provider = self.new_provider()
        recovered = self.apply(self.new_service(provider=provider))
        self.assertEqual("delivery_pending", recovered.status)
        self.assertIsNone(recovered.host)
        result.host.close()

    def test_same_operation_name_different_binding_never_shares_session(self):
        from types import SimpleNamespace
        service = SyntheticFirstUseService.__new__(SyntheticFirstUseService)
        service.authority = self.authority
        service.store = SimpleNamespace(write=lambda receipt: None)
        service.provider = SimpleNamespace(resolve_ref=lambda *_: "b" * 40)
        service._current = lambda *_: None
        service._slot = lambda: {"repository_id": INSTANCE_ID, "canonical_ref": "main"}
        # A complete prior delivery for another authenticated binding must not
        # be returned just because the visible operation name is the same.
        binding_a = self.authority.binding(self.receiver)
        binding_b = dict(binding_a, principal="synthetic-other")
        from scripts.first_use import _digest
        alien = SimpleNamespace(_closed=False, target="different-instance")
        self.authority._sessions[_digest(binding_a)] = alien
        receipt = dict(binding=binding_b, delivery="delivered", steps=[{}] * 9 + [{"candidate": "b" * 40}])
        answer = service._deliver(receipt, self.receiver)
        self.assertEqual("already_applied", answer.status)
        self.assertIsNone(answer.host)
        self.authority._sessions.clear()

    def test_promotion_gate_blocks_initialization(self):
        with self.gate.promotion_barrier():
            with self.assertRaisesRegex(GuardRejected, "admissions are closed"):
                self.prepare()
        self.assertEqual(self.baseline, self.head())
        self.assertFalse(self.provider.first_use_trace)


    def test_new_process_read_only_recovery_has_no_producer_memory_or_writer(self):
        import sys
        self.prepare()
        result = self.apply()
        self.assert_ten_transitions()
        context = result.host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [PROGRESS]}})
        token = context["result"]["documents"][0]["version_token"]
        saved = result.host.invoke({"operation": "save_learning_checkpoint", "arguments": {
            "expected_version_token": token, "checkpoint": {
                "milestone": ["tokens-context"], "return_point": {"kind": "teaching_thread", "focus": "A next-token example was explained. The synthetic learner requested river-bank versus money-bank context comparison."},
                "ready_next": ["Compare river-bank and money-bank contexts; clarify that a likely continuation need not be true."]}}})
        self.assertTrue(saved["ok"], saved)
        result.host.close()
        packet = dict(bindings=[dict(asdict(binding), writable=False) for binding in self.bindings],
                      locator=self.locator, topic=TOPIC, unit=UNIT)
        process = subprocess.run([sys.executable, "-m", "tests.first_use_consumer"], cwd=ROOT,
            input=json.dumps(packet), text=True, capture_output=True, timeout=60, check=True)
        recovered = json.loads(process.stdout)
        self.assertEqual(sorted(packet), recovered["input_fields"])
        self.assertTrue(recovered["fresh_write_rejected"])
        document = next(item for item in recovered["context"]["documents"] if item["path"] == PROGRESS)
        data = yaml.safe_load(document["content"])
        self.assertEqual(2, data["revision"])
        self.assertEqual(["Compare river-bank and money-bank contexts; clarify that a likely continuation need not be true."], data["resume"]["ready_next"])
        # Mechanical replay of the preserved sample, not a new model sample.
        fixtures = ROOT / "tests/fixtures/core19-first-use"
        for name, digest in json.loads((fixtures / "sha256.json").read_text(encoding="utf-8")).items():
            self.assertEqual(digest, hashlib.sha256((fixtures / name).read_bytes()).hexdigest(), name)
        sampled = json.loads((fixtures / "b-packet.json").read_text(encoding="utf-8"))["recovered"]
        def semantic_documents(context):
            result = {}
            for document in context["documents"]:
                data = yaml.safe_load(document["content"])
                data.pop("updated_at")
                result[document["path"]] = data
            return result
        self.assertEqual(semantic_documents(sampled["context"]), semantic_documents(recovered["context"]))
        self.assertEqual(sampled["evidence"], recovered["evidence"])
        self.assertTrue(all(item["status"] == "planned" for item in data["milestones"].values()))
        self.assertTrue(recovered["evidence"]["ok"], recovered["evidence"])


    def test_epoch_core_and_same_tuple_control_head_drift_reject_old_manifest(self):
        for change in ("epoch", "core", "same_tuple"):
            with self.subTest(change=change):
                if change != "epoch":
                    self.doCleanups()
                    self.setUp()
                self.stop_after_receipt()
                clone = self.root / "control"
                if change == "epoch":
                    self.contract["deployment"]["epoch"] = 2
                elif change == "core":
                    self.contract["core"]["commit"] = "f" * 40
                text = yaml.safe_dump(self.contract) + ("\n# same semantic tuple, new authority head\n" if change == "same_tuple" else "")
                (clone / "deployment.yaml").write_text(text, encoding="utf-8")
                git("add", ".", cwd=clone)
                git("commit", "-qm", "Synthetic deployment drift", cwd=clone)
                git("push", "-q", str(self.control_remote), "HEAD:main", cwd=clone)
                with self.assertRaises(GuardRejected):
                    self.apply(self.new_service())
                self.assertEqual(self.baseline, self.head())
                self.assertFalse(self.authority._sessions)

    def test_revoke_drains_admitted_step_before_blocking_the_next(self):
        accepted, release, revoked = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def pause(event, receipt):
            if event == "published" and receipt["confirmed"] == 0:
                accepted.set()
                release.wait(15)
            if event == "step_confirmed" and receipt["confirmed"] == 1:
                revoked.wait(15)
        self.service.event = pause
        self.prepare()
        def run():
            try:
                self.apply()
            except BaseException as error:
                errors.append(error)
        def revoke():
            self.authority.revoke(self.receiver)
            revoked.set()
        writer = threading.Thread(target=run)
        writer.start()
        self.assertTrue(accepted.wait(15))
        revoker = threading.Thread(target=revoke)
        revoker.start()
        try:
            self.assertFalse(revoked.wait(0.1))
        finally:
            release.set()
            writer.join(30)
            revoker.join(30)
        self.assertFalse(writer.is_alive() or revoker.is_alive())
        self.assertTrue(revoked.is_set())
        self.assertTrue(any(isinstance(error, FirstUseRejected) and error.code == "authorization_denied" for error in errors))
        self.assertEqual(1, self.store.read()["confirmed"])
        self.assertFalse(self.authority._sessions)

    def test_concurrent_host_close_and_revoke_join_actual_checkpoint_drain(self):
        self.prepare()
        host = self.apply().host
        context = host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [PROGRESS]}})
        token = context["result"]["documents"][0]["version_token"]
        before = self.head()
        entered, release, close_started, closed, revoked = [threading.Event() for _ in range(5)]
        outcomes, errors = [], []
        real_git, real_close = self.provider._git, host._broker.close_session
        def blocked_push(*args, **kwargs):
            if args and args[0] == "push":
                entered.set()
                if not release.wait(20):
                    raise AssertionError("checkpoint publication not released")
            return real_git(*args, **kwargs)
        def close_session(session):
            close_started.set()
            return real_close(session)
        def writer():
            outcomes.append(host.invoke({"operation": "save_learning_checkpoint", "arguments": {
                "expected_version_token": token, "checkpoint": {"milestone": ["tokens-context"],
                "return_point": {"focus": "Context comparison requested."}, "ready_next": ["Compare banks."]}}}))
        def closer():
            try:
                host.close()
                closed.set()
            except BaseException as error:
                errors.append(error)
        def revoker():
            try:
                self.authority.revoke(self.receiver)
                self.authority.revoke(self.receiver)  # A retry must also be after drain.
                revoked.set()
            except BaseException as error:
                errors.append(error)
        with mock.patch.object(self.provider, "_git", side_effect=blocked_push), mock.patch.object(
                host._broker, "close_session", side_effect=close_session):
            threads = [threading.Thread(target=f) for f in (writer, closer, revoker)]
            threads[0].start()
            try:
                self.assertTrue(entered.wait(15))
                threads[1].start()
                self.assertTrue(close_started.wait(10))
                threads[2].start()
                self.assertFalse(closed.wait(0.1))
                self.assertFalse(revoked.wait(0.1))
                self.assertEqual(before, self.head())
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(30)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([], errors)
        self.assertTrue(outcomes[0]["ok"], outcomes)
        self.assertNotEqual(before, self.head())
        self.assertTrue(closed.is_set() and revoked.is_set())
        self.assertFalse(self.authority._sessions or self.authority._revocations)
        self.assertFalse(host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [PROGRESS]}})["ok"])

    def test_failed_revoke_retains_pending_drain_for_retry(self):
        self.prepare()
        host = self.apply().host
        with mock.patch.object(host, "close", side_effect=OSError("synthetic close failure")):
            with self.assertRaises(OSError):
                self.authority.revoke(self.receiver)
        self.assertNotIn(self.receiver.token, self.authority._grants)
        self.assertIn(self.receiver.token, self.authority._revocations)
        self.assertTrue(self.authority._sessions)
        with self.assertRaisesRegex(FirstUseRejected, "authorization_denied"):
            self.apply()
        self.authority.revoke(self.receiver)
        self.assertTrue(host._closed)
        self.assertFalse(self.authority._sessions or self.authority._revocations)

    def test_revoke_isolates_same_operation_name_for_another_binding(self):
        from types import SimpleNamespace
        from scripts.first_use import _digest
        other = self.authority.authorize(principal="other", surface="other",
            operation_id="operation-a", intent=self.intent, locator=self.locator)
        closed = []
        own_key = _digest(self.authority.binding(self.receiver))
        other_key = _digest(self.authority.binding(other))
        self.authority._sessions[own_key] = SimpleNamespace(close=lambda: closed.append("own"))
        self.authority._sessions[other_key] = SimpleNamespace(close=lambda: closed.append("other"))
        self.authority.revoke(other)
        self.assertEqual(["other"], closed)
        self.assertIn(own_key, self.authority._sessions)
        self.authority._sessions.clear()

    def test_valid_new_authorization_requires_an_explicit_resume_call(self):
        self.stop_after_receipt()
        self.authority.revoke(self.receiver)
        restored = self.authority.authorize(principal="synthetic-learner", surface="synthetic-cli",
            operation_id="operation-a", intent=self.intent, locator=self.locator)
        self.assertEqual(self.baseline, self.head())
        with self.assertRaises(FirstUseRejected):
            self.apply(self.new_service())
        self.assertEqual(self.baseline, self.head())
        result = self.new_service().apply("operation-a", restored, self.intent)
        self.assertEqual("active", result.status)
        self.assert_ten_transitions()
        result.host.close()


if __name__ == "__main__":
    unittest.main()
