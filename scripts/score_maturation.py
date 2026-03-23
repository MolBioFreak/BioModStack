#!/usr/bin/env python3
"""
Score PPIFlow maturation improvements with interface metrics and QC checks.
"""
import argparse
import json
import math
from pathlib import Path

import pyrosetta


def parse_chain_list(value):
    if not value:
        return []
    return [c.strip() for c in value.split(",") if c.strip()]


def parse_position_spec(value):
    residues = set()
    for token in (value or "").split(","):
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


def get_pdb_key(pose, resi):
    pdb_info = pose.pdb_info()
    return (
        pdb_info.chain(resi),
        pdb_info.number(resi),
        pdb_info.icode(resi).strip(),
    )


def ordered_pose_chains(pose):
    chains = []
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(resi)
        if chain and chain not in chains:
            chains.append(chain)
    return chains


def resolve_chain_groups(pose, requested_ab, requested_ag, fallback_ab_count=None, fallback_ag_count=None):
    chains = ordered_pose_chains(pose)
    antigen = [chain for chain in requested_ag if chain in chains]
    antibody = [chain for chain in requested_ab if chain in chains and chain not in antigen]

    if not antibody:
        remaining_for_antibody = [chain for chain in chains if chain not in antigen]
        requested_count = fallback_ab_count or len(requested_ab) or len(remaining_for_antibody) or 1
        requested_count = max(1, min(requested_count, len(remaining_for_antibody))) if remaining_for_antibody else 0
        antibody = remaining_for_antibody[:requested_count]

    antigen = [chain for chain in requested_ag if chain in chains and chain not in antibody]
    remaining = [chain for chain in chains if chain not in antibody]
    if not antigen:
        requested_count = fallback_ag_count or len(requested_ag) or len(remaining)
        antigen = remaining[:requested_count] if remaining else []

    if not antigen and remaining:
        antigen = remaining

    return antibody, antigen, chains


def pose_residue_map(pose):
    mapping = {}
    for resi in range(1, pose.total_residue() + 1):
        key = get_pdb_key(pose, resi)
        mapping[key] = resi
    return mapping


def detect_interface_residues(pose, ab_chains, ag_chains, distance_cutoff):
    pdb_info = pose.pdb_info()
    ab_residues = []
    ag_residues = []
    for i in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(i)
        if chain in ab_chains:
            ab_residues.append(i)
        elif chain in ag_chains:
            ag_residues.append(i)

    interface_ab = set()
    interface_ag = set()
    for i in ab_residues:
        res_i = pose.residue(i)
        xyz_i = res_i.nbr_atom_xyz()
        for j in ag_residues:
            res_j = pose.residue(j)
            xyz_j = res_j.nbr_atom_xyz()
            if xyz_i.distance(xyz_j) <= distance_cutoff:
                interface_ab.add(i)
                interface_ag.add(j)
    
    return sorted(list(interface_ab | interface_ag))


def interface_pairs_within_distance(pose, ab_chains, ag_chains, distance_cutoff):
    pdb_info = pose.pdb_info()
    ab_residues = []
    ag_residues = []
    for i in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(i)
        if chain in ab_chains:
            ab_residues.append(i)
        elif chain in ag_chains:
            ag_residues.append(i)

    pairs = []
    interface_ab = set()
    interface_ag = set()
    for i in ab_residues:
        res_i = pose.residue(i)
        xyz_i = res_i.nbr_atom_xyz()
        for j in ag_residues:
            res_j = pose.residue(j)
            xyz_j = res_j.nbr_atom_xyz()
            if xyz_i.distance(xyz_j) <= distance_cutoff:
                pairs.append((i, j))
                interface_ab.add(i)
                interface_ag.add(j)
    return pairs, sorted(list(interface_ab | interface_ag))


def pair_energy_total(scorefxn, pose, resi_a, resi_b):
    if not hasattr(scorefxn, "eval_ci_2b") or not hasattr(scorefxn, "eval_cd_2b"):
        raise RuntimeError("ScoreFunction does not expose eval_ci_2b/eval_cd_2b; cannot compute strict pair energies")
    emap = pyrosetta.rosetta.core.scoring.EMapVector()
    scorefxn.eval_ci_2b(pose.residue(resi_a), pose.residue(resi_b), pose, emap)
    scorefxn.eval_cd_2b(pose.residue(resi_a), pose.residue(resi_b), pose, emap)
    return float(emap.dot(scorefxn.weights()))


