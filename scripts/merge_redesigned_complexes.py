#!/usr/bin/env python3
"""Merge redesigned chain outputs back into the original input complex."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List


ATOM_PREFIXES = ("ATOM", "HETATM")


def _select_structure_lines(pdb_path: Path, model_number: int | None = None) -> List[str]:
    lines = [
        line if line.endswith("\n") else f"{line}\n"
        for line in pdb_path.read_text(encoding="utf-8").splitlines()
    ]
    has_models = any(line.startswith("MODEL") for line in lines)
    if not has_models:
        return lines

    header_lines: List[str] = []
    models: dict[int, List[str]] = {}
    current_model: int | None = None

    for line in lines:
        if line.startswith("MODEL"):
            parsed = line[10:].strip()
            current_model = int(parsed) if parsed.isdigit() else (len(models) + 1)
            models.setdefault(current_model, [])
            continue
        if line.startswith("ENDMDL"):
            current_model = None
            continue
        if current_model is None:
            if not line.startswith(("ATOM", "HETATM", "ANISOU", "TER", "END")):
                header_lines.append(line)
            continue
        models.setdefault(current_model, []).append(line)

    if not models:
        return lines

    resolved_model = model_number if model_number in models else next(iter(models.keys()))
    return header_lines + models.get(resolved_model, [])


def _strip_end_records(lines: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        if line.startswith(("END", "ENDMDL")):
            continue
        cleaned.append(line if line.endswith("\n") else f"{line}\n")
    return cleaned


def _load_redesigned_chain_lines(pdb_path: Path) -> List[str]:
    lines: List[str] = []
    with pdb_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith(ATOM_PREFIXES):
                lines.append(raw if raw.endswith("\n") else f"{raw}\n")
    if lines and not any(line.startswith("TER") for line in lines[-2:]):
        lines.append("TER\n")
    return lines


def _merge_complex_lines(original_lines: List[str], redesign_lines: List[str], design_chain: str) -> List[str]:
    merged: List[str] = []
    inserted = False
    last_atom_chain = None

    for raw in original_lines:
        line = raw if raw.endswith("\n") else f"{raw}\n"
        if line.startswith(ATOM_PREFIXES):
            chain_id = line[21].strip()
            if chain_id == design_chain:
                if not inserted:
                    merged.extend(redesign_lines)
                    inserted = True
                last_atom_chain = design_chain
                continue
            last_atom_chain = chain_id
            merged.append(line)
            continue

        if line.startswith("TER") and last_atom_chain == design_chain:
            continue
        if line.startswith(("END", "ENDMDL")):
            continue
        merged.append(line)

    if not inserted:
        merged.extend(redesign_lines)
    merged.append("END\n")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge locally redesigned chains back into the original complex")
    parser.add_argument("--input-dir", required=True, help="Directory containing redesigned PDB/JSON pairs")
    parser.add_argument("--complex-pdb", required=True, help="Original full-complex PDB")
    parser.add_argument("--manifest", required=True, help="Region manifest JSON")
    parser.add_argument("--output-dir", required=True, help="Output directory for merged complexes")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    complex_pdb = Path(args.complex_pdb).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    design_chain = str(manifest.get("design_chain", "")).strip()
    if not design_chain:
        raise SystemExit("Manifest is missing design_chain")

    model_number = manifest.get("model_number")
    if isinstance(model_number, str) and model_number.strip().isdigit():
        model_number = int(model_number.strip())
    elif not isinstance(model_number, int):
        model_number = None

    original_lines = _strip_end_records(_select_structure_lines(complex_pdb, model_number))
    manifest_enrichment = {
        "protein_local_redesign": {
            "design_chain": design_chain,
            "model_number": model_number,
            "context_chains": manifest.get("context_chains", []),
            "region_mode": manifest.get("region_mode"),
            "movable_positions_spec": manifest.get("movable_positions_spec", ""),
            "fixed_positions_spec": manifest.get("fixed_positions_spec", ""),
            "contig_spec": manifest.get("contig_spec", ""),
            "source_complex_pdb": str(complex_pdb),
        }
    }

    pdb_paths = sorted(
        path for path in input_dir.glob("*.pdb")
        if path.resolve() != complex_pdb.resolve()
    )
    if not pdb_paths:
        raise SystemExit(f"No redesigned PDBs found in {input_dir}")

    for redesign_pdb in pdb_paths:
        redesign_lines = _load_redesigned_chain_lines(redesign_pdb)
        if not redesign_lines:
            continue
        merged_lines = _merge_complex_lines(original_lines, redesign_lines, design_chain)
        merged_pdb = output_dir / redesign_pdb.name
        merged_pdb.write_text("".join(merged_lines), encoding="utf-8")

        redesign_json = redesign_pdb.with_suffix(".json")
        merged_json = output_dir / redesign_json.name
        payload = {}
        if redesign_json.exists():
            try:
                payload = json.loads(redesign_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        payload.update(manifest_enrichment)
        merged_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"Merged {redesign_pdb.name} -> {merged_pdb.name}")


if __name__ == "__main__":
    main()
