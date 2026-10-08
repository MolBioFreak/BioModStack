nextflow.enable.dsl=2

process MD_FINALIZE_RESULTS {
    tag "md-finalize:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCpu'
    publishDir "${params.out_dir}", mode: 'copy', overwrite: true

    input:
    path replica_manifests

    output:
    path 'manifest.json', emit: manifest

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli aggregate \
      --manifests ${replica_manifests} \
      --output manifest.json
    """
}
