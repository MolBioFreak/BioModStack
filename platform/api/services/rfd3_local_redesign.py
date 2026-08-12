"""API-side normalization and persistence helpers for RFD3 local redesign."""

from __future__ import annotations

import gzip
import hashlib
import mimetypes
import os
import shutil
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from Bio.PDB import MMCIFParser, PDBParser

from paths import (
    get_allowed_roots,
    get_data_root,
    resolve_runtime_data_path,
)
from scripts.rfd3_local_redesign.contract import (
    CONTRACT_REVISION,
    ContractError,
    build_request,
    request_sha256,
    write_request,
)


RFD3_WORKFLOW_ADAPTER_ID = "bms.core-job.protein_local_redesign.adapter.v1"
RFD3_SCIENTIFIC_PARAM_KEYS = {
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


async def requeue_failed_request_for_job(session, *, job_id: str) -> bool:
    """Reset one failed typed request when its owning Job is admitted for retry."""
    from sqlalchemy import select

    from database import Job, RFD3LocalRedesignRequest

    job = await session.get(Job, job_id)
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_local_redesign"
        or job.status != "queued"
        or job.queue_status != "queued"
    ):
        return False
    request = (
        await session.execute(
            select(RFD3LocalRedesignRequest).where(
                RFD3LocalRedesignRequest.job_id == job_id,
                RFD3LocalRedesignRequest.status == "failed",
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return False

    request.status = "queued"
    request.failure_receipt_json = None
    request.terminal_at = None
    request.updated_at = datetime.utcnow()
    await session.flush()
    return True


async def start_request_for_job(session, *, job_id: str) -> bool:
    """Advance one admitted typed request with its authoritative running Job."""
    from sqlalchemy import select

    from database import Job, RFD3LocalRedesignRequest

    job = await session.get(Job, job_id)
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_local_redesign"
        or job.status != "running"
        or job.started_at is None
    ):
        return False
    request = (
        await session.execute(
            select(RFD3LocalRedesignRequest).where(
                RFD3LocalRedesignRequest.job_id == job_id,
                RFD3LocalRedesignRequest.status.in_(("prepared", "queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return False

    request.status = "running"
    request.failure_receipt_json = None
    request.terminal_at = None
    request.updated_at = datetime.utcnow()
    await session.flush()
    return True


def canonical_local_redesign_data_alias(value: Any) -> str:
    """Return one canonical public data-root alias or fail closed."""
    raw = str(value or "").strip()
    candidate = Path(raw)
    if (
        not raw
        or candidate.is_absolute()
        or not candidate.parts
        or len(candidate.parts) < 2
        or candidate.parts[0] != "data"
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != raw
    ):
        raise ContractError("native RFD3 workflow input_structure must be a canonical data/... alias")
    return raw


def validate_local_redesign_workflow_params(
    params: Mapping[str, Any],
    *,
    expected_adapter_id: str = RFD3_WORKFLOW_ADAPTER_ID,
) -> None:
    """Validate immutable public workflow intent without opening its source."""
    allowed = (
        RFD3_SCIENTIFIC_PARAM_KEYS
        - {"input", "input_pdb", "input_cif", "rfd3_request"}
    ) | {"workflow_adapter"}
    unexpected = sorted(str(key) for key in params if key not in allowed)
    if unexpected:
        raise ContractError(
            f"unsupported native RFD3 workflow parameters: {', '.join(unexpected)}"
        )
    if params.get("workflow_adapter") != expected_adapter_id:
        raise ContractError("native RFD3 workflow has no authoritative adapter")
    canonical_local_redesign_data_alias(params.get("input_structure"))


def local_redesign_requests_semantically_equal(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    """Compare scientific request meaning across task-owned paths and display names."""
    left_semantic = deepcopy(dict(left))
    right_semantic = deepcopy(dict(right))
    for request in (left_semantic, right_semantic):
        request.pop("job_name", None)
        input_binding = request.get("input")
        if isinstance(input_binding, dict):
            input_binding.pop("path", None)
    return left_semantic == right_semantic


def prepare_local_redesign_scheduler_params(
    params: Mapping[str, Any],
    *,
    job_name: str,
    expected_adapter_id: str = RFD3_WORKFLOW_ADAPTER_ID,
) -> dict[str, Any]:
    """Bind typed scheduler intent to one source-derived native RFD3 request."""
    scientific_params = dict(params)
    adapter_id = scientific_params.pop("workflow_adapter", None)
    if adapter_id != expected_adapter_id:
        raise ContractError("native RFD3 scheduler has no authoritative workflow adapter")
    normalized, request, _digest = normalize_local_redesign_params(
        scientific_params,
        job_name=job_name,
    )
    prepared = dict(scientific_params)
    prepared["sequence_policy"] = normalized["sequence_policy"]
    prepared["write_full_json"] = normalized["write_full_json"]
    prepared["rfd3_request"] = request
    prepared["workflow_adapter"] = expected_adapter_id
    return prepared


def project_local_redesign_scheduler_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Project persisted native parameters back to an executable path-safe workflow intent."""
    projected_keys = RFD3_SCIENTIFIC_PARAM_KEYS - {
        "input",
        "input_structure",
        "input_pdb",
        "input_cif",
        "rfd3_request",
    }
    projected = {key: deepcopy(value) for key, value in params.items() if key in projected_keys}
    input_value = params.get("input_structure") or params.get("input_pdb")
    if not isinstance(input_value, str) or not input_value.strip():
        raise ContractError("persisted native RFD3 job has no source path for workflow projection")
    try:
        source = Path(input_value).resolve()
        relative = source.relative_to(get_data_root().resolve())
        projected["input_structure"] = canonical_local_redesign_data_alias(
            (Path("data") / relative).as_posix()
        )
    except (OSError, ValueError) as exc:
        raise ContractError("persisted native RFD3 source is outside the allowed workflow roots") from exc
    projected["workflow_adapter"] = RFD3_WORKFLOW_ADAPTER_ID
    return projected


async def terminalize_failed_request_for_job(
    session,
    *,
    job_id: str,
    exit_code: int | None = None,
) -> bool:
    """Bind one published Job failure to its active native RFD3 request.

    The caller owns the transaction and must call this only after the guarded
    Job failure update wins. The post-CAS Job row is the receipt authority.
    """

    from sqlalchemy import select

    from database import Job, RFD3LocalRedesignRequest

    job = await session.get(Job, job_id)
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_local_redesign"
        or job.status != "failed"
    ):
        return False
    request = (
        await session.execute(
            select(RFD3LocalRedesignRequest).where(
                RFD3LocalRedesignRequest.job_id == job_id,
                RFD3LocalRedesignRequest.status.in_(
                    ("prepared", "queued", "running", "generated", "completed")
                ),
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return False

    receipt: dict[str, Any] = {
        "schema": "bms.rfd3.local-redesign.failure-receipt.v1",
        "job_id": str(job.id),
        "status": "failed",
        "error_message": str(job.error_message or "native RFD3 job failed"),
    }
    if exit_code is not None:
        receipt["exit_code"] = int(exit_code)
    now = datetime.utcnow()
    request.status = "failed"
    request.failure_receipt_json = receipt
    request.updated_at = now
    request.terminal_at = now
    await session.flush()
    return True


async def terminalize_completed_request_for_job(session, *, job_id: str) -> bool:
    """Bind one validated Job completion to its generated native request."""

    from sqlalchemy import select

    from database import Job, RFD3LocalRedesignRequest

    job = await session.get(Job, job_id)
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_local_redesign"
        or job.status != "completed"
    ):
        return False
    request = (
        await session.execute(
            select(RFD3LocalRedesignRequest).where(
                RFD3LocalRedesignRequest.job_id == job_id,
                RFD3LocalRedesignRequest.status == "generated",
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return False

    now = datetime.utcnow()
    request.status = "completed"
    request.updated_at = now
    request.terminal_at = now
    await session.flush()
    return True


async def terminalize_cancelled_request_for_job(session, *, job_id: str) -> bool:
    """Bind one authoritative completed cancellation to its native request."""

    from sqlalchemy import select

    from database import Job, RFD3LocalRedesignRequest

    job = await session.get(Job, job_id)
    if (
        job is None
        or str(job.model_id or "").strip().lower() != "protein_local_redesign"
        or job.status != "cancelled"
    ):
        return False
    request = (
        await session.execute(
            select(RFD3LocalRedesignRequest).where(
                RFD3LocalRedesignRequest.job_id == job_id,
                RFD3LocalRedesignRequest.status.in_(
                    ("prepared", "queued", "running", "generated", "completed")
                ),
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return False

    params = dict(job.params or {}) if not isinstance(job.params, str) else {}
    cancellation_receipt = dict(params.get("cancellation_receipt") or {})
    now = datetime.utcnow()
    request.status = "cancelled"
    request.failure_receipt_json = {
        "schema": "bms.rfd3.local-redesign.failure-receipt.v1",
        "job_id": str(job.id),
        "status": "cancelled",
        "error_message": str(job.error_message or "Cancelled by user"),
        "cancellation_receipt": cancellation_receipt,
    }
    request.updated_at = now
    request.terminal_at = now
    await session.flush()
    return True


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


def normalize_local_redesign_params(
    params: Mapping[str, Any],
    *,
    job_name: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve the source and build the immutable request before queueing."""
    normalized = dict(params)
    unexpected = sorted(str(key) for key in normalized if key not in RFD3_SCIENTIFIC_PARAM_KEYS)
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
    source_path = _resolve_local_redesign_source(input_value)
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
    """Copy the source and write the canonical request under the owning job directory."""
    request_value = params.get("rfd3_request")
    if not isinstance(request_value, dict):
        raise ContractError("rfd3_request is missing from normalized job parameters")
    request = deepcopy(request_value)
    input_binding = request.get("input")
    if not isinstance(input_binding, dict) or not isinstance(input_binding.get("path"), str):
        raise ContractError("rfd3_request input binding is missing")
    source_lexical = Path(input_binding["path"]).expanduser()
    expected_source_sha = input_binding.get("sha256")
    if source_lexical.is_symlink():
        raise ContractError("local-redesign source must be a regular non-symlink file")
    source = source_lexical.resolve()
    if not source.is_file():
        raise ContractError("local-redesign source must be a regular non-symlink file")
    output_root_lexical = Path(output_dir).expanduser()
    if output_root_lexical.is_symlink():
        raise ContractError("local-redesign output root must not be a symlink")
    output_root = output_root_lexical.resolve()
    if not output_root.is_dir():
        raise ContractError("local-redesign output root is missing")
    owned_dir = output_root / "external_inputs"
    if owned_dir.exists() and (owned_dir.is_symlink() or not owned_dir.is_dir()):
        raise ContractError("task-owned local-redesign source directory is unsafe")
    owned_dir.mkdir(mode=0o700, exist_ok=True)
    if owned_dir.resolve().parent != output_root:
        raise ContractError("task-owned local-redesign source directory escaped the output root")
    owned_source = owned_dir / source.name
    if owned_source.exists() or owned_source.is_symlink():
        raise ContractError("task-owned local-redesign source already exists")
    temporary_source = owned_dir / f".{source.name}.{uuid.uuid4().hex}.tmp"
    requests_dir = output_root / "requests"
    if requests_dir.exists() and (requests_dir.is_symlink() or not requests_dir.is_dir()):
        raise ContractError("local-redesign request directory is unsafe")
    requests_dir.mkdir(mode=0o700, exist_ok=True)
    if requests_dir.resolve().parent != output_root:
        raise ContractError("local-redesign request directory escaped the output root")
    destination = requests_dir / "rfd3_local_redesign_request.json"
    if destination.exists() or destination.is_symlink():
        raise ContractError("materialized local-redesign request already exists")
    try:
        with source.open("rb") as source_handle, temporary_source.open("xb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        if _sha256_file(temporary_source) != expected_source_sha:
            raise ContractError("task-owned local-redesign source hash mismatch")
        os.replace(temporary_source, owned_source)
        input_binding["path"] = str(owned_source)
        digest = request_sha256(request)
        written_digest = write_request(destination, request)
        if written_digest != digest:
            raise ContractError("canonical request digest changed during materialization")
    except (ContractError, OSError) as exc:
        temporary_source.unlink(missing_ok=True)
        owned_source.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        if isinstance(exc, ContractError):
            raise
        raise ContractError(f"failed to materialize local-redesign source and request: {exc}") from exc

    normalized = dict(params)
    normalized["rfd3_request"] = request
    for key in ("input_structure", "input_pdb", "input_cif", "input", "plr_input_pdb"):
        normalized[key] = str(owned_source)
    normalized["rfd3_request_path"] = str(destination)
    normalized["rfd3_request_sha256"] = digest
    normalized["rfd3_request_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:rfd3-local-redesign:{job_id}"))
    normalized["rfd3_result_contract_id"] = "rfd3_local_redesign_v1"
    return normalized, request, digest, destination


def artifact_media_type(path: Path) -> str:
    if path.name.endswith(".cif.gz"):
        return "chemical/x-mmcif+gzip"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
