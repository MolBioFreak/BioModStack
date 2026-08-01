"""Executable Phase 1 contracts for conformational mapping.

The hash profile is the frozen Phase 0 stdlib profile, not a claim of complete
RFC 8785/JCS compatibility. Cross-record invariants that JSON Schema cannot
express are deliberately checked here and fail closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, computed_field, model_validator
from services.frustrampnn.analysis import score_class as canonical_frustrampnn_score_class


AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_FILENAMES = {
    "cm_request_v1": "cm_request_v1.schema.json",
    "cm_complex_snapshot_v1": "cm_complex_snapshot_v1.schema.json",
    "cm_native_artifacts_v1": "cm_native_artifacts_v1.schema.json",
    "cm_ensemble_v1": "cm_ensemble_v1.schema.json",
    "cm_structure_map_v1": "cm_structure_map_v1.schema.json",
    "cm_frustration_landscape_v1": "cm_frustration_landscape_v1.schema.json",
    "cm_state_landscape_analysis_v1": "cm_state_landscape_analysis_v1.schema.json",
    "cm_analysis_v1": "cm_analysis_v1.schema.json",
    "cm_mutagenesis_handoff_v1": "cm_mutagenesis_handoff_v1.schema.json",
}
_SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "schemas" / "conformational_mapping"
_ALLOWED_ENTITY_TYPES = {"protein", "dna", "rna", "ligand_ccd", "ligand_smiles", "ion"}
_SEQUENCE_ALPHABETS = {
    "protein": set("ACDEFGHIKLMNPQRSTVWYX"),
    "dna": set("ACGTN"),
    "rna": set("ACGUN"),
}
_ALLOWED_MODIFICATIONS = {"MSE", "SEP"}
_FEATURE_MODES = (
    "regenerate_mutated_protein_v1",
    "paired_regenerate_changed_protein_v1",
    "features_disabled_control_v1",
)
_STANDARD_PROTEIN_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
}
_ONE_TO_THREE = {
    "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
    "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
    "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
    "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
    "X": "UNK",
}
_PROTEIN_ATOMS = {
    "A": {"N", "CA", "C", "O", "CB"},
    "C": {"N", "CA", "C", "O", "CB", "SG"},
    "D": {"N", "CA", "C", "O", "CB", "CG", "OD1", "OD2"},
    "E": {"N", "CA", "C", "O", "CB", "CG", "CD", "OE1", "OE2"},
    "F": {"N", "CA", "C", "O", "CB", "CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
    "G": {"N", "CA", "C", "O"},
    "H": {"N", "CA", "C", "O", "CB", "CG", "ND1", "CD2", "CE1", "NE2"},
    "I": {"N", "CA", "C", "O", "CB", "CG1", "CG2", "CD1"},
    "K": {"N", "CA", "C", "O", "CB", "CG", "CD", "CE", "NZ"},
    "L": {"N", "CA", "C", "O", "CB", "CG", "CD1", "CD2"},
    "M": {"N", "CA", "C", "O", "CB", "CG", "SD", "CE"},
    "N": {"N", "CA", "C", "O", "CB", "CG", "OD1", "ND2"},
    "P": {"N", "CA", "C", "O", "CB", "CG", "CD"},
    "Q": {"N", "CA", "C", "O", "CB", "CG", "CD", "OE1", "NE2"},
    "R": {"N", "CA", "C", "O", "CB", "CG", "CD", "NE", "CZ", "NH1", "NH2"},
    "S": {"N", "CA", "C", "O", "CB", "OG"},
    "T": {"N", "CA", "C", "O", "CB", "OG1", "CG2"},
    "V": {"N", "CA", "C", "O", "CB", "CG1", "CG2"},
    "W": {"N", "CA", "C", "O", "CB", "CG", "CD1", "CD2", "NE1", "CE2", "CE3", "CZ2", "CZ3", "CH2"},
    "Y": {"N", "CA", "C", "O", "CB", "CG", "CD1", "CD2", "CE1", "CE2", "CZ", "OH"},
    "X": {"N", "CA", "C", "O"},
}
_CANDIDATE_ROLES = {
    "protenix_v2_ensemble": ("authoritative_cif", "confidence_json", "full_data_json"),
    "confornets": ("authoritative_cif", "confidence_json", "full_data_json"),
    "external_import": ("authoritative_cif",),
}
_GLOBAL_ROLES = {
    "protenix_v2_ensemble": {
        "runtime_input", "feature_policy", "log", "runtime_config", "composition_audit",
        "coordinate_ledger", "coordinate_context", "preprocessing_record", "msa_record",
        "template_record", "preprocess", "optional_analytics", "native_state",
    },
    "confornets": {
        "request", "preprocess", "native_state", "loss", "optional_analytics",
        "command_log", "runtime_provenance", "coordinate_ledger", "coordinate_context",
    },
    "external_import": {"receipt"},
}
_REQUIRED_GLOBAL_ROLES = {
    "protenix_v2_ensemble": {
        "runtime_input", "feature_policy", "log", "runtime_config", "composition_audit",
        "coordinate_ledger", "coordinate_context", "preprocessing_record", "msa_record",
        "template_record",
    },
    "confornets": {
        "request", "preprocess", "command_log", "runtime_provenance",
        "coordinate_ledger", "coordinate_context",
    },
    "external_import": {"receipt"},
}
_ALL_ARTIFACT_ROLES = set().union(*_GLOBAL_ROLES.values(), *map(set, _CANDIDATE_ROLES.values()))


class ContractValidationError(ValueError):
    """A fail-closed contract violation."""


def _check_canonical_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
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
    raise ContractValidationError(f"unsupported canonical JSON value at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Render the exact deterministic stdlib JSON profile frozen in Phase 0."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    _check_canonical_value(value)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"value is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


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
        value = json.loads(payload, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    except ContractValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"invalid JSON: {exc}") from exc
    _check_canonical_value(value)
    return value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def request_sha256(request: Mapping[str, Any]) -> str:
    """Hash a request with the sole self-referential field omitted.

    This is the frozen Phase 0 self-hash convention: ``request_sha256`` is
    omitted, every other field remains present, and the stdlib canonical JSON
    profile is hashed byte-for-byte.
    """

    if not isinstance(request, Mapping):
        raise ContractValidationError("request must be an object")
    return canonical_sha256({key: value for key, value in request.items() if key != "request_sha256"})


def _require_sha256(value: str, field: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise ContractValidationError(f"{field} must be a lowercase SHA-256")


def load_schema(schema_key: str) -> dict[str, Any]:
    try:
        filename = SCHEMA_FILENAMES[schema_key]
    except KeyError as exc:
        raise ContractValidationError(f"unknown schema key: {schema_key}") from exc
    path = _SCHEMA_ROOT / filename
    try:
        schema = canonical_json_loads(path.read_bytes())
    except OSError as exc:
        raise ContractValidationError(f"cannot read schema {schema_key}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ContractValidationError(f"schema {schema_key} is not an object")
    Draft202012Validator.check_schema(schema)
    return schema


def validate_schema(schema_key: str, instance: Any) -> None:
    _check_canonical_value(instance)
    schema = load_schema(schema_key)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        error = errors[0]
        location = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
        raise ContractValidationError(f"{schema_key} rejected {location}: {error.message}")

    if schema_key == "cm_request_v1":
        validate_seed_sources(
            api=instance["ordered_seeds"],
            generated_json=instance["ordered_seeds"],
            cli=instance["ordered_seeds"],
        )
        target_ids = [target["target_id"] for target in instance["targets"]]
        target_orders = [target["target_order"] for target in instance["targets"]]
        if len(set(target_ids)) != len(target_ids):
            raise ContractValidationError("request target IDs must be unique")
        if len(set(target_orders)) != len(target_orders):
            raise ContractValidationError("request target orders must be unique")
        if target_orders != list(range(len(target_orders))):
            raise ContractValidationError("request target orders must be contiguous and ordered")
        if "state_landscape_comparison" in instance:
            validate_state_landscape_comparison_request(instance, instance["state_landscape_comparison"])
        expected_request_hash = request_sha256(instance)
        if instance["request_sha256"] != expected_request_hash:
            raise ContractValidationError("request_sha256 does not match canonical request bytes")
    elif schema_key == "cm_complex_snapshot_v1":
        validate_complex_case(instance)
        roundtrip_instance_mappings(instance)
    elif schema_key == "cm_native_artifacts_v1":
        validate_native_artifacts(instance)
    elif schema_key == "cm_ensemble_v1":
        validate_ensemble(instance)
    elif schema_key == "cm_structure_map_v1":
        validate_structure_map(instance)
    elif schema_key == "cm_frustration_landscape_v1":
        if not instance["residues"]:
            raise ContractValidationError("landscape residues must be nonempty")
        residue_keys: set[tuple[Any, ...]] = set()
        for residue in instance["residues"]:
            validate_landscape_slots(residue["wt"], residue["slots"])
            key = (
                residue["entity_instance_id"], residue["auth_asym_id"],
                residue["auth_seq_id"], residue["insertion_code"], residue["sequence_index"],
            )
            if key in residue_keys:
                raise ContractValidationError("duplicate landscape residue identity")
            residue_keys.add(key)
    elif schema_key == "cm_state_landscape_analysis_v1":
        validate_state_landscape_analysis(instance)
    elif schema_key == "cm_analysis_v1":
        validate_analysis(instance)
    elif schema_key == "cm_mutagenesis_handoff_v1":
        validate_handoff(instance)


def validate_structure_map(instance: Mapping[str, Any]) -> None:
    """Validate structure-map invariants that JSON Schema cannot express."""

    provenance_fields = {
        "source_format",
        "source_sha256",
        "source_bytes",
        "normalized_pdb_sha256",
        "selected_source_model",
        "altloc_policy",
        "normalizer_version",
    }
    present = provenance_fields.intersection(instance)
    if present != provenance_fields:
        raise ContractValidationError(
            f"structure-map provenance is incomplete: missing={sorted(provenance_fields - present)}"
        )
    _require_sha256(instance["source_sha256"], "source_sha256")
    _require_sha256(instance["normalized_pdb_sha256"], "normalized_pdb_sha256")
    if instance["source_format"] != "mmcif":
        raise ContractValidationError("cm_structure_map_v1 requires authoritative mmCIF source")
    if instance["source_sha256"] != instance["original_cif_sha256"]:
        raise ContractValidationError("mmCIF source hash does not match original_cif_sha256")
    if any(row["source_model"] != instance["selected_source_model"] for row in instance["rows"]):
        raise ContractValidationError("row source model disagrees with selected_source_model")

    source_residues: set[tuple[Any, ...]] = set()
    pdb_residues: set[tuple[Any, ...]] = set()
    source_backbone_atoms: set[str] = set()
    instance_chains: dict[str, tuple[Any, ...]] = {}
    for row in instance["rows"]:
        source_key = (
            row["source_model"], row["source_entity_id"], row["label_asym_id"],
            row["label_seq_id"], row["auth_seq_id"], row["insertion_code"],
        )
        pdb_key = (row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"])
        if source_key in source_residues:
            raise ContractValidationError("duplicate source residue identity in structure map")
        if pdb_key in pdb_residues:
            raise ContractValidationError("normalized PDB residue identity collision")
        source_residues.add(source_key)
        pdb_residues.add(pdb_key)

        chain_identity = (
            row.get("source_entity_id"),
            row["label_asym_id"],
            row["auth_asym_id"],
            row["pdb_chain_id"],
        )
        previous = instance_chains.setdefault(row["entity_instance_id"], chain_identity)
        if previous != chain_identity:
            raise ContractValidationError("entity instance has conflicting chain identity")

        backbone = row["backbone_atoms"]
        present_atoms = {atom_id for atom_id in backbone.values() if atom_id is not None}
        if len(present_atoms) != sum(atom_id is not None for atom_id in backbone.values()):
            raise ContractValidationError("backbone source atom identity is not unique within a residue")
        if source_backbone_atoms.intersection(present_atoms):
            raise ContractValidationError("backbone source atom identity is reused across residues")
        source_backbone_atoms.update(present_atoms)
        missing = [atom for atom, atom_id in backbone.items() if atom_id is None]
        status = row["status"]
        reason = row["reason"]
        if status == "mapped":
            if missing or reason is not None or row["residue_name"] not in _STANDARD_PROTEIN_RESIDUES:
                raise ContractValidationError("mapped residue status/reason/backbone semantics are inconsistent")
        elif status == "missing_backbone":
            if not missing or not isinstance(reason, str) or not reason:
                raise ContractValidationError("missing_backbone status requires missing atoms and a reason")
        elif status == "nonstandard_residue":
            if row["residue_name"] in _STANDARD_PROTEIN_RESIDUES or not isinstance(reason, str) or not reason:
                raise ContractValidationError("nonstandard_residue status is inconsistent")
        elif status == "mapping_failed":
            if any(atom_id is not None for atom_id in backbone.values()) or not isinstance(reason, str) or not reason:
                raise ContractValidationError("mapping_failed requires null atom identities and a reason")


def validate_seed_sources(*, api: Sequence[Any], generated_json: Sequence[Any], cli: Sequence[Any]) -> list[int]:
    sources = [list(api), list(generated_json), list(cli)]
    if not sources[0]:
        raise ContractValidationError("ordered seeds must be nonempty")
    for source in sources:
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in source):
            raise ContractValidationError("ordered seeds must be strict integers")
        if any(seed < -(2**31) or seed > 2**31 - 1 for seed in source):
            raise ContractValidationError("ordered seed is outside signed 32-bit range")
        if len(set(source)) != len(source):
            raise ContractValidationError("ordered seeds must be unique")
    if not (sources[0] == sources[1] == sources[2]):
        raise ContractValidationError("seed source conflict")
    return sources[0]


def validate_complex_case(case: Mapping[str, Any]) -> None:
    entities = case.get("entities")
    bonds = case.get("bonds")
    if not isinstance(entities, list) or not isinstance(bonds, list):
        raise ContractValidationError("complex requires entities and bonds")
    instance_ids: list[str] = []
    source_entity_ids: list[str] = []
    instance_entities: dict[str, Mapping[str, Any]] = {}
    source_orders: list[int] = []
    for entity in entities:
        if not isinstance(entity, dict):
            raise ContractValidationError("unsupported_field: entity must be an object")
        entity_type = entity.get("entity_type")
        if entity_type not in _ALLOWED_ENTITY_TYPES:
            raise ContractValidationError("unsupported_entity")
        allowed = {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "source_order"}
        allowed.add("sequence" if entity_type in _SEQUENCE_ALPHABETS else "ccd" if entity_type in {"ligand_ccd", "ion"} else "smiles")
        if entity_type == "protein":
            allowed.add("modifications")
        unknown = set(entity) - allowed
        if unknown:
            raise ContractValidationError(f"unsupported_field: {sorted(unknown)}")
        count = entity.get("count")
        ids = entity.get("ordered_instance_ids")
        source_entity_id = entity.get("source_entity_id")
        if not isinstance(source_entity_id, str) or not source_entity_id:
            raise ContractValidationError("source entity identity is required")
        source_entity_ids.append(source_entity_id)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or not isinstance(ids, list) or len(ids) != count:
            raise ContractValidationError("count_instance_cardinality")
        if any(not isinstance(item, str) or not item for item in ids):
            raise ContractValidationError("count_instance_cardinality")
        instance_ids.extend(ids)
        for instance_id in ids:
            instance_entities[instance_id] = entity
        if "source_order" in entity:
            source_orders.append(entity["source_order"])
        if entity_type in _SEQUENCE_ALPHABETS:
            sequence = entity.get("sequence")
            if not isinstance(sequence, str) or not sequence or not set(sequence).issubset(_SEQUENCE_ALPHABETS[entity_type]):
                raise ContractValidationError("malformed_sequence")
        for modification in entity.get("modifications", []):
            if set(modification) != {"position", "modification"} or modification.get("modification") not in _ALLOWED_MODIFICATIONS:
                raise ContractValidationError("unsupported_modification")
            position = modification.get("position")
            if isinstance(position, bool) or not isinstance(position, int) or position < 1 or position > len(entity["sequence"]):
                raise ContractValidationError("unsupported_modification")
    if len(set(instance_ids)) != len(instance_ids):
        raise ContractValidationError("duplicate_instance_id")
    if len(set(source_entity_ids)) != len(source_entity_ids):
        raise ContractValidationError("duplicate_source_entity_id")
    if source_orders and (len(source_orders) != len(entities) or len(set(source_orders)) != len(source_orders)):
        raise ContractValidationError("ambiguous_ordering")
    for bond in bonds:
        if not isinstance(bond, dict) or set(bond) != {"left", "right"}:
            raise ContractValidationError("unsupported_bond")
        for endpoint in (bond["left"], bond["right"]):
            if not isinstance(endpoint, dict) or set(endpoint) != {"instance_id", "position", "atom"}:
                raise ContractValidationError("unsupported_bond")
            if endpoint["instance_id"] not in instance_ids:
                raise ContractValidationError("dangling_bond_reference")
            if isinstance(endpoint["position"], bool) or not isinstance(endpoint["position"], int) or endpoint["position"] < 1:
                raise ContractValidationError("unsupported_bond")
            entity = instance_entities[endpoint["instance_id"]]
            position = endpoint["position"]
            atom = endpoint["atom"]
            if not isinstance(atom, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9'\"]{0,7}", atom):
                raise ContractValidationError("unsupported_bond")
            if entity["entity_type"] in _SEQUENCE_ALPHABETS:
                if position > len(entity["sequence"]):
                    raise ContractValidationError("bond_sequence_bounds")
                if entity["entity_type"] == "protein":
                    residue = entity["sequence"][position - 1]
                    if atom not in _PROTEIN_ATOMS[residue]:
                        raise ContractValidationError("bond_atom_semantics")
            elif position != 1:
                raise ContractValidationError("bond_sequence_bounds")
    admission = case.get("admission")
    if admission is not None and (
        admission.get("token_count", 0) > admission.get("token_limit", 0)
        or bool(admission.get("conversion_omissions"))
    ):
        raise ContractValidationError("lossy_conversion_or_token_limit")


def roundtrip_instance_mappings(
    snapshot: Mapping[str, Any],
) -> dict[tuple[str, str, str], tuple[Any, ...]]:
    ordered_source_keys = [
        (entity["source_entity_id"], instance_id)
        for entity in snapshot["entities"]
        for instance_id in entity["ordered_instance_ids"]
    ]
    source_keys = set(ordered_source_keys)
    mappings = snapshot.get("instance_mappings")
    if not isinstance(mappings, list):
        raise ContractValidationError("instance mappings are incomplete")
    result: dict[tuple[str, str, str], tuple[Any, ...]] = {}
    candidate_sources: dict[str, set[tuple[str, str]]] = {}
    candidate_runtime_keys: dict[str, set[tuple[Any, ...]]] = {}
    candidate_output_keys: dict[str, set[tuple[Any, ...]]] = {}
    candidate_runtime_orders: dict[str, list[int]] = {}
    candidate_output_orders: dict[str, list[int]] = {}
    runtime_by_source: dict[tuple[str, str], tuple[Any, ...]] = {}
    output_entity_by_source: dict[str, dict[str, str]] = {}
    source_by_output_entity: dict[str, dict[str, str]] = {}
    for mapping in mappings:
        source_key = (mapping["source_entity_id"], mapping["source_instance_id"])
        candidate = mapping["candidate_id"]
        runtime_key = (
            mapping["runtime_target_id"],
            mapping["runtime_entity_id"],
            mapping["runtime_instance_id"],
            mapping["runtime_order"],
        )
        output_key = (
            mapping["output_entity_id"],
            mapping["output_label_asym_id"],
            mapping["output_auth_asym_id"],
            mapping["output_entity_order"],
        )
        if source_key not in source_keys:
            raise ContractValidationError("instance mapping binds the wrong source entity")
        if mapping["runtime_target_id"] != snapshot["target_id"]:
            raise ContractValidationError("instance mapping runtime target mismatch")
        result_key = (candidate, *source_key)
        sources = candidate_sources.setdefault(candidate, set())
        runtime_keys = candidate_runtime_keys.setdefault(candidate, set())
        output_keys = candidate_output_keys.setdefault(candidate, set())
        if result_key in result or source_key in sources or runtime_key in runtime_keys or output_key in output_keys:
            raise ContractValidationError("candidate instance mapping is not bijective")
        previous_runtime = runtime_by_source.setdefault(source_key, runtime_key)
        if previous_runtime != runtime_key:
            raise ContractValidationError("source-to-runtime mapping changes across candidates")
        source_outputs = output_entity_by_source.setdefault(candidate, {})
        output_sources = source_by_output_entity.setdefault(candidate, {})
        output_entity_id = mapping["output_entity_id"]
        previous_output = source_outputs.setdefault(mapping["source_entity_id"], output_entity_id)
        if previous_output != output_entity_id:
            raise ContractValidationError("one source entity maps to multiple output entities within candidate")
        previous_source = output_sources.setdefault(output_entity_id, mapping["source_entity_id"])
        if previous_source != mapping["source_entity_id"]:
            raise ContractValidationError("candidate output entity mapping is not bijective")
        result[result_key] = (*runtime_key, *output_key)
        sources.add(source_key)
        runtime_keys.add(runtime_key)
        output_keys.add(output_key)
        candidate_runtime_orders.setdefault(candidate, []).append(mapping["runtime_order"])
        candidate_output_orders.setdefault(candidate, []).append(mapping["output_entity_order"])
    for candidate, mapped_sources in candidate_sources.items():
        if mapped_sources != source_keys:
            raise ContractValidationError(f"candidate {candidate} mapping is not complete")
        expected_orders = list(range(len(ordered_source_keys)))
        if (
            candidate_runtime_orders[candidate] != expected_orders
            or candidate_output_orders[candidate] != expected_orders
        ):
            raise ContractValidationError(
                "candidate mapping orders must preserve authoritative instance order"
            )
    return result


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProtenixCoordinates(_StrictModel):
    backend: Literal["protenix_v2_ensemble"]
    target_id: str = Field(min_length=1)
    ordered_seed: int = Field(ge=-(2**31), le=2**31 - 1)
    sample_index: int = Field(ge=0)


class ConforNetsCoordinates(_StrictModel):
    backend: Literal["confornets"]
    target_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    test_case_id: str = Field(min_length=1)
    reference_id: str | None
    run_index: int = Field(ge=0)
    saved_step: int = Field(ge=0)
    confornet_index: int = Field(ge=0)
    sample_index: int = Field(ge=0)


class ExternalImportCoordinates(_StrictModel):
    backend: Literal["external_import"]
    target_id: str = Field(min_length=1)
    staged_index: int = Field(ge=0)
    source_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    staged_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


BackendCoordinates = Annotated[
    ProtenixCoordinates | ConforNetsCoordinates | ExternalImportCoordinates,
    Field(discriminator="backend"),
]
_COORDINATE_ADAPTER = TypeAdapter(BackendCoordinates)


def parse_backend_coordinates(value: Any) -> ProtenixCoordinates | ConforNetsCoordinates | ExternalImportCoordinates:
    return _COORDINATE_ADAPTER.validate_python(value, strict=True)


def _target_slug(target_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", target_id.lower()).strip("-")
    if not slug:
        raise ContractValidationError("target identity cannot produce an empty slug")
    return slug


def candidate_id(coordinates: Mapping[str, Any] | BackendCoordinates) -> str:
    parsed = parse_backend_coordinates(coordinates)
    value = parsed.model_dump(mode="json")
    slug = _target_slug(parsed.target_id)
    if isinstance(parsed, ProtenixCoordinates):
        return f"cm_ptx_{slug}_{canonical_sha256(value)[:20]}"
    if isinstance(parsed, ConforNetsCoordinates):
        return f"cm_cn_{slug}_{canonical_sha256(value)[:20]}"
    return f"cm_imp_{slug}_{parsed.staged_index:06d}_{parsed.source_content_sha256[:16]}"


def validate_state_landscape_comparison_request(
    request: Mapping[str, Any], comparison: Mapping[str, Any],
) -> None:
    """Bind optional state-analysis authority to this request's targets and backend."""

    target_id = comparison["target_id"]
    if target_id not in {target["target_id"] for target in request["targets"]}:
        raise ContractValidationError("state landscape comparison target is not a request target")
    if comparison["mode"] != "reference":
        return
    coordinates = comparison["reference_backend_coordinates"]
    try:
        parsed = parse_backend_coordinates(coordinates)
    except ValidationError as exc:
        raise ContractValidationError("state landscape reference coordinates are invalid") from exc
    if parsed.target_id != target_id:
        raise ContractValidationError("state landscape reference target does not match comparison target")
    if parsed.backend != request["backend"]:
        raise ContractValidationError("state landscape reference backend does not match request backend")


