"""Sole admission and materialization authority for canonical CM requests."""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    ContractValidationError,
    canonical_json_bytes,
    canonical_sha256,
    validate_schema,
    validate_seed_sources,
)


BACKENDS = frozenset({"protenix_v2_ensemble", "confornets", "external_import"})
_TOP_LEVEL_FIELDS = frozenset(
    {
        "backend",
        "targets",
        "ordered_seeds",
        "generated_json_ordered_seeds",
        "cli_ordered_seeds",
        "samples_per_seed",
        "feature_policy",
        "runtime_policy",
        "analysis_policy",
        "confornets",
        "protenix_snapshot_id",
        "import_receipt_id",
    }
)
_CONFORNETS_FIELDS = frozenset(
    {
        "sequence",
        "chain_id",
        "task",
        "test_case_id",
        "benchmark_name",
        "references",
        "runs",
        "saved_steps",
        "confornet_count",
        "samples",
        "max_steps",
        "num_recycles",
        "num_diffusion_steps",
        "learning_rate",
        "gradient_clip",
        "skip_msa",
        "compute_confidence",
        "save_full_confidence",
        "compute_evaluation",
        "checkpoint",
        "config",
        "transfer_source",
        "backend_identity",
    }
)
_PROTEIN_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWYX")


class ConformationalMappingRequestError(ValueError):
    """The request cannot be admitted without guessing or dropping data."""


@dataclass(frozen=True)
class ValidatedRequest:
    request_fields: dict[str, Any]
    coordinate_plan: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MaterializedRequest:
    request_path: Path
    coordinate_plan_path: Path
    launch_params: dict[str, str]


