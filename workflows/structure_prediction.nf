#!/usr/bin/env nextflow
/**
 * Structure Prediction Workflow
 * 
 * Standalone entry point for sequence-to-structure prediction using Boltz-2 or RF3.
 * 
 * Usage:
 *   nextflow run workflows/structure_prediction.nf -c nextflow.config \
 *     --sequence_input "MKTLLILAVVAAALA..." \
 *     --sequence_name "my_protein" \
 *     --pred_method boltz
 */

nextflow.enable.dsl = 2

include { structure_prediction_wf } from '../modules/structure_prediction.nf'
include { FrustrampnnQC ; AggregateFrustrationReports } from '../modules/frustrampnn.nf'

// Workflow-specific param defaults
params.sequence_input = null
params.sequence_name = 'predicted'
params.pred_method = 'boltz'
params.num_parallel_jobs = 1
params.run_frustrampnn = false

workflow STRUCTURE_PREDICTION {
    take:
        sequence_ch  // Channel of [sequence, name]
    
    main:
        structure_prediction_wf(sequence_ch)
        
        structures = structure_prediction_wf.out.structures
            .flatten()
            .collect()
        
        // Optional FrustraMPNN QC
        if (params.run_frustrampnn == true) {
            def frustra_input = structures
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            AggregateFrustrationReports(FrustrampnnQC.out.summary.collect())
        }
    
    emit:
        structures
}

// Entry point for direct invocation
workflow {
    // Validate inputs
    if (!params.sequence_input) {
        error("--sequence_input is required. Provide amino acid sequence.")
    }
    
    def seq = params.sequence_input
    def name = params.sequence_name ?: 'predicted'
    def numJobs = params.num_parallel_jobs ?: 1
    
    println("=" * 60)
    println("Structure Prediction Workflow")
    println("=" * 60)
    println("* Sequence: ${seq.take(50)}${seq.length() > 50 ? '...' : ''}")
    println("* Name: ${name}")
    println("* Predictor: ${params.pred_method}")
    println("* Parallel jobs: ${numJobs}")
    println("* FrustraMPNN QC: ${params.run_frustrampnn}")
    println("=" * 60)
    
    // Create parallel job channels
    def job_indices = Channel.from(0..<numJobs)
    def input_ch = job_indices.map { idx ->
        def jobName = numJobs > 1 ? "${name}_job${idx}" : name
        tuple(seq, jobName)
    }
    
    STRUCTURE_PREDICTION(input_ch)
}
