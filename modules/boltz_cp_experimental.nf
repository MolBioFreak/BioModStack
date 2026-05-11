nextflow.enable.dsl = 2

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process RunBoltzCPExperimental {
    label 'BoltzCP'
    label 'gpu'

    publishDir "${params.out_dir}/run/boltz_cp_experimental", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/run/boltz_cp_experimental", mode: 'copy', pattern: 'true_cp_*'
    publishDir "${params.out_dir}/run/boltz_cp_experimental", mode: 'copy', pattern: 'true_cp_context_store/**', saveAs: { filename -> filename.replace('true_cp_context_store/', 'true_cp_context_store/') }
    publishDir "${params.out_dir}/inputs/boltz_cp", mode: 'copy', pattern: 'staged_input/**', saveAs: { filename -> filename.replace('staged_input/', '') }
    publishDir "${params.out_dir}/processed/boltz_cp", mode: 'copy', pattern: 'cp_results/processed/**', saveAs: { filename -> filename.replace('cp_results/processed/', '') }

    input:
    path input_config

    output:
    path 'cp_results', emit: results_dir, optional: true
    path 'cp_results/processed', emit: processed_dir, optional: true
    path 'true_cp_launch_manifest.json', emit: launch_manifest, optional: true
    path 'true_cp_failure_diagnostics.json', emit: failure_diagnostics, optional: true
    path 'true_cp_rank_probe.jsonl', emit: rank_probe, optional: true
    path 'true_cp_context_store', emit: context_store, optional: true
    path '*.log'

    script:
    def gpuIdsParam = params.get('bcp_gpu_ids', null)
    if (gpuIdsParam == null || gpuIdsParam.toString().trim() == '') {
        gpuIdsParam = params.get('gpu_id', '')
    }
    def gpuIdsValue = (gpuIdsParam == null ? '' : gpuIdsParam.toString())
    def gpuIds = gpuIdsValue.split(',').collect { it.trim() }.findAll { it }
    def gpuIdsRaw = shellQuote(gpuIds.join(','))
    def nproc = gpuIds ? gpuIds.size() : 1
    def sizeCp = params.get('bcp_size_cp', 4) as Integer
    def sizeDp = Math.max((int) (nproc / sizeCp), 1)
    def inputFormat = params.get('bcp_input_format', 'config_files').toString()
    def outputFormat = params.get('bcp_output_format', 'mmcif').toString()
    def confidencePrediction = params.get('bcp_confidence_prediction', false).toString().toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def quotedConfidencePrediction = shellQuote(confidencePrediction ? 'true' : 'false')
    def maxMsaSeqsValue = (params.get('bcp_max_msa_seqs', '') ?: '').toString().trim()
    def maxParallelSamplesValue = (params.get('bcp_max_parallel_samples', '1') ?: '1').toString().trim()
    def precisionValue = (params.get('bcp_precision', 'BF16') ?: 'BF16').toString().trim()
    // Default to the built-in reference backend because the deployed Boltz-CP runtime does not ship trifast.
    // Callers can still opt into trifast/cueq explicitly when the container has those kernels installed.
    def triattnBackendValue = (params.get('bcp_triattn_backend', 'reference') ?: 'reference').toString().trim()
    def sdpaWithBiasBackendValue = (params.get('bcp_sdpa_with_bias_backend', 'torch_flex_attn') ?: 'torch_flex_attn').toString().trim()
    def sdpaWithBiasShardwiseBackendValue = (params.get('bcp_sdpa_with_bias_shardwise_backend', 'torch_sdpa_efficient_attention') ?: 'torch_sdpa_efficient_attention').toString().trim()
    def atomsPerWindowQueriesKeysValue = (params.get('bcp_atoms_per_window_queries_keys', '16 32') ?: '16 32').toString().trim()
    def contextStoreModeValue = (params.get('bcp_context_store_mode', 'evidence-only') ?: 'evidence-only').toString().trim()
    def contextStoreRootValue = (params.get('bcp_context_store_root', '') ?: '').toString().trim()
    def contextQueryTileTokensValue = (params.get('bcp_context_store_triangle_attention_query_tile_tokens', params.get('bcp_context_query_tile_tokens', '512')) ?: '').toString().trim()
    def contextLogicalSizeCpValue = (params.get('bcp_context_store_logical_size_cp', '') ?: '').toString().trim()
    def contextPairTileTokensValue = (params.get('bcp_context_store_pair_tile_tokens', '') ?: '').toString().trim()
    def contextKeyTileTokensValue = (params.get('bcp_context_store_key_tile_tokens', '') ?: '').toString().trim()
    def rawContextProjectedTriangleRhsRowReuseWindowValue = params.containsKey('bcp_context_store_projected_triangle_rhs_row_reuse_window')
        ? params.get('bcp_context_store_projected_triangle_rhs_row_reuse_window')
        : params.get('context_store_projected_triangle_rhs_row_reuse_window', '')
    def contextProjectedTriangleRhsRowReuseWindowValue = (rawContextProjectedTriangleRhsRowReuseWindowValue ?: '').toString().trim()
    // The virtual-DRAM projection cache must be large enough to retain one rank's
    // K/V tile working set. A 1 GiB run on 9Z6Z CP4/LCp16 thrashed at ~1.28 GB/rank
    // and produced 150k misses/rank with zero hits; 2 GiB immediately produced hits.
    // Keep explicit 0 opt-outs possible, but do not silently default virtual mode
    // to an ineffective no-cache/too-small path.
    def contextProjectionCacheDefaultValue = contextStoreModeValue == 'virtual-dram-stream-attention' ? '2147483648' : '0'
    def rawContextProjectionCacheByteBudgetValue = params.containsKey('bcp_context_store_projection_cache_byte_budget')
        ? params.get('bcp_context_store_projection_cache_byte_budget')
        : (params.containsKey('bcp_projection_cache_byte_budget') ? params.get('bcp_projection_cache_byte_budget') : contextProjectionCacheDefaultValue)
    def contextProjectionCacheByteBudgetValue = rawContextProjectionCacheByteBudgetValue == null ? contextProjectionCacheDefaultValue : rawContextProjectionCacheByteBudgetValue.toString().trim()
    if (!contextProjectionCacheByteBudgetValue) {
        contextProjectionCacheByteBudgetValue = contextProjectionCacheDefaultValue
    }
    def contextStoreEventLevelValue = (params.get('bcp_context_store_event_level', 'perf-summary') ?: 'perf-summary').toString().trim()
    def contextQueryTileTokens = shellQuote(contextQueryTileTokensValue)
    def contextLogicalSizeCp = shellQuote(contextLogicalSizeCpValue)
    def contextPairTileTokens = shellQuote(contextPairTileTokensValue)
    def contextKeyTileTokens = shellQuote(contextKeyTileTokensValue)
    def contextProjectionCacheByteBudget = shellQuote(contextProjectionCacheByteBudgetValue)
    def contextProjectedTriangleRhsRowReuseWindow = shellQuote(contextProjectedTriangleRhsRowReuseWindowValue)
    def contextStoreEventLevel = shellQuote(contextStoreEventLevelValue)
    def maxMsaSeqs = shellQuote(maxMsaSeqsValue)
    def maxParallelSamples = shellQuote(maxParallelSamplesValue)
    def precision = shellQuote(precisionValue)
    def triattnBackend = shellQuote(triattnBackendValue)
    def sdpaWithBiasBackend = shellQuote(sdpaWithBiasBackendValue)
    def sdpaWithBiasShardwiseBackend = shellQuote(sdpaWithBiasShardwiseBackendValue)
    def atomsPerWindowQueriesKeys = shellQuote(atomsPerWindowQueriesKeysValue)
    def contextStoreMode = shellQuote(contextStoreModeValue)
    def contextStoreRoot = shellQuote(contextStoreRootValue)
    def confidenceFlag = confidencePrediction ? '--confidence_prediction' : '--no_confidence_prediction'
    def maxMsaSeqsFlag = maxMsaSeqsValue ? "--max_msa_seqs ${maxMsaSeqsValue}" : ''
    def maxParallelSamplesFlag = maxParallelSamplesValue ? "--max_parallel_samples ${maxParallelSamplesValue}" : ''
    def precisionFlag = precisionValue ? "--precision ${precisionValue}" : ''
    def triattnBackendFlag = triattnBackendValue ? "--triattn_backend ${triattnBackendValue}" : ''
    def sdpaWithBiasBackendFlag = sdpaWithBiasBackendValue ? "--sdpa_with_bias_backend ${sdpaWithBiasBackendValue}" : ''
    def sdpaWithBiasShardwiseBackendFlag = sdpaWithBiasShardwiseBackendValue ? "--sdpa_with_bias_shardwise_backend ${sdpaWithBiasShardwiseBackendValue}" : ''
    def atomsPerWindowQueriesKeysFlag = atomsPerWindowQueriesKeysValue ? "--atoms_per_window_queries_keys ${atomsPerWindowQueriesKeysValue}" : ''
    def writeFullPaeFlag = (params.get('bcp_write_full_pae', false).toString().toLowerCase() in ['true', '1', 'yes', 'y', 'on']) ? '--write_full_pae' : ''
    def seedValue = (params.get('bcp_seed', '') ?: '').toString().trim()
    def seedFlag = seedValue ? "--seed ${seedValue}" : ''
    def useMsa = params.boltz_use_msa?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def msaProvider = (params.msa_provider ?: 'local').toString()
    def msaPreset = (params.msa_preset ?: 'fast').toString()
    def msaLocalDb = (params.msa_local_db ?: '').toString()
    def msaCacheDir = (params.msa_cache_dir ?: '').toString()
    def msaThreads = params.msa_threads ?: 32
    def msaUseGpu = !(params.msa_use_gpu?.toString()?.toLowerCase() in ['false', '0', 'no', 'off'])
    def colabfoldApiHost = (params.colabfold_api_host ?: 'https://api.colabfold.com').toString()
    def colabfoldApiMinInterval = params.colabfold_api_min_interval ?: 6
    def colabfoldApiPollInterval = params.colabfold_api_poll_interval ?: 6
    def msaMinDepthWarning = params.get('msa_min_depth_warning', 100)
    def msaMinDepthFail = params.get('msa_min_depth_fail', 0)
    def msaForceRefresh = params.msa_force_refresh?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def msaCacheOnly = params.msa_cache_only?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def inputConfigPath = shellQuote(input_config.toString())
    def repoPath = shellQuote(params.get('bcp_repo_path', ''))
    def cachePath = shellQuote(params.get('bcp_cache_path', null) ?: params.get('boltz_models', '') ?: '')
    def quotedInputFormat = shellQuote(inputFormat)
    def quotedUseMsa = shellQuote(useMsa ? 'true' : 'false')
    def codeRoot = shellQuote(params.code_root ?: '')
    def quotedMsaProvider = shellQuote(msaProvider)
    def quotedMsaPreset = shellQuote(msaPreset)
    def quotedMsaLocalDb = shellQuote(msaLocalDb)
    def quotedMsaCacheDir = shellQuote(msaCacheDir)
    def quotedMsaUseGpu = shellQuote(msaUseGpu ? 'true' : 'false')
    def quotedColabfoldApiHost = shellQuote(colabfoldApiHost)
    def quotedMsaForceRefresh = shellQuote(msaForceRefresh ? 'true' : 'false')
    def quotedMsaCacheOnly = shellQuote(msaCacheOnly ? 'true' : 'false')
    def quotedMsaUseExpand = shellQuote(params.msa_use_expand != null ? params.msa_use_expand.toString() : '')
    def quotedMsaUseEnv = shellQuote(params.msa_use_env != null ? params.msa_use_env.toString() : '')
    def quotedMsaNumIterations = shellQuote(params.msa_num_iterations ?: '')
    def quotedMsaMinSeqId = shellQuote(params.msa_min_seq_id ?: '')
    def quotedMsaMinCoverage = shellQuote(params.msa_min_coverage ?: '')
    def quotedMsaTaxonList = shellQuote(params.msa_taxon_list ?: '')
    def bundleIdLiteral = shellQuote(params.get('bcp_bundle_id', ''))
    def bundleIndexLiteral = shellQuote(params.get('bcp_bundle_index', ''))
    def planManifestLiteral = shellQuote(params.get('bcp_plan_manifest_path', ''))
    def storeRootLiteral = shellQuote(params.get('bcp_store_root', ''))
    def assignedGpuLiteral = shellQuote(params.get('bcp_assigned_gpu', ''))
    def bcpRole = (params.get('bcp_role', 'coordinator')).toString()
    def quotedBcpRole = shellQuote(bcpRole)
    def parentJobLiteral = shellQuote(params.containsKey('bcp_parent_job_id') ? params['bcp_parent_job_id'] : (params.containsKey('job_id') ? params['job_id'] : ''))
    def parentShardPlanLiteral = shellQuote(params.get('bcp_shard_plan_id', ''))
    def quotedOutputFormat = shellQuote(outputFormat)
    def ncclIbDisable = params.get('bcp_nccl_ib_disable', 1)
    def cudaAllocConf = params.get('bcp_cuda_alloc_conf', 'expandable_segments:True,max_split_size_mb:256')
    def recyclingSteps = params.get('bcp_recycling_steps', 3)
    def samplingSteps = params.get('bcp_sampling_steps', 200)
    def diffusionSamples = params.get('bcp_diffusion_samples', 1)
    """
    set -euo pipefail

    TASK_ROOT="\$PWD"
    REPO_PATH=${repoPath}
    BOLTZ_CACHE_DIR=${cachePath}
    if [ -z "\$BOLTZ_CACHE_DIR" ]; then
        BOLTZ_CACHE_DIR="\$TASK_ROOT/boltz_cache"
    fi
    if ! mkdir -p "\$BOLTZ_CACHE_DIR" 2> "\$TASK_ROOT/boltz_cache_mkdir.err"; then
        echo "configured_boltz_cache_unavailable: \$BOLTZ_CACHE_DIR; falling back to mounted/container cache if available" >&2
        cat "\$TASK_ROOT/boltz_cache_mkdir.err" >&2 || true
        if [ -d "/boltzcache" ] && [ -w "/boltzcache" ]; then
            echo "using_mounted_boltzcache_fallback: /boltzcache" >&2
            BOLTZ_CACHE_DIR="/boltzcache"
        else
            BOLTZ_CACHE_DIR="\$TASK_ROOT/boltz_cache"
            mkdir -p "\$BOLTZ_CACHE_DIR"
        fi
    fi
    rm -f "\$TASK_ROOT/boltz_cache_mkdir.err"
    export BOLTZ_CACHE_DIR
    BCP_ROLE=${quotedBcpRole}
    BCP_STORE_ROOT=${storeRootLiteral}
    BCP_ASSIGNED_GPU=${assignedGpuLiteral}
    BCP_BUNDLE_ID=${bundleIdLiteral}
    GPU_IDS_RAW=${gpuIdsRaw}
    SIZE_CP=${sizeCp}
    NPROC=${nproc}
    SIZE_DP=${sizeDp}
    INPUT_FORMAT=${quotedInputFormat}
    BCP_CONFIDENCE_PREDICTION=${quotedConfidencePrediction}
    BCP_MAX_MSA_SEQS=${maxMsaSeqs}
    BCP_MAX_PARALLEL_SAMPLES=${maxParallelSamples}
    BCP_PRECISION=${precision}
    BCP_TRIATTN_BACKEND=${triattnBackend}
    BCP_SDPA_WITH_BIAS_BACKEND=${sdpaWithBiasBackend}
    BCP_SDPA_WITH_BIAS_SHARDWISE_BACKEND=${sdpaWithBiasShardwiseBackend}
    BCP_ATOMS_PER_WINDOW_QUERIES_KEYS=${atomsPerWindowQueriesKeys}
    BCP_DATA_PLANE_SEMANTICS=torch.distributed_dtensor_context_parallel
    BCP_CONTEXT_STORE_MODE=${contextStoreMode}
    BCP_CONTEXT_STORE_ROOT=${contextStoreRoot}
    BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}
    BCP_CONTEXT_STORE_LOGICAL_SIZE_CP=${contextLogicalSizeCp}
    BCP_CONTEXT_STORE_PAIR_TILE_TOKENS=${contextPairTileTokens}
    BCP_CONTEXT_STORE_KEY_TILE_TOKENS=${contextKeyTileTokens}
    BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET=${contextProjectionCacheByteBudget}
    BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW=${contextProjectedTriangleRhsRowReuseWindow}
    BCP_CONTEXT_STORE_EVENT_LEVEL=${contextStoreEventLevel}
    if [ -z "\$BCP_CONTEXT_STORE_EVENT_LEVEL" ]; then
        BCP_CONTEXT_STORE_EVENT_LEVEL=perf-summary
    fi
    if [ -z "\$BCP_CONTEXT_STORE_ROOT" ]; then
        BCP_CONTEXT_STORE_ROOT="\$TASK_ROOT/true_cp_context_store"
    fi
    if [[ "\$BCP_CONTEXT_STORE_MODE" == virtual-dram-stream-attention ]]; then
        BCP_CONTEXT_STORE_SEMANTICS=torch.distributed_dtensor_pairformer_virtual_dram_stream_attention_and_projected_triangle_multiplication
    elif [[ "\$BCP_CONTEXT_STORE_MODE" == rank-local-dram-spill* ]] && [ -n "\$BCP_CONTEXT_QUERY_TILE_TOKENS" ]; then
        BCP_CONTEXT_STORE_SEMANTICS=torch.distributed_dtensor_pairformer_rank_local_dram_spill_and_reference_triangle_attention_query_tiling
    elif [[ "\$BCP_CONTEXT_STORE_MODE" == rank-local-dram-spill* ]]; then
        BCP_CONTEXT_STORE_SEMANTICS=torch.distributed_dtensor_pairformer_rank_local_dram_spill
    elif [ -n "\$BCP_CONTEXT_QUERY_TILE_TOKENS" ]; then
        BCP_CONTEXT_STORE_SEMANTICS=torch.distributed_dtensor_pairformer_reference_triangle_attention_query_tiling
    else
        BCP_CONTEXT_STORE_SEMANTICS=torch.distributed_dtensor_pairformer_context_store_evidence
    fi
    export BCP_CONFIDENCE_PREDICTION BCP_MAX_MSA_SEQS BCP_MAX_PARALLEL_SAMPLES BCP_PRECISION BCP_SAMPLING_STEPS
    export BCP_TRIATTN_BACKEND BCP_SDPA_WITH_BIAS_BACKEND BCP_SDPA_WITH_BIAS_SHARDWISE_BACKEND
    export BCP_ATOMS_PER_WINDOW_QUERIES_KEYS BCP_DATA_PLANE_SEMANTICS BCP_CONTEXT_STORE_MODE BCP_CONTEXT_STORE_ROOT BCP_CONTEXT_STORE_SEMANTICS BCP_CONTEXT_QUERY_TILE_TOKENS
    export BCP_CONTEXT_STORE_LOGICAL_SIZE_CP BCP_CONTEXT_STORE_PAIR_TILE_TOKENS BCP_CONTEXT_STORE_KEY_TILE_TOKENS BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW BCP_CONTEXT_STORE_EVENT_LEVEL
    USE_MSA=${quotedUseMsa}
    CODE_ROOT=${codeRoot}
    MSA_PROVIDER=${quotedMsaProvider}
    MSA_PRESET=${quotedMsaPreset}
    MSA_LOCAL_DB=${quotedMsaLocalDb}
    MSA_CACHE_DIR=${quotedMsaCacheDir}
    MSA_THREADS=${msaThreads}
    MSA_USE_GPU=${quotedMsaUseGpu}
    COLABFOLD_API_HOST=${quotedColabfoldApiHost}
    COLABFOLD_API_MIN_INTERVAL=${colabfoldApiMinInterval}
    COLABFOLD_API_POLL_INTERVAL=${colabfoldApiPollInterval}
    MSA_MIN_DEPTH_WARNING=${msaMinDepthWarning}
    MSA_MIN_DEPTH_FAIL=${msaMinDepthFail}
    MSA_FORCE_REFRESH=${quotedMsaForceRefresh}
    MSA_CACHE_ONLY=${quotedMsaCacheOnly}
    MSA_USE_EXPAND=${quotedMsaUseExpand}
    MSA_USE_ENV=${quotedMsaUseEnv}
    MSA_NUM_ITERATIONS=${quotedMsaNumIterations}
    MSA_MIN_SEQ_ID=${quotedMsaMinSeqId}
    MSA_MIN_COVERAGE=${quotedMsaMinCoverage}
    MSA_TAXON_LIST=${quotedMsaTaxonList}
    OUTPUT_FORMAT=${quotedOutputFormat}
    BCP_SAMPLING_STEPS=${samplingSteps}

    if [ ! -d "\$REPO_PATH" ]; then
        echo "Boltz-CP repo not found: \$REPO_PATH" >&2
        exit 1
    fi

    mkdir -p staged_input
    if [ -d ${inputConfigPath} ]; then
        cp -R ${inputConfigPath} staged_input/input_bundle
        DATA_ARG="\$TASK_ROOT/staged_input/input_bundle"
    else
        staged_file="\$(basename ${inputConfigPath})"
        cp ${inputConfigPath} "staged_input/\$staged_file"
        DATA_ARG="\$TASK_ROOT/staged_input/\$staged_file"
    fi

    mkdir -p "\$TASK_ROOT/tmp_home" "\$TASK_ROOT/tmp_cache"
    export HOME="\$TASK_ROOT/tmp_home"
    export XDG_CACHE_HOME="\$TASK_ROOT/tmp_cache"
    export NUMBA_CACHE_DIR="\$TASK_ROOT/tmp_cache/numba"
    export TRITON_CACHE_DIR="\$TASK_ROOT/tmp_cache/triton"
    export MPLCONFIGDIR="\$TASK_ROOT/tmp_cache/matplotlib"
    export BOLTZ_CACHE="\$BOLTZ_CACHE_DIR"
    export PYTHONPATH="\$REPO_PATH/src\${PYTHONPATH:+:\$PYTHONPATH}"
    BOLTZ_PYTHON="\$REPO_PATH/.venv/bin/python"
    if [ ! -x "\$BOLTZ_PYTHON" ]; then
        BOLTZ_PYTHON=python3
    fi
    export BOLTZ_PYTHON
    export CUDA_DEVICE_ORDER=PCI_BUS_ID
    if [ -n "\$GPU_IDS_RAW" ]; then
        export CUDA_VISIBLE_DEVICES="\$GPU_IDS_RAW"
    fi
    export NCCL_IB_DISABLE=${ncclIbDisable}
    export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
    export PYTORCH_CUDA_ALLOC_CONF="${cudaAllocConf}"

    if [ "\$BCP_ROLE" = "child" ]; then
        if [ -z "\$BCP_STORE_ROOT" ]; then
            echo "bcp_store_root is required for child Boltz-CP worker execution" >&2
            exit 1
        fi
        if [ -z "\$BCP_BUNDLE_ID" ]; then
            echo "bcp_bundle_id is required for child Boltz-CP worker execution" >&2
            exit 1
        fi

        assigned_gpu="\$BCP_ASSIGNED_GPU"
        if [ -z "\$assigned_gpu" ] && [ -n "${gpuIds ? gpuIds[0] : ''}" ]; then
            assigned_gpu="${gpuIds ? gpuIds[0] : ''}"
        fi

        mkdir -p cp_results/processed
        cd "\$REPO_PATH"
        set +e
        if [ -n "\$assigned_gpu" ]; then
            "\$BOLTZ_PYTHON" -m boltz.distributed.main large-protein run-bundle \
                --store-root "\$BCP_STORE_ROOT" \
                --bundle-id "\$BCP_BUNDLE_ID" \
                --assigned-gpu "\$assigned_gpu" \
                > "\$TASK_ROOT/run_bundle_output.txt" 2>&1
        else
            "\$BOLTZ_PYTHON" -m boltz.distributed.main large-protein run-bundle \
                --store-root "\$BCP_STORE_ROOT" \
                --bundle-id "\$BCP_BUNDLE_ID" \
                > "\$TASK_ROOT/run_bundle_output.txt" 2>&1
        fi
        worker_rc=\$?
        set -e

        tee "\$TASK_ROOT/boltz_cp_experimental.log" < "\$TASK_ROOT/run_bundle_output.txt"

        export TASK_ROOT BCP_STORE_ROOT BCP_BUNDLE_ID
        export PLAN_MANIFEST_PATH=${planManifestLiteral}
        export PARENT_JOB_ID=${parentJobLiteral}
        export PARENT_SHARD_PLAN_ID=${parentShardPlanLiteral}
        export ASSIGNED_GPU="\$assigned_gpu"
        python3 - <<'PY'
import json
import os
from pathlib import Path

store_root = Path(os.environ['BCP_STORE_ROOT'])
bundle_id = os.environ['BCP_BUNDLE_ID']
processed_dir = Path(os.environ['TASK_ROOT']) / 'cp_results' / 'processed'
processed_dir.mkdir(parents=True, exist_ok=True)
bundle_dir = store_root / 'bundles' / bundle_id
result_path = bundle_dir / 'result.json'
failure_path = bundle_dir / 'failure.json'
marker_path = store_root / 'markers' / f'{bundle_id}.done'
manifest = {
    'bundle_id': bundle_id,
    'store_root': str(store_root),
    'plan_manifest_path': os.environ.get('PLAN_MANIFEST_PATH', '').strip(),
    'parent_job_id': os.environ.get('PARENT_JOB_ID', '').strip(),
    'parent_shard_plan_id': os.environ.get('PARENT_SHARD_PLAN_ID', '').strip(),
    'assigned_gpu': os.environ.get('ASSIGNED_GPU', '').strip(),
    'result_path': str(result_path),
    'failure_path': str(failure_path),
    'marker_path': str(marker_path),
    'status': 'failed' if failure_path.exists() else ('complete' if result_path.exists() and marker_path.exists() else 'unknown'),
}
if result_path.exists():
    (processed_dir / 'result.json').write_text(result_path.read_text(encoding='utf-8'), encoding='utf-8')
if failure_path.exists():
    (processed_dir / 'failure.json').write_text(failure_path.read_text(encoding='utf-8'), encoding='utf-8')
if marker_path.exists():
    (processed_dir / 'marker.json').write_text(marker_path.read_text(encoding='utf-8'), encoding='utf-8')
(processed_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
PY

        if [ \$worker_rc -ne 0 ]; then
            exit \$worker_rc
        fi
    else
        if [ \$((SIZE_CP)) -le 0 ]; then
            echo "bcp_size_cp must be a positive integer" >&2
            exit 1
        fi

        size_cp_axis=\$(python3 - <<'PY'
import math
print(math.isqrt(int("${sizeCp}")))
PY
)
        if [ \$((size_cp_axis * size_cp_axis)) -ne \$SIZE_CP ]; then
            echo "bcp_size_cp must be a perfect square" >&2
            exit 1
        fi

        if [ \$((NPROC % SIZE_CP)) -ne 0 ]; then
            echo "bcp_size_cp must divide the number of selected GPUs" >&2
            exit 1
        fi

        if [ "\$USE_MSA" = "true" ] && [ "\$INPUT_FORMAT" = "config_files" ]; then
            if [ -z "\$CODE_ROOT" ] || [ ! -f "\$CODE_ROOT/scripts/run_local_msa.py" ]; then
                echo "Boltz-CP MSA materialization requires \$CODE_ROOT/scripts/run_local_msa.py" >&2
                exit 1
            fi
            echo "Materializing MSA-enabled Boltz-CP input bundles via run_local_msa.py..."
            # Keep these flags aligned with structure_prediction.nf, including the --msa-provider value sourced from MSA_PROVIDER.
            export TASK_ROOT CODE_ROOT DATA_ARG
            export MSA_PROVIDER MSA_PRESET MSA_LOCAL_DB MSA_CACHE_DIR MSA_THREADS MSA_USE_GPU
            export COLABFOLD_API_HOST COLABFOLD_API_MIN_INTERVAL COLABFOLD_API_POLL_INTERVAL
            export MSA_MIN_DEPTH_WARNING MSA_MIN_DEPTH_FAIL MSA_FORCE_REFRESH MSA_CACHE_ONLY
            export MSA_USE_EXPAND MSA_USE_ENV MSA_NUM_ITERATIONS MSA_MIN_SEQ_ID MSA_MIN_COVERAGE MSA_TAXON_LIST
            DATA_ARG="\$(python3 - <<'PY'
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


task_root = Path(os.environ["TASK_ROOT"])
source = Path(os.environ["DATA_ARG"])
script_path = Path(os.environ["CODE_ROOT"]) / "scripts" / "run_local_msa.py"
msa_out_dir = task_root / "msa"
msa_out_dir.mkdir(parents=True, exist_ok=True)


def add_optional(cmd: list[str], flag: str, env_name: str) -> None:
    value = os.environ.get(env_name, "").strip()
    if value:
        cmd.extend([flag, value])


msa_by_sequence: dict[str, str] = {}


def materialize_yaml(yaml_path: Path, name_prefix: str) -> None:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    sequences = payload.get("sequences") or []
    for index, entry in enumerate(sequences, start=1):
        if not isinstance(entry, dict):
            continue
        protein = entry.get("protein")
        if not isinstance(protein, dict):
            continue
        sequence = str(protein.get("sequence") or "").strip()
        if not sequence:
            raise SystemExit(f"Boltz-CP protein entry {index} in {yaml_path} is missing a sequence")
        existing_msa = str(protein.get("msa") or "").strip()
        canonical_msa = msa_by_sequence.get(sequence)
        if existing_msa and existing_msa.lower() != "empty":
            if canonical_msa and canonical_msa != existing_msa:
                protein["msa"] = canonical_msa
            else:
                msa_by_sequence.setdefault(sequence, existing_msa)
            continue
        if canonical_msa:
            protein["msa"] = canonical_msa
            continue
        component_id = protein.get("id")
        if isinstance(component_id, list) and component_id:
            chain_id = str(component_id[0]).strip() or f"chain{index}"
        else:
            chain_id = str(component_id or f"chain{index}").strip() or f"chain{index}"
        msa_name = f"{name_prefix}_{chain_id}"
        cmd = [
            sys.executable,
            str(script_path),
            "--sequence",
            sequence,
            "--name",
            msa_name,
            "--out_dir",
            str(msa_out_dir),
            "--threads",
            os.environ.get("MSA_THREADS", "32"),
            "--msa-provider",
            os.environ.get("MSA_PROVIDER", "local"),
            "--colabfold-api-host",
            os.environ.get("COLABFOLD_API_HOST", "https://api.colabfold.com"),
            "--colabfold-api-min-interval",
            os.environ.get("COLABFOLD_API_MIN_INTERVAL", "6"),
            "--colabfold-api-poll-interval",
            os.environ.get("COLABFOLD_API_POLL_INTERVAL", "6"),
            "--preset",
            os.environ.get("MSA_PRESET", "fast"),
            "--min-depth-warning",
            os.environ.get("MSA_MIN_DEPTH_WARNING", "100"),
            "--min-depth-fail",
            os.environ.get("MSA_MIN_DEPTH_FAIL", "0"),
        ]
        add_optional(cmd, "--db_path", "MSA_LOCAL_DB")
        add_optional(cmd, "--cache_dir", "MSA_CACHE_DIR")
        add_optional(cmd, "--num-iterations", "MSA_NUM_ITERATIONS")
        add_optional(cmd, "--min-seq-id", "MSA_MIN_SEQ_ID")
        add_optional(cmd, "--min-coverage", "MSA_MIN_COVERAGE")
        add_optional(cmd, "--taxon-list", "MSA_TAXON_LIST")

        msa_use_expand = os.environ.get("MSA_USE_EXPAND", "").strip().lower()
        if msa_use_expand in {"true", "false"}:
            cmd.extend(["--use-expand", "1" if msa_use_expand == "true" else "0"])
        msa_use_env = os.environ.get("MSA_USE_ENV", "").strip().lower()
        if msa_use_env in {"true", "false"}:
            cmd.extend(["--use-env", "1" if msa_use_env == "true" else "0"])
        if os.environ.get("MSA_USE_GPU", "true").strip().lower() in {"false", "0", "no", "off"}:
            cmd.append("--cpu-only")
        if os.environ.get("MSA_FORCE_REFRESH", "").strip().lower() == "true":
            cmd.append("--force_refresh")
        if os.environ.get("MSA_CACHE_ONLY", "").strip().lower() == "true":
            cmd.append("--cache-only")

        subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
        msa_path = msa_out_dir / f'{msa_name}.a3m'
        if not msa_path.exists():
            raise SystemExit(f'run_local_msa.py did not produce expected file {msa_path}')
        canonical_msa = str(msa_path.resolve())
        msa_by_sequence[sequence] = canonical_msa
        protein["msa"] = canonical_msa

    yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


if source.is_dir():
    target_root = task_root / "staged_input_with_msa" / "input_bundle"
    if target_root.exists():
        shutil.rmtree(target_root)
    shutil.copytree(source, target_root)
    yaml_files = sorted(
        path for path in target_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )
    if not yaml_files:
        raise SystemExit(f"No YAML files found under {source} for MSA materialization")
    for yaml_path in yaml_files:
        materialize_yaml(yaml_path, yaml_path.stem)
    print(str(target_root))
else:
    target_root = task_root / "staged_input_with_msa"
    target_root.mkdir(parents=True, exist_ok=True)
    target_path = target_root / source.name
    shutil.copy2(source, target_path)
    if target_path.suffix.lower() not in {".yaml", ".yml"}:
        raise SystemExit(f"Boltz-CP MSA materialization currently expects YAML inputs, got {target_path.name}")
    materialize_yaml(target_path, target_path.stem)
    print(str(target_path))
PY
)"
        fi

        echo "runtime-parameter-preflight: checking BCP_SAMPLING_STEPS=\$BCP_SAMPLING_STEPS"
        export TASK_ROOT BCP_SAMPLING_STEPS BCP_DATA_PLANE_SEMANTICS
        python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

raw_steps = os.environ.get("BCP_SAMPLING_STEPS", "")
try:
    sampling_steps = int(raw_steps)
except (TypeError, ValueError):
    sampling_steps = -1
if sampling_steps < 2:
    task_root = Path(os.environ["TASK_ROOT"])
    payload = {
        "status": "failed",
        "stage": "runtime-parameter-preflight",
        "error_type": "InvalidBoltzSamplingSteps",
        "sampling_steps": raw_steps,
        "minimum_sampling_steps": 2,
        "error": f"Need at least 2 sampling steps, got {raw_steps}",
        "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
        "is_true_distributed_context_parallel": True,
        "recommendation": "Set bcp_sampling_steps to 2 or higher before launching true distributed CP.",
    }
    (task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"InvalidBoltzSamplingSteps: Need at least 2 sampling steps, got {raw_steps}", file=sys.stderr)
    sys.exit(87)
print(f"runtime-parameter-preflight: sampling_steps={sampling_steps} passed")
PY

        echo "triattn-backend-preflight: checking BCP_TRIATTN_BACKEND=\$BCP_TRIATTN_BACKEND"
        export TASK_ROOT BCP_TRIATTN_BACKEND BCP_DATA_PLANE_SEMANTICS
        set +e
        "\$BOLTZ_PYTHON" - <<'PY'
import importlib.util
import json
import os
import sys
from pathlib import Path

backend = os.environ.get("BCP_TRIATTN_BACKEND", "reference").strip().lower() or "reference"
supported_backends = {"reference", "trifast", "cueq"}
if backend not in supported_backends:
    task_root = Path(os.environ["TASK_ROOT"])
    payload = {
        "status": "failed",
        "stage": "triattn-backend-preflight",
        "error_type": "UnsupportedTriangleAttentionBackend",
        "triattn_backend": backend,
        "supported_backends": sorted(supported_backends),
        "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
        "is_true_distributed_context_parallel": True,
        "recommendation": "Use bcp_triattn_backend=reference, trifast, or cueq. Non-reference kernels still require their packages to be installed.",
    }
    (task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"UnsupportedTriangleAttentionBackend: triattn_backend={backend!r} supported backends: {', '.join(sorted(supported_backends))}",
        file=sys.stderr,
    )
    sys.exit(85)
requirements = {
    "trifast": ["trifast"],
    "cueq": ["cuequivariance", "cuequivariance_torch"],
}
missing = [name for name in requirements.get(backend, []) if importlib.util.find_spec(name) is None]
if missing:
    task_root = Path(os.environ["TASK_ROOT"])
    payload = {
        "status": "failed",
        "stage": "triattn-backend-preflight",
        "error_type": "MissingTriangleAttentionBackend",
        "triattn_backend": backend,
        "missing_modules": missing,
        "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
        "is_true_distributed_context_parallel": True,
        "recommendation": "Use bcp_triattn_backend=reference or install the requested triangle-attention kernel package before launching true distributed CP.",
    }
    (task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"MissingTriangleAttentionBackend: triattn_backend={backend!r} missing modules: {', '.join(missing)}",
        file=sys.stderr,
    )
    sys.exit(86)
print(f"triattn-backend-preflight: triattn_backend={backend!r} dependency check passed")
PY
        preflight_rc=\$?
        set -e
        if [ \$preflight_rc -ne 0 ]; then
            exit \$preflight_rc
        fi

        export TASK_ROOT DATA_ARG REPO_PATH GPU_IDS_RAW SIZE_CP SIZE_DP NPROC INPUT_FORMAT OUTPUT_FORMAT
        python3 - <<'PY'
import json
import os
from pathlib import Path

context_store_mode = os.environ.get("BCP_CONTEXT_STORE_MODE", "").strip()
context_store_spill_enabled = context_store_mode.startswith("rank-local-dram-spill")
context_virtual_streaming_enabled = context_store_mode == "virtual-dram-stream-attention"
context_triangle_query_tile_enabled = bool(os.environ.get("BCP_CONTEXT_QUERY_TILE_TOKENS", "").strip())
if context_virtual_streaming_enabled:
    memory_reduction_scope = "rank_local_virtual_dram_streaming_for_triangle_attention_and_projected_triangle_multiplication"
elif context_store_mode == "rank-local-dram-spill-layer":
    memory_reduction_scope = "rank_local_between_pairformer_layers_only"
elif context_store_mode == "rank-local-dram-spill-op":
    memory_reduction_scope = "rank_local_between_pairformer_operations_only"
elif context_triangle_query_tile_enabled:
    memory_reduction_scope = "within_operation_reference_triangle_attention_query_tiling"
else:
    memory_reduction_scope = None

payload = {
    "backend": "true-distributed-context-parallel",
    "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
    "launcher": "torch.distributed.run",
    "boltz_python": os.environ.get("BOLTZ_PYTHON", ""),
    "repo_path": os.environ.get("REPO_PATH", ""),
    "data_arg": os.environ.get("DATA_ARG", ""),
    "task_root": os.environ.get("TASK_ROOT", ""),
    "boltz_cache_dir": os.environ.get("BOLTZ_CACHE_DIR", ""),
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
    "gpu_ids_raw": os.environ.get("GPU_IDS_RAW", ""),
    "nproc_per_node": os.environ.get("NPROC", ""),
    "size_dp": os.environ.get("SIZE_DP", ""),
    "size_cp": os.environ.get("SIZE_CP", ""),
    "input_format": os.environ.get("INPUT_FORMAT", ""),
    "output_format": os.environ.get("OUTPUT_FORMAT", ""),
    "confidence_prediction": os.environ.get("BCP_CONFIDENCE_PREDICTION", ""),
    "max_msa_seqs": os.environ.get("BCP_MAX_MSA_SEQS", ""),
    "max_parallel_samples": os.environ.get("BCP_MAX_PARALLEL_SAMPLES", ""),
    "precision": os.environ.get("BCP_PRECISION", ""),
    "sampling_steps": os.environ.get("BCP_SAMPLING_STEPS", ""),
    "triattn_backend": os.environ.get("BCP_TRIATTN_BACKEND", ""),
    "context_store_mode": os.environ.get("BCP_CONTEXT_STORE_MODE", ""),
    "context_store_root": os.environ.get("BCP_CONTEXT_STORE_ROOT", ""),
    "context_store_semantics": os.environ.get("BCP_CONTEXT_STORE_SEMANTICS", ""),
    "triangle_attention_query_tile_tokens": os.environ.get("BCP_CONTEXT_QUERY_TILE_TOKENS", "").strip(),
    "context_store_logical_size_cp": os.environ.get("BCP_CONTEXT_STORE_LOGICAL_SIZE_CP", "").strip(),
    "context_store_pair_tile_tokens": os.environ.get("BCP_CONTEXT_STORE_PAIR_TILE_TOKENS", "").strip(),
    "context_store_key_tile_tokens": os.environ.get("BCP_CONTEXT_STORE_KEY_TILE_TOKENS", "").strip(),
    "context_store_projection_cache_byte_budget": os.environ.get("BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET", "").strip(),
    "context_store_projected_triangle_rhs_row_reuse_window": os.environ.get("BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW", "").strip(),
    "context_store_event_level": os.environ.get("BCP_CONTEXT_STORE_EVENT_LEVEL", "").strip(),
    "context_store_truth": {
        "predictor_owned": True,
        "evidence_source": "Boltz2Distributed/PairformerModule.forward",
        "shared_cache_serial_prediction": False,
        "output_tiling_only": False,
        "streaming_spill_enabled": context_store_spill_enabled,
        "virtual_streaming_enabled": context_virtual_streaming_enabled,
        "within_operation_memory_reduction_claimed": context_virtual_streaming_enabled or context_triangle_query_tile_enabled,
        "projection_cache_enabled": os.environ.get("BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET", "0").strip() not in {"", "0"},
        "projection_cache_byte_budget": os.environ.get("BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET", "0").strip(),
        "projected_triangle_rhs_row_reuse_window": os.environ.get("BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW", "").strip() or "2",
        "memory_reduction_claimed": context_virtual_streaming_enabled or context_store_spill_enabled or context_triangle_query_tile_enabled,
        "memory_reduction_scope": memory_reduction_scope,
        "limitations": {
            "within_op_peak_not_reduced": context_store_spill_enabled and not context_triangle_query_tile_enabled and not context_virtual_streaming_enabled,
            "triangle_attention_not_tiled_by_this_mode": context_store_spill_enabled and not context_triangle_query_tile_enabled and not context_virtual_streaming_enabled,
            "triangle_multiplication_not_tiled_by_this_mode": context_store_spill_enabled and not context_virtual_streaming_enabled,
            "model_parameters_not_spilled": context_store_spill_enabled,
            "msa_and_diffusion_state_not_spilled": context_store_spill_enabled,
        },
    },
    "sdpa_with_bias_backend": os.environ.get("BCP_SDPA_WITH_BIAS_BACKEND", ""),
    "sdpa_with_bias_shardwise_backend": os.environ.get("BCP_SDPA_WITH_BIAS_SHARDWISE_BACKEND", ""),
    "atoms_per_window_queries_keys": os.environ.get("BCP_ATOMS_PER_WINDOW_QUERIES_KEYS", ""),
    "rank_probe_path": str(Path(os.environ["TASK_ROOT"]) / "true_cp_rank_probe.jsonl"),
}
Path(os.environ["TASK_ROOT"]) .joinpath("true_cp_launch_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY

        cat > "\$TASK_ROOT/true_cp_rank_probe.py" <<'PY'
import json
import os
import socket
import sys

payload = {
    "rank": os.environ.get("RANK", ""),
    "local_rank": os.environ.get("LOCAL_RANK", ""),
    "world_size": os.environ.get("WORLD_SIZE", ""),
    "local_world_size": os.environ.get("LOCAL_WORLD_SIZE", ""),
    "master_addr": os.environ.get("MASTER_ADDR", ""),
    "master_port": os.environ.get("MASTER_PORT", ""),
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
    "hostname": socket.gethostname(),
}
probe_path = os.environ["TRUE_CP_RANK_PROBE_PATH"]
with open(probe_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, sort_keys=True) + "\\n")
print("true_cp_rank_probe: " + json.dumps(payload, sort_keys=True), flush=True)
sys.exit(0)
PY
        TRUE_CP_RANK_PROBE_PATH="\$TASK_ROOT/true_cp_rank_probe.jsonl"
        export TRUE_CP_RANK_PROBE_PATH
        rm -f "\$TRUE_CP_RANK_PROBE_PATH"

        cd "\$REPO_PATH"
        set +e
        "\$BOLTZ_PYTHON" -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node \$NPROC \
            "\$TASK_ROOT/true_cp_rank_probe.py" \
            2>&1 | tee "\$TASK_ROOT/true_cp_rank_probe.log"
        rank_probe_rc=\${PIPESTATUS[0]}
        set -e
        if [ \$rank_probe_rc -ne 0 ]; then
            export TRUE_CP_RANK_PROBE_RC="\$rank_probe_rc"
            python3 - <<'PY'
import json
import os
from pathlib import Path

task_root = Path(os.environ["TASK_ROOT"])
payload = {
    "status": "failed",
    "stage": "torch.distributed.run rank-probe",
    "error_type": "TrueCPRankProbeFailed",
    "exit_code": int(os.environ.get("TRUE_CP_RANK_PROBE_RC", "-1")),
    "rank_probe_log_path": str(task_root / "true_cp_rank_probe.log"),
    "rank_probe_path": str(task_root / "true_cp_rank_probe.jsonl"),
    "launch_manifest_path": str(task_root / "true_cp_launch_manifest.json"),
    "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
    "is_true_distributed_context_parallel": True,
}
(task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
            exit \$rank_probe_rc
        fi

        triangle_query_tile_flag=()
        if [ -n "\$BCP_CONTEXT_QUERY_TILE_TOKENS" ]; then
            triangle_query_tile_flag=(--context_store_triangle_attention_query_tile_tokens "\$BCP_CONTEXT_QUERY_TILE_TOKENS")
        fi
        context_logical_size_cp_flag=()
        if [ -n "\$BCP_CONTEXT_STORE_LOGICAL_SIZE_CP" ]; then
            context_logical_size_cp_flag=(--context_store_logical_size_cp "\$BCP_CONTEXT_STORE_LOGICAL_SIZE_CP")
        fi
        context_pair_tile_tokens_flag=()
        if [ -n "\$BCP_CONTEXT_STORE_PAIR_TILE_TOKENS" ]; then
            context_pair_tile_tokens_flag=(--context_store_pair_tile_tokens "\$BCP_CONTEXT_STORE_PAIR_TILE_TOKENS")
        fi
        context_key_tile_tokens_flag=()
        if [ -n "\$BCP_CONTEXT_STORE_KEY_TILE_TOKENS" ]; then
            context_key_tile_tokens_flag=(--context_store_key_tile_tokens "\$BCP_CONTEXT_STORE_KEY_TILE_TOKENS")
        fi
        context_projection_cache_flag=()
        if [ -n "\$BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET" ]; then
            context_projection_cache_flag=(--context_store_projection_cache_byte_budget "\$BCP_CONTEXT_STORE_PROJECTION_CACHE_BYTE_BUDGET")
        fi
        context_projected_triangle_rhs_reuse_window_flag=()
        if [ -n "\$BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW" ]; then
            context_projected_triangle_rhs_reuse_window_flag=(--context_store_projected_triangle_rhs_row_reuse_window "\$BCP_CONTEXT_STORE_PROJECTED_TRIANGLE_RHS_ROW_REUSE_WINDOW")
        fi

        set +e
        "\$BOLTZ_PYTHON" -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node \$NPROC \
            src/boltz/distributed/main.py predict "\$DATA_ARG" \
            --out_dir "\$TASK_ROOT" \
            --cache "\$BOLTZ_CACHE_DIR" \
            --size_dp \$SIZE_DP \
            --size_cp \$SIZE_CP \
            --input_format "\$INPUT_FORMAT" \
            --output_format "\$OUTPUT_FORMAT" \
            --context_store_root "\$BCP_CONTEXT_STORE_ROOT" \
            --context_store_mode "\$BCP_CONTEXT_STORE_MODE" \
            --context_store_event_level "\$BCP_CONTEXT_STORE_EVENT_LEVEL" \
            "\${triangle_query_tile_flag[@]}" \
            "\${context_logical_size_cp_flag[@]}" \
            "\${context_pair_tile_tokens_flag[@]}" \
            "\${context_key_tile_tokens_flag[@]}" \
            "\${context_projection_cache_flag[@]}" \
            "\${context_projected_triangle_rhs_reuse_window_flag[@]}" \
            --recycling_steps ${recyclingSteps} \
            --sampling_steps ${samplingSteps} \
            --diffusion_samples ${diffusionSamples} \
            ${maxParallelSamplesFlag} \
            ${maxMsaSeqsFlag} \
            ${precisionFlag} \
            ${triattnBackendFlag} \
            ${sdpaWithBiasBackendFlag} \
            ${sdpaWithBiasShardwiseBackendFlag} \
            ${atomsPerWindowQueriesKeysFlag} \
            ${confidenceFlag} \
            ${writeFullPaeFlag} \
            ${seedFlag} \
            2>&1 | tee "\$TASK_ROOT/boltz_cp_experimental.log"
        run_rc=\${PIPESTATUS[0]}
        set -e
        if [ \$run_rc -ne 0 ]; then
            export TRUE_CP_RUN_RC="\$run_rc"
            python3 - <<'PY'
import json
import os
from pathlib import Path

task_root = Path(os.environ["TASK_ROOT"])
payload = {
    "status": "failed",
    "stage": "torch.distributed.run predict",
    "exit_code": int(os.environ.get("TRUE_CP_RUN_RC", "-1")),
    "log_path": str(task_root / "boltz_cp_experimental.log"),
    "launch_manifest_path": str(task_root / "true_cp_launch_manifest.json"),
    "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
}
(task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
            exit \$run_rc
        fi

        result_dir="\$(find "\$TASK_ROOT" -maxdepth 1 -type d -name 'boltz_results_*' | head -n 1)"
        if [ -z "\$result_dir" ]; then
            echo "Boltz-CP run did not produce a boltz_results_* directory" >&2
            python3 - <<'PY'
import json
import os
from pathlib import Path

task_root = Path(os.environ["TASK_ROOT"])
payload = {
    "status": "failed",
    "stage": "result-discovery",
    "exit_code": 1,
    "reason": "Boltz-CP run did not produce a boltz_results_* directory",
    "log_path": str(task_root / "boltz_cp_experimental.log"),
    "launch_manifest_path": str(task_root / "true_cp_launch_manifest.json"),
    "data_plane_semantics": os.environ.get("BCP_DATA_PLANE_SEMANTICS", ""),
}
(task_root / "true_cp_failure_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
            exit 1
        fi
        mv "\$result_dir" "\$TASK_ROOT/cp_results"

        python3 - <<'PY'
import json
from pathlib import Path

processed_dir = Path("\$TASK_ROOT/cp_results/processed")
processed_dir.mkdir(parents=True, exist_ok=True)
manifest_path = processed_dir / "manifest.json"
if manifest_path.exists():
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}
else:
    payload = {}

bundle_id = ${bundleIdLiteral}
bundle_index = ${bundleIndexLiteral}
plan_manifest_path = ${planManifestLiteral}
parent_job_id = ${parentJobLiteral}
parent_shard_plan_id = ${parentShardPlanLiteral}
if bundle_id:
    payload["bundle_id"] = bundle_id
if bundle_index:
    payload["bundle_index"] = bundle_index
if plan_manifest_path:
    payload["plan_manifest_path"] = plan_manifest_path
if parent_job_id:
    payload["parent_job_id"] = parent_job_id
if parent_shard_plan_id:
    payload["parent_shard_plan_id"] = parent_shard_plan_id
manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
    fi
    """

    stub:
    """
    mkdir -p cp_results/predictions_dp0_cp0/sample_0001 cp_results/processed
    cat > cp_results/predictions_dp0_cp0/sample_0001/sample_0001_model_0.cif <<'EOF'
data_sample_0001
#
EOF
    cat > cp_results/predictions_dp0_cp0/sample_0001/sample_0001_model_0.pdb <<'EOF'
ATOM      1  N   GLY A   1      11.104  13.207   9.447  1.00 20.00           N
END
EOF
    cat > cp_results/predictions_dp0_cp0/confidence_sample_0001_model_0.json <<'EOF'
{"design_id":"sample_0001_model_0","source_model":"boltz_cp_experimental","confidence":0.91}
EOF
    cat > cp_results/processed/manifest.json <<'EOF'
{"inputs":["sample_0001"],"bundle_id":"${params.get('bcp_bundle_id', 'sample_0001')}","plan_manifest_path":"${params.get('bcp_plan_manifest_path', '')}","parent_shard_plan_id":"${params.get('bcp_shard_plan_id', '')}"}
EOF
    echo "Boltz-CP stub run" > boltz_cp_experimental.log
    """
}

