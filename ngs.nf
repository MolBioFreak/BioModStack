#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Legacy root compatibility wrapper. The canonical workflow and registry ID are
// ont_methylation_analysis; retain the old callable name only at this boundary.
include { ONT_METHYLATION_ANALYSIS as NANOPORE_METHYLATION } from './workflows/ngs/ont_methylation_analysis.nf'

workflow {
    if (params.nanopore_enabled || params.rfd_mode == 'nanopore_methylation') {
        NANOPORE_METHYLATION()
        return null
    }

    error("Unsupported NGS workflow selection. Expected nanopore_methylation mode.")
}