def ensure_candidate_id_uniqueness(records: Sequence[Mapping[str, Any]]) -> None:
    seen_ids: dict[str, bytes] = {}
    seen_coordinates: set[bytes] = set()
    for record in records:
        identifier = record.get("candidate_id")
        if not isinstance(identifier, str) or not identifier:
            raise ContractValidationError("candidate ID is missing")
        coordinates = canonical_json_bytes(record.get("backend_coordinates"))
        if identifier in seen_ids:
            if seen_ids[identifier] != coordinates:
                raise ContractValidationError("candidate ID collision with different coordinates")
            raise ContractValidationError("duplicate candidate ID and coordinate")
        if coordinates in seen_coordinates:
            raise ContractValidationError("duplicate backend coordinate")
        seen_ids[identifier] = coordinates
        seen_coordinates.add(coordinates)


class ResumeDescriptor(_StrictModel):
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complex_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend: Literal["protenix_v2_ensemble", "confornets", "external_import"]
    backend_version: str = Field(min_length=1)
    backend_commit: str = Field(min_length=1)
    runtime_identity: str = Field(min_length=1)
    container_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_id: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_policy: dict[str, Any]
    feature_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordered_seeds: list[int]
    samples_per_seed: int = Field(gt=0)
    coordinate_plan: list[dict[str, Any]] = Field(min_length=1)
    expected_candidate_cardinality: int = Field(gt=0)
    expected_manifest_schema: str = Field(min_length=1)
    expected_manifest_version: int = Field(gt=0)
    required_artifact_roles: list[str] = Field(min_length=1)
    expected_manifest_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings_runtime_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_authority(self) -> "ResumeDescriptor":
        _check_canonical_value(self.model_dump(mode="python", exclude={"resume_key"}))
        validate_seed_sources(
            api=self.ordered_seeds,
            generated_json=self.ordered_seeds,
            cli=self.ordered_seeds,
        )
        policy = validate_feature_policy(self.feature_policy)
        if feature_policy_sha256(policy) != self.feature_policy_sha256:
            raise ValueError("feature_policy_sha256 mismatch")
        if self.expected_manifest_schema != "cm_ensemble" or self.expected_manifest_version != 1:
            raise ValueError("resume descriptor manifest contract is not cm_ensemble_v1")
        if len(set(self.required_artifact_roles)) != len(self.required_artifact_roles):
            raise ValueError("required artifact roles must be unique")
        required = set(_CANDIDATE_ROLES[self.backend])
        if not required.issubset(self.required_artifact_roles):
            raise ValueError("required artifact roles are incomplete")
        parsed = [parse_backend_coordinates(coordinate) for coordinate in self.coordinate_plan]
        if any(coordinate.backend != self.backend for coordinate in parsed):
            raise ValueError("coordinate plan backend mismatch")
        coordinate_bytes = [canonical_json_bytes(coordinate) for coordinate in self.coordinate_plan]
        if len(set(coordinate_bytes)) != len(coordinate_bytes):
            raise ValueError("coordinate plan contains duplicates")
        if self.expected_candidate_cardinality != len(self.coordinate_plan):
            raise ValueError("expected candidate cardinality does not match coordinate plan")
        if self.backend == "protenix_v2_ensemble":
            seeds = [coordinate.ordered_seed for coordinate in parsed if isinstance(coordinate, ProtenixCoordinates)]
            if set(seeds) != set(self.ordered_seeds):
                raise ValueError("coordinate plan seed set does not match ordered seeds")
            expected_pairs = {
                (seed, sample)
                for seed in self.ordered_seeds
                for sample in range(self.samples_per_seed)
            }
            actual_pairs = {
                (coordinate.ordered_seed, coordinate.sample_index)
                for coordinate in parsed
                if isinstance(coordinate, ProtenixCoordinates)
            }
            if actual_pairs != expected_pairs:
                raise ValueError("coordinate plan does not equal ordered seed/sample product")
        return self

    @computed_field
    @property
    def resume_key(self) -> str:
        return canonical_sha256(self.model_dump(exclude={"resume_key"}, mode="json"))


