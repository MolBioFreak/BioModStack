"""Sole admission and materialization authority for canonical CM requests."""

from __future__ import annotations

import json
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
    parse_backend_coordinates,
    validate_schema,
    validate_seed_sources,
)
from .clash import CLASH_DETECTOR_ID, CLASH_DETECTOR_VERSION
from .state_landscape_analysis import (
    MAX_STATE_LANDSCAPE_COMPARISON_ROWS,
    MAX_STATE_LANDSCAPE_COMPARISONS,
    MAX_STATE_LANDSCAPE_RESIDUES_PER_CANDIDATE,
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
        "state_landscape_comparison",
        "confornets",
        "protenix_snapshot_id",
        "import_receipt_id",
        "resolved_import_entries",
        "run_record",
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
    request_sha256: str
    coordinate_plan_sha256: str


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


def _bounded_text(
    value: object,
    *,
    field: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ConformationalMappingRequestError(f"{field} must be text")
    text = value.strip()
    if not allow_empty and not text:
        raise ConformationalMappingRequestError(f"{field} must be nonempty")
    if len(text) > maximum:
        raise ConformationalMappingRequestError(f"{field} exceeds {maximum} characters")
    return text


def _normalize_run_record(value: object) -> dict[str, Any]:
    record = _strict_object(
        value,
        field="run_record",
        allowed_fields=frozenset({"name", "notes", "selected_input"}),
    )
    selected = _strict_object(
        record.get("selected_input"),
        field="run_record.selected_input",
        allowed_fields=frozenset({
            "source_id", "source_kind", "source_label", "source_sha256",
            "provider", "accession", "model_id", "sample_id", "chain_ids",
        }),
    )
    normalized_input: dict[str, Any] = {
        "source_id": _bounded_text(
            selected.get("source_id"), field="run_record.selected_input.source_id", maximum=128,
        ),
        "source_kind": _bounded_text(
            selected.get("source_kind"), field="run_record.selected_input.source_kind", maximum=64,
        ),
        "source_label": _bounded_text(
            selected.get("source_label"), field="run_record.selected_input.source_label", maximum=255,
        ),
        "source_sha256": _sha256(
            selected.get("source_sha256"), field="run_record.selected_input.source_sha256",
        ),
    }
    for key in ("provider", "accession", "model_id", "sample_id"):
        if key in selected:
            normalized_input[key] = _bounded_text(
                selected[key], field=f"run_record.selected_input.{key}", maximum=128,
            )
    if "chain_ids" in selected:
        raw_chain_ids = selected["chain_ids"]
        if not isinstance(raw_chain_ids, list) or len(raw_chain_ids) > 128:
            raise ConformationalMappingRequestError(
                "run_record.selected_input.chain_ids must be a bounded array"
            )
        chain_ids = [
            _bounded_text(chain_id, field="run_record.selected_input.chain_ids", maximum=32)
            for chain_id in raw_chain_ids
        ]
        if len(set(chain_ids)) != len(chain_ids):
            raise ConformationalMappingRequestError(
                "run_record.selected_input.chain_ids must be unique"
            )
        normalized_input["chain_ids"] = chain_ids
    return {
        "name": _bounded_text(record.get("name"), field="run_record.name", maximum=255),
        "notes": _bounded_text(
            record.get("notes", ""), field="run_record.notes", maximum=4000, allow_empty=True,
        ),
        "selected_input": normalized_input,
    }


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
    if task == "mse" and confornet_count != 1:
        raise ConformationalMappingRequestError(
            "ConforNets MSE requires exactly one ConforNet"
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
    reference_ids = (
        [reference["reference_id"] for reference in config["references"]]
        if task == "mse"
        else [None]
    )
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


def _validate_state_landscape_comparison_plan(
    request_fields: Mapping[str, Any], coordinate_plan: list[dict[str, Any]]
) -> None:
    """Reject comparison authorities that cannot resolve against planned coordinates."""

    authority = request_fields.get("state_landscape_comparison")
    if not isinstance(authority, Mapping):
        return
    mode = authority.get("mode")
    target_id = authority.get("target_id")
    if not isinstance(mode, str) or not isinstance(target_id, str):
        return
    try:
        planned = [
            parse_backend_coordinates(coordinate).model_dump(mode="json")
            for coordinate in coordinate_plan
        ]
    except Exception as exc:  # pragma: no cover - plan construction is local authority
        raise ConformationalMappingRequestError("canonical coordinate plan is invalid") from exc
    selected = [coordinate for coordinate in planned if coordinate["target_id"] == target_id]

    def validate_comparison_work(comparison_count: int) -> None:
        if comparison_count > MAX_STATE_LANDSCAPE_COMPARISONS:
            raise ConformationalMappingRequestError(
                "state landscape comparison resolves "
                f"{comparison_count} comparisons, exceeding configured maximum "
                f"{MAX_STATE_LANDSCAPE_COMPARISONS}"
            )
        estimated_rows = comparison_count * MAX_STATE_LANDSCAPE_RESIDUES_PER_CANDIDATE
        if estimated_rows > MAX_STATE_LANDSCAPE_COMPARISON_ROWS:
            raise ConformationalMappingRequestError(
                "state landscape comparison reserves "
                f"{estimated_rows} comparison rows at the "
                f"{MAX_STATE_LANDSCAPE_RESIDUES_PER_CANDIDATE}-residue candidate envelope, "
                f"exceeding configured maximum {MAX_STATE_LANDSCAPE_COMPARISON_ROWS}"
            )

    if mode == "pairwise":
        if len(selected) < 2:
            raise ConformationalMappingRequestError(
                "pairwise state landscape comparison requires at least two planned coordinates"
            )
        comparison_count = len(selected) * (len(selected) - 1) // 2
        validate_comparison_work(comparison_count)
        return
    if mode != "reference":
        return
    selector = authority.get("reference_backend_coordinates")
    if not isinstance(selector, Mapping):
        return
    try:
        reference = parse_backend_coordinates(selector).model_dump(mode="json")
    except Exception:
        return
    if reference["target_id"] != target_id:
        raise ConformationalMappingRequestError(
            "reference state landscape comparison target does not match the selected target"
        )
    matching = [coordinate for coordinate in selected if coordinate == reference]
    if len(matching) != 1:
        raise ConformationalMappingRequestError(
            "reference state landscape comparison does not match exactly one planned coordinate"
        )
    if not any(coordinate != reference for coordinate in selected):
        raise ConformationalMappingRequestError(
            "reference state landscape comparison requires another planned coordinate"
        )
    comparison_count = len(selected) - 1
    validate_comparison_work(comparison_count)


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
    if "run_record" in values:
        request_fields["run_record"] = _normalize_run_record(values["run_record"])
    if "state_landscape_comparison" in values:
        request_fields["state_landscape_comparison"] = values["state_landscape_comparison"]
    analysis_policy = values["analysis_policy"]
    if (
        not isinstance(analysis_policy, Mapping)
        or analysis_policy.get("clash_detector_id") != CLASH_DETECTOR_ID
        or analysis_policy.get("clash_detector_version") != CLASH_DETECTOR_VERSION
    ):
        raise ConformationalMappingRequestError("requested clash detector is not installed")

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
        if settings["task"] == "mse" and len(settings["saved_steps"]) != 1:
            raise ConformationalMappingRequestError(
                "ConforNets MSE requires exactly one saved step"
            )
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
        feature_policy = values["feature_policy"]
        if not isinstance(feature_policy, Mapping) or any(
            key not in feature_policy
            for key in ("protein_msa_enabled", "templates_enabled", "rna_msa_enabled")
        ):
            raise ConformationalMappingRequestError(
                "Protenix requires explicit protein-MSA, template, and RNA-MSA controls"
            )
        if any(not isinstance(feature_policy[key], bool) for key in (
            "protein_msa_enabled", "templates_enabled", "rna_msa_enabled"
        )):
            raise ConformationalMappingRequestError("Protenix feature controls must be booleans")
        if feature_policy.get("mode") == "features_disabled_control_v1" and any(
            feature_policy[key] for key in ("protein_msa_enabled", "templates_enabled", "rna_msa_enabled")
        ):
            raise ConformationalMappingRequestError("feature-disabled control cannot enable features")
        request_fields["protenix_snapshot_id"] = _strict_nonempty_string(
            values["protenix_snapshot_id"], field="protenix_snapshot_id"
        )
        if not targets:
            raise ConformationalMappingRequestError("Protenix requires at least one target")
        for target in targets:
            if not isinstance(target, Mapping) or not isinstance(target.get("target_id"), str):
                raise ConformationalMappingRequestError("Protenix target identity is invalid")
            for seed in ordered_seeds:
                for sample_index in range(samples_per_seed):
                    coordinate_plan.append(
                        {
                            "backend": "protenix_v2_ensemble",
                            "target_id": target["target_id"],
                            "ordered_seed": seed,
                            "sample_index": sample_index,
                        }
                    )
    elif backend == "protenix_v2_ensemble":
        raise ConformationalMappingRequestError("a registered complete-complex snapshot is required")
    if backend == "external_import" and values.get("import_receipt_id") not in (None, ""):
        request_fields["import_receipt_id"] = _strict_nonempty_string(
            values["import_receipt_id"], field="import_receipt_id"
        )
        entries = values.get("resolved_import_entries")
        if not isinstance(entries, list) or len(entries) != len(targets) or not entries:
            raise ConformationalMappingRequestError("import receipt entries must match ordered targets")
        receipt_sha256 = _sha256(
            values["import_receipt_id"], field="import_receipt_id"
        )
        for target, entry in zip(targets, entries, strict=True):
            if not isinstance(target, Mapping) or not isinstance(entry, Mapping):
                raise ConformationalMappingRequestError("import coordinate authority is invalid")
            coordinate_plan.append(
                {
                    "backend": "external_import",
                    "target_id": _strict_nonempty_string(target.get("target_id"), field="target_id"),
                    "staged_index": _strict_nonnegative_int(entry.get("staged_index"), field="staged_index"),
                    "source_content_sha256": _sha256(entry.get("source_content_sha256"), field="source_content_sha256"),
                    "staged_receipt_sha256": receipt_sha256,
                }
            )
    elif backend == "external_import":
        raise ConformationalMappingRequestError("an immutable registered import receipt is required")

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
    _validate_state_landscape_comparison_plan(request_fields, coordinate_plan)
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
        request_sha256=request["request_sha256"],
        coordinate_plan_sha256=plan["coordinate_plan_sha256"],
    )


def bind_materialized_source_snapshot(
    materialized: MaterializedRequest,
    *,
    source_snapshot_sha256: str,
    selected_input: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically bind a server-generated snapshot into request and plan hashes."""

    if len(source_snapshot_sha256) != 64 or any(value not in "0123456789abcdef" for value in source_snapshot_sha256):
        raise ConformationalMappingRequestError("source snapshot identity must be a lowercase SHA-256")
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
    try:
        validate_schema("cm_request_v1", request)
    except ContractValidationError as exc:
        raise ConformationalMappingRequestError(str(exc)) from exc
    expected_request_sha256 = canonical_sha256({
        key: value for key, value in request.items() if key != "request_sha256"
    })
    if request.get("request_sha256") != expected_request_sha256:
        raise ConformationalMappingRequestError("materialized request self-hash is invalid")
    if request.get("request_sha256") != materialized.request_sha256:
        raise ConformationalMappingRequestError("materialized request no longer matches trusted authority")
    allowed_plan_fields = {
        "schema_name", "schema_version", "request_id", "backend", "request_sha256",
        "expected_cardinality", "coordinates", "coordinate_plan_sha256",
    }
    if set(plan) != allowed_plan_fields:
        raise ConformationalMappingRequestError("materialized coordinate plan has unexpected fields")
    expected_plan_sha256 = canonical_sha256({
        key: value for key, value in plan.items() if key != "coordinate_plan_sha256"
    })
    if plan.get("coordinate_plan_sha256") != expected_plan_sha256:
        raise ConformationalMappingRequestError("materialized coordinate-plan self-hash is invalid")
    if plan.get("coordinate_plan_sha256") != materialized.coordinate_plan_sha256:
        raise ConformationalMappingRequestError("materialized coordinate plan no longer matches trusted authority")
    if (
        plan.get("schema_name") != "cm_coordinate_plan"
        or plan.get("schema_version") != 1
        or plan.get("request_id") != request.get("request_id")
        or plan.get("request_sha256") != request.get("request_sha256")
    ):
        raise ConformationalMappingRequestError("materialized request and coordinate plan are not bound")
    targets = request.get("targets")
    coordinates = plan.get("coordinates")
    if not isinstance(targets, list) or not isinstance(coordinates, list):
        raise ConformationalMappingRequestError("materialized coordinate plan does not match request authority")
    if plan.get("expected_cardinality") != len(coordinates) or len(coordinates) != len(targets):
        raise ConformationalMappingRequestError("materialized coordinate plan does not match request authority")
    if request.get("backend") == "external_import" and any(
        not isinstance(coordinate, Mapping)
        or set(coordinate) != {
            "backend", "target_id", "staged_index", "source_content_sha256",
            "staged_receipt_sha256",
        }
        or coordinate.get("backend") != "external_import"
        or coordinate.get("target_id") != target.get("target_id")
        or coordinate.get("staged_index") != index
        or coordinate.get("staged_receipt_sha256") != request.get("import_receipt_id")
        for index, (target, coordinate) in enumerate(zip(targets, coordinates, strict=True))
    ):
        raise ConformationalMappingRequestError(
            "materialized coordinate plan does not match request authority"
        )
    if request.get("backend") != "external_import" or plan.get("backend") != "external_import":
        raise ConformationalMappingRequestError("source snapshot binding is external-import only")
    if "source_snapshot_sha256" in request:
        raise ConformationalMappingRequestError("source snapshot identity is already bound")
    if selected_input is not None:
        run_record = request.get("run_record")
        if not isinstance(run_record, Mapping):
            raise ConformationalMappingRequestError("external import requires an authoritative run record")
        request["run_record"] = {**dict(run_record), "selected_input": dict(selected_input)}
    request["source_snapshot_sha256"] = source_snapshot_sha256
    request["request_sha256"] = canonical_sha256({
        key: value for key, value in request.items() if key != "request_sha256"
    })
    validate_schema("cm_request_v1", request)
    plan["request_sha256"] = request["request_sha256"]
    plan.pop("coordinate_plan_sha256", None)
    plan["coordinate_plan_sha256"] = canonical_sha256(plan)
    _publish_canonical_json_pair(
        materialized.request_path, request,
        materialized.coordinate_plan_path, plan,
    )
    return request, plan
