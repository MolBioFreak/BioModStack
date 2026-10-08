from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np


STANDARD_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS",
    "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO",
    "SER", "THR", "TRP", "TYR", "VAL",
    "DA", "DC", "DT", "DG", "A", "C", "U", "G",
}
NUCLEIC_RESIDUES = {"DA", "DC", "DT", "DG", "A", "C", "U", "G"}

BOLTZ_PAE_NPZ_FORMAT = "boltz_pae_npz"
PROTENIX_FULL_JSON_FORMAT = "protenix_full_json"
CONFIDENCE_JSON_FORMAT = "confidence_json"


@dataclass(frozen=True)
class ResidueRecord:
    index: int
    chain_id: str
    residue_name: str
    residue_number: int
    ca_coord: np.ndarray
    cb_coord: np.ndarray
    chain_type: str


@dataclass(frozen=True)
class AlignedErrorArtifact:
    path: Path
    format: str
    matrix_key: str
    matrix: np.ndarray
    residues: list[ResidueRecord]


def _normalize_path(path_value: str | Path | None) -> Path | None:
    if not path_value:
        return None
    return Path(path_value).expanduser().resolve()


def _parse_pdb_atom_line(line: str) -> dict[str, Any] | None:
    atom_num = int(line[6:11].strip())
    atom_name = line[12:16].strip()
    residue_name = line[17:20].strip()
    chain_id = line[21].strip()
    residue_seq_num = line[22:26].strip()
    if not residue_seq_num:
        return None
    if residue_name == "LIG":
        return None
    return {
        "atom_num": atom_num,
        "atom_name": atom_name,
        "residue_name": residue_name,
        "chain_id": chain_id,
        "residue_seq_num": int(residue_seq_num),
        "x": float(line[30:38].strip()),
        "y": float(line[38:46].strip()),
        "z": float(line[46:54].strip()),
    }


def _parse_cif_atom_line(line: str, field_map: dict[str, int]) -> dict[str, Any] | None:
    parts = line.split()
    residue_name = parts[field_map["label_comp_id"]]
    residue_seq_num = parts[field_map["label_seq_id"]]
    if residue_seq_num == ".":
        return None
    chain_key = "auth_asym_id" if "auth_asym_id" in field_map else "label_asym_id"
    return {
        "atom_num": int(parts[field_map["id"]]),
        "atom_name": parts[field_map["label_atom_id"]],
        "residue_name": residue_name,
        "chain_id": parts[field_map[chain_key]],
        "residue_seq_num": int(residue_seq_num),
        "x": float(parts[field_map["Cartn_x"]]),
        "y": float(parts[field_map["Cartn_y"]]),
        "z": float(parts[field_map["Cartn_z"]]),
    }


def load_structure_residue_records(structure_path: str | Path) -> tuple[list[ResidueRecord], np.ndarray]:
    resolved = _normalize_path(structure_path)
    if resolved is None or not resolved.exists():
        raise FileNotFoundError(f"Structure file not found: {structure_path}")

    is_cif = resolved.suffix.lower() == ".cif"
    field_map: dict[str, int] = {}
    token_mask: list[int] = []
    residues: list[dict[str, Any]] = []
    cb_coords: dict[tuple[str, int, str], np.ndarray] = {}

    with resolved.open("r") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if is_cif and line.startswith("_atom_site."):
                _atom_site, field_name = line.split(".", 1)
                field_map[field_name.strip()] = len(field_map)
                continue
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue

            atom = _parse_cif_atom_line(line, field_map) if is_cif else _parse_pdb_atom_line(line)
            if atom is None:
                token_mask.append(0)
                continue

            atom_name = str(atom["atom_name"]).strip()
            residue_name = str(atom["residue_name"]).strip()
            chain_id = str(atom["chain_id"]).strip()
            residue_number = int(atom["residue_seq_num"])
            coord = np.array([float(atom["x"]), float(atom["y"]), float(atom["z"])], dtype=float)
            residue_key = (chain_id, residue_number, residue_name)

            if atom_name in {"CA", "C1'", "C1"}:
                token_mask.append(1)
                residues.append(
                    {
                        "chain_id": chain_id,
                        "residue_name": residue_name,
                        "residue_number": residue_number,
                        "ca_coord": coord,
                        "key": residue_key,
                    }
                )
            elif residue_name not in STANDARD_RESIDUES:
                token_mask.append(0)

            is_cb_like = atom_name in {"CB", "C3'", "C3"}
            is_gly_fallback = residue_name == "GLY" and atom_name == "CA"
            is_nuc_fallback = residue_name in NUCLEIC_RESIDUES and atom_name in {"C1'", "C1"}
            if is_cb_like or is_gly_fallback or is_nuc_fallback:
                cb_coords[residue_key] = coord

    residue_records: list[ResidueRecord] = []
    for idx, residue in enumerate(residues):
        chain_type = "nucleic_acid" if residue["residue_name"] in NUCLEIC_RESIDUES else "protein"
        ca_coord = residue["ca_coord"]
        cb_coord = cb_coords.get(residue["key"], ca_coord)
        residue_records.append(
            ResidueRecord(
                index=idx,
                chain_id=str(residue["chain_id"]),
                residue_name=str(residue["residue_name"]),
                residue_number=int(residue["residue_number"]),
                ca_coord=np.asarray(ca_coord, dtype=float),
                cb_coord=np.asarray(cb_coord, dtype=float),
                chain_type=chain_type,
            )
        )

    return residue_records, np.asarray(token_mask, dtype=bool)


