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
    assert "triangle_query_tile_flag=(--context_store_triangle_attention_query_tile_tokens \"\\$BCP_CONTEXT_QUERY_TILE_TOKENS\")" in run_block
    assert '"\\${triangle_query_tile_flag[@]}"' in run_block
    assert "BCP_CONTEXT_STORE_MODE=${contextStoreMode}" in run_block
    assert "BCP_CONTEXT_STORE_ROOT=${contextStoreRoot}" in run_block
    assert "BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}" in run_block
    assert "--context_store_root" not in plan_block
    assert "--context_store_mode" not in plan_block
    assert "--context_store_triangle_attention_query_tile_tokens" not in plan_block


def test_true_backend_launch_manifest_reports_rank_local_dram_spill_truth_dynamically() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    run_start = text.index("process RunBoltzCPExperimental {")
    plan_start = text.index("process BuildBoltzCPPlanManifest {")
    run_block = text[run_start:plan_start]

    assert 'context_store_mode = os.environ.get("BCP_CONTEXT_STORE_MODE", "").strip()' in run_block
    assert 'context_store_spill_enabled = context_store_mode.startswith("rank-local-dram-spill")' in run_block
    assert 'context_virtual_streaming_enabled = context_store_mode == "virtual-dram-stream-attention"' in run_block
    assert 'context_triangle_query_tile_enabled = bool(os.environ.get("BCP_CONTEXT_QUERY_TILE_TOKENS", "").strip())' in run_block
    assert '"streaming_spill_enabled": context_store_spill_enabled' in run_block
    assert '"virtual_streaming_enabled": context_virtual_streaming_enabled' in run_block
    assert '"within_operation_memory_reduction_claimed": context_virtual_streaming_enabled or context_triangle_query_tile_enabled' in run_block
    assert '"memory_reduction_claimed": context_virtual_streaming_enabled or context_store_spill_enabled or context_triangle_query_tile_enabled' in run_block
    assert '"within_op_peak_not_reduced": context_store_spill_enabled and not context_triangle_query_tile_enabled and not context_virtual_streaming_enabled' in run_block


def test_true_backend_threads_virtual_dram_streaming_cli_flags() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    run_start = text.index("process RunBoltzCPExperimental {")
    plan_start = text.index("process BuildBoltzCPPlanManifest {")
    run_block = text[run_start:plan_start]

    assert "def contextLogicalSizeCpValue = (params.get('bcp_context_store_logical_size_cp', '') ?: '').toString().trim()" in run_block
    assert "def contextPairTileTokensValue = (params.get('bcp_context_store_pair_tile_tokens', '') ?: '').toString().trim()" in run_block
    assert "def contextKeyTileTokensValue = (params.get('bcp_context_store_key_tile_tokens', '') ?: '').toString().trim()" in run_block
    assert "def contextQueryTileTokensValue = (params.get('bcp_context_store_triangle_attention_query_tile_tokens', params.get('bcp_context_query_tile_tokens', '512')) ?: '').toString().trim()" in run_block
    assert "def contextProjectionCacheByteBudgetValue = (params.get('bcp_context_store_projection_cache_byte_budget', params.get('bcp_projection_cache_byte_budget', '0')) ?: '0').toString().trim()" in run_block
    assert "def contextStoreEventLevelValue = (params.get('bcp_context_store_event_level', 'perf-summary') ?: 'perf-summary').toString().trim()" in run_block
    assert "BCP_CONTEXT_STORE_LOGICAL_SIZE_CP=${contextLogicalSizeCp}" in run_block
    assert "BCP_CONTEXT_STORE_PAIR_TILE_TOKENS=${contextPairTileTokens}" in run_block
    assert "BCP_CONTEXT_STORE_KEY_TILE_TOKENS=${contextKeyTileTokens}" in run_block
    assert "BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET=${contextProjectionCacheByteBudget}" in run_block
    assert "BCP_CONTEXT_STORE_EVENT_LEVEL=${contextStoreEventLevel}" in run_block
    assert 'context_logical_size_cp_flag=(--context_store_logical_size_cp "\$BCP_CONTEXT_STORE_LOGICAL_SIZE_CP")' in run_block
    assert 'context_pair_tile_tokens_flag=(--context_store_pair_tile_tokens "\$BCP_CONTEXT_STORE_PAIR_TILE_TOKENS")' in run_block
    assert 'context_key_tile_tokens_flag=(--context_store_key_tile_tokens "\$BCP_CONTEXT_STORE_KEY_TILE_TOKENS")' in run_block
    assert 'context_projection_cache_flag=(--context_store_projection_cache_byte_budget "\$BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET")' in run_block
    assert '"\${context_logical_size_cp_flag[@]}"' in run_block
    assert '"\${context_pair_tile_tokens_flag[@]}"' in run_block
    assert '"\${context_key_tile_tokens_flag[@]}"' in run_block
    assert '"\${context_projection_cache_flag[@]}"' in run_block
    assert '--context_store_event_level "\$BCP_CONTEXT_STORE_EVENT_LEVEL"' in run_block
    assert '"context_store_logical_size_cp": os.environ.get("BCP_CONTEXT_STORE_LOGICAL_SIZE_CP", "").strip()' in run_block
    assert '"context_store_pair_tile_tokens": os.environ.get("BCP_CONTEXT_STORE_PAIR_TILE_TOKENS", "").strip()' in run_block
    assert '"context_store_key_tile_tokens": os.environ.get("BCP_CONTEXT_STORE_KEY_TILE_TOKENS", "").strip()' in run_block
    assert '"context_store_projection_cache_byte_budget": os.environ.get("BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET", "").strip()' in run_block
    assert '"context_store_event_level": os.environ.get("BCP_CONTEXT_STORE_EVENT_LEVEL", "").strip()' in run_block
    assert "--context_store_logical_size_cp" not in text[text.index("process BuildBoltzCPPlanManifest {"):text.index("process SpawnBoltzCPChildren {")]
