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
    def gpuIds = (params.bcp_gpu_ids ?: '0,1,2,3').toString().split(',').collect { it.trim() }.findAll { it }
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
    def msaMinDepthWarning = params.get('msa_min_depth_warning', 100)
    def msaMinDepthFail = params.get('msa_min_depth_fail', 0)
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
    def bundleIdLiteral = shellQuote(params.get('bcp_bundle_id', ''))
    def bundleIndexLiteral = shellQuote(params.get('bcp_bundle_index', ''))
    def planManifestLiteral = shellQuote(params.get('bcp_plan_manifest_path', ''))
    def parentJobLiteral = shellQuote(params.containsKey('bcp_parent_job_id') ? params['bcp_parent_job_id'] : (params.containsKey('job_id') ? params['job_id'] : ''))
    def parentShardPlanLiteral = shellQuote(params.get('bcp_shard_plan_id', ''))
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

    script:
    def shardPlanId = (params.bcp_shard_plan_id ?: '2x2').toString()
    def inputFormat = (params.bcp_input_format ?: 'config_files').toString()
    def outputFormat = (params.bcp_output_format ?: 'mmcif').toString()
    def repoPath = shellQuote(params.bcp_repo_path ?: '')
    def inputPath = shellQuote(input_config.toString())
    def gpuIds = shellQuote(params.bcp_gpu_ids ?: '0,1,2,3')
    def writeFullPae = shellQuote((params.bcp_write_full_pae?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on']) ? 'true' : 'false')
    def seed = shellQuote(params.bcp_seed ?: '')
    def containerPath = shellQuote(params.bcp_container_path ?: '')
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
    """
    set -euo pipefail
    REPO_PATH=${repoPath}
    INPUT_PATH=${inputPath}
    SHARD_PLAN_ID=${shellQuote(shardPlanId)}
    INPUT_FORMAT=${shellQuote(inputFormat)}
    OUTPUT_FORMAT=${shellQuote(outputFormat)}
    GPU_IDS=${gpuIds}
    WRITE_FULL_PAE=${writeFullPae}
    SEED=${seed}
    CONTAINER_PATH=${containerPath}
    CODE_ROOT=${codeRoot}
    PARENT_JOB_ID=${shellQuote(parent_job_id)}
    BATCH_NAME=${shellQuote(batch_name)}
    PHYSICAL_LAUNCH_SIZE_CP=${params.bcp_size_cp ?: 1}
    BCP_RECYCLING_STEPS=${params.bcp_recycling_steps ?: 3}
    BCP_SAMPLING_STEPS=${params.bcp_sampling_steps ?: 200}
    BCP_DIFFUSION_SAMPLES=${params.bcp_diffusion_samples ?: 1}
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

from boltz.distributed.large_protein.plan import build_plan_manifest  # noqa: E402

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
    'colabfold_api_min_interval': int(os.environ.get('COLABFOLD_API_MIN_INTERVAL', '6') or '6'),
    'colabfold_api_poll_interval': int(os.environ.get('COLABFOLD_API_POLL_INTERVAL', '6') or '6'),
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
manifest = build_plan_manifest(metadata, os.environ['SHARD_PLAN_ID'])
payload = manifest.to_dict()
payload['physical_gpu_ids'] = physical_gpu_ids
payload['physical_launch_size_cp'] = metadata['physical_launch_size_cp']
Path('boltz_cp_plan_manifest.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
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
  "shard_plan": {"name": "${params.bcp_shard_plan_id ?: '2x2'}", "grid_shape": [2, 2]},
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

    output:
    path 'spawn_boltz_cp_result.json', emit: result
    path 'spawn_boltz_cp.log'

    script:
    """
    python3 ${params.code_root}/scripts/spawn_boltz_cp_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --manifest "\$(readlink -f ${manifest})" \\
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

    output:
    path 'collected', emit: results_dir
    path 'bundle_manifests', emit: bundle_manifests
    path 'published/*.pdb', emit: pdbs, optional: true
    path 'published/*.cif', emit: cifs, optional: true
    path 'published/*.json', emit: jsons, optional: true
    path 'published/*.npz', emit: npzs, optional: true
    path 'aggregation_report.json', emit: report

    script:
    """
    set -euo pipefail
    mkdir -p collected published bundle_manifests
    python3 - <<'PY'
import json
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

artifact_counts = {'pdb': 0, 'cif': 0, 'json': 0, 'npz': 0, 'bundle_manifests': 0}


def copy_unique(src: Path, dest_dir: Path, prefix: str) -> None:
    destination = dest_dir / src.name
    if destination.exists():
        destination = dest_dir / f"{prefix}_{src.name}"
    shutil.copy2(src, destination)

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
    processed_root = child_dir / 'processed' / 'boltz_cp'
    if processed_root.exists():
        for manifest_path in processed_root.rglob('*.json'):
            copy_unique(manifest_path, bundle_manifests, prefix)
            artifact_counts['bundle_manifests'] += 1

(Path('collected') / 'child_output_dirs.json').write_text(json.dumps(child_dirs, indent=2), encoding='utf-8')
report = {
    'status': 'complete',
    'children_processed': len(child_dirs),
    'artifacts': artifact_counts,
}
Path('aggregation_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
PY
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
