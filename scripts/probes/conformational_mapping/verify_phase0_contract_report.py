#!/usr/bin/env python3
"""Verify the authenticated Phase 0 admission/default/FrustraMPNN report."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

from adjudicate_phase0_contracts import (
    AA_ORDER,
    admit_complex,
    adjudicate_confornets,
    parser_defaults,
    validate_frustrampnn_contract_rows,
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _result(runtime_status: str, evidence_tier: str, gate_effect: str, rationale: str, refs: list[Path] | None = None) -> dict[str, Any]:
    return {
        "runtime_status": runtime_status,
        "evidence_tier": evidence_tier,
        "gate_effect": gate_effect,
        "rationale": rationale,
        "refs": refs or [],
    }


def _derive_case(vector_id: str, observations_root: Path, repository_root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    fixture_dir = repository_root / "platform/api/tests/fixtures/conformational_mapping/phase_0_vectors"
    def fixture_case(filename: str) -> dict[str, Any]:
        document = json.loads((fixture_dir / filename).read_text(encoding="utf-8"))
        cases = document if isinstance(document, list) else document["cases"]
        return next(item for item in cases if item["case_key"] == vector_id)

    if vector_id.startswith("P0-VECTOR-COMPLEX-"):
        item = fixture_case("complex_cases.json")
        actual, reason, checks = admit_complex(item)
        expected = "accept" if item["kind"] == "positive" else "reject"
        expected_reason = item.get("reason")
        passed = actual == expected and (expected_reason is None or reason == expected_reason)
        return {"result": "PASS" if passed else "STOP", "actual": actual, "reason": reason, "checks": checks}

    if vector_id.startswith("P0-CONFORNETS-NEG-"):
        item = fixture_case("confornets_cases.json")
        actual, reason, checks = adjudicate_confornets(item)
        passed = actual == "reject" and reason == item["reason"]
        return {"result": "PASS" if passed else "STOP", "actual": actual, "reason": reason, "checks": checks}

    if vector_id.startswith("P0-DEFAULTS-"):
        launcher = repository_root / "scripts/run_protenix_inference.py"
        defaults = parser_defaults(launcher)
        meta = json.loads((observations_root / "runtime_inventory/protenix_layout_fresh_v3/run.meta.json").read_text(encoding="utf-8"))
        command = meta.get("command", [])
        def arg(name: str) -> str | None:
            try:
                return str(command[command.index(name) + 1])
            except (ValueError, IndexError):
                return None
        checks = {
            "P0-DEFAULTS-001": [int(x) for x in (arg("--seeds") or "").split(",") if x] == [101, 202, 303, 404, 505],
            "P0-DEFAULTS-002": int(arg("--sample") or 0) == 5,
            "P0-DEFAULTS-003": arg("--use_default_params") == "true" and int(arg("--cycle") or 0) == 10 and int(arg("--step") or 0) == 200,
            "P0-DEFAULTS-004": int(arg("--cycle") or 0) == 10 and int(arg("--step") or 0) == 200,
            "P0-DEFAULTS-005": defaults.get("--use_msa") is True,
            "P0-DEFAULTS-006": defaults.get("--use_template") is False,
            "P0-DEFAULTS-007": defaults.get("--use_rna_msa") is False,
        }
        passed = checks.get(vector_id, False)
        return {"result": "PASS" if passed else "STOP", "actual": "effective_configuration_authenticated" if passed else "configuration_mismatch", "checks": {"expected_value_matches_live_launcher_or_invocation": passed}}

    item = fixture_case("frustrampnn.json")
    frustra_dir = observations_root / "runtime_inventory/frustrampnn_fresh"
    with (frustra_dir / "frustration.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    positions = sorted({int(row["position"]) for row in rows})
    grouped = {position: [row for row in rows if int(row["position"]) == position] for position in positions}
    all_finite = all(math.isfinite(float(row["frustration_pred"])) for row in rows)
    aa_complete = all("".join(row["mutation"] for row in grouped[position]) == AA_ORDER for position in positions)
    chains = sorted({row["chain"] for row in rows})
    malformed_rejected = False
    rejection_reason = None
    try:
        validate_frustrampnn_contract_rows(item.get("rows", []))
    except ValueError as exc:
        malformed_rejected = True
        rejection_reason = str(exc)
    image_identity = identity.get("runtime_files", {}).get("frustrampnn_image", {})
    checkpoint_identity = identity.get("frustrampnn_checkpoint", {})
    meta_text = (frustra_dir / "run.meta").read_text(encoding="utf-8")
    frustra_checks = {
        "authenticated_runtime": "predict_rc=0" in meta_text and image_identity.get("available") is True and checkpoint_identity.get("available") is True,
        "single_selected_chain": chains == ["A"],
        "twenty_ordered_finite_slots_per_residue": len(rows) == 400 and len(positions) == 20 and aa_complete and all_finite,
        "malformed_and_nonfinite_rejected": malformed_rejected,
    }
    if vector_id == "P0-FRUSTRAMPNN-001": passed = frustra_checks["authenticated_runtime"]
    elif vector_id == "P0-FRUSTRAMPNN-002": passed = frustra_checks["single_selected_chain"]
    elif vector_id == "P0-FRUSTRAMPNN-003": passed = frustra_checks["twenty_ordered_finite_slots_per_residue"]
    else: passed = frustra_checks["malformed_and_nonfinite_rejected"]
    return {
        "result": "PASS" if passed else "STOP",
        "actual": "authenticated_runtime_semantics" if passed else "runtime_semantics_failed",
        "checks": frustra_checks,
        "rejection_reason": rejection_reason if vector_id == "P0-FRUSTRAMPNN-004" else None,
        "checkpoint_sha256": checkpoint_identity.get("sha256"),
        "container_sha256": image_identity.get("sha256"),
    }


def classify_contract_report(
    vector_id: str,
    observations_root: Path,
    repository_root: Path,
    identity: dict[str, Any],
) -> dict[str, Any]:
    report_path = observations_root / "runtime_inventory" / "contract_admission" / "report.json"
    if not report_path.is_file() or report_path.is_symlink():
        return _result("unmeasured", "unmeasured", "STOP", "authenticated contract-admission report is unavailable")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("unmeasured", "unmeasured", "STOP", f"contract-admission report is invalid: {exc}", [report_path])

    case = report.get("cases", {}).get(vector_id) if isinstance(report, dict) else None
    summary = report.get("summary") if isinstance(report, dict) else None
    artifacts = report.get("artifacts") if isinstance(report, dict) else None
    runtime_raw = report.get("runtime") if isinstance(report, dict) else None
    runtime: dict[str, Any] = runtime_raw if isinstance(runtime_raw, dict) else {}
    try:
        derived_case = _derive_case(vector_id, observations_root, repository_root, identity)
    except (OSError, ValueError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        return _result("observed-fail", "fresh authenticated", "STOP", f"independent case derivation failed: {exc}", [report_path])
    valid = (
        report.get("schema") == "phase0-contract-admission-runtime-v1"
        and isinstance(summary, dict)
        and summary == {"total": 37, "pass": 37, "stop": 0}
        and isinstance(case, dict)
        and case == derived_case
        and case.get("result") == "PASS"
        and isinstance(artifacts, list)
        and len(artifacts) >= 10
        and isinstance(runtime, dict)
    )
    fixture_dir = repository_root / "platform/api/tests/fixtures/conformational_mapping/phase_0_vectors"
    expected_artifact_paths = {
        (fixture_dir / name).resolve(strict=True) for name in ("complex_cases.json", "confornets_cases.json", "defaults.json", "frustrampnn.json")
    }
    expected_artifact_paths.update({
        (repository_root / "scripts/probes/conformational_mapping/adjudicate_phase0_contracts.py").resolve(strict=True),
        (repository_root / "scripts/run_protenix_inference.py").resolve(strict=True),
        (observations_root / "runtime_inventory/protenix_layout_fresh_v3/run.meta.json").resolve(strict=True),
        (observations_root / "runtime_inventory/frustrampnn_fresh/frustration.csv").resolve(strict=True),
        (observations_root / "runtime_inventory/frustrampnn_fresh/run.meta").resolve(strict=True),
        (observations_root / "runtime_inventory/frustrampnn_fresh/predict.log").resolve(strict=True),
    })
    try:
        actual_artifact_paths = [Path(item.get("path", "")).resolve(strict=True) for item in artifacts if isinstance(item, dict)] if isinstance(artifacts, list) else []
    except (OSError, RuntimeError):
        actual_artifact_paths = []
        valid = False
    valid = valid and len(actual_artifact_paths) == len(expected_artifact_paths) and set(actual_artifact_paths) == expected_artifact_paths
    refs = [report_path]
    roots = (observations_root.resolve(strict=True), repository_root.resolve(strict=True))
    if isinstance(artifacts, list):
        for item in artifacts:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                valid = False
                continue
            path = Path(item["path"])
            try:
                resolved = path.resolve(strict=True)
                contained = any(resolved == root or root in resolved.parents for root in roots)
                valid = valid and contained and resolved.is_file() and not resolved.is_symlink() and _sha256(resolved) == item["sha256"]
                if contained:
                    refs.append(resolved)
            except (OSError, RuntimeError):
                valid = False

    frustra_identity = identity.get("runtime_files", {}).get("frustrampnn_image", {})
    checkpoint_identity = identity.get("frustrampnn_checkpoint", {})
    launcher_identity = identity.get("source_files", {}).get("scripts/run_protenix_inference.py", {})
    valid = valid and frustra_identity.get("available") is True and checkpoint_identity.get("available") is True and launcher_identity.get("available") is True
    valid = valid and all(SHA256_RE.fullmatch(str(item.get("sha256"))) is not None for item in (frustra_identity, checkpoint_identity, launcher_identity))
    valid = valid and runtime.get("frustrampnn_container_sha256") == frustra_identity.get("sha256")
    valid = valid and runtime.get("frustrampnn_checkpoint_sha256") == checkpoint_identity.get("sha256")
    launcher_path = repository_root / "scripts" / "run_protenix_inference.py"
    valid = valid and launcher_path.is_file() and _sha256(launcher_path) == launcher_identity.get("sha256")

    if not valid:
        return _result("observed-fail", "fresh authenticated", "STOP", "contract-admission evidence failed schema, hash, containment, runtime-identity, or case checks", refs)
    return _result("passed", "fresh authenticated", "PASS", f"authenticated Phase 0 contract runtime passed {vector_id}", refs)
