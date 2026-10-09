"""Synthetic owning-operation coverage; no real learner or deployment access."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts.intake_policy import resolve_intake_policy
from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import CasConflict, GuardRejected, MaterializedRepository
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    BrokerProvider, CORE_ID, CORE_COMMIT, RUNTIME_PATH, locator, contract,
)

TOPIC = "topics/synthetic/goal.yaml"
GLOBAL = "learner/execution.yaml"
TOKEN = "a" * 40


def goal():
    return {"schema_version": "0.3", "document_type": "topic_goal", "revision": 2,
            "topic": "synthetic", "goal": {"purpose": "Synthetic goal",
                "include": ["fractions"], "avoid": ["unrelated survey"],
                "preferences": {"explanation_style": "examples"}}}


class IntakeOperationTests(unittest.TestCase):
    def setUp(self):
        self.control = tempfile.TemporaryDirectory()
        self.instance = tempfile.TemporaryDirectory()
        self.addCleanup(self.control.cleanup)
        self.addCleanup(self.instance.cleanup)
        self.provider = BrokerProvider(Path(self.control.name), Path(self.instance.name))
        self.provider.set_branch_registry(role="main", subtopic="unit")
        self.provider.snapshot_extra_paths.update({GLOBAL, TOPIC})
        self.put(TOPIC, goal())
        self.host = self.open_host()
        self.addCleanup(self.host.close)

    def open_host(self, *, readable=("learner", "topics/synthetic"), writable=(GLOBAL, TOPIC)):
        return ReferenceLearningHost.open(
            provider=self.provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
            policy=RuntimeCapabilityPolicy(readable_roots=readable, writable_roots=writable),
            write_admission=DeploymentWriteGate(), expected_generation=3,
        )

    def put(self, path, document):
        self.provider.docs[path] = yaml.safe_dump(document, sort_keys=False)
        self.provider.blobs[path] = TOKEN

    def invoke(self, scope="topic", depth="minimal", *, reset=False, token=TOKEN, **extra):
        args = {"scope": scope, "expected_version_token": token, **extra}
        if not reset:
            args["depth"] = depth
        return self.host.invoke({"operation": "reset_intake_preference" if reset else "set_intake_preference",
                                 "arguments": args})

    def assert_failed_unchanged(self, result, before, code=None):
        self.assertFalse(result["ok"], result)
        if code:
            self.assertEqual(code, result["error"]["code"], result)
        self.assertEqual(before, self.provider.docs)

    def test_set_reset_preserves_other_goal_fields_and_other_documents(self):
        before = dict(self.provider.docs)
        result = self.invoke(depth="thorough")
        self.assertTrue(result["ok"], result)
        saved = yaml.safe_load(self.provider.docs[TOPIC])
        self.assertEqual("thorough", saved["goal"]["preferences"]["intake_depth"])
        self.assertEqual(3, saved["revision"])
        restored = copy.deepcopy(saved)
        restored["goal"]["preferences"].pop("intake_depth")
        restored.pop("updated_at")
        restored["revision"] = 2
        self.assertEqual(goal(), restored)
        self.assertEqual({p: v for p, v in before.items() if p != TOPIC},
                         {p: v for p, v in self.provider.docs.items() if p != TOPIC})
        result = self.invoke(reset=True, token=self.provider.blobs[TOPIC])
        self.assertTrue(result["ok"], result)
        saved = yaml.safe_load(self.provider.docs[TOPIC])
        self.assertEqual({"explanation_style": "examples"}, saved["goal"]["preferences"])
        self.assertEqual(4, saved["revision"])

    def test_global_first_creation_and_fresh_session_inheritance(self):
        result = self.invoke("global", "thorough", token=None)
        self.assertTrue(result["ok"], result)
        saved = yaml.safe_load(self.provider.docs[GLOBAL])
        self.assertEqual({"schema_version", "document_type", "revision", "updated_at", "preferences"}, set(saved))
        self.assertEqual(1, saved["revision"])
        self.assertEqual("learner_execution", saved["document_type"])
        with self.open_host() as fresh:
            read = fresh.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [GLOBAL, TOPIC]}})
        self.assertTrue(read["ok"], read)
        docs = {x["path"]: yaml.safe_load(x["content"]) for x in read["result"]["documents"]}
        policy = resolve_intake_policy(topic_preferences=docs[TOPIC]["goal"]["preferences"],
                                       learner_preferences=docs[GLOBAL]["preferences"])
        self.assertEqual(("thorough", "learner_global"), (policy.depth, policy.source))
        self.assertTrue(self.invoke(depth="minimal")["ok"])
        self.assertTrue(self.invoke(reset=True, token=self.provider.blobs[TOPIC])["ok"])
        self.assertEqual("thorough", resolve_intake_policy(
            topic_preferences=yaml.safe_load(self.provider.docs[TOPIC])["goal"]["preferences"],
            learner_preferences=saved["preferences"]).depth)

    def test_missing_balanced_and_reset_are_noops_without_materialization(self):
        for reset in (False, True):
            before = dict(self.provider.docs)
            result = self.invoke("global", "balanced", reset=reset, token=None)
            self.assertTrue(result["ok"], result)
            self.assertFalse(result["result"]["applied"])
            self.assertEqual(before, self.provider.docs)
            self.assertFalse((self.provider.instance / GLOBAL).exists())

    def test_existing_global_balanced_can_be_explicit_and_preserves_budget(self):
        self.put(GLOBAL, {"schema_version": "0.3", "document_type": "learner_execution", "revision": 4,
                          "weekly_budget": {"hours": 3}, "preferences": {"other": "keep"}})
        self.assertTrue(self.invoke("global", "balanced")["ok"])
        saved = yaml.safe_load(self.provider.docs[GLOBAL])
        self.assertEqual({"other": "keep", "intake_depth": "balanced"}, saved["preferences"])
        self.assertEqual({"hours": 3}, saved["weekly_budget"])
        before = dict(self.provider.docs)
        result = self.invoke("global", "balanced", token=self.provider.blobs[GLOBAL])
        self.assertEqual({"applied": False}, result["result"])
        self.assertEqual(before, self.provider.docs)
        self.assertTrue(self.invoke("global", reset=True, token=self.provider.blobs[GLOBAL])["ok"])
        self.assertEqual({"other": "keep"}, yaml.safe_load(self.provider.docs[GLOBAL])["preferences"])

    def test_missing_topic_goal_cannot_be_created(self):
        self.provider.docs.pop(TOPIC)
        (self.provider.instance / TOPIC).unlink(missing_ok=True)
        before = dict(self.provider.docs)
        self.assert_failed_unchanged(self.invoke(token=None), before, "guard_rejected")

    def test_stale_and_absence_tokens_rejected(self):
        for scope, token in (("topic", "stale"), ("topic", None), ("global", TOKEN)):
            with self.subTest(scope=scope, token=token):
                before = dict(self.provider.docs)
                self.assert_failed_unchanged(self.invoke(scope, token=token), before, "cas_conflict")

    def test_unknown_fields_scope_depth_and_token_types_rejected(self):
        cases = [{"path": GLOBAL}, {"topic": "other"}, {"content": "x"}, {"revision": 9},
                 {"message": "arbitrary"}, {"scope": "current"}, {"scope": []}, {"depth": None},
                 {"depth": True}, {"depth": "deep"}, {"token": False}, {"token": ""}]
        for args in cases:
            with self.subTest(args=args):
                before = dict(self.provider.docs)
                self.assert_failed_unchanged(self.invoke(**args), before, "resolution_failed")
        before = dict(self.provider.docs)
        self.assert_failed_unchanged(self.invoke(reset=True, unexpected="x"), before, "resolution_failed")

    def test_invalid_existing_owners_reject_without_repairing_them(self):
        cases = [{**goal(), "revision": True}, {**goal(), "topic": "other"},
                 {**goal(), "document_type": "learner_execution"}, {**goal(), "goal": []}]
        for value in (True, None, "unknown"):
            document = goal()
            document["goal"]["preferences"]["intake_depth"] = value
            cases.append(document)
        document = goal(); document["goal"]["preferences"] = []
        cases.append(document)
        for document in cases:
            with self.subTest(document=document):
                self.put(TOPIC, document)
                before = dict(self.provider.docs)
                self.assert_failed_unchanged(self.invoke(), before, "guard_rejected")

    def test_permission_and_role_do_not_expand_even_for_noops(self):
        for role in ("practice", "deep_dive", "hub"):
            self.provider.set_branch_registry(role=role, subtopic="unit")
            with self.open_host() as host:
                before = dict(self.provider.docs)
                result = host.invoke({"operation": "reset_intake_preference", "arguments": {
                    "scope": "global", "expected_version_token": None}})
                self.assert_failed_unchanged(result, before, "guard_rejected")
        self.provider.set_branch_registry(role="main", subtopic="unit")
        with self.open_host(writable=(TOPIC,)) as host:
            before = dict(self.provider.docs)
            result = host.invoke({"operation": "set_intake_preference", "arguments": {
                "scope": "global", "depth": "balanced", "expected_version_token": None}})
            self.assert_failed_unchanged(result, before, "guard_rejected")

    def test_noops_reject_frozen_generation_and_revoked_session(self):
        before = dict(self.provider.docs)
        frozen = copy.deepcopy(self.provider.contract)
        frozen["deployment"]["write_state"] = "frozen"
        self.provider.contract = frozen
        self.assert_failed_unchanged(self.invoke("global", "balanced", token=None), before)
        self.provider.contract = contract()
        self.provider.set_generation(4)
        before = dict(self.provider.docs)
        self.assert_failed_unchanged(self.invoke("global", reset=True, token=None), before)
        self.host.close()
        self.assert_failed_unchanged(self.invoke(), before, "guard_rejected")

    def test_noop_and_update_reject_head_drift_after_snapshot(self):
        original = self.provider.read_materialized_text
        def read(snapshot, path):
            result = original(snapshot, path)
            if path == TOPIC:
                self.provider.instance_head = "7" * 40
            return result
        for reset in (False, True):
            self.provider.instance_head = "6" * 40
            before = dict(self.provider.docs)
            with mock.patch.object(self.provider, "read_materialized_text", side_effect=read):
                self.assert_failed_unchanged(self.invoke(reset=reset), before, "guard_rejected")

    def test_first_creation_race_does_not_overwrite_competing_owner(self):
        original = self.provider.create_text
        competitor = {"schema_version": "0.3", "document_type": "learner_execution", "revision": 1,
                      "preferences": {"intake_depth": "minimal"}, "weekly_budget": {"hours": 4}}
        def race(*args, **kwargs):
            self.put(GLOBAL, competitor)
            self.provider.instance_head = "7" * 40
            return original(*args, **kwargs)
        with mock.patch.object(self.provider, "create_text", side_effect=race):
            result = self.invoke("global", "thorough", token=None)
        self.assertEqual("cas_conflict", result["error"]["code"])
        self.assertEqual(competitor, yaml.safe_load(self.provider.docs[GLOBAL]))

    def test_update_race_preserves_concurrent_unrelated_fields(self):
        original = self.provider.update_text
        competitor = goal(); competitor["goal"]["include"].append("geometry")
        def race(*args, **kwargs):
            self.put(TOPIC, competitor)
            self.provider.blobs[TOPIC] = "7" * 40
            self.provider.instance_head = "7" * 40
            return original(*args, **kwargs)
        with mock.patch.object(self.provider, "update_text", side_effect=race):
            result = self.invoke()
        self.assertEqual("cas_conflict", result["error"]["code"])
        self.assertEqual(competitor, yaml.safe_load(self.provider.docs[TOPIC]))

    def test_old_manifest_new_host_rejects_both_write_and_noop(self):
        import shutil
        from tests.test_runtime_broker import ROOT
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shutil.copytree(ROOT / "scripts", root / "scripts")
            (root / "config").mkdir()
            config = yaml.safe_load((ROOT / "config/core.yaml").read_text())
            config["manifest"]["runtime_session_write_policy_fingerprint"] = "36c21b2c392722cc9489955f92255e151adaaea8f939aa5096939b674a6fb605"
            (root / "config/core.yaml").write_text(yaml.safe_dump(config))
            # Reproduce the old operation registry, not merely a corrupt manifest.
            validator = root / "scripts/validate_learning_os.py"
            text = validator.read_text().replace('    "set_intake_preference":"v1",\n', '').replace('    "reset_intake_preference":"v1",\n', '')
            validator.write_text(text)
            original = self.provider.materialize
            def materialize(repo, ref):
                if repo == CORE_ID:
                    return MaterializedRepository(root, CORE_ID, CORE_COMMIT, "synthetic/core")
                return original(repo, ref)
            with mock.patch.object(self.provider, "materialize", side_effect=materialize):
                for args in ({}, {"scope": "global", "depth": "balanced", "token": None}):
                    before = dict(self.provider.docs)
                    self.assert_failed_unchanged(self.invoke(**args), before, "guard_rejected")
                candidate = goal(); candidate["revision"] += 1
                before = dict(self.provider.docs)
                with self.assertRaisesRegex(GuardRejected, "write policy does not match"):
                    self.host._broker.guarded_update(
                        self.host._session, path=TOPIC, content=yaml.safe_dump(candidate),
                        expected_blob_sha=TOKEN, message="synthetic legacy generic operation",
                    )
                self.assertEqual(before, self.provider.docs)


if __name__ == "__main__":
    unittest.main()
