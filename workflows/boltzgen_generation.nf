// Public model-owned generation; native execution and selection stay in the
// existing module. Internal children retain their separate parent-bound route.
nextflow.enable.dsl=2
include { PrepBoltzGenInput; RunBoltzGen; FilterBoltzGen } from '../modules/boltzgen.nf'

workflow {
    def supplied = params.get('boltzgen_yaml_config')
    if (supplied) {
        prepared = Channel.value(file(supplied, checkIfExists: true))
    } else {
        def source = { key, sentinel -> params.get(key) ? file(params.get(key), checkIfExists: true) : file("${params.code_root}/lib/${sentinel}") }
        // Generic structural scaffold occupies the existing backbone input slot;
        // Prep maps that slot to native scaffold_path only for generic modes.
        def backboneKey = params.get('boltzgen_scaffold_path') ? 'boltzgen_scaffold_path' : 'boltzgen_input_pdb'
        PrepBoltzGenInput(
            params.get('boltzgen_ligand_smiles') ?: '',
            params.get('boltzgen_ntp_type') ?: '',
            params.get('boltzgen_scaffold_length') ?: '80-120',
            params.get('boltzgen_num_designs') ?: 10,
            params.get('boltzgen_binding_site_residues') ?: '',
            params.get('boltzgen_catalytic_site') ?: false,
            params.get('boltzgen_protein_sequence') ?: '',
            params.get('boltzgen_dna_template_seq') ?: '',
            params.get('boltzgen_dna_primer_seq') ?: '',
            params.get('boltzgen_secondary_structure') ?: '',
            params.get('boltzgen_protocol') ?: 'auto',
            params.get('boltzgen_covalent_bonds') ?: '',
            params.get('boltzgen_nanobody_framework') ?: '',
            params.get('boltzgen_cdr_h1_length') ?: '',
            params.get('boltzgen_cdr_h2_length') ?: '',
            params.get('boltzgen_cdr_h3_length') ?: '',
            source(backboneKey, 'NO_INPUT_PDB'),
            source('boltzgen_ligand_pdb', 'NO_LIGAND_PDB'),
            source('boltzgen_dna_structure', 'NO_DNA_STRUCT'),
            source('boltzgen_target_pdb_path', 'NO_TARGET_PDB')
        )
        prepared = PrepBoltzGenInput.out.yaml
    }
    RunBoltzGen(prepared)
    // Empty native generation remains empty; never manufacture candidates or
    // launch an invalid empty-argv filter task to enforce a success count.
    structures = RunBoltzGen.out.pdbs.flatten().collect().filter { !it.isEmpty() }
    evidence = RunBoltzGen.out.jsons.flatten().collect()
    FilterBoltzGen(structures, evidence)
}
