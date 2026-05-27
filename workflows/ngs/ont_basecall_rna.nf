#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { NANOPORE_METHYLATION } from './nanopore_methylation.nf'

workflow {
    NANOPORE_METHYLATION()
}
