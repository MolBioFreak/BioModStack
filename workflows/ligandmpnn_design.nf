#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { RunLigandMPNNDesign } from '../modules/ligandmpnn_design'

workflow {
    RunLigandMPNNDesign(
        Channel.value(file(params.ligandmpnn_design_request, checkIfExists: true)),
        Channel.value(file(params.ligandmpnn_design_input, checkIfExists: true))
    )
}
