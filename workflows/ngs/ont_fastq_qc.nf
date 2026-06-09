#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Product-specific ONT/NGS entrypoint. These defaults make direct CLI launches
// match the API registry while still allowing explicit --param overrides.
params.ont_workflow_id = params.ont_workflow_id ?: 'ont_fastq_qc'
params.ont_molecule_type = params.ont_molecule_type ?: 'dna'
params.run_modkit = params.run_modkit != null ? params.run_modkit : false
params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true
params.modified_bases = params.modified_bases ?: 'none'
params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'
params.manifest_contract = params.manifest_contract ?: 'sequence_qc.manifest.v1'

include { NANOPORE_METHYLATION } from './nanopore_methylation.nf'

workflow {
    NANOPORE_METHYLATION()
}
