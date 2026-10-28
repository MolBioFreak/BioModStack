import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_package", ROOT / "scripts/atomworks_studio_package.py")
assert spec is not None and spec.loader is not None
package = importlib.util.module_from_spec(spec)
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
