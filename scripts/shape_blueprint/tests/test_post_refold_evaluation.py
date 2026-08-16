from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.shape_blueprint.tests.test_initial_admission import _cif_text, _write_bundle


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "shape_blueprint" / "evaluate_rfd3_post_refold.py"


def _module():
    assert MODULE_PATH.is_file(), "post-refold evaluator is absent"
    spec = importlib.util.spec_from_file_location("evaluate_rfd3_post_refold", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PostRefoldEvaluationTests(unittest.TestCase):
    def _records(self) -> dict:
        return {
            "boltz2": {"status": "completed", "task_type": "monomer", "native_metrics": {"confidence_score": 0.82, "ptm": 0.75}},
            "esmfold2": {"status": "completed", "task_type": "monomer", "native_metrics": {"plddt_mean": 84.0}},
            "protenix_v2": {"status": "completed", "task_type": "monomer", "native_metrics": {"confidence_score": 0.79}},
        }

    def test_independent_refold_and_native_metrics_are_accepted(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, request, manifest, _ = _write_bundle(root, candidate_text=_cif_text())
            output = root / "post_refold.json"
            result = module.evaluate_post_refold(
                candidate_path=candidate,
                source_structure_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=output,
                validator_records=self._records(),
            )
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["acceptance"]["status"], "accepted")
            self.assertEqual(result["post_refold"]["ca_rmsd_angstrom"], 0.0)
            self.assertEqual(result["validators"]["boltz2"]["task_type"], "monomer")
            self.assertNotIn("ipSAE", result["validators"]["boltz2"]["native_metrics"])
            self.assertTrue(output.is_file())

    def test_missing_validator_is_rejected_without_cross_validator_substitution(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, request, manifest, _ = _write_bundle(root, candidate_text=_cif_text())
            result = module.evaluate_post_refold(
                candidate_path=candidate,
                source_structure_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=root / "post_refold.json",
                validator_records={"esmfold2": self._records()["esmfold2"]},
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"]["code"], "validator_evidence_incomplete")
            self.assertIn("boltz2", result["reason"]["missing_validators"])
            self.assertIn("protenix_v2", result["reason"]["missing_validators"])

    def test_complex_boltz_requires_ipSAE_but_monomer_does_not(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, request, manifest, _ = _write_bundle(root, candidate_text=_cif_text())
            records = self._records()
            records["boltz2"] = {"status": "completed", "task_type": "complex", "native_metrics": {"confidence_score": 0.82}}
            result = module.evaluate_post_refold(
                candidate_path=candidate,
                source_structure_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=root / "post_refold.json",
                validator_records=records,
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"]["code"], "validator_evidence_incomplete")
            self.assertIn("boltz2", result["reason"]["invalid_validators"])


if __name__ == "__main__":
    unittest.main()
