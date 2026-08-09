"""API-side normalization and persistence helpers for RFD3 local redesign."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Mapping

from paths import resolve_runtime_data_path
from scripts.rfd3_local_redesign.contract import ContractError, build_request, request_sha256, write_request


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_local_redesign_params(
    params: Mapping[str, Any],
    *,
    job_name: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve the source and build the immutable request before queueing."""
    normalized = dict(params)
    input_value = normalized.get("input_structure") or normalized.get("input_pdb") or normalized.get("input_cif")
    if not input_value:
        raise ContractError("input_structure is required")
    source_path = resolve_runtime_data_path(str(input_value))
    if not source_path.is_file():
        raise ContractError(f"input structure does not exist: {source_path}")
    if source_path.suffix.lower() not in {".pdb", ".cif", ".gz"}:
        raise ContractError("input_structure must be a PDB or mmCIF file")

    normalized["input_structure"] = str(source_path)
    normalized["input_pdb"] = str(source_path)
    normalized["plr_input_pdb"] = str(source_path)
    source_sha256 = _sha256_file(source_path)
    existing_request = normalized.get("rfd3_request")
    if isinstance(existing_request, dict) and existing_request.get("schema") == "bms.rfd3.local-redesign.request.v1":
        input_binding = existing_request.get("input")
        if (
            not isinstance(input_binding, dict)
            or input_binding.get("path") != str(source_path)
            or input_binding.get("sha256") != source_sha256
        ):
            raise ContractError("existing local-redesign request source binding does not match the current input")
        request = existing_request
    else:
        request = build_request(normalized, job_name=job_name, source_sha256=source_sha256)
    digest = request_sha256(request)
    normalized["rfd3_request"] = request
    normalized["rfd3_request_sha256"] = digest
    normalized["rfd3_request_schema"] = request["schema"]
    normalized["rfd3_profile_id"] = request["profile_id"]
    normalized["plr_redesign_mode"] = request["redesign_mode"]
    normalized["plr_num_designs"] = request["execution"]["num_designs"]
    normalized.setdefault("plr_seq_method", "skip")
    normalized.setdefault("plr_run_boltz_validation", False)
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
