nextflow.enable.dsl=2

include { MD_GROMACS_REPLICA } from '../../../modules/experimental/molecular_dynamics/gromacs_replica'
include { MD_OPENMM_REPLICA } from '../../../modules/experimental/molecular_dynamics/openmm_replica'

params.md_job_config = null
params.md_replica_index = null
params.md_engine = null
params.md_preparation_bundle = null
params.gpu_id = null

workflow {
    if (!params.md_job_config) error "--md_job_config is required"
    if (params.md_replica_index == null) error "--md_replica_index is required"
    if (!params.md_engine) error "--md_engine is required"
    if (params.gpu_id == null) error "--gpu_id is required"

    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    bundle_ch = Channel.fromPath(params.md_preparation_bundle, checkIfExists: true)
    request = config_ch.combine(bundle_ch).map { config, bundle ->
        tuple(params.md_replica_index as int, config, bundle)
    }

    engine = params.md_engine.toString()
    if (engine == 'gromacs') {
        MD_GROMACS_REPLICA(request)
    } else if (engine == 'openmm') {
        MD_OPENMM_REPLICA(request)
    } else {
        error "unsupported MD engine: ${params.md_engine}"
    }
}
