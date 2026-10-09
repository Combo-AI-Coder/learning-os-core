"""Frozen evidence bytes must survive Git's platform newline conversion."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.host_replay_compatibility import HISTORICAL_SOURCE_ARCHIVES

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git"), "Git is required for checkout parity")
class FrozenFixtureCheckoutTests(unittest.TestCase):
    def test_autocrlf_checkout_preserves_exact_frozen_fixture_bytes(self):
        # Use a separate object store and no inherited repository/global config.
        # The unmatched sentinel must convert, proving the test exercises EOL
        # conversion rather than passing because conversion is inactive.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            checkout.mkdir()
            template = root / "empty-template"
            template.mkdir()
            global_attributes = root / "empty-global-attributes"
            global_attributes.write_bytes(b"")
            env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_ATTR_NOSYSTEM="1")

            def git(*arguments):
                return subprocess.run(
                    ["git", "-c", "core.autocrlf=true", "-c",
                     f"core.attributesFile={global_attributes}", *arguments],
                    cwd=checkout, env=env, check=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=30,
                ).stdout

            git("init", "--quiet", f"--template={template}")
            for attribute_path in ('docs/evaluations/.gitattributes', 'tests/.gitattributes'):
                attributes = ROOT / attribute_path
                if attributes.exists():
                    destination = checkout / attribute_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(attributes.read_bytes())
            fixture_paths = (
                "tests/fixtures/core34-diagnostic-repair/heldout-cases/hd01.json",
                "tests/fixtures/core34-policy-history/teaching-decision-v0.3.md",
                "tests/fixtures/core34-diagnostic-repair/candidate3-reproduction-exporter.py.txt",
            )
            # The new experiment also freezes four pre-existing Markdown/Python
            # source files. Exercise their actual checkout attributes as well as
            # frozen fixture data; never weaken byte hashes by normalizing reads.
            history_manifest = ROOT / "tests/fixtures/core34-withdrawn-support/prospective-manifest.json"
            history_paths = json.loads(history_manifest.read_text(encoding="utf-8"))["historical_files"]
            fixture_paths += tuple(history_paths) + tuple(HISTORICAL_SOURCE_ARCHIVES.values())
            constrained_root = "tests/fixtures/core34-constrained-recovery"
            constrained_manifest = ROOT / constrained_root / "publication-manifest.json"
            constrained_paths = json.loads(constrained_manifest.read_text(encoding="utf-8"))["files"]
            fixture_paths += tuple(f"{constrained_root}/{name}" for name in constrained_paths)
            fixture_paths += (f"{constrained_root}/publication-manifest.json",)
            closure_root = "tests/fixtures/core34-answer-closure"
            closure_manifest = ROOT / closure_root / "publication-manifest.json"
            closure_paths = json.loads(closure_manifest.read_text(encoding="utf-8"))["files"]
            fixture_paths += tuple(f"{closure_root}/{name}" for name in closure_paths)
            fixture_paths += (f"{closure_root}/publication-manifest.json",)
            expected = {path: (ROOT / path).read_bytes() for path in fixture_paths}
            expected["tests/fixtures/synthetic-crlf.txt"] = b"frozen\r\nbytes\r\n"
            sentinel = "tests/unprotected-sentinel.txt"
            for path, content in {**expected, sentinel: b"convert\nthis\n"}.items():
                target = checkout / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            git("add", "--all")
            for path, content in expected.items():
                self.assertEqual(content, git("show", f":{path}"), path)
            for path in (*expected, sentinel):
                (checkout / path).unlink()
            git("checkout-index", "--all", "--force")
            for path, content in expected.items():
                self.assertEqual(content, (checkout / path).read_bytes(), path)
            self.assertEqual(b"convert\r\nthis\r\n", (checkout / sentinel).read_bytes())


if __name__ == "__main__":
    unittest.main()
