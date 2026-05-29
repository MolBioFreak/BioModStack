#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { complex_prediction_wf } from '../modules/structure_prediction.nf'
include { FrustrampnnQC ; AggregateFrustrationReports } from '../modules/frustrampnn.nf'

def parseJsonFile(rawPath) {
    return new groovy.json.JsonSlurper().parse(file(rawPath))
}

workflow COMPLEX_PREDICTION {
    main:
        if (!params.complex_json_path && !(params.sequence_batch_json_path && params.complex_batch_dir)) {
            error("complex_prediction requires --complex_json_path or --sequence_batch_json_path with --complex_batch_dir")
        }

        def numParallelJobs = params.num_parallel_jobs ?: 1
        def complex_name = params.sequence_name ?: 'complex_pred'
        def msa_file = params.msa_path ? file(params.msa_path) : file("${params.code_root}/NO_MSA")
        def complex_ch

        println("=" * 60)
        println("Complex Structure Prediction Workflow")
        println("=" * 60)
        if (params.complex_json_path) println("* Complex definition: ${params.complex_json_path}")
        if (params.sequence_batch_json_path) println("* Batch manifest: ${params.sequence_batch_json_path}")
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Number of simulations: ${numParallelJobs}")

        if (params.sequence_batch_json_path && params.complex_batch_dir) {
            def batchEntries = parseJsonFile(params.sequence_batch_json_path) as List
            println("* Batch variants: ${batchEntries.size()}")
            if ((params.pred_method ?: 'boltz') == 'protenix') {
                println("* Protenix complex batch mode: one model bootstrap for ${batchEntries.size()} variants")
                complex_ch = Channel.of(
                    tuple(
                        "${complex_name}_batch",
                        file(params.complex_batch_dir),
                        msa_file,
                    )
                )
            } else {
                complex_ch = Channel
                    .from(batchEntries)
                    .map { entry ->
                        tuple(
                            "${entry.name}",
                            file("${entry.complex_json}"),
                            msa_file,
                        )
                    }
            }
        } else {
            def complex_json = file(params.complex_json_path)
            def job_indices = Channel.from(0..<numParallelJobs)
            complex_ch = job_indices.map { idx ->
                def jobName = numParallelJobs > 1 ? "${complex_name}_job${idx}" : complex_name
                tuple(jobName, complex_json, msa_file)
            }
        }

        complex_prediction_wf(complex_ch)

        final_pdbs = complex_prediction_wf.out.structures
            .flatten()
            .collect()
            .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))

        if (params.run_frustrampnn == true) {
            println("Running FrustraMPNN post-analysis on complex predictions")
            def frustra_input = final_pdbs
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
        }

    emit:
        structures = final_pdbs
}

workflow {
    COMPLEX_PREDICTION()
}
