#!/usr/bin/env python3
"""
Score PPIFlow maturation improvements with interface metrics and QC checks.
"""
import argparse
import json
import math
import re
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


def reconcile_chain_groups_with_selected_positions(
    chains,
    antibody,
    antigen,
    selected_positions,
    fallback_ab_count=None,
):
    """Prefer chain assignments that actually contain the selected movable residues.

    The PPIFlow prep step can resolve antibody chains through ANARCI-aware logic even
    when the workflow params still use symbolic chain labels like H/L. The scorer sees
    only concrete PDB chain IDs. If those concrete selected-position chain IDs disagree
    with the generic antibody/antigen split, selected-loop metrics collapse to zero.
    """
    selected_chains = []
    for chain_id, _resnum in sorted(selected_positions or set(), key=lambda item: (item[0], item[1])):
        if chain_id in chains and chain_id not in selected_chains:
            selected_chains.append(chain_id)

    if not selected_chains:
        return antibody, antigen

    if set(selected_chains).issubset(set(antibody)):
        return antibody, antigen

    requested_count = fallback_ab_count or len(antibody) or len(selected_chains)
    requested_count = max(len(selected_chains), requested_count)
    requested_count = min(requested_count, len(chains))

    remaining = [chain for chain in chains if chain not in selected_chains]
    corrected_antibody = selected_chains + remaining[:max(0, requested_count - len(selected_chains))]
    corrected_antigen = [chain for chain in chains if chain not in corrected_antibody]
    return corrected_antibody, corrected_antigen


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