def _validate_relative_path(value: str) -> None:
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
        raise ContractValidationError(f"unsafe manifest relative path: {value!r}")


def validate_manifest_set_equality(
    expected: set[str] | Sequence[str], *, observed: set[str] | Sequence[str], referenced: set[str] | Sequence[str]
) -> None:
    expected_values, observed_values, referenced_values = list(expected), list(observed), list(referenced)
    for name, values in (
        ("expected", expected_values), ("observed", observed_values), ("referenced", referenced_values)
    ):
        duplicates = sorted(path for path, count in Counter(values).items() if count != 1)
        if duplicates:
            raise ContractValidationError(f"{name} manifest contains duplicate paths: {duplicates}")
    expected_set, observed_set, referenced_set = (
        set(expected_values), set(observed_values), set(referenced_values)
    )
    for path in expected_set | observed_set | referenced_set:
        _validate_relative_path(path)
    basenames = [PurePosixPath(path).name for path in expected_set]
    if len(set(basenames)) != len(basenames):
        raise ContractValidationError("manifest contains basename collision")
    if observed_set != expected_set:
        raise ContractValidationError(
            f"observed manifest set mismatch: missing={sorted(expected_set-observed_set)}, extra={sorted(observed_set-expected_set)}"
        )
    if referenced_set != expected_set:
        raise ContractValidationError(
            f"referenced manifest set mismatch: missing={sorted(expected_set-referenced_set)}, extra={sorted(referenced_set-expected_set)}"
        )


