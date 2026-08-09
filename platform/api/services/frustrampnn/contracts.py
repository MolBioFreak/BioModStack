"""Versioned, fail-closed contracts for the neutral FrustraMPNN component."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
SUCCESS_RESULT_ARTIFACT_PATHS = (
    "normalized_input.pdb",
    "frustrampnn_structure_map_v1.json",
    "raw_frustrampnn.csv",
    "frustrampnn_landscape_v1.json",
    "frustrampnn_summary_v1.json",
    "frustrampnn_stdout.log",
    "frustrampnn_stderr.log",
    "frustrampnn_execution_receipt_v1.json",
)
V2_MANIFEST_ARTIFACT_PATHS = (
    "workflow_component_request_v2.json",
    "normalized_input.pdb",
    "frustrampnn_structure_map_v1.json",
    "raw_frustrampnn.csv",
    "frustrampnn_landscape_v2.json",
    "frustrampnn_summary_v2.json",
    "frustrampnn_stdout.log",
    "frustrampnn_stderr.log",
    "frustrampnn_execution_receipt_v2.json",
    "frustrampnn_statistics_v1.json",
)
AUTHORITY_ARTIFACT_PATH = "authority_artifact_v1.json"
EXTERNAL_SUCCESS_RESULT_ARTIFACT_PATHS = (
    AUTHORITY_ARTIFACT_PATH,
    *SUCCESS_RESULT_ARTIFACT_PATHS,
)
CANONICAL_REQUESTED_OUTPUTS = ("structure_map", "raw_csv", "landscape", "summary", "execution_receipt")
FAILURE_CLASSES = frozenset({
    "request_invalid", "source_missing", "source_hash_mismatch", "identity_ambiguous",
    "normalization_failed", "runtime_unavailable", "runtime_digest_mismatch",
    "checkpoint_mismatch", "gpu_admission_failed", "inference_nonzero_exit",
    "inference_timeout", "raw_output_missing", "raw_output_invalid",
    "position_mapping_failed", "wildtype_mismatch", "landscape_incomplete",
    "manifest_invalid", "publication_failed", "ingestion_failed",
})
SCHEMA_FILENAMES = {
    "workflow_component_request_v1": "workflow_component_request_v1.schema.json",
    "workflow_component_request_v2": "workflow_component_request_v2.schema.json",
    "capability_inventory_v1": "capability_inventory_v1.schema.json",
    "frustrampnn_requested_settings_v1": "settings_v1.schema.json",
    "frustrampnn_effective_settings_v1": "effective_settings_v1.schema.json",
    "frustrampnn_execution_configuration_v2": "execution_configuration_v2.schema.json",
    "frustrampnn_settings_v1": "settings_v1.schema.json",
    "frustrampnn_global_configuration_v2": "execution_configuration_v2.schema.json",
    "workflow_component_result_v1": "workflow_component_result_v1.schema.json",
    "workflow_component_result_v2": "workflow_component_result_v2.schema.json",
    "frustrampnn_structure_map_v1": "frustrampnn_structure_map_v1.schema.json",
    "frustrampnn_landscape_v1": "frustrampnn_landscape_v1.schema.json",
    "frustrampnn_landscape_v2": "frustrampnn_landscape_v2.schema.json",
    "frustrampnn_summary_v1": "frustrampnn_summary_v1.schema.json",
    "frustrampnn_summary_v2": "frustrampnn_summary_v2.schema.json",
    "frustrampnn_execution_receipt_v1": "frustrampnn_execution_receipt_v1.schema.json",
    "frustrampnn_execution_receipt_v2": "frustrampnn_execution_receipt_v2.schema.json",
    "frustrampnn_result_manifest_v1": "frustrampnn_result_manifest_v1.schema.json",
    "frustrampnn_result_manifest_v2": "frustrampnn_result_manifest_v2.schema.json",
    "frustrampnn_statistics_v1": "frustrampnn_statistics_v1.schema.json",
    "frustrampnn_comparison_v1": "frustrampnn_comparison_v1.schema.json",
    "frustrampnn_guidance_v1": "frustrampnn_guidance_v1.schema.json",
    "frustrampnn_multistate_comparison_v1": "frustrampnn_multistate_comparison_v1.schema.json",
}
_SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "schemas" / "frustrampnn"


class ContractValidationError(ValueError):
    """A canonical JSON, schema, or cross-record contract was violated."""


def _check_canonical_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError(f"non-finite number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_canonical_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(f"non-string object key at {path}")
            _check_canonical_value(item, f"{path}.{key}")
        return
    raise ContractValidationError(
        f"unsupported canonical JSON value at {path}: {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Render the deterministic UTF-8 JSON profile frozen by CM Phase 0."""

    _check_canonical_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"value is not canonical JSON: {exc}") from exc


