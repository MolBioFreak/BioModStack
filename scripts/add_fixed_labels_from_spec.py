#!/usr/bin/env python3
"""Mark fixed/designable residues for ProteinMPNN from a redesign manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Set, Tuple


ResidueToken = Tuple[str, int, str]


def parse_position_spec(spec: str, domain=None) -> Set[ResidueToken]:
    result: Set[ResidueToken] = set()
    tokens = [token.strip() for token in str(spec or "").split(",") if token.strip()]
    pattern = re.compile(r"(?P<chain>[A-Za-z0-9])(?P<start>-?\d+)(?P<icode_start>[A-Za-z]?)(?:-(?P<end>-?\d+)(?P<icode_end>[A-Za-z]?))?")
    for token in tokens:
        match = pattern.fullmatch(token)
        if not match:
            continue
        chain = match.group("chain")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        icode_start = (match.group("icode_start") or "").strip()
        icode_end = (match.group("icode_end") or "").strip() if match.group("end") is not None else icode_start
        if start == end and icode_start == icode_end:
            result.add((chain, start, icode_start))
            continue
        lower, upper = sorted(((start, icode_start), (end, icode_end)))
        if domain is not None:
            result.update(r for r in domain if r[0] == chain and lower <= (r[1], r[2]) <= upper)
        elif icode_start or icode_end:
            # Without a source domain only the exact endpoints are known.
            result.update(((chain, start, icode_start), (chain, end, icode_end)))
        else:
            result.update((chain, n, "") for n in range(lower[0], upper[0] + 1))
    return result


def residue_token_from_line(line: str) -> ResidueToken | None:
    if not line.startswith(("ATOM", "HETATM")):
        return None
    chain = line[21].strip()
    raw_resnum = line[22:26].strip()
    if not chain or not raw_resnum or not raw_resnum.lstrip("-").isdigit():
        return None
    return chain, int(raw_resnum), line[26].strip()


def add_labels(input_pdb: Path, output_pdb: Path, designable_tokens: Set[ResidueToken] | None, *, fixed_tokens=(), design_chains="") -> None:
    if __package__:
        from .proteinmpnn_native_binding import ROLE_REMARK, role_contract, pdb_domain
    else:
        from proteinmpnn_native_binding import ROLE_REMARK, role_contract, pdb_domain
    text = input_pdb.read_text()
    domain = pdb_domain(input_pdb, text=text, strict=False)
    # This explicit redesign selection supersedes generator/B-factor masks.
    data = ('\n'.join(line for line in text.splitlines()
                       if not line.startswith(('REMARK BMS_MPNN_', 'REMARK PDBinfo-LABEL:'))) + '\n').encode()
    chains = list(dict.fromkeys(r[0] for r in domain))
    selected = [c for c in chains if not design_chains or c in design_chains.split(',')]
    roles = role_contract(input_pdb, {'design_chain': selected}, data=data, strict=False)
    import hashlib
    roles['source_pdb_sha256'] = hashlib.sha256(input_pdb.read_bytes()).hexdigest()
    roles['fixed_positions'] = [list(r) for r in domain if r[0] not in selected or r in fixed_tokens
                                or (designable_tokens is not None and r not in designable_tokens)]
    output_pdb.write_bytes((ROLE_REMARK + json.dumps(roles) + '\n').encode() + data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply ProteinMPNN fixed labels from protein-local-redesign manifest")
    parser.add_argument("--input_dir", required=True, help="Input directory containing PDB files")
    parser.add_argument("--output_dir", required=True, help="Output directory for labeled PDB files")
    parser.add_argument("--manifest", default="", help="Optional region manifest JSON")
    parser.add_argument("--designable_positions", default="", help="Designable positions, e.g. A21-40 (overrides the manifest); none given means every residue is designable")
    parser.add_argument("--fixed_positions", default="")
    parser.add_argument("--design_chains", default="")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8")) if args.manifest else {}
    designable_spec = (args.designable_positions
        or manifest.get("sequence_redesign_positions_spec")
        or manifest.get("movable_positions_spec", ""))
    explicit = bool(args.designable_positions or args.manifest)

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdb_files = sorted(input_dir.glob("*.pdb"))
    for pdb_path in pdb_files:
        domain = [r for line in pdb_path.read_text().splitlines() if (r := residue_token_from_line(line))]
        designable_tokens = parse_position_spec(designable_spec, domain)
        add_labels(pdb_path, output_dir / pdb_path.name, designable_tokens if explicit else None,
                   fixed_tokens=parse_position_spec(args.fixed_positions, domain),
                   design_chains=','.join(c.strip() for c in args.design_chains.split(',')))
        print(f"Labeled {pdb_path.name}")


if __name__ == "__main__":
    main()
