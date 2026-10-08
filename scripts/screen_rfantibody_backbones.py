#!/usr/bin/env python3
"""
Score and optionally filter RFantibody backbone outputs before sequence design.

This is intentionally coarse. The goal is to remove obviously detached or badly
placed backbones before expensive downstream stages, not to rank final binders.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "platform" / "api"))

try:
    from services.structure_utils import load_structure  # type: ignore
except Exception as exc:  # pragma: no cover - import failure should be explicit at runtime
    raise SystemExit(f"Failed to import structure_utils helpers: {exc}")


def parse_residue_list(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def normalize_chain_hint(raw: str) -> str | None:
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return parts[0] if parts else None


def parse_residue_numbers(epitope_residues: list[str]) -> set[int]:
    residue_numbers: set[int] = set()
    for res_spec in epitope_residues:
        if not res_spec:
            continue
        num_str = "".join(char for char in res_spec.strip() if char.isdigit() or char == "-")
        if not num_str:
            continue
        try:
            residue_numbers.add(int(num_str))
        except ValueError:
            continue
    return residue_numbers


def parse_residue_specs(epitope_residues: list[str]) -> list[tuple[str | None, int]]:
    specs: list[tuple[str | None, int]] = []
    for res_spec in epitope_residues:
        raw = (res_spec or "").strip()
        if not raw:
            continue
        chain_id = raw[0] if raw[0].isalpha() else None
        num_str = "".join(char for char in raw if char.isdigit() or char == "-")
        if not num_str:
            continue
        try:
            specs.append((chain_id, int(num_str)))
        except ValueError:
            continue
    return specs


def unique_residue_ids(ca_atoms) -> list[int]:
    seen: list[int] = []
    for res_id in ca_atoms.res_id.tolist():
        res_id = int(res_id)
        if not seen or seen[-1] != res_id:
            seen.append(res_id)
    return seen


def format_residue_label(atom) -> str:
    chain_id = str(getattr(atom, "chain_id", "") or "")
    res_id = int(getattr(atom, "res_id", 0))
    res_name = str(getattr(atom, "res_name", "") or "").strip()
    if res_name:
        return f"{chain_id}{res_id}:{res_name}"
    return f"{chain_id}{res_id}"


def format_atom_label(atom) -> str:
    residue_label = format_residue_label(atom)
    atom_name = str(getattr(atom, "atom_name", "") or "").strip()
    return f"{residue_label}:{atom_name}" if atom_name else residue_label


def parse_hlt_loop_residue_ids(pdb_path: Path) -> dict[str, set[int]]:
    loop_residues: dict[str, set[int]] = {}
    try:
        with open(pdb_path, "r") as handle:
            for line in handle:
                if not line.startswith("REMARK PDBinfo-LABEL:"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    residue_id = int(parts[2])
                except ValueError:
                    continue
                loop_id = parts[3].strip().upper()
                if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
                    loop_residues.setdefault(loop_id, set()).add(residue_id)
    except Exception:
        return {}
    return loop_residues


def nearest_pair_details(query_atoms, target_atoms) -> dict[str, Any]:
    if len(query_atoms) == 0 or len(target_atoms) == 0:
        return {
            "distance": None,
            "query_residue": None,
            "target_residue": None,
            "query_atom": None,
            "target_atom": None,
        }

    pairwise_distances = np.linalg.norm(
        query_atoms.coord[:, None, :] - target_atoms.coord[None, :, :],
        axis=2,
    )
    query_idx, target_idx = np.unravel_index(np.argmin(pairwise_distances), pairwise_distances.shape)
    query_atom = query_atoms[query_idx]
    target_atom = target_atoms[target_idx]
    return {
        "distance": float(pairwise_distances[query_idx, target_idx]),
        "query_residue": format_residue_label(query_atom),
        "target_residue": format_residue_label(target_atom),
        "query_atom": format_atom_label(query_atom),
        "target_atom": format_atom_label(target_atom),
    }


def calculate_radius_of_gyration(coords: np.ndarray) -> float | None:
    if coords.size == 0:
        return None
    centroid = np.mean(coords, axis=0)
    squared_distances = np.sum((coords - centroid) ** 2, axis=1)
    return float(np.sqrt(np.mean(squared_distances)))


def per_loop_geometry_metrics(
    antibody_ca,
    antibody_atoms,
    epitope_ca,
    epitope_atoms,
    target_ca,
    target_atoms,
    antibody_chain_ids: list[str],
    loop_residue_ids: dict[str, set[int]],
    epitope_contact_distance_threshold: float,
    target_contact_distance_threshold: float,
) -> dict[str, dict[str, Any]]:
    loop_metrics: dict[str, dict[str, Any]] = {}
    heavy_chain_id = antibody_chain_ids[0] if antibody_chain_ids else "H"
    light_chain_id = antibody_chain_ids[1] if len(antibody_chain_ids) > 1 else heavy_chain_id
    for loop_id, residue_ids in loop_residue_ids.items():
        if not residue_ids:
            continue
        loop_chain = light_chain_id if loop_id.startswith("L") else heavy_chain_id
        loop_ca = antibody_ca[(antibody_ca.chain_id == loop_chain) & np.isin(antibody_ca.res_id, list(residue_ids))]
        loop_atoms = antibody_atoms[(antibody_atoms.chain_id == loop_chain) & np.isin(antibody_atoms.res_id, list(residue_ids))]
        if len(loop_ca) == 0:
            continue

        loop_coords = loop_ca.coord
        target_pairwise = np.linalg.norm(
            loop_coords[:, None, :] - target_ca.coord[None, :, :],
            axis=2,
        )
        min_target_distances = np.min(target_pairwise, axis=1)
        target_ca_nearest = nearest_pair_details(loop_ca, target_ca)
        target_atom_nearest = nearest_pair_details(loop_atoms, target_atoms)

        epitope_contact_count = 0
        epitope_min_distance: float | None = None
        epitope_ca_nearest = {
            "distance": None,
            "query_residue": None,
            "target_residue": None,
            "query_atom": None,
            "target_atom": None,
        }
        epitope_atom_nearest = {
            "distance": None,
            "query_residue": None,
            "target_residue": None,
            "query_atom": None,
            "target_atom": None,
        }
        if len(epitope_ca) > 0:
            epitope_pairwise = np.linalg.norm(
                loop_coords[:, None, :] - epitope_ca.coord[None, :, :],
                axis=2,
            )
            min_epitope_distances = np.min(epitope_pairwise, axis=1)
            epitope_contact_count = int(np.sum(min_epitope_distances < epitope_contact_distance_threshold))
            epitope_min_distance = float(np.min(min_epitope_distances))
            epitope_ca_nearest = nearest_pair_details(loop_ca, epitope_ca)
            if len(epitope_atoms) > 0:
                epitope_atom_nearest = nearest_pair_details(loop_atoms, epitope_atoms)

        loop_metrics[loop_id] = {
            "residue_count": int(len(loop_ca)),
            "epitope_contact_count": epitope_contact_count,
            "epitope_min_distance": epitope_min_distance,
            "epitope_min_atom_distance": epitope_atom_nearest["distance"],
            "epitope_nearest_antibody_residue": epitope_ca_nearest["query_residue"],
            "epitope_nearest_target_residue": epitope_ca_nearest["target_residue"],
            "target_contact_count": int(np.sum(min_target_distances < target_contact_distance_threshold)),
            "target_min_distance": float(np.min(min_target_distances)),
            "target_min_atom_distance": target_atom_nearest["distance"],
            "target_nearest_antibody_residue": target_ca_nearest["query_residue"],
            "target_nearest_target_residue": target_ca_nearest["target_residue"],
        }
    return loop_metrics


def per_hotspot_metrics(
    antibody_ca,
    antibody_atoms,
    target_ca,
    target_atoms,
    epitope_residue_numbers: set[int],
    epitope_contact_distance_threshold: float,
) -> tuple[dict[str, dict[str, Any]], int]:
    hotspot_metrics: dict[str, dict[str, Any]] = {}
    covered_count = 0
    if not epitope_residue_numbers:
        return hotspot_metrics, covered_count

    for residue_id in sorted(epitope_residue_numbers):
        hotspot_ca = target_ca[target_ca.res_id == residue_id]
        hotspot_atoms = target_atoms[target_atoms.res_id == residue_id]
        if len(hotspot_ca) == 0:
            continue
        label = format_residue_label(hotspot_ca[0])
        ca_nearest = nearest_pair_details(antibody_ca, hotspot_ca)
        atom_nearest = nearest_pair_details(antibody_atoms, hotspot_atoms) if len(hotspot_atoms) > 0 else {
            "distance": None,
            "query_residue": None,
            "target_residue": None,
            "query_atom": None,
            "target_atom": None,
        }
        ca_min = ca_nearest["distance"]
        if ca_min is not None and float(ca_min) < float(epitope_contact_distance_threshold):
            covered_count += 1
        hotspot_metrics[label] = {
            "ca_min_distance": ca_min,
            "atom_min_distance": atom_nearest["distance"],
            "nearest_antibody_residue": ca_nearest["query_residue"],
            "nearest_antibody_atom": atom_nearest["query_atom"],
        }
    return hotspot_metrics, covered_count


def infer_antibody_chains(all_chains: list[str], antibody_chain_hint: str | None) -> list[str]:
    antibody_chains: list[str] = []
    for chain_id in ["H", "L"]:
        if chain_id in all_chains and chain_id not in antibody_chains:
            antibody_chains.append(chain_id)

    if not antibody_chains:
        ordered_hints: list[str] = []
        if antibody_chain_hint:
            ordered_hints.extend([part.strip() for part in antibody_chain_hint.split(",") if part.strip()])
        ordered_hints.append("A")
        for chain_id in ordered_hints:
            if chain_id in all_chains and chain_id not in antibody_chains:
                antibody_chains.append(chain_id)

    return antibody_chains


def infer_target_chain(
    all_chains: list[str],
    antibody_chain_ids: list[str],
    target_chain_hint: str | None,
    epitope_residues: list[str],
) -> str | None:
    if target_chain_hint and target_chain_hint in all_chains:
        return target_chain_hint

    for res_spec in epitope_residues:
        res_spec = res_spec.strip()
        if not res_spec:
            continue
        if res_spec[0].isalpha():
            hinted_chain = res_spec[0]
            if hinted_chain in all_chains and hinted_chain not in antibody_chain_ids:
                return hinted_chain

    non_antibody_chains = [chain_id for chain_id in all_chains if chain_id not in antibody_chain_ids]
    for preferred_chain in ["B", "T"]:
        if preferred_chain in non_antibody_chains:
            return preferred_chain

    return non_antibody_chains[0] if non_antibody_chains else None


def map_epitope_residue_numbers(
    epitope_residues: list[str],
    design_target_ca,
    target_chain_id: str,
    reference_target_pdb: Path | None,
    reference_target_chain: str | None,
) -> tuple[set[int], str]:
    direct_numbers = {
        resnum
        for chain_id, resnum in parse_residue_specs(epitope_residues)
        if chain_id in (None, target_chain_id)
    }
    if direct_numbers and np.isin(design_target_ca.res_id, list(direct_numbers)).any():
        return direct_numbers, "direct"

    if reference_target_pdb is None or not reference_target_pdb.exists():
        return direct_numbers, "missing_reference"

    reference_structure = load_structure(reference_target_pdb)
    reference_chains = [str(chain_id) for chain_id in np.unique(reference_structure.chain_id)]
    reference_specs = parse_residue_specs(epitope_residues)
    reference_chain = reference_target_chain if reference_target_chain in reference_chains else None
    if reference_chain is None:
        for chain_id, _resnum in reference_specs:
            if chain_id and chain_id in reference_chains:
                reference_chain = chain_id
                break
    if reference_chain is None and len(reference_chains) == 1:
        reference_chain = reference_chains[0]
    if reference_chain is None:
        return direct_numbers, "reference_chain_unresolved"

    reference_target_ca = reference_structure[
        (reference_structure.chain_id == reference_chain) & (reference_structure.atom_name == "CA")
    ]
    if len(reference_target_ca) == 0:
        return direct_numbers, "reference_target_missing"

    reference_order = unique_residue_ids(reference_target_ca)
    design_order = unique_residue_ids(design_target_ca)
    if not reference_order or not design_order:
        return direct_numbers, "reference_or_design_empty"

    ordinal_map = {res_id: idx for idx, res_id in enumerate(reference_order)}
    mapped_numbers: set[int] = set()
    for chain_id, resnum in reference_specs:
        if chain_id not in (None, reference_chain):
            continue
        idx = ordinal_map.get(resnum)
        if idx is None or idx >= len(design_order):
            continue
        mapped_numbers.add(design_order[idx])

    if mapped_numbers:
        return mapped_numbers, "reference_order"

    return direct_numbers, "reference_mapping_failed"


def compute_geometry_metrics(
    pdb_path: Path,
    epitope_residues: list[str],
    antibody_chain: str | None,
    target_chain: str | None,
    target_contact_distance_threshold: float,
    epitope_contact_distance_threshold: float,
    reference_target_pdb: Path | None,
) -> dict[str, Any]:
    structure = load_structure(pdb_path)
    all_chains = [str(chain_id) for chain_id in np.unique(structure.chain_id)]

    antibody_chain_ids = infer_antibody_chains(all_chains, antibody_chain)
    target_chain_id = infer_target_chain(all_chains, antibody_chain_ids, target_chain, epitope_residues)
    if target_chain_id in antibody_chain_ids:
        antibody_chain_ids = [chain_id for chain_id in antibody_chain_ids if chain_id != target_chain_id]

    if not antibody_chain_ids:
        raise ValueError(f"No antibody chains found in structure. Chains: {all_chains}")
    if not target_chain_id:
        raise ValueError(f"No target chain found in structure. Chains: {all_chains}")

    antibody_ca = structure[np.isin(structure.chain_id, antibody_chain_ids) & (structure.atom_name == "CA")]
    target_ca = structure[(structure.chain_id == target_chain_id) & (structure.atom_name == "CA")]
    antibody_atoms = structure[np.isin(structure.chain_id, antibody_chain_ids)]
    target_atoms = structure[structure.chain_id == target_chain_id]
    if len(antibody_ca) == 0:
        raise ValueError(f"No antibody CA atoms found in chains {antibody_chain_ids}")
    if len(target_ca) == 0:
        raise ValueError(f"No target CA atoms found in chain {target_chain_id}")

    loop_residue_ids = parse_hlt_loop_residue_ids(pdb_path)

    antibody_coords = antibody_ca.coord
    target_coords = target_ca.coord
    antibody_ca_rog = calculate_radius_of_gyration(antibody_coords)
    pairwise_target_distances = np.linalg.norm(
        antibody_coords[:, None, :] - target_coords[None, :, :],
        axis=2,
    )
    min_target_distances = np.min(pairwise_target_distances, axis=1)
    target_ca_nearest = nearest_pair_details(antibody_ca, target_ca)
    target_atom_nearest = nearest_pair_details(antibody_atoms, target_atoms)

    epitope_residue_numbers, epitope_mapping_mode = map_epitope_residue_numbers(
        epitope_residues,
        design_target_ca=target_ca,
        target_chain_id=target_chain_id,
        reference_target_pdb=reference_target_pdb,
        reference_target_chain=target_chain,
    )
    epitope_centroid_distance: float | None = None
    epitope_residue_count = 0
    epitope_contact_count = 0
    epitope_min_distance: float | None = None
    epitope_ca_nearest = {
        "distance": None,
        "query_residue": None,
        "target_residue": None,
        "query_atom": None,
        "target_atom": None,
    }
    epitope_atom_nearest = {
        "distance": None,
        "query_residue": None,
        "target_residue": None,
        "query_atom": None,
        "target_atom": None,
    }
    epitope_ca = target_ca[:0]
    epitope_atoms = target_atoms[:0]
    if epitope_residue_numbers:
        epitope_ca = target_ca[np.isin(target_ca.res_id, list(epitope_residue_numbers))]
        epitope_residue_count = int(len(epitope_ca))
        if epitope_residue_count > 0:
            epitope_coords = epitope_ca.coord
            epitope_pairwise_distances = np.linalg.norm(
                antibody_coords[:, None, :] - epitope_coords[None, :, :],
                axis=2,
            )
            min_epitope_distances = np.min(epitope_pairwise_distances, axis=1)
            epitope_contact_count = int(np.sum(min_epitope_distances < epitope_contact_distance_threshold))
            epitope_min_distance = float(np.min(min_epitope_distances))
            antibody_centroid = np.mean(antibody_coords, axis=0)
            epitope_centroid = np.mean(epitope_ca.coord, axis=0)
            epitope_centroid_distance = float(np.linalg.norm(antibody_centroid - epitope_centroid))
            epitope_ca_nearest = nearest_pair_details(antibody_ca, epitope_ca)
            epitope_atoms = target_atoms[np.isin(target_atoms.res_id, list(epitope_residue_numbers))]
            if len(epitope_atoms) > 0:
                epitope_atom_nearest = nearest_pair_details(antibody_atoms, epitope_atoms)

    loop_metrics = per_loop_geometry_metrics(
        antibody_ca=antibody_ca,
        antibody_atoms=antibody_atoms,
        epitope_ca=epitope_ca,
        epitope_atoms=epitope_atoms,
        target_ca=target_ca,
        target_atoms=target_atoms,
        antibody_chain_ids=antibody_chain_ids,
        loop_residue_ids=loop_residue_ids,
        epitope_contact_distance_threshold=epitope_contact_distance_threshold,
        target_contact_distance_threshold=target_contact_distance_threshold,
    )
    hotspot_metrics, hotspot_covered_count = per_hotspot_metrics(
        antibody_ca=antibody_ca,
        antibody_atoms=antibody_atoms,
        target_ca=target_ca,
        target_atoms=target_atoms,
        epitope_residue_numbers=epitope_residue_numbers,
        epitope_contact_distance_threshold=epitope_contact_distance_threshold,
    )

    antibody_target_centroid_distance = float(
        np.linalg.norm(np.mean(antibody_coords, axis=0) - np.mean(target_coords, axis=0))
    )

    return {
        "detected_antibody_chains": ",".join(antibody_chain_ids),
        "detected_target_chain": target_chain_id,
        "antibody_residue_count": int(len(antibody_ca)),
        "target_residue_count": int(len(target_ca)),
        "epitope_residue_count": epitope_residue_count,
        "epitope_mapping_mode": epitope_mapping_mode,
        "epitope_contact_count": epitope_contact_count,
        "epitope_min_distance": epitope_min_distance,
        "epitope_min_atom_distance": epitope_atom_nearest["distance"],
        "epitope_nearest_antibody_residue": epitope_ca_nearest["query_residue"],
        "epitope_nearest_target_residue": epitope_ca_nearest["target_residue"],
        "epitope_nearest_antibody_atom": epitope_atom_nearest["query_atom"],
        "epitope_nearest_target_atom": epitope_atom_nearest["target_atom"],
        "target_contact_count": int(np.sum(min_target_distances < target_contact_distance_threshold)),
        "target_min_distance": float(np.min(min_target_distances)),
        "target_min_atom_distance": target_atom_nearest["distance"],
        "target_nearest_antibody_residue": target_ca_nearest["query_residue"],
        "target_nearest_target_residue": target_ca_nearest["target_residue"],
        "target_nearest_antibody_atom": target_atom_nearest["query_atom"],
        "target_nearest_target_atom": target_atom_nearest["target_atom"],
        "target_contact_distance_threshold": float(target_contact_distance_threshold),
        "epitope_centroid_distance": epitope_centroid_distance,
        "target_centroid_distance": antibody_target_centroid_distance,
        "antibody_ca_rog": antibody_ca_rog,
        "rfa_loop_metrics": loop_metrics,
        "rfa_hotspot_metrics": hotspot_metrics,
        "rfa_hotspot_covered_count": int(hotspot_covered_count),
    }


def screen_design(
    pdb_path: Path,
    epitope_residues: list[str],
    antibody_chain: str | None,
    target_chain: str | None,
    min_contacts: int | None,
    max_epitope_distance: float | None,
    contact_distance_threshold: float,
    min_target_contacts: int | None,
    max_target_distance: float | None,
    max_epitope_centroid_distance: float | None,
    target_contact_distance_threshold: float,
    reference_target_pdb: Path | None,
) -> dict[str, Any]:
    geometry_metrics = compute_geometry_metrics(
        pdb_path,
        epitope_residues=epitope_residues,
        antibody_chain=antibody_chain,
        target_chain=target_chain,
        target_contact_distance_threshold=target_contact_distance_threshold,
        epitope_contact_distance_threshold=contact_distance_threshold,
        reference_target_pdb=reference_target_pdb,
    )
    contact_count = int(geometry_metrics["epitope_contact_count"])
    min_distance = geometry_metrics["epitope_min_distance"]

    passed = True
    reasons: list[str] = []

    if min_contacts is not None and contact_count < min_contacts:
        passed = False
        reasons.append(f"contacts<{min_contacts}")

    if max_epitope_distance is not None:
        if min_distance is None:
            passed = False
            reasons.append("distance_missing")
        elif float(min_distance) > float(max_epitope_distance):
            passed = False
            reasons.append(f"min_distance>{max_epitope_distance}")

    target_contact_count = geometry_metrics["target_contact_count"]
    target_min_distance = geometry_metrics["target_min_distance"]
    epitope_centroid_distance = geometry_metrics["epitope_centroid_distance"]

    if min_target_contacts is not None and int(target_contact_count) < int(min_target_contacts):
        passed = False
        reasons.append(f"target_contacts<{min_target_contacts}")

    if max_target_distance is not None:
        if target_min_distance is None:
            passed = False
            reasons.append("target_distance_missing")
        elif float(target_min_distance) > float(max_target_distance):
            passed = False
            reasons.append(f"target_min_distance>{max_target_distance}")

    if max_epitope_centroid_distance is not None:
        if epitope_centroid_distance is None:
            passed = False
            reasons.append("epitope_centroid_missing")
        elif float(epitope_centroid_distance) > float(max_epitope_centroid_distance):
            passed = False
            reasons.append(f"epitope_centroid>{max_epitope_centroid_distance}")

    return {
        "design_name": pdb_path.stem,
        "pdb_path": str(pdb_path.resolve()),
        "passed_screen": bool(passed),
        "screening_reason": ",".join(reasons) if reasons else "passed",
        **geometry_metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen RFantibody backbones against the selected epitope")
    parser.add_argument("--pdb-dir", required=True, help="Directory containing RFantibody PDBs")
    parser.add_argument("--output-dir", default="screened_output", help="Directory for passed PDBs and summary files")
    parser.add_argument("--summary-json", default="screening_summary.json", help="Path to overall summary JSON")
    parser.add_argument("--epitope-residues", default="", help="Comma-separated residue specs such as A45,A53,A68")
    parser.add_argument("--antibody-chains", default="H", help="Comma-separated antibody chain hints")
    parser.add_argument("--target-chain", default="", help="Optional target chain hint")
    parser.add_argument("--reference-target-pdb", default="", help="Original normalized target structure used to map epitope residues onto RFantibody outputs")
    parser.add_argument("--min-epitope-contacts", type=int, default=None, help="Minimum CA contacts to the selected epitope")
    parser.add_argument("--max-epitope-distance", type=float, default=None, help="Maximum allowed minimum CA distance to the selected epitope")
    parser.add_argument("--contact-distance-threshold", type=float, default=8.0, help="CA contact cutoff in angstroms")
    parser.add_argument("--min-target-contacts", type=int, default=None, help="Minimum loose CA contacts to any target residues")
    parser.add_argument("--max-target-distance", type=float, default=None, help="Maximum allowed minimum CA distance to any target residue")
    parser.add_argument("--max-epitope-centroid-distance", type=float, default=None, help="Maximum antibody-to-epitope centroid distance in angstroms")
    parser.add_argument("--target-contact-distance-threshold", type=float, default=12.0, help="Loose antibody-target CA contact cutoff in angstroms")
    args = parser.parse_args()

    pdb_dir = Path(args.pdb_dir).expanduser().resolve()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    epitope_residues = parse_residue_list(args.epitope_residues)
    antibody_chain = normalize_chain_hint(args.antibody_chains)
    target_chain = (args.target_chain or "").strip() or None
    reference_target_pdb = Path(args.reference_target_pdb).expanduser().resolve() if args.reference_target_pdb else None

    screening_applied = any(
        threshold is not None
        for threshold in (
            args.min_epitope_contacts,
            args.max_epitope_distance,
            args.min_target_contacts,
            args.max_target_distance,
            args.max_epitope_centroid_distance,
        )
    )

    results: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0

    pdb_files = sorted(pdb_dir.glob("*.pdb"))
    if not pdb_files:
        summary = {
            "screening_applied": screening_applied,
            "total_designs": 0,
            "passed_designs": 0,
            "failed_designs": 0,
            "epitope_residues": epitope_residues,
            "reference_target_pdb": str(reference_target_pdb) if reference_target_pdb else None,
            "min_epitope_contacts": args.min_epitope_contacts,
            "max_epitope_distance": args.max_epitope_distance,
            "contact_distance_threshold": args.contact_distance_threshold,
            "min_target_contacts": args.min_target_contacts,
            "max_target_distance": args.max_target_distance,
            "max_epitope_centroid_distance": args.max_epitope_centroid_distance,
            "target_contact_distance_threshold": args.target_contact_distance_threshold,
            "results": [],
        }
        Path(args.summary_json).write_text(json.dumps(summary, indent=2))
        return 0

    for pdb_path in pdb_files:
        try:
            result = screen_design(
                pdb_path=pdb_path,
                epitope_residues=epitope_residues,
                antibody_chain=antibody_chain,
                target_chain=target_chain,
                min_contacts=args.min_epitope_contacts,
                max_epitope_distance=args.max_epitope_distance,
                contact_distance_threshold=args.contact_distance_threshold,
                min_target_contacts=args.min_target_contacts,
                max_target_distance=args.max_target_distance,
                max_epitope_centroid_distance=args.max_epitope_centroid_distance,
                target_contact_distance_threshold=args.target_contact_distance_threshold,
                reference_target_pdb=reference_target_pdb,
            )
        except Exception as exc:
            result = {
                "design_name": pdb_path.stem,
                "pdb_path": str(pdb_path.resolve()),
                "epitope_contact_count": 0,
                "epitope_min_distance": None,
                "epitope_min_atom_distance": None,
                "epitope_nearest_antibody_residue": None,
                "epitope_nearest_target_residue": None,
                "epitope_nearest_antibody_atom": None,
                "epitope_nearest_target_atom": None,
                "epitope_mapping_mode": "error",
                "target_contact_count": 0,
                "target_min_distance": None,
                "target_min_atom_distance": None,
                "target_nearest_antibody_residue": None,
                "target_nearest_target_residue": None,
                "target_nearest_antibody_atom": None,
                "target_nearest_target_atom": None,
                "epitope_centroid_distance": None,
                "target_centroid_distance": None,
                "antibody_ca_rog": None,
                "detected_antibody_chains": "",
                "detected_target_chain": "",
                "antibody_residue_count": 0,
                "target_residue_count": 0,
                "epitope_residue_count": 0,
                "target_contact_distance_threshold": args.target_contact_distance_threshold,
                "passed_screen": False if screening_applied else True,
                "screening_reason": f"error:{exc}",
            }

        results.append(result)
        if result["passed_screen"]:
            passed_count += 1
            shutil.copy2(pdb_path, output_dir / pdb_path.name)
            trb_path = pdb_path.with_suffix(".trb")
            if trb_path.exists():
                shutil.copy2(trb_path, output_dir / trb_path.name)
        else:
            failed_count += 1

    summary_csv = output_dir / "rfantibody_screening_summary.csv"
    with summary_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "design_name",
                "epitope_contact_count",
                "epitope_min_distance",
                "epitope_min_atom_distance",
                "epitope_nearest_antibody_residue",
                "epitope_nearest_target_residue",
                "epitope_nearest_antibody_atom",
                "epitope_nearest_target_atom",
                "epitope_mapping_mode",
                "target_contact_count",
                "target_min_distance",
                "target_min_atom_distance",
                "target_nearest_antibody_residue",
                "target_nearest_target_residue",
                "target_nearest_antibody_atom",
                "target_nearest_target_atom",
                "epitope_centroid_distance",
                "target_centroid_distance",
                "target_contact_distance_threshold",
                "rfa_hotspot_covered_count",
                "antibody_ca_rog",
                "rfa_loop_metrics",
                "rfa_hotspot_metrics",
                "detected_antibody_chains",
                "detected_target_chain",
                "antibody_residue_count",
                "target_residue_count",
                "epitope_residue_count",
                "passed_screen",
                "screening_reason",
                "pdb_path",
            ],
        )
        writer.writeheader()
        for row in results:
            serialized = dict(row)
            for json_key in ("rfa_loop_metrics", "rfa_hotspot_metrics"):
                if serialized.get(json_key) is not None:
                    serialized[json_key] = json.dumps(serialized.get(json_key))
            writer.writerow(serialized)

    summary = {
        "screening_applied": screening_applied,
        "total_designs": len(results),
        "passed_designs": passed_count,
        "failed_designs": failed_count,
        "epitope_residues": epitope_residues,
        "reference_target_pdb": str(reference_target_pdb) if reference_target_pdb else None,
        "min_epitope_contacts": args.min_epitope_contacts,
        "max_epitope_distance": args.max_epitope_distance,
        "contact_distance_threshold": args.contact_distance_threshold,
        "min_target_contacts": args.min_target_contacts,
        "max_target_distance": args.max_target_distance,
        "max_epitope_centroid_distance": args.max_epitope_centroid_distance,
        "target_contact_distance_threshold": args.target_contact_distance_threshold,
        "results": results,
        "summary_csv": str(summary_csv.resolve()),
    }
    Path(args.summary_json).write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
