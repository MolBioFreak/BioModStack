#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { NANOPORE_METHYLATION } from './workflows/ngs/nanopore_methylation.nf'

workflow {
    if (params.nanopore_enabled || params.rfd_mode == 'nanopore_methylation') {
        NANOPORE_METHYLATION()
        return null
    }

    error("Unsupported NGS workflow selection. Expected nanopore_methylation mode.")
}
