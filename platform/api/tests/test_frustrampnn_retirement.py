from __future__ import annotations

import importlib.util
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
CHECKER = REPO_ROOT / "scripts" / "check_frustrampnn_retirement.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("frustrampnn_retirement_checker", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_active_production_paths_have_no_retired_frustrampnn_ownership() -> None:
    checker = _load_checker()
    violations = checker.scan(REPO_ROOT)
    assert not violations, "\n".join(item.render(REPO_ROOT) for item in violations)


def test_operator_stage_label_describes_analysis_not_redesign() -> None:
    jobs_router = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    assert '"frustrampnn": "Frustration analysis"' in jobs_router
    assert "FrustraMPNN redesign" not in jobs_router


def test_checker_reports_path_line_and_rule_for_each_forbidden_family(tmp_path: Path) -> None:
    checker = _load_checker()
    module = tmp_path / "modules" / "legacy.nf"
    module.parent.mkdir(parents=True)
    module.write_text(
        "process FrustrampnnQC { errorStrategy 'ignore' }\n"
        "def candidate_id = pdb.baseName\n",
        encoding="utf-8",
    )
    service = tmp_path / "platform" / "api" / "services" / "legacy.py"
    service.parent.mkdir(parents=True)
    service.write_text(
        "async def maybe_trigger_batch_frustrampnn():\n"
        "    files = root.glob('*_frustration.csv')\n",
        encoding="utf-8",
    )
    frontend = tmp_path / "platform" / "frontend" / "src" / "legacy.ts"
    frontend.parent.mkdir(parents=True)
    frontend.write_text(
        "const endpoint = '/api/frustrampnn/analyze';\n"
        "const parsed = Papa.parse(native_profile + frustration_pred);\n",
        encoding="utf-8",
    )

    violations = checker.scan(tmp_path)
    rules = {item.rule for item in violations}
    assert {
        "legacy_nextflow_process",
        "legacy_batch_execution",
        "retired_upload_route",
        "loose_csv_discovery",
        "frontend_native_profile_parser",
        "frontend_raw_score_parser",
        "frontend_papa_parser",
        "fail_open_error_strategy",
        "basename_identity_join",
    } <= rules
    rendered = [item.render(tmp_path) for item in violations]
    assert all(entry.count(":") >= 3 for entry in rendered)


def test_checker_distinguishes_model_execution_from_unrelated_subprocesses(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    services = tmp_path / "platform" / "api" / "services"
    services.mkdir(parents=True)
    (services / "mixed_gpu.py").write_text(
        "# FrustraMPNN scheduler telemetry\n"
        "import subprocess\n"
        "subprocess.run(['nvidia-smi', '--query-gpu=index'])\n",
        encoding="utf-8",
    )
    (services / "bad_model_owner.py").write_text(
        "import subprocess\n"
        "subprocess.run(['apptainer', 'exec', '/tmp/frustrampnn.sif', 'predict'])\n",
        encoding="utf-8",
    )

    violations = checker.scan(tmp_path)
    direct = [item for item in violations if item.rule == "direct_frustrampnn_process_execution"]
    assert [item.path.name for item in direct] == ["bad_model_owner.py"]


def test_checker_allows_only_the_versioned_cm_policy_adapter_identity(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    adapter = (
        tmp_path
        / "platform"
        / "api"
        / "services"
        / "conformational_mapping"
        / "frustrampnn_adapter.py"
    )
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        'CM_THRESHOLD_POLICY_ADAPTER_ID = "frustrampnn_class_v1"\n',
        encoding="utf-8",
    )
    schema = (
        tmp_path
        / "schemas"
        / "conformational_mapping"
        / "cm_frustration_landscape_v1.schema.json"
    )
    schema.parent.mkdir(parents=True)
    schema.write_text(
        '{"threshold_policy_id":{"const":"frustrampnn_class_v1"}}\n',
        encoding="utf-8",
    )
    rogue = adapter.parent / "rogue_policy.py"
    rogue.write_text(
        'POLICY = {"id": "frustrampnn_class_v1", "high": -1.0}\n',
        encoding="utf-8",
    )

    violations = checker.scan(tmp_path)
    policy = [item for item in violations if item.rule == "duplicate_threshold_policy"]
    assert {item.path.name for item in policy} == {"rogue_policy.py"}
