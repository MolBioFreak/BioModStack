nextflow.enable.dsl=2

include { MD_PREPARE_CONFIG } from '../../../modules/experimental/molecular_dynamics/prepare'
include { MD_GROMACS_REPLICA } from '../../../modules/experimental/molecular_dynamics/gromacs_replica'
include { MD_OPENMM_REPLICA } from '../../../modules/experimental/molecular_dynamics/openmm_replica'
include { MD_FINALIZE_RESULTS } from '../../../modules/experimental/molecular_dynamics/finalize'

params.md_job_config = null
params.md_input_root = null
params.gpu_id = null
params.allow_local_composed_md = false

workflow {
    if (!params.allow_local_composed_md) {
        error "Local composed MD is disabled; BMS must launch prepare, singleton replica, and finalize children durably"
    }
    if (!params.md_job_config) error "--md_job_config is required"
    if (params.gpu_id == null) error "--gpu_id is required"

    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    base_dir = params.md_input_root ?: file(params.md_job_config).parent.toString()
    MD_PREPARE_CONFIG(config_ch, base_dir, params.gpu_id.toString())

    requests = MD_PREPARE_CONFIG.out.metadata
        .combine(MD_PREPARE_CONFIG.out.normalized_config)
        .flatMap { metadata_path, normalized_config ->
            def metadata = new groovy.json.JsonSlurper().parse(metadata_path.toFile())
            (0..<metadata.replicas as int).collect { replica_index ->
                tuple(metadata.engine as String, replica_index as int, normalized_config)
            }
        }

    engine_requests = requests.branch {
        gromacs: it[0] == 'gromacs'
        openmm: it[0] == 'openmm'
    }
    gromacs_requests = engine_requests.gromacs.map { engine, replica_index, config -> tuple(replica_index, config) }
    openmm_requests = engine_requests.openmm.map { engine, replica_index, config -> tuple(replica_index, config) }

    MD_GROMACS_REPLICA(gromacs_requests)
    MD_OPENMM_REPLICA(openmm_requests)

    replica_manifests = MD_GROMACS_REPLICA.out.manifest.mix(MD_OPENMM_REPLICA.out.manifest).collect()
    MD_FINALIZE_RESULTS(replica_manifests)
}