process BuildBoltzCPPlanManifest {
    label 'process_low'

    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: '*.json'

    input:
    val parent_job_id
    val batch_name
    path input_config

    output:
    path 'boltz_cp_plan_manifest.json', emit: manifest
    path 'boltz_cp_plan_store.json', emit: plan_store

    script:
    def shardPlanId = params.get('bcp_shard_plan_id', '2x2').toString()
    def inputFormat = params.get('bcp_input_format', 'config_files').toString()
    def outputFormat = params.get('bcp_output_format', 'mmcif').toString()
    def repoPath = shellQuote(params.get('bcp_repo_path', ''))
    def inputPath = shellQuote(input_config.toString())
    def planGpuIdsParam = params.get('bcp_gpu_ids', null)
    if (planGpuIdsParam == null || planGpuIdsParam.toString().trim() == '') {
        planGpuIdsParam = params.get('gpu_id', '')
    }
    def gpuIds = shellQuote(planGpuIdsParam == null ? '' : planGpuIdsParam.toString())
    def backend = shellQuote(params.get('bcp_backend', 'true-distributed-context-parallel'))
    def writeFullPae = shellQuote((params.get('bcp_write_full_pae', false).toString().toLowerCase() in ['true', '1', 'yes', 'y', 'on']) ? 'true' : 'false')
    def seed = shellQuote(params.get('bcp_seed', '') ?: '')
    def containerPath = shellQuote(params.get('bcp_container_path', ''))
    def codeRoot = shellQuote(params.code_root ?: '')
    def msaProvider = shellQuote(params.msa_provider ?: '')
    def msaPreset = shellQuote(params.msa_preset ?: '')
    def msaLocalDb = shellQuote(params.msa_local_db ?: '')
    def msaCacheDir = shellQuote(params.msa_cache_dir ?: '')
    def msaUseGpu = shellQuote(params.msa_use_gpu != null ? params.msa_use_gpu.toString() : '')
    def colabfoldApiHost = shellQuote(params.colabfold_api_host ?: '')
    def msaUseExpand = shellQuote(params.msa_use_expand != null ? params.msa_use_expand.toString() : '')
    def msaUseEnv = shellQuote(params.msa_use_env != null ? params.msa_use_env.toString() : '')
    def msaNumIterations = shellQuote(params.msa_num_iterations ?: '')
    def msaMinSeqId = shellQuote(params.msa_min_seq_id ?: '')
    def msaMinCoverage = shellQuote(params.msa_min_coverage ?: '')
    def msaTaxonList = shellQuote(params.msa_taxon_list ?: '')
    def defaultStoreRoot = new File((params.out_dir ?: '.').toString(), "run/boltz_cp_plan_store/${parent_job_id}").absolutePath
    def storeRootPath = shellQuote(params.get('bcp_store_root', defaultStoreRoot).toString())
    def configuredRamRootPath = shellQuote(params.get('bcp_configured_ram_root', '').toString())
    def contextStoreManifestPath = shellQuote(params.get('bcp_context_store_manifest_path', '').toString())
    def contextStatePath = shellQuote(params.get('bcp_context_state_path', '').toString())
    def contextLayerStatePath = shellQuote(params.get('bcp_context_layer_state_path', '').toString())
    def contextExecutionMode = shellQuote(params.get('bcp_context_execution_mode', 'cuda').toString())
    def contextTileTokens = shellQuote(params.get('bcp_context_tile_tokens', '').toString())
    def contextKeyTileTokens = shellQuote(params.get('bcp_context_key_tile_tokens', '').toString())
    def contextQueryTileTokens = shellQuote((params.get('bcp_context_query_tile_tokens', '512') ?: '').toString().trim())
    def physicalLaunchSizeCp = params.get('bcp_size_cp', 1)
    def bcpRecyclingSteps = params.get('bcp_recycling_steps', 3)
    def bcpSamplingSteps = params.get('bcp_sampling_steps', 200)
    def bcpDiffusionSamples = params.get('bcp_diffusion_samples', 1)
    """
    set -euo pipefail
    TASK_ROOT="\$PWD"
    REPO_PATH=${repoPath}
    INPUT_PATH=${inputPath}
    SHARD_PLAN_ID=${shellQuote(shardPlanId)}
    INPUT_FORMAT=${shellQuote(inputFormat)}
    OUTPUT_FORMAT=${shellQuote(outputFormat)}
    GPU_IDS=${gpuIds}
    BCP_BACKEND=${backend}
    WRITE_FULL_PAE=${writeFullPae}
    SEED=${seed}
    CONTAINER_PATH=${containerPath}
    CODE_ROOT=${codeRoot}
    PARENT_JOB_ID=${shellQuote(parent_job_id)}
    BATCH_NAME=${shellQuote(batch_name)}
    BCP_STORE_ROOT=${storeRootPath}
    BCP_CONFIGURED_RAM_ROOT=${configuredRamRootPath}
    BCP_CONTEXT_STORE_MANIFEST_PATH=${contextStoreManifestPath}
    BCP_CONTEXT_STATE_PATH=${contextStatePath}
    BCP_CONTEXT_LAYER_STATE_PATH=${contextLayerStatePath}
    BCP_CONTEXT_EXECUTION_MODE=${contextExecutionMode}
    BCP_CONTEXT_TILE_TOKENS=${contextTileTokens}
    BCP_CONTEXT_KEY_TILE_TOKENS=${contextKeyTileTokens}
    BCP_CONTEXT_QUERY_TILE_TOKENS=${contextQueryTileTokens}
    PHYSICAL_LAUNCH_SIZE_CP=${physicalLaunchSizeCp}

    BCP_RECYCLING_STEPS=${bcpRecyclingSteps}
    BCP_SAMPLING_STEPS=${bcpSamplingSteps}
    BCP_DIFFUSION_SAMPLES=${bcpDiffusionSamples}
    BOLTZ_USE_MSA=${params.boltz_use_msa?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'}
    MSA_PROVIDER=${msaProvider}
    MSA_PRESET=${msaPreset}
    MSA_LOCAL_DB=${msaLocalDb}
    MSA_CACHE_DIR=${msaCacheDir}
    MSA_THREADS=${params.msa_threads ?: 32}
    MSA_USE_GPU=${msaUseGpu}
    COLABFOLD_API_HOST=${colabfoldApiHost}
    COLABFOLD_API_MIN_INTERVAL=${params.colabfold_api_min_interval ?: 6}
    COLABFOLD_API_POLL_INTERVAL=${params.colabfold_api_poll_interval ?: 6}
    MSA_MIN_DEPTH_WARNING=${params.get('msa_min_depth_warning', 100)}
    MSA_MIN_DEPTH_FAIL=${params.get('msa_min_depth_fail', 0)}
    MSA_FORCE_REFRESH=${params.msa_force_refresh?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'}
    MSA_CACHE_ONLY=${params.msa_cache_only?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'}
    MSA_USE_EXPAND=${msaUseExpand}
    MSA_USE_ENV=${msaUseEnv}
    MSA_NUM_ITERATIONS=${msaNumIterations}
    MSA_MIN_SEQ_ID=${msaMinSeqId}
    MSA_MIN_COVERAGE=${msaMinCoverage}
    MSA_TAXON_LIST=${msaTaxonList}

    export TASK_ROOT REPO_PATH INPUT_PATH SHARD_PLAN_ID INPUT_FORMAT OUTPUT_FORMAT GPU_IDS BCP_BACKEND WRITE_FULL_PAE SEED CONTAINER_PATH CODE_ROOT PARENT_JOB_ID BATCH_NAME BCP_STORE_ROOT BCP_CONFIGURED_RAM_ROOT BCP_CONTEXT_STORE_MANIFEST_PATH BCP_CONTEXT_STATE_PATH BCP_CONTEXT_LAYER_STATE_PATH BCP_CONTEXT_EXECUTION_MODE BCP_CONTEXT_TILE_TOKENS BCP_CONTEXT_KEY_TILE_TOKENS BCP_CONTEXT_QUERY_TILE_TOKENS PHYSICAL_LAUNCH_SIZE_CP BCP_RECYCLING_STEPS BCP_SAMPLING_STEPS BCP_DIFFUSION_SAMPLES BOLTZ_USE_MSA MSA_PROVIDER MSA_PRESET MSA_LOCAL_DB MSA_CACHE_DIR MSA_THREADS MSA_USE_GPU COLABFOLD_API_HOST COLABFOLD_API_MIN_INTERVAL COLABFOLD_API_POLL_INTERVAL MSA_MIN_DEPTH_WARNING MSA_MIN_DEPTH_FAIL MSA_FORCE_REFRESH MSA_CACHE_ONLY MSA_USE_EXPAND MSA_USE_ENV MSA_NUM_ITERATIONS MSA_MIN_SEQ_ID MSA_MIN_COVERAGE MSA_TAXON_LIST
    export PYTHONPATH="\$REPO_PATH/src\${PYTHONPATH:+:\$PYTHONPATH}"
    BOLTZ_PYTHON="\$REPO_PATH/.venv/bin/python"
    if [ ! -x "\$BOLTZ_PYTHON" ]; then
        BOLTZ_PYTHON=python3
    fi
    export BOLTZ_PYTHON

    python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

import yaml

repo_path = Path(os.environ['REPO_PATH'])
if not repo_path.exists():
    raise SystemExit(f"Boltz-CP repo not found: {repo_path}")
sys.path.insert(0, str(repo_path / 'src'))

input_path = Path(os.environ['INPUT_PATH'])
if not input_path.exists():
    raise SystemExit(f"Boltz-CP input path not found: {input_path}")


def iter_yaml_files(root: Path):
    if root.is_dir():
        for candidate in sorted(root.rglob('*')):
            if candidate.is_file() and candidate.suffix.lower() in {'.yaml', '.yml'}:
                yield candidate
    elif root.suffix.lower() in {'.yaml', '.yml'}:
        yield root


def derive_sequence_length(root: Path) -> int:
    total = 0
    for yaml_path in iter_yaml_files(root):
        payload = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
        for entry in payload.get('sequences') or []:
            if not isinstance(entry, dict):
                continue
            protein = entry.get('protein')
            if isinstance(protein, dict):
                total += len(str(protein.get('sequence') or '').strip())
    if total <= 0:
        raise SystemExit(f"Could not derive a positive protein sequence length from {root}")
    return total

sequence_length = derive_sequence_length(input_path)
physical_gpu_ids = [gpu.strip() for gpu in os.environ.get('GPU_IDS', '').split(',') if gpu.strip()]
metadata = {
    'job_id': os.environ['PARENT_JOB_ID'],
    'batch_name': os.environ['BATCH_NAME'],
    'input_path': str(input_path.resolve()),
    'sequence_length': sequence_length,
    'shard_plan_id': os.environ['SHARD_PLAN_ID'],
    'input_format': os.environ['INPUT_FORMAT'],
    'output_format': os.environ['OUTPUT_FORMAT'],
    'write_full_pae': os.environ.get('WRITE_FULL_PAE', '').strip().lower() == 'true',
    'repo_path': str(repo_path.resolve()),
    'container_path': os.environ.get('CONTAINER_PATH', '').strip(),
    'physical_gpu_ids': physical_gpu_ids,
    'gpu_ids': physical_gpu_ids,
    'bcp_backend': os.environ.get('BCP_BACKEND', '').strip(),
    'bcp_context_store_manifest_path': os.environ.get('BCP_CONTEXT_STORE_MANIFEST_PATH', '').strip(),
    'bcp_context_state_path': os.environ.get('BCP_CONTEXT_STATE_PATH', '').strip(),
    'bcp_context_layer_state_path': os.environ.get('BCP_CONTEXT_LAYER_STATE_PATH', '').strip(),
    'bcp_context_execution_mode': os.environ.get('BCP_CONTEXT_EXECUTION_MODE', '').strip(),
    'bcp_context_tile_tokens': os.environ.get('BCP_CONTEXT_TILE_TOKENS', '').strip(),
    'bcp_context_key_tile_tokens': os.environ.get('BCP_CONTEXT_KEY_TILE_TOKENS', '').strip(),
    'bcp_context_query_tile_tokens': os.environ.get('BCP_CONTEXT_QUERY_TILE_TOKENS', '').strip(),
    'physical_launch_size_cp': int(os.environ.get('PHYSICAL_LAUNCH_SIZE_CP', '1') or '1'),
    'bcp_recycling_steps': int(os.environ.get('BCP_RECYCLING_STEPS', '3') or '3'),
    'bcp_sampling_steps': int(os.environ.get('BCP_SAMPLING_STEPS', '200') or '200'),
    'bcp_diffusion_samples': int(os.environ.get('BCP_DIFFUSION_SAMPLES', '1') or '1'),
    'boltz_use_msa': os.environ.get('BOLTZ_USE_MSA', '').strip().lower() == 'true',
    'msa_provider': os.environ.get('MSA_PROVIDER', '').strip(),
    'msa_preset': os.environ.get('MSA_PRESET', '').strip(),
    'msa_local_db': os.environ.get('MSA_LOCAL_DB', '').strip(),
    'msa_cache_dir': os.environ.get('MSA_CACHE_DIR', '').strip(),
    'msa_threads': int(os.environ.get('MSA_THREADS', '32') or '32'),
    'msa_use_gpu': os.environ.get('MSA_USE_GPU', '').strip(),
    'colabfold_api_host': os.environ.get('COLABFOLD_API_HOST', '').strip(),
    'colabfold_api_min_interval': float(os.environ.get('COLABFOLD_API_MIN_INTERVAL', '6') or '6'),
    'colabfold_api_poll_interval': float(os.environ.get('COLABFOLD_API_POLL_INTERVAL', '6') or '6'),
    'msa_min_depth_warning': int(os.environ.get('MSA_MIN_DEPTH_WARNING', '100') or '100'),
    'msa_min_depth_fail': int(os.environ.get('MSA_MIN_DEPTH_FAIL', '0') or '0'),
    'msa_force_refresh': os.environ.get('MSA_FORCE_REFRESH', '').strip().lower() == 'true',
    'msa_cache_only': os.environ.get('MSA_CACHE_ONLY', '').strip().lower() == 'true',
    'msa_use_expand': os.environ.get('MSA_USE_EXPAND', '').strip(),
    'msa_use_env': os.environ.get('MSA_USE_ENV', '').strip(),
    'msa_num_iterations': os.environ.get('MSA_NUM_ITERATIONS', '').strip(),
    'msa_min_seq_id': os.environ.get('MSA_MIN_SEQ_ID', '').strip(),
    'msa_min_coverage': os.environ.get('MSA_MIN_COVERAGE', '').strip(),
    'msa_taxon_list': os.environ.get('MSA_TAXON_LIST', '').strip(),
    'code_root': os.environ.get('CODE_ROOT', '').strip(),
}
seed = os.environ.get('SEED', '').strip()
if seed:
    metadata['bcp_seed'] = seed
Path('boltz_cp_input_metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
required_bytes = max(sequence_length * sequence_length * 16, 1024 * 1024)
Path('boltz_cp_plan_requirements.json').write_text(json.dumps({'required_bytes': required_bytes}, indent=2), encoding='utf-8')
PY

    INPUT_METADATA_JSON="\$(python3 - <<'PY'
import json
from pathlib import Path
print(json.dumps(json.loads(Path('boltz_cp_input_metadata.json').read_text(encoding='utf-8')), separators=(',', ':')))
PY
)"
    REQUIRED_BYTES="\$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('boltz_cp_plan_requirements.json').read_text(encoding='utf-8'))['required_bytes'])
PY
)"

    cd "\$REPO_PATH"
    init_plan_args=(
        "\$BOLTZ_PYTHON" -m boltz.distributed.main large-protein init-plan
        --input-metadata-json "\$INPUT_METADATA_JSON"
        --grid-size "\$SHARD_PLAN_ID"
        --required-bytes "\$REQUIRED_BYTES"
        --fallback-root "\$BCP_STORE_ROOT"
    )
    if [ -n "\$BCP_CONFIGURED_RAM_ROOT" ]; then
        init_plan_args+=(--configured-ram-root "\$BCP_CONFIGURED_RAM_ROOT")
    fi
    store_root="\$("\${init_plan_args[@]}")"
    export STORE_ROOT="\$store_root"
    export PLAN_MANIFEST_PATH="\$store_root/metadata/plan_manifest.json"
    export TASK_ROOT
    python3 - <<'PY'
import json
import os
from pathlib import Path

store_root = Path(os.environ['STORE_ROOT']).resolve()
plan_manifest_path = Path(os.environ['PLAN_MANIFEST_PATH']).resolve()
task_root = Path(os.environ['TASK_ROOT']).resolve()
manifest_payload = json.loads(plan_manifest_path.read_text(encoding='utf-8'))
(task_root / 'boltz_cp_plan_manifest.json').write_text(json.dumps(manifest_payload, indent=2), encoding='utf-8')
plan_store_payload = {
    'plan_id': manifest_payload.get('plan_id', os.environ['PARENT_JOB_ID']),
    'store_root': str(store_root),
    'plan_manifest_path': str(plan_manifest_path),
    'physical_launch_size_cp': int(os.environ.get('PHYSICAL_LAUNCH_SIZE_CP', '1') or '1'),
    'shard_plan_id': os.environ['SHARD_PLAN_ID'],
    'parent_job_id': os.environ['PARENT_JOB_ID'],
    'batch_name': os.environ['BATCH_NAME'],
}
(task_root / 'boltz_cp_plan_store.json').write_text(json.dumps(plan_store_payload, indent=2), encoding='utf-8')
PY
    """

    stub:
    """
    cat > boltz_cp_plan_manifest.json <<'EOF'
{
  "manifest_version": 1,
  "plan_id": "${parent_job_id}",
  "logical_size_cp": 4,
  "physical_launch_size_cp": 1,
  "shard_plan": {"name": "${params.get('bcp_shard_plan_id', '2x2')}", "grid_shape": [2, 2]},
  "input_metadata": {"job_id": "${parent_job_id}", "batch_name": "${batch_name}"},
  "physical_gpu_ids": ["0"],
  "bundles": [
    {"bundle_id": "bundle-r00-c00", "row_index": 0, "col_index": 0, "row_range": [0, 10], "col_range": [0, 10]},
    {"bundle_id": "bundle-r00-c01", "row_index": 0, "col_index": 1, "row_range": [0, 10], "col_range": [10, 20]},
    {"bundle_id": "bundle-r01-c00", "row_index": 1, "col_index": 0, "row_range": [10, 20], "col_range": [0, 10]},
    {"bundle_id": "bundle-r01-c01", "row_index": 1, "col_index": 1, "row_range": [10, 20], "col_range": [10, 20]}
  ]
}
EOF
    cat > boltz_cp_plan_store.json <<'EOF'
{"plan_id":"${parent_job_id}","store_root":"${params.out_dir}/run/boltz_cp_plan_store/${parent_job_id}","plan_manifest_path":"${params.out_dir}/run/boltz_cp_plan_store/${parent_job_id}/metadata/plan_manifest.json","physical_launch_size_cp":1}
EOF
    """
}

