#!/usr/bin/env python3
"""Execute Phase 0 contract-admission and live-default probes.

This is evidence generation, not a production converter. It validates the frozen
positive/negative admission semantics, Protenix launcher defaults, and authenticated
FrustraMPNN row semantics, then writes one deterministic JSON report.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
ENTITY_FIELDS = {
    "protein": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "sequence", "modifications", "source_order"},
    "dna": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "sequence", "source_order"},
    "rna": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "sequence", "source_order"},
    "ligand_ccd": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "ccd", "source_order"},
    "ligand_smiles": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "smiles", "source_order"},
    "ion": {"entity_type", "source_entity_id", "count", "ordered_instance_ids", "ccd", "source_order"},
}
ROOT_FIELDS = {"case_key", "kind", "reason", "entities", "bonds", "admission", "expected_instance_count", "repeated_semantics"}
KNOWN_MODIFICATIONS = {"MSE", "SEP"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_frustrampnn_contract_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a nonempty list")
    validated = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"mutation_aa", "status", "score"}:
            raise ValueError(f"row {index} has malformed fields")
        mutation = row["mutation_aa"]
        if mutation not in AA_ORDER or mutation in seen:
            raise ValueError(f"row {index} has invalid or duplicate mutation")
        if row["status"] != "ok":
            raise ValueError(f"row {index} status is not ok")
        score = row["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise ValueError(f"row {index} score is missing or nonfinite")
        seen.add(mutation)
        validated.append(row)
    return validated


def reject(reason: str, checks: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    return "reject", reason, checks


def admit_complex(case: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    checks: dict[str, Any] = {}
    unknown_root = sorted(set(case) - ROOT_FIELDS)
    checks["root_fields_supported"] = not unknown_root
    if unknown_root:
        return reject("unsupported_field", checks)

    entities = case.get("entities")
    bonds = case.get("bonds")
    if not isinstance(entities, list) or not isinstance(bonds, list):
        return reject("malformed_case", checks)

    admission = case.get("admission")
    if isinstance(admission, dict):
        lossy = bool(admission.get("conversion_omissions")) or admission.get("token_count", 0) > admission.get("token_limit", 0)
        checks["lossless_and_within_token_limit"] = not lossy
        if lossy:
            return reject("lossy_conversion_or_token_limit", checks)

    source_orders = [entity.get("source_order") for entity in entities if isinstance(entity, dict) and "source_order" in entity]
    if source_orders and len(source_orders) != len(set(source_orders)):
        checks["source_order_unambiguous"] = False
        return reject("ambiguous_ordering", checks)
    checks["source_order_unambiguous"] = True

    seen_instances: set[str] = set()
    instance_entity: dict[str, dict[str, Any]] = {}
    ledger: list[dict[str, Any]] = []
    for entity_index, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict):
            return reject("malformed_case", checks)
        entity_type = entity.get("entity_type")
        if entity_type not in ENTITY_FIELDS:
            checks["entity_types_supported"] = False
            return reject("unsupported_entity", checks)
        checks["entity_types_supported"] = True
        unknown = sorted(set(entity) - ENTITY_FIELDS[entity_type])
        if unknown:
            checks["entity_fields_supported"] = False
            return reject("unsupported_field", checks)
        checks["entity_fields_supported"] = True

        count = entity.get("count")
        ids = entity.get("ordered_instance_ids")
        if not isinstance(count, int) or count < 1 or not isinstance(ids, list) or len(ids) != count:
            checks["count_matches_instance_ids"] = False
            return reject("count_instance_cardinality", checks)
        checks["count_matches_instance_ids"] = True
        if any(not isinstance(item, str) or not item or item in seen_instances for item in ids):
            checks["instance_ids_unique"] = False
            return reject("duplicate_instance_id", checks)
        checks["instance_ids_unique"] = True

        sequence = entity.get("sequence")
        if entity_type in {"protein", "dna", "rna"}:
            alphabet = set(AA_ORDER + "BXZOU") if entity_type == "protein" else set("ACGTN") if entity_type == "dna" else set("ACGUN")
            if not isinstance(sequence, str) or not sequence or any(symbol not in alphabet for symbol in sequence.upper()):
                checks["sequence_valid"] = False
                return reject("malformed_sequence", checks)
            checks["sequence_valid"] = True
        elif entity_type in {"ligand_ccd", "ion"}:
            if not isinstance(entity.get("ccd"), str) or not re.fullmatch(r"[A-Z0-9]{1,5}", entity["ccd"]):
                return reject("malformed_entity", checks)
        elif not isinstance(entity.get("smiles"), str) or not entity["smiles"].strip():
            return reject("malformed_entity", checks)

        modifications = entity.get("modifications", [])
        if not isinstance(modifications, list):
            return reject("unsupported_modification", checks)
        for modification in modifications:
            valid = (
                isinstance(modification, dict)
                and set(modification) == {"position", "modification"}
                and isinstance(modification.get("position"), int)
                and isinstance(sequence, str)
                and 1 <= modification["position"] <= len(sequence)
                and modification.get("modification") in KNOWN_MODIFICATIONS
            )
            if not valid:
                checks["modifications_supported"] = False
                return reject("unsupported_modification", checks)
        checks["modifications_supported"] = True

        for ordinal, instance_id in enumerate(ids, start=1):
            seen_instances.add(instance_id)
            instance_entity[instance_id] = entity
            ledger.append({
                "entity_index": entity_index,
                "source_entity_id": entity["source_entity_id"],
                "instance_id": instance_id,
                "ordinal": ordinal,
                "entity_type": entity_type,
            })

    for bond in bonds:
        if not isinstance(bond, dict) or set(bond) != {"left", "right"}:
            checks["bond_type_supported"] = False
            return reject("unsupported_bond", checks)
        checks["bond_type_supported"] = True
        for endpoint in (bond.get("left"), bond.get("right")):
            if not isinstance(endpoint, dict) or set(endpoint) != {"instance_id", "position", "atom"}:
                return reject("unsupported_bond", checks)
            instance_id = endpoint.get("instance_id")
            if instance_id not in instance_entity:
                checks["bond_references_resolve"] = False
                return reject("dangling_bond_reference", checks)
            entity = instance_entity[instance_id]
            max_position = len(entity["sequence"]) if "sequence" in entity else 1
            if not isinstance(endpoint.get("position"), int) or not 1 <= endpoint["position"] <= max_position or not isinstance(endpoint.get("atom"), str) or not endpoint["atom"]:
                return reject("unsupported_bond", checks)
        checks["bond_references_resolve"] = True

    checks["instance_count"] = len(ledger)
    expected_count = case.get("expected_instance_count")
    checks["expected_instance_count_matches"] = expected_count is None or expected_count == len(ledger)
    if not checks["expected_instance_count_matches"]:
        return reject("count_instance_cardinality", checks)
    checks["ordered_instance_ledger"] = ledger
    return "accept", "accepted_losslessly", checks


def adjudicate_confornets(case: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    checks = {
        "single_chain": case.get("chain_count") == 1,
        "protein_only": case.get("molecule_types") == ["protein"],
        "at_most_two_references": case.get("reference_count", 0) <= 2,
        "coordinates_complete": case.get("observed_coordinates", case.get("expected_coordinates")) == case.get("expected_coordinates"),
    }
    if not checks["single_chain"]:
        return "reject", "multi_chain", checks
    if not checks["protein_only"]:
        return "reject", "non_protein", checks
    if not checks["at_most_two_references"]:
        return "reject", "too_many_references", checks
    if not checks["coordinates_complete"]:
        return "reject", "missing_coordinate", checks
    return "accept", "accepted", checks


def parser_defaults(source: Path) -> dict[str, Any]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    result: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument" or not node.args:
            continue
        if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        for keyword in node.keywords:
            if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
                result[node.args[0].value] = keyword.value.value
    return result


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--observations-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    obs = args.observations_root.resolve(strict=True)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    fixture_dir = repo / "platform/api/tests/fixtures/conformational_mapping/phase_0_vectors"
    fixture_paths = {
        "complex": fixture_dir / "complex_cases.json",
        "confornets": fixture_dir / "confornets_cases.json",
        "defaults": fixture_dir / "defaults.json",
        "frustrampnn": fixture_dir / "frustrampnn.json",
    }
    cases: dict[str, Any] = {}

    for case in load_json(fixture_paths["complex"])["cases"]:
        actual, reason, checks = admit_complex(case)
        expected = "accept" if case["kind"] == "positive" else "reject"
        expected_reason = case.get("reason")
        passed = actual == expected and (expected_reason is None or reason == expected_reason)
        cases[case["case_key"]] = {"result": "PASS" if passed else "STOP", "actual": actual, "reason": reason, "checks": checks}

    for case in load_json(fixture_paths["confornets"])["cases"]:
        if case["kind"] != "negative":
            continue
        actual, reason, checks = adjudicate_confornets(case)
        passed = actual == "reject" and reason == case["reason"]
        cases[case["case_key"]] = {"result": "PASS" if passed else "STOP", "actual": actual, "reason": reason, "checks": checks}

    launcher = repo / "scripts/run_protenix_inference.py"
    launcher_defaults = parser_defaults(launcher)
    layout_meta_path = obs / "runtime_inventory/protenix_layout_fresh_v3/run.meta.json"
    layout_meta = load_json(layout_meta_path)
    layout_args = layout_meta.get("command", [])
    def layout_arg(name: str) -> str | None:
        try:
            index = layout_args.index(name)
            return str(layout_args[index + 1])
        except (ValueError, IndexError):
            return None
    layout_command = {
        "seeds": [int(item) for item in (layout_arg("--seeds") or "").split(",") if item],
        "samples_per_seed": int(layout_arg("--sample") or 0),
        "use_default_params": layout_arg("--use_default_params") == "true",
        "cycle": int(layout_arg("--cycle") or 0),
        "step": int(layout_arg("--step") or 0),
    }
    default_checks = {
        "P0-DEFAULTS-001": layout_command.get("seeds") == [101, 202, 303, 404, 505],
        "P0-DEFAULTS-002": layout_command.get("samples_per_seed") == 5,
        "P0-DEFAULTS-003": layout_command.get("use_default_params") is True and layout_command.get("cycle") == 10 and layout_command.get("step") == 200,
        "P0-DEFAULTS-004": layout_command.get("cycle") == 10 and layout_command.get("step") == 200,
        "P0-DEFAULTS-005": launcher_defaults.get("--use_msa") is True,
        "P0-DEFAULTS-006": launcher_defaults.get("--use_template") is False,
        "P0-DEFAULTS-007": launcher_defaults.get("--use_rna_msa") is False,
    }
    for case in load_json(fixture_paths["defaults"])["cases"]:
        passed = default_checks.get(case["case_key"], False)
        cases[case["case_key"]] = {
            "result": "PASS" if passed else "STOP",
            "actual": "effective_configuration_authenticated" if passed else "configuration_mismatch",
            "checks": {"expected_value_matches_live_launcher_or_invocation": passed},
        }

    frustra_dir = obs / "runtime_inventory/frustrampnn_fresh"
    csv_path = frustra_dir / "frustration.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    positions = sorted({int(row["position"]) for row in rows})
    grouped = {position: [row for row in rows if int(row["position"]) == position] for position in positions}
    all_finite = all(math.isfinite(float(row["frustration_pred"])) for row in rows)
    aa_complete = all("".join(row["mutation"] for row in grouped[position]) == AA_ORDER for position in positions)
    chains = sorted({row["chain"] for row in rows})
    meta_text = (frustra_dir / "run.meta").read_text(encoding="utf-8")
    image = Path("/mnt/BioModStack/apptainer/frustrampnn.sif")
    checkpoint_result = subprocess.run(
        ["apptainer", "exec", "--containall", str(image), "sha256sum", "/opt/frustrampnn_weights/megascale.ckpt"],
        text=True, capture_output=True, check=False,
    )
    checkpoint_sha = checkpoint_result.stdout.split()[0] if checkpoint_result.returncode == 0 and checkpoint_result.stdout.split() else None
    frustra_fixture = load_json(fixture_paths["frustrampnn"])["cases"]
    negative_rows = next(case["rows"] for case in frustra_fixture if case["case_key"] == "P0-FRUSTRAMPNN-004")
    try:
        validate_frustrampnn_contract_rows(negative_rows)
        malformed_rejected = False
        rejection_reason = None
    except ValueError as exc:
        malformed_rejected = True
        rejection_reason = str(exc)
    frustra_checks = {
        "authenticated_runtime": "predict_rc=0" in meta_text and image.is_file() and checkpoint_sha is not None,
        "single_selected_chain": chains == ["A"],
        "twenty_ordered_finite_slots_per_residue": len(rows) == 400 and len(positions) == 20 and aa_complete and all_finite,
        "malformed_and_nonfinite_rejected": malformed_rejected,
    }
    for case in frustra_fixture:
        key = case["case_key"]
        if key == "P0-FRUSTRAMPNN-001":
            passed = frustra_checks["authenticated_runtime"]
        elif key == "P0-FRUSTRAMPNN-002":
            passed = frustra_checks["single_selected_chain"]
        elif key == "P0-FRUSTRAMPNN-003":
            passed = frustra_checks["twenty_ordered_finite_slots_per_residue"]
        else:
            malformed_rows = case.get("rows", [])
            passed = frustra_checks["malformed_and_nonfinite_rejected"] and any(
                row.get("status") != "ok" or row.get("score") in {None, "NaN"}
                for row in malformed_rows
                if isinstance(row, dict)
            )
        cases[key] = {
            "result": "PASS" if passed else "STOP",
            "actual": "authenticated_runtime_semantics" if passed else "runtime_semantics_failed",
            "checks": frustra_checks,
            "rejection_reason": rejection_reason if key == "P0-FRUSTRAMPNN-004" else None,
            "checkpoint_sha256": checkpoint_sha,
            "container_sha256": sha256(image),
        }

    artifacts = []
    for path in [
        *fixture_paths.values(),
        Path(__file__).resolve(),
        launcher,
        layout_meta_path,
        csv_path,
        frustra_dir / "run.meta",
        frustra_dir / "predict.log",
    ]:
        if path.is_file():
            artifacts.append({"path": str(path), "sha256": sha256(path)})
    passed_count = sum(row["result"] == "PASS" for row in cases.values())
    report = {
        "schema": "phase0-contract-admission-runtime-v1",
        "cases": cases,
        "summary": {"total": len(cases), "pass": passed_count, "stop": len(cases) - passed_count},
        "runtime": {
            "frustrampnn_container": str(image),
            "frustrampnn_container_sha256": sha256(image),
            "frustrampnn_checkpoint_sha256": checkpoint_sha,
            "protenix_launcher": str(launcher),
            "protenix_launcher_defaults": launcher_defaults,
        },
        "artifacts": artifacts,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if passed_count == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(run())
