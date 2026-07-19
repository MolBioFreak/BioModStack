nextflow.enable.dsl=2

include { MD_GROMACS_REPLICA } from '../../../modules/experimental/molecular_dynamics/gromacs_replica'
include { MD_OPENMM_REPLICA } from '../../../modules/experimental/molecular_dynamics/openmm_replica'

params.md_job_config = null
params.md_replica_index = null
params.md_engine = null
params.gpu_id = null

workflow {
    if (!params.md_job_config) error "--md_job_config is required"
    if (params.md_replica_index == null) error "--md_replica_index is required"
    if (!params.md_engine) error "--md_engine is required"
    if (params.gpu_id == null) error "--gpu_id is required"

    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    request = config_ch.map { config -> tuple(params.md_replica_index as int, config) }

    switch (params.md_engine.toString()) {
        case 'gromacs':
            MD_GROMACS_REPLICA(request)
            break
        case 'openmm':
            MD_OPENMM_REPLICA(request)
            break
        default:
            error "unsupported MD engine: ${params.md_engine}"
    }
}
