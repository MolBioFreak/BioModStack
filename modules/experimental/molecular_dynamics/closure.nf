nextflow.enable.dsl=2

process MD_JOIN_REPLICAS {
    stageInMode 'copy'
    tag "md-aggregate:${params.job_id}"
    label 'MolecularDynamicsCoordinator'
    errorStrategy 'terminate'

    input:
    path config
    path bundle
    path replicas

    output:
    path 'aggregate', emit: aggregate

    script:
    def inputs = replicas.collect { "'${it}'" }.join(' ')
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.closure aggregate --config '${config}' --bundle '${bundle}' --root aggregate --inputs ${inputs}
    """
}

process MD_SEAL_RESULTS {
    tag "md-completion:${params.job_id}"
    label 'MolecularDynamicsCoordinator'
    errorStrategy 'terminate'
    publishDir "${params.out_dir}", mode: 'copy', overwrite: false

    input:
    path aggregate
    path analyses

    output:
    path 'manifest.json', emit: manifest
    path 'normalized_config.json', emit: config
    path 'replicas', emit: replicas
    path 'preparation', emit: preparation
    path 'analysis', emit: analysis
    path 'md_completion_barrier.json', emit: completion

    script:
    def inputs = analyses.collect { "'${it}'" }.join(' ')
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    cp -a '${aggregate}/.' .
    python3 -m scripts.bms_md.closure complete --root . --inputs ${inputs}
    """
}
