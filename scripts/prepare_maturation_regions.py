#!/usr/bin/env python3
"""Resolve existing native region masks without running repack/anchor analysis."""
import argparse
import json
from pathlib import Path

from identify_anchors import build_loop_residue_map, build_ppiflow_region_spec, parse_chain_list, parse_loop_list


def prepare(pdb, prefix, chains, region, loops, loop_json, manual_json):
    positions, cdrs, error = build_ppiflow_region_spec(
        pdb, parse_chain_list(chains), region, selected_loops=parse_loop_list(loops),
        cdr_positions_by_loop_path=loop_json, manual_cdr_definitions_path=manual_json,
    )
    if not positions:
        raise ValueError(error or f"No movable residues resolved for region_mode={region}")
    mapping, _ = build_loop_residue_map(pdb, parse_chain_list(chains),
        cdr_positions_by_loop_path=loop_json, manual_cdr_definitions_path=manual_json)
    Path(f"{prefix}_ppiflow_positions.txt").write_text(positions + "\n")
    Path(f"{prefix}_cdr_positions.txt").write_text((cdrs or positions) + "\n")
    Path(f"{prefix}_cdr_positions_by_loop.json").write_text(json.dumps(mapping))
    # An explicitly disabled anchor stage means an empty fixed-anchor mask, not
    # an analysis that ran and found no anchors.
    Path(f"{prefix}_anchors.json").write_text(json.dumps({
        "anchors": [], "analysis_status": "not_run", "anchor_selection_method": "not_run"
    }))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdb', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--chains', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--loops', default='')
    parser.add_argument('--loop-json', default='')
    parser.add_argument('--manual-json', default='')
    args = parser.parse_args()
    prepare(args.pdb, args.prefix, args.chains, args.region, args.loops, args.loop_json, args.manual_json)
