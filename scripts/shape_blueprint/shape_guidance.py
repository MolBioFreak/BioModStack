"""Torch-only Shape Blueprint guidance objective for the pinned RFD3 runtime."""

from __future__ import annotations

from dataclasses import dataclass
import math

import torch


def guidance_scale(
    *,
    step: int,
    total_steps: int,
    start_fraction: float = 0.2,
    end_fraction: float = 0.8,
    terminal_scale: float = 0.1,
) -> float:
    """Ramp Shape guidance to full strength, then taper to a nonzero terminal floor."""
    if total_steps <= 1:
        raise ValueError("total_steps must be greater than one")
    if not (0.0 <= start_fraction < end_fraction <= 1.0):
        raise ValueError("guidance fractions must satisfy 0 <= start < end <= 1")
    if not (0.0 < terminal_scale <= 1.0):
        raise ValueError("terminal_scale must satisfy 0 < terminal_scale <= 1")
    start = int(math.ceil(total_steps * start_fraction))
    end = int(math.floor(total_steps * end_fraction))
    if step < start:
        return 0.0
    if step <= end:
        return float(step - start + 1) / float(max(1, end - start + 1))
    tail_fraction = float(step - end) / float(max(1, total_steps - 1 - end))
    return 1.0 + (float(terminal_scale) - 1.0) * min(1.0, tail_fraction)


