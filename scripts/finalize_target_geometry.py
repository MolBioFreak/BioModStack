#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
from pathlib import Path
from typing import Iterable, List, Tuple

from Bio.PDB import MMCIFParser, PDBIO, PDBParser, Superimposer
from Bio.PDB.Structure import Structure as BioStructure


def parse_chain_csv(raw: str) -> list[str]:
    return [token.strip() for token in (raw or "").split(",") if token.strip()]


def load_structure(path: Path, structure_id: str):
    suffix = path.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return MMCIFParser(QUIET=True).get_structure(structure_id, path)
    return PDBParser(QUIET=True).get_structure(structure_id, path)


def select_single_model(structure, model_number: int | None):
    models = list(structure.get_models())
    if not models or model_number is None or len(models) == 1:
        return structure

    selected_model = None
    for model in models:
        serial_num = getattr(model, "serial_num", None)
        if serial_num == model_number or model.id == model_number or model.id == (model_number - 1):
            selected_model = model
            break

    if selected_model is None:
        raise ValueError(f"Requested target model {model_number} not found in reference structure")

    single_model = BioStructure(structure.id)
    single_model.add(copy.deepcopy(selected_model))
    return single_model


def get_chain_ids(structure) -> list[str]:
    chain_ids: list[str] = []
    seen = set()
    for model in structure:
        for chain in model:
            if chain.id in seen:
                continue
            seen.add(chain.id)
            chain_ids.append(chain.id)
    return chain_ids


def get_ca_atoms_by_key(structure, chains: Iterable[str] | None = None):
    allowed = set(chains) if chains is not None else None
    ca_atoms = {}
    for model in structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            for residue in chain:
                if "CA" not in residue:
                    continue
                _, resseq, icode = residue.id
                key = (chain.id, int(resseq), str(icode).strip())
                ca_atoms[key] = residue["CA"]
    return ca_atoms


def get_ca_atoms_by_chain_order(structure, chains: Iterable[str] | None = None):
    allowed = set(chains) if chains is not None else None
    by_chain = {}
    for model in structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            ordered = [residue["CA"] for residue in chain if "CA" in residue]
            if ordered:
                by_chain[chain.id] = ordered
    return by_chain


def get_matched_ca_atoms(ref_structure, mobile_structure, chains: Iterable[str] | None = None):
    ref_map = get_ca_atoms_by_key(ref_structure, chains=chains)
    mobile_map = get_ca_atoms_by_key(mobile_structure, chains=chains)
    common_keys = sorted(set(ref_map.keys()) & set(mobile_map.keys()), key=lambda item: (item[0], item[1], item[2]))
    if len(common_keys) >= 3:
        return [ref_map[key] for key in common_keys], [mobile_map[key] for key in common_keys]

    ref_ordered = get_ca_atoms_by_chain_order(ref_structure, chains=chains)
    mobile_ordered = get_ca_atoms_by_chain_order(mobile_structure, chains=chains)
    shared = [chain_id for chain_id in ref_ordered if chain_id in mobile_ordered]
    ref_atoms = []
    mobile_atoms = []
    for chain_id in shared:
        ref_chain_atoms = ref_ordered[chain_id]
        mobile_chain_atoms = mobile_ordered[chain_id]
        if len(ref_chain_atoms) != len(mobile_chain_atoms):
            continue
        ref_atoms.extend(ref_chain_atoms)
        mobile_atoms.extend(mobile_chain_atoms)
    if len(ref_atoms) < 3 or len(ref_atoms) != len(mobile_atoms):
        raise ValueError(f"Insufficient matched CA atoms for chains={list(chains) if chains is not None else 'ALL'}")
    return ref_atoms, mobile_atoms


def get_matched_ca_atoms_by_chain_map(
    ref_structure,
    mobile_structure,
    ref_chain_ids: list[str],
    mobile_chain_ids: list[str],
):
    if len(ref_chain_ids) != len(mobile_chain_ids):
        raise ValueError("Reference/mobile chain lists must have equal length")

    ref_by_chain = get_ca_atoms_by_chain_order(ref_structure, ref_chain_ids)
    mobile_by_chain = get_ca_atoms_by_chain_order(mobile_structure, mobile_chain_ids)
    ref_atoms = []
    mobile_atoms = []
    for ref_chain_id, mobile_chain_id in zip(ref_chain_ids, mobile_chain_ids):
        ref_chain_atoms = ref_by_chain.get(ref_chain_id, [])
        mobile_chain_atoms = mobile_by_chain.get(mobile_chain_id, [])
        if len(ref_chain_atoms) != len(mobile_chain_atoms):
            raise ValueError(
                f"Chain length mismatch for target mapping {ref_chain_id}->{mobile_chain_id}: "
                f"{len(ref_chain_atoms)} vs {len(mobile_chain_atoms)}"
            )
        ref_atoms.extend(ref_chain_atoms)
        mobile_atoms.extend(mobile_chain_atoms)
    if len(ref_atoms) < 3:
        raise ValueError("Insufficient CA atoms for mapped target alignment")
    return ref_atoms, mobile_atoms


