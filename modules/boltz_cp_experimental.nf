nextflow.enable.dsl = 2

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
    """
    set -euo pipefail

    TASK_ROOT="\$PWD"
    REPO_PATH="${params.bcp_repo_path ?: ''}"
    SIZE_CP=${sizeCp}
    NPROC=${nproc}
    SIZE_DP=${sizeDp}
    INPUT_FORMAT="${inputFormat}"

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
    if [ -d "${input_config}" ]; then
        cp -R "${input_config}" staged_input/input_bundle
        DATA_ARG="\$TASK_ROOT/staged_input/input_bundle"
    else
        staged_file="\$(basename "${input_config}")"
        cp "${input_config}" "staged_input/\$staged_file"
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

    cd "\$REPO_PATH"
    python3 -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node \$NPROC \
        src/boltz/distributed/main.py predict "\$DATA_ARG" \
        --out_dir "\$TASK_ROOT" \
        --cache /boltzcache \
        --size_dp \$SIZE_DP \
        --size_cp \$SIZE_CP \
        --input_format \$INPUT_FORMAT \
        --output_format ${outputFormat} \
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
