#!/usr/bin/env python3
"""
Identify interface anchor residues for PPIFlow maturation.

Produces:
- anchors.json: per-residue energies for interface residues passing threshold
- interface_score.json: aggregate interface score for baseline comparison
- cdr_positions.txt (optional): CDR ranges for PPIFlow partial flow
"""
import argparse
import json
import sys
from pathlib import Path

import pyrosetta
from pyrosetta import rosetta


def parse_chain_list(value):
    if not value:
        return []
    return [c.strip() for c in value.split(",") if c.strip()]


def get_chain_ids(pose):
    pdb_info = pose.pdb_info()
    chains = []
    for i in range(1, pose.total_residue() + 1):
        chain = pdb_info.chain(i)
        if chain not in chains:
            chains.append(chain)
    return chains


def residue_id(pose, resi):
    pdb_info = pose.pdb_info()
    chain = pdb_info.chain(resi)
    resnum = pdb_info.number(resi)
    icode = pdb_info.icode(resi).strip()
    if icode:
        return f"{chain}{resnum}{icode}"
    return f"{chain}{resnum}"


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


def compute_interface_score(pose, interface_residues):
    energies = pose.energies()
    score = 0.0
    for resi in interface_residues:
        score += energies.residue_total_energy(resi)
    return score


def build_cdr_positions_from_hlt(pdb_path, antibody_chains):
    try:
        from prep_antibody_constraints import parse_hlt_cdr_labels, get_ranges
    except Exception as exc:  # pragma: no cover - import guard
        return None, f"Failed to import HLT parser: {exc}"

    cdr_dict = parse_hlt_cdr_labels(pdb_path)
    has_labels = any(len(v) > 0 for v in cdr_dict.values())
    if not has_labels:
        return None, None

    heavy_chain = antibody_chains[0] if antibody_chains else "H"
    light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None

    ranges_by_chain = {}
    for loop in ("H1", "H2", "H3"):
        residues = cdr_dict.get(loop, [])
        for start, end in get_ranges(residues):
            ranges_by_chain.setdefault(heavy_chain, []).append((start, end))

    if light_chain:
        for loop in ("L1", "L2", "L3"):
            residues = cdr_dict.get(loop, [])
            for start, end in get_ranges(residues):
                ranges_by_chain.setdefault(light_chain, []).append((start, end))

    if not ranges_by_chain:
        return None, None

    cdr_ranges = []
    for chain_id, ranges in ranges_by_chain.items():
        for start, end in ranges:
            if start == end:
                cdr_ranges.append(f"{chain_id}{start}")
            else:
                cdr_ranges.append(f"{chain_id}{start}-{end}")

    return ",".join(cdr_ranges), None


def build_default_cdr_positions(antibody_chains):
    # IMGT default ranges (H/L): 27-38, 56-65, 105-117
    default_ranges = [(27, 38), (56, 65), (105, 117)]
    cdr_ranges = []
    for chain_id in antibody_chains:
        for start, end in default_ranges:
            cdr_ranges.append(f"{chain_id}{start}-{end}")
    return ",".join(cdr_ranges)


def main():
    parser = argparse.ArgumentParser(description="Identify interface anchors for PPIFlow")
    parser.add_argument("--pdb", required=True, help="Input complex PDB path")
    parser.add_argument("--antibody_chains", default="H,L",
                        help="Comma-separated antibody chain IDs")
    parser.add_argument("--antigen_chains", default="",
                        help="Comma-separated antigen chain IDs (optional)")
    parser.add_argument("--energy_threshold", type=float, default=-5.0,
                        help="Anchor threshold (REU) for interface residues")
    parser.add_argument("--distance_cutoff", type=float, default=8.0,
                        help="Distance cutoff (A) for interface detection")
    parser.add_argument("--output_anchors", required=True, help="Path to write anchors JSON")
    parser.add_argument("--output_score", required=True, help="Path to write interface score JSON")
    parser.add_argument("--output_cdr_positions", default="",
                        help="Optional path to write CDR positions string")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)

    pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1")
    pose = pyrosetta.pose_from_pdb(args.pdb)
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose)

    if not antibody_chains:
        print("[ANCHORS] No antibody chains provided.", file=sys.stderr)
        sys.exit(1)

    if not antigen_chains:
        antigen_chains = [c for c in get_chain_ids(pose) if c not in antibody_chains]
        if not antigen_chains:
            print("[ANCHORS] No antigen chains detected.", file=sys.stderr)
            sys.exit(1)

    interface_residues = detect_interface_residues(
        pose, antibody_chains, antigen_chains, args.distance_cutoff
    )

    anchors = []
    energies = pose.energies()
    for resi in interface_residues:
        energy = energies.residue_total_energy(resi)
        if energy <= args.energy_threshold:
            anchors.append({
                "chain": pose.pdb_info().chain(resi),
                "resnum": pose.pdb_info().number(resi),
                "icode": pose.pdb_info().icode(resi).strip(),
                "pdb_position": residue_id(pose, resi),
                "aa": pose.residue(resi).name1(),
                "energy": float(energy),
            })

    interface_score = compute_interface_score(pose, interface_residues)

    anchor_payload = {
        "anchor_count": len(anchors),
        "interface_residue_count": len(interface_residues),
        "energy_threshold": args.energy_threshold,
        "anchors": anchors,
    }

    score_payload = {
        "interface_score": float(interface_score),
        "interface_residue_count": len(interface_residues),
        "anchor_count": len(anchors),
        "energy_threshold": args.energy_threshold,
    }

    Path(args.output_anchors).write_text(json.dumps(anchor_payload, indent=2))
    Path(args.output_score).write_text(json.dumps(score_payload, indent=2))

    if args.output_cdr_positions:
        cdr_positions, err = build_cdr_positions_from_hlt(args.pdb, antibody_chains)
        if err:
            print(f"[ANCHORS] {err}", file=sys.stderr)
        if not cdr_positions:
            cdr_positions = build_default_cdr_positions(antibody_chains)
        Path(args.output_cdr_positions).write_text(cdr_positions + "\n")


if __name__ == "__main__":
    main()
