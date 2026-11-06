#!/usr/bin/env nextflow
nextflow.enable.dsl = 2
include { RunRFD4Proteina } from '../modules/rfd4_proteina'

workflow RFD4_PROTEINA {
    take:
    closure
    main:
    RunRFD4Proteina(closure)
    emit:
    native_outputs = RunRFD4Proteina.out.native_outputs
}

workflow {
    RFD4_PROTEINA(Channel.value(file(params.rfd4_input_closure, checkIfExists: true)))
}