process SpawnBoltzCPChildren {
    label 'process_low'

    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: '*.json'
    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: '*.log'

    input:
    val parent_job_id
    val batch_name
    path manifest
    path plan_store

    output:
    path 'spawn_boltz_cp_result.json', emit: result
    path 'spawn_boltz_cp.log'

    script:
    """
    python3 ${params.code_root}/scripts/spawn_boltz_cp_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --manifest "\$(readlink -f ${manifest})" \\
        --plan_store "\$(readlink -f ${plan_store})" \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output spawn_boltz_cp_result.json \\
        2>&1 | tee spawn_boltz_cp.log
    """

    stub:
    """
    cat > spawn_boltz_cp_result.json <<'EOF'
{"status":"complete","spawned_jobs":4,"bundle_count":4,"failed_spawns":0}
EOF
    echo "Spawned Boltz-CP child jobs" > spawn_boltz_cp.log
    """
}

process WaitForBoltzCPChildren {
    label 'process_low'

    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: '*.json'
    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: '*.log'

    input:
    val parent_job_id
    path spawn_result
    val batch_name

    output:
    path 'boltz_cp_child_outputs.json', emit: result
    path 'wait_boltz_cp.log'

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "boltz_cp_bundle" \\
        --poll_interval 30 \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output boltz_cp_child_outputs.json \\
        2>&1 | tee wait_boltz_cp.log
    """

    stub:
    """
    cat > boltz_cp_child_outputs.json <<'EOF'
{"status":"complete","completed":4,"failed":0,"cancelled":0,"child_output_dirs":[]}
EOF
    echo "Waited for Boltz-CP child jobs" > wait_boltz_cp.log
    """
}

process FinalizeBoltzCPExperimentalChildren {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: 'published/*.pdb', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/cif_files/predictions", mode: 'copy', pattern: 'published/*.cif', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/json_files/predictions", mode: 'copy', pattern: 'published/*.json', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/npz_files/predictions", mode: 'copy', pattern: 'published/*.npz', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/processed/boltz_cp", mode: 'copy', pattern: 'bundle_manifests/*.json', saveAs: { filename -> filename.replace('bundle_manifests/', '') }
    publishDir "${params.out_dir}/run/boltz_cp_experimental_coordinator", mode: 'copy', pattern: 'aggregation_report.json'

    input:
    path child_outputs_json
    path plan_store

    output:
    path 'collected', emit: results_dir
    path 'bundle_manifests', emit: bundle_manifests
    path 'published/*.pdb', emit: pdbs, optional: true
    path 'published/*.cif', emit: cifs, optional: true
    path 'published/*.json', emit: jsons, optional: true
    path 'published/*.npz', emit: npzs, optional: true
    path 'aggregation_report.json', emit: report

    script:
    def repoPath = shellQuote(params.get('bcp_repo_path', ''))
    """
    set -euo pipefail
    TASK_ROOT="\$PWD"
    REPO_PATH=${repoPath}
    PLAN_STORE_PATH="\$(readlink -f ${plan_store})"
    export TASK_ROOT PLAN_STORE_PATH
    mkdir -p collected published bundle_manifests

    STORE_ROOT="\$(python3 - <<'PY'
import json
import os
from pathlib import Path
payload = json.loads(Path(os.environ['PLAN_STORE_PATH']).read_text(encoding='utf-8'))
print(str(Path(payload['store_root']).resolve()))
PY
)"
    if [ -z "\$STORE_ROOT" ]; then
        echo "boltz_cp_plan_store.json is missing store_root" >&2
        exit 1
    fi
    export STORE_ROOT

    if [ ! -d "\$REPO_PATH" ]; then
        echo "Boltz-CP repo not found: \$REPO_PATH" >&2
        exit 1
    fi
    BOLTZ_PYTHON="\$REPO_PATH/.venv/bin/python"
    if [ ! -x "\$BOLTZ_PYTHON" ]; then
        BOLTZ_PYTHON=python3
    fi
    export BOLTZ_PYTHON

    cd "\$REPO_PATH"
    set +e
    "\$BOLTZ_PYTHON" -m boltz.distributed.main large-protein finalize --store-root "\$STORE_ROOT" > "\$TASK_ROOT/large_protein_finalize.log" 2>&1
    finalize_rc=\$?
    set -e
    cat "\$TASK_ROOT/large_protein_finalize.log"
    cd "\$TASK_ROOT"

    python3 - <<'PY'
