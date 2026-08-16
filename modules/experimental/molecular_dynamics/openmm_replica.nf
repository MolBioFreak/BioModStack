nextflow.enable.dsl=2

process MD_OPENMM_REPLICA {
    tag "openmm-replica:${replica_index}"
    label 'MolecularDynamicsOpenMM'
    publishDir "${params.out_dir}/replicas/replica_${replica_index}", mode: 'copy', overwrite: true

    input:
    tuple val(replica_index), path(normalized_config), path(preparation_bundle)

    output:
    path "openmm_replica_${replica_index}_manifest.json", emit: manifest
    path "replica_${replica_index}", emit: artifacts

    script:
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
    python3 -m scripts.bms_md.cli run \
      --config runtime_config.json \
      --output-dir replica_${replica_index} \
      --replica-index ${replica_index} \
      --preparation-bundle ${preparation_bundle}
    cp replica_${replica_index}/manifest.json openmm_replica_${replica_index}_manifest.json
    """
}
