"""Native Fold-CP CLI contract; run with the image's Python, not host mocks.

Example: apptainer exec --cleanenv --bind "$PWD/tests:/runtime-tests:ro" \
    --env BMS_TEST_FOLD_CP_NATIVE=1 \
    <new.sif> python3 /runtime-tests/test_fold_cp_native_cli.py
Only run_predict is intercepted: CLI imports, Click validation, enum conversion,
and all argument forwarding use the actual installed native modules.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("BMS_TEST_FOLD_CP_NATIVE") == "1", "requires the Fold-CP image interpreter")
class NativeTopologyCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global CliRunner, main, prediction
        from click.testing import CliRunner
        import boltz.distributed.main as main
        import boltz.distributed.predict as prediction

    def invoke(self, extra):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "model.ckpt"
            checkpoint.touch()
            argv = [
                "predict", directory, "--checkpoint", str(checkpoint),
                "--mol_dir", directory, "--cache", directory,
            ]
            with patch.object(prediction, "run_predict") as predict, patch.object(
                main, "download_boltz2", side_effect=AssertionError("unexpected download")
            ):
                result = CliRunner().invoke(main.cli, argv + extra)
            return result, predict

    def test_default_is_2d_and_existing_defaults_survive(self):
        result, predict = self.invoke([])
        self.assertEqual(result.exit_code, 0, result.output)
        values = predict.call_args.kwargs
        self.assertEqual(values["cp_topology"], "2d")
        self.assertEqual(values["size_cp"], 1)
        self.assertEqual(values["accelerator"], "gpu")
        self.assertEqual(values["recycling_steps"], 3)
        self.assertEqual(values["sampling_steps"], 200)
        self.assertEqual(values["diffusion_samples"], 1)
        self.assertEqual(values["step_scale"], 1.5)
        self.assertEqual(values["output_format"], "mmcif")
        self.assertFalse(values["write_full_pae"])
        self.assertTrue(values["use_templates"])
        self.assertTrue(values["auto_pad_tokens_for_sm100f"])

    def test_explicit_1d_forwards_without_changing_other_settings(self):
        options = [
            "--size_cp", "3", "--accelerator", "cpu", "--sampling_steps", "17",
            "--recycling_steps", "2", "--diffusion_samples", "4", "--seed", "42",
            "--write_full_pae", "--output_format", "pdb", "--precision", "FP32",
        ]
        default, default_predict = self.invoke(options)
        one_d, one_d_predict = self.invoke(options + ["--cp_topology", "1d"])
        self.assertEqual(default.exit_code, 0, default.output)
        self.assertEqual(one_d.exit_code, 0, one_d.output)
        baseline = dict(default_predict.call_args.kwargs)
        actual = dict(one_d_predict.call_args.kwargs)
        self.assertEqual(actual.pop("cp_topology"), "1d")
        self.assertEqual(baseline.pop("cp_topology"), "2d")
        # Scratch path identities differ; all inference values must be identical.
        for key in ("data", "mol_dir", "checkpoint"):
            actual.pop(key)
            baseline.pop(key)
        self.assertEqual(actual, baseline)
        self.assertEqual(actual["size_cp"], 3)
        self.assertEqual(actual["sampling_steps"], 17)
        self.assertTrue(actual["write_full_pae"])

    def test_explicit_2d_forwards(self):
        result, predict = self.invoke(["--cp_topology", "2d", "--size_cp", "4"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(predict.call_args.kwargs["cp_topology"], "2d")
        self.assertEqual(predict.call_args.kwargs["size_cp"], 4)

    def test_invalid_topology_uses_native_click_choice(self):
        result, predict = self.invoke(["--cp_topology", "3d"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Invalid value for '--cp_topology'", result.output)
        predict.assert_not_called()

    def test_help_and_native_python_default(self):
        import inspect

        result = CliRunner().invoke(main.cli, ["predict", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--cp_topology [2d|1d]", result.output)
        self.assertEqual(inspect.signature(prediction.run_predict).parameters["cp_topology"].default, "2d")
        self.assertTrue(main.__file__.startswith("/opt/fold-cp/src/"))
        self.assertTrue(prediction.__file__.startswith("/opt/fold-cp/src/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
