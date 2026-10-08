from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from services.aligned_error_utils import BOLTZ_PAE_NPZ_FORMAT, load_aligned_error_artifact

from .archive import (
    MAX_JSON_BYTES,
    ArchiveSafetyError,
    copy_members,
    inspect_sab_archive,
    read_member_bytes,
    sha256_file,
)
from .contracts import ExternalImportPreview


PROVIDER_ID = "boltz_api"
SAB_RESOURCE = "predictions:structure-and-binding"
SUPPORTED_RESOURCES = frozenset({SAB_RESOURCE})
KNOWN_RESOURCES = frozenset(
    {
        SAB_RESOURCE,
        "protein:design",
        "protein:library-screen",
        "small-molecule:design",
        "small-molecule:library-screen",
        "predictions:adme",
    }
)
_SAMPLE_STRUCTURE = re.compile(r"^prediction/sample_([0-9]+)_predicted_structure\.cif$")
_SAMPLE_PAE = re.compile(r"^prediction/sample_([0-9]+)_pae\.npz$")
_SAFE_PROVIDER_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_PAE_DIMENSION = 8192
_MAX_PAE_EXPANDED_BYTES = 512 * 1024**2
_MAX_NPZ_COMPRESSION_RATIO = 200
_SENSITIVE_METADATA_KEY = re.compile(
    r"(?:token|secret|password|authorization|cookie|credential|api[_-]?key|url|uri|endpoint|host|connection)",
    re.IGNORECASE,
)


class BoltzImportError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _read_json_file(path: Path, *, code: str) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink():
        raise BoltzImportError(code, f"required regular file is missing: {path.name}")
    payload = path.read_bytes()
    if len(payload) > 10 * 1024**2:
        raise BoltzImportError(code, f"metadata file is too large: {path.name}")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoltzImportError(code, f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise BoltzImportError(code, f"{path.name} must contain a JSON object")
    return value, payload


def _redact_sensitive_json(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_METADATA_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_sensitive_json(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_json(item) for item in value]
    if isinstance(value, str) and (
        re.search(r"\b(?:Bearer|Basic)\s+", value, re.IGNORECASE)
        or value.startswith(("http://", "https://"))
    ):
        return "[REDACTED]"
    return value


def _resource_from_checkpoint(checkpoint: dict[str, Any], job_id: str) -> str:
    resource = str(checkpoint.get("resource") or "").strip()
    if resource:
        return resource
    if job_id.startswith("sab_pred_"):
        return SAB_RESOURCE
    if job_id.startswith("prot_des_"):
        return "protein:design"
    raise BoltzImportError("PROVIDER_UNSUPPORTED", "cannot establish the Boltz API resource type")


def _normalize_chain_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    chain_ids = [str(item).strip() for item in values if str(item).strip()]
    if not chain_ids or len(chain_ids) != len(set(chain_ids)):
        raise BoltzImportError("INPUT_MANIFEST_MISSING", "each entity requires unique explicit chain IDs")
    return chain_ids


def _parse_entities(run: dict[str, Any]) -> list[dict[str, Any]]:
    input_payload = run.get("input")
    if not isinstance(input_payload, dict) or not isinstance(input_payload.get("entities"), list):
        raise BoltzImportError("INPUT_MANIFEST_MISSING", "run.json.input.entities is required")
    entities: list[dict[str, Any]] = []
    seen_chains: set[str] = set()
    for index, raw in enumerate(input_payload["entities"]):
        if not isinstance(raw, dict):
            raise BoltzImportError("INPUT_MANIFEST_MISSING", f"entity {index} has an unsupported shape")
        if isinstance(raw.get("type"), str):
            molecule_type = str(raw["type"]).strip().lower()
            payload = raw
            chain_value = raw.get("chain_ids")
            sequence = raw.get("value")
        elif len(raw) == 1:
            molecule_type, payload = next(iter(raw.items()))
            molecule_type = str(molecule_type).strip().lower()
            chain_value = payload.get("id") or payload.get("chain_ids") if isinstance(payload, dict) else None
            sequence = payload.get("sequence") if isinstance(payload, dict) else None
        else:
            raise BoltzImportError("INPUT_MANIFEST_MISSING", f"entity {index} has an unsupported shape")
        if molecule_type not in {"protein", "dna", "rna", "ligand", "ion"} or not isinstance(payload, dict):
            raise BoltzImportError("INPUT_MANIFEST_MISSING", f"entity {index} has an unsupported molecule type")
        chain_ids = _normalize_chain_ids(chain_value)
        if seen_chains.intersection(chain_ids):
            raise BoltzImportError("INPUT_MANIFEST_MISSING", "chain IDs must be unique across entities")
        seen_chains.update(chain_ids)
        if molecule_type in {"protein", "dna", "rna"} and not isinstance(sequence, str):
            raise BoltzImportError("INPUT_MANIFEST_MISSING", f"entity {index} is missing its exact sequence")
        normalized = {
            "entity_index": index,
            "molecule_type": molecule_type,
            "chain_ids": chain_ids,
            "sequence": sequence,
        }
        if payload.get("modifications") is not None:
            normalized["modifications"] = payload["modifications"]
        entities.append(normalized)
    if not entities:
        raise BoltzImportError("INPUT_MANIFEST_MISSING", "at least one input entity is required")
    return entities


def _sample_count(run: dict[str, Any], metrics: dict[str, Any]) -> int:
    all_samples = metrics.get("all_sample_results")
    if not isinstance(all_samples, list) or not all_samples:
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "metrics are missing all_sample_results")
    requested = (run.get("input") or {}).get("num_samples")
    if requested is not None and (not isinstance(requested, int) or requested < 1 or requested > 10000):
        raise BoltzImportError("RUN_METADATA_INVALID", "input.num_samples is invalid")
    if requested is not None and requested != len(all_samples):
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "sample count does not match metrics")
    return len(all_samples)