def interface_score(pose, ab_chains, ag_chains, distance_cutoff, selected_positions=None, binder_chain_remap=None):
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose)
    interface_pairs, interface_residues = interface_pairs_within_distance(pose, ab_chains, ag_chains, distance_cutoff)
    total = 0.0
    negative_pair_count = 0
    selected_total = 0.0
    selected_negative_pair_count = 0
    selected_interface_residues = set()
    for resi_a, resi_b in interface_pairs:
        pair_score = pair_energy_total(scorefxn, pose, resi_a, resi_b)
        binder_key = get_pdb_key(pose, resi_a)
        binder_position = (
            binder_chain_remap.get(binder_key[0], binder_key[0]) if binder_chain_remap else binder_key[0],
            binder_key[1],
        )
        if pair_score < 0:
            total += pair_score
            negative_pair_count += 1
            if selected_positions and binder_position in selected_positions:
                selected_total += pair_score
                selected_negative_pair_count += 1
                selected_interface_residues.add(resi_a)
                selected_interface_residues.add(resi_b)
    return {
        "global_score": total,
        "global_interface_residues": interface_residues,
        "global_negative_pair_count": negative_pair_count,
        "selected_score": selected_total if selected_positions else None,
        "selected_interface_residues": sorted(selected_interface_residues) if selected_positions else None,
        "selected_negative_pair_count": selected_negative_pair_count if selected_positions else None,
    }


def extract_chain_coords(pose, chain_ids, chain_remap=None):
    coords = {}
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(resi)
        if chain not in chain_ids:
            continue
        if not pose.residue(resi).has("CA"):
            continue
        key = get_pdb_key(pose, resi)
        if chain_remap and key[0] in chain_remap:
            key = (chain_remap[key[0]], key[1], key[2])
        coords[key] = pose.residue(resi).atom("CA").xyz()
    return coords


def filter_coords_by_position_set(coords, position_set, invert=False):
    if not position_set:
        return dict(coords) if invert else {}
    filtered = {}
    for key, value in coords.items():
        membership = (key[0], key[1]) in position_set
        if invert:
            membership = not membership
        if membership:
            filtered[key] = value
    return filtered


def extract_chain_coords_by_order(pose, chain_ids, chain_remap=None):
    coords = {}
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(resi)
        if chain not in chain_ids:
            continue
        if not pose.residue(resi).has("CA"):
            continue
        mapped_chain = chain_remap.get(chain, chain) if chain_remap else chain
        coords.setdefault(mapped_chain, []).append(pose.residue(resi).atom("CA").xyz())
    return coords


def extract_chain_sequences_by_order(pose, chain_ids, chain_remap=None):
    sequences = {}
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(resi)
        if chain not in chain_ids:
            continue
        mapped_chain = chain_remap.get(chain, chain) if chain_remap else chain
        sequences.setdefault(mapped_chain, []).append(pose.residue(resi).name1())
    return sequences


