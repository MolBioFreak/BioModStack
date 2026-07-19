nextflow.enable.dsl=2

include { MD_FINALIZE_RESULTS } from '../../../modules/experimental/molecular_dynamics/finalize'

params.replica_manifests = null

workflow {
    if (!params.replica_manifests) error "--replica_manifests glob is required"
    manifest_ch = Channel.fromPath(params.replica_manifests, checkIfExists: true).collect()
    MD_FINALIZE_RESULTS(manifest_ch)
}
