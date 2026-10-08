"""Fail-closed server authority for frozen NGS/MolBio capability contracts."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from referencing import Registry, Resource

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_ROOT.parents[1]
_CONFIG_ROOT = _API_ROOT / "config/ngs_molbio"
_SCHEMA_ROOT = _REPO_ROOT / "schemas/ngs_molbio"
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
    "schema": "schema_registry_v1.json",
    "adapter": "adapter_registry_v1.json",
    "event": "event_registry_v1.json",
    "dataset": "dataset_kind_registry_v1.json",
    "branch_closure": "branch_closure_v1.json",
    "source_pin": "source_pin_v1.json",
}
_REGISTRY_SCHEMA_IDS = {
    "schema": "bms.ngs-molbio.schema-registry.v1",
    "adapter": "bms.ngs-molbio.adapter-registry.v1",
    "event": "bms.ngs-molbio.event-registry.v1",
    "dataset": "bms.ngs-molbio.dataset-kind-registry.v1",
    "branch_closure": "bms.ngs-molbio.branch-closure.v1",
    "source_pin": "bms.ngs-molbio.source-pin.v1",
}


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


def _git_value(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NgsMolBioCapabilityError("repository identity is unavailable") from exc
    value = completed.stdout.strip()
    if not value:
        raise NgsMolBioCapabilityError("repository identity is empty")
    return value


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
            registry = registry.with_resource(schema_id, Resource.from_contents(schema))
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
        if schema.get("$id") != schema_id:
            raise NgsMolBioCapabilityError(f"schema ID mismatch: {schema_id}")
        if _raw_digest(raw) != entry["schema_sha256"]:
            raise NgsMolBioCapabilityError(f"schema byte digest mismatch: {schema_id}")
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


def _git_blob(commit: str, relative: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "show", f"{commit}:{relative}"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NgsMolBioCapabilityError(f"baseline source authority is unavailable: {relative}") from exc
    return completed.stdout


def _verify_source_pin(document: dict[str, Any]) -> None:
    commit = document["baseline_commit"]
    if _git_value("rev-parse", f"{commit}^{{tree}}") != document["baseline_tree"]:
        raise NgsMolBioCapabilityError("audited baseline commit/tree mismatch")
    try:
        subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "merge-base", "--is-ancestor", commit, "HEAD"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NgsMolBioCapabilityError("audited baseline is not an ancestor of this package") from exc
    observed: set[str] = set()
    for row in document["authorities"]:
        relative = row["path"]
        if relative in observed:
            raise NgsMolBioCapabilityError(f"duplicate source authority path: {relative}")
        observed.add(relative)
        baseline_raw = _git_blob(commit, relative)
        if _raw_digest(baseline_raw) != row["sha256"]:
            raise NgsMolBioCapabilityError(f"baseline source authority digest mismatch: {relative}")


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


def _verify_adapter_owner(row: dict[str, Any]) -> None:
    owner = row["implementation_owner"]
    if owner is None:
        return
    implementation = _resolve_owner(owner, label="adapter")
    expected = {
        "adapter_id": row["adapter_id"],
        "adapter_version": row["adapter_version"],
        "entity_kind": row["entity_kind"],
    }
    for field, value in expected.items():
        if getattr(implementation, field, None) != value:
            raise NgsMolBioCapabilityError(f"adapter owner {field} mismatch: {row['adapter_id']}")


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

    if schema_registry["baseline_source_commit"] != documents["source_pin"]["baseline_commit"]:
        raise NgsMolBioCapabilityError("schema registry baseline commit disagrees with source pin")
    if schema_registry["baseline_source_tree"] != documents["source_pin"]["baseline_tree"]:
        raise NgsMolBioCapabilityError("schema registry baseline tree disagrees with source pin")
    for name in ("adapter", "event", "dataset", "branch_closure"):
        if documents[name]["baseline_source_commit"] != documents["source_pin"]["baseline_commit"]:
            raise NgsMolBioCapabilityError(f"{name} registry baseline commit disagrees with source pin")
    _verify_source_pin(documents["source_pin"])

    inventory, inventory_raw = _read(_CONFIG_ROOT / "capability_inventory_v1.json")
    inventory_schema = schemas.get("bms.ngs-molbio.capability-inventory.v1")
    if inventory_schema is None:
        raise NgsMolBioCapabilityError("capability inventory schema is absent")
    _validate(inventory, inventory_schema, "capability inventory", reference_registry)
    if inventory["content_sha256"] != _canonical_digest(inventory):
        raise NgsMolBioCapabilityError("capability inventory digest mismatch")
    if inventory["baseline_source_commit"] != documents["source_pin"]["baseline_commit"]:
        raise NgsMolBioCapabilityError("capability baseline commit disagrees with source pin")
    if inventory["baseline_source_tree"] != documents["source_pin"]["baseline_tree"]:
        raise NgsMolBioCapabilityError("capability baseline tree disagrees with source pin")
    byte_bindings = {
        "source_pin_sha256": "source_pin",
        "schema_registry_sha256": "schema",
        "adapter_registry_sha256": "adapter",
        "event_registry_sha256": "event",
        "dataset_registry_sha256": "dataset",
        "branch_closure_sha256": "branch_closure",
    }
    for field, name in byte_bindings.items():
        if inventory[field] != _raw_digest(raw_documents[name]):
            raise NgsMolBioCapabilityError(f"capability inventory binds different {name} bytes")

    schema_rows = _unique(schema_registry["entries"], "schema_id", "schema ID")
    capabilities = _unique(inventory["capabilities"], "capability_id", "capability ID")
    if len(capabilities) != 21:
        raise NgsMolBioCapabilityError("capability denominator must contain exactly 21 IDs")
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
        passed = all(row["state"] in {"pass", "not_applicable"} for row in record["parity_ledger"])
        if record["unsupported_parameter_keys"] or record["unclassified_parameter_keys"]:
            passed = False
        if record["plannable"] != (record["exposure_state"] == "accepted" and passed):
            raise NgsMolBioCapabilityError(f"unsafe exposure state: {capability_id}")

    adapters = _unique(documents["adapter"]["entries"], "adapter_id", "adapter ID")
    for adapter in adapters.values():
        _verify_adapter_owner(adapter)
    for row in documents["dataset"]["entries"]:
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

    _unique(documents["dataset"]["entries"], "dataset_kind", "Dataset kind")
    for row in documents["event"]["entries"]:
        schema_row = schema_rows.get(row["payload_schema_id"])
        if schema_row is None or schema_row["schema_sha256"] != row["payload_schema_sha256"]:
            raise NgsMolBioCapabilityError(f"event payload schema binding mismatch: {row['event_type']}")
    _unique(documents["event"]["entries"], "event_type", "event type")
    _unique(documents["branch_closure"]["entries"], "candidate_id", "branch candidate")

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
    if name not in {"adapter", "event", "dataset", "branch_closure", "source_pin", "schema"}:
        raise NgsMolBioCapabilityError(f"unknown contract registry: {name}")
    return copy.deepcopy(_loaded()[3][name])


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
    return copy.deepcopy(value)


def validate_domain_experiment(value: dict[str, Any]) -> dict[str, Any]:
    _inventory, schemas, reference_registry, _documents = _loaded()
    _validate(
        value,
        schemas["bms.domain-experiment.v2"],
        "domain experiment",
        reference_registry,
    )
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
