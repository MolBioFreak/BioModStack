"""Exercise the patch applicator against a genuine pinned upstream Git checkout.

Set BMS_ATOMWORKS_SOURCE to a local upstream clone with the reviewed commit.
Each case clones that object store into an isolated TMPDIR checkout; no source,
server, or scientific store is modified by these tests.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("studio_patches", ROOT / "scripts/apply_atomworks_studio_patches.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MANIFEST = json.loads((MODULE.ASSETS / "manifest.json").read_text())


class ManifestTests(unittest.TestCase):
    def test_patch_digests_and_paths(self):
        for item in MANIFEST["patches"]:
            self.assertEqual(MODULE.digest(MODULE.safe_path(MODULE.ASSETS, item["file"])), item["sha256"])
        for relative in MANIFEST["files"]:
            MODULE.safe_path(ROOT, relative)
        self.assertEqual(len(MANIFEST["upstream_commit"]), 40)

    def test_path_traversal_refused(self):
        with self.assertRaises(ValueError):
            MODULE.safe_path(ROOT, "../outside")


@unittest.skipUnless(os.environ.get("BMS_ATOMWORKS_SOURCE"), "Set BMS_ATOMWORKS_SOURCE to real upstream source")
class RealCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio-patch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        subprocess.run(["git", "clone", "--shared", "--no-checkout", os.environ["BMS_ATOMWORKS_SOURCE"], str(self.source)],
                       check=True, capture_output=True)
        subprocess.run(["git", "checkout", "--detach", MANIFEST["upstream_commit"]], cwd=self.source,
                       check=True, capture_output=True)

    def test_apply_verify_and_idempotence(self):
        result = MODULE.apply(self.source)
        self.assertTrue(result["source_verified"])
        self.assertEqual(MODULE.apply(self.source, verify_only=True), result)
        self.assertEqual(MODULE.apply(self.source), result)
        MODULE.check_files(self.source, MANIFEST["files"], "after")

    def test_source_drift_refused_before_writes(self):
        target = self.source / "packages/web/src/main.ts"
        target.write_text(target.read_text() + "\n// drift\n")
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            MODULE.apply(self.source)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.source / "tests/test_bms_s03.py").exists())

    def test_locked_input_drift_refused(self):
        target = self.source / "pnpm-lock.yaml"
        target.write_text(target.read_text() + "\n# drift\n")
        with self.assertRaises(ValueError):
            MODULE.apply(self.source)
        self.assertFalse((self.source / "tests/test_bms_s03.py").exists())

    def test_tampered_applied_source_refused(self):
        MODULE.apply(self.source)
        target = self.source / "packages/web/src/main.ts"
        target.write_text(target.read_text() + "\n// drift\n")
        with self.assertRaises(ValueError):
            MODULE.apply(self.source, verify_only=True)


if __name__ == "__main__":
    unittest.main()
