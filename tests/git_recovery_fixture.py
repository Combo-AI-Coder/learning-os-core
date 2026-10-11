"""Offline, synthetic Core #34 journey through the actual local Git provider.

This is a scripted integration check, not a model trial or a live Host test.
The producer exits only AFTER create_evidence returns success. It does not test
mid-write crashes, power loss, multi-process admission, or authority takeover.
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

import yaml

from scripts.reference_host import ReferenceLearningHost
from scripts.runtime_adapter import GitCliProvider, GitRepositoryBinding
from scripts.runtime_broker import DeploymentWriteGate, RuntimeCapabilityPolicy
from tests.test_runtime_broker import (
    CORE_ID, INSTANCE_ID, RC_ID, READ_PATH, READ_V1, REGISTRY_PATH, RUNTIME_PATH,
    branch_registry, branch_runtime, contract, knowledge_candidate, locator,
    typed_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "evi-git-producer-loss-001"
EVIDENCE_PATH = f"evidence/{EVIDENCE_ID}.yaml"
PRODUCER_EXIT = 73
FIXTURE_KIND = "core34-local-git-recovery-v1"


def isolated_env() -> dict[str, str]:
    # No ambient Git configuration, hooks, credentials, SSH agent or network
    # transports are needed. Preserve only process/locale prerequisites.
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "TMPDIR", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    # GIT_ALLOW_PROTOCOL constrains this fixture's own Git commands. The
    # production provider constructs its own environment; its local-only use
    # here comes from the three fixed absolute local bindings below.
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
                "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def git(*args: str, cwd: Path | None = None, binary: bool = False):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull,
         "-c", "commit.gpgSign=false", *args],
        cwd=cwd, env=isolated_env(), capture_output=True,
        timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"fixture git failed ({result.returncode}): "
                           + result.stderr.decode("utf-8", errors="replace"))
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii")
                        + b"\0" + content).hexdigest()


def evidence_text() -> str:
    value = yaml.safe_load(typed_evidence(evidence_id=EVIDENCE_ID))
    value["context"] = {"topic": "synthetic", "subtopic": "unit"}
    return yaml.safe_dump(value, sort_keys=False)


def _commit(seed: Path, message: str) -> str:
    git("add", "--all", cwd=seed)
    git("commit", "-q", "-m", message, cwd=seed)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=seed)
    return git("rev-parse", "HEAD", cwd=seed)


def _repository(root: Path, name: str, files: dict[str, bytes]) -> str:
    remote, seed = root / (name + ".git"), root / (name + "-seed")
    git("init", "--template=", "--bare", "-q", str(remote))
    git("--git-dir", str(remote), "config", "uploadpack.allowFilter", "true")
    git("init", "--template=", "-q", "-b", "main", str(seed))
    git("config", "user.name", "Synthetic Git Journey", cwd=seed)
    git("config", "user.email", "synthetic-journey@invalid.local", cwd=seed)
    git("config", "core.autocrlf", "false", cwd=seed)
    for relative, content in files.items():
        path = seed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git("remote", "add", "origin", str(remote), cwd=seed)
    return _commit(seed, "synthetic initial " + name)


def prepare(root: Path) -> dict:
    # Git-tracked source bytes only; never copy an enclosing workspace, .git,
    # untracked output, private Instance, or an installed user environment.
    paths = git("ls-files", "-z", cwd=ROOT, binary=True).decode("utf-8").split("\0")
    files, identity = {}, []
    for relative in sorted(path for path in paths if path):
        source = ROOT / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("source snapshot requires regular tracked files")
        content = source.read_bytes()
        files[relative] = content
        identity.append({"path": relative, "git_blob_sha": blob_sha(content)})
    core_commit = _repository(root, "core", files)
    rc_commit = _repository(root, "control", {
        "deployment.yaml": yaml.safe_dump(contract(core_commit=core_commit),
                                           sort_keys=False).encode("utf-8"),
    })
    instance_files = {
        "README.md": b"# Synthetic Instance, not deployed\n",
        "config/instance.yaml": yaml.safe_dump({
            "schema_version": "0.4", "document_type": "instance_config",
            "product": {"id": "learning-os"},
            "instance": {"display_timezone": "UTC"},
        }, sort_keys=False).encode("utf-8"),
        REGISTRY_PATH: yaml.safe_dump(branch_registry(subtopic="unit"),
                                     sort_keys=False).encode("utf-8"),
        RUNTIME_PATH: yaml.safe_dump(branch_runtime(), sort_keys=False).encode("utf-8"),
        READ_PATH: READ_V1.encode("utf-8"),
    }
    instance_commit = _repository(root, "instance", instance_files)
    actual_core = raw_snapshot(root, "core")["blobs"]
    if actual_core != {row["path"]: row["git_blob_sha"] for row in identity}:
        raise AssertionError("Git staging changed source snapshot bytes")
    result = {"fixture_kind": FIXTURE_KIND,
              "core_test_commit": core_commit, "control_test_commit": rc_commit,
              "instance_initial_commit": instance_commit,
              "source_files": identity,
              "source_identity_sha256": hashlib.sha256(json.dumps(
                  identity, sort_keys=True, separators=(",", ":")
              ).encode("utf-8")).hexdigest(),
              "runner_files": [{"path": relative, "git_blob_sha": blob_sha(
                  (ROOT / relative).read_bytes())} for relative in (
                      "tests/git_recovery_fixture.py", "tests/test_git_recovery.py")],
              "python_version": sys.version,
              "git_version": git("--version"),
              "authority": "fixture-established Main generation 3; no acquisition or takeover",
              "data": "synthetic only; local file-transport Git repositories"}
    dump(root / "source-identity.json", result)
    return result


def raw_snapshot(root: Path, name: str = "instance") -> dict:
    remote = root / (name + ".git")
    head = git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
    entries = git("--git-dir", str(remote), "ls-tree", "-rz", head, binary=True)
    blobs = {}
    for entry in entries.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            _, kind, sha = metadata.decode("ascii").split()
            if kind != "blob":
                raise AssertionError("non-blob fixture entry")
            blobs[path.decode("utf-8")] = sha
    count = int(git("--git-dir", str(remote), "rev-list", "--count", head))
    parents = git("--git-dir", str(remote), "show", "-s", "--format=%P", head).split()
    changes = git("--git-dir", str(remote), "diff-tree", "--root", "--no-commit-id",
                  "--name-only", "-r", "-z", head, binary=True)
    ref_lines = git("--git-dir", str(remote), "for-each-ref",
                    "--format=%(refname) %(objectname)").splitlines()
    return {"head": head, "commits": count, "blobs": blobs, "parents": parents,
            "changed_paths": [p.decode("utf-8") for p in changes.split(b"\0") if p],
            "refs": dict(line.split(" ", 1) for line in ref_lines)}


def assert_one_commit(before: dict, after: dict, path: str) -> None:
    assert after["parents"] == [before["head"]]
    assert after["changed_paths"] == [path]
    assert after["commits"] == before["commits"] + 1
    assert after["refs"] == {**before["refs"], "refs/heads/main": after["head"]}


def raw_file(root: Path, path: str) -> bytes:
    return git("--git-dir", str(root / "instance.git"), "show",
               "refs/heads/main:" + path, binary=True)


def _advance_readme(root: Path) -> None:
    seed = root / "instance-seed"
    git("fetch", "-q", "origin", "main", cwd=seed)
    git("reset", "--hard", "-q", "FETCH_HEAD", cwd=seed)
    (seed / "README.md").write_bytes(b"# Synthetic concurrent writer\n")
    _commit(seed, "synthetic competing README update")


def worker(root: Path, mode: str) -> None:
    identity = json.loads((root / "source-identity.json").read_text(encoding="utf-8"))
    if identity.get("fixture_kind") != FIXTURE_KIND:
        raise ValueError("worker requires this test's synthetic fixture marker")
    class RaceProvider(GitCliProvider):
        def update_text(self, *args, **kwargs):
            # A genuine independent local Git writer advances the branch after
            # broker checks, while leaving the Knowledge target blob unchanged.
            _advance_readme(root)
            return super().update_text(*args, **kwargs)

    writes = (() if mode == "read" else
              (("evidence",) if mode in {"produce", "retry", "conflicting_evidence"}
               else (READ_PATH,)))
    cls = RaceProvider if mode == "race" else GitCliProvider
    provider = cls([
        GitRepositoryBinding(CORE_ID, str(root / "core.git"), "synthetic/core"),
        GitRepositoryBinding(RC_ID, str(root / "control.git"), "synthetic/control"),
        GitRepositoryBinding(INSTANCE_ID, str(root / "instance.git"),
                             "synthetic/instance", writable=bool(writes)),
    ])
    host = ReferenceLearningHost.open(
        provider=provider, locator_source=locator(), branch_runtime_path=RUNTIME_PATH,
        policy=RuntimeCapabilityPolicy(
            readable_roots=("learner/knowledge", "evidence"), writable_roots=writes),
        write_admission=DeploymentWriteGate(), expected_generation=3,
    )
    try:
        if mode in {"produce", "retry", "conflicting_evidence"}:
            content = evidence_text()
            if mode == "conflicting_evidence":
                conflict = yaml.safe_load(content)
                conflict["observation"]["summary"] = "attempted replacement, not a new performance"
                content = yaml.safe_dump(conflict, sort_keys=False)
            result = host.invoke({"operation": "create_evidence",
                                  "arguments": {"content": content}})
            print(json.dumps({"mode": mode, "response": result}), flush=True)
            if mode == "produce" and result.get("ok") and result["result"]["applied"]:
                # The operation has returned: no Git subprocess remains in flight.
                # Deliberately bypass host/provider cleanup and all later writes.
                os._exit(PRODUCER_EXIT)
            if mode == "produce":
                raise AssertionError("producer did not reach the declared cut")
            return
        discovery = host.invoke({"operation": "discover_learning_evidence", "arguments": {}})
        if mode == "read":
            print(json.dumps({"mode": mode, "discovery": discovery}), flush=True)
            return
        if not discovery.get("ok"):
            raise AssertionError(discovery)
        rows = discovery["result"]["evidence"]
        if len(rows) != 1:
            raise AssertionError("expected one discovered fixture occurrence")
        # Read the actual durable record; no producer response, ID, blob token or
        # serialized provider state is passed into this fresh process.
        discovered_id = yaml.safe_load(rows[0]["content"])["id"]
        read = host.invoke({"operation": "read_learning_context",
                            "arguments": {"required_paths": [READ_PATH]}})
        if not read.get("ok"):
            raise AssertionError(read)
        document = read["result"]["documents"][0]
        current = yaml.safe_load(document["content"])
        candidate = knowledge_candidate(evidence_id=discovered_id,
                                        revision=current["revision"] + 1)
        expected_blob = "0" * 40 if mode == "stale_blob" else document["version_token"]
        result = host.invoke({"operation": "reconcile_knowledge", "arguments": {
            "content": candidate, "expected_version_token": expected_blob}})
        print(json.dumps({"mode": mode, "discovery": discovery, "read": read,
                          "response": result}), flush=True)
    finally:
        host.close()
        provider.close()


class GitRecoveryJourney:
    def __init__(self, root: Path):
        self.root = root
        self.records = []

    def process(self, mode: str, expected_exit: int = 0) -> dict:
        number = len(self.records) + 1
        before = raw_snapshot(self.root)
        process_temp = self.root / f"{number:02d}-{mode}-temporary"
        process_temp.mkdir()
        env = isolated_env()
        # Abrupt producer exit intentionally skips TemporaryDirectory cleanup.
        # Keep every child-owned temporary under this disposable fixture root.
        env.update({key: str(process_temp) for key in ("TMPDIR", "TEMP", "TMP")})
        started = time.monotonic()
        process = subprocess.run(
            [sys.executable, "-m", "tests.git_recovery_fixture", "--worker",
             str(self.root), mode], cwd=ROOT, env=env,
            capture_output=True, timeout=120, check=False,
        )
        prefix = self.root / f"{number:02d}-{mode}"
        prefix.with_suffix(".stdout").write_bytes(process.stdout)
        prefix.with_suffix(".stderr").write_bytes(process.stderr)
        record = {"mode": mode, "returncode": process.returncode,
                  "expected_returncode": expected_exit,
                  "elapsed_seconds": round(time.monotonic() - started, 4),
                  "stdout_sha256": hashlib.sha256(process.stdout).hexdigest(),
                  "stderr_sha256": hashlib.sha256(process.stderr).hexdigest(),
                  "before": before, "after": raw_snapshot(self.root)}
        self.records.append(record)
        dump(self.root / "process-records.json", self.records)
        if process.returncode != expected_exit:
            raise AssertionError(f"{mode} exit {process.returncode}; "
                                 + process.stderr.decode("utf-8", errors="replace"))
        result = json.loads(process.stdout)
        record["result"] = result
        dump(self.root / "process-records.json", self.records)
        return result

    def run(self) -> dict:
        if not __debug__:
            raise RuntimeError("run without -O: integration assertions are required")
        identity = prepare(self.root)
        initial = raw_snapshot(self.root)
        protected = {name: raw_snapshot(self.root, name) for name in ("core", "control")}
        initial_knowledge = raw_file(self.root, READ_PATH)

        produced = self.process("produce", PRODUCER_EXIT)
        assert produced["response"] == {"surface_version": "v4", "ok": True,
            "operation": "create_evidence", "result": {"applied": True}}
        after_evidence = raw_snapshot(self.root)
        assert_one_commit(initial, after_evidence, EVIDENCE_PATH)
        assert raw_file(self.root, EVIDENCE_PATH) == evidence_text().encode("utf-8")
        assert raw_file(self.root, READ_PATH) == initial_knowledge
        assert after_evidence["blobs"] == {**initial["blobs"], EVIDENCE_PATH:
                                          blob_sha(evidence_text().encode("utf-8"))}

        recovered = self.process("read")["discovery"]
        assert recovered["ok"] and len(recovered["result"]["evidence"]) == 1
        row = recovered["result"]["evidence"][0]
        assert row["content"].encode("utf-8") == raw_file(self.root, EVIDENCE_PATH)
        assert row["knowledge_references"][0]["reference_status"] == "not_referenced_by_current_knowledge"
        assert raw_snapshot(self.root) == after_evidence

        reconciliation = self.process("reconcile")
        assert reconciliation["read"]["result"]["documents"][0]["version_token"] == after_evidence["blobs"][READ_PATH]
        reconciled = reconciliation["response"]
        assert reconciled["ok"] and reconciled["result"]["applied"]
        after_knowledge = raw_snapshot(self.root)
        expected_knowledge = knowledge_candidate(evidence_id=EVIDENCE_ID).encode("utf-8")
        assert raw_file(self.root, READ_PATH) == expected_knowledge
        assert_one_commit(after_evidence, after_knowledge, READ_PATH)
        assert after_knowledge["blobs"] == {**after_evidence["blobs"],
                                           READ_PATH: blob_sha(expected_knowledge)}
        successor = self.process("read")["discovery"]
        assert successor["ok"] and len(successor["result"]["evidence"]) == 1
        assert successor["result"]["evidence"][0]["knowledge_references"][0]["reference_status"] == "referenced"
        assert raw_snapshot(self.root) == after_knowledge

        retry = self.process("retry")["response"]
        assert retry["ok"] and retry["result"]["applied"] is False
        assert raw_snapshot(self.root) == after_knowledge

        conflict = self.process("conflicting_evidence")["response"]
        assert conflict["ok"] is False and conflict["error"]["code"] == "guard_rejected"
        assert raw_snapshot(self.root) == after_knowledge

        stale = self.process("stale_blob")["response"]
        assert stale["ok"] is False and stale["error"]["code"] == "cas_conflict"
        assert raw_snapshot(self.root) == after_knowledge

        raced = self.process("race")["response"]
        assert raced["ok"] is False and raced["error"]["code"] == "cas_conflict"
        after_race = raw_snapshot(self.root)
        assert_one_commit(after_knowledge, after_race, "README.md")
        assert after_race["blobs"] == {**after_knowledge["blobs"],
                                      "README.md": blob_sha(b"# Synthetic concurrent writer\n")}
        assert raw_file(self.root, EVIDENCE_PATH) == evidence_text().encode("utf-8")
        assert raw_file(self.root, READ_PATH) == expected_knowledge
        assert {name: raw_snapshot(self.root, name) for name in protected} == protected
        result = {"status": "passed", "source_identity": identity,
                  "processes": self.records, "final": after_race,
                  "scope": "scripted local-Git post-commit producer loss, fresh-process recovery and exact-head race",
                  "not_established": ["teaching benefit", "model autonomy", "mid-write crash or power loss",
                                      "shared multiprocess admission", "real Host or GitHub durability",
                                      "generation acquisition or takeover", "deployment or learner acceptance"]}
        dump(self.root / "result.json", result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=2, metavar=("ROOT", "MODE"))
    args = parser.parse_args()
    if args.worker:
        if args.output:
            parser.error("worker and output are exclusive")
        root, mode = args.worker
        if mode not in {"produce", "read", "reconcile", "retry", "race",
                        "conflicting_evidence", "stale_blob"}:
            parser.error("unknown worker mode")
        worker(Path(root).resolve(), mode)
        return
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("output must be outside the Core checkout")
    output.mkdir(parents=True, exist_ok=False)
    journey = GitRecoveryJourney(output)
    try:
        result = journey.run()
    except BaseException as exc:
        dump(output / "failure.json", {"type": type(exc).__name__, "message": str(exc),
                                      "completed_processes": journey.records})
        raise
    print(json.dumps({"status": result["status"], "process_count": len(result["processes"]),
                      "source_identity_sha256": result["source_identity"]["source_identity_sha256"]}))


if __name__ == "__main__":
    main()
