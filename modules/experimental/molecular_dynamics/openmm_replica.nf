nextflow.enable.dsl=2

process MD_OPENMM_REPLICA {
    tag "openmm-replica:${replica_index}"
    label 'MolecularDynamicsOpenMM'
    publishDir "${params.out_dir}/replicas/replica_${replica_index}", mode: 'copy', overwrite: true

    input:
    tuple val(replica_index), path(normalized_config)

    output:
    path "openmm_replica_${replica_index}_manifest.json", emit: manifest
    path "replica_${replica_index}", emit: artifacts

    script:
    """
    export BMS_FEATURE_MOLECULAR_DYNAMICS="\${BMS_FEATURE_MOLECULAR_DYNAMICS:-0}"
    export CUDA_VISIBLE_DEVICES="${params.gpu_id}"
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli run \
      --config ${normalized_config} \
      --output-dir replica_${replica_index} \
      --replica-index ${replica_index}
    cp replica_${replica_index}/manifest.json openmm_replica_${replica_index}_manifest.json
    """
}
