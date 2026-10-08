#!/usr/bin/env python3
"""Deterministic, standard-library-only validator for Phase 0 contract vectors.

This validates documentation/registry/fixture integrity only.  It never executes a
runtime probe and never promotes a vector from ``unmeasured``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NoReturn, Sequence, Tuple


AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
EXPECTED_SEEDS = [101, 202, 303, 404, 505]
FIXTURE_ROOT = Path("platform/api/tests/fixtures/conformational_mapping/phase_0_vectors")
DEFINITIONS_PATH = Path("docs/specs/conformational_mapping/cm_contract_definitions_v1.md")
VECTORS_PATH = Path("docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json")
VALIDATION_COMMAND = (
    "python scripts/probes/conformational_mapping/validate_phase0_vectors.py "
    "--definitions docs/specs/conformational_mapping/cm_contract_definitions_v1.md "
    "--vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json"
)
EVIDENCE_FILES = [
    "command.json",
    "input_hashes.json",
    "output_hashes.json",
    "artifact_tree.json",
    "runtime_identity.json",
    "resources.json",
    "exit_status.json",
    "disposition.json",
]
BASELINE_COMMANDS = [
    "PYTHONPATH=platform/api python -m pytest -q \\\n  platform/api/tests/test_confornets_experimental.py \\\n  platform/api/tests/test_experimental_nextflow_entrypoint.py",
    "PYTHONPATH=platform/api python -m pytest -q \\\n  platform/api/tests/test_confornets_result_ingester.py \\\n  platform/api/tests/test_result_contracts.py \\\n  platform/api/tests/test_nextflow_entrypoint_registry.py",
    "PYTHONPATH=platform/api python -m pytest -q platform/api/tests",
    "pnpm --dir platform/frontend test",
]
TOP_FIELDS = {
    "schema_name",
    "schema_version",
    "definitions",
    "canonicalization",
    "required_validation_command",
    "evidence_root_template",
    "runtime_status_policy",
    "vectors",
    "registry_sha256",
}
DEFINITION_FIELDS = {"path", "sha256", "binding_strategy"}
VECTOR_FIELDS = {
    "id",
    "family",
    "classification",
    "description",
    "expected_disposition",
    "runtime_status",
    "fixtures",
    "probe_command",
    "probe_vector_id",
    "evidence_subdirectory",
    "evidence_requirements",
}
FIXTURE_REF_FIELDS = {"path", "sha256", "role", "case_key"}
CLASSIFICATIONS = {"[UG]", "[LO]", "[BP]"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """A deterministic contract-validation failure."""


def fail(message: str) -> NoReturn:
    raise ContractError(message)


def no_duplicate_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    fail(f"non-finite JSON number forbidden: {value}")


def load_json_bytes(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{label}: not UTF-8: {exc}")
    try:
        return json.loads(
            text,
            object_pairs_hook=no_duplicate_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ContractError) as exc:
        fail(f"{label}: invalid strict JSON: {exc}")


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        fail(f"cannot canonicalize JSON value: {exc}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_no_symlink(root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail(f"{label}: path escapes containment root")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail(f"{label}: symlink forbidden: {current}")


def expected_ids() -> List[str]:
    ids: List[str] = []
    ids.extend(f"P0-VECTOR-COMPLEX-{i:03d}" for i in range(1, 13))
    ids.extend(f"P0-VECTOR-COMPLEX-NEG-{i:03d}" for i in range(1, 11))
    ids.append("P0-PROTENIX-LAYOUT-001")
    ids.extend(f"P0-PROTENIX-COMPOSITION-{i:03d}" for i in range(1, 10))
    ids.extend(f"P0-CONFORNETS-LAYOUT-{i:03d}" for i in range(1, 4))
    ids.extend(f"P0-CONFORNETS-NEG-{i:03d}" for i in range(1, 5))
    ids.extend(f"P0-DEFAULTS-{i:03d}" for i in range(1, 8))
    ids.extend(f"P0-FRUSTRAMPNN-{i:03d}" for i in range(1, 5))
    ids.extend(["P0-NORMALIZE-001", "P0-USALIGN-001", "P0-BASELINE-001"])
    return ids


EXPECTED_FAMILY = {
    **{f"P0-VECTOR-COMPLEX-{i:03d}": "complex-positive" for i in range(1, 13)},
    **{f"P0-VECTOR-COMPLEX-NEG-{i:03d}": "complex-negative" for i in range(1, 11)},
    "P0-PROTENIX-LAYOUT-001": "protenix-layout",
    **{f"P0-PROTENIX-COMPOSITION-{i:03d}": "protenix-composition" for i in range(1, 10)},
    **{f"P0-CONFORNETS-LAYOUT-{i:03d}": "confornets-layout" for i in range(1, 4)},
    **{f"P0-CONFORNETS-NEG-{i:03d}": "confornets-negative" for i in range(1, 5)},
    **{f"P0-DEFAULTS-{i:03d}": "defaults" for i in range(1, 8)},
    **{f"P0-FRUSTRAMPNN-{i:03d}": "frustrampnn" for i in range(1, 5)},
    "P0-NORMALIZE-001": "normalize",
    "P0-USALIGN-001": "usalign",
    "P0-BASELINE-001": "baseline",
}
NEGATIVE_IDS = {
    *(f"P0-VECTOR-COMPLEX-NEG-{i:03d}" for i in range(1, 11)),
    *(f"P0-CONFORNETS-NEG-{i:03d}" for i in range(1, 5)),
    "P0-FRUSTRAMPNN-004",
}


def require_exact_fields(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label}: expected object")
    actual = set(value)
    if actual != expected:
        fail(
            f"{label}: fields differ; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label}: expected nonempty string")
    return value


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        fail(f"{label}: expected lowercase SHA-256")
    return value


def contained_lexically(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def checked_repo_path(repo_root: Path, relative: Any, label: str) -> Path:
    text = require_nonempty_string(relative, label)
    candidate_rel = Path(text)
    if candidate_rel.is_absolute() or ".." in candidate_rel.parts:
        fail(f"{label}: must be a contained repository-relative path")
    candidate = (repo_root / candidate_rel).absolute()
    if not contained_lexically(candidate, repo_root):
        fail(f"{label}: escapes repository")
    current = repo_root
    for part in candidate_rel.parts:
        current = current / part
        if current.is_symlink():
            fail(f"{label}: symlink forbidden: {current}")
    if not candidate.exists() or not candidate.is_file():
        fail(f"{label}: regular file does not exist: {candidate_rel}")
    resolved = candidate.resolve(strict=True)
    if not contained_lexically(resolved, repo_root):
        fail(f"{label}: resolved path escapes repository")
    return candidate


def checked_argument_path(repo_root: Path, argument: Path, expected: Path, label: str) -> Path:
    lexical = argument if argument.is_absolute() else Path.cwd() / argument
    lexical = lexical.absolute()
    expected_abs = (repo_root / expected).absolute()
    if lexical != expected_abs:
        fail(f"{label}: must name exact path {expected.as_posix()}")
    return checked_repo_path(repo_root, expected.as_posix(), label)


def load_fixture_cases(document: Any, label: str) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(document, dict):
        fail(f"{label}: fixture document must be an object")
    if document.get("schema_version") != 1 or not isinstance(document.get("schema_name"), str):
        fail(f"{label}: fixture schema_name/string and schema_version=1 required")
    raw_cases: Any
    if "cases" in document:
        raw_cases = document["cases"]
        if not isinstance(raw_cases, list):
            fail(f"{label}.cases: expected array")
    elif "case_key" in document:
        raw_cases = [document]
    else:
        fail(f"{label}: fixture must have cases or case_key")
    cases: Dict[str, Mapping[str, Any]] = {}
    for index, case in enumerate(raw_cases):
        if not isinstance(case, dict):
            fail(f"{label}.cases[{index}]: expected object")
        key = require_nonempty_string(case.get("case_key"), f"{label}.cases[{index}].case_key")
        if key in cases:
            fail(f"{label}: duplicate case_key {key}")
        cases[key] = case
    return cases


def validate_registry_structure(registry: Any) -> List[Mapping[str, Any]]:
    top = require_exact_fields(registry, TOP_FIELDS, "registry")
    if top["schema_name"] != "cm_contract_test_vectors" or top["schema_version"] != 1:
        fail("registry: schema_name/version must be cm_contract_test_vectors/1")
    definitions = require_exact_fields(top["definitions"], DEFINITION_FIELDS, "definitions")
    if definitions["path"] != DEFINITIONS_PATH.as_posix():
        fail("definitions.path: unexpected path")
    if definitions["binding_strategy"] != "sha256-exact-file-bytes":
        fail("definitions.binding_strategy: unexpected value")
    require_sha256(definitions["sha256"], "definitions.sha256")
    if top["required_validation_command"] != VALIDATION_COMMAND:
        fail("required_validation_command: exact command drift")
    if top["evidence_root_template"] != "/mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>":
        fail("evidence_root_template: unexpected value")
    if "unmeasured" not in require_nonempty_string(top["runtime_status_policy"], "runtime_status_policy"):
        fail("runtime_status_policy must preserve unmeasured state")
    require_nonempty_string(top["canonicalization"], "canonicalization")
    require_sha256(top["registry_sha256"], "registry_sha256")
    if not isinstance(top["vectors"], list):
        fail("vectors: expected array")
    vectors: List[Mapping[str, Any]] = []
    ids: List[str] = []
    for index, raw in enumerate(top["vectors"]):
        vector = require_exact_fields(raw, VECTOR_FIELDS, f"vectors[{index}]")
        vector_id = require_nonempty_string(vector["id"], f"vectors[{index}].id")
        ids.append(vector_id)
        expected_family = EXPECTED_FAMILY.get(vector_id)
        if vector["family"] != expected_family:
            fail(f"{vector_id}: family must be {expected_family!r}")
        classes = vector["classification"]
        if not isinstance(classes, list) or not classes or len(classes) != len(set(classes)):
            fail(f"{vector_id}: classification must be a nonempty unique array")
        if any(item not in CLASSIFICATIONS for item in classes):
            fail(f"{vector_id}: invalid classification")
        require_nonempty_string(vector["description"], f"{vector_id}.description")
        expected_disposition = "reject_contract" if vector_id in NEGATIVE_IDS else "accept_contract"
        if vector["expected_disposition"] != expected_disposition:
            fail(f"{vector_id}: expected_disposition must be {expected_disposition}")
        if vector["runtime_status"] != "unmeasured":
            fail(f"{vector_id}: runtime_status must remain unmeasured before evidence")
        expected_probe = (
            "python scripts/probes/conformational_mapping/probe_phase0_runtime.py "
            f"--vectors {VECTORS_PATH.as_posix()} "
            "--output /mnt/BioModStack/bms_results/conformational_mapping_phase0/"
            "<approved_run_id>"
        )
        if vector["probe_command"] != expected_probe:
            fail(f"{vector_id}: exact probe command drift")
        if vector["probe_vector_id"] != vector_id or vector["evidence_subdirectory"] != vector_id:
            fail(f"{vector_id}: probe/evidence vector selector drift")
        if vector["evidence_requirements"] != EVIDENCE_FILES:
            fail(f"{vector_id}: exact evidence requirements drift")
        fixtures = vector["fixtures"]
        if not isinstance(fixtures, list) or not fixtures:
            fail(f"{vector_id}.fixtures: nonempty array required")
        for fixture_index, raw_ref in enumerate(fixtures):
            ref = require_exact_fields(
                raw_ref, FIXTURE_REF_FIELDS, f"{vector_id}.fixtures[{fixture_index}]"
            )
            path = require_nonempty_string(ref["path"], f"{vector_id}.fixture.path")
            rel = Path(path)
            if rel.is_absolute() or ".." in rel.parts or not rel.is_relative_to(FIXTURE_ROOT):
                fail(f"{vector_id}: fixture path must be contained under {FIXTURE_ROOT}")
            require_sha256(ref["sha256"], f"{vector_id}.fixture.sha256")
            require_nonempty_string(ref["role"], f"{vector_id}.fixture.role")
            if ref["case_key"] != vector_id:
                fail(f"{vector_id}: fixture case_key must equal vector ID")
        vectors.append(vector)
    expected = expected_ids()
    if len(expected) != 53:
        fail("internal required-ID list is not 53")
    if ids != expected:
        missing = sorted(set(expected) - set(ids))
        extra = sorted(set(ids) - set(expected))
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        fail(
            "vectors must contain each required ID exactly once in canonical order; "
            f"missing={missing}, extra={extra}, duplicates={duplicates}"
        )
    return vectors


def validate_complex_cases(cases: Mapping[str, Mapping[str, Any]]) -> None:
    allowed_entity_fields = {
        "entity_type", "source_entity_id", "count", "ordered_instance_ids", "sequence",
        "ccd", "smiles", "modifications",
    }
    supported_types = {"protein", "dna", "rna", "ligand_ccd", "ligand_smiles", "ion"}
    for number in range(1, 13):
        key = f"P0-VECTOR-COMPLEX-{number:03d}"
        case = cases[key]
        if case.get("kind") != "positive":
            fail(f"{key}: kind must be positive")
        entities = case.get("entities")
        if not isinstance(entities, list) or not entities:
            fail(f"{key}: positive entities required")
        source_ids: List[str] = []
        all_instances: List[str] = []
        for entity in entities:
            if not isinstance(entity, dict) or set(entity) - allowed_entity_fields:
                fail(f"{key}: unsupported positive entity field")
            if entity.get("entity_type") not in supported_types:
                fail(f"{key}: unsupported positive entity type")
            source_id = require_nonempty_string(entity.get("source_entity_id"), f"{key}.source_entity_id")
            source_ids.append(source_id)
            count = entity.get("count")
            instance_ids = entity.get("ordered_instance_ids")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                fail(f"{key}: count must be positive integer")
            if not isinstance(instance_ids, list) or len(instance_ids) != count:
                fail(f"{key}: count/instance cardinality mismatch")
            if any(not isinstance(item, str) or not item for item in instance_ids):
                fail(f"{key}: instance IDs must be nonempty strings")
            all_instances.extend(instance_ids)
            etype = entity["entity_type"]
            if etype == "protein" and not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", entity.get("sequence", "")):
                fail(f"{key}: malformed protein sequence")
            if etype == "dna" and not re.fullmatch(r"[ACGT]+", entity.get("sequence", "")):
                fail(f"{key}: malformed DNA sequence")
            if etype == "rna" and not re.fullmatch(r"[ACGU]+", entity.get("sequence", "")):
                fail(f"{key}: malformed RNA sequence")
        if len(source_ids) != len(set(source_ids)):
            fail(f"{key}: duplicate source entity ID")
        if len(all_instances) != len(set(all_instances)):
            fail(f"{key}: duplicate instance ID")
        for bond in case.get("bonds", []):
            if not isinstance(bond, dict):
                fail(f"{key}: malformed bond")
            for side in ("left", "right"):
                endpoint = bond.get(side)
                if not isinstance(endpoint, dict) or endpoint.get("instance_id") not in all_instances:
                    fail(f"{key}: dangling bond reference")
                if isinstance(endpoint.get("position"), bool) or not isinstance(endpoint.get("position"), int) or endpoint["position"] < 1:
                    fail(f"{key}: bond positions are 1-based positive integers")
    repeated_protein = cases["P0-VECTOR-COMPLEX-009"]["entities"][0]
    if repeated_protein["count"] != 2 or len(set(repeated_protein["ordered_instance_ids"])) != 2:
        fail("P0-VECTOR-COMPLEX-009: repeated protein semantics missing")
    repeated_ligand = cases["P0-VECTOR-COMPLEX-010"]["entities"][0]
    if repeated_ligand["count"] != 2 or len(set(repeated_ligand["ordered_instance_ids"])) != 2:
        fail("P0-VECTOR-COMPLEX-010: repeated ligand semantics missing")
    mixed_distinct = cases["P0-VECTOR-COMPLEX-011"]["entities"]
    if len(mixed_distinct) != 2 or mixed_distinct[0]["sequence"] != mixed_distinct[1]["sequence"] or mixed_distinct[0]["source_entity_id"] == mixed_distinct[1]["source_entity_id"]:
        fail("P0-VECTOR-COMPLEX-011: identical sequence/distinct identity semantics missing")
    full = cases["P0-VECTOR-COMPLEX-012"]
    total = sum(entity["count"] for entity in full["entities"])
    if total != full.get("expected_instance_count") or total != 8:
        fail("P0-VECTOR-COMPLEX-012: full-complex instance cardinality must be 8")
    if full.get("repeated_semantics") != {"protein": ["p1", "p2"], "ligand": ["lc1", "lc2"], "distinct": True}:
        fail("P0-VECTOR-COMPLEX-012: full-complex repeated-copy semantics drift")
    expected_reasons = [
        "count_instance_cardinality", "duplicate_instance_id", "unsupported_entity",
        "unsupported_field", "unsupported_modification", "unsupported_bond",
        "dangling_bond_reference", "lossy_conversion_or_token_limit",
        "malformed_sequence", "ambiguous_ordering",
    ]
    observed_reasons: List[str] = []
    for number in range(1, 11):
        key = f"P0-VECTOR-COMPLEX-NEG-{number:03d}"
        case = cases[key]
        if case.get("kind") != "negative":
            fail(f"{key}: kind must be negative")
        observed_reasons.append(case.get("reason"))
    if observed_reasons != expected_reasons or len(set(observed_reasons)) != 10:
        fail("complex negative reasons must cover ten deterministic unique cases")
    neg8 = cases["P0-VECTOR-COMPLEX-NEG-008"].get("admission", {})
    if not (neg8.get("token_count", 0) > neg8.get("token_limit", 0) and neg8.get("conversion_omissions")):
        fail("P0-VECTOR-COMPLEX-NEG-008: must cover both lossy conversion and token limit")


def validate_layout_and_defaults(documents: Mapping[str, Any]) -> None:
    layout = documents["protenix_layout.json"]
    seeds = layout.get("ordered_seeds")
    if seeds != EXPECTED_SEEDS or len(set(seeds)) != 5:
        fail("P0-PROTENIX-LAYOUT-001: exact five ordered unique seeds required")
    if any(isinstance(seed, bool) or not isinstance(seed, int) or not INT32_MIN <= seed <= INT32_MAX for seed in seeds):
        fail("P0-PROTENIX-LAYOUT-001: seeds must be signed int32")
    if layout.get("samples_per_seed") != 5 or layout.get("expected_cardinality") != 25:
        fail("P0-PROTENIX-LAYOUT-001: exact five samples and cardinality 25 required")
    candidates = layout.get("candidates")
    if not isinstance(candidates, list) or [item.get("seed") for item in candidates] != seeds:
        fail("P0-PROTENIX-LAYOUT-001: candidate seed order drift")
    if any(item.get("sample_indices") != [0, 1, 2, 3, 4] for item in candidates):
        fail("P0-PROTENIX-LAYOUT-001: sample indices drift")
    if layout.get("mandatory_roles") != ["authoritative_cif", "confidence_json", "full_data_json"]:
        fail("P0-PROTENIX-LAYOUT-001: mandatory sidecars drift")

    defaults_cases = load_fixture_cases(documents["defaults.json"], "defaults.json")
    default_seeds = defaults_cases["P0-DEFAULTS-001"].get("value")
    if default_seeds != EXPECTED_SEEDS or len(set(default_seeds)) != 5:
        fail("P0-DEFAULTS-001: five ordered unique seeds required")
    if any(isinstance(seed, bool) or not isinstance(seed, int) or not INT32_MIN <= seed <= INT32_MAX for seed in default_seeds):
        fail("P0-DEFAULTS-001: seeds must be signed int32")
    if defaults_cases["P0-DEFAULTS-002"].get("value") != 5:
        fail("P0-DEFAULTS-002: five samples required")
    default_mode = defaults_cases["P0-DEFAULTS-003"]
    if default_mode.get("value") is not True or default_mode.get("n_cycle") is not None or default_mode.get("n_step") is not None:
        fail("P0-DEFAULTS-003: default mode must omit manual overrides")
    manual_mode = defaults_cases["P0-DEFAULTS-004"]
    if manual_mode.get("use_default_params") is not False or manual_mode.get("n_cycle") != 10 or manual_mode.get("n_step") != 200:
        fail("P0-DEFAULTS-004: manual mode requires defaults false and 10/200")
    for key, case in defaults_cases.items():
        if case.get("approval") != "pending" or case.get("runtime_status") != "unmeasured":
            fail(f"{key}: defaults remain pending/unmeasured")


def validate_composition_and_confornets(documents: Mapping[str, Any]) -> None:
    composition = load_fixture_cases(documents["protenix_composition.json"], "protenix_composition.json")
    for number in range(1, 10):
        key = f"P0-PROTENIX-COMPOSITION-{number:03d}"
        case = composition[key]
        source = case.get("source_instances")
        runtime = case.get("runtime_instances")
        output = case.get("output_instances")
        if not all(isinstance(items, list) and items for items in (source, runtime, output)):
            fail(f"{key}: all mapping stages required")
        if not (len(source) == len(runtime) == len(output)):
            fail(f"{key}: mapping cardinality mismatch")
        if any(len(items) != len(set(items)) for items in (source, runtime, output)):
            fail(f"{key}: mapping identity collapse")
    confornets = load_fixture_cases(documents["confornets_cases.json"], "confornets_cases.json")
    for number in range(1, 4):
        key = f"P0-CONFORNETS-LAYOUT-{number:03d}"
        case = confornets[key]
        dimensions = [case.get("runs"), case.get("saved_steps"), case.get("confornet_indices"), case.get("sample_indices")]
        if any(not isinstance(items, list) or not items or len(items) != len(set(items)) for items in dimensions):
            fail(f"{key}: coordinate dimensions must be nonempty and unique")
        cardinality = math.prod(len(items) for items in dimensions)
        if case.get("expected_cardinality") != cardinality:
            fail(f"{key}: ConforNets coordinate cardinality mismatch")
        for required in ("task", "test_case_id", "reference_id", "target_id"):
            if required not in case:
                fail(f"{key}: missing mandatory coordinate field {required}")
    reasons = [confornets[f"P0-CONFORNETS-NEG-{i:03d}"].get("reason") for i in range(1, 5)]
    if reasons != ["multi_chain", "non_protein", "too_many_references", "missing_coordinate"]:
        fail("ConforNets negative semantics drift")


def validate_frustration_normalize_usalign_baseline(documents: Mapping[str, Any]) -> None:
    frustration = documents["frustrampnn.json"]
    if frustration.get("aa_order") != AA_ORDER:
        fail("FrustraMPNN canonical amino-acid order drift")
    cases = load_fixture_cases(frustration, "frustrampnn.json")
    slots = cases["P0-FRUSTRAMPNN-003"].get("residue", {}).get("slots")
    if not isinstance(slots, list) or len(slots) != 20:
        fail("P0-FRUSTRAMPNN-003: exactly 20 slots required")
    observed = "".join(slot.get("mutation_aa", "") for slot in slots if isinstance(slot, dict))
    if observed != AA_ORDER or len({slot.get("mutation_aa") for slot in slots}) != 20:
        fail("P0-FRUSTRAMPNN-003: exact ordered unique AA slots required")
    if cases["P0-FRUSTRAMPNN-001"].get("checkpoint_sha256") is not None:
        fail("P0-FRUSTRAMPNN-001: checkpoint hash cannot be claimed before evidence")
    negative = cases["P0-FRUSTRAMPNN-004"]
    if negative.get("kind") != "negative" or negative.get("expected") != "reject_contract":
        fail("P0-FRUSTRAMPNN-004: negative disposition drift")
    statuses = {row.get("status") for row in negative.get("rows", [])}
    if statuses != {"malformed_row", "nonfinite_score"}:
        fail("P0-FRUSTRAMPNN-004: malformed/nonfinite coverage required")

    normalize = documents["normalize.json"]
    features = normalize.get("features", {})
    expected_features = {"insertion_code", "label_asym_id", "auth_asym_id", "altlocs", "selected_altloc", "instance_ids", "models", "selected_model"}
    if set(features) != expected_features or len(set(features.get("instance_ids", []))) != 2 or len(features.get("models", [])) < 2:
        fail("P0-NORMALIZE-001: normalization feature coverage drift")

    usalign = documents["usalign.json"]
    if usalign.get("required_version") != "20240730" or usalign.get("executable_sha256") is not None:
        fail("P0-USALIGN-001: required version/unmeasured executable identity drift")
    pattern = re.compile(r"Aligned length=\s*(\d+), RMSD=\s*([0-9.]+), Seq_ID=n_identical/n_aligned=\s*([0-9.]+)")
    match = pattern.fullmatch(usalign.get("parser_input", ""))
    if match is None:
        fail("P0-USALIGN-001: parser input malformed")
    parsed = {"aligned_length": int(match.group(1)), "rmsd": float(match.group(2)), "sequence_identity": float(match.group(3))}
    if parsed != usalign.get("expected_parse"):
        fail("P0-USALIGN-001: deterministic parser expectation mismatch")

    baseline = documents["baseline.json"]
    if baseline.get("commands") != BASELINE_COMMANDS:
        fail("P0-BASELINE-001: four immutable command strings drift")
    if baseline.get("results") != [None, None, None, None] or baseline.get("runtime_status") != "unmeasured":
        fail("P0-BASELINE-001: no baseline success may be claimed before evidence")


def validate_no_evidence_success(value: Any, label: str = "fixture") -> None:
    success_keys = {"runtime_status", "evidence_status", "probe_status"}
    success_values = {"success", "passed", "pass", "measured_success", "complete", "verified"}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in success_keys and isinstance(child, str) and child.lower() in success_values:
                fail(f"{label}: evidence-success state forbidden before authenticated evidence")
            validate_no_evidence_success(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_no_evidence_success(child, f"{label}[{index}]")


def validate_semantics(documents: Mapping[str, Any]) -> None:
    complex_cases = load_fixture_cases(documents["complex_cases.json"], "complex_cases.json")
    validate_complex_cases(complex_cases)
    validate_layout_and_defaults(documents)
    validate_composition_and_confornets(documents)
    validate_frustration_normalize_usalign_baseline(documents)
    for name, document in documents.items():
        validate_no_evidence_success(document, name)


def validate_definitions(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"definitions: not UTF-8: {exc}")
    required_markers = [
        "not an executable schema",
        *(f"## 3.{number}" for number in range(1, 14)),
        "[UG]", "[LO]", "[BP]", AA_ORDER,
        "runtime_status=\"unmeasured\"",
        VALIDATION_COMMAND,
    ]
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        fail(f"definitions: missing required human-readable markers: {missing}")


def validate_all(repo_root: Path, definitions_arg: Path, vectors_arg: Path) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    definitions_path = checked_argument_path(repo_root, definitions_arg, DEFINITIONS_PATH, "--definitions")
    vectors_path = checked_argument_path(repo_root, vectors_arg, VECTORS_PATH, "--vectors")
    definitions_data = definitions_path.read_bytes()
    vectors_data = vectors_path.read_bytes()
    validate_definitions(definitions_data)
    registry = load_json_bytes(vectors_data, "vectors")
    vectors = validate_registry_structure(registry)
    if registry["definitions"]["sha256"] != sha256_bytes(definitions_data):
        fail("definitions.sha256: byte hash mismatch")
    unhashed = copy.deepcopy(registry)
    recorded_registry_hash = unhashed.pop("registry_sha256")
    calculated_registry_hash = sha256_bytes(canonical_bytes(unhashed))
    if recorded_registry_hash != calculated_registry_hash:
        fail(f"registry_sha256 mismatch: expected {calculated_registry_hash}")

    fixture_documents: Dict[str, Any] = {}
    fixture_case_indexes: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for vector in vectors:
        for ref in vector["fixtures"]:
            path_text = ref["path"]
            path = checked_repo_path(repo_root, path_text, f"{vector['id']}.fixture")
            data = path.read_bytes()
            if sha256_bytes(data) != ref["sha256"]:
                fail(f"{vector['id']}: fixture SHA-256 mismatch: {path_text}")
            basename = path.name
            if basename in fixture_documents and canonical_bytes(fixture_documents[basename]) != canonical_bytes(load_json_bytes(data, path_text)):
                fail(f"fixture basename collision: {basename}")
            document = fixture_documents.setdefault(basename, load_json_bytes(data, path_text))
            cases = fixture_case_indexes.setdefault(basename, load_fixture_cases(document, path_text))
            case = cases.get(ref["case_key"])
            if case is None:
                fail(f"{vector['id']}: fixture case_key not found")
            kind = case.get("kind")
            if kind is not None:
                expected_kind = "negative" if vector["expected_disposition"] == "reject_contract" else "positive"
                if kind != expected_kind:
                    fail(f"{vector['id']}: fixture kind/disposition mismatch")
    all_referenced_cases = {
        ref["case_key"] for vector in vectors for ref in vector["fixtures"]
    }
    fixture_cases = {
        key for index in fixture_case_indexes.values() for key in index
    }
    if fixture_cases != all_referenced_cases:
        fail(
            "fixture registry must be complete and have no unreferenced cases; "
            f"unreferenced={sorted(fixture_cases - all_referenced_cases)}, "
            f"missing={sorted(all_referenced_cases - fixture_cases)}"
        )
    validate_semantics(fixture_documents)
    return registry, fixture_documents


def checked_evidence_file(root: Path, relative_text: str, label: str) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        fail(f"{label}: expected contained evidence-relative path")
    path = root / relative
    try:
        path.relative_to(root)
    except ValueError:
        fail(f"{label}: evidence path escapes root")
    assert_no_symlink(root, path, label)
    if not path.is_file():
        fail(f"{label}: required regular file absent")
    return path


def verify_evidence(
    repo_root: Path,
    registry: Mapping[str, Any],
    evidence_root_arg: Path,
    hash_ledger_arg: Path,
) -> None:
    root = evidence_root_arg if evidence_root_arg.is_absolute() else Path.cwd() / evidence_root_arg
    root = root.absolute()
    assert_no_symlink(Path("/"), root, "--evidence-root")
    if not root.is_dir():
        fail("--evidence-root: directory does not exist")
    ledger_path = hash_ledger_arg if hash_ledger_arg.is_absolute() else Path.cwd() / hash_ledger_arg
    ledger_path = ledger_path.absolute()
    if ledger_path != root / "runtime_evidence_hashes.json":
        fail("--hash-ledger must be <evidence-root>/runtime_evidence_hashes.json")
    assert_no_symlink(root, ledger_path, "--hash-ledger")
    if not ledger_path.is_file():
        fail("--hash-ledger: regular file does not exist")

    ledger = require_exact_fields(
        load_json_bytes(ledger_path.read_bytes(), "hash-ledger"),
        {"schema_name", "schema_version", "registry_sha256", "definitions_sha256", "files"},
        "hash-ledger",
    )
    if ledger["schema_name"] != "cm_phase0_runtime_evidence_hashes" or ledger["schema_version"] != 1:
        fail("hash-ledger: schema identity/version mismatch")
    if ledger["registry_sha256"] != registry["registry_sha256"]:
        fail("hash-ledger: registry hash mismatch")
    if ledger["definitions_sha256"] != registry["definitions"]["sha256"]:
        fail("hash-ledger: definitions hash mismatch")
    if not isinstance(ledger["files"], list) or not ledger["files"]:
        fail("hash-ledger.files: nonempty array required")
    ledger_hashes: Dict[str, str] = {}
    for index, raw_entry in enumerate(ledger["files"]):
        entry = require_exact_fields(raw_entry, {"path", "sha256"}, f"hash-ledger.files[{index}]")
        relative = require_nonempty_string(entry["path"], f"hash-ledger.files[{index}].path")
        digest = require_sha256(entry["sha256"], f"hash-ledger.files[{index}].sha256")
        if relative in ledger_hashes:
            fail(f"hash-ledger: duplicate path {relative!r}")
        path = checked_evidence_file(root, relative, f"hash-ledger:{relative}")
        if sha256_file(path) != digest:
            fail(f"hash-ledger: SHA-256 mismatch for {relative}")
        ledger_hashes[relative] = digest

    actual_files = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"evidence-root: symlink forbidden: {path}")
        if path.is_file() and path != ledger_path:
            actual_files.add(path.relative_to(root).as_posix())
    if set(ledger_hashes) != actual_files:
        fail(
            "hash-ledger must exactly inventory evidence files; "
            f"unhashed={sorted(actual_files - set(ledger_hashes))}, "
            f"missing={sorted(set(ledger_hashes) - actual_files)}"
        )

    summary_path = checked_evidence_file(root, "runtime_evidence.json", "runtime-evidence")
    summary = require_exact_fields(
        load_json_bytes(summary_path.read_bytes(), "runtime-evidence"),
        {
            "schema_name", "schema_version", "run_id", "registry_sha256",
            "definitions_sha256", "vector_results",
        },
        "runtime-evidence",
    )
    if summary["schema_name"] != "cm_phase0_runtime_evidence" or summary["schema_version"] != 1:
        fail("runtime-evidence: schema identity/version mismatch")
    require_nonempty_string(summary["run_id"], "runtime-evidence.run_id")
    if summary["run_id"] != root.name:
        fail("runtime-evidence.run_id must equal the evidence-root basename")
    if summary["registry_sha256"] != registry["registry_sha256"]:
        fail("runtime-evidence: registry hash mismatch")
    if summary["definitions_sha256"] != registry["definitions"]["sha256"]:
        fail("runtime-evidence: definitions hash mismatch")
    raw_results = summary["vector_results"]
    if not isinstance(raw_results, list):
        fail("runtime-evidence.vector_results: array required")
    result_ids: List[str] = []
    allowed_observed = {"accept_contract", "reject_contract", "runtime_error", "unsupported"}
    for index, raw_result in enumerate(raw_results):
        result = require_exact_fields(
            raw_result,
            {"id", "runtime_status", "observed_disposition", "evidence_subdirectory"},
            f"runtime-evidence.vector_results[{index}]",
        )
        vector_id = require_nonempty_string(result["id"], f"vector_results[{index}].id")
        if result["runtime_status"] != "measured":
            fail(f"{vector_id}: evidence runtime_status must be measured")
        if result["observed_disposition"] not in allowed_observed:
            fail(f"{vector_id}: invalid observed_disposition")
        if result["evidence_subdirectory"] != vector_id:
            fail(f"{vector_id}: evidence_subdirectory must equal vector ID")
        result_ids.append(vector_id)
    if result_ids != expected_ids():
        fail("runtime-evidence must contain every required vector exactly once in registry order")

    expected_command = (
        "python scripts/probes/conformational_mapping/probe_phase0_runtime.py "
        f"--vectors {VECTORS_PATH.as_posix()} "
        "--output /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>"
    )
    summary_by_id = {item["id"]: item for item in raw_results}
    from probe_phase0_runtime import classify as rederive_vector_result
    vector_by_id = {item["id"]: item for item in registry["vectors"]}
    first_identity_path = root / expected_ids()[0] / "runtime_identity.json"
    semantic_identity = load_json_bytes(first_identity_path.read_bytes(), "semantic runtime identity")
    live_hash_cache: Dict[str, str] = {}
    for vector_id in expected_ids():
        for filename in EVIDENCE_FILES:
            relative = f"{vector_id}/{filename}"
            checked_evidence_file(root, relative, f"{vector_id}:{filename}")
            if relative not in ledger_hashes:
                fail(f"{vector_id}: required evidence file omitted from hash ledger: {filename}")
        command = require_exact_fields(
            load_json_bytes((root / vector_id / "command.json").read_bytes(), f"{vector_id}/command.json"),
            {"vector_id", "command"},
            f"{vector_id}/command.json",
        )
        if command != {"vector_id": vector_id, "command": expected_command}:
            fail(f"{vector_id}: command evidence does not match frozen probe command")
        disposition = require_exact_fields(
            load_json_bytes((root / vector_id / "disposition.json").read_bytes(), f"{vector_id}/disposition.json"),
            {"vector_id", "observed_disposition"},
            f"{vector_id}/disposition.json",
        )
        if disposition["vector_id"] != vector_id or disposition["observed_disposition"] not in allowed_observed:
            fail(f"{vector_id}: invalid disposition evidence")
        exit_status = require_exact_fields(
            load_json_bytes((root / vector_id / "exit_status.json").read_bytes(), f"{vector_id}/exit_status.json"),
            {"vector_id", "exit_code"},
            f"{vector_id}/exit_status.json",
        )
        if exit_status["vector_id"] != vector_id or isinstance(exit_status["exit_code"], bool) or not isinstance(exit_status["exit_code"], int):
            fail(f"{vector_id}: exit status must contain an integer exit_code")
        expected_disposition = "reject_contract" if vector_id in NEGATIVE_IDS else "accept_contract"
        if exit_status["exit_code"] != 0 or disposition["observed_disposition"] != expected_disposition:
            fail(f"{vector_id}: non-passing exit/disposition in authenticated bundle")
        if summary_by_id[vector_id]["observed_disposition"] != expected_disposition:
            fail(f"{vector_id}: summary disposition disagrees with per-vector evidence")
        resources_doc = load_json_bytes((root / vector_id / "resources.json").read_bytes(), f"{vector_id}/resources.json")
        result_doc = resources_doc.get("result") if isinstance(resources_doc, dict) else None
        registry_doc = resources_doc.get("registry_validation") if isinstance(resources_doc, dict) else None
        if not isinstance(result_doc, dict) or result_doc.get("gate_effect") != "PASS":
            fail(f"{vector_id}: substantive runtime gate is not PASS")
        if not isinstance(registry_doc, dict) or registry_doc.get("exit_code") != 0:
            fail(f"{vector_id}: embedded registry validation did not pass")
        tree_doc = load_json_bytes((root / vector_id / "artifact_tree.json").read_bytes(), f"{vector_id}/artifact_tree.json")
        hash_doc = load_json_bytes((root / vector_id / "output_hashes.json").read_bytes(), f"{vector_id}/output_hashes.json")
        observation_root = Path(tree_doc.get("observation_root", "")).resolve(strict=True)
        if observation_root != root.parent.resolve(strict=True):
            fail(f"{vector_id}: observation root is not the approved parent of the evidence bundle")
        tree_files = tree_doc.get("files")
        observation_hashes = hash_doc.get("observation_sha256") if isinstance(hash_doc, dict) else None
        if not isinstance(tree_files, list) or not tree_files or not isinstance(observation_hashes, dict) or not observation_hashes:
            fail(f"{vector_id}: observation artifact records are missing or malformed")
        tree_hashes = {row.get("path"): row.get("sha256") for row in tree_files if isinstance(row, dict)}
        if tree_hashes != observation_hashes:
            fail(f"{vector_id}: artifact tree disagrees with output hash map")
        for relative_path, expected_hash in observation_hashes.items():
            if not isinstance(relative_path, str) or not SHA256_RE.fullmatch(str(expected_hash)):
                fail(f"{vector_id}: malformed observation artifact identity")
            observed_path = (observation_root / relative_path).resolve(strict=True)
            try:
                observed_path.relative_to(observation_root)
            except ValueError:
                fail(f"{vector_id}: observation artifact escapes approved root")
            if observed_path.is_symlink() or not observed_path.is_file() or sha256_file(observed_path) != expected_hash:
                fail(f"{vector_id}: observation artifact hash mismatch: {relative_path}")
        rederived = rederive_vector_result(vector_by_id[vector_id], observation_root, semantic_identity, repo_root)
        rederived.pop("refs", None)
        if rederived != result_doc:
            fail(f"{vector_id}: recorded PASS does not match independently rederived semantic result")
        identity_doc = load_json_bytes((root / vector_id / "runtime_identity.json").read_bytes(), f"{vector_id}/runtime_identity.json")
        if identity_doc != semantic_identity:
            fail(f"{vector_id}: runtime identity disagrees with authenticated semantic identity")
        for group_name in ("source_files", "runtime_files"):
            group = identity_doc.get(group_name) if isinstance(identity_doc, dict) else None
            if not isinstance(group, dict):
                fail(f"{vector_id}: runtime identity group missing: {group_name}")
            for record in group.values():
                if not isinstance(record, dict) or record.get("available") is not True:
                    continue
                live_path = Path(str(record.get("path", ""))).resolve(strict=True)
                expected_live_hash = record.get("sha256")
                if not live_path.is_file() or live_path.is_symlink() or not SHA256_RE.fullmatch(str(expected_live_hash)):
                    fail(f"{vector_id}: malformed live runtime/source identity")
                cache_key = str(live_path)
                if cache_key not in live_hash_cache:
                    live_hash_cache[cache_key] = sha256_file(live_path)
                if live_hash_cache[cache_key] != expected_live_hash:
                    fail(f"{vector_id}: live runtime/source hash drift: {live_path}")
        for filename in EVIDENCE_FILES:
            parsed = load_json_bytes((root / vector_id / filename).read_bytes(), f"{vector_id}/{filename}")
            if not isinstance(parsed, dict):
                fail(f"{vector_id}/{filename}: JSON object required")


def expect_failure(callable_obj: Any, contains: str) -> None:
    try:
        callable_obj()
    except ContractError as exc:
        if contains not in str(exc):
            fail(f"self-test expected failure containing {contains!r}, got {exc!r}")
    else:
        fail(f"self-test expected ContractError containing {contains!r}")


def run_self_tests(registry: Mapping[str, Any], documents: Mapping[str, Any]) -> None:
    if sha256_bytes(canonical_bytes({"b": 2, "a": 1})) != sha256_bytes(canonical_bytes({"a": 1, "b": 2})):
        fail("self-test: canonical key-order invariance failed")
    expect_failure(lambda: load_json_bytes(b'{"x":1,"x":2}', "self-test"), "duplicate JSON object key")
    bad_registry = copy.deepcopy(registry)
    bad_registry["vectors"][1] = copy.deepcopy(bad_registry["vectors"][0])
    expect_failure(
        lambda: validate_registry_structure(bad_registry),
        "vectors must contain each required ID exactly once",
    )
    bad_documents = copy.deepcopy(documents)
    defaults = load_fixture_cases(bad_documents["defaults.json"], "self-test-defaults")
    defaults["P0-DEFAULTS-001"]["value"][1] = defaults["P0-DEFAULTS-001"]["value"][0]
    expect_failure(lambda: validate_semantics(bad_documents), "five ordered unique seeds")
    bad_documents = copy.deepcopy(documents)
    slots = load_fixture_cases(bad_documents["frustrampnn.json"], "self-test-frustration")["P0-FRUSTRAMPNN-003"]["residue"]["slots"]
    slots.pop()
    expect_failure(lambda: validate_semantics(bad_documents), "exactly 20 slots")
    expect_failure(
        lambda: checked_repo_path(Path.cwd(), "../escape.json", "self-test-path"),
        "contained repository-relative path",
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("validate", "verify-evidence"), default="validate")
    parser.add_argument("--definitions", type=Path, default=DEFINITIONS_PATH)
    parser.add_argument("--vectors", type=Path, default=VECTORS_PATH)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--hash-ledger", type=Path)
    parser.add_argument("--self-test", action="store_true", help="run deterministic negative mutation checks after validation")
    args = parser.parse_args(argv)
    if args.mode == "verify-evidence" and (args.evidence_root is None or args.hash_ledger is None):
        parser.error("verify-evidence requires --evidence-root and --hash-ledger")
    if args.mode == "validate" and (args.evidence_root is not None or args.hash_ledger is not None):
        parser.error("--evidence-root/--hash-ledger require verify-evidence mode")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[3]
    try:
        registry, documents = validate_all(repo_root, args.definitions, args.vectors)
        if args.self_test:
            run_self_tests(registry, documents)
        if args.mode == "verify-evidence":
            verify_evidence(repo_root, registry, args.evidence_root, args.hash_ledger)
    except ContractError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.mode == "verify-evidence":
        print(
            f"PASS: authenticated evidence inventory for {len(registry['vectors'])} Phase 0 vectors; "
            f"registry {registry['registry_sha256']}"
        )
        return 0
    fixture_count = len({ref["path"] for vector in registry["vectors"] for ref in vector["fixtures"]})
    suffix = "; self-tests passed" if args.self_test else ""
    print(
        f"PASS: {len(registry['vectors'])} Phase 0 vectors; "
        f"{fixture_count} fixture files; registry {registry['registry_sha256']}{suffix}"
    )
    print("Runtime status remains unmeasured; this is not runtime evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
