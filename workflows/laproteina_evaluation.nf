#!/usr/bin/env nextflow
nextflow.enable.dsl = 2
include { EvaluateLaProteina } from '../modules/laproteina_evaluation'
workflow {
    EvaluateLaProteina(Channel.value(file(params.laproteina_evaluation_input_closure, checkIfExists: true)))
}
