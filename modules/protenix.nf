/*
 * ═══════════════════════════════════════════════════════════════════════════════
 * PROTENIX — Structure Prediction Module
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * ByteDance's open-source structure predictor (AF3-level accuracy).
 * Supports protein, DNA, RNA, ligand, and ion complexes.
 *
 * External: https://github.com/bytedance/Protenix
 * Paper:    https://www.biorxiv.org/content/10.1101/2025.01.08.631790
 *
 * Input format: JSON (proteinChain/dnaSequence/rnaSequence/ligand/ion entities)
 * Output: mmCIF structures + confidence.json (13+ metrics)
 */

// ─────────────────────────────────────────────────────────────────────────────
// PROTENIX PREDICT — Single-chain protein structure prediction
// ─────────────────────────────────────────────────────────────────────────────

process ProtenixPredict {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif", saveAs: { filename -> filename.split('/')[-1] }

    input:
    val sequence
    val sequence_name
    val protenix_model_weights
    val protenix_seeds
    val protenix_n_sample
    val protenix_n_step
    val protenix_n_cycle
    val protenix_use_msa
    val protenix_use_template
    val protenix_enable_cache
    val protenix_enable_fusion

    output:
    path "predictions/**/*.cif", emit: structures, optional: true
    path "predictions/**/confidence.json", emit: confidence, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_weights = protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = protenix_seeds ?: '42'
    def n_sample = protenix_n_sample ?: 5
    def n_step = protenix_n_step ?: 200
    def n_cycle = protenix_n_cycle ?: 10
    def use_template_flag = (protenix_use_template == true || protenix_use_template == 'true') ? '--use_template true' : ''
    def cache_flag = (protenix_enable_cache == true || protenix_enable_cache == 'true' || protenix_enable_cache == null) ? '--enable_cache' : ''
    def fusion_flag = (protenix_enable_fusion == true || protenix_enable_fusion == 'true' || protenix_enable_fusion == null) ? '--enable_fusion' : ''

    // Auto-switch to ESM model if MSA is disabled
    def use_msa = (protenix_use_msa == true || protenix_use_msa == 'true' || protenix_use_msa == null)
    def effective_weights = use_msa ? model_weights : 'protenix_esm_20241211_v0.2.1'

    """
    #!/bin/bash
    set -euo pipefail

    echo "[PROTENIX] Model: ${effective_weights} | Seeds: ${seeds} | Samples: ${n_sample} | Steps: ${n_step} | Cycles: ${n_cycle}"
    echo "[PROTENIX] MSA: ${use_msa} | Template: ${protenix_use_template ?: false}"

    # ═══════════════════════════════════════════════════════════════════════
    # Generate Protenix input JSON
    # Uses proteinChain entity format (NOT 'protein')
    # ═══════════════════════════════════════════════════════════════════════
    cat > input.json << 'ENDJSON'
[{
    "name": "${sequence_name}",
    "modelSeeds": [${seeds}],
    "sequences": [{
        "proteinChain": {
            "sequence": "${sequence}",
            "count": 1
        }
    }]
}]
ENDJSON

    # ═══════════════════════════════════════════════════════════════════════
    # Run MSA generation if enabled (using built-in protenix prep)
    # ═══════════════════════════════════════════════════════════════════════
    if [ "${use_msa}" = "true" ]; then
        echo "[PROTENIX] Running MSA generation via protenix prep..."
        protenix prep --input input.json --output_dir msa_output/ 2>&1 | tee protenix_msa.log || true
    fi

    # ═══════════════════════════════════════════════════════════════════════
    # Run structure prediction
    # ═══════════════════════════════════════════════════════════════════════
    echo "[PROTENIX] Running structure prediction..."
    protenix pred \\
        --input input.json \\
        --output_dir predictions/ \\
        --model_weights ${effective_weights} \\
        --sample_diffusion.N_sample ${n_sample} \\
        --sample_diffusion.N_step ${n_step} \\
        --num_cycle ${n_cycle} \\
        ${use_template_flag} \\
        ${cache_flag} \\
        ${fusion_flag} \\
        2>&1 | tee protenix_predict.log

    echo "[PROTENIX] Prediction complete. Listing outputs:"
    find predictions/ -name "*.cif" -o -name "confidence.json" | head -20
    """
}


// ─────────────────────────────────────────────────────────────────────────────
// PROTENIX FROM COMPLEX — Multi-chain complex prediction
// ─────────────────────────────────────────────────────────────────────────────
// Handles: protein + DNA + RNA + ligand + ion complexes
// Input: Pre-built Protenix-format JSON (from build_nextflow_command or UI)

process ProtenixFromComplex {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix_complex", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path complex_json
    val protenix_model_weights
    val protenix_seeds
    val protenix_n_sample
    val protenix_n_step
    val protenix_n_cycle
    val protenix_use_msa
    val protenix_use_template
    val protenix_enable_cache
    val protenix_enable_fusion

    output:
    path "predictions/**/*.cif", emit: structures, optional: true
    path "predictions/**/confidence.json", emit: confidence, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_weights = protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = protenix_seeds ?: '42'
    def n_sample = protenix_n_sample ?: 5
    def n_step = protenix_n_step ?: 200
    def n_cycle = protenix_n_cycle ?: 10
    def use_template_flag = (protenix_use_template == true || protenix_use_template == 'true') ? '--use_template true' : ''
    def cache_flag = (protenix_enable_cache == true || protenix_enable_cache == 'true' || protenix_enable_cache == null) ? '--enable_cache' : ''
    def fusion_flag = (protenix_enable_fusion == true || protenix_enable_fusion == 'true' || protenix_enable_fusion == null) ? '--enable_fusion' : ''

    def use_msa = (protenix_use_msa == true || protenix_use_msa == 'true' || protenix_use_msa == null)
    def effective_weights = use_msa ? model_weights : 'protenix_esm_20241211_v0.2.1'

    """
    #!/bin/bash
    set -euo pipefail

    echo "[PROTENIX-COMPLEX] Model: ${effective_weights} | Seeds: ${seeds} | Samples: ${n_sample}"
    echo "[PROTENIX-COMPLEX] Input JSON: ${complex_json}"
    cat ${complex_json}

    # Run MSA generation if enabled
    if [ "${use_msa}" = "true" ]; then
        echo "[PROTENIX-COMPLEX] Running MSA generation..."
        protenix prep --input ${complex_json} --output_dir msa_output/ 2>&1 | tee protenix_msa.log || true
    fi

    # Run structure prediction
    echo "[PROTENIX-COMPLEX] Running structure prediction..."
    protenix pred \\
        --input ${complex_json} \\
        --output_dir predictions/ \\
        --model_weights ${effective_weights} \\
        --sample_diffusion.N_sample ${n_sample} \\
        --sample_diffusion.N_step ${n_step} \\
        --num_cycle ${n_cycle} \\
        ${use_template_flag} \\
        ${cache_flag} \\
        ${fusion_flag} \\
        2>&1 | tee protenix_complex.log

    echo "[PROTENIX-COMPLEX] Prediction complete. Listing outputs:"
    find predictions/ -name "*.cif" -o -name "confidence.json" | head -20
    """
}
