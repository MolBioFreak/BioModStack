def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

def normalizeGpuCsvValue(raw) {
    if (raw == null) {
        return ''
    }
    if (raw instanceof Collection) {
        return raw.collect { value -> value?.toString()?.trim() }.findAll { value -> value }.join(',')
    }
    def text = raw.toString().trim()
    if (text.startsWith('[') && text.endsWith(']') && text.length() >= 2) {
        text = text.substring(1, text.length() - 1)
    }
    return text
}

process BatchBoltzValidation {
    label 'Boltz'
    label 'gpu'
    container "${params.container_dir}/boltz2.sif"

    // Stage raw Boltz artifacts; alignment runs in the dedicated PyRosetta container below.
    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "*.log"

    input:
    path pdbs
    // List of PDB files
    path msa

    output:
    path "*_boltzpred.pdb", emit: raw_pdbs
    path "*_boltzpred.json", emit: raw_scores
    path "*_boltzpred.pae.npz", emit: raw_aligned_error
    path "original_designs/*.pdb", emit: original_designs
    path "boltz_batch.log"

    script:
    def resolvedBinderChains = params.antibody_chains ?: params.binder_chains ?: 'H,L'
    def resolvedTargetChains = params.antigen_chains ?: params.target_chains ?: 'T'
    def anchor_target = (params.boltz_anchor_target == true || params.boltz_anchor_target == 'true')
    def geometryMode = params.boltz_target_geometry_mode ?: (anchor_target ? 'conditioned' : 'flexible')
    def anchor_strict = (params.boltz_anchor_strict == true || params.boltz_anchor_strict == 'true')
    def anchor_target_rmsd = params.boltz_anchor_target_max_rmsd ?: 1.5
    def boltzStrictArgs = (anchor_target && anchor_strict) ? "--strict_target_rmsd ${anchor_target_rmsd}" : ""
    def boltzAnchorArgs = anchor_target ? "--anchor_target --target_chains \"${resolvedTargetChains}\" --template_manifest target_templates/manifest.json" : ""
    def boltzBatchCache = shellQuote(params.get('boltz_models', '') ?: '')
    def boltzBatchCacheFallback = shellQuote("${params.get('data_root', '') ?: params.get('code_root', '.')}/cache/boltz")
    def boltzHomeFallback = shellQuote("${params.get('data_root', '') ?: params.get('code_root', '.')}/cache/boltz/home")
    """
    set -euo pipefail
    shopt -s nullglob

    mkdir -p yamls predictions target_templates

    if [ "${anchor_target}" = "true" ]; then
        python3 ${params.code_root}/scripts/extract_target_templates.py \\
            --pdb_files ${pdbs} \\
            --target_chains "${resolvedTargetChains}" \\
            --out_dir target_templates/mmcif \\
            --manifest target_templates/manifest.json
    fi
    
    # Generate YAML config for each PDB sequence
    python3 ${params.code_root}/scripts/prep_boltz_batch.py \\
        --pdb_files ${pdbs} \\
        --msa_path ${msa} \\
        --out_dir yamls \\
        --binder_chains "${resolvedBinderChains}" \\
        --template_threshold ${params.target_template_threshold_angstrom ?: 2.0} \\
        ${params.epitope_residues ? '--epitope_residues "' + params.epitope_residues + '"' : ''} \\
        ${boltzAnchorArgs}
        
    # Run Boltz on the directory of YAMLs (Batch Mode)
    # This loads the model ONCE and processes all sequences.
    # Keep heavyweight Boltz checkpoints in the shared BMS model/cache path;
    # never let Boltz repopulate HOME/.boltz inside each Nextflow task work dir.
    # The shared model cache may be read-only in Apptainer; keep only HOME writable.
    BOLTZ_SHARED_CACHE=${boltzBatchCache}
    if [ -z "\$BOLTZ_SHARED_CACHE" ]; then
        BOLTZ_SHARED_CACHE=${boltzBatchCacheFallback}
    fi
    # The managed container binds the host Boltz model directory at /boltzcache.
    if [ -d /boltzcache ]; then
        BOLTZ_SHARED_CACHE=/boltzcache
    fi
    export BOLTZ_CACHE_DIR="\$BOLTZ_SHARED_CACHE"
    export BOLTZ_CACHE="\$BOLTZ_SHARED_CACHE"
    export NUMBA_CACHE_DIR="\$(pwd)/.numba_cache"
    export XDG_CACHE_HOME="\$(pwd)/.cache_home"
    export HOME="\$BOLTZ_SHARED_CACHE/home"
    mkdir -p "\$BOLTZ_CACHE_DIR" "\$NUMBA_CACHE_DIR" "\$XDG_CACHE_HOME"
    if ! mkdir -p "\$HOME" 2>/dev/null; then
        BOLTZ_HOME_FALLBACK=${boltzHomeFallback}
        mkdir -p "\$BOLTZ_HOME_FALLBACK"
        export HOME="\$BOLTZ_HOME_FALLBACK"
    fi
    
    python3 ${params.code_root}/scripts/run_boltz_wrapper.py predict yamls/ \\
        --cache "\$BOLTZ_CACHE_DIR" \\
        --output_format pdb \\
        --write_full_pae \\
        --diffusion_samples ${params.boltz_diffusion_samples != null ? params.boltz_diffusion_samples : (params.boltz_num_samples != null ? params.boltz_num_samples : 1)} \\
        --recycling_steps ${params.boltz_recycling_steps != null ? params.boltz_recycling_steps : 3} \\
        --sampling_steps ${params.boltz_sampling_steps != null ? params.boltz_sampling_steps : 50} \\
        ${params.boltz_seed != null ? '--seed ' + params.boltz_seed : ''} \\
        ${params.boltz_max_parallel_samples != null ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        ${params.boltz_step_scale != null ? '--step_scale ' + params.boltz_step_scale : ''} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_method ? '--method ' + params.boltz_method : ''} \\
        --out_dir . \\
        ${params.boltz_extra_config ?: ''} \\
        2>&1 | tee boltz_batch.log

    python3 ${params.code_root}/scripts/publish_boltz_samples.py \\
        --native-root boltz_results_yamls/predictions --inputs yamls --output .

    boltz_pdb_count=\$(find . -maxdepth 1 -name '*_boltzpred.pdb' | wc -l)
    boltz_npz_count=\$(find . -maxdepth 1 -name '*_boltzpred.pae.npz' | wc -l)
    if [ "\$boltz_pdb_count" -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: No Boltz PDB predictions were copied for RMSD alignment" >&2
        exit 1
    fi
    if [ "\$boltz_npz_count" -eq 0 ] || [ "\$boltz_npz_count" -lt "\$boltz_pdb_count" ]; then
        echo "[BatchBoltzValidation] ERROR: Missing Boltz PAE NPZ sidecars" >&2
        exit 1
    fi

    # We need access to the original un-repacked templates to calculate RMSD
    # The originals are in 'pdbs' (the input chunk).
    mkdir -p original_designs
    cp ${pdbs} original_designs/
    original_design_count=\$(find original_designs -maxdepth 1 -name '*.pdb' | wc -l)
    if [ "\$original_design_count" -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: No original design PDBs were staged for RMSD alignment" >&2
        exit 1
    fi
    """
}

