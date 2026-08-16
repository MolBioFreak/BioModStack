#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Backward-compatible wrapper only. BioModStack API launches route directly to
// workflow-specific entrypoints; the legacy/core protein-design implementation
// lives in workflows/protein_design.nf.
include { PROTEIN_DESIGN } from './workflows/protein_design.nf'

workflow {
    PROTEIN_DESIGN()
}
