from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from Bio.PDB import MMCIFParser, PDBParser

ION_CODES = {"ZN", "MG", "CA", "NA", "K", "MN", "FE", "CO", "NI", "CU", "CL"}


def parse_chain_csv(raw: str | None) -> list[str]:
    return [token.strip() for token in (raw or "").split(",") if token.strip()]


def parse_residue_specs(raw: str | None) -> list[tuple[str, int]]:
    residues: list[tuple[str, int]] = []
    for token in (raw or "").split(","):
        item = token.strip()
        if not item or ":" not in item:
            continue
        chain_id, position_raw = item.split(":", 1)
        chain_id = chain_id.strip()
        try:
            position = int(position_raw.strip())
        except ValueError:
            continue
        if chain_id:
            residues.append((chain_id, position))
    return residues


def load_structure(path: Path, structure_id: str = "structure"):
    suffix = path.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return MMCIFParser(QUIET=True).get_structure(structure_id, path)
    return PDBParser(QUIET=True).get_structure(structure_id, path)


def select_single_model(structure, model_number: int | None):
    if model_number is None:
        return next(structure.get_models())
    for model in structure:
        serial_num = getattr(model, "serial_num", None)
        if serial_num == model_number or model.id == model_number or model.id == (model_number - 1):
            return model
    raise ValueError(f"Requested target model {model_number} not found")


def infer_target_pocket_residues(
    *,
    target_pdb: Path,
    source_target_chains: Sequence[str],
    predicted_target_chains: Sequence[str] | None = None,
    model_number: int | None = None,
    max_residues: int = 24,
    neighbor_radius: float = 10.0,
    ion_distance: float = 4.5,
) -> list[tuple[str, int]]:
    structure = load_structure(target_pdb, "reference_target")
    model = select_single_model(structure, model_number)

    source_chain_ids = [chain_id for chain_id in source_target_chains if chain_id]
    predicted_chain_ids = list(predicted_target_chains or source_chain_ids)
    if not source_chain_ids:
        return []

    chain_map = {}
    if len(source_chain_ids) == len(predicted_chain_ids):
        chain_map = dict(zip(source_chain_ids, predicted_chain_ids))
    elif len(predicted_chain_ids) == 1 and len(source_chain_ids) == 1:
        chain_map = {source_chain_ids[0]: predicted_chain_ids[0]}
    else:
        chain_map = {chain_id: chain_id for chain_id in source_chain_ids}

    ion_coords: list[tuple[float, float, float]] = []
    residues: list[dict[str, object]] = []

    for chain in model:
        if chain.id not in source_chain_ids:
            continue
        sequence_position = 0
        for residue in chain:
            hetflag = residue.id[0].strip()
            if residue.resname.strip().upper() in ION_CODES or hetflag.startswith("H_"):
                for atom in residue:
                    coord = atom.coord
                    ion_coords.append((float(coord[0]), float(coord[1]), float(coord[2])))
                continue
            if "CA" not in residue:
                continue
            sequence_position += 1
            ca = residue["CA"].coord
            atom_coords = []
            for atom in residue:
                coord = atom.coord
                atom_coords.append((float(coord[0]), float(coord[1]), float(coord[2])))
            residues.append(
                {
                    "source_chain": chain.id,
                    "predicted_chain": chain_map.get(chain.id, chain.id),
                    "resseq": int(residue.id[1]),
                    "seq_position": int(sequence_position),
                    "ca": (float(ca[0]), float(ca[1]), float(ca[2])),
                    "atoms": atom_coords,
                }
            )

    if not residues:
        return []

    radius_sq = float(neighbor_radius) ** 2
    ion_distance_sq = float(ion_distance) ** 2
    ranked: list[tuple[int, float, int, str, int]] = []

    for idx, residue in enumerate(residues):
        x1, y1, z1 = residue["ca"]  # type: ignore[index]
        neighbors = 0
        for other_idx, other in enumerate(residues):
            if idx == other_idx:
                continue
            x2, y2, z2 = other["ca"]  # type: ignore[index]
            dx = x1 - x2
            dy = y1 - y2
            dz = z1 - z2
            if dx * dx + dy * dy + dz * dz <= radius_sq:
                neighbors += 1

        min_ion_dist = math.inf
        if ion_coords:
            for ax, ay, az in residue["atoms"]:  # type: ignore[index]
                for ix, iy, iz in ion_coords:
                    dx = ax - ix
                    dy = ay - iy
                    dz = az - iz
                    dist_sq = dx * dx + dy * dy + dz * dz
                    if dist_sq < min_ion_dist:
                        min_ion_dist = dist_sq
        ion_priority = 0 if min_ion_dist <= ion_distance_sq else 1
        ranked.append(
            (
                ion_priority,
                neighbors,
                int(residue["seq_position"]),
                str(residue["predicted_chain"]),
                idx,
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    selected: list[tuple[str, int]] = []
    seen = set()
    limit = max(1, int(max_residues))
    for _ion_priority, _neighbors, _resseq, _chain_id, idx in ranked:
        residue = residues[idx]
        spec = (str(residue["predicted_chain"]), int(residue["seq_position"]))
        if spec in seen:
            continue
        selected.append(spec)
        seen.add(spec)
        if len(selected) >= limit:
            break
    return selected


def add_pocket_constraint(
    entry: dict,
    *,
    binder_chain_ids: Sequence[str],
    target_residue_specs: Sequence[tuple[str, int]],
    pocket_max_distance: float = 8.0,
    replace_existing: bool = False,
) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("constraint") and not replace_existing:
        return False

    entity_by_chain: dict[str, int] = {}
    protein_entity_index = 0
    sequences = entry.get("sequences", [])
    if not isinstance(sequences, list):
        return False

    for wrapper in sequences:
        if not isinstance(wrapper, dict):
            continue
        chain = wrapper.get("proteinChain")
        if not isinstance(chain, dict):
            continue
        protein_entity_index += 1
        ids = chain.get("id")
        if isinstance(ids, list):
            for chain_id in ids:
                token = str(chain_id).strip()
                if token:
                    entity_by_chain[token] = protein_entity_index
        elif isinstance(ids, str) and ids.strip():
            entity_by_chain[ids.strip()] = protein_entity_index

    binder_entity = next((entity_by_chain[chain_id] for chain_id in binder_chain_ids if chain_id in entity_by_chain), None)
    if binder_entity is None:
        return False

    contact_residues = []
    for chain_id, position in target_residue_specs:
        entity = entity_by_chain.get(chain_id)
        if entity is None:
            continue
        contact_residues.append({"entity": entity, "copy": 1, "position": int(position)})

    if not contact_residues:
        return False

    entry["constraint"] = {
        "pocket": {
            "binder_chain": {"entity": binder_entity, "copy": 1},
            "contact_residues": contact_residues,
            "max_distance": float(pocket_max_distance),
        }
    }
    return True
