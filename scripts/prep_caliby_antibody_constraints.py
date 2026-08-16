#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from prep_antibody_constraints import (
    DISULFIDE_POSITIONS,
    FR_CONTACT_POSITIONS,
    compute_fixed_residues,
    estimate_vhh_tetrad_positions,
    get_chain_sequence,
    get_ranges,
    merge_chain_position_maps,
    parse_chain_position_spec,
    parse_hlt_cdr_labels,
    parse_pdb_chains,
    resolve_per_pdb_fixed_positions,
)


def _normalize_constraint_spec(value: str | None) -> str:
    return str(value or "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Caliby-native positional constraints for antibody/nanobody design.")
    parser.add_argument("--input_dir", required=True, help="Directory containing PDB files")
    parser.add_argument("--out_csv", required=True, help="Output CSV file for Caliby")
    parser.add_argument("--design_mode", default="cdr_only", choices=["cdr_only", "cdr_selective", "framework_allowed", "full_design"])
    parser.add_argument("--design_loops", default="H1,H2,H3,L1,L2,L3")
    parser.add_argument("--protect_tetrad", default="true")
    parser.add_argument("--antibody_chains", default="H,L")
    parser.add_argument("--protected_positions", default="")
    parser.add_argument("--extra_fixed_positions", default="")
    parser.add_argument("--extra_fixed_positions_json", default="")
    parser.add_argument("--cdr_positions", default="")
    parser.add_argument("--cdr_positions_by_loop", default="")
    parser.add_argument("--protect_fr_contacts", default="false")
    parser.add_argument("--protect_disulfides", default="true")
    parser.add_argument("--lock_target_chains", default="true")
    parser.add_argument("--lock_antibody_framework", default="true")
    parser.add_argument("--fixed_pos_override_seq", default="")
    parser.add_argument("--pos_restrict_aatype", default="")
    parser.add_argument("--symmetry_pos", default="")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    pdb_files = list(input_dir.glob("*.pdb"))

    design_mode = args.design_mode
    selected_loops = [loop.strip().upper() for loop in args.design_loops.split(",") if loop.strip()]
    protect_tetrad = str(args.protect_tetrad).lower() in {"true", "1", "yes"}
    antibody_chains = [chain.strip().upper() for chain in args.antibody_chains.split(",") if chain.strip()]
    lock_target_chains = str(args.lock_target_chains).lower() in {"true", "1", "yes"}
    lock_antibody_framework = str(args.lock_antibody_framework).lower() in {"true", "1", "yes"}
    protect_fr_contacts = str(args.protect_fr_contacts).lower() in {"true", "1", "yes"}
    protect_disulfides = str(args.protect_disulfides).lower() in {"true", "1", "yes"}

    extra_protected_imgt: list[int] = []
    extra_fixed_by_chain = parse_chain_position_spec(args.extra_fixed_positions)
    per_pdb_extra_fixed_positions: dict[str, dict[str, list[int]]] = {}
    if args.extra_fixed_positions_json:
        try:
            raw_mapping = json.loads(Path(args.extra_fixed_positions_json).read_text(encoding="utf-8"))
            if isinstance(raw_mapping, dict):
                for pdb_name, spec in raw_mapping.items():
                    per_pdb_extra_fixed_positions[str(pdb_name)] = parse_chain_position_spec(str(spec or ""))
        except Exception as exc:
            print(f"Warning: Failed to read extra_fixed_positions_json: {exc}")
    cdr_override_by_chain = parse_chain_position_spec(args.cdr_positions)
    cdr_positions_by_loop = {}
    if args.cdr_positions_by_loop:
        try:
            cdr_positions_by_loop = json.loads(Path(args.cdr_positions_by_loop).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Warning: Failed to read cdr_positions_by_loop: {exc}")

    if args.protected_positions.strip():
        for pos_str in args.protected_positions.split(","):
            try:
                extra_protected_imgt.append(int(pos_str.strip()))
            except ValueError:
                print(f"Warning: Invalid protected position '{pos_str}', skipping")

    if protect_fr_contacts:
        for positions in FR_CONTACT_POSITIONS.values():
            extra_protected_imgt.extend(positions)
    if protect_disulfides:
        extra_protected_imgt.extend(DISULFIDE_POSITIONS)
    extra_protected_imgt = list(set(extra_protected_imgt))

    fixed_pos_override_seq = _normalize_constraint_spec(args.fixed_pos_override_seq)
    pos_restrict_aatype = _normalize_constraint_spec(args.pos_restrict_aatype)
    symmetry_pos = _normalize_constraint_spec(args.symmetry_pos)

    fieldnames = [
        "pdb_key",
        "fixed_pos_seq",
        "fixed_pos_scn",
        "fixed_pos_override_seq",
        "pos_restrict_aatype",
        "symmetry_pos",
    ]
    rows: list[dict[str, str]] = []

    print(f"Processing {len(pdb_files)} PDBs from {input_dir}...")
    for pdb in pdb_files:
        pdb_name = pdb.stem
        chains_data = parse_pdb_chains(pdb)
        effective_extra_fixed_by_chain = merge_chain_position_maps(
            extra_fixed_by_chain,
            resolve_per_pdb_fixed_positions(pdb_name, per_pdb_extra_fixed_positions),
        )

        cdr_dict = parse_hlt_cdr_labels(pdb)
        loop_override = {k: list(map(int, v)) for k, v in cdr_positions_by_loop.items() if v} if cdr_positions_by_loop else {}
        if loop_override:
            cdr_dict = loop_override

        effective_antibody_chains = list(antibody_chains)
        override_chains = [chain for chain, residues in cdr_override_by_chain.items() if residues and chain in chains_data]
        if override_chains and not any(chain in chains_data for chain in effective_antibody_chains):
            effective_antibody_chains = override_chains
        elif effective_antibody_chains and not any(chain in chains_data for chain in effective_antibody_chains):
            has_light_chain_labels = any(cdr_dict.get(loop_id) for loop_id in ("L1", "L2", "L3"))
            inferred_chain_count = 2 if has_light_chain_labels else 1
            preferred_fallback_order: list[str] = []
            for chain_id in ("H", "L", "A", "B"):
                if chain_id in chains_data and chain_id not in preferred_fallback_order:
                    preferred_fallback_order.append(chain_id)
            for chain_id in sorted(chains_data.keys()):
                if chain_id not in preferred_fallback_order:
                    preferred_fallback_order.append(chain_id)
            effective_antibody_chains = preferred_fallback_order[:max(1, min(inferred_chain_count, len(preferred_fallback_order)))]
            print(
                f"  {pdb_name}: Requested antibody chains {antibody_chains} were not present; "
                f"using inferred chains {effective_antibody_chains}"
            )

        has_cdr_labels = any(len(v) > 0 for v in cdr_dict.values()) or bool(cdr_override_by_chain)
        if not has_cdr_labels:
            chains_to_fix = [chain for chain in chains_data.keys() if chain not in effective_antibody_chains] if lock_target_chains else []
            fixed_specs: list[str] = []
            for chain in chains_to_fix:
                for start, end in get_ranges(chains_data[chain]):
                    fixed_specs.append(f"{chain}{start}-{end}")
            for chain, residues in effective_extra_fixed_by_chain.items():
                if chain not in chains_data:
                    continue
                valid = set(residues) & set(chains_data[chain])
                for start, end in get_ranges(valid):
                    fixed_specs.append(f"{chain}{start}-{end}")

            fixed_spec = ",".join(fixed_specs)
            rows.append(
                {
                    "pdb_key": pdb_name,
                    "fixed_pos_seq": fixed_spec,
                    "fixed_pos_scn": fixed_spec,
                    "fixed_pos_override_seq": fixed_pos_override_seq,
                    "pos_restrict_aatype": pos_restrict_aatype,
                    "symmetry_pos": symmetry_pos,
                }
            )
            continue

        if "H" in chains_data:
            chain_h_residues = get_chain_sequence(pdb, "H")
            tetrad_positions = estimate_vhh_tetrad_positions(chain_h_residues, "H")
            if chain_h_residues and extra_protected_imgt:
                sorted_residues = sorted(chain_h_residues.keys())
                offset = sorted_residues[0] - 1 if sorted_residues else 0
                extra_protected_pdb = [pos + offset for pos in extra_protected_imgt if (pos + offset) in chain_h_residues]
            else:
                extra_protected_pdb = []
        else:
            tetrad_positions = []
            extra_protected_pdb = []

        fixed_residues = compute_fixed_residues(
            mode=design_mode,
            chains_data=chains_data,
            cdr_dict=cdr_dict,
            tetrad_positions=tetrad_positions,
            selected_loops=selected_loops,
            protect_tetrad=protect_tetrad,
            antibody_chains=effective_antibody_chains,
            extra_protected_positions=extra_protected_pdb,
            extra_fixed_by_chain=effective_extra_fixed_by_chain,
            cdr_override_by_chain=cdr_override_by_chain,
            lock_target_chains=lock_target_chains,
            lock_antibody_framework=lock_antibody_framework,
        )

        designable_counts = {}
        for chain in effective_antibody_chains:
            if chain not in chains_data:
                continue
            all_residues = set(chains_data[chain])
            fixed = set(fixed_residues.get(chain, []))
            designable_counts[chain] = len(all_residues - fixed)

        if design_mode in {"cdr_only", "cdr_selective"} and designable_counts and all(count == 0 for count in designable_counts.values()):
            raise RuntimeError(
                f"{pdb_name}: no antibody residues remain designable after Caliby constraint generation "
                f"(antibody_chains={effective_antibody_chains}, override_chains={sorted(override_chains)})"
            )

        fixed_specs: list[str] = []
        for chain, residues in sorted(fixed_residues.items()):
            for start, end in get_ranges(residues):
                fixed_specs.append(f"{chain}{start}-{end}")
        fixed_spec = ",".join(fixed_specs)

        rows.append(
            {
                "pdb_key": pdb_name,
                "fixed_pos_seq": fixed_spec,
                "fixed_pos_scn": fixed_spec,
                "fixed_pos_override_seq": fixed_pos_override_seq,
                "pos_restrict_aatype": pos_restrict_aatype,
                "symmetry_pos": symmetry_pos,
            }
        )

        cdr_count = sum(len(v) for v in cdr_dict.values()) or sum(len(v) for v in cdr_override_by_chain.values())
        fixed_count = sum(len(v) for v in fixed_residues.values())
        print(
            f"  {pdb_name}: {cdr_count} CDR residues, {fixed_count} fixed residues, "
            f"designable={designable_counts}, tetrad={tetrad_positions}"
        )

    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote Caliby constraints to {args.out_csv}")


if __name__ == "__main__":
    main()