process AlignBoltzValidation {
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.json"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.npz"
    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "alignment_batch.log"

    input:
    path raw_pdbs
    path raw_scores
    path raw_aligned_error
    path original_designs

    output:
    path "predictions/*.pdb", emit: pdbs
    path "predictions/*.json", emit: scores
    path "predictions/*.npz", emit: aligned_error
    path "alignment_batch.log"

    script:
    def revision = params.get('core_protein_scientific_contract')
    if (revision != null && (revision instanceof Boolean || revision.toString() != '1')) {
        throw new IllegalArgumentException('core_protein_scientific_contract must be exactly 1')
    }
    def scientificArgs = revision == null ? '' : '--core_protein_scientific_contract 1'
    def quoteRole = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    def resolvedBinderChains = params.antibody_chains ?: params.binder_chains ?: 'H,L'
    def resolvedTargetChains = params.antigen_chains ?: params.target_chains ?: 'T'
    def anchor_target = (params.boltz_anchor_target == true || params.boltz_anchor_target == 'true')
    def geometryMode = params.boltz_target_geometry_mode ?: (anchor_target ? 'conditioned' : 'flexible')
    def anchor_strict = (params.boltz_anchor_strict == true || params.boltz_anchor_strict == 'true')
    def anchor_target_rmsd = params.boltz_anchor_target_max_rmsd ?: 1.5
    def boltzStrictArgs = (anchor_target && anchor_strict) ? "--strict_target_rmsd ${anchor_target_rmsd}" : ""
    """
    set -euo pipefail

    mkdir -p original_designs predictions
    cp ${original_designs} original_designs/

    python3 ${params.code_root}/scripts/align_boltz.py \\
        --design_dir ./original_designs \\
        --boltz_dir ./ \\
        --output_dir predictions \\
        --design_type binder \\
        --binder_chains ${quoteRole(revision == null ? resolvedBinderChains : (params.antibody_chains ?: params.binder_chains ?: ''))} \\
        --target_chains ${quoteRole(revision == null ? resolvedTargetChains : (params.antigen_chains ?: params.target_chains ?: ''))} ${scientificArgs} \\
        --geometry_mode "${geometryMode}" ${boltzStrictArgs} \\
        --ncpus ${task.cpus} \\
        2>&1 | tee alignment_batch.log

    aligned_pdb_count=\$(find predictions -maxdepth 1 -name '*.pdb' | wc -l)
    aligned_json_count=\$(find predictions -maxdepth 1 -name '*.json' | wc -l)
    aligned_pae_count=\$(find predictions -maxdepth 1 -name '*.npz' | wc -l)
    echo "[AlignBoltzValidation] Alignment outputs: pdb=\$aligned_pdb_count json=\$aligned_json_count pae=\$aligned_pae_count"
    if [ "\$aligned_pdb_count" -eq 0 ] || [ "\$aligned_json_count" -eq 0 ] || [ "\$aligned_pae_count" -eq 0 ]; then
        echo "[AlignBoltzValidation] ERROR: alignment produced incomplete PDB/JSON/PAE outputs" >&2
        exit 1
    fi
    """
}

