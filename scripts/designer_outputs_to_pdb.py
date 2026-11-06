#!/usr/bin/env python3
"""Turn native sequence-designer outputs into one PDB per design for the resequence loop.

LigandMPNN  writes mmCIF per design; unresolved (NaN) atoms are dropped, never invented.
Caliby      writes PDB per design already; they are copied under stable names.
ProtonPottsMPNN writes sequences only. Each design becomes a backbone-only PDB of the input
            structure whose residue names carry the designed sequence; the validator (Protenix v2,
            optionally ESMFold2) produces the structure from that sequence.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

AA1_TO_3 = {"A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN", "E": "GLU", "G": "GLY",
            "H": "HIS", "I": "ILE", "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO", "S": "SER",
            "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL"}
BACKBONE = {"N", "CA", "C", "O"}


def _name(stem: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in stem).strip("._-") or "design"


def cif_to_pdb(source: Path, destination: Path) -> int:
    import numpy as np
    from biotite.structure.io.pdb import PDBFile, set_structure
    from biotite.structure.io.pdbx import CIFFile, get_structure

    atoms = get_structure(CIFFile.read(source), model=1, use_author_fields=True)
    resolved = ~np.isnan(atoms.coord).any(axis=1)
    atoms = atoms[resolved]
    if len(atoms) == 0:
        raise ValueError(f"{source.name} has no resolved atoms")
    handle = PDBFile()
    set_structure(handle, atoms)
    handle.write(destination)
    return int((~resolved).sum())


def ligandmpnn(output: Path, out_dir: Path) -> list[Path]:
    manifest = json.loads((output / "manifest.json").read_text())
    written = []
    for record in manifest["records"]:
        for artifact in record["artifacts"]:
            if artifact["kind"] != "structure":
                continue
            target = out_dir / (_name(Path(artifact["path"]).stem) + ".pdb")
            cif_to_pdb(output / artifact["path"], target)
            written.append(target)
    if not written:
        raise ValueError("LigandMPNN wrote no structures; write_structures must be enabled")
    return written


def caliby(output: Path, out_dir: Path) -> list[Path]:
    document = json.loads((output / "caliby_results.json").read_text())
    written = []
    for record in document["records"]:
        source = output / record["structure_path"]
        target = out_dir / (_name(f"caliby_{record['record_id']}_{source.stem}") + ".pdb")
        if source.suffix.lower() in {".cif", ".mmcif"}:
            cif_to_pdb(source, target)
        else:
            shutil.copyfile(source, target)
        written.append(target)
    if not written:
        raise ValueError("Caliby wrote no structures")
    return written


def protonpotts(output: Path, source: Path, out_dir: Path) -> list[Path]:
    import numpy as np
    from biotite.structure.io.pdb import PDBFile, set_structure
    from biotite.structure.io.pdbx import CIFFile, get_structure

    manifest = json.loads((output / "manifest.json").read_text())
    if source.suffix.lower() in {".cif", ".mmcif"}:
        atoms = get_structure(CIFFile.read(source), model=1, use_author_fields=True)
    else:
        atoms = PDBFile.read(source).get_structure(model=1)
    chain = manifest["request"]["options"]["binder_chain"]
    atoms = atoms[np.isin(atoms.atom_name, list(BACKBONE)) & (atoms.chain_id == chain)]
    keys = list(dict.fromkeys(zip(atoms.chain_id, atoms.res_id, atoms.ins_code)))
    written = []
    for design in manifest["designs"]:
        sequence = design["native"]["canonical_sequence"]
        if len(sequence) != len(keys):
            raise ValueError(f"design {design['design_id']} has {len(sequence)} residues, binder chain has {len(keys)}")
        names = {key: AA1_TO_3.get(letter, "UNK") for key, letter in zip(keys, sequence)}
        carrier = atoms.copy()
        carrier.res_name = np.array([names[(c, r, i)] for c, r, i in zip(carrier.chain_id, carrier.res_id, carrier.ins_code)])
        target = out_dir / (_name(design["design_id"]) + ".pdb")
        handle = PDBFile()
        set_structure(handle, carrier)
        handle.write(target)
        written.append(target)
    if not written:
        raise ValueError("ProtonPottsMPNN wrote no designs")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=("ligandmpnn", "caliby", "protonpottsmpnn"))
    parser.add_argument("--native-output", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="input structure (ProtonPottsMPNN)")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.engine == "ligandmpnn":
        written = ligandmpnn(args.native_output, args.out_dir)
    elif args.engine == "caliby":
        written = caliby(args.native_output, args.out_dir)
    else:
        if args.source is None:
            parser.error("--source is required for protonpottsmpnn")
        written = protonpotts(args.native_output, args.source, args.out_dir)
    print(json.dumps({"engine": args.engine, "designs": [p.name for p in written]}))


if __name__ == "__main__":
    main()
