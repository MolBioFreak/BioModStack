#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PrepProteinCadRequest ; RunLaProteina ; RunDISCO } from '../modules/protein_cad_experimental.nf'

workflow PROTEIN_CAD_EXPERIMENTAL {
    main:
        PrepProteinCadRequest()

        def backend = (params.pcad_backend ?: 'disco').toString().trim().toLowerCase()

        def native_pdbs
        def native_jsons
        def native_manifest

        if (backend == 'laproteina') {
            RunLaProteina(PrepProteinCadRequest.out.request, PrepProteinCadRequest.out.input_dir)
            native_pdbs = RunLaProteina.out.pdbs
            native_jsons = RunLaProteina.out.jsons
            native_manifest = RunLaProteina.out.manifest
        }
        else {
            RunDISCO(PrepProteinCadRequest.out.request, PrepProteinCadRequest.out.input_dir)
            native_pdbs = RunDISCO.out.pdbs
            native_jsons = RunDISCO.out.jsons
            native_manifest = RunDISCO.out.manifest
        }

    emit:
        pdbs = native_pdbs
        jsons = native_jsons
        manifest = native_manifest
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
