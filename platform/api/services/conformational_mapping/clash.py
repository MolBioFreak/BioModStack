"""Versioned deterministic spatial-clearance detector for mapped substitutions."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from .contracts import AA_ORDER, canonical_sha256


CLASH_DETECTOR_ID = "bms_clash"
CLASH_DETECTOR_VERSION = "1"
_SIDECHAIN_REACH = {
    "A": 2.1, "C": 2.8, "D": 3.3, "E": 3.9, "F": 4.4,
    "G": 1.7, "H": 4.0, "I": 3.7, "K": 4.6, "L": 3.8,
    "M": 4.1, "N": 3.4, "P": 3.0, "Q": 4.0, "R": 5.0,
    "S": 2.6, "T": 3.0, "V": 3.3, "W": 5.0, "Y": 4.7,
}
CLASH_DETECTOR_SHA256 = canonical_sha256({
    "id": CLASH_DETECTOR_ID, "version": CLASH_DETECTOR_VERSION,
    "sidechain_reach_angstrom": _SIDECHAIN_REACH,
    "obstacle_radius_angstrom": 1.55, "adjacent_sequence_exclusion": 1,
})


class ClashDetectionError(ValueError):
    """Mapped structure cannot support the declared detector contract."""


def build_clash_rows(
    normalized_pdb: Path | str,
    structure_map: Mapping[str, Any],
    *,
    candidate_id: str,
    detector_id: str,
    detector_version: str,
) -> dict[tuple[str, tuple[Any, ...], str], dict[str, Any]]:
    if detector_id != CLASH_DETECTOR_ID or detector_version != CLASH_DETECTOR_VERSION:
        raise ClashDetectionError("requested clash detector is not installed")
    try:
        from Bio.PDB import PDBParser

        model = next(PDBParser(QUIET=True).get_structure("normalized", str(normalized_pdb)).get_models())
    except Exception as exc:
        raise ClashDetectionError(f"cannot parse normalized structure for clash detection: {exc}") from exc
    mapped_rows = [row for row in structure_map.get("rows", []) if row.get("status") == "mapped"]
    sequence_by_pdb = {
        (str(row["pdb_chain_id"]), int(row["pdb_residue_id"]), str(row.get("pdb_insertion_code") or "")): int(row["sequence_index"])
        for row in mapped_rows
    }
    atoms: list[tuple[str, int, str, int | None, tuple[float, float, float]]] = []
    residues: dict[tuple[str, int, str], Any] = {}
    for chain in model:
        for residue in chain:
            het, number, insertion = residue.id
            key = (str(chain.id), int(number), str(insertion).strip())
            sequence_index = sequence_by_pdb.get(key)
            if not str(het).strip():
                residues[key] = residue
            for atom in residue:
                coordinate = atom.coord
                atoms.append((key[0], key[1], key[2], sequence_index, (float(coordinate[0]), float(coordinate[1]), float(coordinate[2]))))
    output: dict[tuple[str, tuple[Any, ...], str], dict[str, Any]] = {}
    for row in mapped_rows:
        pdb_key = (str(row["pdb_chain_id"]), int(row["pdb_residue_id"]), str(row.get("pdb_insertion_code") or ""))
        residue = residues.get(pdb_key)
        if residue is None or "CA" not in residue:
            continue
        ca = tuple(float(value) for value in residue["CA"].coord)
        sequence_index = int(row["sequence_index"])
        identity = (
            row["entity_instance_id"], row["auth_asym_id"], row["auth_seq_id"],
            row.get("insertion_code") or "", sequence_index,
        )
        obstacle_distances = []
        for chain, number, insertion, obstacle_sequence, coordinate in atoms:
            if (chain, number, insertion) == pdb_key:
                continue
            if chain == pdb_key[0] and obstacle_sequence is not None and abs(obstacle_sequence - sequence_index) <= 1:
                continue
            obstacle_distances.append(math.dist(ca, coordinate))
        minimum_distance = min(obstacle_distances) if obstacle_distances else None
        for mutation in AA_ORDER:
            clearance = _SIDECHAIN_REACH[mutation] + 1.55
            clash_flag = minimum_distance is not None and minimum_distance < clearance
            key = (candidate_id, identity, mutation)
            output[key] = {
                "candidate_id": candidate_id, "entity_instance_id": identity[0],
                "auth_asym_id": identity[1], "auth_seq_id": identity[2],
                "insertion_code": identity[3], "sequence_index": identity[4],
                "mutation_aa": mutation, "clash_flag": clash_flag,
                "minimum_obstacle_distance": minimum_distance,
                "clearance_threshold": clearance,
                "detector_id": CLASH_DETECTOR_ID,
                "detector_version": CLASH_DETECTOR_VERSION,
                "detector_sha256": CLASH_DETECTOR_SHA256,
            }
    return output