def validate_native_artifacts(instance: Mapping[str, Any]) -> None:
    """Validate path, coordinate, candidate, and semantic-role multiplicity."""

    backend = instance["backend"]
    paths = [record["relative_path"] for record in instance["files"]]
    duplicates = sorted(path for path, count in Counter(paths).items() if count != 1)
    if duplicates:
        raise ContractValidationError(f"native manifest contains duplicate paths: {duplicates}")
    path_set = set(paths)
    scoped_roles: list[tuple[str, str]] = []
    candidate_scoped_roles: set[tuple[str, str]] = set()
    candidate_ids: set[str] = set()
    for record in instance["files"]:
        _validate_relative_path(record["relative_path"])
        if record["semantic_role"] not in _ALL_ARTIFACT_ROLES:
            raise ContractValidationError("native manifest semantic role is unknown")
        for related_path in record["related_paths"]:
            _validate_relative_path(related_path)
            if related_path not in path_set:
                raise ContractValidationError("native manifest related path is unbound")
        candidate = record["candidate_id"]
        coordinates = record["backend_coordinates"]
        if (candidate is None) != (coordinates is None):
            raise ContractValidationError("candidate ID and backend coordinate must be jointly nullable")
        if coordinates is None:
            if record["semantic_role"] not in _GLOBAL_ROLES[backend]:
                raise ContractValidationError("candidate artifact role lacks candidate coordinates")
            key = ("<global>", record["semantic_role"])
        else:
            parsed = parse_backend_coordinates(coordinates)
            if parsed.backend != backend:
                raise ContractValidationError("native artifact backend coordinate mismatch")
            if candidate != candidate_id(coordinates):
                raise ContractValidationError("native artifact candidate ID does not match coordinates")
            if record["semantic_role"] not in _CANDIDATE_ROLES[backend]:
                raise ContractValidationError("candidate artifact semantic role is invalid")
            key = (candidate, record["semantic_role"])
            candidate_ids.add(candidate)
        if candidate is not None and key in candidate_scoped_roles:
            raise ContractValidationError("native manifest has duplicate candidate/role multiplicity")
        scoped_roles.append(key)
        if candidate is not None:
            candidate_scoped_roles.add(key)
    required_global = {("<global>", role) for role in _REQUIRED_GLOBAL_ROLES[backend]}
    actual_global = {key for key in scoped_roles if key[0] == "<global>"}
    if not required_global.issubset(actual_global):
        raise ContractValidationError("native manifest required global roles are incomplete")
    required_candidate = {
        (candidate, role)
        for candidate in candidate_ids
        for role in _CANDIDATE_ROLES[backend]
    }
    actual_candidate = {key for key in scoped_roles if key[0] != "<global>"}
    if actual_candidate != required_candidate:
        raise ContractValidationError("native manifest candidate role multiplicity is incomplete or extra")


