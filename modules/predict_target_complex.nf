#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// =============================================================================
// Predict Target Complex - Boltz-2 protein-DNA/RNA complex prediction
// =============================================================================
// Optional upstream step for antibody design when only sequence data available.
// Predicts the target protein in complex with its cognate DNA/RNA.
// =============================================================================

process PredictTargetComplex {
    /*
     * Predict protein-DNA/RNA complex structure with Boltz-2.
     * 
     * Use case: When designing antibodies against proteins that form optimal
     * structures when bound to their cognate nucleic acid (e.g., transcription
     * factors like PAX6).
     * 
     * This is an OPTIONAL upstream step - if user provides a pre-computed
     * target PDB, this process is skipped entirely.
     */
    tag "${meta.id}"
    label 'Boltz'
    label 'gpu'
    
    publishDir "${params.out_dir}/target_complex", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/target_complex", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/target_complex", mode: 'copy', pattern: "*.log"
    
    input:
    tuple val(meta), val(protein_seq), val(dna_seq)
    
    output:
    tuple val(meta), path("target_complex.pdb"), emit: complex
    path "confidence.json", emit: confidence
    path "predict_complex.log", emit: log
    
    script:
    def dna_arg = dna_seq ? "--dna-seq '${dna_seq}'" : ""
    def msa_arg = params.target_use_msa ? "--msa-path ${params.target_msa_path}" : ""
    
    """
    set -euo pipefail
    
    echo "=== Predicting Target Complex ===" | tee predict_complex.log
    echo "Protein sequence length: ${protein_seq.length()}" | tee -a predict_complex.log
    ${dna_seq ? "echo 'DNA sequence length: ${dna_seq.length()}' | tee -a predict_complex.log" : "echo 'No DNA sequence provided' | tee -a predict_complex.log"}
    
    # Generate YAML for Boltz-2
    python3 ${projectDir}/scripts/prep_complex_yaml.py \\
        --protein-seq "${protein_seq}" \\
        ${dna_arg} \\
        --protein-id A \\
        --dna-id B \\
        --output complex.yaml \\
        2>&1 | tee -a predict_complex.log
    
    echo "YAML generated:" | tee -a predict_complex.log
    cat complex.yaml | tee -a predict_complex.log
    
    # Set up tmp directories
    mkdir -p tmp
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Run Boltz-2 prediction
    boltz predict \\
        complex.yaml \\
        --output_format pdb \\
        --recycling_steps ${params.target_complex_recycling ?: 3} \\
        --sampling_steps ${params.target_complex_sampling ?: 50} \\
        --diffusion_samples 1 \\
        ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        --cache /boltzcache \\
        2>&1 | tee -a predict_complex.log
    
    # Extract output files
    if [ -f boltz_results/predictions/complex/complex_model_0.pdb ]; then
        mv boltz_results/predictions/complex/complex_model_0.pdb target_complex.pdb
        echo "Complex structure saved to target_complex.pdb" | tee -a predict_complex.log
    else
        echo "ERROR: Boltz output not found" | tee -a predict_complex.log
        exit 1
    fi
    
    # Extract confidence JSON
    if [ -f boltz_results/predictions/complex/confidence_complex_model_0.json ]; then
        mv boltz_results/predictions/complex/confidence_complex_model_0.json confidence.json
    else
        echo "{}" > confidence.json
    fi
    
    echo "=== Complex Prediction Complete ===" | tee -a predict_complex.log
    """
}
