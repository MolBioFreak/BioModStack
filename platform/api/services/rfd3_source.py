"""Source-structure helpers shared by RFD3 generation tasks (parse, residue identities, path resolution)."""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
from typing import Any

from Bio.PDB import MMCIFParser, PDBParser

from paths import get_allowed_roots, resolve_runtime_data_path
from scripts.rfd3_local_redesign.contract import ContractError


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_model(path: Path, model_serial=None):
    name = path.name.lower()
    is_cif = name.endswith((".cif", ".mmcif", ".cif.gz", ".mmcif.gz"))
    parser = MMCIFParser(QUIET=True) if is_cif else PDBParser(QUIET=True)
    try:
        if name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                structure = parser.get_structure("rfd3_local_redesign_source", handle)
        else:
            structure = parser.get_structure("rfd3_local_redesign_source", str(path))
    except Exception as exc:
        raise ContractError(f"input structure could not be parsed: {path}: {exc}") from exc

    try:
        model = next(m for m in structure.get_models() if model_serial is None or str(m.serial_num) == str(model_serial))
    except StopIteration as exc:
        raise ContractError("input structure contains no models") from exc

    return model


_NATIVE_DROPPED_WATER = frozenset({"HOH", "DOD"})  # atomworks WATER_LIKE_CCDS


def _source_residue_identities(path: Path, model_serial=None) -> list[dict[str, Any]]:
    model = _source_model(path, model_serial)
    chains: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for chain in model:
        residues: list[dict[str, Any]] = []
        for residue in chain:
            if not any(True for _atom in residue.get_atoms()):
                continue
            # RFD3's AtomWorks parser drops crystal water before it builds the atom array, so a
            # residue list that kept waters would name residues the native run cannot find.
            if str(residue.resname or "").strip().upper() in _NATIVE_DROPPED_WATER:
                continue
            residue_number = int(residue.id[1])
            insertion_code = str(residue.id[2] or "").strip()
            identity = (str(chain.id), residue_number, insertion_code)
            if identity in seen:
                continue
            seen.add(identity)
            residues.append(
                {
                    "res_num": residue_number,
                    "insertion_code": insertion_code,
                    "residue_name": str(residue.resname or "").strip(),
                }
            )
        if residues:
            chains.append({"chain_id": str(chain.id), "residues": residues})
    if not chains:
        raise ContractError("input structure contains no residues")
    return chains


def _resolve_local_redesign_source(value: Any) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ContractError("input_structure is required")
    expanded = Path(os.path.expanduser(raw))
    if expanded.is_absolute():
        lexical = expanded
    else:
        parts = expanded.parts
        if not parts:
            raise ContractError("input_structure is required")
        root = get_allowed_roots().get(parts[0])
        if root is None:
            raise ContractError("input_structure alias root is not allowed")
        lexical = root / Path(*parts[1:])
    if lexical.is_symlink():
        raise ContractError("input_structure must not be a symbolic link")
    return resolve_runtime_data_path(lexical)
