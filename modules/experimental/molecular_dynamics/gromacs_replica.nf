nextflow.enable.dsl=2

process MD_GROMACS_REPLICA {
    tag "gromacs-replica:${replica_index}"
    label 'MolecularDynamicsGromacs'
    publishDir "${params.out_dir}/replicas/replica_${replica_index}", mode: 'copy', overwrite: true

    input:
    tuple val(replica_index), path(normalized_config), path(preparation_bundle)

    output:
    path "gromacs_replica_${replica_index}_manifest.json", emit: manifest
    path "replica_${replica_index}", emit: artifacts

    script:
    def run_output = params.md_resume_output_dir ?: "replica_${replica_index}"
    def resume_checkpoint = params.md_resume_checkpoint ?: ""
    def resume_sha256 = params.md_resume_checkpoint_sha256 ?: ""
    def resume_arg = resume_checkpoint ? "--resume-checkpoint '${resume_checkpoint}'" : ""
    """
    export BMS_FEATURE_MOLECULAR_DYNAMICS="\${BMS_FEATURE_MOLECULAR_DYNAMICS:-0}"
    export CUDA_VISIBLE_DEVICES="${params.gpu_id}"
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli validate \
      --config ${normalized_config} \
      --gpu-id 0 \
      --scheduler-gpu-id "${params.gpu_id}" \
      --output runtime_config.json
    engine_runtime_sha256="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine_runtime"]["sif_sha256"])' runtime_config.json)"
    printf '%s  %s\n' "\${engine_runtime_sha256}" '/opt/bms-md-engine-runtime.sif' | sha256sum --check --strict
    if [ -n "${resume_checkpoint}" ]; then
      printf '%s  %s\n' "${resume_sha256}" "${resume_checkpoint}" | sha256sum --check --strict
    fi
    set +e
    rm -f '${run_output}/md-checkpoint-receipt.json' '${run_output}/.bms-pause-boundary.json'
    runner_pid=""
    stop_for_pause() {
      trap - TERM INT
      if [ -n "\${runner_pid}" ]; then
        kill -TERM "\${runner_pid}" 2>/dev/null || true
        wait "\${runner_pid}" || true
      fi
      exit 143
    }
    trap stop_for_pause TERM INT
    python3 -m scripts.bms_md.checkpointing_runner \
      --config runtime_config.json \
      --output-dir '${run_output}' \
      -- \
      python3 -m scripts.bms_md.cli run \
      --config runtime_config.json \
      --output-dir '${run_output}' \
      --replica-index ${replica_index} \
      --preparation-bundle ${preparation_bundle} \
      ${resume_arg} &
    runner_pid="\$!"
    wait "\${runner_pid}"
    run_status="\$?"
    trap - TERM INT
    if [ "\${run_status}" -ne 0 ]; then
      exit "\${run_status}"
    fi
    if [ "${run_output}" != "replica_${replica_index}" ]; then
      rm -rf replica_${replica_index}
      cp -a "${run_output}" replica_${replica_index}
    fi
    cp replica_${replica_index}/manifest.json gromacs_replica_${replica_index}_manifest.json
    """
}
