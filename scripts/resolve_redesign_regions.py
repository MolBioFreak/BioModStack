#!/usr/bin/env python3
"""
Resolve editable and fixed regions for protein-local redesign.

This script is intentionally protein-generic. It accepts a full input complex,
extracts a single design chain as the RFdiffusion3 seed, derives a motif-style
contig specification for the editable spans, and emits a manifest consumed by
the downstream redesign workflow.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple


@dataclass(frozen=True)
class ResidueKey:
    chain: str
    resnum: int
    icode: str = ""

    @property
    def token(self) -> str:
        return f"{self.chain}{self.resnum}{self.icode}".strip()


@dataclass
class ResidueRecord:
    key: ResidueKey
    atom_lines: List[str] = field(default_factory=list)
    atom_coords: List[Tuple[float, float, float]] = field(default_factory=list)


def parse_chain_list(value: str) -> List[str]:
    return [token.strip() for token in str(value or "").split(",") if token.strip()]


def select_structure_lines(pdb_path: Path, model_number: int | None = None) -> Tuple[List[str], int | None]:
    lines = [
        line if line.endswith("\n") else f"{line}\n"
        for line in pdb_path.read_text(encoding="utf-8").splitlines()
    ]
    has_models = any(line.startswith("MODEL") for line in lines)
    if not has_models:
        return lines, model_number

    header_lines: List[str] = []
    models: "OrderedDict[int, List[str]]" = OrderedDict()
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
        return lines, model_number

    resolved_model = model_number if model_number in models else next(iter(models.keys()))
    return header_lines + models.get(resolved_model, []), resolved_model


def parse_pdb_residues_from_lines(lines: Sequence[str]) -> Dict[str, List[ResidueRecord]]:
    chains: "OrderedDict[str, List[ResidueRecord]]" = OrderedDict()
    current_keys: Dict[str, ResidueKey] = {}

    for line in lines:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        chain = line[21].strip()
        if not chain:
            continue
        raw_resnum = line[22:26].strip()
        if not raw_resnum or not raw_resnum.lstrip("-").isdigit():
            continue
        resnum = int(raw_resnum)
        icode = line[26].strip()
        key = ResidueKey(chain=chain, resnum=resnum, icode=icode)
        if chain not in chains:
            chains[chain] = []
        if current_keys.get(chain) != key:
            chains[chain].append(ResidueRecord(key=key))
            current_keys[chain] = key
        residue = chains[chain][-1]
        residue.atom_lines.append(line)
        try:
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
            residue.atom_coords.append((x, y, z))
        except ValueError:
            continue
    return chains


def parse_manual_ranges(
    value: str,
    design_chain: str,
    available_resnums: Set[int],
) -> Set[int]:
    result: Set[int] = set()
    tokens = [token.strip() for token in str(value or "").split(",") if token.strip()]
    if not tokens:
        raise ValueError("manual_ranges mode requires redesign ranges")

    for token in tokens:
        chain = design_chain
        range_token = token
        if ":" in token:
            raw_chain, raw_range = token.split(":", 1)
            chain = raw_chain.strip() or design_chain
            range_token = raw_range.strip()
        elif token and token[0].isalpha():
            chain = token[0]
            range_token = token[1:]

        match = re.fullmatch(r"(?P<start>-?\d+)(?:-(?P<end>-?\d+))?", range_token)
        if not match:
            raise ValueError(f"Invalid redesign range token '{token}'")
        if chain != design_chain:
            raise ValueError(
                f"Manual range '{token}' targets chain '{chain}', but this workflow currently supports a single design chain '{design_chain}'"
            )
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        lower = min(start, end)
        upper = max(start, end)
        for resnum in range(lower, upper + 1):
            if resnum in available_resnums:
                result.add(resnum)
    if not result:
        raise ValueError("Manual redesign ranges did not match any residues in the design chain")
    return result


def distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def pick_interface_shell(
    design_residues: Sequence[ResidueRecord],
    context_residues: Sequence[ResidueRecord],
    cutoff: float,
    padding: int,
) -> Set[int]:
    context_atoms = [coord for residue in context_residues for coord in residue.atom_coords]
    if not context_atoms:
        raise ValueError("interface_shell mode requires context chains with atom coordinates")

    selected_indices: Set[int] = set()
    for index, residue in enumerate(design_residues):
        for atom in residue.atom_coords:
            if any(distance(atom, other) <= cutoff for other in context_atoms):
                selected_indices.add(index)
                break

    if not selected_indices:
        raise ValueError("No design-chain residues fell within the requested interface cutoff")

    expanded: Set[int] = set()
    for index in selected_indices:
        for neighbor in range(max(0, index - padding), min(len(design_residues), index + padding + 1)):
            expanded.add(neighbor)
    return expanded


def residue_numbers_from_indices(residues: Sequence[ResidueRecord], indices: Iterable[int]) -> Set[int]:
    return {residues[index].key.resnum for index in indices}


def build_ranges(resnums: Iterable[int]) -> List[Tuple[int, int]]:
    ordered = sorted(set(int(value) for value in resnums))
    if not ordered:
        return []
    ranges: List[Tuple[int, int]] = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append((start, prev))
        start = prev = number
    ranges.append((start, prev))
    return ranges


def build_spec(chain: str, resnums: Iterable[int]) -> str:
    tokens: List[str] = []
    for start, end in build_ranges(resnums):
        if start == end:
            tokens.append(f"{chain}{start}")
        else:
            tokens.append(f"{chain}{start}-{end}")
    return ",".join(tokens)


def build_contig(design_chain: str, residues: Sequence[ResidueRecord], movable_indices: Set[int]) -> str:
    tokens: List[str] = []
    block_start = 0
    current_state = block_start in movable_indices

    def append_fixed_block(start_idx: int, end_idx: int) -> None:
        start_res = residues[start_idx].key.resnum
        prev_res = start_res
        subblock_start = start_res
        for idx in range(start_idx + 1, end_idx + 1):
            resnum = residues[idx].key.resnum
            if resnum != prev_res + 1:
                tokens.append(f"{design_chain}{subblock_start}-{prev_res}" if subblock_start != prev_res else f"{design_chain}{subblock_start}")
                subblock_start = resnum
            prev_res = resnum
        tokens.append(f"{design_chain}{subblock_start}-{prev_res}" if subblock_start != prev_res else f"{design_chain}{subblock_start}")

    def append_movable_block(start_idx: int, end_idx: int) -> None:
        length = (end_idx - start_idx) + 1
        tokens.append(f"{length}-{length}")

    for idx in range(1, len(residues) + 1):
        next_state = idx < len(residues) and idx in movable_indices
        if idx < len(residues) and next_state == current_state:
            continue
        end_idx = idx - 1
        if current_state:
            append_movable_block(block_start, end_idx)
        else:
            append_fixed_block(block_start, end_idx)
        if idx < len(residues):
            block_start = idx
            current_state = next_state

    return f"[{'/'.join(tokens)}]"


def write_design_seed_from_lines(
    lines: Sequence[str],
    output_path: Path,
    design_chain: str,
) -> None:
    with output_path.open("w", encoding="utf-8") as target:
        wrote_atoms = False
        for line in lines:
            if not line.startswith(("ATOM", "HETATM")):
                continue
            if line[21].strip() != design_chain:
                continue
            target.write(line if line.endswith("\n") else f"{line}\n")
            wrote_atoms = True
        if wrote_atoms:
            target.write("TER\n")
        target.write("END\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve constrained local-redesign regions from an input PDB")
    parser.add_argument("--input_pdb", required=True, help="Input complex PDB")
    parser.add_argument("--model_number", type=int, default=None, help="Optional model number for multi-model PDBs")
    parser.add_argument("--design_chains", required=True, help="Single protein chain to locally redesign")
    parser.add_argument("--context_chains", default="", help="Optional context chains for interface-shell mode")
    parser.add_argument("--region_mode", choices=["manual_ranges", "interface_shell"], default="manual_ranges")
    parser.add_argument("--redesign_ranges", default="", help="Manual ranges like A26-35,A95-115 or 26-35,95-115")
    parser.add_argument("--interface_cutoff", type=float, default=6.0, help="Distance cutoff for interface-shell mode")
    parser.add_argument("--region_padding", type=int, default=2, help="Residue padding around interface hits")
    parser.add_argument("--output_seed_pdb", required=True, help="Output seed PDB containing only the design chain")
    parser.add_argument("--output_manifest", required=True, help="Output manifest JSON")
    args = parser.parse_args()

    input_pdb = Path(args.input_pdb).expanduser().resolve()
    design_chains = parse_chain_list(args.design_chains)
    if len(design_chains) != 1:
        raise SystemExit("This first version supports exactly one design chain")
    design_chain = design_chains[0]
    context_chains = parse_chain_list(args.context_chains)

    structure_lines, resolved_model_number = select_structure_lines(input_pdb, args.model_number)
    chains = parse_pdb_residues_from_lines(structure_lines)
    if design_chain not in chains:
        raise SystemExit(f"Design chain '{design_chain}' not found in {input_pdb}")

    design_residues = chains[design_chain]
    available_resnums = {residue.key.resnum for residue in design_residues}
    if args.region_mode == "manual_ranges":
        movable_resnums = parse_manual_ranges(args.redesign_ranges, design_chain, available_resnums)
        movable_indices = {
            index for index, residue in enumerate(design_residues) if residue.key.resnum in movable_resnums
        }
    else:
        context_residues = [
            residue
            for chain_id in context_chains
            for residue in chains.get(chain_id, [])
        ]
        movable_indices = pick_interface_shell(
            design_residues,
            context_residues,
            cutoff=float(args.interface_cutoff),
            padding=max(0, int(args.region_padding)),
        )
        movable_resnums = residue_numbers_from_indices(design_residues, movable_indices)

    fixed_by_chain: Dict[str, Set[int]] = OrderedDict()
    movable_by_chain: Dict[str, Set[int]] = OrderedDict()
    movable_by_chain[design_chain] = set(movable_resnums)

    for chain_id, residues in chains.items():
        residue_numbers = {residue.key.resnum for residue in residues}
        if chain_id == design_chain:
            fixed_by_chain[chain_id] = residue_numbers - movable_resnums
        else:
            fixed_by_chain[chain_id] = residue_numbers

    contig = build_contig(design_chain, design_residues, movable_indices)
    movable_spec = build_spec(design_chain, movable_resnums)
    fixed_tokens = [
        build_spec(chain_id, resnums)
        for chain_id, resnums in fixed_by_chain.items()
        if resnums
    ]
    fixed_spec = ",".join(token for token in fixed_tokens if token)

    output_seed_pdb = Path(args.output_seed_pdb)
    output_manifest = Path(args.output_manifest)
    write_design_seed_from_lines(structure_lines, output_seed_pdb, design_chain)

    manifest = {
        "input_pdb": str(input_pdb),
        "model_number": resolved_model_number,
        "design_chain": design_chain,
        "context_chains": context_chains,
        "region_mode": args.region_mode,
        "movable_positions_spec": movable_spec,
        "fixed_positions_spec": fixed_spec,
        "contig_spec": contig,
        "movable_residue_count": len(movable_resnums),
        "fixed_residue_count": sum(len(values) for values in fixed_by_chain.values()),
        "design_chain_length": len(design_residues),
        "rfd3_mode": "monomer_motifscaff",
    }
    output_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
