nextflow.enable.dsl=2

params.md_job_config = null
params.md_composed = true
params.md_input_root = null
params.job_id = null
params.gpu_id = null
params.md_analysis_enabled = (System.getenv('BMS_MD_ANALYSIS_ENABLED') ?: '0') == '1'
params.md_analysis_sif_sha256 = System.getenv('BMS_MD_ANALYSIS_SIF_SHA256') ?: '3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68'
params.md_analysis_implementation_sha256 = System.getenv('BMS_MD_ANALYSIS_IMPLEMENTATION_SHA256')
params.md_analysis_stride = 1
params.md_analysis_max_points = 2000

include { MD_PREPARE_CONFIG } from '../../../modules/experimental/molecular_dynamics/prepare'
include { MD_GROMACS_REPLICA } from '../../../modules/experimental/molecular_dynamics/gromacs_replica'
include { MD_OPENMM_REPLICA } from '../../../modules/experimental/molecular_dynamics/openmm_replica'
include { MD_ANALYZE_REPLICA } from '../../../modules/experimental/molecular_dynamics/analyze'
include { MD_JOIN_REPLICAS; MD_SEAL_RESULTS } from '../../../modules/experimental/molecular_dynamics/closure'

workflow MD_CLOSURE {
    if (!params.md_job_config) error '--md_job_config is required'
    if (!params.job_id) error '--job_id is required'
    if (params.gpu_id == null) error '--gpu_id is required'
    if (!params.md_analysis_enabled) error 'mandatory native MD analysis is disabled'
    if (!params.md_analysis_implementation_sha256) error 'MD analysis implementation identity is required'

    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    base_dir = params.md_input_root ?: file(params.md_job_config).parent.toString()
    MD_PREPARE_CONFIG(config_ch, base_dir)
    requests = MD_PREPARE_CONFIG.out.metadata
        .combine(MD_PREPARE_CONFIG.out.normalized_config)
        .combine(MD_PREPARE_CONFIG.out.preparation_bundle)
        .flatMap { metadata_file, config, bundle ->
            def metadata = new groovy.json.JsonSlurper().parse(metadata_file.toFile())
            def request = new groovy.json.JsonSlurper().parse(config.toFile())
            if (request.job_id != params.job_id.toString() || request.replicas != metadata.replicas || request.engine != metadata.engine)
                error 'MD prepared request identity does not match the bound workflow'
            if (!(metadata.engine in ['gromacs', 'openmm'])) error 'unsupported MD engine'
            (0..<(metadata.replicas as int)).collect { index ->
                tuple(metadata.engine, index, config, bundle)
            }
        }
    engines = requests.branch {
        gromacs: it[0] == 'gromacs'
        openmm: it[0] == 'openmm'
    }
    MD_GROMACS_REPLICA(engines.gromacs.map { engine, index, config, bundle -> tuple(index, config, bundle) })
    MD_OPENMM_REPLICA(engines.openmm.map { engine, index, config, bundle -> tuple(index, config, bundle) })
    // toList emits an empty list too: absence must fail, never suppress the join.
    replicas = MD_GROMACS_REPLICA.out.artifacts.mix(MD_OPENMM_REPLICA.out.artifacts).toList().map { it.sort { a, b -> a.name <=> b.name } }
    MD_JOIN_REPLICAS(MD_PREPARE_CONFIG.out.normalized_config, MD_PREPARE_CONFIG.out.preparation_bundle, replicas)
    analysis_requests = MD_JOIN_REPLICAS.out.aggregate.flatMap { root ->
        def aggregate = new groovy.json.JsonSlurper().parse(root.resolve('manifest.json').toFile())
        aggregate.replicas.collect { replica ->
            def directory = root.resolve("replicas/replica_${replica.replica_index}")
            def digest = java.security.MessageDigest.getInstance('SHA-256')
                .digest(directory.resolve('manifest.json').toFile().bytes).encodeHex().toString()
            tuple(replica.replica_index as int, directory, digest)
        }
    }
    MD_ANALYZE_REPLICA(analysis_requests)
    MD_SEAL_RESULTS(MD_JOIN_REPLICAS.out.aggregate, MD_ANALYZE_REPLICA.out.native_results.toList().map { it.sort { a, b -> a.name <=> b.name } })
}

workflow { MD_CLOSURE() }
