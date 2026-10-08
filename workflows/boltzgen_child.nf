// Internal component entrypoint: the selected parent owns preparation, roster,
// global filtering and publication. Reuse the canonical generator unchanged.
nextflow.enable.dsl=2
include { RunBoltzGen } from '../modules/boltzgen.nf'

workflow {
    if (!params.boltzgen_yaml_config || !params.parent_job_id) {
        error('BoltzGen child requires prepared YAML and native parent identity')
    }
    RunBoltzGen(Channel.value(file(params.boltzgen_yaml_config, checkIfExists: true)))
}
