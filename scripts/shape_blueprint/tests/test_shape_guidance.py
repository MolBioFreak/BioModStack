from __future__ import annotations

import sys
from pathlib import Path
import unittest

import torch

sys.path.insert(0, str(Path(__file__).parents[1]))

from shape_guidance import ShapeGuidanceField, guidance_scale  # noqa: E402


class ShapeGuidanceTests(unittest.TestCase):
    @staticmethod
    def field() -> ShapeGuidanceField:
        axis = torch.linspace(-2.0, 2.0, 9)
        xyz = torch.stack(torch.meshgrid(axis, axis, axis, indexing="ij"), dim=-1)
        sdf = 1.0 - xyz.abs().amax(dim=-1)
        points = torch.tensor(
            [
                [-0.75, 0.0, 0.0],
                [-0.25, 0.0, 0.0],
                [0.25, 0.0, 0.0],
                [0.75, 0.0, 0.0],
            ],
            dtype=torch.float32,
        )
        return ShapeGuidanceField(
            points=points,
            sdf=sdf,
            origin=torch.tensor([-2.0, -2.0, -2.0]),
            spacing=torch.tensor([0.5, 0.5, 0.5]),
        )

    def test_signed_distance_is_positive_inside_and_negative_outside_grid(self) -> None:
        field = self.field()
        values = field.signed_distance(
            torch.tensor([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]])
        )
        torch.testing.assert_close(values, torch.tensor([1.0, -0.5, -2.0]), atol=1e-5, rtol=0.0)

    def test_shape_loss_penalizes_shifted_outside_coordinates(self) -> None:
        field = self.field()
        matching = field.points.unsqueeze(0)
        shifted = matching + torch.tensor([3.0, 0.0, 0.0])
        inside = field.loss(matching)
        outside = field.loss(shifted)
        self.assertLess(float(inside["total"]), 1e-7)
        self.assertGreater(float(outside["total"]), float(inside["total"]) + 1.0)
        self.assertGreater(float(outside["outside"]), 0.0)

    def test_projection_reduces_loss_and_clips_per_atom_update(self) -> None:
        field = self.field()
        coordinates = (field.points + torch.tensor([2.25, 0.0, 0.0])).unsqueeze(0)
        before = field.loss(coordinates)["total"]
        with torch.no_grad():
            projected, receipt = field.project(coordinates, step_size=0.5, max_update=0.2)
        after = field.loss(projected)["total"]
        update_norm = torch.linalg.vector_norm(projected - coordinates, dim=-1)
        self.assertLess(float(after), float(before))
        self.assertLessEqual(float(update_norm.max()), 0.200001)
        self.assertGreater(receipt["gradient_norm"], 0.0)
        self.assertGreater(receipt["applied_update_norm"], 0.0)


    def test_source_parity_outside_loss_is_summed_not_residue_mean_reduced(self) -> None:
        field = self.field()
        one = torch.tensor([[[3.0, 0.0, 0.0]]], dtype=torch.float32)
        four = one.expand(1, 4, 3).clone()
        one_loss = field.loss(one, chamfer_weight=0.0, outside_weight=1.0)["outside"]
        four_loss = field.loss(four, chamfer_weight=0.0, outside_weight=1.0)["outside"]
        torch.testing.assert_close(four_loss, one_loss * 4.0)

    def test_source_parity_projection_uses_raw_gradient_magnitude(self) -> None:
        field = self.field()
        near = torch.tensor([[[3.0, 0.0, 0.0]]], dtype=torch.float32)
        far = torch.tensor([[[5.0, 0.0, 0.0]]], dtype=torch.float32)
        near_projected, _ = field.project(
            near,
            step_size=0.01,
            max_update=10.0,
            chamfer_weight=0.0,
            outside_weight=1.0,
        )
        far_projected, _ = field.project(
            far,
            step_size=0.01,
            max_update=10.0,
            chamfer_weight=0.0,
            outside_weight=1.0,
        )
        near_update = torch.linalg.vector_norm(near_projected - near)
        far_update = torch.linalg.vector_norm(far_projected - far)
        self.assertGreater(float(far_update), float(near_update) * 1.5)

    def test_connectivity_guidance_repairs_broken_ca_spacing(self) -> None:
        field = self.field()
        coordinates = torch.tensor([[[0.0, 0.0, 0.0], [12.0, 0.0, 0.0], [24.0, 0.0, 0.0]]])
        before = field.loss(coordinates, connectivity_weight=5.0)["connectivity"]
        projected, receipt = field.project(
            coordinates,
            step_size=0.2,
            max_update=0.5,
            chamfer_weight=0.0,
            outside_weight=0.0,
            connectivity_weight=5.0,
        )
        after = field.loss(projected, connectivity_weight=5.0)["connectivity"]
        self.assertLess(float(after), float(before))
        self.assertGreater(receipt["connectivity"], 0.0)

    def test_source_parity_guidance_scale_is_constant_at_every_reverse_step(self) -> None:
        scales = [guidance_scale(step=step, total_steps=100) for step in range(100)]
        self.assertEqual(scales, [1.0] * 100)


if __name__ == "__main__":
    unittest.main()
