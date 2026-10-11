"""Synthetic lost-host-ack witness; no network, real Host or learner data.

The deterministic producer cut is after a genuine local Git push has returned
success, before GitCliProvider.create_text returns to broker/host. This is not
an interrupted Git write, power-loss simulation, transport-failure test or
global exactly-once guarantee. It never changes production implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "source"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))

import yaml
from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import GitCliProvider, GitRepositoryBinding
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    CORE_ID, INSTANCE_ID, RC_ID, READ_PATH, READ_V1, REGISTRY_PATH, RUNTIME_PATH,
    branch_registry, branch_runtime, contract, knowledge_candidate, locator,
    typed_evidence,
)

KIND = "core34-local-git-lost-host-ack-v1"
CUT_EXIT = 74
CANONICAL_COMMIT = "83f08b016dd51a777ee91c0c4d77c0b85e046e4e"
CANONICAL_TREE = "1995f7807375044aa08873e309bee61e704abc3c"


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def env():
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "TMPDIR", "LANG", "LC_ALL")
    result = {key: os.environ[key] for key in keep if key in os.environ}
    result.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
                   "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return result


def git(*args, cwd=None, raw=False):
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull,
         "-c", "commit.gpgSign=false", *args],
        cwd=cwd, env=env(), capture_output=True, timeout=30, check=False)
    if completed.returncode:
        raise RuntimeError("fixture Git failed: " + completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout if raw else completed.stdout.decode("utf-8").strip()


def producer_payload():
    # Only producer/setup oracle uses this ID. Recovery workers discover it.
    value = yaml.safe_load(typed_evidence(evidence_id="evi-lost-host-ack-001"))
    value["context"] = {"topic": "synthetic", "subtopic": "unit"}
    return yaml.safe_dump(value, sort_keys=False)


def repository(root, name, files, modes=None):
    remote, seed = root / (name + ".git"), root / (name + "-seed")
    git("init", "--template=", "--bare", "-q", str(remote))
    git("--git-dir", str(remote), "config", "uploadpack.allowFilter", "true")
    git("init", "--template=", "-q", "-b", "main", str(seed))
    git("config", "user.name", "Synthetic Lost Ack", cwd=seed)
    git("config", "user.email", "synthetic-lost-ack@invalid.local", cwd=seed)
    git("config", "core.autocrlf", "false", cwd=seed)
    for relative, data in files.items():
        path = seed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if modes and modes[relative] == "100755":
            path.chmod(0o755)
    git("add", "--all", cwd=seed)
    git("commit", "-q", "-m", "synthetic initial " + name, cwd=seed)
    git("remote", "add", "origin", str(remote), cwd=seed)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=seed)
    return git("rev-parse", "HEAD", cwd=seed)


def snapshot(root, name="instance"):
    prefix = ("--git-dir", str(root / (name + ".git")))
    head = git(*prefix, "rev-parse", "refs/heads/main")
    tree = git(*prefix, "rev-parse", head + "^{tree}")
    blobs = {}
    for record in git(*prefix, "ls-tree", "-rz", head, raw=True).split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, sha = metadata.decode().split()
        assert kind == "blob"
        blobs[path.decode()] = {"sha": sha, "mode": mode}
    return {"head": head, "tree": tree, "blobs": blobs,
            "commits": int(git(*prefix, "rev-list", "--count", head)),
            "parents": git(*prefix, "show", "-s", "--format=%P", head).split(),
            "changed_paths": [x.decode() for x in git(
                *prefix, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", head,
                raw=True).split(b"\0") if x],
            "refs": dict(line.split(" ", 1) for line in git(
                *prefix, "for-each-ref", "--format=%(refname) %(objectname)").splitlines())}


def file_bytes(root, path):
    return git("--git-dir", str(root / "instance.git"), "show", "refs/heads/main:" + path, raw=True)


def one_commit(before, after, path):
    assert after["parents"] == [before["head"]]
    assert after["commits"] == before["commits"] + 1
    assert after["changed_paths"] == [path]
    assert after["refs"] == {**before["refs"], "refs/heads/main": after["head"]}


def prepare(root):
    manifest_bytes = (BASE / "evidence/source-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["canonical_commit"] == CANONICAL_COMMIT
    assert manifest["canonical_tree"] == CANONICAL_TREE
    assert len(manifest["files"]) == 267
    files, modes = {}, {}
    for record in manifest["files"]:
        path = SOURCE / record["path"]
        assert path.is_file() and not path.is_symlink()
        data = path.read_bytes()
        assert blob_sha(data) == record["git_blob_sha"], record["path"]
        files[record["path"]] = data
        modes[record["path"]] = record["mode"]
    assert {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*") if p.is_file()} == set(files)
    core = repository(root, "core", files, modes)
    assert snapshot(root, "core")["tree"] == CANONICAL_TREE
    control = repository(root, "control", {
        "deployment.yaml": yaml.safe_dump(contract(core_commit=core), sort_keys=False).encode()})
    instance = repository(root, "instance", {
        "README.md": b"# Synthetic lost-ack Instance; not deployed\n",
        "config/instance.yaml": yaml.safe_dump({
            "schema_version": "0.4", "document_type": "instance_config",
            "product": {"id": "learning-os"}, "instance": {"display_timezone": "UTC"}},
            sort_keys=False).encode(),
        REGISTRY_PATH: yaml.safe_dump(branch_registry(subtopic="unit"), sort_keys=False).encode(),
        RUNTIME_PATH: yaml.safe_dump(branch_runtime(), sort_keys=False).encode(),
        READ_PATH: READ_V1.encode(),
    })
    dump(root / "fixture-marker.json", {"fixture_kind": KIND})
    identity = {"canonical_commit": CANONICAL_COMMIT, "canonical_tree": CANONICAL_TREE,
                "source_manifest_sha256": sha256(manifest_bytes), "source_blob_count": 267,
                "script_sha256": sha256(Path(__file__).read_bytes()),
                "core_test_commit": core, "control_test_commit": control,
                "instance_initial_commit": instance, "python": sys.version,
                "git": git("--version"), "pyyaml": yaml.__version__,
                "authority": "fixture-established Main generation 3; no acquisition or takeover",
                "network": "three fixed absolute local Git bindings; no remote network endpoint"}
    dump(root / "source-runtime-identity.json", identity)
    return identity


def worker(root, mode):
    assert json.loads((root / "fixture-marker.json").read_text()) == {"fixture_kind": KIND}

    class LostAckProvider(GitCliProvider):
        def _git(self, *args, cwd=None, cas=False, binding=None):
            result = super()._git(*args, cwd=cwd, cas=cas, binding=binding)
            if args and args[0] == "push" and cas and binding is not None and binding.repository_id == INSTANCE_ID:
                # Unchanged real push has returned success. Do not return from
                # this call, create_text, broker or host. Diagnostic receipt is
                # separate from the consumer's marker and never read by it.
                dump(root / "injection-receipt.json", {
                    "cut": "after_real_instance_push_success_before_provider_return",
                    "super_git_returned_success": True, "at": utc(),
                    "producer_exit": CUT_EXIT, "host_response_emitted": False})
                os._exit(CUT_EXIT)
            return result

    writes = (() if mode == "read" else
              (("evidence",) if mode in {"produce", "retry"} else (READ_PATH,)))
    provider = (LostAckProvider if mode == "produce" else GitCliProvider)([
        GitRepositoryBinding(CORE_ID, str(root / "core.git"), "synthetic/core"),
        GitRepositoryBinding(RC_ID, str(root / "control.git"), "synthetic/control"),
        GitRepositoryBinding(INSTANCE_ID, str(root / "instance.git"), "synthetic/instance", writable=bool(writes)),
    ])
    host = ReferenceLearningHost.open(
        provider=provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
        policy=RuntimeCapabilityPolicy(readable_roots=("learner/knowledge", "evidence"), writable_roots=writes),
        write_admission=DeploymentWriteGate(), expected_generation=3)
    try:
        if mode == "produce":
            result = host.invoke({"operation": "create_evidence", "arguments": {"content": producer_payload()}})
            print(json.dumps({"unexpected_host_response": result}), flush=True)
            raise AssertionError("producer returned instead of reaching the declared cut")
        discovery = host.invoke({"operation": "discover_learning_evidence", "arguments": {}})
        if mode == "read":
            print(json.dumps({"mode": mode, "discovery": discovery}), flush=True)
            return
        assert discovery["ok"], discovery
        rows = discovery["result"]["evidence"]
        assert len(rows) == 1
        if mode == "retry":
            # No producer ID, original request, response or commit supplied.
            result = host.invoke({"operation": "create_evidence", "arguments": {"content": rows[0]["content"]}})
            print(json.dumps({"mode": mode, "discovery": discovery, "response": result}), flush=True)
            return
        evidence = yaml.safe_load(rows[0]["content"])
        read = host.invoke({"operation": "read_learning_context", "arguments": {"required_paths": [READ_PATH]}})
        assert read["ok"], read
        current = read["result"]["documents"][0]
        candidate = knowledge_candidate(evidence_id=evidence["id"],
                                        revision=yaml.safe_load(current["content"])["revision"] + 1)
        result = host.invoke({"operation": "reconcile_knowledge", "arguments": {
            "content": candidate, "expected_version_token": current["version_token"]}})
        print(json.dumps({"mode": mode, "discovery": discovery, "read": read, "response": result}), flush=True)
    finally:
        host.close()
        provider.close()


class Journey:
    def __init__(self, root):
        self.root, self.records = root, []

    def process(self, mode, exit_code=0):
        stage = self.root / (f"{len(self.records) + 1:02d}-" + mode)
        stage.mkdir()
        temporary = stage / "temporary"
        temporary.mkdir()
        child_env = env()
        child_env.update({key: str(temporary) for key in ("TMPDIR", "TEMP", "TMP")})
        before = snapshot(self.root)
        start, started = time.monotonic(), utc()
        with (stage / "stdout").open("wb") as out, (stage / "stderr").open("wb") as err:
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(self.root), mode],
                                       cwd=BASE, env=child_env, stdout=out, stderr=err, timeout=120, check=False)
        stdout, stderr = (stage / "stdout").read_bytes(), (stage / "stderr").read_bytes()
        record = {"mode": mode, "started_at": started, "completed_at": utc(),
                  "elapsed_seconds": round(time.monotonic() - start, 4),
                  "returncode": completed.returncode, "expected_returncode": exit_code,
                  "stdout_sha256": sha256(stdout), "stderr_sha256": sha256(stderr),
                  "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
                  "before": before, "after": snapshot(self.root)}
        self.records.append(record)
        dump(self.root / "process-records.json", self.records)
        assert len(stdout) <= 1048576 and len(stderr) <= 1048576, "post-run retained-output bound exceeded"
        assert completed.returncode == exit_code, (mode, completed.returncode, stderr.decode(errors="replace"))
        if mode == "produce":
            assert stdout == b"", "producer emitted an acknowledgement or unexpected output"
            assert stderr == b"", "unexpected producer failure output"
            return None
        result = json.loads(stdout)
        record["result"] = result
        dump(self.root / "process-records.json", self.records)
        return result

    def run(self):
        assert __debug__, "run without -O"
        identity = prepare(self.root)
        initial = snapshot(self.root)
        protected = {name: snapshot(self.root, name) for name in ("core", "control")}
        old_knowledge = file_bytes(self.root, READ_PATH)
        payload = producer_payload().encode()
        expected_id = yaml.safe_load(payload)["id"]
        evidence_path = "evidence/" + expected_id + ".yaml"
        self.process("produce", CUT_EXIT)
        cut = json.loads((self.root / "injection-receipt.json").read_text())
        assert cut["super_git_returned_success"] is True and cut["host_response_emitted"] is False
        after_evidence = snapshot(self.root)
        one_commit(initial, after_evidence, evidence_path)
        assert file_bytes(self.root, evidence_path) == payload
        assert file_bytes(self.root, READ_PATH) == old_knowledge
        assert after_evidence["blobs"] == {**initial["blobs"], evidence_path: {"sha": blob_sha(payload), "mode": "100644"}}

        first = self.process("read")["discovery"]
        assert first["ok"] and len(first["result"]["evidence"]) == 1
        row = first["result"]["evidence"][0]
        assert row["content"].encode() == payload
        assert row["knowledge_references"][0]["reference_status"] == "not_referenced_by_current_knowledge"
        assert snapshot(self.root) == after_evidence

        retry = self.process("retry")
        assert retry["response"]["ok"] and retry["response"]["result"]["applied"] is False
        assert retry["discovery"]["result"]["evidence"][0]["content"].encode() == payload
        assert snapshot(self.root) == after_evidence

        reconciled = self.process("reconcile")
        assert reconciled["read"]["result"]["documents"][0]["version_token"] == after_evidence["blobs"][READ_PATH]["sha"]
        assert reconciled["response"]["ok"] and reconciled["response"]["result"]["applied"] is True
        after_knowledge = snapshot(self.root)
        expected_knowledge = knowledge_candidate(evidence_id=expected_id).encode()
        one_commit(after_evidence, after_knowledge, READ_PATH)
        assert file_bytes(self.root, READ_PATH) == expected_knowledge
        assert after_knowledge["blobs"] == {**after_evidence["blobs"], READ_PATH: {"sha": blob_sha(expected_knowledge), "mode": "100644"}}

        final_read = self.process("read")["discovery"]
        assert final_read["ok"] and len(final_read["result"]["evidence"]) == 1
        assert final_read["result"]["evidence"][0]["knowledge_references"][0]["reference_status"] == "referenced"
        assert snapshot(self.root) == after_knowledge
        assert file_bytes(self.root, evidence_path) == payload
        assert {name: snapshot(self.root, name) for name in protected} == protected
        result = {"status": "passed", "identity": identity, "cut": cut, "processes": self.records,
                  "evidence_payload_sha256": sha256(payload), "knowledge_after_sha256": sha256(expected_knowledge),
                  "initial_protected": protected, "final_protected": {name: snapshot(self.root, name) for name in protected},
                  "final_instance": after_knowledge,
                  "claim": "local Git commit survived producer loss before host acknowledgement; fresh discovery, identical-payload no-op retry and one Knowledge reconciliation",
                  "limits": ["scripted deterministic seam, not blind/model behavior", "not Git-inflight crash, power loss or network acknowledgement failure",
                             "separate per-process gates, no shared admission/drain proof", "no live Host/learner, deployment, teaching benefit or global exactly-once guarantee",
                             "output files bounded for acceptance after each process; not a hard disk-write cap", "source fixture constructor and observer share expected payload; Git state read independently"]}
        dump(self.root / "result.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, metavar=("ROOT", "MODE"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        root, mode = args.worker
        assert args.output is None and mode in {"produce", "read", "retry", "reconcile"}
        worker(Path(root).resolve(), mode)
        return
    assert args.output is not None, "--output is required"
    root = args.output.resolve()
    assert root != SOURCE and SOURCE not in root.parents
    root.mkdir(parents=True, exist_ok=False)
    journey = Journey(root)
    try:
        result = journey.run()
    except BaseException as error:
        dump(root / "failure.json", {"type": type(error).__name__, "message": str(error), "processes": journey.records})
        raise
    print(json.dumps({"status": result["status"], "process_count": len(result["processes"]),
                      "script_sha256": result["identity"]["script_sha256"]}))


if __name__ == "__main__":
    main()