import json
import os
import shutil
from pathlib import Path

with open('${child_outputs_json}', encoding='utf-8') as handle:
    payload = json.load(handle)

child_dirs = payload.get('child_output_dirs', [])
published = Path('published')
collected = Path('collected')
bundle_manifests = Path('bundle_manifests')
published.mkdir(exist_ok=True)
collected.mkdir(exist_ok=True)
bundle_manifests.mkdir(exist_ok=True)

store_root = Path(os.environ['STORE_ROOT']).resolve()
summary_path = store_root / 'metadata' / 'summary.json'
summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}

artifact_counts = {'pdb': 0, 'cif': 0, 'json': 0, 'npz': 0, 'bundle_manifests': 0}
published_original_artifacts = []
viewer_ingest_design_name = None


def _artifact_bucket(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == '.pdb':
        return 'pdb'
    if suffix in {'.cif', '.mmcif'}:
        return 'cif'
    if suffix == '.json':
        return 'json'
    if suffix == '.npz':
        return 'npz'
    return None


def copy_unique(src: Path, dest_dir: Path, prefix: str | None = None) -> Path:
    destination = dest_dir / src.name
    if destination.exists() and prefix:
        destination = dest_dir / f"{prefix}_{src.name}"
    shutil.copy2(src, destination)
    return destination


store_published = store_root / 'published'
if store_published.exists():
    for artifact in sorted(store_published.iterdir()):
        if not artifact.is_file():
            continue
        copied = copy_unique(artifact, published, prefix='published_original')
        published_original_artifacts.append(str(copied))
        bucket = _artifact_bucket(copied)
        if bucket:
            artifact_counts[bucket] += 1

    structure_src = next(
        (
            candidate
            for candidate in (
                store_published / 'structure.cif',
                store_published / 'structure.mmcif',
                store_published / 'structure.pdb',
            )
            if candidate.exists()
        ),
        None,
    )
    confidence_src = store_published / 'confidence.json'
    if structure_src is not None and confidence_src.exists():
        viewer_ingest_design_name = 'boltz_cp_result_0'
        structure_alias = published / f"{viewer_ingest_design_name}{structure_src.suffix.lower()}"
        confidence_alias = published / 'confidence_boltz_cp_result_0.json'
        shutil.copy2(structure_src, structure_alias)
        shutil.copy2(confidence_src, confidence_alias)
        structure_bucket = _artifact_bucket(structure_alias)
        if structure_bucket:
            artifact_counts[structure_bucket] += 1
        artifact_counts['json'] += 1


for child_index, raw_child_dir in enumerate(child_dirs, start=1):
    child_dir = Path(raw_child_dir)
    if not child_dir.exists():
        continue
    prefix = f"child{child_index:02d}"
    for subdir, suffix, bucket in (
        ('pdb_files/predictions', '*.pdb', 'pdb'),
        ('cif_files/predictions', '*.cif', 'cif'),
        ('json_files/predictions', '*.json', 'json'),
        ('npz_files/predictions', '*.npz', 'npz'),
    ):
        search_root = child_dir / subdir
        if not search_root.exists():
            continue
        for artifact in search_root.glob(suffix):
            copy_unique(artifact, published, prefix)
            artifact_counts[bucket] += 1

for bundle_dir in sorted((store_root / 'bundles').glob('*')):
    manifest_path = bundle_dir / 'manifest.json'
    if not manifest_path.exists():
        continue
    bundle_id = bundle_dir.name
    copy_unique(manifest_path, bundle_manifests, bundle_id)
    artifact_counts['bundle_manifests'] += 1
    for extra_name in ('result.json', 'failure.json'):
        extra_path = bundle_dir / extra_name
        if extra_path.exists():
            shutil.copy2(extra_path, bundle_manifests / f"{bundle_id}_{extra_name}")

markers_dir = store_root / 'markers'
if markers_dir.exists():
    for marker_path in sorted(markers_dir.glob('*.done')):
        shutil.copy2(marker_path, bundle_manifests / f"{marker_path.stem}_marker.json")

(collected / 'child_output_dirs.json').write_text(json.dumps(child_dirs, indent=2), encoding='utf-8')
(collected / 'plan_store.json').write_text(Path(os.environ['PLAN_STORE_PATH']).read_text(encoding='utf-8'), encoding='utf-8')
if summary:
    (collected / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')

report = dict(summary) if summary else {}
report.setdefault('status', payload.get('status', 'complete'))
report['children_processed'] = len(child_dirs)
report['artifacts'] = artifact_counts
report['store_root'] = str(store_root)
report['plan_store_path'] = os.environ['PLAN_STORE_PATH']
report['published_original_artifacts'] = published_original_artifacts
if viewer_ingest_design_name:
    report['viewer_ingest_design_name'] = viewer_ingest_design_name
Path('aggregation_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
PY

    if [ \$finalize_rc -ne 0 ]; then
        exit \$finalize_rc
    fi
    """

    stub:
    """
    mkdir -p collected published bundle_manifests
    cat > aggregation_report.json <<'EOF'
{"status":"complete","children_processed":4,"artifacts":{"pdb":0,"cif":1,"json":1,"npz":0,"bundle_manifests":4}}
EOF
    """
}

process FinalizeBoltzCPExperimental {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: 'published/*.pdb', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/cif_files/predictions", mode: 'copy', pattern: 'published/*.cif', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/json_files/predictions", mode: 'copy', pattern: 'published/*.json', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/npz_files/predictions", mode: 'copy', pattern: 'published/*.npz', saveAs: { filename -> filename.replace('published/', '') }

    input:
    path results_dir

    output:
    path 'published/*.pdb', emit: pdbs, optional: true
    path 'published/*.cif', emit: cifs, optional: true
    path 'published/*.json', emit: jsons, optional: true
    path 'published/*.npz', emit: npzs, optional: true

    script:
    """
    set -euo pipefail
    mkdir -p published
    python3 - <<'PY'
from pathlib import Path
import shutil

published = Path('published')
published.mkdir(exist_ok=True)
for pattern in ('*.pdb', '*.cif', '*.json', '*.npz'):
    for src in Path('${results_dir}').rglob(pattern):
        shutil.copy2(src, published / src.name)
PY
    """
}
