from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))

from install_rfd3_shape_extension import BASE_SHA256, patch_sampler_source  # noqa: E402


class InstallRFD3ShapeExtensionTests(unittest.TestCase):
    def test_pinned_installed_sampler_receives_one_hook_and_registry_entry(self) -> None:
        source_path = Path("/usr/local/lib/python3.12/dist-packages/rfd3/model/inference_sampler.py")
        source = source_path.read_bytes()
        patched = patch_sampler_source(source)
        text = patched.decode("utf-8")
        self.assertEqual(BASE_SHA256, "e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f")
        self.assertEqual(text.count("self.guide_coordinate_derivative("), 1)
        self.assertEqual(text.count("self.post_step_coordinates("), 0)
        self.assertGreater(
            text.index("self.guide_coordinate_derivative("),
            text.index("# Compute the delta between the noisy and denoised coordinates, scaled by t_hat"),
        )
        self.assertLess(
            text.index("self.guide_coordinate_derivative("),
            text.index("X_L = X_noisy_L + step_scale * d_t * delta_L"),
        )
        self.assertIn("step_i=step_num, total_steps=len(noise_schedule) - 1", text)
        self.assertEqual(text.count('"shape": ShapeGuidedDiffusionSampler'), 1)
        self.assertIn('Literal["default", "symmetry", "shape"]', text)

    def test_unknown_sampler_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsupported RFD3 sampler source"):
            patch_sampler_source(b"not the audited sampler")


if __name__ == "__main__":
    unittest.main()
