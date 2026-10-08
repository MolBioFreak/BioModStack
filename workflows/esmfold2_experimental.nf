#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { RunESMFold2Experimental } from '../modules/esmfold2_experimental.nf'

workflow ESMFOLD2_EXPERIMENTAL {
    main:
        def sequence = (params.get('esmf_sequence', params.get('sequence_input', '')) ?: '').toString().trim()
        def componentJson = (params.get('esmf_complex_components_json', '') ?: '').toString().trim()
        def componentFile = (params.get('esmf_complex_components_file', '') ?: '').toString().trim()
        def pdbSequencePath = (params.get('esmf_pdb_sequence_path', '') ?: '').toString().trim()
        if (!sequence && !componentJson && !componentFile && !pdbSequencePath) {
            error("esmfold2_experimental requires a primary protein sequence, PDB sequence source, or Components JSON")
        }
        if (sequence.contains(':') || sequence.contains(',') || sequence.contains(';') || sequence.contains('/')) {
            error("esmf_sequence must be one protein chain; use Components JSON or PDB sequence source for complexes")
        }
        def variant = (params.get('esmf_model_variant', 'fast') ?: 'fast').toString()
        if (!(variant in ['fast', 'full'])) {
            error("esmf_model_variant must be one of: fast, full")
        }

        RunESMFold2Experimental()

    emit:
        results_dir = RunESMFold2Experimental.out.results_dir
        cifs = RunESMFold2Experimental.out.cifs
        jsons = RunESMFold2Experimental.out.jsons
}

workflow {
    println("=" * 60)
    println("ESMFold2 Experimental Workflow")
    println("=" * 60)
    println("* Variant: ${params.get('esmf_model_variant', 'fast')}")
    println("* Model: ${params.get('esmf_model_id_or_path', 'biohub/ESMFold2-Fast')}")
    println("* Local files only: ${params.get('esmf_local_files_only', true)}")
    println("* Complex inputs supported: true")
    println("* PDB coordinates are sequence sources only: true")
    ESMFOLD2_EXPERIMENTAL()
}
