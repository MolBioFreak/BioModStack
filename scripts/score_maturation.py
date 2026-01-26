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

    interface_residues = []
    for i in ab_residues:
        res_i = pose.residue(i)
        xyz_i = res_i.nbr_atom_xyz()
        for j in ag_residues:
            res_j = pose.residue(j)
            xyz_j = res_j.nbr_atom_xyz()
            if xyz_i.distance(xyz_j) <= distance_cutoff:
                interface_residues.append(i)
                break
    return interface_residues


def interface_score(pose, interface_residues):
    energies = pose.energies()
    return sum(energies.residue_total_energy(resi) for resi in interface_residues)


def extract_chain_coords(pose, chain_ids):
    coords = {}
    pdb_info = pose.pdb_info()
    for resi in range(1, pose.total_residue() + 1):
        if pdb_info.chain(resi) not in chain_ids:
            continue
        if not pose.residue(resi).has("CA"):
            continue
        key = get_pdb_key(pose, resi)
        coords[key] = pose.residue(resi).atom("CA").xyz()
    return coords


def rmsd(coords_a, coords_b):
    shared_keys = [k for k in coords_a if k in coords_b]
    if not shared_keys:
        return None
    total = 0.0
    for key in shared_keys:
        a = coords_a[key]
        b = coords_b[key]
        total += (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2
    return math.sqrt(total / len(shared_keys))


def sequence_identity(pose_a, pose_b, chain_ids):
    map_a = pose_residue_map(pose_a)
    map_b = pose_residue_map(pose_b)
    shared_keys = [k for k in map_a if k in map_b and k[0] in chain_ids]
    if not shared_keys:
        return None
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

    pyrosetta.init("-out:levels all:error")
    pose_original = pyrosetta.pose_from_pdb(args.original_pdb)
    pose_matured = pyrosetta.pose_from_pdb(args.matured_pdb)
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose_original)
    scorefxn(pose_matured)

    if not antigen_chains:
        antigen_chains = [
            c for c in set([pose_original.pdb_info().chain(i) for i in range(1, pose_original.total_residue() + 1)])
            if c not in antibody_chains
        ]

    interface_res_orig = detect_interface_residues(
        pose_original, antibody_chains, antigen_chains, args.distance_cutoff
    )
    interface_res_matured = detect_interface_residues(
        pose_matured, antibody_chains, antigen_chains, args.distance_cutoff
    )

    interface_score_orig = interface_score(pose_original, interface_res_orig)
    interface_score_matured = interface_score(pose_matured, interface_res_matured)
    delta_interface = interface_score_matured - interface_score_orig

    coords_orig = extract_chain_coords(pose_original, antibody_chains)
    coords_matured = extract_chain_coords(pose_matured, antibody_chains)
    rmsd_val = rmsd(coords_orig, coords_matured)
    seq_id = sequence_identity(pose_original, pose_matured, antibody_chains)
    clash_count = count_ca_clashes(pose_matured, cutoff=2.0)

    payload = {
        "interface_score_original": float(interface_score_orig),
        "interface_score_matured": float(interface_score_matured),
        "delta_interface_score": float(delta_interface),
        "rmsd_backbone": None if rmsd_val is None else float(rmsd_val),
        "sequence_identity": None if seq_id is None else float(seq_id),
        "clash_count_ca": int(clash_count),
    }

    Path(args.output).write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
