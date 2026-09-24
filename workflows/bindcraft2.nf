// Prepared, model-owned campaign handoff. Not a public model route until the
// global typed schema, publication and bridge closure are integrated.
nextflow.enable.dsl=2
include { RunBindCraft2 } from '../modules/bindcraft2.nf'
include { PostprocessBindCraft2 } from '../modules/bindcraft2.nf'

workflow {
    def receipt = params.get('bc2_compilation')
    def destination = params.get('bc2_campaign_dir')
    if (!receipt || !destination) {
        error 'BindCraft2 requires compiler-owned bc2_compilation and bc2_campaign_dir'
    }
    if (!new File(destination.toString()).isAbsolute()) {
        error 'BindCraft2 campaign directory must be absolute and job-owned'
    }
    def compilationFile = file(receipt, checkIfExists: true)
    def compiled = new groovy.json.JsonSlurper().parseText(compilationFile.text)
    def operation = compiled.native_action?.operation ?: 'campaign'
    if (operation in ['campaign', 'resume']) {
        RunBindCraft2(Channel.value(compilationFile), Channel.value(destination.toString()))
    } else {
        PostprocessBindCraft2(Channel.value(compilationFile), Channel.value(destination.toString()))
    }
}