@dataclass(frozen=True)
class ShapeGuidanceField:
    points: torch.Tensor
    sdf: torch.Tensor
    origin: torch.Tensor
    spacing: torch.Tensor

    def __post_init__(self) -> None:
        if self.points.ndim != 2 or self.points.shape[-1] != 3 or len(self.points) == 0:
            raise ValueError("points must have shape [P, 3]")
        if self.sdf.ndim != 3 or any(size < 2 for size in self.sdf.shape):
            raise ValueError("sdf must be a three-dimensional grid with each size >= 2")
        if self.origin.shape != (3,) or self.spacing.shape != (3,):
            raise ValueError("origin and spacing must have shape [3]")
        if not bool(torch.isfinite(self.points).all() and torch.isfinite(self.sdf).all()):
            raise ValueError("Shape guidance values must be finite")
        if not bool(torch.isfinite(self.origin).all() and torch.isfinite(self.spacing).all()):
            raise ValueError("Shape guidance grid metadata must be finite")
        if not bool((self.spacing > 0).all()):
            raise ValueError("Shape guidance grid spacing must be positive")

    def _on(self, value: torch.Tensor) -> torch.Tensor:
        return value.to(device=self.points.device, dtype=self.points.dtype)

    def signed_distance(self, coordinates: torch.Tensor) -> torch.Tensor:
        if coordinates.shape[-1] != 3:
            raise ValueError("coordinates must end in xyz")
        coordinates = self._on(coordinates)
        origin = self._on(self.origin)
        spacing = self._on(self.spacing)
        shape = torch.tensor(self.sdf.shape, device=coordinates.device, dtype=coordinates.dtype)
        fractional = (coordinates - origin) / spacing
        clamped = torch.minimum(torch.maximum(fractional, torch.zeros_like(fractional)), shape - 1.0)
        lower = torch.floor(clamped).to(torch.long)
        maximum_lower = torch.tensor(self.sdf.shape, device=coordinates.device) - 2
        lower = torch.minimum(lower, maximum_lower)
        weight = clamped - lower.to(coordinates.dtype)
        upper = lower + 1
        sdf = self._on(self.sdf)

        def sample(ix: torch.Tensor, iy: torch.Tensor, iz: torch.Tensor) -> torch.Tensor:
            return sdf[ix, iy, iz]

        x0, y0, z0 = lower.unbind(dim=-1)
        x1, y1, z1 = upper.unbind(dim=-1)
        wx, wy, wz = weight.unbind(dim=-1)
        c00 = sample(x0, y0, z0) * (1.0 - wx) + sample(x1, y0, z0) * wx
        c01 = sample(x0, y0, z1) * (1.0 - wx) + sample(x1, y0, z1) * wx
        c10 = sample(x0, y1, z0) * (1.0 - wx) + sample(x1, y1, z0) * wx
        c11 = sample(x0, y1, z1) * (1.0 - wx) + sample(x1, y1, z1) * wx
        c0 = c00 * (1.0 - wy) + c10 * wy
        c1 = c01 * (1.0 - wy) + c11 * wy
        sampled = c0 * (1.0 - wz) + c1 * wz
        beyond = torch.linalg.vector_norm((fractional - clamped) * spacing, dim=-1)
        return sampled - beyond

    def loss(
        self,
        coordinates: torch.Tensor,
        *,
        chamfer_weight: float = 1.0,
        outside_weight: float = 1.0,
        connectivity_weight: float = 0.0,
    ) -> dict[str, torch.Tensor]:
        if coordinates.ndim == 2:
            coordinates = coordinates.unsqueeze(0)
        if coordinates.ndim != 3 or coordinates.shape[-1] != 3 or coordinates.shape[1] == 0:
            raise ValueError("coordinates must have shape [B, N, 3]")
        coordinates = self._on(coordinates)
        targets = self.points.unsqueeze(0).expand(coordinates.shape[0], -1, -1)
        distances = torch.cdist(coordinates, targets, p=2).square()
        chamfer = distances.amin(dim=-1).mean() + distances.amin(dim=-2).mean()
        signed = self.signed_distance(coordinates)
        outside = torch.relu(-signed).square().mean()
        adjacent = torch.linalg.vector_norm(coordinates[:, 1:, :] - coordinates[:, :-1, :], dim=-1)
        connectivity = (adjacent - 3.8).square().mean() if coordinates.shape[1] > 1 else coordinates.sum() * 0.0
        total = (
            chamfer * float(chamfer_weight)
            + outside * float(outside_weight)
            + connectivity * float(connectivity_weight)
        )
        return {"total": total, "chamfer": chamfer, "outside": outside, "connectivity": connectivity}

    def project(
        self,
        coordinates: torch.Tensor,
        *,
        step_size: float,
        max_update: float,
        chamfer_weight: float = 1.0,
        outside_weight: float = 1.0,
        connectivity_weight: float = 0.0,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        if step_size <= 0 or max_update <= 0:
            raise ValueError("step_size and max_update must be positive")
        with torch.enable_grad():
            working = self._on(coordinates).detach().requires_grad_(True)
            losses = self.loss(
                working,
                chamfer_weight=chamfer_weight,
                outside_weight=outside_weight,
                connectivity_weight=connectivity_weight,
            )
            gradient = torch.autograd.grad(
                losses["total"], working, create_graph=False
            )[0]
        # Mean-reduced Chamfer/SDF gradients shrink with residue and point count.
        # Normalize per diffusion sample so step_size remains an RMS Angstrom
        # displacement per guided residue rather than vanishing on real jobs.
        atom_gradient_norm = torch.linalg.vector_norm(gradient, dim=-1)
        gradient_rms = torch.sqrt(atom_gradient_norm.square().mean(dim=-1, keepdim=True))
        gradient_rms = torch.clamp(gradient_rms, min=1e-12).unsqueeze(-1)
        delta = -float(step_size) * gradient / gradient_rms
        norms = torch.linalg.vector_norm(delta, dim=-1, keepdim=True)
        scale = torch.clamp(float(max_update) / torch.clamp(norms, min=1e-12), max=1.0)
        delta = delta * scale
        projected = (working + delta).detach()
        receipt = {
            "loss": float(losses["total"].detach().cpu()),
            "chamfer": float(losses["chamfer"].detach().cpu()),
            "outside": float(losses["outside"].detach().cpu()),
            "connectivity": float(losses["connectivity"].detach().cpu()),
            "gradient_norm": float(torch.linalg.vector_norm(gradient).detach().cpu()),
            "applied_update_norm": float(torch.linalg.vector_norm(delta).detach().cpu()),
            "rms_atom_update": float(
                torch.sqrt(torch.linalg.vector_norm(delta, dim=-1).square().mean()).detach().cpu()
            ),
            "max_atom_update": float(torch.linalg.vector_norm(delta, dim=-1).max().detach().cpu()),
        }
        return projected, receipt