def _load_bounded_pae(payload: bytes, sample_index: int) -> np.ndarray:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "pae.npy":
                raise ValueError("NPZ must contain only pae.npy")
            member = members[0]
            if member.file_size <= 0 or member.file_size > _MAX_PAE_EXPANDED_BYTES:
                raise ValueError("PAE matrix exceeds the expanded-size limit")
            if member.compress_size <= 0 or member.file_size > member.compress_size * _MAX_NPZ_COMPRESSION_RATIO:
                raise ValueError("PAE NPZ exceeds the compression-ratio limit")
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _fortran, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version in {(2, 0), (3, 0)}:
                    shape, _fortran, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError(f"unsupported NPY version {version}")
            if dtype.hasobject or dtype.kind not in "fiu":
                raise ValueError("PAE dtype must be a non-object numeric type")
            if len(shape) != 2 or shape[0] != shape[1] or not (1 <= shape[0] <= _MAX_PAE_DIMENSION):
                raise ValueError("PAE must be a bounded square matrix")
            if int(shape[0]) * int(shape[1]) * int(dtype.itemsize) > _MAX_PAE_EXPANDED_BYTES:
                raise ValueError("PAE allocation exceeds the memory limit")
        with np.load(io.BytesIO(payload), allow_pickle=False) as values:
            matrix = np.asarray(values["pae"], dtype=float)
    except Exception as exc:
        raise BoltzImportError("PAE_INVALID", f"sample {sample_index} PAE is invalid: {exc}") from exc
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.isfinite(matrix).all():
        raise BoltzImportError("PAE_INVALID", f"sample {sample_index} PAE must be a finite square matrix")
    return matrix


def _validate_artifact_set(archive_path: Path, run: dict[str, Any], members: dict[str, int]) -> tuple[int, dict[str, Any]]:
    if "prediction/metrics.json" not in members:
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "archive is missing prediction/metrics.json")
    try:
        metrics = json.loads(read_member_bytes(archive_path, "prediction/metrics.json", max_bytes=MAX_JSON_BYTES))
    except (ArchiveSafetyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", f"metrics are invalid: {exc}") from exc
    if not isinstance(metrics, dict):
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "metrics must be a JSON object")
    count = _sample_count(run, metrics)
    structures = {int(match.group(1)) for name in members if (match := _SAMPLE_STRUCTURE.fullmatch(name))}
    pae_files = {int(match.group(1)) for name in members if (match := _SAMPLE_PAE.fullmatch(name))}
    expected = set(range(count))
    if structures != expected or pae_files != expected:
        raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", "structure/PAE sample set is incomplete or non-contiguous")
    for index in expected:
        payload = read_member_bytes(archive_path, f"prediction/sample_{index}_pae.npz")
        _load_bounded_pae(payload, index)
    return count, metrics


