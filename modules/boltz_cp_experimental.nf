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
    def repoPath = shellQuote(params.bcp_repo_path ?: '/opt/fold-cp')
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

    # Only the invocation-private copy is adapted. Original configs stay sealed.
    if [ -n "\${BMS_PORTABLE_INPUT_BINDINGS:-}" ] && [ "\$INPUT_FORMAT" = "config_files" ]; then
        python3 - "\$CODE_ROOT" "\$DATA_ARG" ${inputConfigPath} <<'PY'
import sys
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts'))
from lib.portable_inputs import bind_native_document
root, original = Path(sys.argv[2]), Path(sys.argv[3]).resolve()
files = sorted(root.rglob('*.yaml')) + sorted(root.rglob('*.yml')) if root.is_dir() else [root]
for path in files:
    owner = original / path.relative_to(root) if root.is_dir() else original
    document = yaml.safe_load(path.read_text())
    bound = bind_native_document(document, 'boltz-yaml', owner=owner)
    path.write_text(yaml.safe_dump(bound, sort_keys=False))
PY
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
        python3 - "\$CODE_ROOT" "\$DATA_ARG" <<'PY'
import sys
import hashlib
import shutil
from pathlib import Path
import yaml
sys.path.insert(0, sys.argv[1])
from biomodstack_boltz_msa import resolve_boltz_config
root = Path(sys.argv[2])
files = sorted(root.rglob('*.yaml')) + sorted(root.rglob('*.yml')) if root.is_dir() else [root]
package_root = root if root.is_dir() else root.parent
for path in files:
    # External typed bindings were verified above. Give the existing native
    # validator a contained task-private alignment, not an emulated host root.
    document = yaml.safe_load(path.read_text())
    for entry in document.get('sequences', []):
        protein = entry.get('protein', {})
        value = protein.get('msa')
        if value and value != 'empty':
            source = Path(value)
            source = (source if source.is_absolute() else path.parent / source).resolve()
            if not source.is_relative_to(package_root.resolve()):
                data = source.read_bytes()
                target = package_root / '.portable-msa' / (hashlib.sha256(data).hexdigest() + source.suffix)
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)
                protein['msa'] = str(target.resolve())
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    bound = resolve_boltz_config(path, root=package_root)
    path.write_text(yaml.safe_dump(bound, sort_keys=False))
PY
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