def _validate_square_matrix(matrix: np.ndarray, path: Path) -> np.ndarray:
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"Aligned-error matrix at {path} is not square")
    return matrix.astype(float)


def _reduce_masked_matrix(matrix: np.ndarray, keep_mask: np.ndarray, residue_count: int, path: Path) -> np.ndarray:
    keep_mask = np.asarray(keep_mask, dtype=bool)
    if keep_mask.ndim != 1:
        raise ValueError(f"Aligned-error mask at {path} must be one-dimensional")
    if matrix.shape[0] != keep_mask.size:
        raise ValueError(
            f"Aligned-error matrix size mismatch for {path}: matrix={matrix.shape[0]} mask={keep_mask.size}"
        )
    reduced = matrix[np.ix_(keep_mask, keep_mask)]
    if reduced.shape[0] != residue_count:
        raise ValueError(
            f"Aligned-error reduced matrix mismatch for {path}: reduced={reduced.shape[0]} residues={residue_count}"
        )
    return reduced


def _reduce_atom_token_matrix(matrix: np.ndarray, token_mask: np.ndarray, residue_count: int, path: Path) -> np.ndarray:
    if matrix.shape[0] == residue_count:
        return matrix
    if token_mask.size and matrix.shape[0] == token_mask.size:
        return _reduce_masked_matrix(matrix, token_mask, residue_count, path)
    raise ValueError(
        f"Aligned-error matrix size mismatch for {path}: "
        f"matrix={matrix.shape[0]} residues={residue_count} atom_tokens={token_mask.size}"
    )


def _reduce_protenix_matrix(
    matrix: np.ndarray,
    payload: dict[str, Any],
    token_mask: np.ndarray,
    residue_count: int,
    path: Path,
) -> np.ndarray:
    if matrix.shape[0] == residue_count:
        return matrix

    token_has_frame = payload.get("token_has_frame")
    if isinstance(token_has_frame, list) and len(token_has_frame) == matrix.shape[0]:
        try:
            keep_mask = np.asarray(token_has_frame, dtype=bool)
            if int(keep_mask.sum()) == residue_count:
                return _reduce_masked_matrix(matrix, keep_mask, residue_count, path)
        except Exception:
            pass

    return _reduce_atom_token_matrix(matrix, token_mask, residue_count, path)


def load_aligned_error_artifact(
    *,
    aligned_error_path: str | Path,
    aligned_error_format: str,
    structure_path: str | Path,
    matrix_key: str | None = None,
) -> AlignedErrorArtifact:
    artifact_path = _normalize_path(aligned_error_path)
    if artifact_path is None or not artifact_path.exists():
        raise FileNotFoundError(f"Aligned-error artifact not found: {aligned_error_path}")

    residues, token_mask = load_structure_residue_records(structure_path)
    if not residues:
        raise ValueError(f"No polymer residue records parsed from structure: {structure_path}")

    if aligned_error_format == BOLTZ_PAE_NPZ_FORMAT:
        key = matrix_key or "pae"
        with np.load(artifact_path) as payload:
            if key not in payload:
                raise ValueError(f"Boltz aligned-error artifact {artifact_path} is missing key {key}")
            matrix = np.asarray(payload[key], dtype=float)
        matrix = _validate_square_matrix(matrix, artifact_path)
        matrix = _reduce_atom_token_matrix(matrix, token_mask, len(residues), artifact_path)
    elif aligned_error_format in {PROTENIX_FULL_JSON_FORMAT, CONFIDENCE_JSON_FORMAT}:
        payload = json.loads(artifact_path.read_text())
        key = matrix_key or ("token_pair_pae" if aligned_error_format == PROTENIX_FULL_JSON_FORMAT else "pae")
        if key not in payload:
            raise ValueError(f"Aligned-error JSON artifact {artifact_path} is missing key {key}")
        matrix = np.asarray(payload[key], dtype=float)
        matrix = _validate_square_matrix(matrix, artifact_path)
        if aligned_error_format == PROTENIX_FULL_JSON_FORMAT:
            matrix = _reduce_protenix_matrix(matrix, payload, token_mask, len(residues), artifact_path)
        elif matrix.shape[0] != len(residues):
            raise ValueError(
                f"Aligned-error matrix size mismatch for {artifact_path}: "
                f"matrix={matrix.shape[0]} residues={len(residues)}"
            )
    else:
        raise ValueError(f"Unsupported aligned-error artifact format: {aligned_error_format}")

    return AlignedErrorArtifact(
        path=artifact_path,
        format=aligned_error_format,
        matrix_key=key,
        matrix=matrix,
        residues=residues,
    )


