#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PrepProteinCadRequest ; RunLaProteina ; RunDISCO ; FinalizeProteinCadOutputs } from '../modules/protein_cad_experimental.nf'

workflow PROTEIN_CAD_EXPERIMENTAL {
    main:
        PrepProteinCadRequest()

        def backend = (params.pcad_backend ?: 'disco').toString().trim().toLowerCase()

        if (backend == 'laproteina') {
            RunLaProteina(PrepProteinCadRequest.out.request, PrepProteinCadRequest.out.input_dir)
            FinalizeProteinCadOutputs(
                RunLaProteina.out.pdbs.collect(),
                RunLaProteina.out.jsons.collect(),
                RunLaProteina.out.manifest,
            )
        }
        else {
            RunDISCO(PrepProteinCadRequest.out.request, PrepProteinCadRequest.out.input_dir)
            FinalizeProteinCadOutputs(
                RunDISCO.out.pdbs.collect(),
                RunDISCO.out.jsons.collect(),
                RunDISCO.out.manifest,
            )
        }

    emit:
        pdbs = FinalizeProteinCadOutputs.out.pdbs
        jsons = FinalizeProteinCadOutputs.out.jsons
        manifest = FinalizeProteinCadOutputs.out.manifest
}

workflow {
    println("=" * 60)
    println("Protein CAD Experimental Workflow")
    println("=" * 60)
    println("* Backend: ${params.pcad_backend}")
    println("* Task: ${params.pcad_task}")
    println("* Num designs: ${params.pcad_num_designs}")
    println("* Target lengths: ${params.pcad_target_lengths}")
    PROTEIN_CAD_EXPERIMENTAL()
}
