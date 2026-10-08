#!/usr/bin/env python3
"""
Prepare a PPIFlow maturation input by performing interface rotamer enrichment
and selecting antibody anchor residues from Rosetta-style interchain pair energies.

Outputs:
- enriched_complex.pdb: sidechain-repacked complex used as the partial-flow seed
  (backbone shell movement only when --relax_antibody_backbone_shell is explicit)
- anchors.json: effective fixed anchors after excluding movable-region residues
- interface_score.json: baseline and enriched interface metrics
- rotamer_enrichment.json: enrichment shell / anchor-candidate details
- ppiflow_positions.txt: actual movable region for partial flow
- cdr_positions.txt: canonical CDR positions for redesign context
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import pyrosetta
from pyrosetta import rosetta

from identify_anchors import (
    build_ppiflow_region_spec,
    build_loop_residue_map,
    detect_interface_residues,
    get_chain_ids,
    parse_chain_list,
    parse_loop_list,
    parse_region_mode,
    residue_id,
)


ResidueKey = Tuple[str, int, str]


def _residue_key(pose, resi: int) -> ResidueKey:
    pdb_info = pose.pdb_info()
    return (
        pdb_info.chain(resi),
        int(pdb_info.number(resi)),
        (pdb_info.icode(resi) or "").strip(),
    )


def _interface_pairs_within_distance(
    pose,
    antibody_chains: Sequence[str],
    antigen_chains: Sequence[str],
    distance_cutoff: float,
) -> Tuple[List[Tuple[int, int]], List[int]]:
    pdb_info = pose.pdb_info()
    ab_residues: List[int] = []
    ag_residues: List[int] = []
    for i in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(i)
        if chain in antibody_chains:
            ab_residues.append(i)
        elif chain in antigen_chains:
            ag_residues.append(i)

    pairs: List[Tuple[int, int]] = []
    interface_set: Set[int] = set()
    for i in ab_residues:
        res_i = pose.residue(i)
        xyz_i = res_i.nbr_atom_xyz()
        for j in ag_residues:
            res_j = pose.residue(j)
            xyz_j = res_j.nbr_atom_xyz()
            if xyz_i.distance(xyz_j) <= distance_cutoff:
                pairs.append((i, j))
                interface_set.add(i)
                interface_set.add(j)
    return pairs, sorted(interface_set)


def _detect_shell_residues(pose, seed_residues: Iterable[int], shell_distance: float) -> List[int]:
    if shell_distance <= 0:
        return sorted(set(seed_residues))
    residues = list(sorted(set(seed_residues)))
    if not residues:
        return []
    seed_points = [pose.residue(resi).nbr_atom_xyz() for resi in residues]
    shell: Set[int] = set(residues)
    for resi in range(1, pose.total_residue() + 1):
        point = pose.residue(resi).nbr_atom_xyz()
        if any(point.distance(seed_point) <= shell_distance for seed_point in seed_points):
            shell.add(resi)
    return sorted(shell)


def _run_interface_rotamer_enrichment(
    pose,
    shell_residues: Sequence[int],
    antibody_chains: Sequence[str],
    repeats: int = 2,
    relax_antibody_backbone_shell: bool = False,
) -> None:
    scorefxn = pyrosetta.get_fa_scorefxn()
    shell_set = set(int(value) for value in shell_residues)
    move_map = rosetta.core.kinematics.MoveMap()
    move_map.set_bb(False)
    move_map.set_chi(False)
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(resi)
        if resi in shell_set:
            move_map.set_chi(resi, True)
        if relax_antibody_backbone_shell and resi in shell_set and chain in antibody_chains:
            move_map.set_bb(resi, True)
    relax = rosetta.protocols.relax.FastRelax(scorefxn, int(repeats))
    relax.set_movemap(move_map)
    relax.apply(pose)
    scorefxn(pose)


def _pair_energy_total(scorefxn, pose, resi_a: int, resi_b: int) -> float:
    if not hasattr(scorefxn, "eval_ci_2b") or not hasattr(scorefxn, "eval_cd_2b"):
        raise RuntimeError("ScoreFunction does not expose eval_ci_2b/eval_cd_2b; cannot compute strict pair energies")
    emap = rosetta.core.scoring.EMapVector()
    scorefxn.eval_ci_2b(pose.residue(resi_a), pose.residue(resi_b), pose, emap)
    scorefxn.eval_cd_2b(pose.residue(resi_a), pose.residue(resi_b), pose, emap)
    return float(emap.dot(scorefxn.weights()))


def _compute_interface_pair_energy_breakdown(
    pose,
    antibody_chains: Sequence[str],
    antigen_chains: Sequence[str],
    distance_cutoff: float,
) -> Tuple[float, List[int], Dict[ResidueKey, float], List[Dict[str, object]]]:
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose)
    interface_pairs, interface_residues = _interface_pairs_within_distance(
        pose, antibody_chains, antigen_chains, distance_cutoff
    )
    binder_residue_scores: Dict[ResidueKey, float] = {}
    pair_payload: List[Dict[str, object]] = []
    total_score = 0.0
    for binder_resi, target_resi in interface_pairs:
        pair_score = _pair_energy_total(scorefxn, pose, binder_resi, target_resi)
        if pair_score >= 0:
            continue
        binder_key = _residue_key(pose, binder_resi)
        target_key = _residue_key(pose, target_resi)
        binder_residue_scores[binder_key] = binder_residue_scores.get(binder_key, 0.0) + float(pair_score)
        total_score += float(pair_score)
        pair_payload.append({
            "binder_position": residue_id(pose, binder_resi),
            "target_position": residue_id(pose, target_resi),
            "binder_chain": binder_key[0],
            "target_chain": target_key[0],
            "binder_resnum": binder_key[1],
            "target_resnum": target_key[1],
            "pair_score": float(pair_score),
        })
    pair_payload.sort(key=lambda entry: entry["pair_score"])
    return total_score, interface_residues, binder_residue_scores, pair_payload


def _parse_position_spec(position_spec: str) -> Set[Tuple[str, int]]:
    residues: Set[Tuple[str, int]] = set()
    for token in (position_spec or "").split(","):
        token = token.strip()
        if not token:
            continue
        chain_id = token[0]
        raw = token[1:]
        if "-" in raw:
            start_text, end_text = raw.split("-", 1)
            try:
                start = int(start_text)
                end = int(end_text)
            except ValueError:
                continue
            for resnum in range(min(start, end), max(start, end) + 1):
                residues.add((chain_id, int(resnum)))
            continue
        number = ""
        for char in raw:
            if char.isdigit() or (char == "-" and not number):
                number += char
            else:
                break
        if number:
            residues.add((chain_id, int(number)))
    return residues


def _build_anchor_payload(
    pose,
    interface_residues: Sequence[int],
    binder_residue_scores: Dict[ResidueKey, float],
    energy_threshold: float,
    movable_positions: Set[Tuple[str, int]],
    antibody_chains: Sequence[str],
    antigen_chains: Sequence[str],
    region_mode: str,
    selected_loops: Sequence[str],
) -> Dict[str, object]:
    interface_set = set(int(value) for value in interface_residues)
    anchor_candidates = []
    effective_anchors = []
    movable_anchor_candidates = []
    for resi in sorted(interface_set):
        key = _residue_key(pose, resi)
        chain_id, resnum, icode = key
        if chain_id not in antibody_chains:
            continue
        contribution = float(binder_residue_scores.get(key, 0.0))
        if contribution > energy_threshold:
            continue
        record = {
            "chain": chain_id,
            "resnum": resnum,
            "icode": icode,
            "pdb_position": residue_id(pose, resi),
            "aa": pose.residue(resi).name1(),
            "interface_contribution": contribution,
            "chain_role": "antibody",
            "movable_region_member": (chain_id, resnum) in movable_positions,
        }
        anchor_candidates.append(record)
        if record["movable_region_member"]:
            movable_anchor_candidates.append(record)
            continue
        effective_anchors.append(record)

    return {
        "anchor_selection_method": "rotamer_enriched_antibody_negative_pair_energy",
        "anchor_count": len(effective_anchors),
        "anchor_candidate_count": len(anchor_candidates),
        "movable_anchor_candidate_count": len(movable_anchor_candidates),
        "excluded_movable_anchor_count": len(movable_anchor_candidates),
        "anchors_include_movable_positions": False,
        "interface_residue_count": len(interface_set),
        "energy_threshold": float(energy_threshold),
        "region_mode": region_mode,
        "selected_loops": list(selected_loops),
        "anchors": effective_anchors,
        "anchor_candidates": anchor_candidates,
        "movable_anchor_candidates": movable_anchor_candidates,
    }


def _interface_residue_payload(pose, interface_residues: Sequence[int], binder_residue_scores: Dict[ResidueKey, float]) -> List[Dict[str, object]]:
    payload = []
    for resi in sorted(set(int(value) for value in interface_residues)):
        key = _residue_key(pose, resi)
        payload.append({
            "pdb_position": residue_id(pose, resi),
            "chain": key[0],
            "resnum": key[1],
            "icode": key[2],
            "aa": pose.residue(resi).name1(),
            "interface_contribution": float(binder_residue_scores.get(key, 0.0)),
        })
    payload.sort(key=lambda entry: entry["interface_contribution"])
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare PPIFlow maturation input with rotamer enrichment and anchors")
    parser.add_argument("--pdb", required=True, help="Input complex PDB path")
    parser.add_argument("--antibody_chains", default="H,L", help="Comma-separated antibody chain IDs")
    parser.add_argument("--antigen_chains", default="", help="Comma-separated antigen chain IDs")
    parser.add_argument("--energy_threshold", type=float, default=-5.0, help="Anchor threshold on summed negative binder-side pair energy (REU)")
    parser.add_argument("--distance_cutoff", type=float, default=12.0, help="Distance cutoff (A) for interface residue detection")
    parser.add_argument("--region_mode", default="selected_cdrs", help="Movable antibody region")
    parser.add_argument("--selected_loops", default="", help="Selected loop IDs when region_mode=selected_cdrs")
    parser.add_argument("--cdr_positions_by_loop_json", default="", help="Optional JSON file mapping loop ID -> residue numbers")
    parser.add_argument("--manual_cdr_definitions_json", default="", help="Optional JSON file containing manual CDR definitions")
    parser.add_argument("--output_enriched_pdb", required=True, help="Path to write enriched complex PDB")
    parser.add_argument("--output_anchors", required=True, help="Path to write anchors JSON")
    parser.add_argument("--output_score", required=True, help="Path to write interface score JSON")
    parser.add_argument("--output_rotamer_enrichment", required=True, help="Path to write enrichment JSON")
    parser.add_argument("--output_positions", required=True, help="Path to write movable positions")
    parser.add_argument("--output_cdr_positions", required=True, help="Path to write canonical CDR positions")
    parser.add_argument("--output_cdr_positions_by_loop", required=True, help="Path to write resolved loop-position JSON")
    parser.add_argument("--rotamer_enrichment", action="store_true", help="Enable interface rotamer enrichment repacking")
    parser.add_argument("--rotamer_shell_distance", type=float, default=20.0, help="Shell distance (A) around interface residues to repack")
    parser.add_argument(
        "--relax_antibody_backbone_shell",
        action="store_true",
        help="Allow FastRelax backbone movement for antibody residues in the enrichment shell. Default is side-chain-only enrichment.",
    )
    parser.add_argument("--require_anchors", action="store_true", help="Fail if no non-movable anchors are found")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)
    selected_loops = parse_loop_list(args.selected_loops)
    region_mode = parse_region_mode(args.region_mode)

    pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1 -use_input_sc -ex1 -ex2aro")
    input_pose = pyrosetta.pose_from_pdb(args.pdb)
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(input_pose)

    detected_chains = get_chain_ids(input_pose)
    antibody_chains = [chain for chain in antibody_chains if chain in detected_chains]
    if not antibody_chains:
        if not detected_chains:
            raise SystemExit("[PPIFlow] No chains detected in pose")
        antibody_chains = [detected_chains[0]]

    antigen_chains = [chain for chain in antigen_chains if chain in detected_chains and chain not in antibody_chains]
    if not antigen_chains:
        antigen_chains = [chain for chain in detected_chains if chain not in antibody_chains]
        if not antigen_chains:
            raise SystemExit("[PPIFlow] No antigen chains detected in pose")

    ppiflow_positions, all_cdr_positions, err = build_ppiflow_region_spec(
        args.pdb,
        antibody_chains,
        region_mode,
        selected_loops=selected_loops,
        cdr_positions_by_loop_path=args.cdr_positions_by_loop_json,
        manual_cdr_definitions_path=args.manual_cdr_definitions_json,
    )
    if err:
        print(f"[PPIFlow] {err}", file=sys.stderr)
    if not ppiflow_positions:
        raise SystemExit(f"[PPIFlow] No movable residues resolved for region_mode={region_mode}")
    Path(args.output_positions).write_text(ppiflow_positions + "\n")
    Path(args.output_cdr_positions).write_text((all_cdr_positions or ppiflow_positions) + "\n")
    loop_residue_map, _ = build_loop_residue_map(
        args.pdb,
        antibody_chains,
        cdr_positions_by_loop_path=args.cdr_positions_by_loop_json,
        manual_cdr_definitions_path=args.manual_cdr_definitions_json,
    )
    Path(args.output_cdr_positions_by_loop).write_text(json.dumps(loop_residue_map, indent=2))
    movable_positions = _parse_position_spec(ppiflow_positions)

    original_interface_score, original_interface_residues, original_binder_scores, original_pair_scores = _compute_interface_pair_energy_breakdown(
        input_pose,
        antibody_chains,
        antigen_chains,
        args.distance_cutoff,
    )

    enriched_pose = rosetta.core.pose.Pose()
    enriched_pose.assign(input_pose)
    repack_shell_residues = _detect_shell_residues(enriched_pose, original_interface_residues, args.rotamer_shell_distance)
    if args.rotamer_enrichment and repack_shell_residues:
        _run_interface_rotamer_enrichment(
            enriched_pose,
            repack_shell_residues,
            antibody_chains,
            relax_antibody_backbone_shell=bool(args.relax_antibody_backbone_shell),
        )

    enriched_interface_score, enriched_interface_residues, enriched_binder_scores, enriched_pair_scores = _compute_interface_pair_energy_breakdown(
        enriched_pose,
        antibody_chains,
        antigen_chains,
        args.distance_cutoff,
    )
    enriched_pose.dump_pdb(args.output_enriched_pdb)

    anchor_payload = _build_anchor_payload(
        enriched_pose,
        enriched_interface_residues,
        enriched_binder_scores,
        args.energy_threshold,
        movable_positions,
        antibody_chains,
        antigen_chains,
        region_mode,
        selected_loops,
    )
    interface_payload = {
        "interface_score": float(enriched_interface_score),
        "interface_score_original": float(original_interface_score),
        "interface_score_enriched": float(enriched_interface_score),
        "interface_residue_count": len(enriched_interface_residues),
        "interface_residue_count_original": len(original_interface_residues),
        "interface_residue_count_enriched": len(enriched_interface_residues),
        "anchor_count": anchor_payload["anchor_count"],
        "anchor_candidate_count": anchor_payload["anchor_candidate_count"],
        "energy_threshold": float(args.energy_threshold),
        "distance_cutoff": float(args.distance_cutoff),
        "interface_energy_method": "negative_interchain_pair_energy_sum",
        "region_mode": region_mode,
        "selected_loops": list(selected_loops),
    }
    enrichment_payload = {
        "rotamer_enrichment_enabled": bool(args.rotamer_enrichment),
        "enrichment_method": "pyrosetta_fastrelax_chi_shell",
        "relax_antibody_backbone_shell": bool(args.relax_antibody_backbone_shell),
        "backbone_movement_allowed": bool(args.relax_antibody_backbone_shell),
        "anchor_selection_method": anchor_payload["anchor_selection_method"],
        "repack_shell_distance": float(args.rotamer_shell_distance),
        "repacked_residue_count": len(repack_shell_residues),
        "repacked_residues": [residue_id(enriched_pose, resi) for resi in repack_shell_residues],
        "interface_residue_count_original": len(original_interface_residues),
        "interface_residue_count_enriched": len(enriched_interface_residues),
        "interface_residues_original": _interface_residue_payload(input_pose, original_interface_residues, original_binder_scores),
        "interface_residues_enriched": _interface_residue_payload(enriched_pose, enriched_interface_residues, enriched_binder_scores),
        "pair_scores_original": original_pair_scores,
        "pair_scores_enriched": enriched_pair_scores,
        "movable_positions": sorted(ppiflow_positions.split(",")),
        "region_mode": region_mode,
        "selected_loops": list(selected_loops),
    }

    Path(args.output_anchors).write_text(json.dumps(anchor_payload, indent=2))
    Path(args.output_score).write_text(json.dumps(interface_payload, indent=2))
    Path(args.output_rotamer_enrichment).write_text(json.dumps(enrichment_payload, indent=2))

    if args.require_anchors and int(anchor_payload["anchor_count"]) <= 0:
        raise SystemExit(
            "[PPIFlow] No antibody anchor residues passed the strict energy threshold after rotamer enrichment; "
            "relax the threshold, expand the interface shell, or disable strict anchor requirement."
        )


if __name__ == "__main__":
    main()
