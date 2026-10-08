#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from protenix_constraint_utils import (
    add_pocket_constraint,
    infer_target_pocket_residues,
    parse_chain_csv,
    parse_residue_specs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inject Protenix pocket constraints into an input JSON payload")
    parser.add_argument("--input_json", required=True)
    parser.add_argument("--output_json", required=True)
    parser.add_argument("--binder_chains", default="")
    parser.add_argument("--predicted_target_chains", default="")
    parser.add_argument("--epitope_residues", default="")
    parser.add_argument("--target_pdb", default="")
    parser.add_argument("--source_target_chains", default="")
    parser.add_argument("--target_model_number", type=int, default=None)
    parser.add_argument("--auto-pocket-if-missing", action="store_true")
    parser.add_argument("--auto-pocket-max-residues", type=int, default=24)
    parser.add_argument("--pocket-max-distance", type=float, default=8.0)
    parser.add_argument("--replace-existing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_json).expanduser().resolve()
    output_path = Path(args.output_json).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Expected top-level list in Protenix input JSON")

    binder_chain_ids = parse_chain_csv(args.binder_chains)
    predicted_target_chains = parse_chain_csv(args.predicted_target_chains)
    target_residue_specs = parse_residue_specs(args.epitope_residues)

    if (
        not target_residue_specs
        and args.auto_pocket_if_missing
        and args.target_pdb
        and args.source_target_chains
        and predicted_target_chains
    ):
        target_residue_specs = infer_target_pocket_residues(
            target_pdb=Path(args.target_pdb).expanduser().resolve(),
            source_target_chains=parse_chain_csv(args.source_target_chains),
            predicted_target_chains=predicted_target_chains,
            model_number=args.target_model_number,
            max_residues=int(args.auto_pocket_max_residues),
        )

    applied = 0
    if binder_chain_ids and target_residue_specs:
        for entry in payload:
            if add_pocket_constraint(
                entry,
                binder_chain_ids=binder_chain_ids,
                target_residue_specs=target_residue_specs,
                pocket_max_distance=float(args.pocket_max_distance),
                replace_existing=bool(args.replace_existing),
            ):
                applied += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"[prepare_protenix_constraints] wrote {output_path} "
        f"(binder_chains={binder_chain_ids}, target_residues={len(target_residue_specs)}, applied={applied})",
        flush=True,
    )


if __name__ == "__main__":
    main()