def validate_ensemble(instance: Mapping[str, Any]) -> None:
    if instance["resumable"]:
        if not isinstance(instance["resume_descriptor"], Mapping):
            raise ContractValidationError("resumable ensemble requires ResumeDescriptor")
        descriptor = ResumeDescriptor.model_validate(instance["resume_descriptor"])
        if descriptor.resume_key != instance["resume_key"]:
            raise ContractValidationError("resumable ensemble resume key mismatch")
    elif instance["resume_descriptor"] is not None or instance["resume_key"] != "0" * 64:
        raise ContractValidationError("non-resumable ensemble must disable resume identity")
    expected = instance["expected_coordinates"]
    candidates = instance["candidates"]
    if instance["expected_cardinality"] != len(expected):
        raise ContractValidationError("expected coordinate cardinality mismatch")
    if instance["expected_cardinality"] != len(candidates):
        raise ContractValidationError("candidate cardinality mismatch")
    ensure_candidate_id_uniqueness(candidates)
    expected_bytes = [canonical_json_bytes(coordinate) for coordinate in expected]
    actual_bytes = [canonical_json_bytes(candidate["backend_coordinates"]) for candidate in candidates]
    if Counter(expected_bytes) != Counter(actual_bytes):
        raise ContractValidationError("candidate coordinate multiset does not equal expected coordinates")
    paths: list[str] = []
    for candidate in candidates:
        coordinate = candidate["backend_coordinates"]
        parsed = parse_backend_coordinates(coordinate)
        if parsed.backend != instance["backend"]:
            raise ContractValidationError("ensemble candidate backend mismatch")
        if candidate["candidate_id"] != candidate_id(coordinate):
            raise ContractValidationError("ensemble candidate ID does not match coordinates")
        paths.append(candidate["authoritative_structure_path"])
        paths.extend(candidate["sidecar_paths"])
    for path in paths:
        _validate_relative_path(path)
    duplicates = sorted(path for path, count in Counter(paths).items() if count != 1)
    if duplicates:
        raise ContractValidationError(f"ensemble shares or duplicates artifact paths: {duplicates}")


def validate_state_landscape_analysis(instance: Mapping[str, Any]) -> None:
    """Fail closed on state-comparison missingness and stable row identities."""

    comparison = {
        "mode": instance["comparison_mode"],
        "comparison_target_id": instance["comparison_target_id"],
        "comparison_scope": instance["comparison_scope"],
        "reference_backend_coordinates": instance["reference_backend_coordinates"],
        "reference_candidate_id": instance["reference_candidate_id"],
        "resolved_pairs": instance["resolved_pairs"],
    }
    if instance["comparison_sha256"] != canonical_sha256(comparison):
        raise ContractValidationError("state landscape comparison hash does not bind authority and resolved pairs")
    if instance["comparison_mode"] == "reference" and not instance["reference_candidate_id"]:
        raise ContractValidationError("reference state comparison requires an explicit reference candidate")
    if instance["comparison_mode"] == "pairwise" and instance["reference_candidate_id"] is not None:
        raise ContractValidationError("pairwise state comparison cannot claim a reference candidate")
    resolved_pairs = instance["resolved_pairs"]
    if resolved_pairs != sorted(resolved_pairs, key=lambda pair: pair["pair_id"]):
        raise ContractValidationError("state landscape resolved pairs must have stable pair-id order")
    pair_identities = [
        (pair["pair_id"], pair["candidate_a_id"], pair["candidate_b_id"])
        for pair in resolved_pairs
    ]
    if len({pair[0] for pair in pair_identities}) != len(pair_identities):
        raise ContractValidationError("state landscape resolved pair IDs must be unique")
    if any(pair[1] == pair[2] for pair in pair_identities):
        raise ContractValidationError("state landscape resolved pair candidates must differ")
    if instance["comparison_mode"] == "reference":
        selector = instance["reference_backend_coordinates"]
        try:
            parsed = parse_backend_coordinates(selector)
        except ValidationError as exc:
            raise ContractValidationError("reference state comparison coordinates are invalid") from exc
        if parsed.target_id != instance["comparison_target_id"]:
            raise ContractValidationError("reference state comparison target does not match comparison target")
        if any(pair[1] != instance["reference_candidate_id"] for pair in pair_identities):
            raise ContractValidationError("reference state comparison pairs must begin with the reference candidate")
        if len({pair[2] for pair in pair_identities}) != len(pair_identities) or any(
            pair[2] == instance["reference_candidate_id"] for pair in pair_identities
        ):
            raise ContractValidationError("reference state comparison candidates must be unique and exclude reference")
    resolved_pair_set = set(pair_identities)
    support_pairs = {
        (entry["pair_id"], entry["candidate_a_id"], entry["candidate_b_id"])
        for entry in instance["support_ledger"]
    }
    if support_pairs != resolved_pair_set or len(instance["support_ledger"]) != len(resolved_pair_set):
        raise ContractValidationError("state landscape support ledger must cover exactly the resolved pair ledger")
    for row in [*instance["rows"], *instance["exclusion_ledger"]]:
        if (row["pair_id"], row["candidate_a_id"], row["candidate_b_id"]) not in resolved_pair_set:
            raise ContractValidationError("state landscape rows and exclusions must belong to resolved pairs")
    row_keys: set[tuple[Any, ...]] = set()
    for row in instance["rows"]:
        identity = row["identity"]
        key = (
            row["pair_id"], identity["target_id"], identity["entity_instance_id"],
            identity["auth_asym_id"], identity["auth_seq_id"], identity["insertion_code"],
            identity["sequence_index"], identity["validated_wt"],
        )
        if key in row_keys:
            raise ContractValidationError("state landscape analysis rows must have unique pair identities")
        row_keys.add(key)
        for metric_name in (
            "native_score", "high_non_native_highly_frustrated_fraction",
            "maximum_non_native_substitution_delta_relative_to_native",
        ):
            metric = row["metrics"][metric_name]
            values = (metric["a"], metric["b"], metric["delta_b_minus_a"])
            if metric["status"] == "ok":
                if metric["reason"] is not None or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                    for value in values
                ):
                    raise ContractValidationError("available state numeric metric must be finite and reason-free")
            elif any(value is not None for value in values) or not isinstance(metric["reason"], str) or not metric["reason"]:
                raise ContractValidationError("unavailable state numeric metric must not fabricate values")
        native_class = row["metrics"]["native_class"]
        if native_class["status"] == "ok":
            if native_class["reason"] is not None or not all(
                isinstance(native_class[field], str) and native_class[field]
                for field in ("a", "b", "transition")
            ):
                raise ContractValidationError("available state class metric is incomplete")
        elif any(native_class[field] is not None for field in ("a", "b", "transition")) or not isinstance(native_class["reason"], str) or not native_class["reason"]:
            raise ContractValidationError("unavailable state class metric must not fabricate values")


def validate_analysis(instance: Mapping[str, Any]) -> None:
    result_keys: set[str] = set()
    for result in instance["results"]:
        key = result["source_row_key"]
        if key in result_keys:
            raise ContractValidationError("analysis result source-row keys must be unique")
        result_keys.add(key)
        identity = result["identity"]
        if key != analysis_source_row_key(identity):
            raise ContractValidationError("analysis source-row key does not bind its identity")
        expected = result["expected_coordinate_count"]
        valid = result["valid_coordinate_count"]
        if valid > expected:
            raise ContractValidationError("analysis valid coordinate count exceeds expected count")
        expected_support = valid / expected
        if not math.isclose(result["coordinate_support_fraction"], expected_support, rel_tol=0.0, abs_tol=1e-12):
            raise ContractValidationError("analysis coordinate support disagrees with counts")
        status = result["status"]
        metrics = (result["hierarchical_mean"], result["hotspot_score"], result["switch_score"])
        reason = result["failure_reason"]
        if status == "robust":
            if any(metric is None for metric in metrics) or reason is not None:
                raise ContractValidationError("robust analysis requires complete metrics and no failure reason")
        elif status == "conditional":
            if any(metric is None for metric in metrics) or not isinstance(reason, str) or not reason:
                raise ContractValidationError("conditional analysis requires metrics and a threshold reason")
        elif not isinstance(reason, str) or not reason or any(metric is not None for metric in metrics):
            raise ContractValidationError("insufficient-support analysis requires null metrics and a reason")
    exclusion_keys = [record["coordinate"] for record in instance["exclusions"]]
    if len(set(exclusion_keys)) != len(exclusion_keys):
        raise ContractValidationError("analysis exclusions must have unique coordinates")


def handoff_mutation_identity(instance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "target_id": instance["target_id"],
        "entity_instance_id": instance["entity_instance_id"],
        "auth_asym_id": instance["auth_asym_id"],
        "auth_seq_id": instance["auth_seq_id"],
        "insertion_code": instance["insertion_code"],
        "sequence_index": instance["sequence_index"],
        "validated_wt": instance["validated_wt"],
        "substitution": instance["substitution"],
    }


