#!/usr/bin/env python3
"""Fail-closed admission of native RFD3 all-atom candidates before sequence design."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
from typing import Any, Iterable

import numpy as np


ADMISSION_PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "shape_blueprint" / "initial_admission_profiles.json"
AMINO_ACIDS = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}
BACKBONE_ATOMS = {"N", "CA", "C", "O"}


class AdmissionReject(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class AtomRecord:
    group: str
    atom_id: str
    atom_name: str
    element: str
    residue_name: str
    chain_id: str
    residue_number: int
    insertion_code: str
    coord: tuple[float, float, float]

    @property
    def residue_key(self) -> tuple[str, int, str]:
        return self.chain_id, self.residue_number, self.insertion_code


@dataclass(frozen=True)
class ResidueRecord:
    key: tuple[str, int, str]
    residue_name: str
    atoms: tuple[AtomRecord, ...]



def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{label} is absent, linked, or not a regular single-link file")
    return path


def _json(path: Path, label: str) -> dict[str, Any]:
    _regular(path, label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def _load_profile() -> dict[str, Any]:
    registry = _json(ADMISSION_PROFILE_PATH, "initial admission profile registry")
    if registry.get("schema") != "bms_rfd3_initial_admission_registry_v1":
        raise ValueError("initial admission profile registry schema is invalid")
    profiles = registry.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get("rfd3_initial_admission_v1"), dict):
        raise ValueError("initial admission profile is missing")
    profile = dict(profiles["rfd3_initial_admission_v1"])
    if profile.get("status") != "active":
        raise ValueError("initial admission profile is not active")
    return profile


def _read_cif_text(path: Path) -> str:
    _regular(path, "candidate structure")
    if not path.name.endswith(".cif.gz"):
        raise ValueError("initial RFD3 admission requires a compressed mmCIF (.cif.gz) candidate")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise ValueError("candidate compressed mmCIF cannot be read") from exc


def _parse_int(value: str, label: str) -> int:
    if value in {"?", "."}:
        raise ValueError(f"candidate mmCIF has missing {label}")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"candidate mmCIF has invalid {label}: {value}") from exc


def _parse_float(value: str, label: str) -> float:
    if value in {"?", "."}:
        raise ValueError(f"candidate mmCIF has missing {label}")
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"candidate mmCIF has invalid {label}: {value}") from exc
    if not math.isfinite(number):
        raise ValueError(f"candidate mmCIF has non-finite {label}")
    return number


def _row_value(row: dict[str, str], *names: str, default: str | None = None) -> str:
    for name in names:
        if name in row:
            return row[name]
    if default is not None:
        return default
    raise ValueError(f"candidate mmCIF is missing required field {names[0]}")


def _atom_rows(text: str) -> Iterable[dict[str, str]]:
    lines = text.splitlines()
    index = 0
    found_atom_loop = False
    while index < len(lines):
        if lines[index].strip().lower() != "loop_":
            index += 1
            continue
        index += 1
        headers: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("_"):
            headers.append(lines[index].strip().split()[0])
            index += 1
        if not headers:
            raise ValueError("candidate mmCIF loop has no headers")
        values: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or stripped == "#" or stripped.lower() == "loop_" or stripped.lower().startswith("data_") or stripped.startswith("_"):
                break
            if stripped.startswith(";"):
                raise ValueError("candidate mmCIF semicolon text fields are unsupported in atom_site loop")
            values.extend(shlex.split(stripped, comments=False))
            index += 1
        if any(header.startswith("_atom_site.") for header in headers):
            found_atom_loop = True
            width = len(headers)
            if len(values) % width:
                raise ValueError("candidate mmCIF atom_site loop has an incomplete row")
            for start in range(0, len(values), width):
                row = dict(zip(headers, values[start : start + width], strict=True))
                yield row
        # The boundary line is processed by the outer loop on the next pass.
    if not found_atom_loop:
        raise ValueError("candidate mmCIF has no atom_site loop")


def _parse_atoms(text: str) -> list[AtomRecord]:
    atoms: list[AtomRecord] = []
    for row in _atom_rows(text):
        group = _row_value(row, "_atom_site.group_PDB", default="ATOM").upper()
        if group not in {"ATOM", "HETATM"}:
            continue
        comp = _row_value(row, "_atom_site.label_comp_id", "_atom_site.auth_comp_id")
        if group != "ATOM":
            if comp.upper() not in {"HOH", "WAT"}:
                raise AdmissionReject("hetero_atoms_present", "candidate contains non-water hetero atoms")
            continue
        chain = _row_value(row, "_atom_site.auth_asym_id", "_atom_site.label_asym_id")
        if chain in {"?", "."}:
            raise ValueError("candidate mmCIF has an unassigned protein chain")
        residue_number = _parse_int(
            _row_value(row, "_atom_site.auth_seq_id", "_atom_site.label_seq_id"),
            "residue number",
        )
        insertion = _row_value(row, "_atom_site.pdbx_PDB_ins_code", default="")
        if insertion in {"?", "."}:
            insertion = ""
        atom_name = _row_value(row, "_atom_site.label_atom_id", "_atom_site.auth_atom_id").upper()
        element = _row_value(row, "_atom_site.type_symbol", default=atom_name[0]).upper()
        atoms.append(
            AtomRecord(
                group=group,
                atom_id=_row_value(row, "_atom_site.id"),
                atom_name=atom_name,
                element=element,
                residue_name=comp.upper(),
                chain_id=chain,
                residue_number=residue_number,
                insertion_code=insertion,
                coord=(
                    _parse_float(_row_value(row, "_atom_site.Cartn_x"), "Cartn_x"),
                    _parse_float(_row_value(row, "_atom_site.Cartn_y"), "Cartn_y"),
                    _parse_float(_row_value(row, "_atom_site.Cartn_z"), "Cartn_z"),
                ),
            )
        )
    if not atoms:
        raise ValueError("candidate mmCIF has no protein atom_site rows")
    return atoms


def _residues(atoms: list[AtomRecord]) -> list[ResidueRecord]:
    grouped: dict[tuple[str, int, str], list[AtomRecord]] = {}
    names: dict[tuple[str, int, str], str] = {}
    for atom in atoms:
        grouped.setdefault(atom.residue_key, []).append(atom)
        names.setdefault(atom.residue_key, atom.residue_name)
    ordered_keys = sorted(grouped, key=lambda key: (key[0], key[1], key[2]))
    return [ResidueRecord(key, names[key], tuple(grouped[key])) for key in ordered_keys]


def _atom_map(residue: ResidueRecord) -> dict[str, AtomRecord]:
    result: dict[str, AtomRecord] = {}
    for atom in residue.atoms:
        result.setdefault(atom.atom_name, atom)
    return result


def _distance(first: AtomRecord, second: AtomRecord) -> float:
    return float(np.linalg.norm(np.asarray(first.coord) - np.asarray(second.coord)))


def _range_violation(value: float, bounds: list[float]) -> bool:
    return not bounds[0] <= value <= bounds[1]


def _cad_metrics(ca: np.ndarray, points: np.ndarray, sdf: np.ndarray, manifest: dict[str, Any]) -> dict[str, float]:
    if not np.isfinite(ca).all() or not np.isfinite(points).all() or not np.isfinite(sdf).all():
        raise AdmissionReject("nonfinite_geometry", "CAD inputs or candidate coordinates are non-finite")
    pairwise = np.linalg.norm(ca[:, None, :] - points[None, :, :], axis=2)
    ca_to_point = pairwise.min(axis=1)
    point_to_ca = pairwise.min(axis=0)
    origin = np.asarray(manifest["sdf_origin_angstrom"], dtype=np.float64)
    spacing = np.asarray(manifest["sdf_spacing_angstrom"], dtype=np.float64)
    grid_shape = tuple(int(value) for value in manifest["sdf_grid_shape"])
    fractional = (ca - origin) / spacing
    if np.any(fractional < 0.0) or any(np.any(fractional[:, axis] > grid_shape[axis] - 1) for axis in range(3)):
        raise AdmissionReject("sdf_query_out_of_bounds", "candidate Cα coordinates fall outside the canonical SDF grid")
    lower = np.floor(fractional).astype(int)
    upper = np.minimum(lower + 1, np.asarray(grid_shape) - 1)
    weight = fractional - lower
    values = np.zeros(len(ca), dtype=np.float64)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                indices = (
                    np.where(dx, upper[:, 0], lower[:, 0]),
                    np.where(dy, upper[:, 1], lower[:, 1]),
                    np.where(dz, upper[:, 2], lower[:, 2]),
                )
                coefficient = (
                    np.where(dx, weight[:, 0], 1.0 - weight[:, 0])
                    * np.where(dy, weight[:, 1], 1.0 - weight[:, 1])
                    * np.where(dz, weight[:, 2], 1.0 - weight[:, 2])
                )
                values += coefficient * sdf[indices]
    return {
        "cad_ca_to_point_mean_angstrom": float(ca_to_point.mean()),
        "cad_ca_to_point_max_angstrom": float(ca_to_point.max()),
        "cad_point_to_ca_mean_angstrom": float(point_to_ca.mean()),
        "cad_point_to_ca_max_angstrom": float(point_to_ca.max()),
        "cad_bidirectional_mean_angstrom": float((ca_to_point.mean() + point_to_ca.mean()) / 2.0),
        "cad_sdf_outside_fraction": float(np.mean(values < 0.0)),
        "cad_sdf_min": float(values.min()),
        "cad_sdf_mean": float(values.mean()),
    }


def _clash_metrics(residues: list[ResidueRecord], profile: dict[str, Any]) -> tuple[int, int]:
    atoms = [atom for residue in residues for atom in residue.atoms if atom.element != "H"]
    coords = np.asarray([atom.coord for atom in atoms], dtype=np.float64)
    residue_index = {id(atom): index for index, residue in enumerate(residues) for atom in residue.atoms}
    backbone_clashes = 0
    sidechain_clashes = 0
    backbone_limit = float(profile["backbone_clash_distance_angstrom"])
    sidechain_limit = float(profile["sidechain_clash_distance_angstrom"])
    for index, first in enumerate(atoms):
        distances = np.linalg.norm(coords[index + 1 :] - coords[index], axis=1)
        for offset, distance in enumerate(distances, start=index + 1):
            second = atoms[offset]
            first_residue = residue_index[id(first)]
            second_residue = residue_index[id(second)]
            if first_residue == second_residue:
                continue
            if first.atom_name in BACKBONE_ATOMS and second.atom_name in BACKBONE_ATOMS:
                if abs(second_residue - first_residue) <= 1:
                    continue
                if distance < backbone_limit:
                    backbone_clashes += 1
            elif distance < sidechain_limit:
                sidechain_clashes += 1
    return backbone_clashes, sidechain_clashes


def _reject_result(
    *,
    candidate_path: Path,
    candidate_sha256: str,
    request: dict[str, Any],
    manifest: dict[str, Any],
    profile: dict[str, Any],
    reason: AdmissionReject,
    counts: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "bms_rfd3_initial_admission_v1",
        "status": "rejected",
        "candidate_filename": candidate_path.name,
        "candidate_id": candidate_path.name[:-7] if candidate_path.name.endswith(".cif.gz") else candidate_path.stem,
        "candidate_sha256": candidate_sha256,
        "request_sha256": request.get("request_sha256"),
        "geometry_sha256": request.get("geometry_sha256"),
        "point_pool_sha256": request.get("point_pool_sha256"),
        "sdf_sha256": manifest.get("sdf_sha256"),
        "admission_profile": profile,
        "counts": counts or {},
        "metrics": metrics or {},
        "reason": {"code": reason.code, "message": reason.message, **reason.details},
    }


def _write_result(output_path: Path, result: dict[str, Any]) -> None:
    output_path = output_path.resolve()
    if output_path.is_symlink():
        raise ValueError("initial admission output must not be a symlink")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(result) + b"\n")
    os.replace(temporary, output_path)


def _validate_request_manifest(request: dict[str, Any], manifest: dict[str, Any], points_path: Path, sdf_path: Path | None) -> None:
    if request.get("schema") != "bms_shape_design_request_v2":
        raise ValueError("initial admission requires Shape request schema v2")
    if request.get("generator") != "rfd3":
        raise ValueError("initial admission requires generator=rfd3")
    claimed = request.get("request_sha256")
    unsigned = dict(request)
    unsigned.pop("request_sha256", None)
    if not isinstance(claimed, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != claimed:
        raise ValueError("initial admission request hash mismatch")
    if request.get("geometry_sha256") != manifest.get("geometry_sha256"):
        raise ValueError("initial admission geometry identity mismatch")
    if request.get("point_pool_sha256") != _sha256(points_path):
        raise ValueError("initial admission point-pool hash mismatch")
    if manifest.get("point_pool_sha256") != request.get("point_pool_sha256"):
        raise ValueError("initial admission manifest point-pool hash mismatch")
    if sdf_path is not None and manifest.get("sdf_sha256") != _sha256(sdf_path):
        raise ValueError("initial admission SDF hash mismatch")
    if manifest.get("sdf_sign") != "positive_inside":
        raise ValueError("initial admission requires positive_inside SDF")
    if not isinstance(manifest.get("sdf_grid_shape"), list) or len(manifest["sdf_grid_shape"]) != 3:
        raise ValueError("initial admission SDF grid shape is invalid")
    length_policy = request.get("length_policy")
    if not isinstance(length_policy, dict) or length_policy.get("mode") not in {"fixed", "uniform_integer_range"}:
        raise ValueError("initial admission length policy is invalid")
    minimum, maximum = length_policy.get("min"), length_policy.get("max")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum > maximum:
        raise ValueError("initial admission length policy bounds are invalid")
    if length_policy["mode"] == "fixed" and minimum != maximum:
        raise ValueError("initial admission fixed length policy is not fixed")


def admit_initial_candidate(
    *,
    candidate_path: Path,
    request_path: Path,
    manifest_path: Path,
    output_path: Path,
    points_path: Path | None = None,
    sdf_path: Path | None = None,
) -> dict[str, Any]:
    candidate_path = _regular(candidate_path, "candidate structure")
    if not candidate_path.name.endswith(".cif.gz"):
        raise ValueError("initial RFD3 admission requires a compressed mmCIF (.cif.gz) candidate")
    request = _json(request_path, "Shape request")
    manifest = _json(manifest_path, "Shape geometry manifest")
    profile = _load_profile()
    points_path = _regular(points_path or manifest_path.with_name("points.f32le"), "canonical point pool")
    sdf_path = _regular(sdf_path or manifest_path.with_name("sdf.f32le"), "canonical SDF")
    _validate_request_manifest(request, manifest, points_path, sdf_path)
    candidate_sha256 = _sha256(candidate_path)
    try:
        atoms = _parse_atoms(_read_cif_text(candidate_path))
    except AdmissionReject as rejected:
        result = _reject_result(
            candidate_path=candidate_path,
            candidate_sha256=candidate_sha256,
            request=request,
            manifest=manifest,
            profile=profile,
            reason=rejected,
        )
        _write_result(output_path, result)
        return result
    residues = _residues(atoms)
    chains = sorted({residue.key[0] for residue in residues})
    length_policy = request["length_policy"]
    minimum, maximum = int(length_policy["min"]), int(length_policy["max"])
    residue_ids = [f"{chain}:{number}{insertion}" for chain, number, insertion in (residue.key for residue in residues)]
    counts = {
        "chain_count": len(chains),
        "residue_count": len(residues),
        "ca_count": sum(1 for atom in atoms if atom.atom_name == "CA"),
        "atom_count": len(atoms),
        "expected_length_min": minimum,
        "expected_length_max": maximum,
    }
    metrics: dict[str, Any] = {
        "finite_coordinates": True,
        "backbone_incomplete_residue_count": 0,
        "backbone_incomplete_residue_ids": [],
        "ca_spacing_violation_count": 0,
        "peptide_bond_violation_count": 0,
        "chainbreak_count": 0,
        "covalent_geometry_violation_count": 0,
        "backbone_clash_count": 0,
        "sidechain_clash_count": 0,
    }
    reason: AdmissionReject | None = None
    if len(chains) != int(profile["expected_chain_count"]):
        reason = AdmissionReject("chain_count_invalid", "candidate must contain exactly one protein chain", observed=chains)
    elif not minimum <= len(residues) <= maximum:
        reason = AdmissionReject("residue_count_out_of_range", "candidate residue count is outside the request length policy", observed=len(residues))
    elif any(residue.residue_name not in AMINO_ACIDS for residue in residues):
        invalid = [residue_ids[index] for index, residue in enumerate(residues) if residue.residue_name not in AMINO_ACIDS]
        reason = AdmissionReject("nonprotein_residue", "candidate contains a non-standard protein residue", residue_ids=invalid)
    else:
        residue_maps = [_atom_map(residue) for residue in residues]
        incomplete = [residue_ids[index] for index, atom_map in enumerate(residue_maps) if not BACKBONE_ATOMS.issubset(atom_map)]
        metrics["backbone_incomplete_residue_count"] = len(incomplete)
        metrics["backbone_incomplete_residue_ids"] = incomplete
        if incomplete:
            reason = AdmissionReject("backbone_incomplete", "candidate is missing one or more required N/CA/C/O atoms", residue_ids=incomplete)
    if reason is None:
        residue_maps = [_atom_map(residue) for residue in residues]
        ca = np.asarray([atom_map["CA"].coord for atom_map in residue_maps], dtype=np.float64)
        if not np.isfinite(ca).all():
            reason = AdmissionReject("nonfinite_coordinates", "candidate Cα coordinates are non-finite")
        else:
            ca_bounds = [float(value) for value in profile["ca_spacing_angstrom"]]
            ca_distances = np.linalg.norm(np.diff(ca, axis=0), axis=1)
            ca_violations = [index for index, value in enumerate(ca_distances) if _range_violation(float(value), ca_bounds)]
            metrics["ca_spacing_min_angstrom"] = float(ca_distances.min()) if len(ca_distances) else 0.0
            metrics["ca_spacing_max_angstrom"] = float(ca_distances.max()) if len(ca_distances) else 0.0
            metrics["ca_spacing_violation_count"] = len(ca_violations)
            sequence_gaps = [
                index for index, pair in enumerate(zip(residues, residues[1:]))
                if pair[1].key[1] != pair[0].key[1] + 1 or pair[1].key[2] not in {"", "A"}
            ]
            bond_ranges = profile["backbone_bond_ranges_angstrom"]
            covalent_violations = 0
            for atom_map in residue_maps:
                covalent_violations += sum(
                    _range_violation(_distance(atom_map[first], atom_map[second]), bounds)
                    for first, second, bounds in (
                        ("N", "CA", bond_ranges["N_CA"]),
                        ("CA", "C", bond_ranges["CA_C"]),
                        ("C", "O", bond_ranges["C_O"]),
                    )
                )
            peptide_violations = 0
            for previous, current in zip(residue_maps, residue_maps[1:]):
                if _range_violation(_distance(previous["C"], current["N"]), bond_ranges["C_N_next"]):
                    peptide_violations += 1
            metrics["peptide_bond_violation_count"] = peptide_violations
            metrics["covalent_geometry_violation_count"] = covalent_violations + peptide_violations
            metrics["chainbreak_count"] = len(ca_violations) + peptide_violations + len(sequence_gaps)
            if metrics["chainbreak_count"]:
                reason = AdmissionReject("chainbreaks_present", "candidate has one or more Cα, peptide-bond, or residue-order chainbreaks")
            elif metrics["covalent_geometry_violation_count"]:
                reason = AdmissionReject("covalent_geometry_invalid", "candidate backbone covalent geometry is outside the admission profile")
            else:
                backbone_clashes, sidechain_clashes = _clash_metrics(residues, profile)
                metrics["backbone_clash_count"] = backbone_clashes
                metrics["sidechain_clash_count"] = sidechain_clashes
                if backbone_clashes:
                    reason = AdmissionReject("backbone_clashes_present", "candidate has nonbonded backbone clashes")
                elif sidechain_clashes:
                    reason = AdmissionReject("sidechain_clashes_present", "candidate has nonbonded side-chain clashes")
                else:
                    point_count = int(manifest["point_count"])
                    points_raw = np.frombuffer(points_path.read_bytes(), dtype="<f4")
                    if points_raw.size != point_count * 3:
                        raise ValueError("initial admission point-pool byte shape mismatch")
                    sdf_shape = tuple(int(value) for value in manifest["sdf_grid_shape"])
                    sdf_raw = np.frombuffer(sdf_path.read_bytes(), dtype="<f4")
                    if sdf_raw.size != int(np.prod(sdf_shape)):
                        raise ValueError("initial admission SDF byte shape mismatch")
                    try:
                        cad = _cad_metrics(
                            ca,
                            points_raw.reshape(point_count, 3).astype(np.float64),
                            sdf_raw.reshape(sdf_shape).astype(np.float64),
                            manifest,
                        )
                    except AdmissionReject as rejected:
                        reason = rejected
                    else:
                        metrics.update(cad)
                        if cad["cad_bidirectional_mean_angstrom"] > float(profile["max_bidirectional_cad_mean_angstrom"]):
                            reason = AdmissionReject("cad_correspondence_invalid", "candidate CAD correspondence exceeds the initial admission bound")
                        elif cad["cad_sdf_outside_fraction"] > float(profile["max_cad_outside_fraction"]):
                            reason = AdmissionReject("cad_sdf_invalid", "candidate CAD SDF metrics exceed the initial admission bound")
    result = _reject_result(
        candidate_path=candidate_path,
        candidate_sha256=candidate_sha256,
        request=request,
        manifest=manifest,
        profile=profile,
        reason=reason or AdmissionReject("accepted", "candidate passed initial RFD3 admission"),
        counts=counts,
        metrics=metrics,
    )
    if reason is None:
        result["status"] = "accepted"
        result["reason"] = None
    _write_result(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--points", type=Path)
    parser.add_argument("--sdf", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()
    result = admit_initial_candidate(
        candidate_path=args.candidate,
        request_path=args.request,
        manifest_path=args.manifest,
        points_path=args.points,
        sdf_path=args.sdf,
        output_path=args.output,
    )
    if args.strict_exit and result["status"] != "accepted":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