def fingerprint_aligned_error_artifact(
    *,
    aligned_error_path: str | Path,
    aligned_error_format: str,
    matrix_key: str | None = None,
) -> dict[str, Any]:
    artifact_path = _normalize_path(aligned_error_path)
    if artifact_path is None or not artifact_path.exists():
        raise FileNotFoundError(f"Aligned-error artifact not found: {aligned_error_path}")
    stat = artifact_path.stat()
    return {
        "path": str(artifact_path),
        "format": aligned_error_format,
        "matrix_key": matrix_key or None,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def detect_aligned_error_artifact(
    *,
    structure_path: str | Path,
    summary_json_path: str | Path | None = None,
) -> tuple[Path, str, str] | None:
    structure = _normalize_path(structure_path)
    summary = _normalize_path(summary_json_path)
    if structure is None:
        return None

    parents = [structure.parent]
    if summary is not None and summary.parent not in parents:
        parents.insert(0, summary.parent)
    aligned_error_parents = [parent / "aligned_error" for parent in parents]

    structure_stem = structure.stem
    summary_stem = summary.stem if summary is not None else ""
    stems = [
        structure_stem,
        summary_stem,
        summary_stem.replace("_boltzpred", ""),
        structure_stem.replace("_boltzpred", ""),
        summary_stem.replace("confidence_", ""),
        structure_stem.replace("confidence_", ""),
    ]
    seen: set[str] = set()
    clean_stems: list[str] = []
    for stem in stems:
        stem = str(stem or "").strip()
        if not stem or stem in seen:
            continue
        seen.add(stem)
        clean_stems.append(stem)

    for parent in parents:
        for stem in clean_stems:
            for candidate in (
                parent / f"{stem}.pae.npz",
                parent / f"pae_{stem}.npz",
                parent / f"{stem}_pae.npz",
                parent / f"pae_{stem}_model_0.npz",
            ):
                if candidate.exists():
                    return candidate, BOLTZ_PAE_NPZ_FORMAT, "pae"

    if summary is not None and summary.exists():
        try:
            payload = json.loads(summary.read_text())
        except Exception:
            payload = None

        if isinstance(payload, dict):
            raw_artifact_ref = str(payload.get("aligned_error_artifact") or "").strip()
            if raw_artifact_ref:
                candidate = Path(raw_artifact_ref)
                if not candidate.is_absolute():
                    candidate = summary.parent / candidate
                candidate = candidate.expanduser().resolve()
                if candidate.exists():
                    artifact_format = str(payload.get("aligned_error_format") or "").strip() or PROTENIX_FULL_JSON_FORMAT
                    matrix_key = str(payload.get("aligned_error_key") or "").strip()
                    if not matrix_key:
                        matrix_key = "pae" if artifact_format == CONFIDENCE_JSON_FORMAT else "token_pair_pae"
                    return candidate, artifact_format, matrix_key

        protenix_candidates = []
        if "_summary_confidence_sample_" in summary.name:
            protenix_candidates.append(summary.with_name(summary.name.replace("_summary_confidence_sample_", "_full_data_sample_")))
        for stem in clean_stems:
            for parent in [summary.parent, *aligned_error_parents]:
                protenix_candidates.extend(
                    [
                        parent / f"{stem}_full_data.json",
                        parent / f"{stem}_full_data_sample_0.json",
                        parent / f"full_data_{stem}.json",
                    ]
                )
        seen_candidates: set[str] = set()
        for candidate in protenix_candidates:
            candidate_str = str(candidate)
            if candidate_str in seen_candidates:
                continue
            seen_candidates.add(candidate_str)
            if candidate.exists():
                return candidate, PROTENIX_FULL_JSON_FORMAT, "token_pair_pae"

        if isinstance(payload, dict) and "pae" in payload:
            return summary, CONFIDENCE_JSON_FORMAT, "pae"

    return None
