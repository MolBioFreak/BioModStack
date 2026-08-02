from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
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

    def test_source_parity_defaults_do_not_add_invented_connectivity_force(self) -> None:
        sampler = self.sampler()
        self.assertEqual(sampler.shape_connectivity_weight, 0.0)

    def test_hash_mismatch_fails_before_sampling(self) -> None:
        sampler = self.sampler()
        sampler.shape_expected_point_pool_sha256 = "b" * 64
        with self.assertRaisesRegex(ValueError, "point-pool hash"):
            sampler.load_field(torch.device("cpu"))

    def test_guidance_broadcasts_ca_update_and_preserves_fixed_and_nonprotein_atoms(self) -> None:
        sampler = self.sampler()
        coordinates = torch.tensor(
            [[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0], [0.20, 0.0, 0.0], [0.30, 0.0, 0.0], [3.0, 0.0, 0.0], [3.1, 0.0, 0.0]]],
            dtype=torch.float32,
        )
        features = {
            "atom_to_token_map": torch.tensor([0, 0, 1, 1, 2, 2]),
            "is_central": torch.tensor([False, True, False, True, False, True]),
            "is_protein": torch.tensor([True, True, False]),
            "diffusion_mask": torch.tensor([False, False, True, True, False, False]),
        }
        guided = sampler.post_step_coordinates(
            X_L=coordinates,
            f=features,
            step_i=50,
            total_steps=100,
            fixed_atom_mask=features["diffusion_mask"],
        )
        delta = guided - coordinates
        torch.testing.assert_close(delta[:, 0], delta[:, 1])
        self.assertGreater(float(torch.linalg.vector_norm(delta[:, 0])), 0.0)
        torch.testing.assert_close(delta[:, 2:4], torch.zeros_like(delta[:, 2:4]))
        torch.testing.assert_close(delta[:, 4:6], torch.zeros_like(delta[:, 4:6]))
        self.assertLessEqual(float(torch.linalg.vector_norm(delta, dim=-1).max()), 0.200001)
        receipt = json.loads(self.receipt_path.read_text().splitlines()[0])
        self.assertEqual(receipt["step_index"], 50)
        self.assertEqual(receipt["geometry_sha256"], "a" * 64)
        self.assertEqual(receipt["guided_ca_count"], 1)
        self.assertEqual(receipt["schema"], "bms_rfd3_shape_guidance_step_v2")
        self.assertEqual(receipt["guidance_decay"], "constant")
        self.assertEqual(receipt["gradient_scaling"], "raw")
        self.assertEqual(receipt["outside_reduction"], "sum")
        self.assertEqual(receipt["coordinate_state"], "X_L")
        self.assertEqual(receipt["connectivity_weight"], 0.0)
        self.assertNotIn("smoothed_delta_adjacent_rms", receipt)


    def test_source_parity_constant_guidance_is_active_at_first_reverse_step(self) -> None:
        sampler = self.sampler()
        coordinates = torch.tensor([[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0]]])
        features = {
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
            "diffusion_mask": torch.tensor([False, False]),
        }
        guided = sampler.post_step_coordinates(
            X_L=coordinates,
            f=features,
            step_i=0,
            total_steps=100,
            fixed_atom_mask=features["diffusion_mask"],
        )
        self.assertGreater(float(torch.linalg.vector_norm(guided - coordinates)), 0.0)
        self.assertTrue(self.receipt_path.exists())

    def test_guided_X_L_is_consumed_by_the_next_rfd3_reverse_step(self) -> None:
        sampler = self.sampler(step_size=0.01)
        sampler.gamma_0 = 0.0
        sampler.noise_scale = 0.0
        sampler.step_scale = 1.0
        sampler.allow_realignment = False
        initial = torch.tensor([[[1.50, 0.0, 0.0], [1.60, 0.0, 0.0]]])
        sampler._construct_inference_noise_schedule = types.MethodType(
            lambda self, device, partial_t=None: torch.tensor([1.0, 0.5, 0.1], device=device),
            sampler,
        )
        sampler._get_initial_structure = types.MethodType(
            lambda self, **kwargs: initial.clone(),
            sampler,
        )
        guided_states = []
        original_post_step = sampler.post_step_coordinates

        def recording_post_step(self, **kwargs):
            guided = original_post_step(**kwargs)
            guided_states.append(guided.clone())
            return guided

        sampler.post_step_coordinates = types.MethodType(recording_post_step, sampler)

        class IdentityDenoiser:
            def __init__(self):
                self.inputs = []

            def __call__(self, *, X_noisy_L, **kwargs):
                self.inputs.append(X_noisy_L.clone())
                return {"X_L": X_noisy_L.clone()}

        denoiser = IdentityDenoiser()
        features = {
            "ref_element": torch.tensor([0]),
            "is_motif_atom_with_fixed_coord": torch.tensor([False, False]),
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
        }
        with torch.no_grad():
            sampler.sample_diffusion_like_af3(
                f=features,
                diffusion_module=denoiser,
                diffusion_batch_size=1,
                coord_atom_lvl_to_be_noised=torch.zeros_like(initial),
                initializer_outputs={},
                ref_initializer_outputs=None,
                f_ref=None,
            )
        self.assertEqual(len(denoiser.inputs), 2)
        self.assertEqual(len(guided_states), 2)
        torch.testing.assert_close(denoiser.inputs[1], guided_states[0])

    def test_zero_step_size_is_an_explicit_matched_control(self) -> None:
        sampler = self.sampler(step_size=0.0)
        coordinates = torch.randn((1, 2, 3))
        features = {
            "atom_to_token_map": torch.tensor([0, 0]),
            "is_central": torch.tensor([False, True]),
            "is_protein": torch.tensor([True]),
        }
        controlled = sampler.post_step_coordinates(
            X_L=coordinates,
            f=features,
            step_i=50,
            total_steps=100,
            fixed_atom_mask=torch.tensor([False, False]),
        )
        torch.testing.assert_close(controlled, coordinates)
        self.assertFalse(self.receipt_path.exists())


if __name__ == "__main__":
    unittest.main()
