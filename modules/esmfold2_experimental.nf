nextflow.enable.dsl = 2


def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}


def boolString(value) {
    return value?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'
}


process RunESMFold2Experimental {
    label 'ESMFold2'
    label 'gpu'

    publishDir "${params.out_dir}/final/esmfold2", mode: 'copy', pattern: 'esmfold2_results/**/*', saveAs: { filename -> filename.replace('esmfold2_results/', '') }
    publishDir "${params.out_dir}/run/esmfold2", mode: 'copy', pattern: '*.log'

    output:
    path 'esmfold2_results', emit: results_dir
    path 'esmfold2_results/*.cif', emit: cifs, optional: true
    path 'esmfold2_results/*.json', emit: jsons, optional: true
    path '*.log'

    script:
    def sequence = shellQuote(params.get('esmf_sequence', params.get('sequence_input', '')))
    def sequenceName = shellQuote(params.get('esmf_sequence_name', params.get('sequence_name', 'esmfold2_candidate')))
    def chainId = shellQuote(params.get('esmf_chain_id', 'A'))
    def complexComponentsJson = shellQuote(params.get('esmf_complex_components_json', ''))
    def complexComponentsFile = shellQuote(params.get('esmf_complex_components_file', ''))
    def pdbSequencePath = shellQuote(params.get('esmf_pdb_sequence_path', ''))
    def pdbChainIds = shellQuote(params.get('esmf_pdb_chain_ids', ''))
    def pdbIncludeDnaRna = shellQuote(boolString(params.get('esmf_pdb_include_dna_rna', true)))
    def msaPath = shellQuote(params.get('esmf_msa_path', ''))
    def msaFormat = shellQuote(params.get('esmf_msa_format', 'auto'))
    def msaMaxSequencesValue = params.get('esmf_msa_max_sequences', '')
    def msaMaxSequencesArg = msaMaxSequencesValue == null || msaMaxSequencesValue.toString().trim() == '' ? '' : "--msa-max-sequences ${msaMaxSequencesValue}"
    def msaRemoveInsertions = shellQuote(boolString(params.get('esmf_msa_remove_insertions', true)))
    def dnaSequence = shellQuote(params.get('esmf_dna_sequence', ''))
    def dnaChainId = shellQuote(params.get('esmf_dna_chain_id', 'C'))
    def rnaSequence = shellQuote(params.get('esmf_rna_sequence', ''))
    def rnaChainId = shellQuote(params.get('esmf_rna_chain_id', 'D'))
    def ligandSmiles = shellQuote(params.get('esmf_ligand_smiles', ''))
    def ligandCcd = shellQuote(params.get('esmf_ligand_ccd', ''))
    def ligandChainId = shellQuote(params.get('esmf_ligand_chain_id', 'L'))
    def modelVariant = shellQuote(params.get('esmf_model_variant', 'fast'))
    def modelIdOrPath = shellQuote(params.get('esmf_model_id_or_path', ''))
    def localFilesOnly = shellQuote(boolString(params.get('esmf_local_files_only', true)))
    def numLoops = params.get('esmf_num_loops', 3)
    def numSamplingSteps = params.get('esmf_num_sampling_steps', 50)
    def numDiffusionSamples = params.get('esmf_num_diffusion_samples', 1)
    def seedValue = params.get('esmf_seed', '')
    def seedArg = seedValue == null || seedValue.toString().trim() == '' ? '' : "--seed ${seedValue}"
    def device = shellQuote(params.get('esmf_device', 'auto'))
    def outDir = shellQuote(params.out_dir)
    """
    set -euo pipefail
    python3 /scripts/run_esmfold2_inference.py \
        --sequence ${sequence} \
        --sequence-name ${sequenceName} \
        --chain-id ${chainId} \
        --complex-components-json ${complexComponentsJson} \
        --complex-components-file ${complexComponentsFile} \
        --pdb-sequence-path ${pdbSequencePath} \
        --pdb-chain-ids ${pdbChainIds} \
        --pdb-include-dna-rna ${pdbIncludeDnaRna} \
        --msa-path ${msaPath} \
        --msa-format ${msaFormat} \
        ${msaMaxSequencesArg} \
        --msa-remove-insertions ${msaRemoveInsertions} \
        --dna-sequence ${dnaSequence} \
        --dna-chain-id ${dnaChainId} \
        --rna-sequence ${rnaSequence} \
        --rna-chain-id ${rnaChainId} \
        --ligand-smiles ${ligandSmiles} \
        --ligand-ccd ${ligandCcd} \
        --ligand-chain-id ${ligandChainId} \
        --model-variant ${modelVariant} \
        --model-id-or-path ${modelIdOrPath} \
        --local-files-only ${localFilesOnly} \
        --num-loops ${numLoops} \
        --num-sampling-steps ${numSamplingSteps} \
        --num-diffusion-samples ${numDiffusionSamples} \
        ${seedArg} \
        --device ${device} \
        --output-dir esmfold2_results \
        2>&1 | tee run_esmfold2.log
    mkdir -p ${outDir}/final/esmfold2 ${outDir}/pdb_files
    cp -R esmfold2_results/. ${outDir}/final/esmfold2/
    for artifact in esmfold2_results/*.cif esmfold2_results/*.json; do
        [ -e "\$artifact" ] || continue
        cp "\$artifact" ${outDir}/pdb_files/
    done
    """
}