def canonical_json_loads(payload: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractValidationError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ContractValidationError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"invalid JSON: {exc}") from exc
    _check_canonical_value(value)
    return value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def request_sha256(request: Mapping[str, Any]) -> str:
    if not isinstance(request, Mapping):
        raise ContractValidationError("request must be an object")
    return canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )


def validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or value.startswith("./")
        or "\\" in value
        or "//" in value
        or value.endswith("/")
        or str(path) != value
    ):
        raise ContractValidationError(f"unsafe relative path: {value!r}")


def load_schema(schema_key: str) -> dict[str, Any]:
    try:
        filename = SCHEMA_FILENAMES[schema_key]
    except KeyError as exc:
        raise ContractValidationError(f"unknown schema key: {schema_key}") from exc
    try:
        schema = canonical_json_loads((_SCHEMA_ROOT / filename).read_bytes())
    except OSError as exc:
        raise ContractValidationError(f"cannot read schema {schema_key}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ContractValidationError(f"schema {schema_key} is not an object")
    Draft202012Validator.check_schema(schema)
    return schema


def _schema_validate(schema_key: str, instance: Any) -> None:
    _check_canonical_value(instance)
    errors = sorted(
        Draft202012Validator(
            load_schema(schema_key), format_checker=FormatChecker()
        ).iter_errors(instance),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
        if list(error.absolute_path)[-1:] == ["relative_path"]:
            raise ContractValidationError(
                f"{schema_key} rejected unsafe relative path at {location}: {error.message}"
            )
        raise ContractValidationError(
            f"{schema_key} rejected {location}: {error.message}"
        )


def _duplicates(values: Sequence[Any]) -> list[Any]:
    return [value for value, count in Counter(values).items() if count != 1]


def _validate_request(instance: Mapping[str, Any]) -> None:
    validate_relative_path(instance["source_artifact"]["relative_path"])
    if tuple(instance["requested_outputs"]) != CANONICAL_REQUESTED_OUTPUTS:
        raise ContractValidationError("requested outputs must be the exact canonical ordered set")
    parameters = instance["parameters"]
    configuration_fields = {"configuration_id", "configuration_sha256"}
    supplied_configuration_fields = configuration_fields & set(parameters)
    if supplied_configuration_fields and supplied_configuration_fields != configuration_fields:
        raise ContractValidationError("configuration identity must include configuration_id and configuration_sha256")
    if supplied_configuration_fields == configuration_fields:
        from .configuration import global_configuration

        expected = global_configuration()
        if (
            parameters["configuration_id"] != expected["configuration_id"]
            or parameters["configuration_sha256"] != expected["configuration_sha256"]
        ):
            raise ContractValidationError("request configuration identity does not match the global configuration")
    if instance["protein_selection"]["mode"] == "explicit":
        entities = instance["protein_selection"]["entities"]
        identities = [entity["entity_instance_id"] for entity in entities]
        if _duplicates(identities):
            raise ContractValidationError("explicit protein selection has duplicate identities")
        if instance["identity_authority"] == "pdb_coordinates" and any(
            entity["source_entity_id"] is not None or entity["label_asym_id"] is not None
            for entity in entities
        ):
            raise ContractValidationError(
                "PDB-coordinate authority cannot invent mmCIF entity/label identity"
            )


def _typed_requested_settings(instance: Mapping[str, Any]):
    from .settings import FrustraMPNNRequestedSettings

    try:
        return FrustraMPNNRequestedSettings.model_validate(instance)
    except ValidationError as exc:
        raise ContractValidationError(f"requested settings are invalid: {exc}") from exc


def _typed_effective_settings(instance: Mapping[str, Any]):
    from .settings import FrustraMPNNEffectiveSettings

    try:
        return FrustraMPNNEffectiveSettings.model_validate(instance)
    except ValidationError as exc:
        raise ContractValidationError(f"effective settings are invalid: {exc}") from exc


def _validate_settings_v1(instance: Mapping[str, Any]) -> None:
    _typed_requested_settings(instance)


def _validate_execution_configuration_v2(instance: Mapping[str, Any]) -> None:
    from .configuration import ConfigurationValidationError, validate_configuration

    try:
        validate_configuration(instance)
    except ConfigurationValidationError as exc:
        raise ContractValidationError(
            f"execution configuration receipt is invalid: {exc}"
        ) from exc


def _validate_request_v2(instance: Mapping[str, Any]) -> None:
    from .configuration import (
        ConfigurationValidationError,
        FrustraMPNNExecutionConfigurationV2,
        validate_configuration,
    )
    from .settings import (
        classification_policy_sha256,
        effective_settings_sha256,
        requested_settings_sha256,
    )

    validate_relative_path(instance["source_artifact"]["relative_path"])
    if tuple(instance["requested_outputs"]) != CANONICAL_REQUESTED_OUTPUTS:
        raise ContractValidationError(
            "requested outputs must be the exact canonical ordered set"
        )

    requested = _typed_requested_settings(instance["requested_settings"])
    effective = _typed_effective_settings(instance["effective_settings"])
    if instance["settings_value_origin"] != requested.settings_value_origin:
        raise ContractValidationError(
            "settings value origin does not match requested settings"
        )
    if instance["settings_value_origin"] != effective.settings_value_origin:
        raise ContractValidationError(
            "settings value origin does not match effective settings"
        )
    if effective.requested_settings != requested:
        raise ContractValidationError(
            "effective settings do not contain the exact requested settings"
        )
    if instance["requested_settings_sha256"] != requested_settings_sha256(requested):
        raise ContractValidationError("requested settings SHA-256 does not match")
    if instance["effective_settings_sha256"] != effective_settings_sha256(effective):
        raise ContractValidationError("effective settings SHA-256 does not match")
    if instance["classification_policy_sha256"] != classification_policy_sha256(
        requested.classification_policy
    ):
        raise ContractValidationError(
            "classification policy SHA-256 does not match requested settings"
        )
    if (
        instance["capability_inventory_byte_sha256"]
        != effective.capability_inventory_byte_sha256
    ):
        raise ContractValidationError(
            "capability inventory byte SHA-256 does not match effective settings"
        )
    resolution = effective.resolution_identity
    if instance["source_artifact"]["sha256"] != resolution.source_artifact_sha256:
        raise ContractValidationError(
            "source artifact SHA-256 does not match effective resolution identity"
        )
    if instance["structure_map_sha256"] != resolution.structure_map_sha256:
        raise ContractValidationError(
            "structure map SHA-256 does not match effective resolution identity"
        )
    if instance["normalized_pdb_sha256"] != resolution.normalized_pdb_sha256:
        raise ContractValidationError(
            "normalized PDB SHA-256 does not match effective resolution identity"
        )

    configuration_payload = instance["execution_configuration"]
    try:
        validate_configuration(configuration_payload)
        configuration = FrustraMPNNExecutionConfigurationV2.model_validate(
            configuration_payload
        )
    except (ConfigurationValidationError, ValidationError) as exc:
        raise ContractValidationError(
            f"execution configuration receipt is invalid: {exc}"
        ) from exc
    if configuration.effective_settings != effective:
        raise ContractValidationError(
            "execution configuration does not contain the exact effective settings"
        )
    if instance["settings_value_origin"] != configuration.settings_value_origin:
        raise ContractValidationError(
            "settings value origin does not match execution configuration"
        )
    if instance["requested_settings_sha256"] != configuration.requested_settings_sha256:
        raise ContractValidationError(
            "requested settings SHA-256 does not match execution configuration"
        )
    if instance["effective_settings_sha256"] != configuration.effective_settings_sha256:
        raise ContractValidationError(
            "effective settings SHA-256 does not match execution configuration"
        )
    if (
        instance["classification_policy_sha256"]
        != configuration.classification_policy_sha256
    ):
        raise ContractValidationError(
            "classification policy SHA-256 does not match execution configuration"
        )
    if (
        instance["capability_inventory_byte_sha256"]
        != configuration.capability_inventory_byte_sha256
    ):
        raise ContractValidationError(
            "capability inventory byte SHA-256 does not match execution configuration"
        )
    if instance["runtime_identity_sha256"] != configuration.runtime_identity_sha256:
        raise ContractValidationError(
            "runtime identity SHA-256 does not match execution configuration"
        )
    if instance["structure_map_sha256"] != configuration.structure_map_sha256:
        raise ContractValidationError(
            "structure map SHA-256 does not match execution configuration"
        )
    if instance["normalized_pdb_sha256"] != configuration.normalized_pdb_sha256:
        raise ContractValidationError(
            "normalized PDB SHA-256 does not match execution configuration"
        )
    if (
        instance["execution_configuration_sha256"]
        != configuration.configuration_sha256
    ):
        raise ContractValidationError(
            "execution configuration SHA-256 does not match receipt"
        )


def _validate_result(instance: Mapping[str, Any]) -> None:
    validate_relative_path(instance["source_artifact"]["relative_path"])
    paths = [record["relative_path"] for record in instance["artifacts"]]
    if _duplicates(paths):
        raise ContractValidationError("result artifact paths must be unique")
    for path in paths:
        validate_relative_path(path)
    status = instance["status"]
    if status == "succeeded":
        if instance["failure_class"] is not None or instance["diagnostic"] is not None:
            raise ContractValidationError("succeeded result cannot contain failure fields")
        if paths not in (
            list(SUCCESS_RESULT_ARTIFACT_PATHS),
            list(EXTERNAL_SUCCESS_RESULT_ARTIFACT_PATHS),
        ):
            raise ContractValidationError(
                "succeeded result artifact inventory is incomplete or out of canonical order"
            )
        expected = {
            AUTHORITY_ARTIFACT_PATH: ("producer_manifest", 1, ("records",)),
            "normalized_input.pdb": (None, None, ("residues",)),
            "frustrampnn_structure_map_v1.json": ("frustrampnn_structure_map", 1, ("residues",)),
            "raw_frustrampnn.csv": (None, None, ("rows",)),
            "frustrampnn_landscape_v1.json": ("frustrampnn_landscape", 1, ("residues",)),
            "frustrampnn_summary_v1.json": ("frustrampnn_summary", 1, ("records",)),
            "frustrampnn_stdout.log": (None, None, None),
            "frustrampnn_stderr.log": (None, None, None),
            "frustrampnn_execution_receipt_v1.json": ("frustrampnn_execution_receipt", 1, ("records",)),
        }
        for record in instance["artifacts"]:
            name, version, kinds = expected[record["relative_path"]]
            expected_role = (
                "identity_authority"
                if record["relative_path"] == AUTHORITY_ARTIFACT_PATH
                else None
            )
            if record.get("role") != expected_role:
                raise ContractValidationError("succeeded artifact semantic role is not exact")
            is_log = record["relative_path"] in {
                "frustrampnn_stdout.log", "frustrampnn_stderr.log",
            }
            if record["bytes"] < 0 or (not is_log and record["bytes"] == 0):
                raise ContractValidationError("succeeded artifact bytes must be positive")
            if record["schema_name"] != name or record["schema_version"] != version:
                raise ContractValidationError("succeeded artifact schema identity is not exact")
            card = record["cardinality"]
            if kinds is None:
                if card is not None:
                    raise ContractValidationError("log artifact cardinality must be null")
            elif not isinstance(card, Mapping) or card.get("kind") not in kinds or not isinstance(card.get("count"), int) or card["count"] <= 0:
                raise ContractValidationError("succeeded artifact cardinality must be positive and exact")
        if instance["result_payload"] != {"schema_name": "frustrampnn_summary", "schema_version": 1}:
            raise ContractValidationError("succeeded result payload must be frustrampnn summary v1")
        gpu = instance["assigned_gpu"]
        if not isinstance(gpu["physical_device_id"], str) or not gpu["physical_device_id"]:
            raise ContractValidationError("succeeded result requires physical GPU identity")
        if isinstance(gpu["task_visible_device_index"], bool) or not isinstance(gpu["task_visible_device_index"], int):
            raise ContractValidationError("succeeded result requires visible GPU device index")
    elif instance["failure_class"] not in FAILURE_CLASSES or not instance["diagnostic"]:
        raise ContractValidationError("failed/not_run result requires exact failure_class enum and diagnostic")


def _validate_structure_map(instance: Mapping[str, Any]) -> None:
    source_hash = instance["source_sha256"]
    if instance["authority_artifact_sha256"] != source_hash and instance[
        "identity_authority"
    ] in {"pdb_self_identity_v1", "mmcif_atom_site_v1"}:
        raise ContractValidationError("self-authoritative source structure hash mismatch")
    if instance["identity_authority"] == "pdb_self_identity_v1":
        if instance["source_format"] != "pdb" or instance["identity_domain"] != "candidate_local":
            raise ContractValidationError("PDB self identity must remain candidate_local")
        if any(
            row["source_entity_id"] is not None
            or row["label_asym_id"] is not None
            or row["label_seq_id"] is not None
            for row in instance["rows"]
        ):
            raise ContractValidationError("PDB self identity invented mmCIF hierarchy")
    if instance["model_ready_sequence_sha256"] != hashlib.sha256(
        instance["model_ready_sequence"].encode("ascii")
    ).hexdigest():
        raise ContractValidationError("model-ready sequence hash mismatch")
    rows = instance["rows"]
    source_keys = [
        (
            row["entity_instance_id"],
            row["auth_asym_id"],
            row["auth_seq_id"],
            row["insertion_code"],
        )
        for row in rows
    ]
    pdb_keys = [
        (row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"])
        for row in rows
    ]
    position_keys = [
        (row["pdb_chain_id"], row["model_position"])
        for row in rows
        if row["status"] == "mapped"
    ]
    if _duplicates(source_keys):
        raise ContractValidationError("duplicate source residue identity")
    if _duplicates(pdb_keys):
        raise ContractValidationError("duplicate normalized PDB residue identity")
    if _duplicates(position_keys):
        raise ContractValidationError("duplicate model position")
    mapped = [row for row in rows if row["status"] == "mapped"]
    if not mapped:
        raise ContractValidationError("structure map has no mapped protein residues")
    if any(row["selected_model"] != instance["selected_source_model"] for row in rows):
        raise ContractValidationError("row model disagrees with selected source model")
    if any(
        not row["backbone_complete"]
        or set(row["backbone_atoms"]) != {"N", "CA", "C", "O"}
        or any(value is None for value in row["backbone_atoms"].values())
        or row["reason"] is not None
        for row in mapped
    ):
        raise ContractValidationError("mapped residue backbone/status semantics are inconsistent")
    for row in rows:
        if row["status"] != "mapped" and (row["backbone_complete"] or row["reason"] is None):
            raise ContractValidationError("non-mapped residue backbone/status semantics are inconsistent")
    label_keys = [(row["entity_instance_id"], row["label_seq_id"]) for row in rows if row["label_seq_id"] is not None]
    if _duplicates(label_keys):
        raise ContractValidationError("duplicate label sequence identity")
    sequence = "".join(row["wt"] for row in mapped)
    if sequence != instance["model_ready_sequence"]:
        raise ContractValidationError("mapped rows disagree with model-ready sequence")


def _validate_landscape(instance: Mapping[str, Any]) -> None:
    if instance["threshold_policy_sha256"] != canonical_sha256(instance["threshold_policy"]):
        raise ContractValidationError("landscape threshold policy hash mismatch")
    residue_keys: set[tuple[Any, ...]] = set()
    position_keys: set[tuple[str, int]] = set()
    for residue in instance["residues"]:
        identity = (
            residue["entity_instance_id"], residue["auth_asym_id"],
            residue["auth_seq_id"], residue["insertion_code"], residue["sequence_index"],
        )
        position = (residue["pdb_chain_id"], residue["model_position"])
        if identity in residue_keys:
            raise ContractValidationError("duplicate landscape residue identity")
        if position in position_keys:
            raise ContractValidationError("duplicate landscape model position")
        residue_keys.add(identity)
        position_keys.add(position)
        slots = residue["slots"]
        if [slot["mutation_aa"] for slot in slots] != list(AA_ORDER):
            raise ContractValidationError("landscape slots are not exact canonical AA order")
        for slot in slots:
            if (
                slot["status"] != "ok"
                or not slot["scoreable"]
                or slot["score"] is None
                or slot["class"] is None
                or slot["reason"] is not None
            ):
                raise ContractValidationError("successful landscape contains an unscoreable slot")
            if not isinstance(slot["score"], (int, float)) or not math.isfinite(slot["score"]):
                raise ContractValidationError("successful landscape score must be finite")
            expected_class = "high" if slot["score"] <= -1.0 else "minimal" if slot["score"] >= 0.58 else "neutral"
            if slot["class"] != expected_class:
                raise ContractValidationError("landscape class disagrees with threshold policy")
            if slot["native"] != (slot["mutation_aa"] == residue["wt"]):
                raise ContractValidationError("native-slot identity is inconsistent")


def _validate_summary(instance: Mapping[str, Any]) -> None:
    if sum(instance["native_slot_counts"].values()) != instance["residue_support"]["scoreable"]:
        raise ContractValidationError("native slot counts do not match scoreable residues")
    if sum(instance["complete_landscape_counts"].values()) != instance["slot_support"]["scoreable"]:
        raise ContractValidationError("landscape class counts do not match scoreable slots")
    if instance["threshold_policy_sha256"] != canonical_sha256(instance["threshold_policy"]):
        raise ContractValidationError("summary threshold policy hash mismatch")
    r, s = instance["residue_support"], instance["slot_support"]
    if r["expected"] != r["mapped"] + r["excluded"] + r["ambiguous"] or r["mapped"] != r["scoreable"]:
        raise ContractValidationError("summary residue support invariant failed")
    if s["expected"] != r["scoreable"] * len(AA_ORDER) or s["observed"] != s["scoreable"] or s["scoreable"] != r["scoreable"] * len(AA_ORDER):
        raise ContractValidationError("summary slot support invariant failed")
    support = instance["support_by_entity_chain"]
    if sum(item["expected_residues"] for item in support) != r["expected"] or sum(item["mapped_residues"] for item in support) != r["mapped"] or sum(item["scoreable_residues"] for item in support) != r["scoreable"] or sum(item["expected_slots"] for item in support) != s["expected"] or sum(item["observed_slots"] for item in support) != s["observed"] or sum(item["scoreable_slots"] for item in support) != s["scoreable"]:
        raise ContractValidationError("summary entity-chain support invariant failed")
    for counts_key, fractions_key, denominator in (("native_slot_counts", "native_slot_fractions", r["scoreable"]), ("complete_landscape_counts", "complete_landscape_fractions", s["scoreable"])):
        for name, count in instance[counts_key].items():
            if not math.isclose(instance[fractions_key][name], count / denominator if denominator else 0.0, rel_tol=0, abs_tol=1e-12):
                raise ContractValidationError("summary fraction invariant failed")


def _validate_manifest(instance: Mapping[str, Any]) -> None:
    paths = [record["relative_path"] for record in instance["artifacts"]]
    if len(paths) != instance["artifact_count"] or _duplicates(paths):
        raise ContractValidationError("manifest artifact cardinality/path uniqueness mismatch")
    for path in paths:
        validate_relative_path(path)


def _validate_typed_selection(selection: Mapping[str, Any], *, label: str) -> None:
    chains = selection["chains"]
    positions = selection["positions"]
    if chains is not None and chains != sorted(chains):
        raise ContractValidationError(f"{label} chains are not in canonical order")
    if positions is not None and positions != sorted(positions):
        raise ContractValidationError(f"{label} positions are not in canonical order")
    if positions is not None and chains is None:
        raise ContractValidationError(f"{label} positions require chains")
    expected_path = f"raw_frustrampnn_shard_{selection['ordinal']:04d}.csv"
    if selection["shard_relative_path"] != expected_path:
        raise ContractValidationError(f"{label} shard path disagrees with ordinal")


def _validate_closed_timing(
    started_at: str,
    ended_at: str,
    duration_seconds: float,
    *,
    label: str,
) -> tuple[datetime, datetime]:
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds < 0
    ):
        raise ContractValidationError(f"{label} duration is invalid")
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContractValidationError(f"{label} timing is invalid") from exc
    if ended < started or not math.isclose(
        (ended - started).total_seconds(),
        duration_seconds,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise ContractValidationError(f"{label} timing/duration is inconsistent")
    return started, ended


def _validate_execution_receipt_v2(instance: Mapping[str, Any]) -> None:
    plan = instance["command_plan"]
    entries = plan["entries"]
    commands = instance["commands"]
    if [entry["ordinal"] for entry in entries] != list(range(len(entries))):
        raise ContractValidationError("command plan ordinals are not contiguous and ordered")
    for entry in entries:
        _validate_typed_selection(entry, label="command-plan selection")
    if plan["plan_sha256"] != canonical_sha256({"entries": entries}):
        raise ContractValidationError("command plan SHA-256 does not match canonical plan")
    if instance["command_count"] != len(entries) or len(commands) != len(entries):
        raise ContractValidationError("command count does not match the command plan")

    modes = {
        "all" if entry["chains"] is None else
        "entities" if entry["positions"] is None else "residues"
        for entry in entries
    }
    if len(modes) != 1 or ("all" in modes and len(entries) != 1) or (
        "entities" in modes and len(entries) != 1
    ):
        raise ContractValidationError("command plan mixes incompatible selection grammars")
    seen_chains: set[str] = set()
    seen_position_groups: set[tuple[int, ...]] = set()
    for entry in entries:
        chains = entry["chains"] or []
        if seen_chains.intersection(chains):
            raise ContractValidationError("command plan repeats a selected chain")
        seen_chains.update(chains)
        if entry["positions"] is not None:
            position_group = tuple(entry["positions"])
            if position_group in seen_position_groups:
                raise ContractValidationError(
                    "command plan leaves identical position tuples split across shards"
                )
            seen_position_groups.add(position_group)

    for entry, command in zip(entries, commands, strict=True):
        _validate_typed_selection(command, label="command selection")
        for field in ("ordinal", "chains", "positions", "shard_relative_path"):
            if command[field] != entry[field]:
                raise ContractValidationError(
                    "command selection does not match its command-plan entry"
                )
        argv = command["argv"]
        if command["argv_sha256"] != canonical_sha256(argv):
            raise ContractValidationError("command argv SHA-256 does not match exact argv")
        chains = command["chains"]
        positions = command["positions"]
        expected_tail: list[str] = []
        if chains is not None:
            expected_tail.extend(["--chains", ",".join(chains)])
        if positions is not None:
            expected_tail.extend(["--positions", ",".join(map(str, positions))])
        if expected_tail and argv[-len(expected_tail):] != expected_tail:
            raise ContractValidationError(
                "command argv typed selection does not match command selection"
            )
        if not expected_tail and any(flag in argv for flag in ("--chains", "--positions")):
            raise ContractValidationError("unrestricted command argv contains selection fragments")
        if argv.count("--chains") != (1 if chains is not None else 0) or argv.count(
            "--positions"
        ) != (1 if positions is not None else 0):
            raise ContractValidationError("command argv selection grammar is not exact")
        if command["status"] == "succeeded":
            if (
                command["exit_code"] != 0
                or command["shard_sha256"] is None
                or not isinstance(command["shard_row_count"], int)
                or command["shard_row_count"] <= 0
                or command["started_at"] is None
                or command["ended_at"] is None
                or command["duration_seconds"] is None
            ):
                raise ContractValidationError(
                    "succeeded command status/exit/shard/timing receipt is incomplete"
                )
            _validate_closed_timing(
                command["started_at"],
                command["ended_at"],
                command["duration_seconds"],
                label="succeeded command",
            )
            if positions is not None and command["shard_row_count"] != (
                len(chains or []) * len(positions) * len(AA_ORDER)
            ):
                raise ContractValidationError(
                    "selected-residue command shard row count is not exact"
                )
        elif command["shard_sha256"] is not None or command["shard_row_count"] is not None:
            raise ContractValidationError("failed/not-run command cannot bind a partial shard")

    receipt_started, receipt_ended = _validate_closed_timing(
        instance["started_at"],
        instance["ended_at"],
        instance["duration_seconds"],
        label="execution receipt",
    )
    for command in commands:
        if command["started_at"] is None or command["ended_at"] is None:
            continue
        command_started, command_ended = _validate_closed_timing(
            command["started_at"],
            command["ended_at"],
            command["duration_seconds"],
            label="command",
        )
        if command_started < receipt_started or command_ended > receipt_ended:
            raise ContractValidationError("command timing falls outside the execution receipt")


def _validate_threshold_policy(instance: Mapping[str, Any], *, label: str) -> None:
    policy = instance["threshold_policy"]
    if instance["threshold_policy_sha256"] != canonical_sha256(policy):
        raise ContractValidationError(f"{label} threshold policy hash mismatch")
    if policy["high_max"] >= policy["minimal_min"]:
        raise ContractValidationError(f"{label} threshold policy boundaries are invalid")
    if policy["mode"] == "canonical" and (
        policy["high_max"] != -1.0 or policy["minimal_min"] != 0.58
    ):
        raise ContractValidationError(
            f"{label} canonical threshold policy boundaries are invalid"
        )


def _validate_landscape_v2(instance: Mapping[str, Any]) -> None:
    _validate_threshold_policy(instance, label="landscape")
    policy = instance["threshold_policy"]
    residue_keys: set[tuple[Any, ...]] = set()
    position_keys: set[tuple[str, int]] = set()
    previous_position: tuple[str, int] | None = None
    for residue in instance["residues"]:
        identity = (
            residue["entity_instance_id"], residue["source_entity_id"],
            residue["label_asym_id"], residue["auth_asym_id"],
            residue["auth_seq_id"], residue["insertion_code"],
            residue["sequence_index"],
        )
        position = (residue["pdb_chain_id"], residue["model_position"])
        if identity in residue_keys or position in position_keys:
            raise ContractValidationError("duplicate v2 landscape residue identity")
        if previous_position is not None and position <= previous_position:
            raise ContractValidationError("v2 landscape residues are not in canonical order")
        previous_position = position
        residue_keys.add(identity)
        position_keys.add(position)
        slots = residue["slots"]
        if [slot["mutation_aa"] for slot in slots] != list(AA_ORDER):
            raise ContractValidationError("v2 landscape slots are not canonical AA order")
        for slot in slots:
            score = slot["score"]
            expected_class = (
                "high" if score <= policy["high_max"] else
                "minimal" if score >= policy["minimal_min"] else "neutral"
            )
            if slot["class"] != expected_class:
                raise ContractValidationError(
                    "v2 landscape class disagrees with threshold policy"
                )
            if slot["native"] != (slot["mutation_aa"] == residue["wt"]):
                raise ContractValidationError("v2 landscape native slot is inconsistent")


def _validate_summary_v2(instance: Mapping[str, Any]) -> None:
    _validate_threshold_policy(instance, label="summary")
    _validate_summary(instance)
    if instance["missingness_by_reason"]:
        raise ContractValidationError(
            "complete selected v2 summary cannot contain missingness"
        )


def _validate_manifest_v2(instance: Mapping[str, Any]) -> None:
    _validate_manifest(instance)
    records = instance["artifacts"]
    paths = [record["relative_path"] for record in records]
    if paths != list(V2_MANIFEST_ARTIFACT_PATHS):
        raise ContractValidationError(
            "v2 manifest artifact paths are not the exact canonical generation"
        )
    expected = {
        "workflow_component_request_v2.json": ("workflow_component_request", 2, None),
        "normalized_input.pdb": (None, None, ("residues",)),
        "frustrampnn_structure_map_v1.json": (
            "frustrampnn_structure_map", 1, ("residues",)
        ),
        "raw_frustrampnn.csv": (None, None, ("rows",)),
        "frustrampnn_landscape_v2.json": (
            "frustrampnn_landscape", 2, ("residues",)
        ),
        "frustrampnn_summary_v2.json": ("frustrampnn_summary", 2, ("records",)),
        "frustrampnn_stdout.log": (None, None, None),
        "frustrampnn_stderr.log": (None, None, None),
        "frustrampnn_execution_receipt_v2.json": (
            "frustrampnn_execution_receipt", 2, ("records",)
        ),
        "frustrampnn_statistics_v1.json": (
            "frustrampnn_statistics", 1, ("records",)
        ),
    }
    for record in records:
        schema_name, schema_version, kinds = expected[record["relative_path"]]
        if record["schema_name"] != schema_name or record["schema_version"] != schema_version:
            raise ContractValidationError("v2 manifest artifact schema identity is not exact")
        is_log = record["relative_path"].endswith(".log")
        if record["bytes"] < 0 or (not is_log and record["bytes"] == 0):
            raise ContractValidationError("v2 manifest artifact byte count is invalid")
        cardinality = record["cardinality"]
        if kinds is None:
            if cardinality is not None:
                raise ContractValidationError("v2 manifest log cardinality must be null")
        elif (
            not isinstance(cardinality, Mapping)
            or cardinality["kind"] not in kinds
            or cardinality["count"] <= 0
        ):
            raise ContractValidationError("v2 manifest artifact cardinality is invalid")


def _validate_result_v2(instance: Mapping[str, Any]) -> None:
    status = instance["status"]
    if status == "succeeded":
        if (
            instance["failure_class"] is not None
            or instance["diagnostic"] is not None
            or instance["result_manifest"] is None
            or instance["result_payload"] is None
        ):
            raise ContractValidationError(
                "succeeded v2 result must bind request/manifest/payload without failure"
            )
    elif (
        instance["failure_class"] not in FAILURE_CLASSES
        or not instance["diagnostic"]
        or instance["result_manifest"] is not None
        or instance["result_payload"] is not None
    ):
        raise ContractValidationError(
            "failed/not-run v2 result requires failure fields and no success manifest"
        )


def validate_schema(schema_key: str, instance: Any) -> None:
    _schema_validate(schema_key, instance)
    validators = {
        "workflow_component_request_v1": _validate_request,
        "workflow_component_request_v2": _validate_request_v2,
        "frustrampnn_requested_settings_v1": _validate_settings_v1,
        "frustrampnn_effective_settings_v1": _typed_effective_settings,
        "frustrampnn_execution_configuration_v2": _validate_execution_configuration_v2,
        "frustrampnn_settings_v1": _validate_settings_v1,
        "frustrampnn_global_configuration_v2": _validate_execution_configuration_v2,
        "workflow_component_result_v1": _validate_result,
        "frustrampnn_structure_map_v1": _validate_structure_map,
        "frustrampnn_landscape_v1": _validate_landscape,
        "frustrampnn_summary_v1": _validate_summary,
        "frustrampnn_result_manifest_v1": _validate_manifest,
        "frustrampnn_execution_receipt_v2": _validate_execution_receipt_v2,
        "frustrampnn_landscape_v2": _validate_landscape_v2,
        "frustrampnn_summary_v2": _validate_summary_v2,
        "frustrampnn_result_manifest_v2": _validate_manifest_v2,
        "workflow_component_result_v2": _validate_result_v2,
    }
    validator = validators.get(schema_key)
    if validator is not None:
        validator(instance)


__all__ = [
    "AA_ORDER", "AUTHORITY_ARTIFACT_PATH", "ContractValidationError",
    "canonical_json_bytes",
    "canonical_json_loads", "canonical_sha256", "load_schema", "request_sha256",
    "validate_relative_path", "validate_schema",
]
