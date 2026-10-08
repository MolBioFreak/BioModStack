#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PrepDiffDock ; RunDiffDock } from '../modules/diffdock.nf'
include { PrepUniDock ; RunUniDock ; FilterUniDock } from '../modules/unidock.nf'

def paramOrBlank(String key) {
    return params.containsKey(key) && params[key] != null ? params[key] : ''
}

workflow DOCKING {
    main:
        def engine = (params.docking_engine ?: 'diffdock').toString().toLowerCase()
        def receptorPath = params.skip_input_dir ?: params.protein_pdb ?: params.input_pdb ?: params.receptor_pdb
        if (!receptorPath) {
            error("docking requires --skip_input_dir, --protein_pdb, --input_pdb, or --receptor_pdb")
        }

        def inputPath = file(receptorPath)
        if (!inputPath.exists()) {
            error("Receptor PDB path not found: ${receptorPath}")
        }

        def receptor_pdbs = inputPath.isFile()
            ? [inputPath]
            : inputPath.listFiles().findAll { child ->
                def lower = child.name.toLowerCase()
                lower.endsWith('.pdb') || lower.endsWith('.cif') || lower.endsWith('.mmcif')
            }
        if (receptor_pdbs.isEmpty()) {
            error("No receptor structure files found in: ${receptorPath}")
        }

        println("=" * 60)
        println("Docking Workflow")
        println("=" * 60)
        println("* Engine: ${engine}")
        println("* Receptors: ${receptor_pdbs.size()}")
        println("* Ligand SMILES: ${paramOrBlank('diffdock_ligand_smiles') ?: paramOrBlank('unidock_ligand_smiles') ?: 'N/A'}")
        println("* NTP Type: ${paramOrBlank('diffdock_ntp_type') ?: paramOrBlank('unidock_ntp_type') ?: 'N/A'}")

        if (engine in ['diffdock', 'dual', 'dual_docking', 'compare', 'consensus']) {
            def diffdock_input_pdbs = Channel.of(receptor_pdbs)
            def ligand_smiles = paramOrBlank('diffdock_ligand_smiles') ?: paramOrBlank('ligand_smiles') ?: ''
            def ntp_type = paramOrBlank('diffdock_ntp_type') ?: paramOrBlank('ntp_type') ?: ''
            PrepDiffDock(diffdock_input_pdbs, ligand_smiles, ntp_type)
            def diffdock_input = PrepDiffDock.out.csv
                .combine(PrepDiffDock.out.pdbs.collect().map { pdbs -> [pdbs] })
                .map { csv, pdbs -> tuple("batch_0", csv, pdbs) }
            RunDiffDock(diffdock_input)
        }

        if (engine in ['unidock', 'dual', 'dual_docking', 'compare', 'consensus']) {
            def receptor_ch = Channel.from(receptor_pdbs)
            PrepUniDock(
                receptor_ch,
                paramOrBlank('unidock_ligand_smiles') ?: paramOrBlank('ligand_smiles') ?: '',
                paramOrBlank('unidock_ntp_type') ?: paramOrBlank('ntp_type') ?: '',
                paramOrBlank('unidock_box_size'),
                paramOrBlank('unidock_box_center') ?: '',
                paramOrBlank('unidock_flexible_residues') ?: '',
            )
            def flex_receptor = PrepUniDock.out.flex_receptor.ifEmpty(file('NO_FLEX'))
            def unidock_input = PrepUniDock.out.receptor
                .combine(flex_receptor)
                .combine(PrepUniDock.out.ligand_dir)
                .combine(PrepUniDock.out.box)
                .map { receptor, flex, ligands, box ->
                    tuple("unidock_0", receptor, flex, ligands, box)
                }
            RunUniDock(unidock_input)
            FilterUniDock(RunUniDock.out.poses.collect(), RunUniDock.out.scores)
        }
}

workflow {
    DOCKING()
}
