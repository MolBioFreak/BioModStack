#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PrepProteinHunterRequest ; RunProteinHunter ; FinalizeProteinHunterOutputs } from '../modules/protein_hunter_experimental.nf'

workflow PROTEIN_HUNTER_EXPERIMENTAL {
    main:
        PrepProteinHunterRequest()
        RunProteinHunter(PrepProteinHunterRequest.out.request, PrepProteinHunterRequest.out.input_dir)
        FinalizeProteinHunterOutputs(
            RunProteinHunter.out.pdbs.collect(),
            RunProteinHunter.out.jsons.collect(),
            RunProteinHunter.out.manifest,
        )

    emit:
        pdbs = FinalizeProteinHunterOutputs.out.pdbs
        jsons = FinalizeProteinHunterOutputs.out.jsons
        manifest = FinalizeProteinHunterOutputs.out.manifest
}

workflow {
    println("=" * 60)
    println("Protein Hunter Experimental Workflow")
    println("=" * 60)
    println("* Backend: ${params.ph_backend}")
    println("* Task: ${params.ph_task}")
    println("* Num designs: ${params.ph_num_designs}")
    println("* Num cycles: ${params.ph_num_cycles}")
    PROTEIN_HUNTER_EXPERIMENTAL()
}
