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
import re
import sys
from pathlib import Path

import pyrosetta
from pyrosetta import rosetta


def parse_chain_list(value):
    if not value:
        return []
    return [c.strip() for c in value.split(",") if c.strip()]


def parse_loop_list(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = str(value).replace("[", "").replace("]", "").split(",")
    loops = []
    for item in raw:
        loop = str(item).strip().upper()
        if loop and loop not in loops:
            loops.append(loop)
    return loops


def parse_region_mode(value):
    normalized = str(value or "").strip().lower()
    if normalized == "all_cdrs":
        return "all_cdrs"
    if normalized in {"framework", "framework_only"}:
        return "framework_only"
    if normalized in {"all_antibody", "whole_antibody", "full_antibody"}:
        return "all_antibody"
    return "selected_cdrs"


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


def get_ranges(numbers):
    if not numbers:
        return []
    ordered = sorted(set(int(value) for value in numbers))
    ranges = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append((start, prev))
        start = prev = number
    ranges.append((start, prev))
    return ranges


def parse_antibody_residue_numbers(pdb_path, antibody_chains):
    chains = {chain_id: [] for chain_id in antibody_chains}
    seen = {chain_id: set() for chain_id in antibody_chains}
    with open(pdb_path) as handle:
        for line in handle:
            if not line.startswith("ATOM"):
                continue
            chain_id = line[21].strip()
            if chain_id not in chains:
                continue
            raw_resnum = line[22:26].strip()
            if not raw_resnum.lstrip("-").isdigit():
                continue
            resnum = int(raw_resnum)
            icode = line[26].strip()
            key = (resnum, icode)
            if key in seen[chain_id]:
                continue
            seen[chain_id].add(key)
            chains[chain_id].append(resnum)
    return {chain_id: sorted(set(values)) for chain_id, values in chains.items() if values}


def normalize_loop_residue_map(raw_map):
    normalized = {}
    if not isinstance(raw_map, dict):
        return normalized
    for raw_loop_id, residues in raw_map.items():
        loop_id = str(raw_loop_id or "").strip().upper()
        if not loop_id:
            continue
        values = []
        for residue in residues or []:
            token = str(residue).strip().upper()
            if not token:
                continue
            if token[0].isalpha():
                token = token[1:]
            match = re.match(r"^-?\d+", token)
            if match:
                values.append(int(match.group(0)))
        if values:
            normalized[loop_id] = sorted(set(values))
    return normalized


def load_loop_override_map(cdr_positions_by_loop_path="", manual_cdr_definitions_path=""):
    for path_str in (cdr_positions_by_loop_path, manual_cdr_definitions_path):
        if not path_str:
            continue
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(payload, list):
            manual_map = {}
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                loop_id = str(entry.get("id") or "").strip().upper()
                residues = entry.get("residues") or []
                if loop_id and residues:
                    manual_map[loop_id] = residues
            payload = manual_map
        normalized = normalize_loop_residue_map(payload)
        if normalized:
            return normalized
    return {}


def build_default_loop_map(antibody_chains, residue_numbers_by_chain):
    default_ranges = {
        "H1": (27, 38),
        "H2": (56, 65),
        "H3": (105, 117),
        "L1": (27, 38),
        "L2": (56, 65),
        "L3": (105, 117),
    }
    heavy_chain = antibody_chains[0] if antibody_chains else "H"
    light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None
    loop_map = {}
    for loop_id, (start, end) in default_ranges.items():
        chain_id = heavy_chain if loop_id.startswith("H") else light_chain
        if not chain_id:
            continue
        residues = [
            residue for residue in residue_numbers_by_chain.get(chain_id, [])
            if start <= residue <= end
        ]
        if residues:
            loop_map[loop_id] = residues
    return loop_map


def build_loop_residue_map(
    pdb_path,
    antibody_chains,
    cdr_positions_by_loop_path="",
    manual_cdr_definitions_path="",
):
    override_map = load_loop_override_map(cdr_positions_by_loop_path, manual_cdr_definitions_path)
    if override_map:
        return override_map, None

    try:
        from prep_antibody_constraints import parse_hlt_cdr_labels
    except Exception as exc:  # pragma: no cover - import guard
        parse_error = f"Failed to import HLT parser: {exc}"
        parse_hlt_cdr_labels = None
    else:
        parse_error = None

    if parse_hlt_cdr_labels is not None:
        cdr_dict = parse_hlt_cdr_labels(pdb_path)
        if any(len(v) > 0 for v in cdr_dict.values()):
            return normalize_loop_residue_map(cdr_dict), None

    residue_numbers_by_chain = parse_antibody_residue_numbers(pdb_path, antibody_chains)
    default_map = build_default_loop_map(antibody_chains, residue_numbers_by_chain)
    if default_map:
        return default_map, parse_error
    return {}, parse_error


def loop_map_to_chain_map(loop_map, antibody_chains, selected_loops=None):
    selected = set(parse_loop_list(selected_loops))
    heavy_chain = antibody_chains[0] if antibody_chains else "H"
    light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None
    by_chain = {}
    for loop_id, residues in loop_map.items():
        if selected and loop_id not in selected:
            continue
        chain_id = heavy_chain if loop_id.startswith("H") else light_chain
        if not chain_id or not residues:
            continue
        by_chain.setdefault(chain_id, set()).update(int(value) for value in residues)
    return by_chain


def chain_map_to_spec(chain_map, chain_order):
    tokens = []
    for chain_id in chain_order:
        residues = sorted(set(chain_map.get(chain_id, set())))
        for start, end in get_ranges(residues):
            if start == end:
                tokens.append(f"{chain_id}{start}")
            else:
                tokens.append(f"{chain_id}{start}-{end}")
    return ",".join(tokens)


def build_ppiflow_region_spec(
    pdb_path,
    antibody_chains,
    region_mode,
    selected_loops=None,
    cdr_positions_by_loop_path="",
    manual_cdr_definitions_path="",
):
    residue_numbers_by_chain = parse_antibody_residue_numbers(pdb_path, antibody_chains)
    loop_map, err = build_loop_residue_map(
        pdb_path,
        antibody_chains,
        cdr_positions_by_loop_path=cdr_positions_by_loop_path,
        manual_cdr_definitions_path=manual_cdr_definitions_path,
    )
    all_cdr_chain_map = loop_map_to_chain_map(loop_map, antibody_chains)
    all_cdr_spec = chain_map_to_spec(all_cdr_chain_map, antibody_chains)

    if region_mode == "all_antibody":
        region_chain_map = {
            chain_id: set(residue_numbers_by_chain.get(chain_id, []))
            for chain_id in antibody_chains
        }
    elif region_mode == "framework_only":
        region_chain_map = {}
        for chain_id in antibody_chains:
            all_residues = set(residue_numbers_by_chain.get(chain_id, []))
            cdr_residues = set(all_cdr_chain_map.get(chain_id, set()))
            region_chain_map[chain_id] = all_residues - cdr_residues
    elif region_mode == "all_cdrs":
        region_chain_map = all_cdr_chain_map
    else:
        region_chain_map = loop_map_to_chain_map(loop_map, antibody_chains, selected_loops=selected_loops)
        if not any(region_chain_map.values()):
            region_chain_map = all_cdr_chain_map

    region_spec = chain_map_to_spec(region_chain_map, antibody_chains)
    return region_spec, all_cdr_spec, err


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


def build_cdr_positions_from_hlt(pdb_path, antibody_chains, selected_loops=None):
    try:
        from prep_antibody_constraints import parse_hlt_cdr_labels, get_ranges
    except Exception as exc:  # pragma: no cover - import guard
        return None, f"Failed to import HLT parser: {exc}"

    selected = set(parse_loop_list(selected_loops))
    cdr_dict = parse_hlt_cdr_labels(pdb_path)
    has_labels = any(len(v) > 0 for v in cdr_dict.values())
    if not has_labels:
        return None, None

    heavy_chain = antibody_chains[0] if antibody_chains else "H"
    light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None

    ranges_by_chain = {}
    for loop in ("H1", "H2", "H3"):
        if selected and loop not in selected:
            continue
        residues = cdr_dict.get(loop, [])
        for start, end in get_ranges(residues):
            ranges_by_chain.setdefault(heavy_chain, []).append((start, end))

    if light_chain:
        for loop in ("L1", "L2", "L3"):
            if selected and loop not in selected:
                continue
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


def build_default_cdr_positions(antibody_chains, selected_loops=None):
    # IMGT default ranges (H/L): 27-38, 56-65, 105-117
    default_ranges = [(27, 38), (56, 65), (105, 117)]
    cdr_ranges = []
    selected = parse_loop_list(selected_loops)
    selected_chains = {loop[0] for loop in selected if len(loop) > 1}
    for chain_id in antibody_chains:
        if selected and chain_id.upper() not in selected_chains:
            continue
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
    parser.add_argument("--output_positions", default="",
                        help="Optional path to write PPIFlow movable-region positions string")
    parser.add_argument("--output_cdr_positions", default="",
                        help="Optional path to write CDR positions string")
    parser.add_argument("--region_mode", default="selected_cdrs",
                        help="Movable antibody region: selected_cdrs, all_cdrs, framework_only, all_antibody")
    parser.add_argument("--selected_loops", default="",
                        help="Optional comma-separated CDR loops to retain (e.g. H2,H3)")
    parser.add_argument("--cdr_positions_by_loop_json", default="",
                        help="Optional JSON file mapping loop ID -> residue numbers")
    parser.add_argument("--manual_cdr_definitions_json", default="",
                        help="Optional JSON file containing manual CDR definitions")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)
    selected_loops = parse_loop_list(args.selected_loops)
    region_mode = parse_region_mode(args.region_mode)

    pyrosetta.init("-out:levels all:error -ignore_unrecognized_res 1")
    pose = pyrosetta.pose_from_pdb(args.pdb)
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose)

    detected_chains = get_chain_ids(pose)

    if not antibody_chains:
        print("[ANCHORS] No antibody chains provided.", file=sys.stderr)
        sys.exit(1)

    antibody_chains = [c for c in antibody_chains if c in detected_chains]
    if not antibody_chains:
        if not detected_chains:
            print("[ANCHORS] No chains detected in pose.", file=sys.stderr)
            sys.exit(1)
        antibody_chains = [detected_chains[0]]
        print(
            f"[ANCHORS] Configured antibody chains not found; inferring antibody chain as "
            f"'{antibody_chains[0]}' from detected chains {detected_chains}",
            file=sys.stderr,
        )

    antigen_chains = [c for c in antigen_chains if c in detected_chains and c not in antibody_chains]
    if not antigen_chains:
        antigen_chains = [c for c in detected_chains if c not in antibody_chains]
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
        "region_mode": region_mode,
        "selected_loops": selected_loops,
        "anchors": anchors,
    }

    score_payload = {
        "interface_score": float(interface_score),
        "interface_residue_count": len(interface_residues),
        "anchor_count": len(anchors),
        "energy_threshold": args.energy_threshold,
        "region_mode": region_mode,
        "selected_loops": selected_loops,
    }

    Path(args.output_anchors).write_text(json.dumps(anchor_payload, indent=2))
    Path(args.output_score).write_text(json.dumps(score_payload, indent=2))

    ppiflow_positions, all_cdr_positions, err = build_ppiflow_region_spec(
        args.pdb,
        antibody_chains,
        region_mode,
        selected_loops=selected_loops,
        cdr_positions_by_loop_path=args.cdr_positions_by_loop_json,
        manual_cdr_definitions_path=args.manual_cdr_definitions_json,
    )
    if err:
        print(f"[ANCHORS] {err}", file=sys.stderr)
    if args.output_positions:
        if not ppiflow_positions:
            print(f"[ANCHORS] No residues resolved for region_mode={region_mode}.", file=sys.stderr)
            sys.exit(1)
        Path(args.output_positions).write_text(ppiflow_positions + "\n")
    if args.output_cdr_positions:
        if not all_cdr_positions:
            all_cdr_positions, _fallback_err = build_cdr_positions_from_hlt(args.pdb, antibody_chains)
        if not all_cdr_positions:
            all_cdr_positions = build_default_cdr_positions(antibody_chains)
        Path(args.output_cdr_positions).write_text(all_cdr_positions + "\n")


if __name__ == "__main__":
    main()
