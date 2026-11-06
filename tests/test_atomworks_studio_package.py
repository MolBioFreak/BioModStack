import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_package", ROOT / "scripts/atomworks_studio_package.py")
assert spec is not None and spec.loader is not None
package = importlib.util.module_from_spec(spec)
import sys
sys.path.insert(0, str(ROOT / "scripts"))
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_service_worker_opt_out_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index.html"
            index.write_text("<html><head></head><body></body></html>")
            self.assertTrue(package.disable_worker(index))
            self.assertFalse(package.disable_worker(index))
            self.assertEqual(index.read_text().count(package.OPT_OUT), 1)

    def test_unexpected_index_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index.html"
            index.write_text("unexpected")
            with self.assertRaises(ValueError):
                package.disable_worker(index)
            self.assertEqual(index.read_text(), "unexpected")

    def test_private_host_patch_and_notice_receipt(self):
        import json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "web"
            (web / "assets").mkdir(parents=True)
            (web / "index.html").write_text("<html><head></head></html>")
            bundle = web / "assets/index-CUTfffbv.js"
            bundle.write_text('function Te(e){return e.startsWith(`/api/`)&&!e.startsWith(`/api/v1/`)?`/api/v1/${e.slice(5)}`:e}')
            with patch.object(package.importlib.metadata, "distributions", return_value=[]):
                package.prepare_host(web, root / "receipt")
                package.prepare_host(web, root / "receipt")
            self.assertEqual(bundle.read_text().count('return `/api/atomworks-studio`+'), 1)
            receipt = json.loads((root / "receipt/receipt.json").read_text())
            self.assertIn("notices/supplements/Patinae-LICENSE", receipt["notice_sha256"])
            self.assertIn("notices/supplements/Cython-LICENSE.txt", receipt["notice_sha256"])
            self.assertEqual(len(receipt["modified_web"]), 2)
            bundle.write_text("unexpected upgraded client")
            with self.assertRaises(ValueError):
                package.prepare_host(web, root / "receipt")

    @unittest.skipUnless(os.environ.get("STUDIO_PATCHED_WHEEL"), "rebuilt wheel not provided")
    def test_actual_rebuilt_package_provenance_and_unchanged_members(self):
        import hashlib
        import json
        import zipfile
        wheel = Path(os.environ["STUDIO_PATCHED_WHEEL"])
        release = Path(os.environ["STUDIO_RELEASE_WHEEL"])
        self.assertGreater(package.verify_record(wheel), 0)
        with self.assertRaises(ValueError):
            package.verify_wheel(wheel)  # Never pretend this is the published wheel.
        descriptor = json.loads((package.ASSETS / "host-build.json").read_text())
        manifest = json.loads((package.ASSETS / "manifest.json").read_text())
        with zipfile.ZipFile(wheel) as built, zipfile.ZipFile(release) as base:
            provenance = json.loads(built.read("annotation_studio/bms_build.json"))
            self.assertEqual(provenance["base_release_wheel_sha256"], package.WHEEL_SHA256)
            self.assertEqual(provenance["manifest_sha256"], package.digest(package.ASSETS / "manifest.json"))
            self.assertEqual(provenance["web_build"], "passed")
            for name in base.namelist():
                if name.endswith(".dist-info/RECORD") or name.startswith("annotation_studio/web/"):
                    continue
                if name in provenance["replaced_python"]:
                    expected = manifest["files"]["packages/" + ("api/" if name.startswith("annotation_studio/") else "editing/") + name]["after"]
                    self.assertEqual(hashlib.sha256(built.read(name)).hexdigest(), expected)
                else:
                    self.assertEqual(built.read(name), base.read(name), name)
            actual_web = {n.removeprefix("annotation_studio/web/"): hashlib.sha256(built.read(n)).hexdigest()
                          for n in built.namelist() if n.startswith("annotation_studio/web/")}
            self.assertEqual(actual_web, descriptor["web_files"])
            self.assertNotIn("annotation_studio/web/assets/index-CUTfffbv.js", built.namelist())

    @unittest.skipUnless(os.environ.get("STUDIO_PATCHED_WHEEL"), "rebuilt wheel not provided")
    def test_rebuilt_host_transform_binds_exact_assets_and_rejects_drift(self):
        import json
        import zipfile
        from unittest.mock import patch
        wheel = Path(os.environ["STUDIO_PATCHED_WHEEL"])
        descriptor = json.loads((package.ASSETS / "host-build.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(root / "installed")
            web = root / "installed/annotation_studio/web"
            with patch.object(package.importlib.metadata, "distributions", return_value=[]):
                package.prepare_host(web, root / "receipt", wheel)
            receipt = json.loads((root / "receipt/receipt.json").read_text())
            self.assertEqual(receipt["wheel_sha256"], package.digest(wheel))
            self.assertNotEqual(receipt["wheel_sha256"], receipt["base_release_wheel_sha256"])
            self.assertEqual(receipt["build_provenance"]["web_build"], "passed")
            bundle = web / descriptor["transport_asset"]
            self.assertEqual(bundle.read_text().count(descriptor["transport_replacement"]), 1)
            self.assertIn(package.OPT_OUT, (web / "index.html").read_text())
            # Start from the real wheel again, then tamper the scientific browser bundle.
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(root / "installed")
            bundle.write_text(bundle.read_text() + "\n// unreviewed build")
            with self.assertRaisesRegex(ValueError, "drift"):
                package.prepare_host(web, root / "bad-receipt", wheel)
            self.assertFalse((root / "bad-receipt/receipt.json").exists())

    def test_rejects_wrong_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wrong.whl"
            wheel.write_bytes(b"not a wheel")
            with self.assertRaises(ValueError):
                package.verify_wheel(wheel)

    @unittest.skipUnless(os.environ.get("STUDIO_RELEASE_WHEEL"), "release wheel not provided")
    def test_release_wheel(self):
        self.assertEqual(package.verify_wheel(Path(os.environ["STUDIO_RELEASE_WHEEL"])), 156)


if __name__ == "__main__":
    unittest.main()