def handoff_evidence_key(instance: Mapping[str, Any]) -> str:
    return analysis_source_row_key(handoff_mutation_identity(instance))


def analysis_source_row_key(identity: Mapping[str, Any]) -> str:
    normalized = {
        "target_id": str(identity["target_id"]),
        "entity_instance_id": str(identity["entity_instance_id"]),
        "auth_asym_id": str(identity["auth_asym_id"]),
        "auth_seq_id": int(identity["auth_seq_id"]),
        "insertion_code": str(identity.get("insertion_code") or ""),
        "sequence_index": int(identity["sequence_index"]),
        "validated_wt": str(identity["validated_wt"]),
        "substitution": str(identity["substitution"]),
    }
    return "cm_row_" + canonical_sha256(normalized)


def validate_handoff(instance: Mapping[str, Any]) -> None:
    if instance["validated_wt"] == instance["substitution"]:
        raise ContractValidationError("handoff substitution must mutate WT")
    insertion = instance["insertion_code"]
    expected_string = (
        f"{instance['validated_wt']}{instance['auth_seq_id']}{insertion}{instance['substitution']}"
    )
    if instance["mutation_set_string"] != expected_string:
        raise ContractValidationError("mutation_set_string does not match mapped mutation identity")
    identity = handoff_mutation_identity(instance)
    if instance["mutation_set_id"] != canonical_sha256([identity]):
        raise ContractValidationError("mutation_set_id does not match canonical candidate set")
    expected_evidence = handoff_evidence_key(instance)
    if instance["evidence_row_keys"] != [expected_evidence]:
        raise ContractValidationError("handoff evidence keys do not exactly bind the mutation")
    validate_feature_policy(instance["feature_policy"])
    seeds = instance["resampling_settings"].get("ordered_seeds")
    if seeds is not None:
        validate_seed_sources(api=seeds, generated_json=seeds, cli=seeds)
    expected_key = handoff_idempotency_key(
        instance["source_complex_sha256"], [identity], instance["resampling_settings"]
    )
    if instance["idempotency_key"] != expected_key:
        raise ContractValidationError("handoff idempotency_key mismatch")


def validate_contract_bundle(bundle: Mapping[str, Any], *, resume_descriptor: Mapping[str, Any] | None = None) -> None:
    """Validate all present v1 records and their authoritative cross-bindings."""

    unknown = set(bundle) - set(SCHEMA_FILENAMES)
    if unknown:
        raise ContractValidationError(f"unknown contract bundle members: {sorted(unknown)}")
    for schema_key, instance in bundle.items():
        validate_schema(schema_key, instance)

    request = bundle.get("cm_request_v1")
    snapshot = bundle.get("cm_complex_snapshot_v1")
    native = bundle.get("cm_native_artifacts_v1")
    ensemble = bundle.get("cm_ensemble_v1")
    structure_map = bundle.get("cm_structure_map_v1")
    landscape = bundle.get("cm_frustration_landscape_v1")
    analysis = bundle.get("cm_analysis_v1")
    handoff = bundle.get("cm_mutagenesis_handoff_v1")
    if request and native:
        if native["request_id"] != request["request_id"] or native["backend"] != request["backend"]:
            raise ContractValidationError("native manifest is not bound to request identity/backend")
    if request and ensemble:
        if ensemble["request_id"] != request["request_id"] or ensemble["backend"] != request["backend"]:
            raise ContractValidationError("ensemble is not bound to request identity/backend")
        if ensemble["request_sha256"] != request["request_sha256"]:
            raise ContractValidationError("ensemble request hash mismatch")
    if request and snapshot:
        request_target_ids = {target["target_id"] for target in request["targets"]}
        if snapshot["target_id"] not in request_target_ids:
            raise ContractValidationError(
                "complex snapshot target is not authorized by request targets"
            )
    if snapshot and ensemble:
        if ensemble["source_snapshot_sha256"] != canonical_sha256(snapshot):
            raise ContractValidationError("ensemble source snapshot hash mismatch")
        snapshot_candidate_ids = {
            mapping["candidate_id"] for mapping in snapshot["instance_mappings"]
        }
        ensemble_candidate_ids = {
            candidate["candidate_id"] for candidate in ensemble["candidates"]
        }
        if snapshot_candidate_ids != ensemble_candidate_ids:
            raise ContractValidationError(
                "complex snapshot candidate mappings do not exactly match ensemble candidates"
            )
        ensemble_target_ids = {
            candidate["backend_coordinates"]["target_id"]
            for candidate in ensemble["candidates"]
        }
        if ensemble_target_ids != {snapshot["target_id"]}:
            raise ContractValidationError(
                "complex snapshot target does not exactly match ensemble candidate targets"
            )
    if native and ensemble:
        if ensemble["native_manifest_sha256"] != canonical_sha256(native):
            raise ContractValidationError("ensemble native manifest hash mismatch")
        native_by_path = {record["relative_path"]: record for record in native["files"]}
        required_native_paths: list[str] = []
        for candidate in ensemble["candidates"]:
            coordinate = candidate["backend_coordinates"]
            candidate_id_value = candidate["candidate_id"]
            role_paths = {
                "authoritative_cif": candidate["authoritative_structure_path"],
            }
            sidecar_roles = [role for role in _CANDIDATE_ROLES[ensemble["backend"]] if role != "authoritative_cif"]
            if len(candidate["sidecar_paths"]) != len(sidecar_roles):
                raise ContractValidationError("candidate mandatory sidecar role multiplicity mismatch")
            role_paths.update(zip(sidecar_roles, candidate["sidecar_paths"], strict=True))
            for role, path in role_paths.items():
                required_native_paths.append(path)
                record = native_by_path.get(path)
                if record is None:
                    raise ContractValidationError("ensemble references artifact absent from native manifest")
                if record["semantic_role"] != role or record["candidate_id"] != candidate_id_value:
                    raise ContractValidationError("ensemble/native role or candidate binding mismatch")
                if canonical_json_bytes(record["backend_coordinates"]) != canonical_json_bytes(coordinate):
                    raise ContractValidationError("ensemble/native coordinate binding mismatch")
                if role == "authoritative_cif" and record["sha256"] != candidate["authoritative_structure_sha256"]:
                    raise ContractValidationError("ensemble/native authoritative structure hash mismatch")
        candidate_native_paths = [
            record["relative_path"] for record in native["files"] if record["candidate_id"] is not None
        ]
        if Counter(candidate_native_paths) != Counter(required_native_paths):
            raise ContractValidationError("native candidate artifacts are missing, extra, or duplicated")
    if snapshot and structure_map:
        validate_structure_map_snapshot_binding(structure_map, snapshot)
    if structure_map and landscape:
        if landscape["target_id"] != structure_map["target_id"] or landscape["candidate_id"] != structure_map["candidate_id"]:
            raise ContractValidationError("landscape identity is not bound to structure map")
        mapped_keys = {
            (row["entity_instance_id"], row["auth_asym_id"], row["auth_seq_id"], row["insertion_code"], row["sequence_index"])
            for row in structure_map["rows"]
        }
        landscape_keys = {
            (row["entity_instance_id"], row["auth_asym_id"], row["auth_seq_id"], row["insertion_code"], row["sequence_index"])
            for row in landscape["residues"]
        }
        if not landscape_keys.issubset(mapped_keys):
            raise ContractValidationError("landscape residues are not bound to structure-map rows")
    if ensemble and analysis and analysis["source_ensemble_sha256"] != canonical_sha256(ensemble):
        raise ContractValidationError("analysis source ensemble hash mismatch")
    if ensemble and handoff and handoff["source_ensemble_sha256"] != canonical_sha256(ensemble):
        raise ContractValidationError("handoff source ensemble hash mismatch")
    if snapshot and handoff and handoff["source_complex_sha256"] != canonical_sha256(snapshot):
        raise ContractValidationError("handoff source complex hash mismatch")
    if structure_map and handoff and handoff["source_structure_map_sha256"] != canonical_sha256(structure_map):
        raise ContractValidationError("handoff source structure-map hash mismatch")
    if analysis and handoff and handoff["source_analysis_sha256"] != canonical_sha256(analysis):
        raise ContractValidationError("handoff source analysis hash mismatch")
    if resume_descriptor is not None:
        descriptor = ResumeDescriptor.model_validate(resume_descriptor)
        if request and descriptor.request_sha256 != request["request_sha256"]:
            raise ContractValidationError("resume descriptor request hash mismatch")
        if snapshot and descriptor.complex_snapshot_sha256 != canonical_sha256(snapshot):
            raise ContractValidationError("resume descriptor complex snapshot hash mismatch")
        if ensemble and descriptor.resume_key != ensemble["resume_key"]:
            raise ContractValidationError("ensemble resume key mismatch")


