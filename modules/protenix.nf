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
    tuple val(sequence), val(sequence_name)

    output:
    path "predictions/**/*.cif", emit: cifs, optional: true
    path "predictions/**/confidence.json", emit: confidence, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_weights = params.protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def use_template_flag = (params.protenix_use_template == true || params.protenix_use_template == 'true') ? '--use_template true' : ''
    def cache_flag = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null) ? '--enable_cache' : ''
    def fusion_flag = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null) ? '--enable_fusion' : ''

    // Auto-switch to ESM model if MSA is disabled
    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
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
// PREP PROTENIX COMPLEX — Convert BMS complex JSON → Protenix-format JSON
// ─────────────────────────────────────────────────────────────────────────────
// BMS format:  {"components": [{"type": "protein", "id": "A", "sequence": "..."}]}
// Protenix:    [{"name": "...", "modelSeeds": [42], "sequences": [{"proteinChain": {"sequence": "...", "count": 1}}]}]

process PrepProtenixComplex {
    label 'CPU'

    input:
    tuple val(name), path(complex_json), path(msa_file)

    output:
    path "protenix_input.json", emit: protenix_json

    script:
    def seeds = params.protenix_seeds ?: '42'
    """
    #!/usr/bin/env python3
    import json, sys

    with open("${complex_json}") as f:
        bms = json.load(f)

    type_map = {
        'protein': 'proteinChain',
        'dna':     'dnaSequence',
        'rna':     'rnaSequence',
    }

    sequences = []
    for comp in bms.get('components', []):
        t = comp.get('type', 'protein').lower()
        seq = comp.get('sequence', '')
        if t in type_map:
            sequences.append({type_map[t]: {"sequence": seq, "count": 1}})
        elif t == 'ligand':
            ccd = comp.get('ccd', '')
            smiles = comp.get('smiles', '')
            entry = {}
            if ccd:
                entry = {"ligand": {"ligand": ccd, "count": 1}}
            elif smiles:
                entry = {"ligand": {"smiles": smiles, "count": 1}}
            if entry:
                sequences.append(entry)

    protenix_input = [{
        "name": "${name}",
        "modelSeeds": [${seeds}],
        "sequences": sequences,
    }]

    with open("protenix_input.json", "w") as f:
        json.dump(protenix_input, f, indent=2)

    print(f"[PrepProtenixComplex] Converted {len(bms.get('components', []))} components to Protenix format")
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

    output:
    path "predictions/**/*.cif", emit: structures, optional: true
    path "predictions/**/confidence.json", emit: confidence, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_weights = params.protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def use_template_flag = (params.protenix_use_template == true || params.protenix_use_template == 'true') ? '--use_template true' : ''
    def cache_flag = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null) ? '--enable_cache' : ''
    def fusion_flag = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null) ? '--enable_fusion' : ''

    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
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
