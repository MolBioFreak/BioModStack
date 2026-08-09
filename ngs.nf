#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { ONT_METHYLATION_ANALYSIS } from './workflows/ngs/ont_methylation_analysis.nf'
include { ONT_POOLED_REFERENCE_ASSIGNMENT } from './workflows/ngs/ont_pooled_reference_assignment.nf'
workflow {
    def selected = params.ont_workflow_id ?: params.mode ?: params.workflow
    if (selected == 'ont_methylation_analysis') {
        ONT_METHYLATION_ANALYSIS()
        return null
    }
    if (selected == 'ont_pooled_reference_assignment') {
        ONT_POOLED_REFERENCE_ASSIGNMENT()
        return null
    }

    error("Unsupported NGS workflow selection. Expected ont_methylation_analysis or ont_pooled_reference_assignment mode.")
}
