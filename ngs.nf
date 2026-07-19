#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { ONT_METHYLATION_ANALYSIS } from './workflows/ngs/ont_methylation_analysis.nf'
workflow {
    def selected = params.ont_workflow_id ?: params.mode ?: params.workflow
    if (selected == 'ont_methylation_analysis') {
        ONT_METHYLATION_ANALYSIS()
        return null
    }

    error("Unsupported NGS workflow selection. Expected ont_methylation_analysis mode.")
}
