from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "shape_blueprint" / "evaluate_rfd3_initial_candidate.py"


def _module():
    assert MODULE_PATH.is_file(), "initial RFD3 admission module is absent"
    spec = importlib.util.spec_from_file_location("evaluate_rfd3_initial_candidate", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cif_text(*, ca2_x: float = 4.2, include_oxygen: bool = True, chain: str = "A") -> str:
    atoms = [
        ("ATOM", 1, "N", "ALA", chain, 1, -1.46, 0.0, 0.0, "N"),
        ("ATOM", 2, "CA", "ALA", chain, 1, 0.0, 0.0, 0.0, "C"),
        ("ATOM", 3, "C", "ALA", chain, 1, 1.52, 0.0, 0.0, "C"),
    ]
    if include_oxygen:
        atoms.append(("ATOM", 4, "O", "ALA", chain, 1, 1.52, 1.24, 0.0, "O"))
    offset = len(atoms)
    atoms += [
        ("ATOM", offset + 1, "N", "GLY", chain, 2, ca2_x - 1.46, 0.0, 0.0, "N"),
        ("ATOM", offset + 2, "CA", "GLY", chain, 2, ca2_x, 0.0, 0.0, "C"),
        ("ATOM", offset + 3, "C", "GLY", chain, 2, ca2_x + 1.52, 0.0, 0.0, "C"),
    ]
    if include_oxygen:
        atoms.append(("ATOM", offset + 4, "O", "GLY", chain, 2, ca2_x + 1.52, 1.24, 0.0, "O"))
    lines = [
        "data_candidate",
        "#",
        "loop_",
        "_atom_site.group_PDB",
        "_atom_site.id",
        "_atom_site.type_symbol",
        "_atom_site.label_atom_id",
        "_atom_site.label_comp_id",
        "_atom_site.label_asym_id",
        "_atom_site.label_seq_id",
        "_atom_site.Cartn_x",
        "_atom_site.Cartn_y",
        "_atom_site.Cartn_z",
        "_atom_site.occupancy",
        "_atom_site.B_iso_or_equiv",
    ]
    lines.extend(
        f"{group} {atom_id} {element} {atom_name} {residue} {chain_id} {residue_id} {x:.3f} {y:.3f} {z:.3f} 1.00 20.00"
        for group, atom_id, atom_name, residue, chain_id, residue_id, x, y, z, element in atoms
    )
    lines.append("#")
    return "\n".join(lines) + "\n"


def _write_bundle(root: Path, *, candidate_text: str, points: np.ndarray | None = None) -> tuple[Path, Path, Path, Path]:
    candidate = root / "candidate.cif.gz"
    with gzip.open(candidate, "wt", encoding="utf-8") as handle:
        handle.write(candidate_text)
    points = points if points is not None else np.asarray([[0.0, 0.0, 0.0], [3.8, 0.0, 0.0]], dtype="<f4")
    point_bytes = points.tobytes()
    sdf = np.ones((3, 3, 3), dtype="<f4")
    sdf_bytes = sdf.tobytes()
    points_path = root / "points.f32le"
    sdf_path = root / "sdf.f32le"
    points_path.write_bytes(point_bytes)
    sdf_path.write_bytes(sdf_bytes)
    manifest = {
        "schema": "bms_shape_canonical_geometry_v1",
        "geometry_sha256": "a" * 64,
        "point_pool_sha256": hashlib.sha256(point_bytes).hexdigest(),
        "point_count": len(points),
        "sdf_sha256": hashlib.sha256(sdf_bytes).hexdigest(),
        "sdf_sign": "positive_inside",
        "sdf_grid_shape": [3, 3, 3],
        "sdf_origin_angstrom": [-10.0, -10.0, -10.0],
        "sdf_spacing_angstrom": [10.0, 10.0, 10.0],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    request = {
        "schema": "bms_shape_design_request_v2",
        "geometry_sha256": "a" * 64,
        "point_pool_sha256": manifest["point_pool_sha256"],
        "sdf_sha256": manifest["sdf_sha256"],
        "length_policy": {"mode": "fixed", "min": 2, "max": 2},
        "num_backbones": 1,
        "seed": 17,
        "generator": "rfd3",
    }
    request["request_sha256"] = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request, sort_keys=True))
    return candidate, request_path, manifest_path, root / "admission.json"


class InitialAdmissionTests(unittest.TestCase):
    def test_valid_native_candidate_is_accepted_with_complete_metrics(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            candidate, request, manifest, output = _write_bundle(Path(directory), candidate_text=_cif_text())
            result = module.admit_initial_candidate(
                candidate_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=output,
            )
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["reason"], None)
            self.assertEqual(result["counts"]["residue_count"], 2)
            self.assertEqual(result["counts"]["ca_count"], 2)
            self.assertEqual(result["metrics"]["chainbreak_count"], 0)
            self.assertEqual(result["metrics"]["backbone_incomplete_residue_count"], 0)
            self.assertEqual(result["metrics"]["backbone_clash_count"], 0)
            self.assertEqual(result["metrics"]["sidechain_clash_count"], 0)
            self.assertTrue(output.is_file())

    def test_missing_backbone_is_rejected_with_deterministic_reason(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            candidate, request, manifest, output = _write_bundle(
                Path(directory), candidate_text=_cif_text(include_oxygen=False)
            )
            result = module.admit_initial_candidate(
                candidate_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=output,
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"]["code"], "backbone_incomplete")
            self.assertEqual(result["reason"]["residue_ids"], ["A:1", "A:2"])

    def test_chainbreak_is_rejected_before_any_downstream_stage(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            candidate, request, manifest, output = _write_bundle(
                Path(directory), candidate_text=_cif_text(ca2_x=8.0)
            )
            result = module.admit_initial_candidate(
                candidate_path=candidate,
                request_path=request,
                manifest_path=manifest,
                output_path=output,
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"]["code"], "chainbreaks_present")
            self.assertGreater(result["metrics"]["chainbreak_count"], 0)

    def test_uncompressed_structure_is_rejected_fail_closed(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, request, manifest, output = _write_bundle(root, candidate_text=_cif_text())
            plain = root / "candidate.cif"
            plain.write_text(gzip.open(candidate, "rt", encoding="utf-8").read())
            with self.assertRaisesRegex(ValueError, "compressed mmCIF"):
                module.admit_initial_candidate(
                    candidate_path=plain,
                    request_path=request,
                    manifest_path=manifest,
                    output_path=output,
                )


if __name__ == "__main__":
    unittest.main()
