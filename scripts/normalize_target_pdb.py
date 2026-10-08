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
WATER_RESIDUES = {"HOH", "WAT", "H2O", "DOD"}


def atom_element(line: str) -> str:
    """Return an uppercase PDB element, with an atom-name fallback."""
    if len(line) >= 78:
        element = line[76:78].strip().upper()
        if element:
            return element
    atom_name = line[12:16].strip().upper() if len(line) >= 16 else ""
    atom_name = atom_name.lstrip("0123456789")
    return atom_name[:1]


def clear_altloc(line: str) -> str:
    """Clear the PDB alternate-location column after selecting one conformer."""
    return f"{line[:16]} {line[17:]}" if len(line) > 16 else line


def parse_chain_set(chains_arg: str | None) -> set[str]:
    if not chains_arg:
        return set()
    return {token.strip().upper() for token in chains_arg.split(",") if token.strip()}


def normalize_pdb(
    input_path: Path,
    output_path: Path,
    keep_chains: set[str],
    first_model_only: bool,
    model_number: int | None = None,
    selected_altloc: str = "A",
    keep_hydrogens: bool = False,
    keep_water: bool = False,
    keep_hetero: bool = False,
) -> dict:
    header_lines: list[str] = []
    body_lines: list[str] = []
    atom_count = 0
    kept_models = 0
    retained_altlocs: set[str] = set()
    dropped_altloc_records = 0
    dropped_hydrogen_records = 0
    dropped_water_records = 0
    dropped_hetero_records = 0

    selected_altloc = selected_altloc.strip().upper()
    if len(selected_altloc) > 1:
        raise ValueError("selected_altloc must be blank or one character")

    inside_model = False
    keep_current_model = not first_model_only and model_number is None
    first_model_seen = False
    saw_model_records = False
    selected_model_found = model_number is None

    with input_path.open("r") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")

            if line.startswith("MODEL"):
                saw_model_records = True
                inside_model = True
                parsed_model_number = None
                try:
                    parsed_model_number = int(line[10:].strip())
                except ValueError:
                    parsed_model_number = None

                if model_number is not None:
                    keep_current_model = parsed_model_number == model_number
                    if keep_current_model:
                        selected_model_found = True
                        kept_models = 1
                elif not first_model_only:
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
                if model_number is not None and keep_current_model:
                    break
                if first_model_only and keep_current_model:
                    # Stop after the first model to avoid carrying later models.
                    break
                keep_current_model = not first_model_only and model_number is None
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
                altloc = line[16].upper() if len(line) > 16 else ""
                if altloc not in {"", " ", selected_altloc}:
                    dropped_altloc_records += 1
                    continue
                if line.startswith("ANISOU"):
                    # Coordinate normalization intentionally emits coordinates only.
                    continue
                residue_name = line[17:20].strip().upper() if len(line) >= 20 else ""
                if not keep_hydrogens and atom_element(line) in {"H", "D"}:
                    dropped_hydrogen_records += 1
                    continue
                if line.startswith("HETATM"):
                    if residue_name in WATER_RESIDUES:
                        if not keep_water:
                            dropped_water_records += 1
                            continue
                    elif not keep_hetero:
                        dropped_hetero_records += 1
                        continue
                if altloc not in {"", " "}:
                    retained_altlocs.add(altloc)
                    line = clear_altloc(line)
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

    if model_number is not None and saw_model_records and not selected_model_found:
        raise ValueError(f"Requested model {model_number} not found")
    if model_number is not None and not saw_model_records:
        kept_models = 1

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
        "model_number": model_number,
        "selected_altloc": selected_altloc,
        "retained_altlocs": sorted(retained_altlocs),
        "dropped_altloc_records": dropped_altloc_records,
        "dropped_hydrogen_records": dropped_hydrogen_records,
        "dropped_water_records": dropped_water_records,
        "dropped_hetero_records": dropped_hetero_records,
        "keep_hydrogens": keep_hydrogens,
        "keep_water": keep_water,
        "keep_hetero": keep_hetero,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize target antigen PDB input")
    parser.add_argument("--input", required=True, help="Input PDB path")
    parser.add_argument("--output", required=True, help="Output normalized PDB path")
    parser.add_argument("--chains", default="", help="Comma-separated chain IDs to retain")
    parser.add_argument(
        "--altloc",
        default="A",
        help="Alternate location to retain alongside blank records (default: A)",
    )
    parser.add_argument("--keep-hydrogens", action="store_true", help="Retain hydrogen/deuterium atoms")
    parser.add_argument("--keep-water", action="store_true", help="Retain water HETATM records")
    parser.add_argument("--keep-hetero", action="store_true", help="Retain non-water HETATM records")
    parser.add_argument(
        "--first-model-only",
        action="store_true",
        help="Collapse multi-model PDBs to the first model",
    )
    parser.add_argument(
        "--model-number",
        type=int,
        default=None,
        help="Select a specific MODEL number before stripping MODEL/ENDMDL records",
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
            model_number=args.model_number,
            selected_altloc=args.altloc,
            keep_hydrogens=args.keep_hydrogens,
            keep_water=args.keep_water,
            keep_hetero=args.keep_hetero,
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
