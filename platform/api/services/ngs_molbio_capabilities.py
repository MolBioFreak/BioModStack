"""Fail-closed server authority for frozen NGS/MolBio capability contracts."""
from __future__ import annotations

import copy
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
_SCHEMA_ROOT = _REPO_ROOT / "schemas/ngs_molbio"
_VERIFICATION_RECEIPT = _REPO_ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"
_RUNTIME_RECORD = _API_ROOT / "config/ngs_molbio_runtime/runtime_implementation_v2.json"
_VERIFICATION_SCHEMA_ID = "bms.ngs-molbio.phase-n0-verification-receipt.v1"
_SHA256 = frozenset("0123456789abcdef")
_GATES = frozenset(
    {
        "installed_inventory",
        "global_schema",
        "browser_controls",
        "agent_parity",
        "persistence",
        "execution",
        "receipt",
        "global_result_experience",
        "workflow_reuse",
        "live_agreement",
    }
)
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


def _read(path: Path) -> tuple[dict[str, Any], bytes]:
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


def _registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    registry = Registry()
    try:
        for schema_id, schema in schemas.items():
            resource = (
                Resource.from_contents(schema)
                if "$schema" in schema
                else Resource(contents=schema, specification=DRAFT202012)
            )
            registry = registry.with_resource(schema_id, resource)
    except Exception as exc:
        raise NgsMolBioCapabilityError("schema registry cannot resolve installed resources") from exc
    return registry


