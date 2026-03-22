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


def interface_score(pose, interface_residues):
    energies = pose.energies()
    return sum(energies.residue_total_energy(resi) for resi in interface_residues)


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
    parser.add_argument("--output", required=True, help="Output JSON file")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)

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

    interface_res_orig = detect_interface_residues(
        pose_original, antibody_chains_original, antigen_chains_original, args.distance_cutoff
    )
    interface_res_matured = detect_interface_residues(
        pose_matured, antibody_chains_matured, antigen_chains_matured, args.distance_cutoff
    )

    interface_score_orig = interface_score(pose_original, interface_res_orig)
    interface_score_matured = interface_score(pose_matured, interface_res_matured)
    delta_interface = interface_score_matured - interface_score_orig

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
    seq_id = sequence_identity(
        pose_original,
        pose_matured,
        antibody_chains_original,
        antibody_chains_matured,
        chain_remap_b=matured_to_original_chain_map,
    )
    clash_count = count_ca_clashes(pose_matured, cutoff=2.0)

    payload = {
        "interface_score_original": float(interface_score_orig),
        "interface_score_matured": float(interface_score_matured),
        "delta_interface_score": float(delta_interface),
        "rmsd_backbone": None if rmsd_val is None else float(rmsd_val),
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
        "interface_residue_count_original": len(interface_res_orig),
        "interface_residue_count_matured": len(interface_res_matured),
        "distance_cutoff": float(args.distance_cutoff),
    }

    Path(args.output).write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
