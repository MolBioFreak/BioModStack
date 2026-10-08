#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PrepCalibyRequest ; RunCalibyExperimental ; FinalizeCalibyExperimentalOutputs } from '../modules/caliby_experimental.nf'

workflow CALIBY_EXPERIMENTAL {
    main:
        PrepCalibyRequest()
        RunCalibyExperimental(PrepCalibyRequest.out.request, PrepCalibyRequest.out.input_dir)
        FinalizeCalibyExperimentalOutputs(
            RunCalibyExperimental.out.pdbs.collect(),
            RunCalibyExperimental.out.jsons.collect(),
            RunCalibyExperimental.out.manifest,
        )

    emit:
        pdbs = FinalizeCalibyExperimentalOutputs.out.pdbs
        jsons = FinalizeCalibyExperimentalOutputs.out.jsons
        manifest = FinalizeCalibyExperimentalOutputs.out.manifest
}

workflow {
    println("=" * 60)
    println("Caliby Experimental Workflow")
    println("=" * 60)
    println("* Task: ${params.caliby_task}")
    println("* Model: ${params.caliby_model_name}")
    println("* Num seqs per structure: ${params.caliby_num_seqs_per_pdb}")
    CALIBY_EXPERIMENTAL()
}
