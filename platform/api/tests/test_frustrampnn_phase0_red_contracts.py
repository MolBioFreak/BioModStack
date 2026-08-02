"""Phase 0 executable migration obligations for the canonical FrustraMPNN replacement.

These tests are intentionally RED at the Phase 0 base commit. They inspect source
contracts without importing future modules, so each failure identifies missing
behavior rather than a collection or environment defect.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
_FUTURE_CONTRACT = pytest.mark.xfail(
    strict=True,
    reason="Phase 0 migration obligation; remove this mark when this contract becomes green",
)


def _text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _production_python_files() -> list[Path]:
    roots = (REPO_ROOT / "platform/api", REPO_ROOT / "scripts")
    return sorted(
        path
        for root in roots
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
        and ".venv" not in path.parts
    )


@_FUTURE_CONTRACT
def test_red_neutral_frustrampnn_schemas_and_service_package_exist() -> None:
    required = [
        "schemas/workflow_components/workflow_component_request_v1.schema.json",
        "schemas/workflow_components/workflow_component_result_v1.schema.json",
        "schemas/frustrampnn/frustrampnn_structure_map_v1.schema.json",
        "schemas/frustrampnn/frustrampnn_landscape_v1.schema.json",
        "schemas/frustrampnn/frustrampnn_summary_v1.schema.json",
        "schemas/frustrampnn/frustrampnn_execution_receipt_v1.schema.json",
        "schemas/frustrampnn/frustrampnn_result_manifest_v1.schema.json",
        "platform/api/services/frustrampnn/__init__.py",
        "platform/api/services/frustrampnn/contracts.py",
        "platform/api/services/frustrampnn/runtime.py",
        "platform/api/services/frustrampnn/structure.py",
        "platform/api/services/frustrampnn/analysis.py",
        "platform/api/services/frustrampnn/manifests.py",
        "platform/api/services/frustrampnn/ingestion.py",
        "scripts/run_frustrampnn_component.py",
    ]
    missing = [relative for relative in required if not (REPO_ROOT / relative).is_file()]
    invalid = []
    for relative in required:
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        if path.suffix == ".json":
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                invalid.append(f"{relative}: invalid JSON: {exc}")
                continue
            if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                invalid.append(f"{relative}: wrong JSON Schema dialect")
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                invalid.append(f"{relative}: schema does not fail closed on object shape")
        elif path.suffix == ".py":
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            except (UnicodeDecodeError, SyntaxError) as exc:
                invalid.append(f"{relative}: invalid Python: {exc}")
            else:
                if not parsed.body:
                    invalid.append(f"{relative}: empty Python module")
    assert not missing and not invalid, (
        "neutral FrustraMPNN schemas/package contract is not implemented; "
        f"missing={missing}; invalid={invalid}"
    )


def test_red_rewritten_module_exposes_only_canonical_frustrampnn_component() -> None:
    module = _text("modules/frustrampnn.nf")
    violations = []
    if not re.search(r"(?m)^\s*process\s+CanonicalFrustraMPNNTask\s*\{", module):
        violations.append("missing scheduler task CanonicalFrustraMPNNTask")
    if not re.search(r"(?m)^\s*workflow\s+CanonicalFrustraMPNN\s*\{", module):
        violations.append("missing public workflow CanonicalFrustraMPNN")
    for forbidden in (
        "process FrustrampnnQC",
        "process AggregateFrustrationReports",
        "pd.read_csv(",
        "high_frustration_count",
    ):
        if forbidden in module:
            violations.append(f"legacy module token remains: {forbidden}")
    assert not violations, (
        "canonical Nextflow component contract is not implemented; "
        f"violations={violations}"
    )


def test_hardened_command_has_explicit_scheduler_and_model_device_contract(
    tmp_path: Path,
) -> None:
    from services.frustrampnn.runtime import build_frustrampnn_command

    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    normalized = input_root / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    invocation = build_frustrampnn_command(
        apptainer="/usr/bin/apptainer",
        container="/proc/self/fd/41",
        normalized=normalized,
        raw=output_root / "raw_frustrampnn.csv",
        output_root=output_root,
        physical_gpu_id=3,
    )
    argv = list(invocation.argv)
    assert ["--env", "CUDA_VISIBLE_DEVICES=3"] == argv[
        argv.index("CUDA_VISIBLE_DEVICES=3") - 1 : argv.index("CUDA_VISIBLE_DEVICES=3") + 1
    ]
    assert argv[-2:] == ["--device", "cuda"]
    model_argv = argv[argv.index("/opt/venv/bin/frustrampnn") :]
    assert "--gpu_id" not in model_argv and "--gpu-id" not in model_argv
    assert invocation.physical_gpu_id == 3
    assert invocation.task_visible_gpu_id == 0


@_FUTURE_CONTRACT
def test_red_result_ingestion_is_manifest_first_not_loose_csv_discovery() -> None:
    ingester = _text("platform/api/services/result_ingester.py")
    violations = []
    if "frustrampnn_result_manifest_v1" not in ingester:
        violations.append("missing frustrampnn_result_manifest_v1 validation")
    if 'glob("*_frustration.csv")' in ingester:
        violations.append("legacy loose *_frustration.csv discovery remains")
    if "def parse_frustration_csv(" in ingester:
        violations.append("legacy second FrustraMPNN CSV parser remains")
    assert not violations, (
        "manifest-first transactional ingestion contract is not implemented; "
        f"violations={violations}"
    )


@_FUTURE_CONTRACT
def test_red_api_and_upload_actions_are_scheduler_backed() -> None:
    nextflow_service = _text("platform/api/services/nextflow.py")
    direct_router_path = REPO_ROOT / "platform/api/routers/frustrampnn.py"
    mutagenesis = _text("platform/frontend/src/components/MutagenesisTemplate.tsx")
    violations = []
    if direct_router_path.exists():
        direct_router = direct_router_path.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "apptainer", "CONTAINER", "CHECKPOINT"):
            if forbidden in direct_router:
                violations.append(f"execution behavior remains in read-capable router: {forbidden}")
    if "asyncio.create_task(\n            run_batch_frustrampnn" in nextflow_service:
        violations.append("unscheduled run_batch_frustrampnn background task remains")
    if "async def run_batch_frustrampnn(" in nextflow_service:
        violations.append("direct service-owned Apptainer batch runner remains")
    if "fetch('/api/frustrampnn/analyze'" in mutagenesis:
        violations.append("upload UI still calls synchronous execution endpoint")

    assert not violations, (
        "scheduler-backed persisted job/action boundary is not established; "
        f"violations={violations}"
    )


@_FUTURE_CONTRACT
def test_red_legacy_execution_and_writes_are_retired_but_historical_reads_remain() -> None:
    database = _text("platform/api/database.py")
    designs_router = _text("platform/api/routers/designs.py")

    caller_paths = [
        "workflows/protein_design.nf",
        "workflows/antibody_denovo.nf",
        "workflows/complex_prediction.nf",
        "workflows/structure_prediction.nf",
    ]
    violations = []
    for relative in caller_paths:
        source = _text(relative)
        if "FrustrampnnQC" in source or "AggregateFrustrationReports" in source:
            violations.append(f"legacy process include/call remains: {relative}")
    historical_fields = (
        "frustration_high_count",
        "frustration_min_count",
        "frustration_pct_high",
        "frustration_residues",
        "frustration_csv_path",
    )
    for field in historical_fields:
        assert field in database, f"historical Design field was destructively removed: {field}"
        assert field in designs_router, f"historical Design read projection was removed: {field}"
        for path in _production_python_files():
            source = path.read_text(encoding="utf-8")
            if re.search(rf"\b[A-Za-z_][A-Za-z0-9_]*\.{re.escape(field)}\s*=", source):
                relative = path.relative_to(REPO_ROOT)
                violations.append(f"production write to historical Design field remains: {relative}:{field}")
    if (REPO_ROOT / "scripts/backfill_frustrampnn_metrics.py").exists():
        violations.append("legacy CSV metrics backfill remains")
    assert not violations, (
        "legacy retirement boundary is not complete; "
        f"violations={violations}"
    )