def rmsd_without_refit(ref_atoms, mobile_atoms) -> float:
    if len(ref_atoms) != len(mobile_atoms) or not ref_atoms:
        raise ValueError("Invalid atom sets for RMSD computation")
    squared = 0.0
    for ref_atom, mobile_atom in zip(ref_atoms, mobile_atoms):
        diff = ref_atom.coord - mobile_atom.coord
        squared += float(diff[0] ** 2 + diff[1] ** 2 + diff[2] ** 2)
    return math.sqrt(squared / len(ref_atoms))


def renumber_mobile_residues_by_order(ref_structure, mobile_structure, chains: Iterable[str] | None = None) -> None:
    allowed = set(chains) if chains is not None else None
    ref_by_chain = {}
    mobile_by_chain = {}
    for model in ref_structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            residues = [residue for residue in chain if "CA" in residue]
            if residues:
                ref_by_chain[chain.id] = residues
    for model in mobile_structure:
        for chain in model:
            if allowed is not None and chain.id not in allowed:
                continue
            residues = [residue for residue in chain if "CA" in residue]
            if residues:
                mobile_by_chain[chain.id] = residues
    for chain_id in sorted(set(ref_by_chain) & set(mobile_by_chain)):
        ref_residues = ref_by_chain[chain_id]
        mobile_residues = mobile_by_chain[chain_id]
        if len(ref_residues) != len(mobile_residues):
            continue
        for ref_residue, mobile_residue in zip(ref_residues, mobile_residues):
            mobile_residue.id = ref_residue.id


def iter_prediction_records(prediction_dir: Path, backend: str) -> list[Tuple[str, Path, Path]]:
    records: list[Tuple[str, Path, Path]] = []
    if backend == "protenix":
        for summary_json in sorted(prediction_dir.rglob("*_summary_confidence_sample_*.json")):
            base_name, sample_rank = summary_json.stem.rsplit("_summary_confidence_sample_", 1)
            design_name = f"{base_name}_sample_{sample_rank}"
            structure_path = summary_json.with_name(f"{design_name}.cif")
            if not structure_path.exists():
                structure_path = summary_json.with_name(f"{design_name}.pdb")
            if structure_path.exists():
                records.append((design_name, structure_path, summary_json))
        return records

    for structure_path in sorted(prediction_dir.rglob("*.pdb")):
        if structure_path.name.endswith(".raw.pdb"):
            continue
        summary_candidates = [
            structure_path.with_name(f"confidence_{structure_path.stem}.json"),
            structure_path.with_name(f"{structure_path.stem}.json"),
        ]
        summary_json = next((candidate for candidate in summary_candidates if candidate.exists()), None)
        if summary_json is None:
            continue
        records.append((structure_path.stem, structure_path, summary_json))
    return records


def replace_target_chains(mobile_structure, ref_structure, ref_chain_ids: list[str], predicted_chain_ids: list[str]) -> None:
    ref_model = next(ref_structure.get_models())
    mobile_model = next(mobile_structure.get_models())

    for chain_id in predicted_chain_ids:
        if chain_id in mobile_model:
            mobile_model.detach_child(chain_id)

    for ref_chain_id, predicted_chain_id in zip(ref_chain_ids, predicted_chain_ids):
        if ref_chain_id not in ref_model:
            continue
        cloned_chain = copy.deepcopy(ref_model[ref_chain_id])
        cloned_chain.id = predicted_chain_id
        mobile_model.add(cloned_chain)