process BatchProtenixValidation {
    label 'Protenix'
    label 'gpu'
    container { params.protenix_container_path ?: "${params.container_dir}/protenix.sif" }

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.cif"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.json"
    publishDir "${params.out_dir}/pdb_files/aligned_error", mode: 'copy', pattern: "predictions/aligned_error/*.json"
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"

    input:
    path pdbs
    path msa

    output:
    path "predictions/*.pdb", emit: pdbs
    path "predictions/*.json", emit: scores
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/aligned_error/*.json", emit: aligned_error, optional: true
    path "protenix_batch.log"

    script:
    def revision = params.get('core_protein_scientific_contract')
    if (revision != null && (revision instanceof Boolean || revision.toString() != '1')) {
        throw new IllegalArgumentException('core_protein_scientific_contract must be exactly 1')
    }
    def scientificArgs = revision == null ? '' : '--core_protein_scientific_contract 1'
    def model_name = params.protenix_model_weights ?: 'protenix-v2'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def requested_template = (params.protenix_use_template == true || params.protenix_use_template == 'true')
    def anchor_target = (params.protenix_anchor_target == true || params.protenix_anchor_target == 'true')
    def geometryMode = params.protenix_target_geometry_mode ?: (anchor_target ? 'conditioned' : 'flexible')
    def anchor_strict = (params.protenix_anchor_strict == true || params.protenix_anchor_strict == 'true')
    def anchor_target_rmsd = params.protenix_anchor_target_max_rmsd ?: 1.5
    def protenixStrictArgs = (anchor_target && anchor_strict) ? "--strict_target_rmsd ${anchor_target_rmsd}" : ""
    def use_template = requested_template || anchor_target
    def enable_cache = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null)
    def enable_fusion = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null)
    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
    def msa_backend = params.protenix_msa_backend ?: 'auto'
    def msa_allow_cpu_fallback = (params.protenix_allow_cpu_msa_fallback == true || params.protenix_allow_cpu_msa_fallback == 'true')
    def local_msa_timeout_seconds = params.protenix_local_msa_timeout_seconds ?: 900
    def externalTargetAsTarget = (
        params.protenix_external_target_as_target == true ||
        params.protenix_external_target_as_target == 'true' ||
        (params.protenix_external_target_as_target == null && params.target_pdb)
    )
    def explicitBinderSourceChains = params.protenix_binder_source_chains ?: ''
    def defaultBinderChains = params.antibody_chains ?: params.binder_chains ?: ''
    def fallbackAlignmentBinderChains = externalTargetAsTarget ? (defaultBinderChains ?: explicitBinderSourceChains ?: 'A') : (defaultBinderChains ?: 'H,L')
    def fallbackAlignmentTargetChains = params.antigen_chains ?: params.target_chains ?: 'T'
    def msa_preferred_gpu_csv = normalizeGpuCsvValue(params.msa_preferred_gpus)
    def msa_excluded_gpu_csv = normalizeGpuCsvValue(params.msa_excluded_gpus)
    def msa_cpu_only_flag = (params.msa_use_gpu == false || params.msa_use_gpu == 'false') ? '--cpu-only' : ''
    def msa_allow_cpu_fallback_flag = msa_allow_cpu_fallback ? '--allow-cpu-fallback' : ''
    def msa_cache_only_flag = (params.msa_cache_only == true || params.msa_cache_only == 'true') ? '--cache-only' : ''
    if (model_name != 'protenix-v2') {
        throw new IllegalArgumentException("Protenix is pinned to V2 weights; received ${model_name}")
    }
    def effective_model = 'protenix-v2'

    """
    #!/bin/bash
    set -euo pipefail
    shopt -s nullglob

    mkdir -p validation_designs raw_predictions predictions

    SHARED_PROTENIX_ROOT="/protenix_weights"
    if [ "${requested_template}" = "true" ] && [ "${anchor_target}" = "true" ]; then
        export BMS_PROTENIX_GENERIC_MMCIF_DIR="\$SHARED_PROTENIX_ROOT/mmcif"
    fi
    if [ "${anchor_target}" = "true" ]; then
        export PROTENIX_ROOT_DIR="\$PWD/.protenix_anchor_root"
        mkdir -p "\$PROTENIX_ROOT_DIR"
        if [ -d "\$SHARED_PROTENIX_ROOT/checkpoint" ] && [ ! -e "\$PROTENIX_ROOT_DIR/checkpoint" ]; then
            ln -s "\$SHARED_PROTENIX_ROOT/checkpoint" "\$PROTENIX_ROOT_DIR/checkpoint"
        fi
        for shared_data in common search_database; do
            if [ -d "\$SHARED_PROTENIX_ROOT/\$shared_data" ] && [ ! -e "\$PROTENIX_ROOT_DIR/\$shared_data" ]; then
                ln -s "\$SHARED_PROTENIX_ROOT/\$shared_data" "\$PROTENIX_ROOT_DIR/\$shared_data"
            fi
        done
    else
        export PROTENIX_ROOT_DIR="\$SHARED_PROTENIX_ROOT"
    fi
    # Installed weights/common data are immutable; generated caches are task-owned.
    export XDG_CACHE_HOME="\$PWD/.protenix_cache"
    export TRITON_CACHE_DIR="\$XDG_CACHE_HOME/triton"
    export MPLCONFIGDIR="\$XDG_CACHE_HOME/matplotlib"
    export PYTHONNOUSERSITE=1
    export PIP_NO_USER=1
    mkdir -p "\$XDG_CACHE_HOME" "\$TRITON_CACHE_DIR" "\$MPLCONFIGDIR"

    if ! command -v python3 &> /dev/null; then
        echo "[BatchProtenixValidation] ERROR: python3 not found in container image" >&2
        exit 127
    fi

    python3 ${params.code_root}/scripts/prep_protenix_batch.py \\
        --pdb_files ${pdbs} \\
        --out_json input.json \\
        --chain_roles_json chain_roles.json \\
        --out_pdb_dir validation_designs \\
        --seeds "${seeds}" \\
        --target_pdb "${params.target_pdb}" \\
        --target_chains "${params.antigen_chains ?: params.target_chains ?: ''}" \\
        ${params.epitope_residues ? '--epitope_residues "' + params.epitope_residues + '"' : ''} \\
        --auto_pocket_if_missing \\
        --auto_pocket_max_residues ${params.protenix_auto_pocket_residue_count ?: 24} \\
        --pocket_max_distance ${params.protenix_pocket_max_distance ?: 8.0} \\
        ${externalTargetAsTarget ? '--external-target-as-target' : ''} \\
        ${explicitBinderSourceChains ? '--binder_source_chains "' + explicitBinderSourceChains + '"' : ''} \\
        ${defaultBinderChains ? '--default_binder_chains "' + defaultBinderChains + '"' : ''} \\
        ${params.target_model_number ? '--target_model_number ' + params.target_model_number : ''}

    if [ ! -s chain_roles.json ]; then
        echo "[BatchProtenixValidation] ERROR: prep_protenix_batch.py did not emit chain role metadata" >&2
        exit 1
    fi

    PROTENIX_BINDER_CHAINS="\$(python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('chain_roles.json').read_text())
print(",".join(payload.get('all_binder_chain_ids') or []))
PY
)"
    PROTENIX_TARGET_CHAINS="\$(python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('chain_roles.json').read_text())
print(",".join(payload.get('all_target_chain_ids') or []))
PY
)"
    if [ "${revision == null}" = "true" ] && [ -z "\$PROTENIX_BINDER_CHAINS" ]; then
        PROTENIX_BINDER_CHAINS="${fallbackAlignmentBinderChains}"
    fi
    if [ "${revision == null}" = "true" ] && [ -z "\$PROTENIX_TARGET_CHAINS" ]; then
        PROTENIX_TARGET_CHAINS="${fallbackAlignmentTargetChains}"
    fi

    if [ "${anchor_target}" = "true" ]; then
        if [ -z "\$PROTENIX_TARGET_CHAINS" ]; then
            echo "[BatchProtenixValidation] ERROR: no resolved target chains available for anchored template extraction" >&2
            exit 1
        fi
        python3 ${params.code_root}/scripts/extract_target_templates.py \\
            --pdb_files validation_designs/*.pdb \\
            --target_chains "\$PROTENIX_TARGET_CHAINS" \\
            --out_dir "\$PROTENIX_ROOT_DIR/mmcif" \\
            --native-template-ids \\
            --manifest target_template_manifest.json
        echo "[BatchProtenixValidation] Antibody validator mode: target-anchored co-fold. A task-local Protenix template DB was staged from the experimental target chains." | tee -a protenix_batch.log
    else
        if [ "${externalTargetAsTarget}" = "true" ]; then
            echo "[BatchProtenixValidation] Antibody validator mode: flexible co-fold using binder chains from source PDBs plus experimental target chains from ${params.target_pdb}." | tee -a protenix_batch.log
        else
            echo "[BatchProtenixValidation] Antibody validator mode: sequence-only complex co-fold with original chain IDs preserved in input.json." | tee -a protenix_batch.log
        fi
    fi
    if [ "${requested_template}" = "true" ] && [ "${anchor_target}" != "true" ]; then
        echo "[BatchProtenixValidation] Generic template DB conditioning enabled for this run; no explicit target anchoring is applied." | tee -a protenix_batch.log
    fi

    PROTENIX_INPUT_JSON="input.json"
    if [ "${use_msa}" = "true" ]; then
        PROTENIX_MSA_CACHE_DIR="${params.msa_cache_dir}"
        if [ -z "\$PROTENIX_MSA_CACHE_DIR" ] || ! mkdir -p "\$PROTENIX_MSA_CACHE_DIR/.locks" 2>/dev/null; then
            PROTENIX_MSA_CACHE_DIR="\$PWD/msa_prepared/cache"
            mkdir -p "\$PROTENIX_MSA_CACHE_DIR"
            echo "[BatchProtenixValidation] Shared MSA cache unavailable; using task-local cache at \$PROTENIX_MSA_CACHE_DIR"
        else
            echo "[BatchProtenixValidation] Using shared MSA cache at \$PROTENIX_MSA_CACHE_DIR"
        fi
        python3 ${params.code_root}/scripts/prepare_protenix_msa.py \\
            --generated-service protenix:generated_msa \\
            --input_json input.json \\
            --output_json prepared_input.json \\
            --out_dir msa_prepared \\
            --backend "${msa_backend}" \\
            --binder-chain-ids "\$PROTENIX_BINDER_CHAINS" \\
            --binder-max-unpaired-msa-rows ${params.protenix_binder_max_unpaired_msa_rows ?: 256} \\
            --binder-min-residue-coverage ${params.protenix_binder_min_residue_coverage ?: 0.5} \\
            --colabfold-api-host "${params.colabfold_api_host ?: 'https://api.colabfold.com'}" \\
            --db-path "${params.msa_local_db}" \\
            --cache-dir "\$PROTENIX_MSA_CACHE_DIR" \\
            --threads ${params.msa_threads ?: task.cpus} \\
            --preset "${params.msa_preset ?: 'fast'}" \\
            ${msa_cpu_only_flag} \\
            --gpu-mode "${params.msa_gpu_mode ?: 'auto'}" \\
            --gpu-threshold ${params.msa_gpu_threshold ?: 80} \\
            ${msa_preferred_gpu_csv ? '--preferred-gpus "' + msa_preferred_gpu_csv + '"' : ''} \\
            ${msa_excluded_gpu_csv ? '--excluded-gpus "' + msa_excluded_gpu_csv + '"' : ''} \\
            --gpu-server-mode "${params.msa_gpu_server_mode ?: 'persistent'}" \\
            --gpu-server-wait-timeout ${params.msa_gpu_server_wait_timeout ?: 120} \\
            --gpu-server-db-load-mode ${params.msa_gpu_server_db_load_mode ?: 2} \\
            --gpu-server-startup-wait ${params.msa_gpu_server_startup_wait ?: 5.0} \\
            --local-msa-timeout-seconds ${local_msa_timeout_seconds} \\
            ${msa_cache_only_flag} \\
            ${msa_allow_cpu_fallback_flag} \\
            2>&1 | tee protenix_msa_prep.log
        PROTENIX_INPUT_JSON="prepared_input.json"
    fi

    ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS=""
    if [ "${anchor_target}" = "true" ]; then
        python3 ${params.code_root}/scripts/prepare_protenix_exact_templates.py \\
            --input_json "\$PROTENIX_INPUT_JSON" \\
            --output_json prepared_input_exact_templates.json \\
            --manifest target_template_manifest.json \\
            ${requested_template ? '--generic-templates' : ''} \\
            --target_chains "\$PROTENIX_TARGET_CHAINS" \\
            --out_dir exact_target_templates
        PROTENIX_INPUT_JSON="prepared_input_exact_templates.json"
        ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS="\$(python3 -c 'import json; print(",".join(dict.fromkeys(r["template_id"] for r in json.load(open("target_template_manifest.json")).values())))')"
    fi

    python3 ${params.code_root}/scripts/run_protenix_inference.py \\
        --input "\$PROTENIX_INPUT_JSON" \\
        --out_dir raw_predictions/ \\
        --allow_exact_duplicate_template_pdb_ids "\$ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS" \\
        --model_name ${effective_model} \\
        --seeds "${seeds}" \\
        --sample ${n_sample} \\
        --step ${n_step} \\
        --cycle ${n_cycle} \\
        --use_msa ${use_msa} \\
        --use_template ${use_template} \\
        --enable_cache ${enable_cache} \\
        --enable_fusion ${enable_fusion} \\
        2>&1 | tee protenix_batch.log

    raw_cif_count=\$(find raw_predictions -type f -name '*.cif' | wc -l)
    raw_summary_json_count=\$(find raw_predictions -type f -name '*_summary_confidence_sample_*.json' | wc -l)
    raw_full_json_count=\$(find raw_predictions -type f -name '*full_data*.json' | wc -l)
    if [ "\$raw_cif_count" -eq 0 ] || [ "\$raw_summary_json_count" -eq 0 ] || [ "\$raw_full_json_count" -eq 0 ]; then
        echo "[BatchProtenixValidation] ERROR: Protenix produced no CIF/summary/full-data outputs; strict ipSAE requires raw aligned-error artifacts" >&2
        exit 1
    fi
    python3 ${params.code_root}/scripts/align_protenix.py \\
        --design_dir ./validation_designs \\
        --protenix_dir ./raw_predictions \\
        --output_dir predictions \\
        --design_type binder \\
        --binder_chains "\$PROTENIX_BINDER_CHAINS" \\
        --target_chains "\$PROTENIX_TARGET_CHAINS" \\
        --chain_roles_json chain_roles.json ${scientificArgs} \\
        --geometry_mode "${geometryMode}" ${protenixStrictArgs} \\
        --ncpus ${task.cpus} \\
        2>&1 | tee alignment_protenix.log

    aligned_pdb_count=\$(find predictions -maxdepth 1 -name '*.pdb' | wc -l)
    aligned_json_count=\$(find predictions -maxdepth 1 -name '*.json' | wc -l)
    if [ "\$aligned_pdb_count" -eq 0 ] || [ "\$aligned_json_count" -eq 0 ]; then
        echo "[BatchProtenixValidation] ERROR: alignment produced no usable PDB/JSON outputs" >&2
        exit 1
    fi
    """
}

process BatchESMFold2Validation {
    label 'ESMFold2'
    container "${params.container_dir}/esmfold2.sif"
    publishDir "${params.out_dir}/structure_validation/esmfold2", mode: 'copy', overwrite: true

    input:
    path pdbs
    path msa_files

    output:
    path "predictions/*_esmfold2.pdb", emit: pdbs
    path "predictions/*_esmfold2.cif", emit: cifs
    path "predictions/*_esmfold2.metrics.json", emit: metrics
    path "predictions/*.confidence.npz", optional: true, emit: native_confidence
    path "raw_predictions/*/effective_settings.json", emit: effective_settings
    path "predictions/esmfold2_validation_summary.json", emit: summary
    path "esmfold2_batch.log", emit: log

    script:
    def codeRoot = params.code_root ?: workflow.projectDir
    def pdbList = pdbs instanceof Collection ? pdbs : [pdbs]
    def msaList = msa_files instanceof Collection ? msa_files : [msa_files]
    def payload = groovy.json.JsonOutput.toJson([pdbs: pdbList.collect { it.toString() }, msa_files: msaList.collect { it.toString() }, settings: params.findAll { k, v -> k.toString().startsWith('esmf_') || k.toString().startsWith('esmfold2_') || ['model_variant', 'model_id_or_path', 'local_files_only', 'num_loops', 'num_sampling_steps', 'num_diffusion_samples', 'seed', 'device', 'msa_path', 'msa_format', 'msa_max_sequences', 'msa_remove_insertions', 'complex_components', 'complex_components_json', 'complex_components_file', 'covalent_bonds', 'distogram_conditioning', 'pdb_chain_ids', 'pdb_include_dna_rna', 'dna_sequence', 'dna_chain_id', 'rna_sequence', 'rna_chain_id', 'ligand_smiles', 'ligand_ccd', 'ligand_chain_id'].contains(k) }]).bytes.encodeBase64().toString()
    """
    set -euo pipefail
    mkdir -p validation_designs raw_predictions predictions
    cp ${pdbs} validation_designs/
    : > esmfold2_batch.log

    python3 - <<'ESM_BATCH_INPUTS'
import base64
from pathlib import Path
Path('esmfold2_batch_inputs.json').write_bytes(base64.b64decode('${payload}'))
ESM_BATCH_INPUTS
    python3 ${codeRoot}/scripts/run_esmfold2_batch.py --validation-inputs esmfold2_batch_inputs.json --raw-root raw_predictions 2>&1 | tee -a esmfold2_batch.log

    python3 ${codeRoot}/scripts/normalize_esmfold2_validation.py \\
        --design-dir validation_designs \\
        --raw-root raw_predictions \\
        --output-dir predictions \\
        2>&1 | tee -a esmfold2_batch.log

    test -n "\$(find predictions -maxdepth 1 -name '*_esmfold2.pdb' -print -quit)" || {
        echo "[BatchESMFold2Validation] ERROR: no normalized PDB predictions produced" >&2
        exit 1
    }
    """
}

