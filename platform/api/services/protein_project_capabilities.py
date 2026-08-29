from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


CAPABILITY_ID = "protein.structure_prediction.esmfold2"
ADAPTER_ID = "bms.core-job.esmfold2.adapter.v1"
PARAMETER_SCHEMA_ID = "bms.workflow-parameters.protein.structure_prediction.esmfold2.v1"
SOURCE_PIN = "3a7e99afa19d696baf80ad33d2dcfad80a79d2e0"


class ProteinProjectCapabilityError(ValueError):
    pass


def _field(
    *,
    title: str,
    kind: str,
    default: Any = None,
    has_default: bool = True,
    const: Any = None,
    enum: list[str] | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    control: str = "typed_control",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "title": title,
        "type": kind,
        "x-bms-ui-control": control,
        "x-bms-precision": (
            "boolean" if kind == "boolean"
            else "integer" if kind == "integer"
            else "exact_utf8_or_enum"
        ),
        "x-bms-persisted-representation": "requested_and_effective",
        "x-bms-reproducibility-effect": "changes_output",
    }
    if has_default:
        value["default"] = default
        value["x-bms-default-policy"] = "schema_default"
    else:
        value["x-bms-default-policy"] = "required_explicit_or_authority_bound"
    if const is not None:
        value["const"] = const
    if enum is not None:
        value["enum"] = enum
    if minimum is not None:
        value["minimum"] = minimum
    if maximum is not None:
        value["maximum"] = maximum
    if min_length is not None:
        value["minLength"] = min_length
    if max_length is not None:
        value["maxLength"] = max_length
    if pattern is not None:
        value["pattern"] = pattern
    return value


_PARAMETER_SCHEMA: dict[str, Any] = {
    "$id": PARAMETER_SCHEMA_ID,
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Governed ESMFold2 structure prediction settings",
    "type": "object",
    "additionalProperties": False,
    "x-bms-source-pin": SOURCE_PIN,
    "x-bms-unknown-fields": "reject_before_preparation",
    "properties": {
        "sequence": _field(
            title="Protein sequence",
            kind="string",
            has_default=False,
            min_length=1,
            max_length=10000,
            pattern="^[ACDEFGHIKLMNPQRSTVWY]+$",
        ),
        "sequence_name": _field(
            title="Sequence name",
            kind="string",
            default="Ubiquitin 1UBQ",
            min_length=1,
            max_length=255,
        ),
        "pred_method": _field(
            title="Prediction method",
            kind="string",
            default="esmfold2",
            const="esmfold2",
            control="read_only",
        ),
        "num_parallel_jobs": _field(
            title="Parallel jobs",
            kind="integer",
            default=1,
            const=1,
            minimum=1,
            maximum=1,
            control="read_only",
        ),
        "run_frustrampnn": _field(
            title="Run FrustraMPNN",
            kind="boolean",
            default=False,
            const=False,
            control="read_only",
        ),
        "frustrampnn_requiredness": _field(
            title="FrustraMPNN policy",
            kind="string",
            default="required",
            const="required",
            control="read_only",
        ),
        "model_variant": _field(
            title="ESMFold2 model variant",
            kind="string",
            default="fast",
            enum=["fast", "full"],
            control="select",
        ),
        "local_files_only": _field(
            title="Use installed model files only",
            kind="boolean",
            default=True,
            const=True,
            control="read_only",
        ),
    },
    "required": [
        "sequence",
        "sequence_name",
        "pred_method",
        "num_parallel_jobs",
        "run_frustrampnn",
        "frustrampnn_requiredness",
        "model_variant",
        "local_files_only",
    ],
}

_CAPABILITY: dict[str, Any] = {
    "capability_id": CAPABILITY_ID,
    "capability_version": "1",
    "label": "ESMFold2 structure prediction",
    "scientific_role": "folding_structure_prediction",
    "plannable": True,
    "exposure_state": "accepted",
    "allowed_domain_modes": ["prediction"],
    "workflow_family": "typed_core_job",
    "workflow_adapter_id": ADAPTER_ID,
    "launch_mode": "typed_launcher_handoff",
    "canonical_source_destination": "/submit?template=structure_prediction",
    "parameter_schema_id": PARAMETER_SCHEMA_ID,
    "allowed_model_modes": [{"model_id": "esmfold2", "mode": "predict"}],
    "accepted_source_roles": ["target_structure_receipt"],
    "receipt_contracts": ["bms.global.external-entity-receipt.v1"],
    "result_contracts": ["core_job_result", "typed_core_job_result"],
}


def protein_capability_inventory() -> dict[str, Any]:
    payload = {
        "schema": "bms.protein-project-capability-inventory.v1",
        "capabilities": [_CAPABILITY],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return copy.deepcopy({**payload, "content_sha256": hashlib.sha256(canonical.encode()).hexdigest()})


def protein_capability_record(capability_id: str) -> dict[str, Any]:
    if capability_id != CAPABILITY_ID:
        raise ProteinProjectCapabilityError(f"unknown Protein Project capability: {capability_id}")
    return copy.deepcopy(_CAPABILITY)


def protein_parameter_schema(capability_id: str) -> dict[str, Any]:
    if capability_id != CAPABILITY_ID:
        raise ProteinProjectCapabilityError(f"unknown Protein Project capability: {capability_id}")
    return copy.deepcopy(_PARAMETER_SCHEMA)