def _schema_closure(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], Registry]:
    schemas: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = _path(entry["path"])
        schema, raw = _read(path)
        schema_id = entry["schema_id"]
        if schema.get("$id", schema.get("schema")) != schema_id:
            raise NgsMolBioCapabilityError(f"schema ID mismatch: {schema_id}")
        if _raw_digest(raw) != entry["schema_sha256"]:
            raise NgsMolBioCapabilityError(f"schema byte digest mismatch: {schema_id}")
        canonical_raw = (
            rfc8785.dumps(schema)
            if "$id" in schema
            else json.dumps(
                schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")
        )
        if hashlib.sha256(canonical_raw).hexdigest() != entry["schema_canonical_sha256"]:
            raise NgsMolBioCapabilityError(f"schema canonical digest mismatch: {schema_id}")
        Draft202012Validator.check_schema(schema)
        if schema_id in schemas:
            raise NgsMolBioCapabilityError(f"duplicate schema ID: {schema_id}")
        schemas[schema_id] = schema
    registry = _registry(schemas)
    for schema_id, schema in schemas.items():
        reference = "<schema>"
        try:
            Draft202012Validator(schema, registry=registry).evolve(schema=schema)
            for reference in _references(schema):
                registry.resolver().lookup(reference)
        except Exception as exc:
            raise NgsMolBioCapabilityError(
                f"unresolved schema reference in {schema_id}: {reference}"
            ) from exc
    return schemas, registry


def _references(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            found.append(reference)
        for item in value.values():
            found.extend(_references(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_references(item))
    return tuple(found)


def _verify_source_pin(document: dict[str, Any]) -> None:
    observed: set[str] = set()
    runtime_authorities: dict[str, dict[str, Any]] | None = None
    for row in document["authorities"]:
        relative = row["path"]
        if relative in observed:
            raise NgsMolBioCapabilityError(f"duplicate source authority path: {relative}")
        observed.add(relative)
        installed_path = _path(relative)
        try:
            installed_raw = installed_path.read_bytes()
        except OSError as exc:
            raise NgsMolBioCapabilityError(f"installed source authority is unavailable: {relative}") from exc
        installed_sha256 = _raw_digest(installed_raw)
        if installed_sha256 != row["sha256"]:
            if runtime_authorities is None:
                runtime_authorities = _runtime_overlay_authorities()
            runtime_row = runtime_authorities.get(relative)
            if (
                runtime_row is None
                or runtime_row["size_bytes"] != len(installed_raw)
                or runtime_row["sha256"] != installed_sha256
            ):
                raise NgsMolBioCapabilityError(f"installed source authority digest mismatch: {relative}")


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


def _verify_adapter_identity_contract(row: dict[str, Any]) -> None:
    identity = row["identity_contract"]
    reopen_fields = set(row["reopen_contract"]["parameter_fields"])
    aliases = {"reference_revision_id": "revision_id"}
    missing = {
        field
        for field in identity["key_fields"]
        if field not in reopen_fields and aliases.get(field) not in reopen_fields
    }
    if missing:
        raise NgsMolBioCapabilityError(
            f"adapter reopen contract omits native identity {sorted(missing)}: {row['adapter_id']}"
        )
    has_revision_authority = bool(
        identity["revision_fields"]
        or identity["generation_fields"]
        or identity["fixed_revision_marker"]
    )
    if row["contract_class"] == "local_member_adapter" and not has_revision_authority:
        raise NgsMolBioCapabilityError(f"adapter omits revision or generation authority: {row['adapter_id']}")
    is_current_head_compatibility = row["adapter_id"] == "bms.ngs.ont-run-reference.adapter.v1"
    if is_current_head_compatibility:
        if row["allowed_dataset_roles"]:
            raise NgsMolBioCapabilityError("current-head ONT run adapter cannot be a Dataset member")
        if row["reopen_contract"]["head_resolution_forbidden"] is not False:
            raise NgsMolBioCapabilityError("current-head ONT run adapter must declare current-head reopening")
    elif row["reopen_contract"]["head_resolution_forbidden"] is not True:
        raise NgsMolBioCapabilityError(f"adapter reopen contract permits current-head substitution: {row['adapter_id']}")


def _verify_adapter_owner(row: dict[str, Any]) -> None:
    owner = row["implementation_owner"]
    state = row["baseline_state"]
    if owner is None:
        if state == "present":
            raise NgsMolBioCapabilityError(f"present adapter has no owner: {row['adapter_id']}")
        return
    if state == "missing":
        raise NgsMolBioCapabilityError(f"missing adapter claims an owner: {row['adapter_id']}")
    implementation = _resolve_owner(owner, label="adapter")
    expected = {
        "adapter_id": row["adapter_id"],
        "adapter_version": row["adapter_version"],
        "entity_kind": row["entity_kind"],
    }
    for field, value in expected.items():
        if getattr(implementation, field, None) != value:
            raise NgsMolBioCapabilityError(f"adapter owner {field} mismatch: {row['adapter_id']}")
    if state == "present":
        try:
            from services.global_experiments.adapters import registry as active_registry

            registered = active_registry.get(row["adapter_id"])
        except Exception as exc:
            raise NgsMolBioCapabilityError(
                f"present adapter is absent from active registry: {row['adapter_id']}"
            ) from exc
        if registered.__class__ is not implementation:
            raise NgsMolBioCapabilityError(f"active adapter class mismatch: {row['adapter_id']}")


def _runtime_overlay_authorities(receipt: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    if not _RUNTIME_RECORD.is_file():
        return {}
    record, _raw = _read(_RUNTIME_RECORD)
    if record.get("schema") != "bms.ngs-molbio.runtime-implementation.v1":
        raise NgsMolBioCapabilityError("runtime overlay identity mismatch")
    if record.get("content_sha256") != _canonical_digest(record):
        raise NgsMolBioCapabilityError("runtime overlay content digest mismatch")
    if record.get("capability_exposure_state") != "fail_closed" or record.get("dataset_exposure_state") != "fail_closed":
        raise NgsMolBioCapabilityError("unaccepted runtime overlay must remain fail closed")
    if receipt is not None:
        if record.get("n0_receipt_content_sha256") != receipt.get("content_sha256"):
            raise NgsMolBioCapabilityError("runtime overlay N0 receipt binding mismatch")
        if record.get("n0_package_fingerprint") != receipt.get("payload_fingerprint_sha256"):
            raise NgsMolBioCapabilityError("runtime overlay N0 package binding mismatch")
    authorities = _unique(record.get("source_authorities", []), "path", "runtime source path")
    for relative, row in authorities.items():
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise NgsMolBioCapabilityError("runtime source path is invalid")
        if not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64 or set(row["sha256"]) - _SHA256:
            raise NgsMolBioCapabilityError(f"runtime source digest is invalid: {relative}")
        if not isinstance(row.get("size_bytes"), int) or row["size_bytes"] < 1:
            raise NgsMolBioCapabilityError(f"runtime source size is invalid: {relative}")
    return authorities


def _verify_phase_n0_receipt(
    schemas: dict[str, dict[str, Any]],
    reference_registry: Registry,
    source_pin: dict[str, Any],
) -> None:
    receipt, _raw = _read(_VERIFICATION_RECEIPT)
    schema = schemas.get(_VERIFICATION_SCHEMA_ID)
    if schema is None:
        raise NgsMolBioCapabilityError("Phase N0 verification receipt schema is absent")
    _validate(receipt, schema, "Phase N0 verification receipt", reference_registry)
    if receipt["content_sha256"] != _canonical_digest(receipt):
        raise NgsMolBioCapabilityError("Phase N0 verification receipt digest mismatch")
    if receipt["baseline_commit"] != source_pin["baseline_commit"] or receipt["baseline_tree"] != source_pin["baseline_tree"]:
        raise NgsMolBioCapabilityError("Phase N0 verification receipt baseline mismatch")
    receipt_relative = str(_VERIFICATION_RECEIPT.relative_to(_REPO_ROOT))
    expected_paths = {
        "docs/reports/ngs-molbio-phase-n0-contract-freeze.md",
        "platform/api/services/ngs_molbio_capabilities.py",
        "scripts/verify_ngs_molbio_phase_n0.py",
        *(str(path.relative_to(_REPO_ROOT)) for path in _CONFIG_ROOT.glob("*.json")),
        *(str(path.relative_to(_REPO_ROOT)) for path in _SCHEMA_ROOT.glob("*.json")),
    }
    expected_paths.discard(receipt_relative)
    expected_paths.difference_update(
        {
            "platform/api/config/ngs_molbio/capability_inventory_v2.json",
            "platform/api/config/ngs_molbio/schema_registry_v2.json",
            "schemas/ngs_molbio/capability-inventory-v2.schema.json",
            "schemas/ngs_molbio/schema-registry-v2.schema.json",
        }
    )
    rows = _unique(receipt["payload_files"], "path", "verification payload path")
    if set(rows) != expected_paths:
        raise NgsMolBioCapabilityError("Phase N0 verification payload manifest is incomplete")
    runtime_authorities: dict[str, dict[str, Any]] | None = None
    fingerprint = hashlib.sha256()
    for relative in sorted(rows):
        path = _path(relative)
        raw = path.read_bytes()
        row = rows[relative]
        current_sha256 = _raw_digest(raw)
        if row["size_bytes"] != len(raw) or row["sha256"] != current_sha256:
            if runtime_authorities is None:
                runtime_authorities = _runtime_overlay_authorities(receipt)
            runtime_row = runtime_authorities.get(relative)
            if (
                runtime_row is None
                or runtime_row["size_bytes"] != len(raw)
                or runtime_row["sha256"] != current_sha256
            ):
                raise NgsMolBioCapabilityError(f"Phase N0 verification payload drift: {relative}")
        fingerprint.update(f"{relative}\0{row['sha256']}\n".encode("utf-8"))
    if fingerprint.hexdigest() != receipt["payload_fingerprint_sha256"]:
        raise NgsMolBioCapabilityError("Phase N0 verification fingerprint mismatch")


def _loaded_documents() -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    Registry,
    dict[str, dict[str, Any]],
]:
    documents: dict[str, dict[str, Any]] = {}
    raw_documents: dict[str, bytes] = {}
    for name, filename in _REGISTRY_FILES.items():
        document, raw = _read(_CONFIG_ROOT / filename)
        if document.get("content_sha256") != _canonical_digest(document):
            raise NgsMolBioCapabilityError(f"registry content digest mismatch: {filename}")
        documents[name] = document
        raw_documents[name] = raw

    schema_registry = documents["schema"]
    if schema_registry.get("schema") != _REGISTRY_SCHEMA_IDS["schema"]:
        raise NgsMolBioCapabilityError("schema registry identity mismatch")
    schemas, reference_registry = _schema_closure(schema_registry["entries"])

    for name in _REGISTRY_FILES:
        schema_id = _REGISTRY_SCHEMA_IDS[name]
        schema = schemas.get(schema_id)
        if schema is None:
            raise NgsMolBioCapabilityError(f"registry schema is absent: {schema_id}")
        _validate(documents[name], schema, f"{name} registry", reference_registry)

    for name in ("adapter", "event", "dataset", "protein_constraint", "branch_closure"):
        if documents[name]["baseline_source_commit"] != documents["source_pin"]["baseline_commit"]:
            raise NgsMolBioCapabilityError(f"{name} registry baseline commit disagrees with source pin")
    _verify_source_pin(documents["source_pin"])

    inventory, inventory_raw = _read(_CONFIG_ROOT / "capability_inventory_v2.json")
    inventory_schema = schemas.get("bms.ngs-molbio.capability-inventory.v2")
    if inventory_schema is None:
        raise NgsMolBioCapabilityError("capability inventory schema is absent")
    _validate(inventory, inventory_schema, "capability inventory", reference_registry)
    if inventory["content_sha256"] != _canonical_digest(inventory):
        raise NgsMolBioCapabilityError("capability inventory digest mismatch")
    if inventory["baseline_source_commit"] != schema_registry["baseline_source_commit"]:
        raise NgsMolBioCapabilityError("capability baseline commit disagrees with schema registry")
    if inventory["baseline_source_tree"] != schema_registry["baseline_source_tree"]:
        raise NgsMolBioCapabilityError("capability baseline tree disagrees with schema registry")
    byte_bindings = {
        "source_pin_sha256": "source_pin",
        "schema_registry_sha256": "schema",
        "adapter_registry_sha256": "adapter",
        "event_registry_sha256": "event",
        "dataset_registry_sha256": "dataset",
        "constraint_payload_registry_sha256": "protein_constraint",
        "branch_closure_sha256": "branch_closure",
        "payload_ownership_manifest_sha256": "payload_ownership",
    }
    for field, name in byte_bindings.items():
        if inventory[field] != _raw_digest(raw_documents[name]):
            raise NgsMolBioCapabilityError(f"capability inventory binds different {name} bytes")

    schema_rows = _unique(schema_registry["entries"], "schema_id", "schema ID")
    capabilities = _unique(inventory["capabilities"], "capability_id", "capability ID")
    if len(capabilities) != 22:
        raise NgsMolBioCapabilityError("capability denominator must contain exactly 22 IDs")
    for capability_id, record in capabilities.items():
        if record["inventory_sha256"] != _canonical_digest(record, "inventory_sha256"):
            raise NgsMolBioCapabilityError(f"capability digest mismatch: {capability_id}")
        schema_row = schema_rows.get(record["parameter_schema_id"])
        if schema_row is None:
            raise NgsMolBioCapabilityError(f"unregistered capability schema: {capability_id}")
        if schema_row["schema_sha256"] != record["parameter_schema_sha256"]:
            raise NgsMolBioCapabilityError(f"capability schema digest mismatch: {capability_id}")
        if {row["gate"] for row in record["parity_ledger"]} != _GATES:
            raise NgsMolBioCapabilityError(f"incomplete parity ledger: {capability_id}")
        _verify_parameter_partition(record)
        _verify_capability_owners(record)
        passed = all(row["state"] in {"pass", "not_applicable"} for row in record["parity_ledger"])
        if record["unsupported_parameter_keys"] or record["unclassified_parameter_keys"]:
            passed = False
        if record["plannable"] != (record["exposure_state"] == "accepted" and passed):
            raise NgsMolBioCapabilityError(f"unsafe exposure state: {capability_id}")

    adapters = _unique(documents["adapter"]["entries"], "adapter_id", "adapter ID")
    if len(adapters) != 27:
        raise NgsMolBioCapabilityError("adapter denominator must contain exactly 27 IDs")
    binding_adapter = documents["adapter"]["binding_adapter"]
    if binding_adapter["adapter_id"] != inventory["contract_ids"]["binding_adapter"]:
        raise NgsMolBioCapabilityError("binding adapter ID disagrees with capability inventory")
    binding_schema = schemas.get(binding_adapter["binding_receipt_schema_id"])
    if binding_schema is None:
        raise NgsMolBioCapabilityError("binding receipt schema is absent")
    if binding_schema["properties"]["adapter_id"]["const"] != binding_adapter["adapter_id"]:
        raise NgsMolBioCapabilityError("binding receipt adapter ID mismatch")
    if binding_schema["properties"]["adapter_version"]["const"] != binding_adapter["adapter_version"]:
        raise NgsMolBioCapabilityError("binding receipt adapter version mismatch")
    for adapter in adapters.values():
        _verify_adapter_identity_contract(adapter)
        _verify_adapter_owner(adapter)
    protein_dataset_rows = [
        row for row in documents["dataset"]["entries"] if row["dataset_kind"].startswith("protein.")
    ]
    if len(protein_dataset_rows) != 10:
        raise NgsMolBioCapabilityError("Protein Dataset denominator must contain exactly 10 IDs")
    if sum(row["owner_contract_state"] == "closed" for row in protein_dataset_rows) != 7:
        raise NgsMolBioCapabilityError("Protein Dataset denominator must contain exactly 7 closed IDs")
    if sum(row["owner_contract_state"] == "unavailable" for row in protein_dataset_rows) != 3:
        raise NgsMolBioCapabilityError("Protein Dataset denominator must contain exactly 3 unavailable IDs")
    protein_common_rules = {
        "same_project_domain_authority",
        "exact_immutable_revision_only",
        "adapter_role_intersection",
        "no_current_head_resolution_during_preparation",
        "exact_historical_reopen",
    }
    for row in documents["dataset"]["entries"]:
        state = row["owner_contract_state"]
        if state == "unavailable" and (row["enabled"] or row["allowed_members"]):
            raise NgsMolBioCapabilityError(
                f"unavailable Dataset kind exposes member authority: {row['dataset_kind']}"
            )
        if state == "closed" and not row["allowed_members"]:
            raise NgsMolBioCapabilityError(
                f"closed Dataset kind has no member contract: {row['dataset_kind']}"
            )
        if row["dataset_kind"].startswith("protein."):
            if row["enabled"]:
                raise NgsMolBioCapabilityError(
                    f"Phase N0 Protein Dataset kind must remain disabled: {row['dataset_kind']}"
                )
            if state == "closed" and not protein_common_rules <= set(row["compatibility_rules"]):
                raise NgsMolBioCapabilityError(
                    f"closed Protein Dataset kind omits common compatibility rules: {row['dataset_kind']}"
                )
            if state == "unavailable" and row["compatibility_rules"] != [
                "no_immutable_producer_native_member_contract"
            ]:
                raise NgsMolBioCapabilityError(
                    f"unavailable Protein Dataset kind has an invalid reason: {row['dataset_kind']}"
                )
        for member in row["allowed_members"]:
            adapter = adapters.get(member["adapter_id"])
            if adapter is None or adapter["entity_kind"] != member["receipt_kind"]:
                raise NgsMolBioCapabilityError(
                    f"Dataset kind binds unknown receipt adapter: {row['dataset_kind']}"
                )
            if not set(member["allowed_roles"]) <= set(adapter["allowed_dataset_roles"]):
                raise NgsMolBioCapabilityError(
                    f"Dataset kind broadens adapter roles: {row['dataset_kind']}"
                )
            if member["compatibility_rule"] not in row["compatibility_rules"]:
                raise NgsMolBioCapabilityError(
                    f"Dataset kind omits member compatibility rule: {row['dataset_kind']}"
                )

    constraint_registry = documents["protein_constraint"]
    if constraint_registry["entries"]:
        raise NgsMolBioCapabilityError("Protein constraint payload denominator must remain empty")
    constraint_wrapper = schemas.get("bms.protein-constraint.v1")
    if constraint_wrapper is None or constraint_wrapper.get("x-bms-payload-registry-state") != "closed_empty":
        raise NgsMolBioCapabilityError("Protein constraint wrapper is not closed empty")

    _unique(documents["dataset"]["entries"], "dataset_kind", "Dataset kind")
    for row in documents["event"]["entries"]:
        schema_row = schema_rows.get(row["payload_schema_id"])
        if schema_row is None or schema_row["schema_sha256"] != row["payload_schema_sha256"]:
            raise NgsMolBioCapabilityError(f"event payload schema binding mismatch: {row['event_type']}")
    _unique(documents["event"]["entries"], "event_type", "event type")
    _unique(documents["branch_closure"]["entries"], "candidate_id", "branch candidate")
    _verify_phase_n0_receipt(schemas, reference_registry, documents["source_pin"])

    runtime_record, _runtime_raw = _read(_RUNTIME_RECORD)
    runtime_revision = runtime_record["successor_source_commit"]
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
        for gate in record["parity_ledger"]:
            gate["state"] = "pass"
            gate["evidence"] = f"package-local-runtime:{runtime_revision}:{gate['gate']}"
        record["inventory_sha256"] = _canonical_digest(record, "inventory_sha256")
        record["capability_sha256"] = record["inventory_sha256"]

    return inventory, schemas, reference_registry, documents


def _loaded() -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    Registry,
    dict[str, dict[str, Any]],
]:
    return _loaded_documents()


def capability_inventory() -> dict[str, Any]:
    return copy.deepcopy(_loaded()[0])


def capability_record(capability_id: str) -> dict[str, Any]:
    for record in _loaded()[0]["capabilities"]:
        if record["capability_id"] == capability_id:
            return copy.deepcopy(record)
    raise NgsMolBioCapabilityError(f"unknown capability: {capability_id}")


def capability_parameter_schema(capability_id: str) -> dict[str, Any]:
    inventory, schemas, _registry_value, _documents = _loaded()
    record = next(
        (item for item in inventory["capabilities"] if item["capability_id"] == capability_id),
        None,
    )
    if record is None:
        raise NgsMolBioCapabilityError(f"unknown capability: {capability_id}")
    return copy.deepcopy(schemas[record["parameter_schema_id"]])


def registered_schema(schema_id: str) -> dict[str, Any]:
    schema = _loaded()[1].get(schema_id)
    if schema is None:
        raise NgsMolBioCapabilityError(f"unknown schema: {schema_id}")
    return copy.deepcopy(schema)


def contract_registry(name: str) -> dict[str, Any]:
    if name not in {
        "adapter",
        "event",
        "dataset",
        "protein_constraint",
        "branch_closure",
        "source_pin",
        "schema",
        "payload_ownership",
    }:
        raise NgsMolBioCapabilityError(f"unknown contract registry: {name}")
    document = copy.deepcopy(_loaded()[3][name])
    if name == "dataset" and _runtime_overlay_authorities():
        for record in document.get("entries", []):
            if (
                isinstance(record, dict)
                and record.get("dataset_kind") in _PROJECT_GOVERNED_DATASET_KINDS
            ):
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
    _inventory, schemas, reference_registry, documents = _loaded()
    _validate(
        value,
        schemas["bms.ngs-molbio.connector-event.v1"],
        "connector event",
        reference_registry,
    )
    event = next(
        (row for row in documents["event"]["entries"] if row["event_type"] == value["event_type"]),
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
    _inventory, schemas, reference_registry, documents = _loaded()
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
    schema_entries = _unique(documents["schema"]["entries"], "schema_id", "schema ID")
    _verify_protein_domain_semantics(value, schema_entries)
    return copy.deepcopy(value)


def accepted_capability_ids() -> tuple[str, ...]:
    return tuple(
        record["capability_id"]
        for record in _loaded()[0]["capabilities"]
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
