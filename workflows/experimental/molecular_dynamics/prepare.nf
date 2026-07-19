nextflow.enable.dsl=2

include { MD_PREPARE_CONFIG } from '../../../modules/experimental/molecular_dynamics/prepare'

params.md_job_config = null
params.md_input_root = null

workflow {
    if (!params.md_job_config) {
        error "--md_job_config is required"
    }
    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    base_dir = params.md_input_root ?: file(params.md_job_config).parent.toString()
    MD_PREPARE_CONFIG(config_ch, base_dir)
}
