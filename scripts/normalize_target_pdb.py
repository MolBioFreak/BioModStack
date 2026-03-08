#!/usr/bin/env python3
"""
Normalize antigen PDB input for RFantibody and related workflows.

Current normalization:
- collapse multi-model PDBs to the first model
- optionally keep only selected chain IDs
- emit a single-model PDB without MODEL/ENDMDL records
"""

import argparse
import sys
from pathlib import Path


HEADER_PREFIXES = (
    "HEADER",
    "TITLE ",
    "COMPND",
    "SOURCE",
    "KEYWDS",
    "EXPDTA",
    "AUTHOR",
    "REMARK",
    "CRYST1",
    "ORIGX1",
    "ORIGX2",
    "ORIGX3",
    "SCALE1",
    "SCALE2",
    "SCALE3",
    "MTRIX1",
    "MTRIX2",
    "MTRIX3",
)

ATOMISH_PREFIXES = ("ATOM  ", "HETATM", "ANISOU")


def parse_chain_set(chains_arg: str | None) -> set[str]:
    if not chains_arg:
        return set()
    return {token.strip().upper() for token in chains_arg.split(",") if token.strip()}


def normalize_pdb(input_path: Path, output_path: Path, keep_chains: set[str], first_model_only: bool) -> dict:
    header_lines: list[str] = []
    body_lines: list[str] = []
    atom_count = 0
    kept_models = 0

    inside_model = False
    keep_current_model = not first_model_only
    first_model_seen = False

    with input_path.open("r") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")

            if line.startswith("MODEL"):
                inside_model = True
                if not first_model_only:
                    keep_current_model = True
                elif not first_model_seen:
                    keep_current_model = True
                    first_model_seen = True
                    kept_models += 1
                else:
                    keep_current_model = False
                continue

            if line.startswith("ENDMDL"):
                inside_model = False
                if first_model_only and keep_current_model:
                    # Stop after the first model to avoid carrying later models.
                    break
                keep_current_model = not first_model_only
                continue

            if line.startswith(HEADER_PREFIXES):
                if not inside_model:
                    header_lines.append(f"{line}\n")
                continue

            if line.startswith(ATOMISH_PREFIXES):
                if inside_model and not keep_current_model:
                    continue
                chain_id = line[21].upper() if len(line) > 21 else ""
                if keep_chains and chain_id not in keep_chains:
                    continue
                body_lines.append(f"{line}\n")
                if line.startswith(("ATOM  ", "HETATM")):
                    atom_count += 1
                continue

            if line.startswith("TER"):
                if inside_model and not keep_current_model:
                    continue
                chain_id = line[21].upper() if len(line) > 21 else ""
                if keep_chains and chain_id and chain_id not in keep_chains:
                    continue
                body_lines.append(f"{line}\n")
                continue

    if not body_lines:
        raise ValueError("No coordinate records remained after normalization")

    if first_model_only and not first_model_seen:
        kept_models = 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as out:
        out.writelines(header_lines)
        out.writelines(body_lines)
        if not body_lines[-1].startswith("END"):
            out.write("END\n")

    return {
        "input": str(input_path),
        "output": str(output_path),
        "chains": sorted(keep_chains) if keep_chains else [],
        "atom_count": atom_count,
        "kept_models": kept_models,
        "first_model_only": first_model_only,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize target antigen PDB input")
    parser.add_argument("--input", required=True, help="Input PDB path")
    parser.add_argument("--output", required=True, help="Output normalized PDB path")
    parser.add_argument("--chains", default="", help="Comma-separated chain IDs to retain")
    parser.add_argument(
        "--first-model-only",
        action="store_true",
        help="Collapse multi-model PDBs to the first model",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        result = normalize_pdb(
            input_path=input_path,
            output_path=output_path,
            keep_chains=parse_chain_set(args.chains),
            first_model_only=args.first_model_only,
        )
    except Exception as exc:
        print(f"Failed to normalize {input_path}: {exc}", file=sys.stderr)
        return 1

    chains_text = ",".join(result["chains"]) if result["chains"] else "all"
    print(
        f"Normalized target PDB -> {result['output']} "
        f"(chains={chains_text}, atoms={result['atom_count']}, kept_models={result['kept_models']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
