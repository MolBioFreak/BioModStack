"""Typed native RFD3 general-generation request authority."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any, Mapping


REQUEST_SCHEMA = "bms.rfd3.generation.request.v1"
RESULT_SCHEMA = "bms.rfd3.generation.result-manifest.v1"
READ_MODEL_SCHEMA = "bms.rfd3.generation.read-model.v1"
_ALLOWED_INPUTS = {
    "generator",
    "generation_mode",
    "min_length",
    "max_length",
    "num_designs",
    "seed",
    "dump_trajectories",
    "modification_mode",
}


class GenerationContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GenerationContractError(f"request is not canonical JSON: {exc}") from exc


def request_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise GenerationContractError(f"{name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise GenerationContractError(f"{name} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise GenerationContractError(f"{name} must be within [{minimum}, {maximum}]")
    return normalized


def normalize_generation_params(
    params: Mapping[str, Any], *, job_name: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    raw = dict(params)
    unexpected = sorted(str(key) for key in raw if key not in _ALLOWED_INPUTS)
    if unexpected:
        raise GenerationContractError(f"unsupported general RFD3 parameters: {', '.join(unexpected)}")
    generator = str(raw.get("generator") or "rfd3").strip().lower()
    if generator != "rfd3":
        raise GenerationContractError("native general-generation contract requires generator=rfd3")
    generation_mode = str(raw.get("generation_mode") or "unconditional_monomer").strip().lower()
    if generation_mode != "unconditional_monomer":
        raise GenerationContractError("generation_mode must be unconditional_monomer")
    minimum = _int(raw.get("min_length", 100), "min_length", minimum=40, maximum=600)
    maximum = _int(raw.get("max_length", 200), "max_length", minimum=40, maximum=600)
    if minimum > maximum:
        raise GenerationContractError("min_length must be less than or equal to max_length")
    num_designs = _int(raw.get("num_designs", 8), "num_designs", minimum=1, maximum=200)
    seed = _int(raw.get("seed", 0), "seed", minimum=0, maximum=2_147_483_647)
    dump_value = raw.get("dump_trajectories", False)
    if not isinstance(dump_value, bool):
        raise GenerationContractError("dump_trajectories must be a boolean")
    name = str(job_name or "").strip()
    if not name:
        raise GenerationContractError("job_name is required")
    request = {
        "schema": REQUEST_SCHEMA,
        "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:rfd3-generation-preview:{name}")),
        "job_id": "validation-preview",
        "generation": {
            "min_length": minimum,
            "max_length": maximum,
            "num_designs": num_designs,
        },
        "execution": {"seed": seed, "dump_trajectories": dump_value},
    }
    digest = request_sha256(request)
    normalized = {
        **raw,
        "generator": "rfd3",
        "generation_mode": generation_mode,
        "modification_mode": "de_novo_design",
        "min_length": minimum,
        "max_length": maximum,
        "num_designs": num_designs,
        "seed": seed,
        "dump_trajectories": dump_value,
        "rfd3_generation_request": request,
        "rfd3_generation_request_sha256": digest,
        "rfd3_generation_request_schema": REQUEST_SCHEMA,
        "diffusion_method": "rfd3",
        "rfd_mode": "monomer_denovo",
        "rfd_contigs": f"[{minimum}-{maximum}]",
        "rfd_num_designs": num_designs,
        "rfd3_batches_per_design": num_designs,
        "rfd3_generation_min_length": minimum,
        "rfd3_generation_max_length": maximum,
        "rfd3_generation_num_designs": num_designs,
        "rfd3_generation_seed": seed,
        "rfd3_generation_dump_trajectories": dump_value,
        "run_rfd_only": True,
        "skip_rfd": False,
        "skip_rfd_seq": False,
        "skip_rfd_seq_pred": False,
        "run_frustrampnn": False,
    }
    return normalized, request, digest


def materialize_generation_request(
    params: Mapping[str, Any], *, output_dir: str | Path, job_id: str
) -> tuple[dict[str, Any], Path]:
    request_template = params.get("rfd3_generation_request")
    if not isinstance(request_template, dict) or request_template.get("schema") != REQUEST_SCHEMA:
        raise GenerationContractError("normalized RFD3 generation request is missing")
    expected_digest = str(params.get("rfd3_generation_request_sha256") or "")
    digest = request_sha256(request_template)
    if digest != expected_digest:
        raise GenerationContractError("RFD3 generation request digest mismatch")
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:rfd3-generation:{job_id}"))
    request = {
        **request_template,
        "request_id": request_id,
        "job_id": str(job_id),
    }
    digest = request_sha256(request)
    root = Path(output_dir).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise GenerationContractError("RFD3 generation output root is missing or unsafe")
    request_dir = root / "requests"
    request_dir.mkdir(mode=0o700, exist_ok=True)
    if request_dir.is_symlink() or request_dir.resolve().parent != root:
        raise GenerationContractError("RFD3 generation request directory is unsafe")
    destination = request_dir / "rfd3_generation_request.json"
    if destination.exists() or destination.is_symlink():
        raise GenerationContractError("materialized RFD3 generation request already exists")
    temporary = request_dir / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    payload = canonical_json(request) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise GenerationContractError(f"failed to materialize RFD3 generation request: {exc}") from exc
    normalized = dict(params)
    normalized["rfd3_generation_request"] = request
    normalized["rfd3_generation_request_path"] = str(destination)
    normalized["rfd3_generation_request_id"] = request_id
    normalized["rfd3_generation_request_sha256"] = digest
    normalized["rfd3_generation_result_contract_id"] = "rfd3_generation_v1"
    return normalized, destination


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GenerationContractError(f"{label} must contain exact fields")
    return value


def _finite_number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationContractError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < minimum:
        raise GenerationContractError(f"{label} is outside its valid range")
    return normalized


def _metric_summary(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "mean": round(sum(values) / len(values), 6),
        "max": max(values),
    }


def validate_result_manifest(
    value: Any,
    *,
    request: Mapping[str, Any],
    output_root: str | Path,
    job_id: str,
) -> dict[str, Any]:
    """Validate the exact native producer manifest and every referenced byte."""
    manifest = _exact_object(
        value,
        {"schema", "job_id", "request_id", "request_sha256", "aggregate", "candidates"},
        "RFD3 generation result manifest",
    )
    if manifest["schema"] != RESULT_SCHEMA:
        raise GenerationContractError("unsupported RFD3 generation result manifest schema")
    if manifest["job_id"] != str(job_id) or request.get("job_id") != str(job_id):
        raise GenerationContractError("RFD3 generation job binding mismatch")
    if manifest["request_id"] != request.get("request_id"):
        raise GenerationContractError("RFD3 generation request binding mismatch")
    if manifest["request_sha256"] != request_sha256(request):
        raise GenerationContractError("RFD3 generation request hash mismatch")
    candidates = manifest["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise GenerationContractError("RFD3 generation candidates must be a non-empty array")
    requested = request.get("generation", {}).get("num_designs")
    if isinstance(requested, bool) or not isinstance(requested, int) or requested != len(candidates):
        raise GenerationContractError("RFD3 generation candidate cardinality mismatch")

    root = Path(output_root).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise GenerationContractError("RFD3 generation output root is missing or unsafe")
    seen_ids: set[str] = set()
    validated_candidates: list[dict[str, Any]] = []
    lengths: list[float] = []
    radii: list[float] = []
    accepted_count = 0
    for raw_candidate in candidates:
        candidate = _exact_object(
            raw_candidate,
            {"candidate_id", "accepted", "metrics", "artifact_manifest_sha256", "artifacts"},
            "RFD3 generation candidate",
        )
        candidate_id = str(candidate["candidate_id"] or "")
        if not candidate_id or len(candidate_id) > 255 or candidate_id in seen_ids:
            raise GenerationContractError("RFD3 generation candidate identity is invalid")
        seen_ids.add(candidate_id)
        if not isinstance(candidate["accepted"], bool):
            raise GenerationContractError("RFD3 generation candidate accepted flag must be boolean")
        accepted_count += int(candidate["accepted"])
        metrics = _exact_object(
            candidate["metrics"],
            {"residue_count", "chain_count", "radius_of_gyration", "helix_count", "strand_count"},
            "RFD3 generation candidate metrics",
        )
        residue_count = _finite_number(metrics["residue_count"], "residue_count", minimum=1)
        _finite_number(metrics["chain_count"], "chain_count", minimum=1)
        radius = _finite_number(metrics["radius_of_gyration"], "radius_of_gyration")
        for optional_name in ("helix_count", "strand_count"):
            optional_value = metrics[optional_name]
            if optional_value is not None:
                _finite_number(optional_value, optional_name)
        lengths.append(residue_count)
        radii.append(radius)

        artifacts = candidate["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) != 2:
            raise GenerationContractError("RFD3 generation candidate must declare two artifacts")
        if candidate["artifact_manifest_sha256"] != _json_sha256(artifacts):
            raise GenerationContractError("RFD3 generation artifact manifest hash mismatch")
        roles: set[str] = set()
        validated_artifacts: list[dict[str, Any]] = []
        for raw_artifact in artifacts:
            artifact = _exact_object(
                raw_artifact,
                {"role", "relative_path", "sha256", "bytes", "media_type"},
                "RFD3 generation artifact",
            )
            role = str(artifact["role"] or "")
            if role not in {"candidate_structure", "candidate_metadata"} or role in roles:
                raise GenerationContractError("RFD3 generation artifact roles are invalid")
            roles.add(role)
            relative = Path(str(artifact["relative_path"] or ""))
            relative_text = relative.as_posix()
            if (
                relative.is_absolute()
                or relative_text != artifact["relative_path"]
                or any(part in {"", ".", ".."} for part in relative.parts)
                or "\\" in str(artifact["relative_path"])
            ):
                raise GenerationContractError("RFD3 generation artifact path is unsafe")
            current = root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise GenerationContractError("RFD3 generation artifact path contains a symlink")
            resolved = current.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise GenerationContractError("RFD3 generation artifact is unavailable")
            size = resolved.stat().st_size
            if isinstance(artifact["bytes"], bool) or artifact["bytes"] != size:
                raise GenerationContractError("RFD3 generation artifact byte count mismatch")
            digest = file_sha256(resolved)
            if digest != artifact["sha256"]:
                raise GenerationContractError("RFD3 generation artifact hash mismatch")
            validated_artifacts.append({**artifact, "resolved_path": str(resolved)})
        validated_candidates.append({**candidate, "artifacts": validated_artifacts})

    expected_aggregate = {
        "requested": requested,
        "generated": len(candidates),
        "accepted": accepted_count,
        "length": _metric_summary(lengths),
        "radius_of_gyration": _metric_summary(radii),
    }
    if manifest["aggregate"] != expected_aggregate:
        raise GenerationContractError("RFD3 generation aggregate statistics mismatch")
    return {**manifest, "candidates": validated_candidates}
