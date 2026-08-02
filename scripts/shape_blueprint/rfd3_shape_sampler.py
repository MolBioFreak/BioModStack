"""Version-pinned Shape guidance sampler extension for RFD3 0.1.9."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F

from rfd3.model.inference_sampler import SampleDiffusionWithMotif
from shape_guidance import ShapeGuidanceField, guidance_scale


RFD3_BASE_SAMPLER_SHA256 = "e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read_regular(path_value: str, *, label: str) -> bytes:
    path = Path(path_value)
    stat = path.lstat()
    if path.is_symlink() or not path.is_file() or stat.st_nlink != 1:
        raise ValueError(f"{label} must be a regular, single-link staged input")
    return path.read_bytes()


@dataclass(kw_only=True)
class ShapeGuidedDiffusionSampler(SampleDiffusionWithMotif):
    shape_manifest_path: str = ""
    shape_points_path: str = ""
    shape_sdf_path: str = ""
    shape_expected_geometry_sha256: str = ""
    shape_expected_point_pool_sha256: str = ""
    shape_receipt_path: str = "shape_guidance_steps.jsonl"
    shape_step_size: float = 0.5
    shape_max_update: float = 0.2
    shape_chamfer_weight: float = 1.0
    shape_outside_weight: float = 1.0
    shape_connectivity_weight: float = 5.0
    shape_start_fraction: float = 0.2
    shape_end_fraction: float = 0.8
    shape_terminal_scale: float = 1.0
    _shape_fields: dict[str, ShapeGuidanceField] = field(default_factory=dict, init=False, repr=False)
    _shape_manifest: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.shape_manifest_path or not self.shape_points_path or not self.shape_sdf_path:
            raise ValueError("Shape sampler requires manifest, point-pool, and SDF paths")
        if self.shape_step_size < 0 or self.shape_max_update <= 0:
            raise ValueError("Shape guidance step size must be non-negative and max update positive")
        if self.shape_chamfer_weight < 0 or self.shape_outside_weight < 0 or self.shape_connectivity_weight < 0:
            raise ValueError("Shape guidance weights must be non-negative")
        if self.shape_chamfer_weight == 0 and self.shape_outside_weight == 0:
            raise ValueError("at least one Shape guidance weight must be positive")
        if not 0.0 < self.shape_terminal_scale <= 1.0:
            raise ValueError("Shape terminal guidance scale must be in (0, 1]")

    def load_field(self, device: torch.device) -> ShapeGuidanceField:
        cache_key = str(device)
        if cache_key in self._shape_fields:
            return self._shape_fields[cache_key]
        manifest_bytes = _read_regular(self.shape_manifest_path, label="geometry manifest")
        manifest = json.loads(manifest_bytes)
        if manifest.get("geometry_sha256") != self.shape_expected_geometry_sha256:
            raise ValueError("geometry hash does not match the expected Shape identity")
        point_bytes = _read_regular(self.shape_points_path, label="point pool")
        point_hash = hashlib.sha256(point_bytes).hexdigest()
        if point_hash != manifest.get("point_pool_sha256") or point_hash != self.shape_expected_point_pool_sha256:
            raise ValueError("point-pool hash does not match the expected Shape identity")
        sdf_bytes = _read_regular(self.shape_sdf_path, label="signed-distance grid")
        sdf_hash = hashlib.sha256(sdf_bytes).hexdigest()
        if sdf_hash != manifest.get("sdf_sha256"):
            raise ValueError("SDF hash does not match the canonical geometry manifest")
        point_count = int(manifest["point_count"])
        sdf_shape = tuple(int(value) for value in manifest["sdf_grid_shape"])
        if len(point_bytes) != point_count * 3 * 4:
            raise ValueError("point-pool byte length does not match its manifest")
        if len(sdf_shape) != 3 or any(value < 2 for value in sdf_shape):
            raise ValueError("SDF grid shape is invalid")
        if len(sdf_bytes) != int(np.prod(sdf_shape)) * 4:
            raise ValueError("SDF byte length does not match its manifest")
        points = torch.from_numpy(np.frombuffer(point_bytes, dtype="<f4").reshape((point_count, 3)).copy()).to(device)
        sdf = torch.from_numpy(np.frombuffer(sdf_bytes, dtype="<f4").reshape(sdf_shape).copy()).to(device)
        shape_field = ShapeGuidanceField(
            points=points,
            sdf=sdf,
            origin=torch.tensor(manifest["sdf_origin_angstrom"], dtype=torch.float32, device=device),
            spacing=torch.tensor(manifest["sdf_spacing_angstrom"], dtype=torch.float32, device=device),
        )
        self._shape_fields[cache_key] = shape_field
        self._shape_manifest = manifest
        return shape_field

    @staticmethod
    def _smooth_ca_delta(delta: torch.Tensor) -> torch.Tensor:
        if delta.shape[1] < 2:
            return delta
        channels = delta.transpose(1, 2)
        padded = F.pad(channels, (2, 2), mode="replicate")
        kernel = delta.new_tensor([1.0, 4.0, 6.0, 4.0, 1.0]).div_(16.0)
        weight = kernel.view(1, 1, 5).expand(3, 1, 5)
        return F.conv1d(padded, weight, groups=3).transpose(1, 2).to(dtype=delta.dtype)

    @staticmethod
    def _atom_mask(value: torch.Tensor, atom_count: int) -> torch.Tensor:
        mask = value.bool()
        if mask.ndim > 1:
            mask = mask[0]
        if mask.shape != (atom_count,):
            raise ValueError("RFD3 Shape sampler received an invalid atom-level mask")
        return mask

    def post_step_coordinates(
        self,
        *,
        X_L: torch.Tensor,
        f: dict[str, Any],
        step_i: int,
        total_steps: int,
        fixed_atom_mask: torch.Tensor,
    ) -> torch.Tensor:
        if self.shape_step_size == 0:
            return X_L
        scale = guidance_scale(
            step=step_i,
            total_steps=total_steps,
            start_fraction=self.shape_start_fraction,
            end_fraction=self.shape_end_fraction,
            terminal_scale=self.shape_terminal_scale,
        )
        if scale == 0.0:
            return X_L
        if X_L.ndim != 3 or X_L.shape[-1] != 3:
            raise ValueError("RFD3 Shape sampler expected coordinates with shape [D, L, 3]")
        atom_count = X_L.shape[1]
        token_map = f["atom_to_token_map"].long()
        if token_map.ndim > 1:
            token_map = token_map[0]
        if token_map.shape != (atom_count,):
            raise ValueError("RFD3 Shape sampler received an invalid atom-to-token map")
        is_central = self._atom_mask(f["is_central"], atom_count)
        fixed = self._atom_mask(fixed_atom_mask, atom_count)
        is_protein_token = f["is_protein"].bool()
        if is_protein_token.ndim > 1:
            is_protein_token = is_protein_token[0]
        if token_map.max().item() >= len(is_protein_token):
            raise ValueError("RFD3 Shape sampler token map exceeds protein annotations")
        protein_atoms = is_protein_token[token_map]
        guided_ca = is_central & protein_atoms & ~fixed
        if not bool(guided_ca.any()):
            raise ValueError("RFD3 Shape guidance found no diffused protein central atoms")

        shape_field = self.load_field(X_L.device)
        ca_coordinates = X_L[:, guided_ca, :]
        projected_ca, receipt = shape_field.project(
            ca_coordinates,
            step_size=self.shape_step_size * scale,
            max_update=self.shape_max_update * scale,
            chamfer_weight=self.shape_chamfer_weight,
            outside_weight=self.shape_outside_weight,
            connectivity_weight=self.shape_connectivity_weight,
        )
        raw_ca_delta = projected_ca - ca_coordinates
        ca_delta = self._smooth_ca_delta(raw_ca_delta)
        token_count = int(token_map.max().item()) + 1
        token_delta = torch.zeros(
            (X_L.shape[0], token_count, 3), dtype=X_L.dtype, device=X_L.device
        )
        guided_tokens = token_map[guided_ca]
        if len(torch.unique(guided_tokens)) != len(guided_tokens):
            raise ValueError("RFD3 Shape sampler found multiple central atoms for one token")
        token_delta[:, guided_tokens, :] = ca_delta
        eligible_atoms = protein_atoms & ~fixed
        atom_delta = token_delta[:, token_map, :] * eligible_atoms[None, :, None]
        guided = X_L + atom_delta
        if not bool(torch.equal(guided[:, fixed, :], X_L[:, fixed, :])):
            raise RuntimeError("Shape guidance altered fixed coordinates")
        if not bool(torch.isfinite(guided).all()):
            raise RuntimeError("Shape guidance produced non-finite coordinates")

        manifest = self._shape_manifest or {}
        step_receipt = {
            "schema": "bms_rfd3_shape_guidance_step_v1",
            "base_sampler_sha256": RFD3_BASE_SAMPLER_SHA256,
            "geometry_sha256": manifest.get("geometry_sha256"),
            "point_pool_sha256": manifest.get("point_pool_sha256"),
            "sdf_sha256": manifest.get("sdf_sha256"),
            "step_index": int(step_i),
            "total_steps": int(total_steps),
            "schedule_scale": float(scale),
            "guided_ca_count": int(guided_ca.sum().item()),
            "raw_delta_adjacent_rms": float(torch.sqrt(torch.diff(raw_ca_delta, dim=1).square().sum(dim=-1).mean()).cpu()) if raw_ca_delta.shape[1] > 1 else 0.0,
            "smoothed_delta_adjacent_rms": float(torch.sqrt(torch.diff(ca_delta, dim=1).square().sum(dim=-1).mean()).cpu()) if ca_delta.shape[1] > 1 else 0.0,
            **receipt,
        }
        receipt_path = Path(self.shape_receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with receipt_path.open("ab") as handle:
            handle.write(_canonical(step_receipt) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return guided
