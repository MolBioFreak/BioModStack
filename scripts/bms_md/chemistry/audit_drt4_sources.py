#!/usr/bin/env python3
"""Produce immutable, fail-closed evidence from the staged DRT4 RCSB corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import Bio
from Bio.PDB.MMCIF2Dict import MMCIF2Dict


def _values(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    return value if isinstance(value, list) else [value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atom_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    names = {
        "group": "_atom_site.group_PDB",
        "chain": "_atom_site.auth_asym_id",
        "label_chain": "_atom_site.label_asym_id",
        "component": "_atom_site.auth_comp_id",
        "sequence": "_atom_site.auth_seq_id",
        "atom": "_atom_site.label_atom_id",
        "x": "_atom_site.Cartn_x",
        "y": "_atom_site.Cartn_y",
        "z": "_atom_site.Cartn_z",
    }
    columns = {name: _values(data, key) for name, key in names.items()}
    lengths = {len(column) for column in columns.values()}
    if len(lengths) != 1:
        raise ValueError("mmCIF atom_site columns have inconsistent lengths")
    return [dict(zip(columns, values, strict=True)) for values in zip(*columns.values(), strict=True)]


def _audit_9vdo(data: dict[str, Any]) -> dict[str, Any]:
    rows = _atom_rows(data)
    dctp_sites = sorted({(row["label_chain"], row["sequence"]) for row in rows if row["component"] == "DCP"})
    manganese_sites = sorted({(row["label_chain"], row["sequence"]) for row in rows if row["component"] == "MN"})
    metal_connections = _values(data, "_struct_conn.conn_type_id")
    return {
        "deposited_nonpolymer_component_ids": sorted(set(_values(data, "_pdbx_entity_nonpoly.comp_id"))),
        "dctp_component_id": "DCP",
        "dctp_site_count": len(dctp_sites),
        "manganese_site_count": len(manganese_sites),
        "manganese_per_dctp_site": (
            len(manganese_sites) / len(dctp_sites) if dctp_sites else None
        ),
        "metalc_connection_count": sum(item == "metalc" for item in metal_connections),
        "required_dctp_present": bool(dctp_sites),
        "required_two_manganese_per_active_site_observed": (
            bool(dctp_sites) and len(manganese_sites) == 2 * len(dctp_sites)
        ),
    }


def _audit_9vdp(data: dict[str, Any], rcsb_entry: dict[str, Any]) -> dict[str, Any]:
    rows = _atom_rows(data)
    tyr_oh = [row for row in rows if row["component"] == "TYR" and row["sequence"] == "125" and row["atom"] == "OH"]
    dna_p = [row for row in rows if row["group"] == "ATOM" and row["atom"] == "P"]
    distances = [
        math.dist(
            (float(tyr[axis]) for axis in ("x", "y", "z")),
            (float(phosphate[axis]) for axis in ("x", "y", "z")),
        )
        for tyr in tyr_oh
        for phosphate in dna_p
    ]
    entry_info = rcsb_entry.get("rcsb_entry_info", {})
    minimum = min(distances) if distances else None
    return {
        "tyr125_oh_site_count": len(tyr_oh),
        "dna_phosphate_atom_count": len(dna_p),
        "deposited_struct_conn_present": bool(_values(data, "_struct_conn.id")),
        "rcsb_inter_mol_covalent_bond_count": entry_info.get("inter_mol_covalent_bond_count"),
        "minimum_tyr125_oh_to_any_deposited_dna_p_angstrom": minimum,
        "required_tyr125_oh_to_dna_p_link_observed": bool(
            minimum is not None
            and minimum <= 2.0
            and bool(_values(data, "_struct_conn.id"))
            and entry_info.get("inter_mol_covalent_bond_count", 0) > 0
        ),
    }


def _audit_9vdv(data: dict[str, Any]) -> dict[str, Any]:
    rows = _atom_rows(data)
    residues = {
        (row["chain"], row["sequence"], row["component"])
        for row in rows
        if row["sequence"] in {"240", "241"}
    }
    chain_positions: dict[str, dict[str, str]] = {}
    for chain, sequence, component in sorted(residues):
        chain_positions.setdefault(chain, {})[sequence] = component
    complete = [positions for positions in chain_positions.values() if {"240", "241"}.issubset(positions)]
    return {
        "auth_chain_positions": chain_positions,
        "chains_with_both_positions": len(complete),
        "d240a_d241a_observed_in_every_complete_chain": bool(complete) and all(
            positions["240"] == "ALA" and positions["241"] == "ALA" for positions in complete
        ),
    }


def audit_corpus(source_dir: Path) -> dict[str, Any]:
    paths = {pdb: source_dir / f"{pdb}.cif" for pdb in ("9VDO", "9VDP", "9VDV")}
    metadata_path = source_dir / "9VDP.rcsb-entry.json"
    for path in [*paths.values(), metadata_path]:
        if not path.is_file():
            raise FileNotFoundError(path)
    parsed = {pdb: MMCIF2Dict(str(path)) for pdb, path in paths.items()}
    rcsb_entry = json.loads(metadata_path.read_text(encoding="utf-8"))
    files = [*paths.values(), metadata_path]
    return {
        "schema": "bms.md.drt4-source-audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "script": "scripts/bms_md/chemistry/audit_drt4_sources.py",
            "python": platform.python_version(),
            "biopython": Bio.__version__,
        },
        "sources": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
        "9VDO": _audit_9vdo(parsed["9VDO"]),
        "9VDP": _audit_9vdp(parsed["9VDP"], rcsb_entry),
        "9VDV": _audit_9vdv(parsed["9VDV"]),
        "acceptance": {
            "9VDO_dctp_two_manganese_source_observed": _audit_9vdo(parsed["9VDO"])["required_two_manganese_per_active_site_observed"],
            "9VDP_tyr125_phosphate_link_source_observed": _audit_9vdp(parsed["9VDP"], rcsb_entry)["required_tyr125_oh_to_dna_p_link_observed"],
            "9VDV_d240a_d241a_source_observed": _audit_9vdv(parsed["9VDV"])["d240a_d241a_observed_in_every_complete_chain"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = audit_corpus(args.source_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "acceptance": payload["acceptance"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
