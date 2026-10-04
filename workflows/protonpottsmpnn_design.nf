#!/usr/bin/env nextflow
nextflow.enable.dsl = 2
include { RunProtonPottsMPNNDesign } from '../modules/protonpottsmpnn'
workflow {
    RunProtonPottsMPNNDesign(
        Channel.value(file(params.protonpottsmpnn_design_request, checkIfExists: true)),
        Channel.value(file(params.protonpottsmpnn_design_input, checkIfExists: true))
    )
}
