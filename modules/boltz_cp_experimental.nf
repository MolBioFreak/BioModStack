nextflow.enable.dsl = 2

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process RunBoltzCPExperimental {
    label 'BoltzCP'
    label 'gpu'

    publishDir "${params.out_dir}/run/boltz_cp_experimental", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/inputs/boltz_cp", mode: 'copy', pattern: 'staged_input/**', saveAs: { filename -> filename.replace('staged_input/', '') }
    publishDir "${params.out_dir}/processed/boltz_cp", mode: 'copy', pattern: 'cp_results/processed/**', saveAs: { filename -> filename.replace('cp_results/processed/', '') }

    input:
    path input_config

    output:
    path 'cp_results', emit: results_dir, optional: true
    path 'cp_results/processed', emit: processed_dir, optional: true
    path '*.log'

    script:
    def gpuIdsParam = params.get('bcp_gpu_ids', null)
    if (gpuIdsParam == null || gpuIdsParam.toString().trim() == '') {
        gpuIdsParam = params.get('gpu_id', '')
    }
    def gpuIds = (gpuIdsParam == null ? '' : gpuIdsParam.toString()).split(',').collect { it.trim() }.findAll { it }
    def nproc = gpuIds ? gpuIds.size() : 1
    def sizeCp = (params.bcp_size_cp ?: 4) as Integer
    def sizeDp = Math.max((int) (nproc / sizeCp), 1)
    def inputFormat = (params.bcp_input_format ?: 'config_files').toString()
    def outputFormat = (params.bcp_output_format ?: 'mmcif').toString()
    def writeFullPaeFlag = (params.bcp_write_full_pae?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']) ? '--write_full_pae' : ''
    def seedFlag = params.bcp_seed != null && params.bcp_seed.toString() != '' ? "--seed ${params.bcp_seed}" : ''
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
    def msaMinDepthWarning = params.msa_min_depth_warning ?: 100
    def msaMinDepthFail = params.msa_min_depth_fail ?: 0
    def msaForceRefresh = params.msa_force_refresh?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def msaCacheOnly = params.msa_cache_only?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']
    def inputConfigPath = shellQuote(input_config.toString())
    def repoPath = shellQuote(params.bcp_repo_path ?: '')
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
    def quotedOutputFormat = shellQuote(outputFormat)
    """
    set -euo pipefail

    TASK_ROOT="\$PWD"
    REPO_PATH=${repoPath}
    SIZE_CP=${sizeCp}
    NPROC=${nproc}
    SIZE_DP=${sizeDp}
    INPUT_FORMAT=${quotedInputFormat}
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

    if [ ! -d "\$REPO_PATH" ]; then
        echo "Boltz-CP repo not found: \$REPO_PATH" >&2
        exit 1
    fi

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
    export BOLTZ_CACHE=/boltzcache
    export PYTHONPATH="\$REPO_PATH/src\${PYTHONPATH:+:\$PYTHONPATH}"

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


def materialize_yaml(yaml_path: Path, name_prefix: str) -> None:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    sequences = payload.get("sequences") or []
    for index, entry in enumerate(sequences, start=1):
        if not isinstance(entry, dict):
            continue
        protein = entry.get("protein")
        if not isinstance(protein, dict):
            continue
        existing_msa = str(protein.get("msa") or "").strip()
        if existing_msa and existing_msa.lower() != "empty":
            continue
        sequence = str(protein.get("sequence") or "").strip()
        if not sequence:
            raise SystemExit(f"Boltz-CP protein entry {index} in {yaml_path} is missing a sequence")
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
        msa_path = msa_out_dir / f"{msa_name}.a3m"
        if not msa_path.exists():
            raise SystemExit(f"run_local_msa.py did not produce expected file {msa_path}")
        protein["msa"] = str(msa_path.resolve())

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

    cd "\$REPO_PATH"
    python3 -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node \$NPROC \
        src/boltz/distributed/main.py predict "\$DATA_ARG" \
        --out_dir "\$TASK_ROOT" \
        --cache /boltzcache \
        --size_dp \$SIZE_DP \
        --size_cp \$SIZE_CP \
        --input_format "\$INPUT_FORMAT" \
        --output_format "\$OUTPUT_FORMAT" \
        --recycling_steps ${params.bcp_recycling_steps ?: 3} \
        --sampling_steps ${params.bcp_sampling_steps ?: 200} \
        --diffusion_samples ${params.bcp_diffusion_samples ?: 1} \
        ${writeFullPaeFlag} \
        ${seedFlag} \
        2>&1 | tee "\$TASK_ROOT/boltz_cp_experimental.log"

    result_dir="\$(find "\$TASK_ROOT" -maxdepth 1 -type d -name 'boltz_results_*' | head -n 1)"
    if [ -z "\$result_dir" ]; then
        echo "Boltz-CP run did not produce a boltz_results_* directory" >&2
        exit 1
    fi
    mv "\$result_dir" "\$TASK_ROOT/cp_results"
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
{"inputs":["sample_0001"]}
EOF
    echo "Boltz-CP stub run" > boltz_cp_experimental.log
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