process BatchImmunogenicity {
    label 'Antiberty'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    path pdbs

    output:
    path "immunogenicity_scores.csv", emit: scores

    script:
    """
    # Run AntiBERTy on all PDBs at once
    python3 ${params.code_root}/scripts/batch_antiberty.py \\
        --pdb_files ${pdbs} \\
        ${((params.antibody_chains ?: params.binder_chains) ? '--chain_ids "' + (params.antibody_chains ?: params.binder_chains) + '"' : '')} \\
        --out_csv immunogenicity_scores.csv
    """
}

process BatchStability {
    label 'ThermoMPNN'
    container "${params.container_dir}/stability_tools.sif"

    input:
    path pdbs

    output:
    path "stability_scores.csv", emit: scores

    script:
    """
    # ThermoMPNN custom_inference.py expects ../local.yaml relative to the analysis/ folder
    # Copy config to make relative path work
    mkdir -p analysis_run
    cp /opt/ThermoMPNN/local.yaml ./local.yaml
    
    # Loop over PDBs (ThermoMPNN is fast, loop is fine)
    echo "file,score" > stability_scores.csv
    
    for pdb in ${pdbs}; do
        cd analysis_run
        python3 /opt/ThermoMPNN/analysis/custom_inference.py \\
            --pdb "../\$pdb" \\
            --model_path /opt/ThermoMPNN/models/thermoMPNN_default.pt \\
            --out_dir ../ \\
            >> ../stability_scores.csv 2>&1 || echo "\$pdb,error" >> ../stability_scores.csv
        cd ..
    done
    """
}
