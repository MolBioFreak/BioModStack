#!/usr/bin/env python3
"""Materialize one admitted RFD3 mmCIF candidate as a bound PDB backbone.

The RFD3 runtime emits compressed native mmCIF.  Sequence-design and structure
prediction lanes consume PDB, so this conversion is an explicit, hash-bound
handoff rather than an implicit Nextflow filename/path assumption.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from evaluate_rfd3_initial_candidate import (
    _atom_map,
    _canonical,
    _parse_atoms,
    _read_cif_text,
    _regular,
    _residues,
    _sha256,
)


def _json(path: Path, label: str) -> dict[str, Any]:
    path = _regular(path, label)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def _pdb_atom_line(serial: int, atom: Any, residue: Any) -> str:
    chain, residue_number, insertion = residue.key
    chain = (str(chain) or "A")[0]
    residue_name = str(residue.residue_name).upper()[:3]
    atom_name = str(atom.atom_name).upper()[:4]
    element = str(getattr(atom, "element", "") or atom_name[0]).upper()[:2]
    x, y, z = (float(value) for value in atom.coord)
    return (
        f"ATOM  {serial:5d} {atom_name:>4s} {residue_name:>3s} {chain:1s}"
        f"{int(residue_number):4d}{str(insertion or '')[:1]:1s}   "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{1.00:6.2f}{0.00:6.2f}"
        f"          {element:>2s}\n"
    )


def _materialize_pdb(candidate_path: Path, output_path: Path) -> tuple[int, int]:
    atoms = _parse_atoms(_read_cif_text(candidate_path))
    residues = _residues(atoms)
    if not residues:
        raise ValueError("admitted candidate contains no residues")
    if len({residue.key[0] for residue in residues}) != 1:
        raise ValueError("Shape sequence handoff requires exactly one chain")
    lines: list[str] = []
    serial = 1
    for residue in residues:
        atom_map = _atom_map(residue)
        for atom_name in sorted(atom_map):
            lines.append(_pdb_atom_line(serial, atom_map[atom_name], residue))
            serial += 1
    lines.append("TER\n")
    lines.append("END\n")
    output_path.write_text("".join(lines), encoding="utf-8")
    return len(residues), serial - 1


def prepare_backbone(
    *,
    candidate_path: Path,
    admission_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    candidate_path = _regular(candidate_path, "RFD3 candidate")
    admission_path = _regular(admission_path, "initial admission")
    admission = _json(admission_path, "initial admission")
    if admission.get("status") != "accepted":
        raise ValueError("only an initially admitted candidate may enter the sequence handoff")
    candidate_id = admission.get("candidate_id")
    if not isinstance(candidate_id, str) or len(candidate_id) != 64:
        raise ValueError("initial admission has no valid candidate ID")
    if admission.get("candidate_sha256") != _sha256(candidate_path):
        raise ValueError("initial admission does not bind the staged candidate bytes")
    if output_dir.exists():
        raise ValueError("backbone output directory must be new")
    output_dir.mkdir(parents=True)
    pdb_path = output_dir / "shape_backbone.pdb"
    residue_count, atom_count = _materialize_pdb(candidate_path, pdb_path)
    staged_admission = output_dir / "initial_admission.json"
    shutil.copyfile(admission_path, staged_admission)
    backbone_sha256 = _sha256(pdb_path)
    manifest = {
        "schema": "bms_shape_initial_backbone_v1",
        "candidate_id": candidate_id,
        "status": "accepted",
        "source_native_structure": {
            "filename": candidate_path.name,
            "sha256": _sha256(candidate_path),
            "bytes": candidate_path.stat().st_size,
        },
        "backbone": {
            "filename": pdb_path.name,
            "sha256": backbone_sha256,
            "bytes": pdb_path.stat().st_size,
            "format": "pdb",
            "chain_id": "A",
            "residue_count": residue_count,
            "atom_count": atom_count,
        },
        "initial_admission": {
            "filename": staged_admission.name,
            "sha256": _sha256(staged_admission),
            "bytes": staged_admission.stat().st_size,
        },
        "request_sha256": admission.get("request_sha256"),
        "geometry_sha256": admission.get("geometry_sha256"),
        "point_pool_sha256": admission.get("point_pool_sha256"),
        "sdf_sha256": admission.get("sdf_sha256"),
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    manifest_path = output_dir / "shape_backbone_manifest.json"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--admission", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    prepare_backbone(
        candidate_path=args.candidate,
        admission_path=args.admission,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
