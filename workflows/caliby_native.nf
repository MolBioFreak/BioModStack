#!/usr/bin/env nextflow
nextflow.enable.dsl=2

include { RunCalibyNative } from '../modules/caliby_native'

workflow {
    RunCalibyNative(Channel.value(file(params.caliby_request_dir, checkIfExists: true)))
}
