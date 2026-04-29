from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "modules" / "boltz_cp_experimental.nf"


def test_build_plan_manifest_exports_env_before_embedded_python() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    process_marker = "process BuildBoltzCPPlanManifest {"
    process_start = text.index(process_marker)
    process_end = text.index("process SpawnBoltzCPChildren {", process_start)
    process_block = text[process_start:process_end]

    export_line = (
        "export TASK_ROOT REPO_PATH INPUT_PATH SHARD_PLAN_ID INPUT_FORMAT OUTPUT_FORMAT GPU_IDS "
        "BCP_BACKEND WRITE_FULL_PAE SEED CONTAINER_PATH CODE_ROOT PARENT_JOB_ID BATCH_NAME BCP_STORE_ROOT"
    )

    assert export_line in process_block
    assert process_block.index(export_line) < process_block.index("python3 - <<'PY'")
    assert 'export PYTHONPATH="\\$REPO_PATH/src\\${PYTHONPATH:+:\\$PYTHONPATH}"' in process_block
    assert 'BOLTZ_PYTHON="\\$REPO_PATH/.venv/bin/python"' in process_block
    assert 'init_plan_args=(' in process_block
    assert '--fallback-root "\\$BCP_STORE_ROOT"' in process_block
    assert '--configured-ram-root "\\$BCP_CONFIGURED_RAM_ROOT"' in process_block
    assert 'BCP_BACKEND=' in process_block
    assert "'bcp_backend': os.environ.get('BCP_BACKEND', '').strip()" in process_block
    assert 'BCP_BACKEND' in process_block.split("python3 - <<'PY'", 1)[0]
    assert 'store_root="\\$("\\${init_plan_args[@]}")"' in process_block
    assert "'colabfold_api_min_interval': float(os.environ.get('COLABFOLD_API_MIN_INTERVAL', '6') or '6')" in process_block
    assert "'colabfold_api_poll_interval': float(os.environ.get('COLABFOLD_API_POLL_INTERVAL', '6') or '6')" in process_block


def test_child_postprocess_exports_bundle_env_before_embedded_python() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    process_marker = "process RunBoltzCPExperimental {"
    process_start = text.index(process_marker)
    process_end = text.index("process BuildBoltzCPPlanManifest {", process_start)
    process_block = text[process_start:process_end]

    child_marker = 'if [ "\\$BCP_ROLE" = "child" ]; then'
    child_start = process_block.index(child_marker)
    export_line = 'export TASK_ROOT BCP_STORE_ROOT BCP_BUNDLE_ID'
    export_index = process_block.index(export_line, child_start)
    python_index = process_block.index("python3 - <<'PY'", export_index)
    child_exit_index = process_block.index('if [ \\$worker_rc -ne 0 ]; then', python_index)

    assert child_start < export_index < python_index < child_exit_index
    assert "store_root = Path(os.environ['BCP_STORE_ROOT'])" in process_block[export_index:child_exit_index]
    assert "bundle_id = os.environ['BCP_BUNDLE_ID']" in process_block[export_index:child_exit_index]
    assert "processed_dir = Path(os.environ['TASK_ROOT']) / 'cp_results' / 'processed'" in process_block[export_index:child_exit_index]


def test_build_plan_manifest_threads_context_spill_contract_into_input_metadata() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    process_marker = "process BuildBoltzCPPlanManifest {"
    process_start = text.index(process_marker)
    process_end = text.index("process SpawnBoltzCPChildren {", process_start)
    process_block = text[process_start:process_end]

    for expected in (
        "BCP_CONTEXT_STORE_MANIFEST_PATH=${contextStoreManifestPath}",
        "BCP_CONTEXT_STATE_PATH=${contextStatePath}",
        "BCP_CONTEXT_LAYER_STATE_PATH=${contextLayerStatePath}",
        "BCP_CONTEXT_EXECUTION_MODE=${contextExecutionMode}",
        "BCP_CONTEXT_TILE_TOKENS=${contextTileTokens}",
        "BCP_CONTEXT_KEY_TILE_TOKENS=${contextKeyTileTokens}",
        "BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}",
        "'bcp_context_store_manifest_path': os.environ.get('BCP_CONTEXT_STORE_MANIFEST_PATH', '').strip()",
        "'bcp_context_state_path': os.environ.get('BCP_CONTEXT_STATE_PATH', '').strip()",
        "'bcp_context_layer_state_path': os.environ.get('BCP_CONTEXT_LAYER_STATE_PATH', '').strip()",
        "'bcp_context_execution_mode': os.environ.get('BCP_CONTEXT_EXECUTION_MODE', '').strip()",
        "'bcp_context_tile_tokens': os.environ.get('BCP_CONTEXT_TILE_TOKENS', '').strip()",
        "'bcp_context_key_tile_tokens': os.environ.get('BCP_CONTEXT_KEY_TILE_TOKENS', '').strip()",
        "'bcp_context_query_tile_tokens': os.environ.get('BCP_CONTEXT_QUERY_TILE_TOKENS', '').strip()",
    ):
        assert expected in process_block
    assert "cont..." not in process_block


def test_workflow_routes_true_distributed_directly_and_keeps_legacy_backends_on_plan_worker_path() -> None:
    workflow_text = (REPO_ROOT / "workflows" / "boltz_cp_experimental.nf").read_text(encoding="utf-8")

    assert "def requestedBackend = params.get('bcp_backend', 'true-distributed-context-parallel').toString()" in workflow_text
    assert "def useTrueDistributed = requestedBackend == 'true-distributed-context-parallel'" in workflow_text
    assert "def requiresPlanRuntime = requestedBackend in ['dram-context-spill-workhorse', 'shared-cache-serial-output-tiling', 'metadata-only']" in workflow_text
    assert "def useCoordinator = bcpRole != 'child' && !useTrueDistributed && (logicalSizeCp > 1 || requiresPlanRuntime)" in workflow_text


def test_true_backend_context_store_is_predictor_owned_not_plan_runtime() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    run_start = text.index("process RunBoltzCPExperimental {")
    plan_start = text.index("process BuildBoltzCPPlanManifest {")
    run_block = text[run_start:plan_start]
    plan_block = text[plan_start:text.index("process SpawnBoltzCPChildren {", plan_start)]

    assert "--context_store_root \"\\$BCP_CONTEXT_STORE_ROOT\"" in run_block
    assert "--context_store_mode \"\\$BCP_CONTEXT_STORE_MODE\"" in run_block
    assert "BCP_CONTEXT_STORE_MODE=${contextStoreMode}" in run_block
    assert "BCP_CONTEXT_STORE_ROOT=${contextStoreRoot}" in run_block
    assert "--context_store_root" not in plan_block
    assert "--context_store_mode" not in plan_block


def test_true_backend_launch_manifest_reports_rank_local_dram_spill_truth_dynamically() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    run_start = text.index("process RunBoltzCPExperimental {")
    plan_start = text.index("process BuildBoltzCPPlanManifest {")
    run_block = text[run_start:plan_start]

    assert 'context_store_mode = os.environ.get("BCP_CONTEXT_STORE_MODE", "").strip()' in run_block
    assert 'context_store_spill_enabled = context_store_mode.startswith("rank-local-dram-spill")' in run_block
    assert '"streaming_spill_enabled": context_store_spill_enabled' in run_block
    assert '"memory_reduction_claimed": context_store_spill_enabled' in run_block
    assert '"within_op_peak_not_reduced": context_store_spill_enabled' in run_block
