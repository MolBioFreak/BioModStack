nextflow.enable.dsl=2

// Offline test only: consume licensed native trajectory fixtures instead of
// running MD preparation/engines. Production join/seal modules remain unchanged.
include { MD_JOIN_REPLICAS; MD_SEAL_RESULTS } from '../../modules/experimental/molecular_dynamics/closure'

process NATIVE_FIXTURE_ANALYSIS {
    input:
    tuple val(index), path(replica)
    output:
    path "analysis_result_${index}", emit: results
    script:
    """
    export PYTHONPATH='${params.code_root}'
    export BMS_FEATURE_MOLECULAR_DYNAMICS=1
    sleep ${params.analysis_delay ?: 0}
    test '${params.fail_analysis ?: false}' != 'true'
    mkdir analysis_result_${index}
    '${params.python}' -m scripts.bms_md.cli analyze \
      --manifest '${replica}/manifest.json' \
      --output 'analysis_result_${index}/md_analysis_replica_${index}.json' \
      --runtime-sha256 '${params.md_analysis_sif_sha256}'
    """
}

workflow {
    configs = Channel.fromPath(params.config, checkIfExists: true)
    bundles = Channel.fromPath(params.bundle, checkIfExists: true)
    replicas = Channel.fromPath(params.replicas, checkIfExists: true, type: 'dir').toList().map { it.sort { a, b -> a.name <=> b.name } }
    MD_JOIN_REPLICAS(configs, bundles, replicas)
    analysis_requests = MD_JOIN_REPLICAS.out.aggregate.flatMap { root ->
        def manifest = new groovy.json.JsonSlurper().parse(root.resolve('manifest.json').toFile())
        manifest.replicas.collect { replica -> tuple(replica.replica_index, root.resolve("replicas/replica_${replica.replica_index}")) }
    }
    NATIVE_FIXTURE_ANALYSIS(analysis_requests)
    MD_SEAL_RESULTS(MD_JOIN_REPLICAS.out.aggregate, NATIVE_FIXTURE_ANALYSIS.out.results.toList().map { it.sort { a, b -> a.name <=> b.name } })
}