def validate_structure_map_snapshot_binding(
    structure_map: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> None:
    if structure_map["target_id"] != snapshot["target_id"]:
        raise ContractValidationError("structure map target does not match complex snapshot")
    entities = {entity["source_entity_id"]: entity for entity in snapshot["entities"]}
    authorized: dict[tuple[str, str, str], tuple[str, tuple[str, ...]]] = {}
    for mapping in snapshot["instance_mappings"]:
        if mapping["candidate_id"] != structure_map["candidate_id"]:
            continue
        entity = entities[mapping["source_entity_id"]]
        if entity["entity_type"] != "protein":
            continue
        key = (
            mapping["source_entity_id"], mapping["output_label_asym_id"],
            mapping["output_auth_asym_id"],
        )
        residue_names = [_ONE_TO_THREE[letter] for letter in entity["sequence"]]
        for modification in entity.get("modifications", []):
            residue_names[modification["position"] - 1] = modification["modification"]
        authorized[key] = (mapping["source_instance_id"], tuple(residue_names))
    if not authorized:
        raise ContractValidationError("complex snapshot authorizes no protein instances for candidate")
    seen_positions: dict[str, set[int]] = {}
    seen_author_identities: dict[str, set[tuple[int, str]]] = {}
    for row in structure_map["rows"]:
        key = (row["source_entity_id"], row["label_asym_id"], row["auth_asym_id"])
        context = authorized.get(key)
        if context is None or row["entity_instance_id"] != context[0]:
            raise ContractValidationError("structure-map row is not bound to authoritative instance mapping")
        if row["label_seq_id"] != row["sequence_index"]:
            raise ContractValidationError(
                "structure-map label_seq_id does not match authoritative sequence position"
            )
        if row["sequence_index"] > len(context[1]):
            raise ContractValidationError("structure-map sequence index exceeds authoritative sequence")
        expected_residue_name = context[1][row["sequence_index"] - 1]
        if row["residue_name"] != expected_residue_name:
            raise ContractValidationError(
                "structure-map authoritative residue identity does not match source sequence"
            )
        positions = seen_positions.setdefault(context[0], set())
        if row["sequence_index"] in positions:
            raise ContractValidationError(
                "structure-map sequence position is duplicated within an authoritative instance"
            )
        positions.add(row["sequence_index"])
        author_identity = (row["auth_seq_id"], row["insertion_code"])
        author_identities = seen_author_identities.setdefault(context[0], set())
        if author_identity in author_identities:
            raise ContractValidationError(
                "structure-map author residue identity is duplicated within an authoritative instance"
            )
        author_identities.add(author_identity)
    expected_by_instance = {
        instance_id: set(range(1, len(residue_names) + 1))
        for instance_id, residue_names in authorized.values()
    }
    if seen_positions != expected_by_instance:
        missing = {
            instance_id: sorted(expected - seen_positions.get(instance_id, set()))
            for instance_id, expected in expected_by_instance.items()
            if expected != seen_positions.get(instance_id, set())
        }
        raise ContractValidationError(
            f"structure map omits authoritative sequence positions: {missing}"
        )


_LANDSCAPE_STATUSES = {
    "ok",
    "unscoreable_residue",
    "missing_row",
    "duplicate_row",
    "malformed_row",
    "nonfinite_score",
    "mapping_failed",
    "conformer_missing",
}


def validate_landscape_slots(wt: str, slots: Sequence[Mapping[str, Any]]) -> None:
    if wt not in AA_ORDER:
        raise ContractValidationError("WT residue is not canonical")
    mutations = [slot.get("mutation_aa") for slot in slots]
    if len(slots) != 20 or "".join(str(value) for value in mutations) != AA_ORDER:
        raise ContractValidationError("landscape must contain the exact 20 canonical ordered slots")
    if sum(bool(slot.get("native")) for slot in slots) != 1:
        raise ContractValidationError("landscape must contain exactly one native=true slot")
    for slot in slots:
        mutation = slot["mutation_aa"]
        if slot.get("wt") != wt:
            raise ContractValidationError("landscape slot WT does not match residue WT")
        if bool(slot.get("native")) != (mutation == wt):
            raise ContractValidationError("landscape native flag does not match WT substitution")
        status = slot.get("status")
        if status not in _LANDSCAPE_STATUSES:
            raise ContractValidationError("landscape slot status is invalid")
        score = slot.get("score")
        if status == "ok":
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                raise ContractValidationError("ok landscape slot requires a finite score")
            canonical_class = canonical_frustrampnn_score_class(float(score))
            expected_class = (
                "minimally_frustrated" if canonical_class == "minimal" else canonical_class
            )
            if slot.get("class") != expected_class:
                raise ContractValidationError("landscape class disagrees with threshold policy")
            if slot.get("scoreable") is not True or slot.get("reason") is not None:
                raise ContractValidationError("ok landscape slot scoreable/reason semantics are invalid")
        elif (
            score is not None
            or slot.get("class") is not None
            or slot.get("scoreable") is not False
            or not isinstance(slot.get("reason"), str)
            or not slot.get("reason")
        ):
            raise ContractValidationError("non-ok landscape slot missingness semantics are invalid")


@dataclass(frozen=True)
class HierarchicalSummary:
    mean: float
    stratum_means: dict[str, float]
    outer_support_fraction: float
    coordinate_support_fraction: float
    valid_strata: int
    expected_strata: int
    valid_coordinates: int
    expected_coordinates: int


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ContractValidationError(f"{field} must be finite")
    return float(value)


def hierarchical_aggregate(
    values_by_stratum: Mapping[str, Sequence[Any]], *, expected_inner_counts: Mapping[str, int]
) -> HierarchicalSummary:
    if not expected_inner_counts or any(isinstance(count, bool) or not isinstance(count, int) or count <= 0 for count in expected_inner_counts.values()):
        raise ContractValidationError("expected hierarchical coordinates must be nonempty")
    if set(values_by_stratum) - set(expected_inner_counts):
        raise ContractValidationError("values contain unexpected strata")
    stratum_means: dict[str, float] = {}
    valid_coordinates = 0
    for stratum, expected_count in expected_inner_counts.items():
        raw_values = values_by_stratum.get(stratum, [])
        if len(raw_values) > expected_count:
            raise ContractValidationError(f"stratum {stratum} has extra coordinates")
        valid = [_finite_number(value, f"{stratum} value") for value in raw_values if value is not None]
        valid_coordinates += len(valid)
        if valid:
            stratum_means[stratum] = sum(valid) / len(valid)
    if not stratum_means:
        raise ContractValidationError("insufficient_support: no valid strata")
    expected_coordinates = sum(expected_inner_counts.values())
    return HierarchicalSummary(
        mean=sum(stratum_means.values()) / len(stratum_means),
        stratum_means=stratum_means,
        outer_support_fraction=len(stratum_means) / len(expected_inner_counts),
        coordinate_support_fraction=valid_coordinates / expected_coordinates,
        valid_strata=len(stratum_means),
        expected_strata=len(expected_inner_counts),
        valid_coordinates=valid_coordinates,
        expected_coordinates=expected_coordinates,
    )


def substitution_difference(mutant_score: Any, native_score: Any) -> float:
    return _finite_number(mutant_score, "mutant score") - _finite_number(native_score, "native score")


def context_difference(state_b_score: Any, state_a_score: Any) -> float:
    return _finite_number(state_b_score, "state B score") - _finite_number(state_a_score, "state A score")


def hotspot_score(coordinate_support_fraction: Any, hierarchical_abs_d_sub_mean: Any) -> float:
    support = _finite_number(coordinate_support_fraction, "coordinate support")
    mean = _finite_number(hierarchical_abs_d_sub_mean, "hierarchical absolute D_sub mean")
    if not 0 <= support <= 1 or mean < 0:
        raise ContractValidationError("hotspot inputs are outside their domains")
    return support * mean


def switch_score(coordinate_support_fraction: Any, context_transition_rate: Any, hierarchical_d_ctx_mean: Any) -> float:
    support = _finite_number(coordinate_support_fraction, "coordinate support")
    transition = _finite_number(context_transition_rate, "context transition rate")
    mean = _finite_number(hierarchical_d_ctx_mean, "hierarchical D_ctx mean")
    if not 0 <= support <= 1 or not 0 <= transition <= 1:
        raise ContractValidationError("switch inputs are outside their domains")
    return support * transition * abs(mean)


class FeaturePolicy(_StrictModel):
    mode: Literal[
        "regenerate_mutated_protein_v1",
        "paired_regenerate_changed_protein_v1",
        "features_disabled_control_v1",
    ]
    per_entity_hashes: dict[
        str, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ] | None = None
    protein_msa_enabled: bool | None = None
    templates_enabled: bool | None = None
    rna_msa_enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_entity_hashes(cls, value: Any) -> Any:
        if (
            isinstance(value, Mapping)
            and "per_entity_hashes" in value
            and value["per_entity_hashes"] is None
        ):
            raise ValueError("per_entity_hashes must be an object when present")
        return value

    @model_validator(mode="after")
    def validate_disabled_control(self) -> "FeaturePolicy":
        if self.mode == "features_disabled_control_v1" and any(
            value is True for value in (
                self.protein_msa_enabled, self.templates_enabled, self.rna_msa_enabled
            )
        ):
            raise ValueError("feature-disabled control cannot enable MSA or templates")
        return self


def validate_feature_policy(value: Any) -> dict[str, Any]:
    return FeaturePolicy.model_validate(value).model_dump(mode="json", exclude_none=True)


def feature_policy_sha256(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return canonical_sha256(value)


def handoff_idempotency_key(source_digest: str, candidate_set: Sequence[Mapping[str, Any]], resampling_settings: Mapping[str, Any]) -> str:
    _require_sha256(source_digest, "source digest")
    material = source_digest.encode("ascii") + canonical_json_bytes(list(candidate_set)) + canonical_json_bytes(dict(resampling_settings))
    return hashlib.sha256(material).hexdigest()


def normalize_artifact_class_alias(value: Any) -> str:
    """Accept exactly the canonical spelling and the one frozen historical alias."""

    if value in ("monomer_conformation", "conformer"):
        return "monomer_conformation"
    raise ContractValidationError("unknown artifact class alias")


def _phase0_fixture_case(vector: Mapping[str, Any], repo_root: Path) -> Any:
    fixture_ref = vector["fixtures"][0]
    relative = fixture_ref["path"]
    _validate_relative_path(relative)
    fixture_path = repo_root / relative
    payload = canonical_json_loads(fixture_path.read_bytes())
    case_key = fixture_ref.get("case_key")
    if payload.get("case_key") == case_key:
        return payload
    for case in payload.get("cases", []):
        if case.get("case_key") == case_key:
            return case
    raise ContractValidationError(f"Phase 0 fixture case is missing: {case_key}")


def _validate_phase0_vector_material(vector: Mapping[str, Any], case: Mapping[str, Any]) -> None:
    family = vector["family"]
    if family in {"complex-positive", "complex-negative"}:
        validate_complex_case(case)
        return
    if family == "protenix-layout":
        seeds = validate_seed_sources(
            api=case["ordered_seeds"], generated_json=case["ordered_seeds"], cli=case["ordered_seeds"]
        )
        coordinates = [
            (candidate["seed"], sample)
            for candidate in case["candidates"]
            for sample in candidate["sample_indices"]
        ]
        expected = [(seed, sample) for seed in seeds for sample in range(case["samples_per_seed"])]
        if coordinates != expected or len(coordinates) != case["expected_cardinality"]:
            raise ContractValidationError("Protenix coordinate layout/cardinality mismatch")
        if tuple(case["mandatory_roles"]) != _CANDIDATE_ROLES["protenix_v2_ensemble"]:
            raise ContractValidationError("Protenix mandatory role contract mismatch")
        return
    if family == "protenix-composition":
        groups = [case["source_instances"], case["runtime_instances"], case["output_instances"]]
        if len({len(group) for group in groups}) != 1 or any(len(set(group)) != len(group) for group in groups):
            raise ContractValidationError("composition mapping is not cardinality-preserving")
        return
    if family == "confornets-layout":
        product = (
            len(case["runs"]) * len(case["saved_steps"]) *
            len(case["confornet_indices"]) * len(case["sample_indices"])
        )
        if product != case["expected_cardinality"] or case["task"] not in {"diversity", "mse", "transfer"}:
            raise ContractValidationError("ConforNets coordinate contract mismatch")
        return
    if family == "confornets-negative":
        if case.get("chain_count", 1) != 1:
            raise ContractValidationError("multi_chain")
        if case.get("molecule_types") != ["protein"]:
            raise ContractValidationError("non_protein")
        if case.get("reference_count", 0) > 2:
            raise ContractValidationError("too_many_references")
        if case.get("observed_coordinates") != case.get("expected_coordinates"):
            raise ContractValidationError("missing_coordinate")
        return
    if family == "defaults":
        setting = case["setting"]
        if setting == "ordered_seeds":
            validate_seed_sources(api=case["value"], generated_json=case["value"], cli=case["value"])
        elif setting == "samples_per_seed" and case["value"] < 1:
            raise ContractValidationError("samples_per_seed must be positive")
        elif setting == "use_default_params" and (
            case["value"] is not True or case["n_cycle"] is not None or case["n_step"] is not None
        ):
            raise ContractValidationError("default-parameter mode has manual overrides")
        elif setting == "manual_overrides" and (
            case["use_default_params"] is not False or case["n_cycle"] < 1 or case["n_step"] < 1
        ):
            raise ContractValidationError("manual override mode is malformed")
        elif setting in {"protein_msa", "templates", "rna_msa"} and case["value"] not in {"enabled", "disabled"}:
            raise ContractValidationError("feature default is malformed")
        return
    if family == "frustrampnn":
        case_key = case["case_key"]
        if case_key == "P0-FRUSTRAMPNN-001":
            if not case["checkpoint_id"] or case["runtime_status"] != "unmeasured":
                raise ContractValidationError("checkpoint provenance contract is malformed")
        elif case_key == "P0-FRUSTRAMPNN-002":
            if case["selected_chain"] in case["excluded_chains"]:
                raise ContractValidationError("selected-chain semantics are contradictory")
        elif case_key == "P0-FRUSTRAMPNN-003":
            slots = []
            wt = case["residue"]["wt"]
            for slot in case["residue"]["slots"]:
                score = slot["score"]
                slots.append({
                    **slot,
                    "wt": wt,
                    "native": slot["mutation_aa"] == wt,
                    "class": (
                        "minimally_frustrated"
                        if canonical_frustrampnn_score_class(float(score)) == "minimal"
                        else canonical_frustrampnn_score_class(float(score))
                    ),
                    "scoreable": True,
                    "reason": None,
                })
            validate_landscape_slots(wt, slots)
        else:
            for row in case["rows"]:
                score = row["score"]
                if row["mutation_aa"] not in AA_ORDER or (
                    score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score))
                ):
                    raise ContractValidationError("malformed/nonfinite FrustraMPNN row")
            raise ContractValidationError("negative FrustraMPNN vector did not contain malformed data")
        return
    if family == "normalize":
        features = case["features"]
        if (
            len(features["insertion_code"]) != 1
            or len(features["instance_ids"]) != len(set(features["instance_ids"]))
            or features["selected_altloc"] not in features["altlocs"]
            or features["selected_model"] not in features["models"]
        ):
            raise ContractValidationError("normalization identity vector is malformed")
        return
    if family == "usalign":
        match = re.search(
            r"Aligned length=\s*(\d+), RMSD=\s*([0-9.]+), .*?=\s*([0-9.]+)",
            case["parser_input"],
        )
        if not match:
            raise ContractValidationError("USAlign parser fixture is malformed")
        observed = {
            "aligned_length": int(match.group(1)),
            "rmsd": float(match.group(2)),
            "sequence_identity": float(match.group(3)),
        }
        if observed != case["expected_parse"]:
            raise ContractValidationError("USAlign expected parse mismatch")
        return
    raise ContractValidationError(f"unsupported Phase 0 contract family: {family}")


def validate_phase0_contract_vector(
    vector: Mapping[str, Any], *, repo_root: Path | str
) -> Literal["accept_contract", "reject_contract"] | None:
    """Execute contract-only disposition for each frozen Phase 0 vector.

    The immutable broad baseline vector is deliberately non-applicable because
    it records commands/results rather than a schema or data-contract case.
    """

    if vector["family"] == "baseline":
        return None
    case = _phase0_fixture_case(vector, Path(repo_root))
    try:
        _validate_phase0_vector_material(vector, case)
    except (ContractValidationError, KeyError, TypeError, ValueError):
        return "reject_contract"
    return "accept_contract"
