"""Fail-closed server authority for frozen NGS/MolBio capability contracts."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from functools import lru_cache
import hashlib
import importlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_ROOT.parents[1]
_CONFIG_ROOT = _API_ROOT / "config/ngs_molbio"
_REGISTRY_FILES = {
    "schema": "schema_registry_v2.json",
    "adapter": "adapter_registry_v1.json",
    "event": "event_registry_v1.json",
    "dataset": "dataset_kind_registry_v1.json",
    "protein_constraint": "constraint_payload_registry_v1.json",
    "branch_closure": "branch_closure_v1.json",
    "source_pin": "source_pin_v1.json",
    "payload_ownership": "payload_ownership_manifest_v1.json",
}
_REGISTRY_SCHEMA_IDS = {
    "schema": "bms.ngs-molbio.schema-registry.v2",
    "adapter": "bms.ngs-molbio.adapter-registry.v1",
    "event": "bms.ngs-molbio.event-registry.v1",
    "dataset": "bms.ngs-molbio.dataset-kind-registry.v1",
    "protein_constraint": "bms.protein.constraint-payload-registry.v1",
    "branch_closure": "bms.ngs-molbio.branch-closure.v1",
    "source_pin": "bms.ngs-molbio.source-pin.v1",
    "payload_ownership": "bms.payload-ownership-manifest.v1",
}

_PROJECT_SCHEDULED_CAPABILITIES: dict[str, tuple[str, str, str]] = {
    "ngs.ont.basecall_dna": ("nanopore", "basecall_dna", "/api/ont/ngs/basecall-dna/submit"),
    "ngs.ont.basecall_rna": ("nanopore", "basecall_rna", "/api/ont/ngs/basecall-rna/submit"),
    "ngs.ont.plasmid_qc": ("nanopore", "plasmid_qc", "/api/ont/ngs/plasmid-qc/submit"),
    "ngs.ont.construct_screening": (
        "nanopore",
        "construct_screening",
        "/api/ont/ngs/construct-screening/submit",
    ),
    "ngs.ont.methylation_analysis": (
        "nanopore",
        "methylation_analysis",
        "/api/ont/ngs/methylation-analysis/submit",
    ),
    "ngs.ont.assembly_contamination_scan": (
        "nanopore",
        "assembly_contamination_scan",
        "/api/ont/ngs/assembly-contamination-scan/submit",
    ),
    "ngs.ont.microbial_isolate_analysis": (
        "nanopore",
        "microbial_isolate_analysis",
        "/api/ont/ngs/microbial-isolate-analysis/submit",
    ),
    "ngs.ont.raw_signal_qc": ("nanopore", "raw_signal_qc", "/api/ont/ngs/raw-signal-qc/submit"),
    "molbio.oligo_design.rfdpoly": ("oligo_design", "oligo_design", "/api/jobs/submit"),
}
_PROJECT_GOVERNED_DATASET_KINDS = frozenset(
    {
        "ngs_molbio.acquisition_run_input_cohort.v1",
        "ngs_molbio.molecular_construct_cohort.v1",
        "ngs_molbio.qc_analysis_result_cohort.v1",
        "ngs_molbio.reference_comparison_panel_cohort.v1",
        "ngs_molbio.sample_cohort.v1",
        "ngs_molbio.saved_review_comparison_cohort.v1",
    }
)
_PROJECT_SOURCE_RECEIPT_CONTRACTS = [
    "bms.ngs-molbio.sample-revision.adapter.v1",
    "bms.ngs.reference-revision.adapter.v1",
    "bms.ngs.job-reference.adapter.v1",
    "bms.ngs.reference-set.adapter.v1",
    "bms.ngs.ont-run-observation.adapter.v1",
]


_FORMAT_CHECKER = FormatChecker()
_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@_FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def _is_rfc3339_datetime(value: Any) -> bool:
    if not isinstance(value, str) or _RFC3339_DATETIME.fullmatch(value) is None:
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None


class NgsMolBioCapabilityError(RuntimeError):
    """The installed NGS/MolBio capability authority is absent or inconsistent."""


def _semantic_tuple(item: dict[str, Any], fields: Iterable[str]) -> tuple[bytes, ...]:
    return tuple(rfc8785.dumps(item.get(field)) for field in fields)


def _unique_by(
    validator: Any,
    fields: Any,
    instance: Any,
    schema: dict[str, Any],
) -> Iterable[ValidationError]:
    del validator, schema
    if not isinstance(instance, list) or not isinstance(fields, list):
        return
    seen: set[tuple[bytes, ...]] = set()
    for item in instance:
        if not isinstance(item, dict):
            continue
        key = _semantic_tuple(item, fields)
        if key in seen:
            yield ValidationError(f"duplicate semantic identity for fields {fields}")
        seen.add(key)


def _unique_field(
    validator: Any,
    field: Any,
    instance: Any,
    schema: dict[str, Any],
) -> Iterable[ValidationError]:
    if isinstance(field, str):
        yield from _unique_by(validator, [field], instance, schema)


def _unique_ordinal(
    validator: Any,
    enabled: Any,
    instance: Any,
    schema: dict[str, Any],
) -> Iterable[ValidationError]:
    if enabled is True:
        yield from _unique_by(validator, ["ordinal"], instance, schema)


NgsMolBioContractValidator = validators.extend(
    Draft202012Validator,
    {
        "x-bms-unique-by": _unique_by,
        "x-bms-unique-field": _unique_field,
        "x-bms-unique-ordinal": _unique_ordinal,
    },
)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NgsMolBioCapabilityError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


@lru_cache(maxsize=128)
def _read_version(path: Path, mtime_ns: int, size: int) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except NgsMolBioCapabilityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NgsMolBioCapabilityError(f"contract unreadable: {path}") from exc
    if type(value) is not dict:
        raise NgsMolBioCapabilityError(f"contract must be an object: {path}")
    return value, raw



def _read(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        version = path.stat()
    except OSError as exc:
        raise NgsMolBioCapabilityError(f"contract unreadable: {path}") from exc
    document, raw = _read_version(path, version.st_mtime_ns, version.st_size)
    return copy.deepcopy(document), raw


def _canonical_digest(value: dict[str, Any], field: str = "content_sha256") -> str:
    preimage = dict(value)
    preimage.pop(field, None)
    return hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


def _raw_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _path(relative: str) -> Path:
    candidate = (_REPO_ROOT / relative).resolve()
    try:
        candidate.relative_to(_REPO_ROOT.resolve())
    except ValueError as exc:
        raise NgsMolBioCapabilityError(f"contract path escapes repository: {relative}") from exc
    return candidate


def _unique(rows: Iterable[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row[key]
        if value in indexed:
            raise NgsMolBioCapabilityError(f"duplicate {label}: {value}")
        indexed[value] = row
    return indexed


def _validate(
    value: dict[str, Any],
    schema: dict[str, Any],
    label: str,
    registry: Registry | None = None,
) -> None:
    Draft202012Validator.check_schema(schema)
    validator = NgsMolBioContractValidator(
        schema,
        registry=registry or Registry(),
        format_checker=_FORMAT_CHECKER,
    )
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise NgsMolBioCapabilityError(f"{label} invalid at {location}: {errors[0].message}")


class _ScopedSchemas(Mapping[str, dict[str, Any]]):
    """Open only a requested schema and the references actually used by it."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.rows = _unique(entries, "schema_id", "schema ID")
        self.loaded = {}

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, schema_id: str) -> dict[str, Any]:
        if schema_id not in self.loaded:
            entry = self.rows[schema_id]
            schema, raw = _read(_path(entry["path"]))
            if schema.get("$id", schema.get("schema")) != schema_id:
                raise NgsMolBioCapabilityError(f"schema ID mismatch: {schema_id}")
            if _raw_digest(raw) != entry["schema_sha256"]:
                raise NgsMolBioCapabilityError(f"schema byte digest mismatch: {schema_id}")
            canonical = rfc8785.dumps(schema) if "$id" in schema else json.dumps(
                schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")
            if _raw_digest(canonical) != entry["schema_canonical_sha256"]:
                raise NgsMolBioCapabilityError(f"schema canonical digest mismatch: {schema_id}")
            Draft202012Validator.check_schema(schema)
            self.loaded[schema_id] = schema
        return self.loaded[schema_id]


def _schema_closure(entries: list[dict[str, Any]]) -> tuple[_ScopedSchemas, Registry]:
    schemas = _ScopedSchemas(entries)
    def retrieve(uri):
        try:
            schema = schemas[uri]
        except KeyError as exc:
            from referencing.exceptions import NoSuchResource
            raise NoSuchResource(ref=uri) from exc
        return Resource(contents=schema, specification=DRAFT202012)
    return schemas, Registry(retrieve=retrieve)


def _contract_document(name: str) -> dict[str, Any]:
    if name not in _REGISTRY_FILES:
        raise NgsMolBioCapabilityError(f"unknown contract registry: {name}")
    document, _raw = _read(_CONFIG_ROOT / _REGISTRY_FILES[name])
    if document.get("schema") != _REGISTRY_SCHEMA_IDS[name]:
        raise NgsMolBioCapabilityError(f"{name} registry identity mismatch")
    if document.get("content_sha256") != _canonical_digest(document):
        raise NgsMolBioCapabilityError(f"{name} registry content digest mismatch")
    return document


def _schema_context() -> tuple[_ScopedSchemas, Registry]:
    return _schema_closure(_contract_document("schema")["entries"])


def _verify_parameter_partition(record: dict[str, Any]) -> None:
    names = (
        "classified_parameter_keys",
        "server_owned_parameter_keys",
        "unsupported_parameter_keys",
        "unclassified_parameter_keys",
    )
    partitions = {name: set(record[name]) for name in names}
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if partitions[left] & partitions[right]:
                raise NgsMolBioCapabilityError(
                    f"parameter classification overlaps for {record['capability_id']}: {left}/{right}"
                )
    if set(record["observed_parameter_keys"]) != set().union(*partitions.values()):
        raise NgsMolBioCapabilityError(
            f"parameter classification is incomplete for {record['capability_id']}"
        )


def _resolve_owner(owner: str, *, label: str) -> Any:
    module_name, separator, attribute = owner.rpartition(".")
    if not separator:
        raise NgsMolBioCapabilityError(f"invalid {label} owner path: {owner}")
    try:
        implementation = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise NgsMolBioCapabilityError(f"{label} owner is unavailable: {owner}") from exc
    if not callable(implementation):
        raise NgsMolBioCapabilityError(f"{label} owner is not callable: {owner}")
    return implementation


def _verify_capability_owners(record: dict[str, Any]) -> None:
    for field in (
        "preparation_owner",
        "submission_owner",
        "entrypoint_owner",
        "materializer_owner",
    ):
        owner = record[field]
        if owner is not None:
            _resolve_owner(owner, label=f"capability {field}")


def capability_inventory() -> dict[str, Any]:
    """Read the operational catalog, not historical release acceptance evidence."""
    inventory, _raw = _read(_CONFIG_ROOT / "capability_inventory_v2.json")
    if inventory.get("schema") != "bms.ngs-molbio.capability-inventory.v2":
        raise NgsMolBioCapabilityError("capability inventory identity mismatch")
    if inventory.get("content_sha256") != _canonical_digest(inventory):
        raise NgsMolBioCapabilityError("capability inventory digest mismatch")
    _unique(inventory["capabilities"], "capability_id", "capability ID")
    for record in inventory["capabilities"]:
        mapping = _PROJECT_SCHEDULED_CAPABILITIES.get(record["capability_id"])
        if mapping is None:
            continue
        model_id, mode, destination = mapping
        record["exposure_state"] = "accepted"
        record["plannable"] = True
        record["workflow_family"] = "typed_core_job"
        record["workflow_adapter_id"] = "bms.ngs.job-reference.adapter.v1"
        record["allowed_model_modes"] = [{"model_id": model_id, "mode": mode}]
        record["canonical_source_destination"] = destination
        record["source_receipt_contracts"] = list(_PROJECT_SOURCE_RECEIPT_CONTRACTS)
        record["result_contract"] = "bms.global.ngs-molbio-job-result.v1"
        record["native_mapping"]["native_request_compatibility"] = "exact_native_mapping"
        # Engineering acceptance is a release concern, not live admission.
        for gate in record["parity_ledger"]:
            gate["state"] = "not_applicable"
            gate["evidence"] = f"operational-discovery:{destination}; release acceptance is separate"
        record["inventory_sha256"] = _canonical_digest(record, "inventory_sha256")
        record["capability_sha256"] = record["inventory_sha256"]
    return inventory


def capability_record(capability_id: str) -> dict[str, Any]:
    record = next((row for row in capability_inventory()["capabilities"] if row["capability_id"] == capability_id), None)
    if record is None:
        raise NgsMolBioCapabilityError(f"unknown capability: {capability_id}")
    _verify_parameter_partition(record)
    _verify_capability_owners(record)
    # Only the selected operation's parameter contract is required here.
    unsigned = {key: value for key, value in record.items() if key != "capability_sha256"}
    if record["inventory_sha256"] != _canonical_digest(unsigned, "inventory_sha256"):
        raise NgsMolBioCapabilityError(f"capability digest mismatch: {capability_id}")
    schemas, _registry = _schema_context()
    try:
        schemas[record["parameter_schema_id"]]
        if schemas.rows[record["parameter_schema_id"]]["schema_sha256"] != record["parameter_schema_sha256"]:
            raise NgsMolBioCapabilityError(f"capability schema digest mismatch: {capability_id}")
    except KeyError as exc:
        raise NgsMolBioCapabilityError(f"unregistered capability schema: {capability_id}") from exc
    return record


def capability_parameter_schema(capability_id: str) -> dict[str, Any]:
    record = capability_record(capability_id)
    return registered_schema(record["parameter_schema_id"])


def registered_schema(schema_id: str) -> dict[str, Any]:
    schemas, _registry_value = _schema_context()
    try:
        return copy.deepcopy(schemas[schema_id])
    except KeyError as exc:
        raise NgsMolBioCapabilityError(f"unknown schema: {schema_id}") from exc


def contract_registry(name: str) -> dict[str, Any]:
    document = _contract_document(name)
    if name != "schema":
        schemas, registry = _schema_context()
        _validate(document, schemas[_REGISTRY_SCHEMA_IDS[name]], f"{name} registry", registry)
    if name == "dataset":
        for record in document.get("entries", []):
            if record.get("dataset_kind") in _PROJECT_GOVERNED_DATASET_KINDS:
                record["enabled"] = True
    return document


def _payload_value(payload: dict[str, Any], expression: str) -> Any:
    if not expression.startswith("payload."):
        raise NgsMolBioCapabilityError(f"unsupported event derivation: {expression}")
    value: Any = payload
    for field in expression.removeprefix("payload.").split("."):
        if not isinstance(value, dict) or field not in value:
            raise NgsMolBioCapabilityError(f"unresolved event derivation: {expression}")
        value = value[field]
    return value


def _event_derived_value(payload: dict[str, Any], expression: str) -> Any:
    if expression == "null":
        return None
    if expression.startswith("constant:"):
        raw = expression.removeprefix("constant:")
        try:
            return int(raw)
        except ValueError as exc:
            raise NgsMolBioCapabilityError(f"invalid event constant: {expression}") from exc
    return _payload_value(payload, expression)


def _event_stream(payload: dict[str, Any], template: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = _payload_value(payload, match.group(1))
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise NgsMolBioCapabilityError(f"invalid event stream placeholder: {match.group(1)}")
        return str(value)

    stream = re.sub(r"\{(payload\.[A-Za-z0-9_.]+)\}", replace, template)
    if "{" in stream or "}" in stream:
        raise NgsMolBioCapabilityError(f"unresolved event stream template: {template}")
    return stream


def validate_connector_event(value: dict[str, Any]) -> dict[str, Any]:
    schemas, reference_registry = _schema_context()
    _validate(
        value,
        schemas["bms.ngs-molbio.connector-event.v1"],
        "connector event",
        reference_registry,
    )
    event = next(
        (row for row in contract_registry("event")["entries"] if row["event_type"] == value["event_type"]),
        None,
    )
    if event is None:
        raise NgsMolBioCapabilityError(f"unknown connector event type: {value['event_type']}")
    _validate(
        value["payload"],
        schemas[event["payload_schema_id"]],
        "connector event payload",
        reference_registry,
    )
    if value["payload_sha256"] != hashlib.sha256(rfc8785.dumps(value["payload"])).hexdigest():
        raise NgsMolBioCapabilityError("connector event payload digest mismatch")
    expected_stream = _event_stream(value["payload"], event["event_stream_template"])
    if value["event_stream"] != expected_stream:
        raise NgsMolBioCapabilityError("connector event stream mismatch")
    expected_generation = _event_derived_value(value["payload"], event["source_generation_derivation"])
    if value["source_generation"] != expected_generation:
        raise NgsMolBioCapabilityError("connector source generation mismatch")
    expected_state = _event_derived_value(value["payload"], event["state_revision_id_derivation"])
    if value["state_revision_id"] != expected_state:
        raise NgsMolBioCapabilityError("connector state revision mismatch")
    return copy.deepcopy(value)


def _assert_unique_values(values: Iterable[Any], *, label: str) -> None:
    seen: set[bytes] = set()
    for value in values:
        encoded = rfc8785.dumps(value)
        if encoded in seen:
            raise NgsMolBioCapabilityError(f"duplicate {label}")
        seen.add(encoded)


def _verify_ngs_molbio_domain_semantics(value: dict[str, Any]) -> None:
    if value.get("domain_kind") != "ngs_molbio":
        return
    payload = value["domain_payload"]
    if value.get("status") in {"planned", "active"}:
        for field in ("planned_capability_ids", "acceptance_criteria", "evidence_plan"):
            items = payload.get(field)
            if not isinstance(items, list) or not items:
                raise NgsMolBioCapabilityError(
                    f"{value['status']} NGS/MolBio Domains require non-empty {field}"
                )
    _assert_unique_values(
        (row["group_id"] for row in payload["grouping_intent"]),
        label="group ID",
    )
    for group in payload["grouping_intent"]:
        members = group["members"]
        _assert_unique_values(
            (
                [row["member_kind"], row["resource_id"], row["role"]]
                for row in members
            ),
            label=f"member identity in group {group['group_id']}",
        )
        _assert_unique_values(
            (row["ordinal"] for row in members),
            label=f"member ordinal in group {group['group_id']}",
        )
    _assert_unique_values(
        (row["criterion_id"] for row in payload["acceptance_criteria"]),
        label="criterion ID",
    )
    _assert_unique_values(
        (row["requirement_id"] for row in payload["evidence_plan"]),
        label="evidence requirement ID",
    )


def _protein_capability_catalogue() -> dict[str, dict[str, Any]]:
    module = importlib.import_module("services.protein_project_capabilities")
    inventory = module.protein_capability_inventory()
    rows = inventory.get("capabilities") if isinstance(inventory, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise NgsMolBioCapabilityError("Protein capability inventory is malformed")
    catalogue: dict[str, dict[str, Any]] = {}
    for row in rows:
        capability_id = row.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id:
            raise NgsMolBioCapabilityError("Protein capability inventory has an invalid capability ID")
        if capability_id in catalogue:
            raise NgsMolBioCapabilityError(f"duplicate Protein capability ID: {capability_id}")
        catalogue[capability_id] = row
    return catalogue


def _verify_registered_schema_digest(
    item: dict[str, Any],
    *,
    schema_entries: dict[str, dict[str, Any]],
    label: str,
) -> None:
    schema_id = item["schema_id"]
    entry = schema_entries.get(schema_id)
    if entry is None:
        raise NgsMolBioCapabilityError(f"unknown {label} schema ID: {schema_id}")
    if item["schema_sha256"] != entry["schema_sha256"]:
        raise NgsMolBioCapabilityError(f"{label} schema digest mismatch: {schema_id}")


def _verify_protein_domain_semantics(
    value: dict[str, Any],
    schema_entries: dict[str, dict[str, Any]],
) -> None:
    if value.get("domain_kind") != "protein_in_silico":
        return
    payload = value["domain_payload"]
    if payload["design_constraints"]:
        raise NgsMolBioCapabilityError(
            "Protein constraints are unavailable because the closed payload registry is empty"
        )

    targets = payload.get("targets", [])
    _assert_unique_values((target["target_id"] for target in targets), label="protein target ID")
    for target in targets:
        _assert_unique_values(
            (
                [ref["dataset_revision_id"], ref["member_id"]]
                for ref in target.get("dataset_member_refs", [])
            ),
            label=f"Dataset member reference in Protein target {target['target_id']}",
        )

    outer_dataset_ids = value.get("dataset_revision_ids", [])
    target_dataset_ids = [
        ref["dataset_revision_id"]
        for target in targets
        for ref in target.get("dataset_member_refs", [])
    ]
    ordered_target_dataset_ids = list(dict.fromkeys(target_dataset_ids))
    if ordered_target_dataset_ids != outer_dataset_ids:
        raise NgsMolBioCapabilityError(
            "Protein target Dataset member references must match the ordered outer dataset_revision_ids list"
        )

    # Historical Protein v1/v2 payloads remain read-only under their frozen semantics.
    if payload.get("schema") != "bms.protein-in-silico-experiment.v3":
        return

    mode = payload["experiment_mode"]
    planned = payload["planned_capability_ids"]
    validation = payload["validation_capability_ids"]
    _assert_unique_values(planned, label="planned Protein capability ID")
    _assert_unique_values(validation, label="Protein validation capability ID")
    catalogue = _protein_capability_catalogue()

    selected_rows: list[dict[str, Any]] = []
    for capability_id in planned:
        row = catalogue.get(capability_id)
        if row is None:
            raise NgsMolBioCapabilityError(f"unknown planned Protein capability ID: {capability_id}")
        allowed_modes = row.get("allowed_domain_modes")
        if (
            row.get("exposure_state") != "accepted"
            or row.get("plannable") is not True
            or not isinstance(allowed_modes, list)
            or mode not in allowed_modes
        ):
            raise NgsMolBioCapabilityError(
                f"Protein capability is not accepted, plannable, and applicable to {mode}: {capability_id}"
            )
        selected_rows.append(row)

    for capability_id in validation:
        row = catalogue.get(capability_id)
        if row is None:
            raise NgsMolBioCapabilityError(f"unknown Protein validation capability ID: {capability_id}")
        validator_modes = row.get("validator_domain_modes")
        if (
            row.get("exposure_state") != "accepted"
            or row.get("allowed_as_validator") is not True
            or not isinstance(validator_modes, list)
            or mode not in validator_modes
        ):
            raise NgsMolBioCapabilityError(
                f"Protein capability is not explicitly accepted as a validator for {mode}: {capability_id}"
            )
        selected_rows.append(row)

    registered_compatibility_contracts: set[str] = set()
    for row in selected_rows:
        contract_ids = row.get("comparison_compatibility_contract_ids")
        if contract_ids is None:
            continue
        if not isinstance(contract_ids, list) or any(
            not isinstance(contract_id, str) or not contract_id for contract_id in contract_ids
        ):
            raise NgsMolBioCapabilityError(
                f"Protein capability has malformed comparison compatibility contracts: {row['capability_id']}"
            )
        registered_compatibility_contracts.update(contract_ids)

    target_ids = {target["target_id"] for target in targets}
    groups = payload["comparison_groups"]
    _assert_unique_values((group["group_id"] for group in groups), label="Protein comparison group ID")
    for group in groups:
        group_id = group["group_id"]
        contract_id = group["compatibility_contract_id"]
        if contract_id not in registered_compatibility_contracts:
            raise NgsMolBioCapabilityError(
                f"unknown Protein comparison compatibility contract ID: {contract_id}"
            )
        members = group["members"]
        _assert_unique_values(
            ([member["target_id"], member["role"]] for member in members),
            label=f"member identity in Protein comparison group {group_id}",
        )
        _assert_unique_values(
            (member["ordinal"] for member in members),
            label=f"member ordinal in Protein comparison group {group_id}",
        )
        for member in members:
            if member["target_id"] not in target_ids:
                raise NgsMolBioCapabilityError(
                    f"Protein comparison group {group_id} references an unknown target ID: {member['target_id']}"
                )

    criteria = payload["acceptance_criteria"]
    evidence_plan = payload["evidence_plan"]
    _assert_unique_values((item["criterion_id"] for item in criteria), label="Protein criterion ID")
    _assert_unique_values(
        (item["requirement_id"] for item in evidence_plan),
        label="Protein evidence requirement ID",
    )
    for item in criteria:
        _verify_registered_schema_digest(
            item,
            schema_entries=schema_entries,
            label="Protein acceptance criterion",
        )
    for item in evidence_plan:
        _verify_registered_schema_digest(
            item,
            schema_entries=schema_entries,
            label="Protein evidence requirement",
        )

    if value.get("status") in {"planned", "active"}:
        for field in ("planned_capability_ids", "acceptance_criteria", "evidence_plan"):
            if not payload[field]:
                raise NgsMolBioCapabilityError(
                    f"{value['status']} Protein Domains require non-empty {field}"
                )


def validate_domain_experiment(value: dict[str, Any]) -> dict[str, Any]:
    schemas, reference_registry = _schema_context()
    schema_id = value.get("schema")
    if schema_id not in {"bms.domain-experiment.v2", "bms.domain-experiment.v3", "bms.domain-experiment.v4"}:
        raise NgsMolBioCapabilityError("unsupported Domain Experiment schema")
    _validate(
        value,
        schemas[schema_id],
        "domain experiment",
        reference_registry,
    )
    _verify_ngs_molbio_domain_semantics(value)
    schema_entries = _unique(_contract_document("schema")["entries"], "schema_id", "schema ID")
    _verify_protein_domain_semantics(value, schema_entries)
    return copy.deepcopy(value)


def accepted_capability_ids() -> tuple[str, ...]:
    return tuple(
        record["capability_id"]
        for record in capability_inventory()["capabilities"]
        if record["plannable"]
    )


__all__ = [
    "NgsMolBioCapabilityError",
    "accepted_capability_ids",
    "capability_inventory",
    "capability_parameter_schema",
    "capability_record",
    "contract_registry",
    "registered_schema",
    "validate_connector_event",
    "validate_domain_experiment",
]