def rmsd(coords_a, coords_b, ordered_a=None, ordered_b=None):
    shared_keys = [k for k in coords_a if k in coords_b]
    if not shared_keys:
        if not ordered_a or not ordered_b:
            return None
        total = 0.0
        count = 0
        for chain_id in ordered_a:
            chain_a = ordered_a.get(chain_id, [])
            chain_b = ordered_b.get(chain_id, [])
            shared_count = min(len(chain_a), len(chain_b))
            for idx in range(shared_count):
                a = chain_a[idx]
                b = chain_b[idx]
                total += (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2
                count += 1
        if count == 0:
            return None
        return math.sqrt(total / count)
    total = 0.0
    for key in shared_keys:
        a = coords_a[key]
        b = coords_b[key]
        total += (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2
    return math.sqrt(total / len(shared_keys))


def sequence_identity(pose_a, pose_b, chain_ids_a, chain_ids_b, chain_remap_b=None):
    map_a = pose_residue_map(pose_a)
    map_b_raw = pose_residue_map(pose_b)
    map_b = {}
    for key, resi in map_b_raw.items():
        if key[0] not in chain_ids_b:
            continue
        mapped_key = key
        if chain_remap_b and key[0] in chain_remap_b:
            mapped_key = (chain_remap_b[key[0]], key[1], key[2])
        map_b[mapped_key] = resi
    shared_keys = [k for k in map_a if k in map_b and k[0] in chain_ids_a]
    if not shared_keys:
        seqs_a = extract_chain_sequences_by_order(pose_a, chain_ids_a)
        seqs_b = extract_chain_sequences_by_order(pose_b, chain_ids_b, chain_remap=chain_remap_b)
        matches = 0
        total = 0
        for chain_id in seqs_a:
            chain_a = seqs_a.get(chain_id, [])
            chain_b = seqs_b.get(chain_id, [])
            shared_count = min(len(chain_a), len(chain_b))
            if shared_count == 0:
                continue
            for idx in range(shared_count):
                if chain_a[idx] == chain_b[idx]:
                    matches += 1
            total += shared_count
        return (matches / total) if total else None
    matches = 0
    for key in shared_keys:
        res_a = pose_a.residue(map_a[key]).name1()
        res_b = pose_b.residue(map_b[key]).name1()
        if res_a == res_b:
            matches += 1
    return matches / len(shared_keys)


def count_ca_clashes(pose, cutoff):
    coords = []
    for resi in range(1, pose.total_residue() + 1):
        if pose.residue(resi).has("CA"):
            coords.append(pose.residue(resi).atom("CA").xyz())
    clash_count = 0
    for i in range(len(coords)):
        for j in range(i + 2, len(coords)):
            if coords[i].distance(coords[j]) < cutoff:
                clash_count += 1
    return clash_count


def main():
    parser = argparse.ArgumentParser(description="Score PPIFlow maturation improvements")
    parser.add_argument("--original_pdb", required=True, help="Original complex PDB")
    parser.add_argument("--matured_pdb", required=True, help="Matured complex PDB")
    parser.add_argument("--antibody_chains", default="H,L",
                        help="Comma-separated antibody chain IDs")
    parser.add_argument("--antigen_chains", default="",
                        help="Comma-separated antigen chain IDs")
    parser.add_argument("--distance_cutoff", type=float, default=8.0,
                        help="Interface distance cutoff (A)")
    parser.add_argument("--selected_positions", default="",
                        help="Comma-separated antibody positions iterated by PPIFlow (e.g. H27-38,H56-65)")
    parser.add_argument("--output", required=True, help="Output JSON file")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)
    selected_positions = parse_position_spec(args.selected_positions)

    pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1")
    pose_original = pyrosetta.pose_from_pdb(args.original_pdb)
    pose_matured = pyrosetta.pose_from_pdb(args.matured_pdb)
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose_original)
    scorefxn(pose_matured)

    antibody_chains_original, antigen_chains_original, original_detected_chains = resolve_chain_groups(
        pose_original,
        antibody_chains,
        antigen_chains,
    )
    antibody_chains_matured, antigen_chains_matured, matured_detected_chains = resolve_chain_groups(
        pose_matured,
        antibody_chains,
        antigen_chains,
        fallback_ab_count=len(antibody_chains_original),
        fallback_ag_count=len(antigen_chains_original),
    )
    matured_to_original_chain_map = {
        matured_chain: original_chain
        for original_chain, matured_chain in zip(antibody_chains_original, antibody_chains_matured)
    }

    interface_score_orig = interface_score(
        pose_original,
        antibody_chains_original,
        antigen_chains_original,
        args.distance_cutoff,
        selected_positions=selected_positions,
    )
    interface_score_matured = interface_score(
        pose_matured,
        antibody_chains_matured,
        antigen_chains_matured,
        args.distance_cutoff,
        selected_positions=selected_positions,
        binder_chain_remap=matured_to_original_chain_map,
    )
    delta_interface = interface_score_matured["global_score"] - interface_score_orig["global_score"]
    selected_delta_interface = None
    if interface_score_orig["selected_score"] is not None and interface_score_matured["selected_score"] is not None:
        selected_delta_interface = interface_score_matured["selected_score"] - interface_score_orig["selected_score"]

    coords_orig = extract_chain_coords(pose_original, antibody_chains_original)
    coords_orig_ordered = extract_chain_coords_by_order(pose_original, antibody_chains_original)
    coords_matured = extract_chain_coords(
        pose_matured,
        antibody_chains_matured,
        chain_remap=matured_to_original_chain_map,
    )
    coords_matured_ordered = extract_chain_coords_by_order(
        pose_matured,
        antibody_chains_matured,
        chain_remap=matured_to_original_chain_map,
    )
    rmsd_val = rmsd(
        coords_orig,
        coords_matured,
        ordered_a=coords_orig_ordered,
        ordered_b=coords_matured_ordered,
    )
    selected_coords_orig = filter_coords_by_position_set(coords_orig, selected_positions)
    selected_coords_matured = filter_coords_by_position_set(coords_matured, selected_positions)
    selected_rmsd_val = rmsd(selected_coords_orig, selected_coords_matured)
    nonselected_coords_orig = filter_coords_by_position_set(coords_orig, selected_positions, invert=True)
    nonselected_coords_matured = filter_coords_by_position_set(coords_matured, selected_positions, invert=True)
    nonselected_rmsd_val = rmsd(nonselected_coords_orig, nonselected_coords_matured)
    seq_id = sequence_identity(
        pose_original,
        pose_matured,
        antibody_chains_original,
        antibody_chains_matured,
        chain_remap_b=matured_to_original_chain_map,
    )
    clash_count = count_ca_clashes(pose_matured, cutoff=2.0)

    payload = {
        "interface_score_original": float(interface_score_orig["global_score"]),
        "interface_score_matured": float(interface_score_matured["global_score"]),
        "delta_interface_score": float(delta_interface),
        "selected_interface_score_original": None if interface_score_orig["selected_score"] is None else float(interface_score_orig["selected_score"]),
        "selected_interface_score_matured": None if interface_score_matured["selected_score"] is None else float(interface_score_matured["selected_score"]),
        "selected_delta_interface_score": None if selected_delta_interface is None else float(selected_delta_interface),
        "rmsd_backbone": None if rmsd_val is None else float(rmsd_val),
        "selected_rmsd_backbone": None if selected_rmsd_val is None else float(selected_rmsd_val),
        "nonselected_rmsd_backbone": None if nonselected_rmsd_val is None else float(nonselected_rmsd_val),
        "sequence_identity": None if seq_id is None else float(seq_id),
        "clash_count_ca": int(clash_count),
        "antibody_chains_requested": antibody_chains,
        "antigen_chains_requested": antigen_chains,
        "antibody_chains_original": antibody_chains_original,
        "antibody_chains_matured": antibody_chains_matured,
        "antigen_chains_original": antigen_chains_original,
        "antigen_chains_matured": antigen_chains_matured,
        "detected_chains_original": original_detected_chains,
        "detected_chains_matured": matured_detected_chains,
        "matured_to_original_chain_map": matured_to_original_chain_map,
        "interface_residue_count_original": len(interface_score_orig["global_interface_residues"]),
        "interface_residue_count_matured": len(interface_score_matured["global_interface_residues"]),
        "negative_pair_count_original": int(interface_score_orig["global_negative_pair_count"]),
        "negative_pair_count_matured": int(interface_score_matured["global_negative_pair_count"]),
        "selected_interface_residue_count_original": (
            len(interface_score_orig["selected_interface_residues"]) if interface_score_orig["selected_interface_residues"] is not None else None
        ),
        "selected_interface_residue_count_matured": (
            len(interface_score_matured["selected_interface_residues"]) if interface_score_matured["selected_interface_residues"] is not None else None
        ),
        "selected_negative_pair_count_original": interface_score_orig["selected_negative_pair_count"],
        "selected_negative_pair_count_matured": interface_score_matured["selected_negative_pair_count"],
        "selected_position_count": len(selected_positions) if selected_positions else 0,
        "selected_positions": sorted(f"{chain}{resnum}" for chain, resnum in selected_positions) if selected_positions else [],
        "interface_energy_method": "negative_interchain_pair_energy_sum",
        "distance_cutoff": float(args.distance_cutoff),
    }

    Path(args.output).write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
