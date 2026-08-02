"""Versioned, fail-closed contracts for the neutral FrustraMPNN component."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

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
    "workflow_component_result_v1": "workflow_component_result_v1.schema.json",
    "frustrampnn_structure_map_v1": "frustrampnn_structure_map_v1.schema.json",
    "frustrampnn_landscape_v1": "frustrampnn_landscape_v1.schema.json",
    "frustrampnn_summary_v1": "frustrampnn_summary_v1.schema.json",
    "frustrampnn_execution_receipt_v1": "frustrampnn_execution_receipt_v1.schema.json",
    "frustrampnn_result_manifest_v1": "frustrampnn_result_manifest_v1.schema.json",
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


def validate_schema(schema_key: str, instance: Any) -> None:
    _schema_validate(schema_key, instance)
    validators = {
        "workflow_component_request_v1": _validate_request,
        "workflow_component_result_v1": _validate_result,
        "frustrampnn_structure_map_v1": _validate_structure_map,
        "frustrampnn_landscape_v1": _validate_landscape,
        "frustrampnn_summary_v1": _validate_summary,
        "frustrampnn_result_manifest_v1": _validate_manifest,
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
