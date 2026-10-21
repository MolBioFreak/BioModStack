#!/usr/bin/env nextflow
nextflow.enable.dsl=2

include { RunComplexa } from '../modules/complexa'

workflow COMPLEXA {
    take:
    request_directory
    main:
    RunComplexa(request_directory)
    emit:
    native_results = RunComplexa.out.native_results
}

workflow {
    COMPLEXA(Channel.value(file(params.complexa_request_dir, checkIfExists: true)))
}