def interface_score(
    pose,
    ab_chains,
    ag_chains,
    distance_cutoff,
    selected_positions=None,
    position_groups=None,
    binder_chain_remap=None,
):
    scorefxn = pyrosetta.get_fa_scorefxn()
    scorefxn(pose)
    interface_pairs, interface_residues = interface_pairs_within_distance(pose, ab_chains, ag_chains, distance_cutoff)
    total = 0.0
    negative_pair_count = 0
    selected_total = 0.0
    selected_negative_pair_count = 0
    selected_interface_residues = set()
    group_scores = {
        group_name: {
            "score": 0.0,
            "negative_pair_count": 0,
            "interface_residues": set(),
        }
        for group_name in (position_groups or {})
    }
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
            for group_name, position_set in (position_groups or {}).items():
                if binder_position not in position_set:
                    continue
                group_scores[group_name]["score"] += pair_score
                group_scores[group_name]["negative_pair_count"] += 1
                group_scores[group_name]["interface_residues"].add(resi_a)
                group_scores[group_name]["interface_residues"].add(resi_b)
    return {
        "global_score": total,
        "global_interface_residues": interface_residues,
        "global_negative_pair_count": negative_pair_count,
        "selected_score": selected_total if selected_positions else None,
        "selected_interface_residues": sorted(selected_interface_residues) if selected_positions else None,
        "selected_negative_pair_count": selected_negative_pair_count if selected_positions else None,
        "position_groups": {
            group_name: {
                "score": float(group_data["score"]),
                "negative_pair_count": int(group_data["negative_pair_count"]),
                "interface_residues": sorted(group_data["interface_residues"]),
            }
            for group_name, group_data in group_scores.items()
        },
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


def load_loop_residue_map(path_str):
    if not path_str:
        return {}
    try:
        payload = json.loads(Path(path_str).read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    normalized = {}
    for loop_id, residues in payload.items():
        loop_name = str(loop_id).strip().upper()
        if not loop_name:
            continue
        residue_numbers = []
        for residue in residues or []:
            if isinstance(residue, int):
                residue_numbers.append(int(residue))
                continue
            token = str(residue).strip().upper()
            if not token:
                continue
            if token[0].isalpha():
                token = token[1:]
            match = re.match(r"^-?\d+", token)
            if match:
                residue_numbers.append(int(match.group(0)))
        if residue_numbers:
            normalized[loop_name] = sorted(set(residue_numbers))
    return normalized


def build_loop_position_sets(loop_residue_map, antibody_chains, selected_positions):
    if not loop_residue_map:
        return {
            "SELECTED": {
                "positions": set(selected_positions),
                "selected": True,
            }
        } if selected_positions else {}

    heavy_chain = antibody_chains[0] if antibody_chains else "H"
    light_chain = antibody_chains[1] if len(antibody_chains) > 1 else None
    loop_positions = {}
    for loop_id, residues in sorted(loop_residue_map.items()):
        chain_id = heavy_chain if loop_id.startswith("H") else light_chain
        if not chain_id:
            continue
        position_set = {(chain_id, int(resnum)) for resnum in residues}
        if not position_set:
            continue
        loop_positions[loop_id] = {
            "positions": position_set,
            "selected": bool(position_set & selected_positions) if selected_positions else True,
        }
    if not loop_positions and selected_positions:
        loop_positions["SELECTED"] = {
            "positions": set(selected_positions),
            "selected": True,
        }
    return loop_positions


def centroid_distance(coords_a, coords_b):
    if not coords_a or not coords_b:
        return None
    ax = sum(coord.x for coord in coords_a.values()) / len(coords_a)
    ay = sum(coord.y for coord in coords_a.values()) / len(coords_a)
    az = sum(coord.z for coord in coords_a.values()) / len(coords_a)
    bx = sum(coord.x for coord in coords_b.values()) / len(coords_b)
    by = sum(coord.y for coord in coords_b.values()) / len(coords_b)
    bz = sum(coord.z for coord in coords_b.values()) / len(coords_b)
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


def contact_metrics(query_coords, target_coords, distance_cutoff):
    if not query_coords or not target_coords:
        return {
            "contact_count": None,
            "min_distance": None,
            "centroid_distance": None,
        }

    min_distances = []
    target_xyz = list(target_coords.values())
    for query_xyz in query_coords.values():
        min_distance = min(query_xyz.distance(target_xyz_entry) for target_xyz_entry in target_xyz)
        min_distances.append(min_distance)

    return {
        "contact_count": int(sum(distance < distance_cutoff for distance in min_distances)),
        "min_distance": float(min(min_distances)),
        "centroid_distance": centroid_distance(query_coords, target_coords),
    }


def contact_delta(original_value, matured_value):
    if original_value is None or matured_value is None:
        return None
    return int(matured_value - original_value)


def distance_improvement(original_value, matured_value):
    if original_value is None or matured_value is None:
        return None
    return float(original_value - matured_value)


def _loop_signal(metric, prefix):
    return any(metric.get(f"{prefix}_{suffix}") is not None for suffix in ("contact_delta", "distance_delta", "centroid_distance_delta"))


def compute_loop_objective_score(metric, objective_mode):
    mode = (objective_mode or "selected_interface").strip().lower()
    if mode == "loop_epitope" and not _loop_signal(metric, "epitope"):
        mode = "loop_target"
    if mode == "balanced" and not _loop_signal(metric, "epitope"):
        mode = "loop_target"

    interface_term = metric.get("delta_interface_score")
    if interface_term is None:
        interface_term = 0.0
    rmsd_penalty = metric.get("rmsd_backbone") or 0.0

    target_contact_delta = metric.get("target_contact_delta") or 0.0
    target_distance_delta = metric.get("target_distance_delta") or 0.0
    target_centroid_delta = metric.get("target_centroid_distance_delta") or 0.0
    epitope_contact_delta = metric.get("epitope_contact_delta") or 0.0
    epitope_distance_delta = metric.get("epitope_distance_delta") or 0.0
    epitope_centroid_delta = metric.get("epitope_centroid_distance_delta") or 0.0

    if mode == "loop_target":
        return float(
            interface_term
            - 0.75 * target_contact_delta
            - 0.20 * target_distance_delta
            - 0.05 * target_centroid_delta
            + 0.10 * rmsd_penalty
        )
    if mode == "loop_epitope":
        return float(
            interface_term
            - 1.25 * epitope_contact_delta
            - 0.35 * epitope_distance_delta
            - 0.10 * epitope_centroid_delta
            + 0.10 * rmsd_penalty
        )
    if mode == "balanced":
        return float(
            interface_term
            - 0.75 * target_contact_delta
            - 0.20 * target_distance_delta
            - 0.05 * target_centroid_delta
            - 1.25 * epitope_contact_delta
            - 0.35 * epitope_distance_delta
            - 0.10 * epitope_centroid_delta
            + 0.10 * rmsd_penalty
        )
    return float(interface_term)


def compute_overall_objective_score(loop_metrics, objective_mode, selected_delta_interface, global_delta_interface, nonselected_rmsd, clash_count):
    mode = (objective_mode or "selected_interface").strip().lower()
    if mode == "selected_interface" or not loop_metrics:
        base_score = selected_delta_interface if selected_delta_interface is not None else global_delta_interface
        return None if base_score is None else float(base_score)

    preferred_scores = [
        metric["objective_score"]
        for metric in loop_metrics.values()
        if metric.get("selected") and metric.get("objective_score") is not None
    ]
    if not preferred_scores:
        preferred_scores = [
            metric["objective_score"]
            for metric in loop_metrics.values()
            if metric.get("objective_score") is not None
        ]
    if not preferred_scores:
        base_score = selected_delta_interface if selected_delta_interface is not None else global_delta_interface
        return None if base_score is None else float(base_score)

    overall = float(sum(preferred_scores) / len(preferred_scores))
    if nonselected_rmsd is not None:
        overall += 0.10 * float(nonselected_rmsd)
    if clash_count is not None:
        overall += 0.20 * float(clash_count)
    return overall


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
    parser.add_argument("--epitope_residues", default="",
                        help="Comma-separated target epitope/hotspot residues (e.g. A35,A37)")
    parser.add_argument("--selected_positions", default="",
                        help="Comma-separated antibody positions iterated by PPIFlow (e.g. H27-38,H56-65)")
    parser.add_argument("--cdr_positions_by_loop_json", default="",
                        help="Resolved loop-position JSON emitted during PPIFlow preparation")
    parser.add_argument("--objective_mode", default="selected_interface",
                        choices=["selected_interface", "loop_target", "loop_epitope", "balanced"],
                        help="Ranking objective for partial-flow outputs")
    parser.add_argument("--output", required=True, help="Output JSON file")
    args = parser.parse_args()

    antibody_chains = parse_chain_list(args.antibody_chains)
    antigen_chains = parse_chain_list(args.antigen_chains)
    selected_positions = parse_position_spec(args.selected_positions)
    epitope_positions = parse_position_spec(args.epitope_residues)
    loop_residue_map = load_loop_residue_map(args.cdr_positions_by_loop_json)

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
    antibody_chains_original, antigen_chains_original = reconcile_chain_groups_with_selected_positions(
        original_detected_chains,
        antibody_chains_original,
        antigen_chains_original,
        selected_positions,
        fallback_ab_count=len(antibody_chains_original),
    )
    antibody_chains_matured, antigen_chains_matured, matured_detected_chains = resolve_chain_groups(
        pose_matured,
        antibody_chains,
        antigen_chains,
        fallback_ab_count=len(antibody_chains_original),
        fallback_ag_count=len(antigen_chains_original),
    )
    antibody_chains_matured, antigen_chains_matured = reconcile_chain_groups_with_selected_positions(
        matured_detected_chains,
        antibody_chains_matured,
        antigen_chains_matured,
        selected_positions,
        fallback_ab_count=len(antibody_chains_original),
    )
    matured_to_original_chain_map = {
        matured_chain: original_chain
        for original_chain, matured_chain in zip(antibody_chains_original, antibody_chains_matured)
    }
    matured_target_to_original_chain_map = {
        matured_chain: original_chain
        for original_chain, matured_chain in zip(antigen_chains_original, antigen_chains_matured)
    }
    loop_position_specs = build_loop_position_sets(loop_residue_map, antibody_chains_original, selected_positions)
    loop_position_groups = {
        loop_id: loop_info["positions"]
        for loop_id, loop_info in loop_position_specs.items()
    }
    loop_selection_map = {
        loop_id: bool(loop_info["selected"])
        for loop_id, loop_info in loop_position_specs.items()
    }

    interface_score_orig = interface_score(
        pose_original,
        antibody_chains_original,
        antigen_chains_original,
        args.distance_cutoff,
        selected_positions=selected_positions,
        position_groups=loop_position_groups,
    )
    interface_score_matured = interface_score(
        pose_matured,
        antibody_chains_matured,
        antigen_chains_matured,
        args.distance_cutoff,
        selected_positions=selected_positions,
        position_groups=loop_position_groups,
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
    target_coords_orig = extract_chain_coords(pose_original, antigen_chains_original)
    target_coords_matured = extract_chain_coords(
        pose_matured,
        antigen_chains_matured,
        chain_remap=matured_target_to_original_chain_map,
    )
    epitope_coords_orig = filter_coords_by_position_set(target_coords_orig, epitope_positions)
    epitope_coords_matured = filter_coords_by_position_set(target_coords_matured, epitope_positions)
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

    loop_metrics = {}
    for loop_id, position_set in loop_position_groups.items():
        group_orig = interface_score_orig["position_groups"].get(loop_id, {})
        group_matured = interface_score_matured["position_groups"].get(loop_id, {})
        loop_coords_orig = filter_coords_by_position_set(coords_orig, position_set)
        loop_coords_matured = filter_coords_by_position_set(coords_matured, position_set)
        loop_rmsd = rmsd(loop_coords_orig, loop_coords_matured)
        target_loop_orig = contact_metrics(loop_coords_orig, target_coords_orig, args.distance_cutoff)
        target_loop_matured = contact_metrics(loop_coords_matured, target_coords_matured, args.distance_cutoff)
        epitope_loop_orig = contact_metrics(loop_coords_orig, epitope_coords_orig, 8.0)
        epitope_loop_matured = contact_metrics(loop_coords_matured, epitope_coords_matured, 8.0)

        loop_metric = {
            "loop_id": loop_id,
            "selected": bool(loop_selection_map.get(loop_id, False)),
            "position_count": len(position_set),
            "positions": sorted(f"{chain}{resnum}" for chain, resnum in position_set),
            "interface_score_original": float(group_orig.get("score", 0.0)),
            "interface_score_matured": float(group_matured.get("score", 0.0)),
            "delta_interface_score": float(group_matured.get("score", 0.0) - group_orig.get("score", 0.0)),
            "negative_pair_count_original": int(group_orig.get("negative_pair_count", 0)),
            "negative_pair_count_matured": int(group_matured.get("negative_pair_count", 0)),
            "interface_residue_count_original": len(group_orig.get("interface_residues", [])),
            "interface_residue_count_matured": len(group_matured.get("interface_residues", [])),
            "rmsd_backbone": None if loop_rmsd is None else float(loop_rmsd),
            "target_contact_count_original": None if target_loop_orig["contact_count"] is None else int(target_loop_orig["contact_count"]),
            "target_contact_count_matured": None if target_loop_matured["contact_count"] is None else int(target_loop_matured["contact_count"]),
            "target_contact_delta": contact_delta(target_loop_orig["contact_count"], target_loop_matured["contact_count"]),
            "target_min_distance_original": target_loop_orig["min_distance"],
            "target_min_distance_matured": target_loop_matured["min_distance"],
            "target_distance_delta": distance_improvement(target_loop_orig["min_distance"], target_loop_matured["min_distance"]),
            "target_centroid_distance_original": target_loop_orig["centroid_distance"],
            "target_centroid_distance_matured": target_loop_matured["centroid_distance"],
            "target_centroid_distance_delta": distance_improvement(target_loop_orig["centroid_distance"], target_loop_matured["centroid_distance"]),
            "epitope_contact_count_original": None if epitope_loop_orig["contact_count"] is None else int(epitope_loop_orig["contact_count"]),
            "epitope_contact_count_matured": None if epitope_loop_matured["contact_count"] is None else int(epitope_loop_matured["contact_count"]),
            "epitope_contact_delta": contact_delta(epitope_loop_orig["contact_count"], epitope_loop_matured["contact_count"]),
            "epitope_min_distance_original": epitope_loop_orig["min_distance"],
            "epitope_min_distance_matured": epitope_loop_matured["min_distance"],
            "epitope_distance_delta": distance_improvement(epitope_loop_orig["min_distance"], epitope_loop_matured["min_distance"]),
            "epitope_centroid_distance_original": epitope_loop_orig["centroid_distance"],
            "epitope_centroid_distance_matured": epitope_loop_matured["centroid_distance"],
            "epitope_centroid_distance_delta": distance_improvement(epitope_loop_orig["centroid_distance"], epitope_loop_matured["centroid_distance"]),
        }
        loop_metric["objective_score"] = compute_loop_objective_score(loop_metric, args.objective_mode)
        loop_metrics[loop_id] = loop_metric

    primary_loop = None
    primary_loop_metric = None
    ranked_loops = [
        metric
        for metric in loop_metrics.values()
        if metric.get("objective_score") is not None
    ]
    ranked_loops.sort(key=lambda metric: (0 if metric.get("selected") else 1, metric["objective_score"]))
    if ranked_loops:
        primary_loop_metric = ranked_loops[0]
        primary_loop = primary_loop_metric["loop_id"]

    objective_score = compute_overall_objective_score(
        loop_metrics,
        args.objective_mode,
        selected_delta_interface,
        delta_interface,
        nonselected_rmsd_val,
        clash_count,
    )

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
        "loop_metrics": loop_metrics,
        "loop_ids": sorted(loop_metrics.keys()),
        "selected_loop_ids": sorted(loop_id for loop_id, selected in loop_selection_map.items() if selected),
        "primary_loop": primary_loop,
        "primary_loop_rmsd": primary_loop_metric.get("rmsd_backbone") if primary_loop_metric else None,
        "primary_loop_target_contact_delta": primary_loop_metric.get("target_contact_delta") if primary_loop_metric else None,
        "primary_loop_target_distance_delta": primary_loop_metric.get("target_distance_delta") if primary_loop_metric else None,
        "primary_loop_epitope_contact_delta": primary_loop_metric.get("epitope_contact_delta") if primary_loop_metric else None,
        "primary_loop_epitope_distance_delta": primary_loop_metric.get("epitope_distance_delta") if primary_loop_metric else None,
        "objective_mode": args.objective_mode,
        "objective_score": objective_score,
        "target_contact_distance_cutoff": float(args.distance_cutoff),
        "epitope_contact_distance_cutoff": 8.0,
        "epitope_residues": sorted(f"{chain}{resnum}" for chain, resnum in epitope_positions) if epitope_positions else [],
        "interface_energy_method": "negative_interchain_pair_energy_sum",
        "distance_cutoff": float(args.distance_cutoff),
    }

    Path(args.output).write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
