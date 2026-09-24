"""Exact source snapshots and the existing checked PDB-only receiving boundary.

No launch policy or display conversion lives here. Native bytes remain retained.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from database import Design, Job
from paths import resolve_allowed_path, to_allowed_relative
from services.binder_diagnostic_selection import CandidateDocument, selected_document


class StructureSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    design_id: str | None = None
    job_id: str | None = None
    document: CandidateDocument | None = None
    output_format: Literal["native", "pdb"] = "native"
    model_number: int | None = None
    expected_sha256: str | None = None


def materialize_source_bytes(raw: bytes, suffix: str, destination: Path, *, output_format: str,
                             model_number: int | None = None) -> dict:
    """Keep original bytes; explicitly select a model without renumbering authors."""
    from Bio.PDB import MMCIFIO, MMCIFParser, PDBParser, PDBIO, Select
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    from Bio.PDB.PDBExceptions import PDBConstructionException, PDBIOException

    native_format = "cif" if suffix.lower() in {".cif", ".mmcif"} else "pdb"
    if suffix.lower() not in {".pdb", ".cif", ".mmcif"}:
        raise ValueError("Select a PDB or mmCIF structure document")
    source = destination / f"native.{native_format}"
    source.write_bytes(raw)
    consumed = source
    parser = MMCIFParser(QUIET=True) if native_format == "cif" else PDBParser(QUIET=True)
    inspection_error = None
    try:
        structure = parser.get_structure("source", io.StringIO(raw.decode("utf-8")))
    except (ValueError, KeyError, TypeError, PDBConstructionException) as exc:
        if output_format != "native" or model_number is not None:
            raise ValueError(f"Could not prepare the requested structure representation: {exc}") from exc
        # Native consumers retain their own parsing authority. Failure to enrich
        # inspection metadata is not a new native-source admission condition.
        structure = []
        inspection_error = str(exc)
    numbers = [int(model.serial_num) for model in structure]
    if model_number is not None:
        if model_number not in numbers:
            raise ValueError("Requested model is not present in this exact document")
        consumed = destination / f"model-{model_number}.{native_format}"
        if native_format == "cif":
            columns = MMCIF2Dict(io.StringIO(raw.decode("utf-8")))
            model_column = columns.get("_atom_site.pdbx_PDB_model_num", ["1"] * len(columns["_atom_site.id"]))
            rows = [i for i, value in enumerate(model_column) if int(value) == model_number]
            for key, values in list(columns.items()):
                if key.startswith("_atom_site."):
                    columns[key] = [values[i] for i in rows]
            writer = MMCIFIO()
            writer.set_dict(columns)
            writer.save(str(consumed))
        else:
            class ExactModel(Select):
                def accept_model(self, model):
                    return int(model.serial_num) == model_number
            writer = PDBIO()
            writer.set_structure(structure)
            writer.save(str(consumed), select=ExactModel(), preserve_atom_numbering=True)
    selected = parser.get_structure("selected", str(consumed)) if consumed != source else structure
    identity = [{"model_number": int(model.serial_num), "auth_asym_id": chain.id,
                 "auth_seq_id": residue.id[1], "insertion_code": residue.id[2].strip(),
                 "residue_name": residue.resname}
                for model in selected for chain in model for residue in chain]
    if output_format == "pdb" and native_format == "cif":
        # One converter, owned by Jobs; lazy import avoids router import cycles.
        from routers.jobs import _cif_selection_pdb
        try:
            consumed = _cif_selection_pdb(consumed, destination / "derived.pdb")
        except (PDBConstructionException, PDBIOException) as exc:
            raise ValueError(f"Selected CIF cannot be represented in PDB: {exc}") from exc
    return {"path": to_allowed_relative(consumed), "format": "pdb" if output_format == "pdb" else native_format,
            "sha256": hashlib.sha256(consumed.read_bytes()).hexdigest(),
            "native_path": to_allowed_relative(source), "native_sha256": hashlib.sha256(raw).hexdigest(),
            "native_format": native_format, "model_numbers": numbers,
            "model_number": model_number, "author_residues": identity, "inspection_error": inspection_error}


async def resolve_structure_source(request: StructureSourceRequest, session):
    identity = {}
    if request.design_id:
        design = await session.get(Design, request.design_id)
        if design is None:
            raise ValueError("Source Design not found")
        job = await session.get(Job, design.job_id)
        if job is None or (request.job_id is not None and request.job_id != job.id):
            raise ValueError("Source Design does not belong to the requested Job")
        path, identity = await selected_document(job, design, request.document, session)
        # Preserve existing artifact-root containment; never trust a client path.
        from services.job_result_roots import resolve_persisted_job_result_root
        path = path.resolve() if path.is_absolute() else resolve_allowed_path(str(path))
        path.relative_to(resolve_persisted_job_result_root(job).resolve())
    elif request.path and not request.document and not request.job_id:
        path = resolve_allowed_path(request.path)
    else:
        raise ValueError("Choose a governed path or an exact Design document")
    return path, identity
