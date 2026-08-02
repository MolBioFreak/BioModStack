from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parents[1]))

from rfd3_shape_sampler import ShapeGuidedDiffusionSampler  # noqa: E402


class RFD3ShapeSamplerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        axis = np.linspace(-2.0, 2.0, 9, dtype=np.float32)
        xyz = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
        sdf = np.asarray(1.0 - np.max(np.abs(xyz), axis=-1), dtype="<f4")
        points = np.asarray(
            [[-0.75, 0.0, 0.0], [-0.25, 0.0, 0.0], [0.25, 0.0, 0.0], [0.75, 0.0, 0.0]],
            dtype="<f4",
        )
        self.points_path = root / "points.f32le"
        self.sdf_path = root / "sdf.f32le"
        self.manifest_path = root / "manifest.json"
        self.receipt_path = root / "receipt.jsonl"
        self.points_path.write_bytes(points.tobytes())
        self.sdf_path.write_bytes(sdf.tobytes())
        self.manifest_path.write_text(
            json.dumps(
                {
                    "geometry_sha256": "a" * 64,
                    "point_count": len(points),
                    "point_pool_sha256": hashlib.sha256(points.tobytes()).hexdigest(),
                    "sdf_grid_shape": list(sdf.shape),
                    "sdf_origin_angstrom": [-2.0, -2.0, -2.0],
                    "sdf_spacing_angstrom": [0.5, 0.5, 0.5],
                    "sdf_sha256": hashlib.sha256(sdf.tobytes()).hexdigest(),
                },
                sort_keys=True,
            )
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sampler(self, *, step_size: float = 0.5) -> ShapeGuidedDiffusionSampler:
        return ShapeGuidedDiffusionSampler(
            shape_manifest_path=str(self.manifest_path),
            shape_points_path=str(self.points_path),
            shape_sdf_path=str(self.sdf_path),
            shape_expected_geometry_sha256="a" * 64,
            shape_expected_point_pool_sha256=hashlib.sha256(self.points_path.read_bytes()).hexdigest(),
            shape_receipt_path=str(self.receipt_path),
            shape_step_size=step_size,
            shape_max_update=0.2,
        )

    @staticmethod
    def features() -> dict[str, torch.Tensor]:
        return {
            "atom_to_token_map": torch.tensor([0, 0, 1, 1, 2, 2]),
            "is_central": torch.tensor([False, True, False, True, False, True]),
            "is_protein": torch.tensor([True, True, False]),
            "diffusion_mask": torch.tensor([False, False, True, True, False, False]),
        }

    def test_source_parity_defaults_do_not_add_invented_connectivity_force(self) -> None:
        self.assertEqual(self.sampler().shape_connectivity_weight, 0.0)

    def test_hash_mismatch_fails_before_sampling(self) -> None:
        sampler = self.sampler()
        sampler.shape_expected_point_pool_sha256 = "b" * 64
        with self.assertRaisesRegex(ValueError, "point-pool hash"):
            sampler.load_field(torch.device("cpu"))

    def test_derivative_guidance_broadcasts_ca_update_and_preserves_fixed_and_nonprotein_atoms(self) -> None:
        sampler = self.sampler()
        denoised = torch.tensor(
            [[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0], [0.20, 0.0, 0.0], [0.30, 0.0, 0.0], [3.0, 0.0, 0.0], [3.1, 0.0, 0.0]]],
            dtype=torch.float32,
        )
        base_delta = torch.zeros_like(denoised)
        features = self.features()
        guided = sampler.guide_coordinate_derivative(
            X_noisy_L=denoised + 1.0,
            X_denoised_L=denoised,
            delta_L=base_delta,
            t_hat=torch.tensor(2.0),
            f=features,
            step_i=50,
            total_steps=100,
            fixed_atom_mask=features["diffusion_mask"],
        )
        adjustment = guided - base_delta
        torch.testing.assert_close(adjustment[:, 0], adjustment[:, 1])
        self.assertGreater(float(torch.linalg.vector_norm(adjustment[:, 0])), 0.0)
        torch.testing.assert_close(adjustment[:, 2:4], torch.zeros_like(adjustment[:, 2:4]))
        torch.testing.assert_close(adjustment[:, 4:6], torch.zeros_like(adjustment[:, 4:6]))
        self.assertLessEqual(float(torch.linalg.vector_norm(adjustment, dim=-1).max()), 0.100001)

        receipt = json.loads(self.receipt_path.read_text().splitlines()[0])
        self.assertEqual(receipt["step_index"], 50)
        self.assertEqual(receipt["geometry_sha256"], "a" * 64)
        self.assertEqual(receipt["guided_ca_count"], 1)
        self.assertEqual(receipt["schema"], "bms_rfd3_shape_guidance_step_v3")
        self.assertEqual(receipt["guidance_decay"], "constant")
        self.assertEqual(receipt["gradient_scaling"], "raw")
        self.assertEqual(receipt["outside_reduction"], "sum")
        self.assertEqual(receipt["coordinate_state"], "delta_L")
        self.assertEqual(receipt["guidance_reference"], "X_denoised_L")
        self.assertEqual(receipt["edm_t_hat"], 2.0)
        self.assertEqual(receipt["connectivity_weight"], 0.0)

    def test_source_parity_constant_guidance_is_active_at_first_reverse_step(self) -> None:
        sampler = self.sampler()
        denoised = torch.tensor([[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0]]])
        features = {
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
        }
        guided = sampler.guide_coordinate_derivative(
            X_noisy_L=denoised + 1.0,
            X_denoised_L=denoised,
            delta_L=torch.zeros_like(denoised),
            t_hat=torch.tensor(1.0),
            f=features,
            step_i=0,
            total_steps=100,
            fixed_atom_mask=torch.tensor([False, False]),
        )
        self.assertGreater(float(torch.linalg.vector_norm(guided)), 0.0)
        self.assertTrue(self.receipt_path.exists())

    def test_guidance_is_integrated_by_the_native_edm_step(self) -> None:
        sampler = self.sampler(step_size=0.01)
        denoised = torch.tensor([[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0]]])
        noisy = denoised + 1.0
        t_hat = torch.tensor(2.0)
        d_t = torch.tensor(-0.5)
        base_delta = (noisy - denoised) / t_hat
        features = {
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
        }
        guided_delta = sampler.guide_coordinate_derivative(
            X_noisy_L=noisy,
            X_denoised_L=denoised,
            delta_L=base_delta,
            t_hat=t_hat,
            f=features,
            step_i=0,
            total_steps=100,
            fixed_atom_mask=torch.tensor([False, False]),
        )
        base_next = noisy + d_t * base_delta
        guided_next = noisy + d_t * guided_delta
        denoised_ca = denoised[:, 1:2, :]
        projected_ca, _ = sampler.load_field(torch.device("cpu")).project(
            denoised_ca,
            step_size=sampler.shape_step_size,
            max_update=sampler.shape_max_update,
            chamfer_weight=sampler.shape_chamfer_weight,
            outside_weight=sampler.shape_outside_weight,
            connectivity_weight=sampler.shape_connectivity_weight,
        )
        expected_effect = (d_t / t_hat) * (denoised_ca - projected_ca)
        torch.testing.assert_close(guided_next[:, 0:1] - base_next[:, 0:1], expected_effect)
        torch.testing.assert_close(guided_next[:, 1:2] - base_next[:, 1:2], expected_effect)

    def test_zero_step_size_is_an_explicit_matched_control(self) -> None:
        sampler = self.sampler(step_size=0.0)
        coordinates = torch.randn((1, 2, 3))
        base_delta = torch.randn_like(coordinates)
        features = {
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
        }
        controlled = sampler.guide_coordinate_derivative(
            X_noisy_L=coordinates,
            X_denoised_L=coordinates,
            delta_L=base_delta,
            t_hat=torch.tensor(1.0),
            f=features,
            step_i=50,
            total_steps=100,
            fixed_atom_mask=torch.tensor([False, False]),
        )
        torch.testing.assert_close(controlled, base_delta)
        self.assertFalse(self.receipt_path.exists())


if __name__ == "__main__":
    unittest.main()