def write_pdb(structure, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    io = PDBIO()
    io.set_structure(structure)
    io.save(str(out_path))


def finalize_record(
    *,
    backend: str,
    geometry_mode: str,
    structure_path: Path,
    summary_json_path: Path,
    reference_target_path: Path,
    reference_target_chains: list[str],
    predicted_target_chains: list[str],
    target_model_number: int | None,
) -> None:
    ref_structure = select_single_model(
        load_structure(reference_target_path, "reference_target"),
        target_model_number,
    )
    mobile_structure = load_structure(structure_path, "predicted_complex")

    renumber_mobile_residues_by_order(
        ref_structure,
        mobile_structure,
        chains=predicted_target_chains,
    )

    if reference_target_chains == predicted_target_chains:
        ref_target_atoms, mobile_target_atoms = get_matched_ca_atoms(
            ref_structure,
            mobile_structure,
            chains=reference_target_chains,
        )
    else:
        ref_target_atoms, mobile_target_atoms = get_matched_ca_atoms_by_chain_map(
            ref_structure,
            mobile_structure,
            reference_target_chains,
            predicted_target_chains,
        )

    superimposer = Superimposer()
    superimposer.set_atoms(ref_target_atoms, mobile_target_atoms)
    superimposer.apply(mobile_structure.get_atoms())

    if reference_target_chains == predicted_target_chains:
        ref_target_atoms, mobile_target_atoms = get_matched_ca_atoms(
            ref_structure,
            mobile_structure,
            chains=reference_target_chains,
        )
    else:
        ref_target_atoms, mobile_target_atoms = get_matched_ca_atoms_by_chain_map(
            ref_structure,
            mobile_structure,
            reference_target_chains,
            predicted_target_chains,
        )
    raw_target_rmsd = rmsd_without_refit(ref_target_atoms, mobile_target_atoms)

    target_replaced = geometry_mode == "frozen"
    final_target_rmsd = raw_target_rmsd
    if target_replaced:
        replace_target_chains(mobile_structure, ref_structure, reference_target_chains, predicted_target_chains)
        final_target_rmsd = 0.0

    with summary_json_path.open("r", encoding="utf-8") as handle:
        metrics = json.load(handle)

    output_pdb = structure_path.with_suffix(".pdb")
    if backend == "boltz" and structure_path.suffix.lower() == ".pdb":
        raw_copy = structure_path.with_suffix(".raw.pdb")
        if not raw_copy.exists():
            shutil.copy2(structure_path, raw_copy)
    write_pdb(mobile_structure, output_pdb)

    metrics["target_geometry_mode"] = geometry_mode
    metrics["target_replaced_from_reference"] = target_replaced
    metrics["raw_target_rmsd"] = round(raw_target_rmsd, 3)
    metrics["final_target_rmsd"] = round(final_target_rmsd, 3)
    metrics["aligned_pdb"] = output_pdb.name
    metrics["target_reference_path"] = str(reference_target_path)
    metrics["target_reference_chains"] = reference_target_chains
    metrics["target_prediction_chains"] = predicted_target_chains
    if backend == "boltz":
        metrics["boltz_target_rmsd"] = round(raw_target_rmsd, 3)
    else:
        metrics["protenix_target_rmsd"] = round(raw_target_rmsd, 3)

    with summary_json_path.open("w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Align and optionally freeze target geometry in predicted complexes")
    parser.add_argument("--prediction_dir", type=Path, required=True)
    parser.add_argument("--backend", choices=["boltz", "protenix"], required=True)
    parser.add_argument("--geometry_mode", choices=["flexible", "conditioned", "frozen"], required=True)
    parser.add_argument("--target_pdb", type=Path, required=True)
    parser.add_argument("--reference_target_chains", required=True)
    parser.add_argument("--predicted_target_chains", required=True)
    parser.add_argument("--target_model_number", type=int, default=None)
    args = parser.parse_args()

    if args.geometry_mode == "flexible":
        return

    reference_target_chains = parse_chain_csv(args.reference_target_chains)
    predicted_target_chains = parse_chain_csv(args.predicted_target_chains)
    if not reference_target_chains or not predicted_target_chains:
        raise SystemExit("Target chain lists are required for conditioned/frozen geometry")
    if len(reference_target_chains) != len(predicted_target_chains):
        raise SystemExit("Reference and predicted target chain counts must match")

    records = iter_prediction_records(args.prediction_dir, args.backend)
    if not records:
        raise SystemExit(f"No prediction records found in {args.prediction_dir}")

    for _design_name, structure_path, summary_json_path in records:
        finalize_record(
            backend=args.backend,
            geometry_mode=args.geometry_mode,
            structure_path=structure_path,
            summary_json_path=summary_json_path,
            reference_target_path=args.target_pdb,
            reference_target_chains=reference_target_chains,
            predicted_target_chains=predicted_target_chains,
            target_model_number=args.target_model_number,
        )


if __name__ == "__main__":
    main()