def _canonical_fingerprint(run: dict[str, Any], resource: str, archive_sha256: str) -> str:
    stable_run = {
        key: run.get(key)
        for key in (
            "id",
            "status",
            "model",
            "model_version",
            "engine",
            "engine_version",
            "pipeline",
            "pipeline_version",
            "created_at",
            "started_at",
            "completed_at",
            "input",
        )
    }
    payload = json.dumps(
        {"provider": PROVIDER_ID, "resource": resource, "run": stable_run, "archive_sha256": archive_sha256},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def preview_boltz_api_run(source_dir: Path) -> ExternalImportPreview:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir() or source_dir.is_symlink():
        raise BoltzImportError("SOURCE_NOT_ALLOWED", "source must be a real directory")
    run, run_bytes = _read_json_file(source_dir / "run.json", code="RUN_METADATA_MISSING")
    checkpoint, _ = _read_json_file(source_dir / ".boltz-run.json", code="RUN_METADATA_MISSING")
    job_id = str(run.get("id") or checkpoint.get("job_id") or "").strip()
    if not job_id or checkpoint.get("job_id") not in {None, job_id}:
        raise BoltzImportError("RUN_METADATA_INVALID", "provider job identity is missing or inconsistent")
    if not _SAFE_PROVIDER_JOB_ID.fullmatch(job_id):
        raise BoltzImportError("RUN_METADATA_INVALID", "provider job identity contains unsafe characters")
    resource = _resource_from_checkpoint(checkpoint, job_id)
    status = str(run.get("status") or "").strip().lower()
    run_sha = hashlib.sha256(run_bytes).hexdigest()
    if resource not in SUPPORTED_RESOURCES:
        return ExternalImportPreview(
            provider=PROVIDER_ID,
            resource_type=resource,
            provider_job_id=job_id,
            model=str(run.get("model") or run.get("engine") or "") or None,
            model_version=str(run.get("model_version") or run.get("engine_version") or "") or None,
            status=status,
            sample_count=0,
            entities=[],
            source_fingerprint=hashlib.sha256(f"{PROVIDER_ID}:{resource}:{job_id}:{run_sha}".encode()).hexdigest(),
            run_metadata_sha256=run_sha,
            archive_sha256=None,
            importable=False,
            error_code="RESOURCE_UNSUPPORTED",
            errors=[f"Boltz API resource is detected but not supported for ingestion: {resource}"],
            provider_metadata={"data_deleted_at": run.get("data_deleted_at")},
        )
    if status != "succeeded":
        raise BoltzImportError("REMOTE_STATUS_NOT_SUCCEEDED", f"remote status is {status or 'missing'}")
    entities = _parse_entities(run)
    archive_path = source_dir / "outputs" / "archive.tar.gz"
    try:
        inventory = inspect_sab_archive(archive_path)
    except ArchiveSafetyError as exc:
        raise BoltzImportError("ARCHIVE_UNSAFE", str(exc)) from exc
    sample_count, _metrics = _validate_artifact_set(archive_path, run, inventory.members)
    fingerprint = _canonical_fingerprint(run, resource, inventory.archive_sha256)
    return ExternalImportPreview(
        provider=PROVIDER_ID,
        resource_type=resource,
        provider_job_id=job_id,
        model=str(run.get("model") or run.get("engine") or "") or None,
        model_version=str(run.get("model_version") or run.get("engine_version") or "") or None,
        status=status,
        sample_count=sample_count,
        entities=entities,
        source_fingerprint=fingerprint,
        run_metadata_sha256=run_sha,
        archive_sha256=inventory.archive_sha256,
        importable=True,
        provider_metadata={
            key: run.get(key)
            for key in ("workspace_id", "created_at", "started_at", "completed_at", "data_deleted_at")
        },
    )


def normalize_boltz_api_run(source_dir: Path, data_root: Path, preview: ExternalImportPreview) -> dict[str, Any]:
    if not preview.importable or preview.resource_type != SAB_RESOURCE:
        raise BoltzImportError("RESOURCE_UNSUPPORTED", preview.resource_type)
    current = preview_boltz_api_run(source_dir)
    if current.source_fingerprint != preview.source_fingerprint:
        raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", "source changed after preview")
    run, _ = _read_json_file(source_dir / "run.json", code="RUN_METADATA_MISSING")
    archive_path = source_dir / "outputs" / "archive.tar.gz"
    inventory = inspect_sab_archive(archive_path)
    _, metrics = _validate_artifact_set(archive_path, run, inventory.members)

    resource_slug = preview.resource_type.replace(":", "-")
    final_root = (data_root / "external_imports" / PROVIDER_ID / resource_slug / preview.provider_job_id / preview.source_fingerprint).resolve()
    final_root.parent.mkdir(parents=True, exist_ok=True)
    if final_root.exists():
        manifest_path = final_root / "normalized" / "import-manifest.json"
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text())
            if existing.get("source", {}).get("source_fingerprint") == preview.source_fingerprint:
                return existing
        raise BoltzImportError("IMPORT_IDENTITY_CONFLICT", "normalized destination already exists with different evidence")

    staging = Path(tempfile.mkdtemp(prefix=f".{preview.provider_job_id}.", dir=final_root.parent))
    try:
        source_out = staging / "source"
        source_out.mkdir()
        sanitized_run = _redact_sensitive_json(_read_json_file(source_dir / "run.json", code="RUN_METADATA_INVALID")[0])
        sanitized_checkpoint = _redact_sensitive_json(
            _read_json_file(source_dir / ".boltz-run.json", code="CHECKPOINT_INVALID")[0]
        )
        (source_out / "run.json").write_text(json.dumps(sanitized_run, indent=2, sort_keys=True))
        (source_out / "downloader-checkpoint.json").write_text(
            json.dumps(sanitized_checkpoint, indent=2, sort_keys=True)
        )
        shutil.copy2(archive_path, source_out / "archive.tar.gz")
        member_names = sorted(inventory.members)
        artifacts = copy_members(archive_path, member_names, staging / "artifacts")

        samples: list[dict[str, Any]] = []
        descriptors: list[dict[str, Any]] = []
        chain_types = {
            chain: entity["molecule_type"]
            for entity in preview.entities
            for chain in entity["chain_ids"]
        }
        all_results = metrics["all_sample_results"]
        best_metrics = metrics.get("best_sample", {}).get("metrics")
        for index in range(preview.sample_count):
            structure_name = f"prediction/sample_{index}_predicted_structure.cif"
            pae_name = f"prediction/sample_{index}_pae.npz"
            structure = artifacts[structure_name]
            pae = artifacts[pae_name]
            artifact = load_aligned_error_artifact(
                aligned_error_path=pae,
                aligned_error_format=BOLTZ_PAE_NPZ_FORMAT,
                matrix_key="pae",
                structure_path=structure,
            )
            observed_chains = {residue.chain_id for residue in artifact.residues}
            if observed_chains != set(chain_types):
                raise BoltzImportError(
                    "CHAIN_MAPPING_MISMATCH",
                    f"sample {index} chains {sorted(observed_chains)} do not match input {sorted(chain_types)}",
                )
            raw = all_results[index].get("metrics") if isinstance(all_results[index], dict) else None
            if not isinstance(raw, dict) or not all(
                isinstance(value, (int, float)) and math.isfinite(float(value)) for value in raw.values()
            ):
                raise BoltzImportError("ARTIFACT_SET_INCOMPLETE", f"sample {index} metrics are invalid")
            sample_id = f"sample_{index}"
            samples.append(
                {
                    "sample_id": sample_id,
                    "rank": index,
                    "is_best_sample": raw == best_metrics,
                    "provider_metrics": raw,
                    "canonical_metrics": {
                        "plddt_overall": float(raw["complex_plddt"]) * 100.0 if raw.get("complex_plddt") is not None else None,
                        "ptm": float(raw["ptm"]) if raw.get("ptm") is not None else None,
                        "conf_score": float(raw["structure_confidence"]) if raw.get("structure_confidence") is not None else None,
                    },
                    "structure_path": str(Path("artifacts") / structure.name),
                    "aligned_error_path": str(Path("artifacts") / pae.name),
                }
            )
            for kind, fmt, path, extra in (
                ("structure", "mmcif", structure, {}),
                ("aligned_error", BOLTZ_PAE_NPZ_FORMAT, pae, {"matrix_key": "pae"}),
            ):
                descriptors.append(
                    {
                        "sample_id": sample_id,
                        "kind": kind,
                        "format": fmt,
                        "path": str(path.relative_to(staging)),
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                        **extra,
                    }
                )

        manifest = {
            "schema": "bms.external-result.v1",
            "schema_version": 1,
            "provider": {
                "id": PROVIDER_ID,
                "resource": preview.resource_type,
                "job_id": preview.provider_job_id,
                "model": preview.model,
                "provider_version": preview.model_version,
                "status": preview.status,
                **preview.provider_metadata,
            },
            "input": {"entities": preview.entities, "num_samples": preview.sample_count},
            "artifacts": descriptors,
            "samples": samples,
            "source": {
                "run_metadata_sha256": preview.run_metadata_sha256,
                "archive_sha256": preview.archive_sha256,
                "source_fingerprint": preview.source_fingerprint,
            },
        }
        normalized = staging / "normalized"
        normalized.mkdir()
        manifest_path = normalized / "import-manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        with manifest_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, final_root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
