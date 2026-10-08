nextflow.enable.dsl = 2

process PrepProteinHunterRequest {
    label 'process_low'

    publishDir "${params.out_dir}/inputs/protein_hunter", mode: 'copy', pattern: 'protein_hunter_request.json'
    publishDir "${params.out_dir}/inputs/protein_hunter", mode: 'copy', pattern: 'protein_hunter_inputs/*', saveAs: { filename -> filename.replace('protein_hunter_inputs/', '') }

    output:
    path 'protein_hunter_request.json', emit: request
    path 'protein_hunter_inputs', emit: input_dir

    script:
    def boltzModelPath = params.ph_boltz_model_path ?: params.boltz_model_path ?: "/weights/boltz/boltz2_conf.ckpt"
    def boltzCcdPath = params.ph_boltz_ccd_path ?: params.boltz_ccd_path ?: "/weights/boltz/mols"
    """
    mkdir -p protein_hunter_inputs

    python3 ${params.code_root}/scripts/prep_protein_hunter_request.py \\
        --job-id "${params.job_id ?: 'unknown'}" \\
        --job-name "${params.name ?: params.batch_name ?: 'protein_hunter_experimental'}" \\
        --backend "${params.ph_backend ?: 'boltz'}" \\
        --task "${params.ph_task ?: 'protein_binder'}" \\
        --num-designs ${params.ph_num_designs ?: 4} \\
        --num-cycles ${params.ph_num_cycles ?: 7} \\
        --min-protein-length ${params.ph_min_protein_length ?: 90} \\
        --max-protein-length ${params.ph_max_protein_length ?: 150} \\
        --percent-x ${params.ph_percent_x ?: 50} \\
        --seed-binder-sequence "${params.ph_seed_binder_sequence ?: ''}" \\
        --target-protein-sequences "${params.ph_target_protein_sequences ?: ''}" \\
        --target-pdb "${params.ph_target_pdb ?: ''}" \\
        --target-pdb-chain "${params.ph_target_pdb_chain ?: ''}" \\
        --target-template-path "${params.ph_target_template_path ?: ''}" \\
        --target-template-chain-id "${params.ph_target_template_chain_id ?: ''}" \\
        --ligand-smiles "${params.ph_ligand_smiles ?: ''}" \\
        --ligand-ccd "${params.ph_ligand_ccd ?: ''}" \\
        --nucleic-sequence "${params.ph_nucleic_sequence ?: ''}" \\
        --nucleic-type "${params.ph_nucleic_type ?: 'rna'}" \\
        --contact-residues "${params.ph_contact_residues ?: ''}" \\
        --cyclic "${params.ph_cyclic ?: false}" \\
        --alanine-bias "${params.ph_alanine_bias ?: true}" \\
        --temperature "${params.ph_temperature ?: 0.1}" \\
        --high-iptm-threshold "${params.ph_high_iptm_threshold ?: 0.7}" \\
        --high-plddt-threshold "${params.ph_high_plddt_threshold ?: 0.8}" \\
        --msa-mode "${params.ph_msa_mode ?: 'mmseqs'}" \\
        --boltz-model-version "${params.ph_boltz_model_version ?: 'boltz2'}" \\
        --boltz-model-path "${boltzModelPath}" \\
        --boltz-ccd-path "${boltzCcdPath}" \\
        --chai-hysteresis-mode "${params.ph_chai_hysteresis_mode ?: 'templates'}" \\
        --chai-num-recycles ${params.ph_chai_num_recycles ?: 3} \\
        --chai-num-diff-steps ${params.ph_chai_num_diff_steps ?: 200} \\
        --chai-repredict "${params.ph_chai_repredict ?: true}" \\
        --output protein_hunter_request.json \\
        --input-dir protein_hunter_inputs
    """
}

process RunProteinHunter {
    label 'ProteinHunter'
    label 'gpu'

    publishDir "${params.out_dir}/collected/protein_hunter_raw", mode: 'copy', pattern: 'raw/pdbs/*.pdb', saveAs: { filename -> filename.replace('raw/pdbs/', '') }
    publishDir "${params.out_dir}/collected/protein_hunter_raw", mode: 'copy', pattern: 'raw/metadata/*.json', saveAs: { filename -> filename.replace('raw/metadata/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'design_manifest.json'
    publishDir "${params.out_dir}/run/protein_hunter", mode: 'copy', pattern: '*.log'

    input:
    path request_json
    path input_dir

    output:
    path 'raw/pdbs/*.pdb', emit: pdbs
    path 'raw/metadata/*.json', emit: jsons
    path 'design_manifest.json', emit: manifest
    path '*.log'

    stub:
    """
    mkdir -p raw/pdbs raw/metadata
    cat > raw/pdbs/protein_hunter_stub_0001.pdb <<'EOF'
ATOM      1  N   GLY A   1      11.104  13.207   9.447  1.00 20.00           N
ATOM      2  CA  GLY A   1      12.104  13.207   9.447  1.00 20.00           C
ATOM      3  C   GLY A   1      12.804  14.507   9.947  1.00 20.00           C
ATOM      4  O   GLY A   1      12.304  15.607   9.747  1.00 20.00           O
TER
END
EOF
    cat > raw/metadata/generator_protein_hunter_stub_0001.json <<'EOF'
{"design_id":"protein_hunter_stub_0001","source":"protein_hunter","source_model":"Protein Hunter (stub)","generator_family":"protein_hunter_experimental"}
EOF
    cat > design_manifest.json <<'EOF'
[{"design_id":"protein_hunter_stub_0001","sequence":"G","structure_path":"raw/pdbs/protein_hunter_stub_0001.pdb","metadata_path":"raw/metadata/generator_protein_hunter_stub_0001.json"}]
EOF
    echo "Protein Hunter stub run" > protein_hunter.log
    """

    script:
    """
    python3 ${params.code_root}/scripts/run_protein_hunter_inference.py \\
        --request ${request_json} \\
        --input-dir ${input_dir} \\
        --output-dir .
    """
}

process FinalizeProteinHunterOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'published/*.pdb', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'published/confidence_*.json', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'published/design_manifest.json', saveAs: { filename -> filename.replace('published/', '') }

    input:
    path pdb_files
    path metadata_jsons
    path design_manifest

    output:
    path 'published/*.pdb', emit: pdbs
    path 'published/confidence_*.json', emit: jsons
    path 'published/design_manifest.json', emit: manifest

    script:
    """
    mkdir -p published
    cp ${pdb_files} published/
    cp ${design_manifest} published/design_manifest.json
    for meta in ${metadata_jsons}; do
        base=\$(basename "\$meta")
        target="\${base#generator_}"
        cp "\$meta" "published/confidence_\${target}"
    done
    """
}
