nextflow.enable.dsl = 2

include { RunPPIFlowGeneration } from '../modules/ppiflow.nf'

workflow {
    // One owned portable request tree contains separately bound target/framework
    // bytes, native settings and (when supplied) CSV dependency snapshots.
    RunPPIFlowGeneration(Channel.value(file(params.ppiflow_generation_request, checkIfExists: true)))
}