def _strict_object(
    value: object,
    *,
    field: str,
    allowed_fields: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConformationalMappingRequestError(f"{field} must be an object")
    normalized = dict(value)
    unknown = sorted(set(normalized) - allowed_fields)
    if unknown:
        raise ConformationalMappingRequestError(
            f"unknown {field} fields fail closed: {', '.join(unknown)}"
        )
    return normalized


def _strict_positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConformationalMappingRequestError(f"{field} must be a positive integer")
    return value


def _strict_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConformationalMappingRequestError(f"{field} must be a nonnegative integer")
    return value


def _strict_nonempty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConformationalMappingRequestError(f"{field} must be nonempty")
    return value.strip()


def _strict_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConformationalMappingRequestError(f"{field} must be a boolean")
    return value


def _sha256(value: object, *, field: str) -> str:
    text = _strict_nonempty_string(value, field=field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ConformationalMappingRequestError(f"{field} must be a lowercase SHA-256")
    return text


def _validate_staged_record(value: object, *, field: str) -> dict[str, Any]:
    record = _strict_object(
        value,
        field=field,
        allowed_fields=frozenset({"path", "sha256"}),
    )
    return {
        "path": _strict_nonempty_string(record.get("path"), field=f"{field}.path"),
        "sha256": _sha256(record.get("sha256"), field=f"{field}.sha256"),
    }


def _normalize_confornets_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    config = _strict_object(
        settings,
        field="confornets",
        allowed_fields=_CONFORNETS_FIELDS,
    )
    missing = sorted(_CONFORNETS_FIELDS - set(config))
    if missing:
        raise ConformationalMappingRequestError(
            f"confornets settings are incomplete: missing {', '.join(missing)}"
        )

    sequence = _strict_nonempty_string(config["sequence"], field="confornets.sequence").upper()
    if any(residue not in _PROTEIN_ALPHABET for residue in sequence):
        raise ConformationalMappingRequestError(
            "ConforNets requires one authorized single-chain protein sequence"
        )
    chain_id = _strict_nonempty_string(config["chain_id"], field="confornets.chain_id")
    if len(chain_id) > 8:
        raise ConformationalMappingRequestError("confornets.chain_id is too long")
    task = _strict_nonempty_string(config["task"], field="confornets.task").lower()
    if task not in {"diversity", "mse", "transfer"}:
        raise ConformationalMappingRequestError("unknown ConforNets task")

    references = config["references"]
    if not isinstance(references, list):
        raise ConformationalMappingRequestError("confornets.references must be an array")
    if len(references) > 2:
        raise ConformationalMappingRequestError("ConforNets supports at most two staged references")
    normalized_references: list[dict[str, Any]] = []
    for index, value in enumerate(references):
        field = f"confornets.references[{index}]"
        reference = _strict_object(
            value,
            field=field,
            allowed_fields=frozenset(
                {"reference_id", "staged_path", "content_sha256", "state", "source"}
            ),
        )
        normalized_references.append(
            {
                "reference_id": _strict_nonempty_string(
                    reference.get("reference_id"), field=f"{field}.reference_id"
                ),
                "staged_path": _strict_nonempty_string(
                    reference.get("staged_path"), field=f"{field}.staged_path"
                ),
                "content_sha256": _sha256(
                    reference.get("content_sha256"), field=f"{field}.content_sha256"
                ),
                "state": _strict_nonempty_string(reference.get("state"), field=f"{field}.state"),
                "source": _strict_nonempty_string(
                    reference.get("source"), field=f"{field}.source"
                ),
            }
        )
    reference_ids = [reference["reference_id"] for reference in normalized_references]
    if len(set(reference_ids)) != len(reference_ids):
        raise ConformationalMappingRequestError("ConforNets reference IDs must be unique")

    runs = _strict_positive_int(config["runs"], field="confornets.runs")
    confornet_count = _strict_positive_int(
        config["confornet_count"], field="confornets.confornet_count"
    )
    samples = _strict_positive_int(config["samples"], field="confornets.samples")
    max_steps = _strict_positive_int(config["max_steps"], field="confornets.max_steps")
    saved_steps = config["saved_steps"]
    if not isinstance(saved_steps, list) or not saved_steps:
        raise ConformationalMappingRequestError("confornets.saved_steps must be nonempty")
    normalized_steps = [
        _strict_nonnegative_int(step, field="confornets.saved_steps") for step in saved_steps
    ]
    if len(set(normalized_steps)) != len(normalized_steps):
        raise ConformationalMappingRequestError("confornets.saved_steps must be unique")
    if max(normalized_steps) > max_steps:
        raise ConformationalMappingRequestError("saved steps exceed confornets.max_steps")

    if task == "diversity" and confornet_count < 2:
        raise ConformationalMappingRequestError(
            "ConforNets diversity requires at least 2 ConforNets"
        )
    if task == "mse" and not normalized_references:
        raise ConformationalMappingRequestError(
            "ConforNets MSE requires a non-null staged reference"
        )

    transfer = config["transfer_source"]
    normalized_transfer: dict[str, Any] | None = None
    if transfer is not None:
        record = _strict_object(
            transfer,
            field="confornets.transfer_source",
            allowed_fields=frozenset(
                {"kind", "staged_path", "content_sha256", "source_test_cases"}
            ),
        )
        kind = _strict_nonempty_string(record.get("kind"), field="transfer_source.kind")
        if kind not in {"confornet_state", "mse_state"}:
            raise ConformationalMappingRequestError("unknown ConforNets transfer source kind")
        normalized_transfer = {
            "kind": kind,
            "staged_path": _strict_nonempty_string(
                record.get("staged_path"), field="transfer_source.staged_path"
            ),
            "content_sha256": _sha256(
                record.get("content_sha256"), field="transfer_source.content_sha256"
            ),
            "source_test_cases": str(record.get("source_test_cases") or ""),
        }
    if task == "transfer" and normalized_transfer is None:
        raise ConformationalMappingRequestError(
            "ConforNets transfer requires an authenticated transfer source"
        )
    if task != "transfer" and normalized_transfer is not None:
        raise ConformationalMappingRequestError(
            "ConforNets transfer source is invalid for this task"
        )

    identity = _strict_object(
        config["backend_identity"],
        field="confornets.backend_identity",
        allowed_fields=frozenset(
            {
                "backend_version",
                "backend_commit",
                "runtime_identity",
                "container_digest",
                "model_id",
                "feature_identity_sha256",
                "repo_path",
            }
        ),
    )
    container_digest = _strict_nonempty_string(
        identity.get("container_digest"), field="backend_identity.container_digest"
    )
    if not container_digest.startswith("sha256:"):
        raise ConformationalMappingRequestError("container_digest must use sha256 identity")
    _sha256(container_digest[7:], field="backend_identity.container_digest")

    for numeric_field in ("learning_rate", "gradient_clip"):
        value = config[numeric_field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConformationalMappingRequestError(
                f"confornets.{numeric_field} must be positive"
            )

    return {
        "sequence": sequence,
        "chain_id": chain_id,
        "task": task,
        "test_case_id": _strict_nonempty_string(
            config["test_case_id"], field="confornets.test_case_id"
        ),
        "benchmark_name": _strict_nonempty_string(
            config["benchmark_name"], field="confornets.benchmark_name"
        ),
        "references": normalized_references,
        "runs": runs,
        "saved_steps": normalized_steps,
        "confornet_count": confornet_count,
        "samples": samples,
        "max_steps": max_steps,
        "num_recycles": _strict_nonnegative_int(
            config["num_recycles"], field="confornets.num_recycles"
        ),
        "num_diffusion_steps": _strict_positive_int(
            config["num_diffusion_steps"], field="confornets.num_diffusion_steps"
        ),
        "learning_rate": float(config["learning_rate"]),
        "gradient_clip": float(config["gradient_clip"]),
        "skip_msa": _strict_bool(config["skip_msa"], field="confornets.skip_msa"),
        "compute_confidence": _strict_bool(
            config["compute_confidence"], field="confornets.compute_confidence"
        ),
        "save_full_confidence": _strict_bool(
            config["save_full_confidence"], field="confornets.save_full_confidence"
        ),
        "compute_evaluation": _strict_bool(
            config["compute_evaluation"], field="confornets.compute_evaluation"
        ),
        "checkpoint": _validate_staged_record(
            config["checkpoint"], field="confornets.checkpoint"
        ),
        "config": (
            None
            if config["config"] is None
            else _validate_staged_record(config["config"], field="confornets.config")
        ),
        "transfer_source": normalized_transfer,
        "backend_identity": {
            "backend_version": _strict_nonempty_string(
                identity.get("backend_version"), field="backend_identity.backend_version"
            ),
            "backend_commit": _strict_nonempty_string(
                identity.get("backend_commit"), field="backend_identity.backend_commit"
            ),
            "runtime_identity": _strict_nonempty_string(
                identity.get("runtime_identity"), field="backend_identity.runtime_identity"
            ),
            "container_digest": container_digest,
            "model_id": _strict_nonempty_string(
                identity.get("model_id"), field="backend_identity.model_id"
            ),
            "feature_identity_sha256": _sha256(
                identity.get("feature_identity_sha256"),
                field="backend_identity.feature_identity_sha256",
            ),
            "repo_path": _strict_nonempty_string(
                identity.get("repo_path"), field="backend_identity.repo_path"
            ),
        },
    }


def build_confornets_coordinate_plan(
    settings: Mapping[str, Any],
    *,
    target_id: str,
) -> list[dict[str, Any]]:
    """Build the complete ordered ConforNets coordinate product."""

    config = _normalize_confornets_settings(settings)
    task = config["task"]
    test_case_id = config["test_case_id"]
    reference_ids = [reference["reference_id"] for reference in config["references"]] or [None]
    runs = config["runs"]
    confornet_count = config["confornet_count"]
    samples = config["samples"]
    saved_steps = config["saved_steps"]

    coordinates: list[dict[str, Any]] = []
    for reference_id in reference_ids:
        for run_index in range(runs):
            for saved_step in saved_steps:
                for confornet_index in range(confornet_count):
                    for sample_index in range(samples):
                        coordinates.append(
                            {
                                "backend": "confornets",
                                "target_id": target_id,
                                "task": task,
                                "test_case_id": test_case_id,
                                "reference_id": reference_id,
                                "run_index": run_index,
                                "saved_step": saved_step,
                                "confornet_index": confornet_index,
                                "sample_index": sample_index,
                            }
                        )

    expected_cardinality = (
        len(reference_ids) * runs * len(saved_steps) * confornet_count * samples
    )
    if len(coordinates) != expected_cardinality:
        raise ConformationalMappingRequestError("ConforNets coordinate cardinality mismatch")
    return coordinates


def validate_request_params(params: Mapping[str, Any]) -> ValidatedRequest:
    """Validate API controls without writing files or scheduling work."""

    values = _strict_object(params, field="request", allowed_fields=_TOP_LEVEL_FIELDS)
    required = {
        "backend",
        "targets",
        "ordered_seeds",
        "samples_per_seed",
        "feature_policy",
        "runtime_policy",
        "analysis_policy",
    }
    missing = sorted(required - set(values))
    if missing:
        raise ConformationalMappingRequestError(
            f"canonical request is incomplete: missing {', '.join(missing)}"
        )

    backend = values["backend"]
    if backend not in BACKENDS:
        raise ConformationalMappingRequestError(f"unknown backend: {backend!r}")

    api_seeds = values["ordered_seeds"]
    generated_seeds = values.get("generated_json_ordered_seeds", api_seeds)
    cli_seeds = values.get("cli_ordered_seeds", api_seeds)
    try:
        ordered_seeds = validate_seed_sources(
            api=api_seeds,
            generated_json=generated_seeds,
            cli=cli_seeds,
        )
    except (ContractValidationError, TypeError) as exc:
        raise ConformationalMappingRequestError(str(exc)) from exc

    samples_per_seed = _strict_positive_int(
        values["samples_per_seed"], field="samples_per_seed"
    )
    targets = values["targets"]
    if not isinstance(targets, list):
        raise ConformationalMappingRequestError("targets must be an ordered array")

    request_fields = {
        "backend": backend,
        "targets": targets,
        "ordered_seeds": ordered_seeds,
        "samples_per_seed": samples_per_seed,
        "feature_policy": values["feature_policy"],
        "runtime_policy": values["runtime_policy"],
        "analysis_policy": values["analysis_policy"],
    }

    coordinate_plan: list[dict[str, Any]] = []
    if backend == "confornets":
        if len(targets) != 1:
            raise ConformationalMappingRequestError(
                "ConforNets requires exactly one single-chain protein target"
            )
        if "confornets" not in values:
            raise ConformationalMappingRequestError("confornets settings are required")
        target = targets[0]
        if not isinstance(target, Mapping) or not isinstance(target.get("target_id"), str):
            raise ConformationalMappingRequestError("ConforNets target identity is invalid")
        settings = _normalize_confornets_settings(values["confornets"])
        if (
            target.get("sequence") != settings["sequence"]
            or target.get("molecule_type") != "protein"
            or target.get("chain_count") != 1
        ):
            raise ConformationalMappingRequestError(
                "ConforNets requires exactly one authorized single-chain protein target sequence"
            )
        if len(ordered_seeds) != 1:
            raise ConformationalMappingRequestError(
                "ConforNets requires exactly one explicit ordered seed"
            )
        if samples_per_seed != settings["samples"]:
            raise ConformationalMappingRequestError(
                "samples_per_seed must match confornets.samples"
            )
        request_fields["confornets"] = settings
        coordinate_plan = build_confornets_coordinate_plan(
            settings,
            target_id=target["target_id"],
        )
    elif "confornets" in values:
        raise ConformationalMappingRequestError(
            "confornets controls are invalid for the selected backend"
        )

    if backend != "protenix_v2_ensemble" and values.get("protenix_snapshot_id") not in (None, ""):
        raise ConformationalMappingRequestError(
            "protenix_snapshot_id is invalid for the selected backend"
        )
    if backend != "external_import" and values.get("import_receipt_id") not in (None, ""):
        raise ConformationalMappingRequestError(
            "import_receipt_id is invalid for the selected backend"
        )
    if backend == "protenix_v2_ensemble" and values.get("protenix_snapshot_id") not in (None, ""):
        request_fields["protenix_snapshot_id"] = _strict_nonempty_string(
            values["protenix_snapshot_id"], field="protenix_snapshot_id"
        )
    if backend == "external_import" and values.get("import_receipt_id") not in (None, ""):
        request_fields["import_receipt_id"] = _strict_nonempty_string(
            values["import_receipt_id"], field="import_receipt_id"
        )

    # Exercise the Phase 1 executable schema with a temporary valid identity/hash.
    preview = {
        "schema_name": "cm_request",
        "schema_version": 1,
        "request_id": "00000000-0000-4000-8000-000000000000",
        **request_fields,
        "source": {"kind": "api_submission_v1", "sha256": "1" * 64},
        "created_by": {"principal_id": "biomodstack-api"},
        "request_sha256": "0" * 64,
    }
    preview["request_sha256"] = canonical_sha256(
        {key: value for key, value in preview.items() if key != "request_sha256"}
    )
    try:
        validate_schema("cm_request_v1", preview)
    except ContractValidationError as exc:
        raise ConformationalMappingRequestError(str(exc)) from exc
    return ValidatedRequest(
        request_fields=request_fields,
        coordinate_plan=tuple(coordinate_plan),
    )


def _stage_canonical_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _stage_bytes(path: Path, payload: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".rollback", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_canonical_json_pair(
    first_path: Path,
    first_payload: Mapping[str, Any],
    second_path: Path,
    second_payload: Mapping[str, Any],
) -> None:
    """Publish both authorities or restore the exact pre-publication pair."""

    if first_path.parent != second_path.parent:
        raise ConformationalMappingRequestError("request authority pair must share one directory")
    first_path.parent.mkdir(parents=True, exist_ok=True)
    destinations = (first_path, second_path)
    existence = tuple(path.exists() for path in destinations)
    if existence[0] != existence[1]:
        raise ConformationalMappingRequestError(
            "refusing to overwrite an incomplete existing request authority pair"
        )
    previous: dict[Path, bytes | None] = {}
    for destination in destinations:
        if destination.is_symlink():
            raise ConformationalMappingRequestError(
                f"request authority destination may not be a symlink: {destination}"
            )
        if destination.exists() and not destination.is_file():
            raise ConformationalMappingRequestError(
                f"request authority destination must be a regular file: {destination}"
            )
        previous[destination] = destination.read_bytes() if destination.exists() else None

    staged_new = [
        _stage_canonical_json(first_path, first_payload),
        _stage_canonical_json(second_path, second_payload),
    ]
    staged_previous: dict[Path, Path] = {
        destination: _stage_bytes(destination, payload)
        for destination, payload in previous.items()
        if payload is not None
    }
    published: list[Path] = []
    try:
        for temporary, destination in zip(staged_new, destinations, strict=True):
            os.replace(temporary, destination)
            published.append(destination)
            _fsync_directory(destination.parent)
        staged_new.clear()
    except Exception as publication_error:
        rollback_errors: list[str] = []
        for destination in reversed(published):
            try:
                previous_payload = previous[destination]
                if previous_payload is None:
                    destination.unlink(missing_ok=True)
                else:
                    backup = staged_previous.pop(destination)
                    os.replace(backup, destination)
                _fsync_directory(destination.parent)
            except Exception as rollback_error:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{destination}: {rollback_error}")
        if rollback_errors:
            raise ConformationalMappingRequestError(
                "paired request publication failed and rollback was incomplete: "
                f"{rollback_errors}"
            ) from publication_error
        raise
    finally:
        for temporary in staged_new:
            temporary.unlink(missing_ok=True)
        for temporary in staged_previous.values():
            temporary.unlink(missing_ok=True)


def materialize_trusted_internal_request(
    params: Mapping[str, Any],
    *,
    output_dir: Path | str,
    request_id: str,
    principal_id: str = "biomodstack-api",
    source_kind: str = "api_submission_v1",
) -> MaterializedRequest:
    """Internal/test seam for already-authorized, server-owned request inputs.

    This function performs contract validation and pair-atomic publication; it
    does not authenticate a principal or turn caller host paths/identity strings
    into authority. Public/generic API handlers must never call it.
    """

    try:
        normalized_request_id = str(uuid.UUID(str(request_id)))
    except (ValueError, AttributeError) as exc:
        raise ConformationalMappingRequestError("request_id must be a UUID") from exc

    validated = validate_request_params(params)
    request_without_hash = {
        "schema_name": "cm_request",
        "schema_version": 1,
        "request_id": normalized_request_id,
        **validated.request_fields,
        "source": {
            "kind": _strict_nonempty_string(source_kind, field="source_kind"),
            "sha256": canonical_sha256(validated.request_fields),
        },
        "created_by": {
            "principal_id": _strict_nonempty_string(principal_id, field="principal_id")
        },
    }
    request = {
        **request_without_hash,
        "request_sha256": canonical_sha256(request_without_hash),
    }
    try:
        validate_schema("cm_request_v1", request)
    except ContractValidationError as exc:
        raise ConformationalMappingRequestError(str(exc)) from exc

    root = Path(output_dir)
    request_path = root / "cm_request_v1.json"
    coordinate_plan_path = root / "cm_coordinate_plan_v1.json"
    plan = {
        "schema_name": "cm_coordinate_plan",
        "schema_version": 1,
        "request_id": normalized_request_id,
        "backend": request["backend"],
        "request_sha256": request["request_sha256"],
        "expected_cardinality": len(validated.coordinate_plan),
        "coordinates": list(validated.coordinate_plan),
    }
    plan["coordinate_plan_sha256"] = canonical_sha256(plan)
    _publish_canonical_json_pair(request_path, request, coordinate_plan_path, plan)
    return MaterializedRequest(
        request_path=request_path,
        coordinate_plan_path=coordinate_plan_path,
        launch_params={"cm_request_path": str(request_path)},
    )
