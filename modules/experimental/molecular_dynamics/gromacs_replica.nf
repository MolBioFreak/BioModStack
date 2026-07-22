nextflow.enable.dsl=2

process MD_GROMACS_REPLICA {
    tag "gromacs-replica:${replica_index}"
    label 'MolecularDynamicsGromacs'
    publishDir "${params.out_dir}/replicas/replica_${replica_index}", mode: 'copy', overwrite: true

    input:
    tuple val(replica_index), path(normalized_config)

    output:
    path "gromacs_replica_${replica_index}_manifest.json", emit: manifest
    path "replica_${replica_index}", emit: artifacts

    script:
    """
    export BMS_FEATURE_MOLECULAR_DYNAMICS="\${BMS_FEATURE_MOLECULAR_DYNAMICS:-0}"
    export CUDA_VISIBLE_DEVICES="${params.gpu_id}"
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    engine_runtime_sha256="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine_runtime"]["sif_sha256"])' ${normalized_config})"
    printf '%s  %s\n' "\${engine_runtime_sha256}" '/opt/bms-md-engine-runtime.sif' | sha256sum --check --strict
    python3 -m scripts.bms_md.cli run \
      --config ${normalized_config} \
      --output-dir replica_${replica_index} \
      --replica-index ${replica_index}
    cp replica_${replica_index}/manifest.json gromacs_replica_${replica_index}_manifest.json
    """
}
