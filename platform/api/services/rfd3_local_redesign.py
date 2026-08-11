"""API-side normalization and persistence helpers for RFD3 local redesign."""

from __future__ import annotations

import gzip
import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Mapping

from Bio.PDB import MMCIFParser, PDBParser

from paths import get_data_root, resolve_runtime_data_path
from scripts.rfd3_local_redesign.contract import (
    CONTRACT_REVISION,
    ContractError,
    build_request,
    request_sha256,
    write_request,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_residue_identities(path: Path) -> list[dict[str, Any]]:
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
        model = next(structure.get_models())
    except StopIteration as exc:
        raise ContractError("input structure contains no models") from exc

    chains: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for chain in model:
        residues: list[dict[str, Any]] = []
        for residue in chain:
            if not any(True for _atom in residue.get_atoms()):
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


def normalize_local_redesign_params(
    params: Mapping[str, Any],
    *,
    job_name: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve the source and build the immutable request before queueing."""
    normalized = dict(params)
    allowed_keys = {
        "input_structure",
        "input_pdb",
        "input_cif",
        "input",
        "redesign_mode",
        "design_chains",
        "context_chains",
        "redesign_ranges",
        "region_mode",
        "sequence_policy",
        "insertion_anchor",
        "insertion_min_length",
        "insertion_max_length",
        "partial_t",
        "ligand",
        "select_hotspots",
        "select_hbond_donor",
        "select_hbond_acceptor",
        "num_designs",
        "seed",
        "dump_trajectories",
        "write_full_json",
        "profile_id",
        "source_residue_identities",
        "rfd3_request",
    }
    unexpected = sorted(str(key) for key in normalized if key not in allowed_keys)
    if unexpected:
        raise ContractError(f"unsupported local-redesign parameters: {', '.join(unexpected)}")
    if normalized.get("sequence_policy") not in {None, "", "skip"}:
        raise ContractError("native local redesign requires sequence_policy=skip")
    normalized["sequence_policy"] = "skip"
    normalized["write_full_json"] = True
    input_value = (
        normalized.get("input_structure")
        or normalized.get("input_pdb")
        or normalized.get("input_cif")
        or normalized.get("input")
    )
    if not input_value:
        raise ContractError("input_structure is required")
    raw_source_path = Path(str(input_value)).expanduser()
    if raw_source_path.exists() and raw_source_path.is_symlink():
        raise ContractError("input_structure must not be a symbolic link")
    source_path = resolve_runtime_data_path(str(input_value))
    data_root = get_data_root().resolve()
    try:
        source_path.relative_to(data_root)
    except ValueError as exc:
        raise ContractError("input_structure must be contained within the active BioModStack data root") from exc
    if not source_path.is_file() or source_path.is_symlink():
        raise ContractError(f"input structure does not exist: {source_path}")
    if source_path.suffix.lower() not in {".pdb", ".cif", ".gz"}:
        raise ContractError("input_structure must be a PDB or mmCIF file")

    normalized["input_structure"] = str(source_path)
    normalized["input_pdb"] = str(source_path)
    normalized["plr_input_pdb"] = str(source_path)
    normalized["source_residue_identities"] = _source_residue_identities(source_path)
    source_sha256 = _sha256_file(source_path)
    rebuilt_request = build_request(normalized, job_name=job_name, source_sha256=source_sha256)
    existing_request = normalized.get("rfd3_request")
    if isinstance(existing_request, dict):
        if existing_request.get("schema") != "bms.rfd3.local-redesign.request.v1":
            raise ContractError("existing native RFD3 request schema is unsupported")
        if existing_request.get("contract_revision") != CONTRACT_REVISION:
            raise ContractError(
                "existing native RFD3 request predates the fixed-scaffold contract and cannot be replayed"
            )
        if existing_request != rebuilt_request:
            raise ContractError("existing native RFD3 request does not match the canonical source-derived request")
    request = rebuilt_request
    digest = request_sha256(request)
    normalized["rfd3_request"] = request
    normalized["rfd3_request_sha256"] = digest
    normalized["rfd3_request_schema"] = request["schema"]
    normalized["rfd3_profile_id"] = request["profile_id"]
    normalized["plr_redesign_mode"] = request["redesign_mode"]
    normalized["plr_num_designs"] = request["execution"]["num_designs"]
    normalized["plr_seed"] = request["execution"]["seed"]
    normalized["plr_dump_trajectories"] = request["execution"]["dump_trajectories"]
    normalized["plr_write_full_json"] = request["execution"]["write_full_json"]
    normalized["rfd3_batches_per_design"] = request["execution"]["num_designs"]
    normalized["plr_seq_method"] = "skip"
    normalized["seq_method"] = "skip"
    normalized["plr_run_boltz_validation"] = False
    normalized["run_boltz_validation"] = False
    return normalized, request, digest


def materialize_local_redesign_request(
    params: Mapping[str, Any],
    *,
    output_dir: str | Path,
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str, Path]:
    """Write the canonical request under the owning job directory."""
    request = params.get("rfd3_request")
    if not isinstance(request, dict):
        raise ContractError("rfd3_request is missing from normalized job parameters")
    digest = request_sha256(request)
    destination = Path(output_dir).expanduser().resolve() / "requests" / "rfd3_local_redesign_request.json"
    written_digest = write_request(destination, request)
    if written_digest != digest:
        raise ContractError("canonical request digest changed during materialization")

    normalized = dict(params)
    normalized["rfd3_request_path"] = str(destination)
    normalized["rfd3_request_sha256"] = digest
    normalized["rfd3_request_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:rfd3-local-redesign:{job_id}"))
    normalized["rfd3_result_contract_id"] = "rfd3_local_redesign_v1"
    return normalized, request, digest, destination


def artifact_media_type(path: Path) -> str:
    if path.name.endswith(".cif.gz"):
        return "chemical/x-mmcif+gzip"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
