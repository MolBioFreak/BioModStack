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

// ─────────────────────────────────────────────────────────────────────────────
// PROTENIX PREDICT — Single-chain protein structure prediction
// ─────────────────────────────────────────────────────────────────────────────

process ProtenixPredict {
    label 'Protenix'
    label 'gpu'
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa_prepared/msa_report.json", saveAs: { ignoredFilename -> "protenix_msa_report.json" }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*confidence*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*full_data*.json", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(sequence), val(sequence_name)

    output:
    path "predictions/**/*.cif", emit: cifs, optional: true
    path "predictions/**/*confidence*.json", emit: confidence, optional: true
    path "predictions/**/*full_data*.json", emit: full_confidence, optional: true
    path "msa_prepared/msa_report.json", emit: msa_report, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_name = params.protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def use_template = (params.protenix_use_template == true || params.protenix_use_template == 'true')
    def enable_cache = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null)
    def enable_fusion = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null)
    def msa_backend = params.protenix_msa_backend ?: 'auto'
    def msa_allow_cpu_fallback = (params.protenix_allow_cpu_msa_fallback == true || params.protenix_allow_cpu_msa_fallback == 'true')
    def msa_preferred_gpu_csv = normalizeGpuCsvValue(params.msa_preferred_gpus)
    def msa_excluded_gpu_csv = normalizeGpuCsvValue(params.msa_excluded_gpus)
    def msa_cpu_only_flag = (params.msa_use_gpu == false || params.msa_use_gpu == 'false') ? '--cpu-only' : ''
    def msa_cache_only_flag = (params.msa_cache_only == true || params.msa_cache_only == 'true') ? '--cache-only' : ''
    def msa_allow_cpu_fallback_flag = msa_allow_cpu_fallback ? '--allow-cpu-fallback' : ''

    // Auto-switch to ESM model if MSA is disabled
    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
    def model_aliases = [
        'protenix_esm_20241211_v0.2.1': 'protenix_mini_esm_v0.5.0',
        'protenix_base_20241211_v0.2.1': 'protenix_base_default_v1.0.0'
    ]
    def effective_model = model_aliases.get(model_name, model_name)
    if (!use_msa && !(effective_model.contains('esm') || effective_model.contains('ism') || effective_model.contains('protenix-v2'))) {
        effective_model = 'protenix_mini_esm_v0.5.0'
    }

    """
    #!/bin/bash
    set -euo pipefail

    # Persist Protenix caches/checkpoints in the shared model-weights store.
    # nextflow.config binds params.protenix_weights into this container at /protenix_weights.
    export PROTENIX_ROOT_DIR="/protenix_weights"
    export XDG_CACHE_HOME="\$PROTENIX_ROOT_DIR/common"
    export TRITON_CACHE_DIR="\$PROTENIX_ROOT_DIR/triton"
    export MPLCONFIGDIR="\$PROTENIX_ROOT_DIR/matplotlib"
    export PYTHONNOUSERSITE=1
    export PIP_NO_USER=1
    export PATH="/root/miniconda3/bin:\$PATH"
    mkdir -p "\$PROTENIX_ROOT_DIR/common" "\$PROTENIX_ROOT_DIR/checkpoint" "\$PROTENIX_ROOT_DIR/triton" "\$PROTENIX_ROOT_DIR/matplotlib"

    # Validate the container has Python available for the repo-local wrapper.
    if ! command -v python3 &> /dev/null; then
        echo "[PROTENIX] ERROR: python3 not found in container image"
        exit 127
    fi

    if [ "${use_template}" = "true" ]; then
        template_dir="\$PROTENIX_ROOT_DIR/mmcif"
        template_file=""
        if [ -d "\$template_dir" ]; then
            template_file="\$(find "\$template_dir" -type f \\( -name '*.cif' -o -name '*.mmcif' -o -name '*.cif.gz' -o -name '*.mmcif.gz' \\) | head -n 1 || true)"
        fi
        if [ -z "\$template_file" ]; then
            echo "[PROTENIX] ERROR: Template mode requested but no mmCIF template database was found at \$template_dir" | tee -a protenix_predict.log
            echo "[PROTENIX] Set protenix_use_template=false or populate \$template_dir with template CIF files." | tee -a protenix_predict.log
            exit 89
        fi
        echo "[PROTENIX] Template database detected: \$template_file"
    fi

    # Fail fast if GPU architecture is unsupported by the container's torch build.
    python3 - << 'PY'
import re
import sys

try:
    import torch
except Exception as exc:
    print(f"[PROTENIX] ERROR: Could not import torch: {exc}")
    raise SystemExit(87)

if not torch.cuda.is_available():
    print("[PROTENIX] WARNING: torch.cuda.is_available() is false; continuing")
    raise SystemExit(0)

major, minor = torch.cuda.get_device_capability(0)
device_arch = f"sm_{major}{minor}"
supported = set()
for arch in torch.cuda.get_arch_list():
    match = re.search(r"sm_(\\d+)", arch)
    if match:
        supported.add(f"sm_{match.group(1)}")

if supported and device_arch not in supported:
    print(f"[PROTENIX] ERROR: GPU architecture {device_arch} is unsupported by this torch build: {sorted(supported)}")
    raise SystemExit(88)

print(f"[PROTENIX] torch={torch.__version__} cuda={torch.version.cuda} device_arch={device_arch} supported={sorted(supported)}")
PY

    echo "[PROTENIX] Requested model: ${model_name} | Effective model: ${effective_model}"
    echo "[PROTENIX] Seeds: ${seeds} | Samples: ${n_sample} | Steps: ${n_step} | Cycles: ${n_cycle}"
    echo "[PROTENIX] MSA: ${use_msa} | Template: ${use_template} | Cache: ${enable_cache} | Fusion: ${enable_fusion}"

    # ═══════════════════════════════════════════════════════════════════════
    # Generate Protenix input JSON
    # Uses proteinChain entity format.
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

    PROTENIX_INPUT_JSON="input.json"
    if [ "${use_template}" = "true" ]; then
        python3 - <<'PY'
import json
import os
from pathlib import Path

input_path = Path("input.json")
output_path = Path("protenix_input_with_templates.json")
template_dir = str((Path(os.environ["PROTENIX_ROOT_DIR"]) / "mmcif").resolve())

payload = json.loads(input_path.read_text(encoding="utf-8"))
if not isinstance(payload, list):
    raise SystemExit("Expected top-level list in Protenix input JSON")

for entry in payload:
    if isinstance(entry, dict):
        entry["templatesPath"] = template_dir

output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
        PROTENIX_INPUT_JSON="protenix_input_with_templates.json"
        echo "[PROTENIX] Injected templatesPath=\$PROTENIX_ROOT_DIR/mmcif into \$PROTENIX_INPUT_JSON"
    fi
    if [ "${use_msa}" = "true" ]; then
        PROTENIX_MSA_CACHE_DIR="${params.msa_cache_dir}"
        if [ -z "\$PROTENIX_MSA_CACHE_DIR" ] || ! mkdir -p "\$PROTENIX_MSA_CACHE_DIR/.locks" 2>/dev/null; then
            PROTENIX_MSA_CACHE_DIR="\$PWD/msa_prepared/cache"
            mkdir -p "\$PROTENIX_MSA_CACHE_DIR"
            echo "[PROTENIX] Shared MSA cache unavailable; using task-local cache at \$PROTENIX_MSA_CACHE_DIR"
        else
            echo "[PROTENIX] Using shared MSA cache at \$PROTENIX_MSA_CACHE_DIR"
        fi
        python3 ${params.code_root}/scripts/prepare_protenix_msa.py \\
            --input_json "\$PROTENIX_INPUT_JSON" \\
            --output_json prepared_input.json \\
            --out_dir msa_prepared \\
            --report_json msa_prepared/msa_report.json \\
            --backend "${msa_backend}" \\
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
            ${msa_cache_only_flag} \\
            ${msa_allow_cpu_fallback_flag} \\
            2>&1 | tee protenix_msa_prep.log
        PROTENIX_INPUT_JSON="prepared_input.json"
    fi

    # ═══════════════════════════════════════════════════════════════════════
    # Run structure prediction
    # ═══════════════════════════════════════════════════════════════════════
    echo "[PROTENIX] Running structure prediction..."
    python3 ${params.code_root}/scripts/run_protenix_inference.py \\
        --input "\$PROTENIX_INPUT_JSON" \\
        --out_dir predictions/ \\
        --model_name ${effective_model} \\
        --seeds "${seeds}" \\
        --sample ${n_sample} \\
        --step ${n_step} \\
        --cycle ${n_cycle} \\
        --use_msa ${use_msa} \\
        --use_template ${use_template} \\
        --enable_cache ${enable_cache} \\
        --enable_fusion ${enable_fusion} \\
        2>&1 | tee protenix_predict.log

    first_cif="\$(find predictions/ -type f -name '*.cif' | head -n 1 || true)"
    if [ -z "\$first_cif" ]; then
        echo "[PROTENIX] ERROR: Protenix returned without producing any CIF output" | tee -a protenix_predict.log
        if [ -d predictions/ERR ]; then
            echo "[PROTENIX] Error reports under predictions/ERR:" | tee -a protenix_predict.log
            find predictions/ERR -type f | head -20 | tee -a protenix_predict.log || true
            first_err="\$(find predictions/ERR -type f | head -n 1 || true)"
            if [ -n "\$first_err" ]; then
                echo "[PROTENIX] First error report (\$first_err):" | tee -a protenix_predict.log
                sed -n '1,80p' "\$first_err" | tee -a protenix_predict.log || true
            fi
        fi
        exit 86
    fi

    first_full_data="\$(find predictions/ -type f -name '*full_data*.json' | head -n 1 || true)"
    if [ -z "\$first_full_data" ]; then
        echo "[PROTENIX] ERROR: Protenix returned without producing any full-data confidence JSON output" | tee -a protenix_predict.log
        exit 85
    fi

    echo "[PROTENIX] Prediction complete. Listing outputs:"
    find predictions/ -type f \\( -name "*.cif" -o -name "*confidence*.json" \\) | head -20 || true
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
    from pathlib import Path

    input_path = Path("${complex_json}")

    type_map = {
        'protein': 'proteinChain',
        'peptide': 'proteinChain',
        'dna': 'dnaSequence',
        'rna': 'rnaSequence',
    }

    def convert_entry(bms, default_name):
        sequences = []
        for comp in bms.get('components', []):
            t = comp.get('type', 'protein').lower()
            seq = comp.get('sequence', '')
            comp_id = str(comp.get('id') or '').strip()
            count_raw = comp.get('count', 1)
            try:
                count = max(1, int(count_raw))
            except Exception:
                count = 1

            if t in type_map:
                if seq:
                    chain_entry = {"sequence": seq, "count": count}
                    if comp_id:
                        chain_entry["id"] = [comp_id]
                    sequences.append({type_map[t]: chain_entry})
            elif t == 'ligand':
                ccd = comp.get('ccd', '')
                smiles = comp.get('smiles', '')
                entry = {}
                if ccd:
                    ligand_id = str(ccd)
                    if not ligand_id.startswith("CCD_"):
                        ligand_id = f"CCD_{ligand_id}"
                    ligand_entry = {"ligand": ligand_id, "count": count}
                    if comp_id:
                        ligand_entry["id"] = [comp_id]
                    entry = {"ligand": ligand_entry}
                elif smiles:
                    ligand_entry = {"ligand": str(smiles), "count": count}
                    if comp_id:
                        ligand_entry["id"] = [comp_id]
                    entry = {"ligand": ligand_entry}
                if entry:
                    sequences.append(entry)
            elif t == 'ion':
                entry = {}
                ion = comp.get('ion') or comp.get('element') or comp.get('ccd')
                if ion:
                    ion_entry = {"ion": str(ion).upper(), "count": count}
                    if comp_id:
                        ion_entry["id"] = [comp_id]
                    entry = {"ion": ion_entry}
                if entry:
                    sequences.append(entry)

        protenix_entry = {
            "name": str(bms.get("name") or default_name),
            "modelSeeds": [${seeds}],
            "sequences": sequences,
        }
        return protenix_entry

    protenix_input = []
    input_files = [input_path]
    if input_path.is_dir():
        input_files = sorted(path for path in input_path.glob("*.json") if path.is_file())

    for path in input_files:
        with open(path) as f:
            bms = json.load(f)
        protenix_input.append(convert_entry(bms, path.stem))

    with open("protenix_input.json", "w") as f:
        json.dump(protenix_input, f, indent=2)

    print(f"[PrepProtenixComplex] Prepared {len(protenix_input)} Protenix tasks from {input_path}")
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
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa_prepared/msa_report.json", saveAs: { ignoredFilename -> "protenix_complex_msa_report.json" }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*confidence*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/**/*full_data*.json", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path complex_json

    output:
    path "predictions/**/*.cif", emit: structures, optional: true
    path "predictions/**/*confidence*.json", emit: confidence, optional: true
    path "predictions/**/*full_data*.json", emit: full_confidence, optional: true
    path "msa_prepared/msa_report.json", emit: msa_report, optional: true
    path "*.log", emit: logs, optional: true

    script:
    def model_name = params.protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def requested_template = (params.protenix_use_template == true || params.protenix_use_template == 'true')
    def anchor_target = (params.protenix_anchor_target == true || params.protenix_anchor_target == 'true')
    def geometryMode = params.protenix_target_geometry_mode ?: (anchor_target ? 'conditioned' : 'flexible')
    def use_template = requested_template || anchor_target
    def enable_cache = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null)
    def enable_fusion = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null)
    def msa_backend = params.protenix_msa_backend ?: 'auto'
    def msa_allow_cpu_fallback = (params.protenix_allow_cpu_msa_fallback == true || params.protenix_allow_cpu_msa_fallback == 'true')
    def msa_preferred_gpu_csv = normalizeGpuCsvValue(params.msa_preferred_gpus)
    def msa_excluded_gpu_csv = normalizeGpuCsvValue(params.msa_excluded_gpus)
    def msa_cpu_only_flag = (params.msa_use_gpu == false || params.msa_use_gpu == 'false') ? '--cpu-only' : ''
    def msa_cache_only_flag = (params.msa_cache_only == true || params.msa_cache_only == 'true') ? '--cache-only' : ''
    def msa_allow_cpu_fallback_flag = msa_allow_cpu_fallback ? '--allow-cpu-fallback' : ''
    def binderChainCsv = params.binder_chains ?: ''
    def epitopeResiduesCsv = params.epitope_residues ?: params.hotspot_residues ?: ''

    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
    def model_aliases = [
        'protenix_esm_20241211_v0.2.1': 'protenix_mini_esm_v0.5.0',
        'protenix_base_20241211_v0.2.1': 'protenix_base_default_v1.0.0'
    ]
    def fixedTargetSourcePath = params.fixed_target_source_path ?: ''
    def resolvedFixedTargetSourcePath = fixedTargetSourcePath
    if (fixedTargetSourcePath && !fixedTargetSourcePath.startsWith('/')) {
        resolvedFixedTargetSourcePath = "${params.code_root}/${fixedTargetSourcePath}".replaceAll('/+', '/')
    }
    def effective_model = model_aliases.get(model_name, model_name)
    if (!use_msa && !(effective_model.contains('esm') || effective_model.contains('ism') || effective_model.contains('protenix-v2'))) {
        effective_model = 'protenix_mini_esm_v0.5.0'
    }

    """
    #!/bin/bash
    set -euo pipefail

    SHARED_PROTENIX_ROOT="/protenix_weights"
    if [ "${anchor_target}" = "true" ]; then
        if [ -z "${params.fixed_target_source_path ?: ''}" ] || [ -z "${params.fixed_target_source_chains ?: ''}" ]; then
            echo "[PROTENIX-COMPLEX] ERROR: protenix_anchor_target requires fixed_target_source_path and fixed_target_source_chains" | tee -a protenix_complex.log
            exit 84
        fi
        export PROTENIX_ROOT_DIR="\$PWD/.protenix_anchor_root"
        mkdir -p "\$PROTENIX_ROOT_DIR"
        if [ -d "\$SHARED_PROTENIX_ROOT/checkpoint" ] && [ ! -e "\$PROTENIX_ROOT_DIR/checkpoint" ]; then
            ln -s "\$SHARED_PROTENIX_ROOT/checkpoint" "\$PROTENIX_ROOT_DIR/checkpoint"
        fi
        if [ -d "\$SHARED_PROTENIX_ROOT/common" ] && [ ! -e "\$PROTENIX_ROOT_DIR/common" ]; then
            ln -s "\$SHARED_PROTENIX_ROOT/common" "\$PROTENIX_ROOT_DIR/common"
        fi
        if [ -d "\$SHARED_PROTENIX_ROOT/search_database" ] && [ ! -e "\$PROTENIX_ROOT_DIR/search_database" ]; then
            ln -s "\$SHARED_PROTENIX_ROOT/search_database" "\$PROTENIX_ROOT_DIR/search_database"
        fi
    else
        export PROTENIX_ROOT_DIR="\$SHARED_PROTENIX_ROOT"
    fi
    export XDG_CACHE_HOME="\$PROTENIX_ROOT_DIR/common"
    export TRITON_CACHE_DIR="\$PROTENIX_ROOT_DIR/triton"
    export MPLCONFIGDIR="\$PROTENIX_ROOT_DIR/matplotlib"
    export PYTHONNOUSERSITE=1
    export PIP_NO_USER=1
    export PATH="/root/miniconda3/bin:\$PATH"
    mkdir -p "\$PROTENIX_ROOT_DIR/common" "\$PROTENIX_ROOT_DIR/checkpoint" "\$PROTENIX_ROOT_DIR/triton" "\$PROTENIX_ROOT_DIR/matplotlib"
    RESOLVED_FIXED_TARGET_SOURCE_PATH="${resolvedFixedTargetSourcePath}"

    # Validate container runtime is self-contained (no runtime installs or patching).
    if ! command -v python3 &> /dev/null; then
        echo "[PROTENIX-COMPLEX] ERROR: python3 not found in container image"
        exit 127
    fi

    if [ "${anchor_target}" = "true" ]; then
        if [ -z "\$RESOLVED_FIXED_TARGET_SOURCE_PATH" ] || [ ! -f "\$RESOLVED_FIXED_TARGET_SOURCE_PATH" ]; then
            echo "[PROTENIX-COMPLEX] ERROR: Fixed-target source PDB not found: \$RESOLVED_FIXED_TARGET_SOURCE_PATH" | tee -a protenix_complex.log
            exit 84
        fi
        python3 ${params.code_root}/scripts/extract_target_templates.py \\
            --pdb_files "\$RESOLVED_FIXED_TARGET_SOURCE_PATH" \\
            --target_chains "${params.fixed_target_source_chains}" \\
            --out_dir "\$PROTENIX_ROOT_DIR/mmcif" \\
            --manifest target_template_manifest.json \\
            ${params.fixed_target_model_number ? '--model_number ' + params.fixed_target_model_number : ''}
        echo "[PROTENIX-COMPLEX] Fixed-target mode enabled: staged target templates from \$RESOLVED_FIXED_TARGET_SOURCE_PATH" | tee -a protenix_complex.log
    fi

    if [ "${use_template}" = "true" ]; then
        template_dir="\$PROTENIX_ROOT_DIR/mmcif"
        template_file=""
        if [ -d "\$template_dir" ]; then
            template_file="\$(find "\$template_dir" -type f \\( -name '*.cif' -o -name '*.mmcif' -o -name '*.cif.gz' -o -name '*.mmcif.gz' \\) | head -n 1 || true)"
        fi
        if [ -z "\$template_file" ]; then
            echo "[PROTENIX-COMPLEX] ERROR: Template mode requested but no mmCIF template database was found at \$template_dir" | tee -a protenix_complex.log
            echo "[PROTENIX-COMPLEX] Set protenix_use_template=false or populate \$template_dir with template CIF files." | tee -a protenix_complex.log
            exit 89
        fi
        echo "[PROTENIX-COMPLEX] Template database detected: \$template_file"
    fi
    if [ "${requested_template}" = "true" ] && [ "${anchor_target}" != "true" ]; then
        echo "[PROTENIX-COMPLEX] Generic template DB conditioning enabled for this run; no explicit target anchoring is applied." | tee -a protenix_complex.log
    fi

    # Fail fast if GPU architecture is unsupported by the container's torch build.
    python3 - << 'PY'
import re
import sys

try:
    import torch
except Exception as exc:
    print(f"[PROTENIX-COMPLEX] ERROR: Could not import torch: {exc}")
    raise SystemExit(87)

if not torch.cuda.is_available():
    print("[PROTENIX-COMPLEX] WARNING: torch.cuda.is_available() is false; continuing")
    raise SystemExit(0)

major, minor = torch.cuda.get_device_capability(0)
device_arch = f"sm_{major}{minor}"
supported = set()
for arch in torch.cuda.get_arch_list():
    match = re.search(r"sm_(\\d+)", arch)
    if match:
        supported.add(f"sm_{match.group(1)}")

if supported and device_arch not in supported:
    print(f"[PROTENIX-COMPLEX] ERROR: GPU architecture {device_arch} is unsupported by this torch build: {sorted(supported)}")
    raise SystemExit(88)

print(f"[PROTENIX-COMPLEX] torch={torch.__version__} cuda={torch.version.cuda} device_arch={device_arch} supported={sorted(supported)}")
PY

    echo "[PROTENIX-COMPLEX] Requested model: ${model_name} | Effective model: ${effective_model}"
    echo "[PROTENIX-COMPLEX] Seeds: ${seeds} | Samples: ${n_sample} | Steps: ${n_step} | Cycles: ${n_cycle}"
    echo "[PROTENIX-COMPLEX] MSA: ${use_msa} | Template: ${use_template} | Cache: ${enable_cache} | Fusion: ${enable_fusion}"
    echo "[PROTENIX-COMPLEX] Input JSON: ${complex_json}"
    cat ${complex_json}

    PROTENIX_INPUT_JSON="${complex_json}"
    if [ "${use_template}" = "true" ]; then
        python3 - <<'PY'
import json
import os
from pathlib import Path

input_path = Path("${complex_json}")
output_path = Path("protenix_input_with_templates.json")
template_dir = str((Path(os.environ["PROTENIX_ROOT_DIR"]) / "mmcif").resolve())

payload = json.loads(input_path.read_text(encoding="utf-8"))
if not isinstance(payload, list):
    raise SystemExit("Expected top-level list in Protenix complex input JSON")

for entry in payload:
    if isinstance(entry, dict):
        entry["templatesPath"] = template_dir

output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
        PROTENIX_INPUT_JSON="protenix_input_with_templates.json"
        echo "[PROTENIX-COMPLEX] Injected templatesPath=\$PROTENIX_ROOT_DIR/mmcif into \$PROTENIX_INPUT_JSON"
    fi
    python3 ${params.code_root}/scripts/prepare_protenix_constraints.py \\
        --input_json "\$PROTENIX_INPUT_JSON" \\
        --output_json protenix_input_conditioned.json \\
        --binder_chains "${binderChainCsv}" \\
        --predicted_target_chains "${params.target_chains ?: ''}" \\
        --epitope_residues "${epitopeResiduesCsv}" \\
        --target_pdb "\$RESOLVED_FIXED_TARGET_SOURCE_PATH" \\
        --source_target_chains "${params.fixed_target_source_chains ?: ''}" \\
        --auto-pocket-if-missing \\
        --auto-pocket-max-residues ${params.protenix_auto_pocket_residue_count ?: 24} \\
        --pocket-max-distance ${params.protenix_pocket_max_distance ?: 8.0} \\
        ${params.fixed_target_model_number ? '--target_model_number ' + params.fixed_target_model_number : ''} \\
        2>&1 | tee -a protenix_msa_prep.log
    PROTENIX_INPUT_JSON="protenix_input_conditioned.json"
    if [ "${use_msa}" = "true" ]; then
        PROTENIX_MSA_CACHE_DIR="${params.msa_cache_dir}"
        if [ -z "\$PROTENIX_MSA_CACHE_DIR" ] || ! mkdir -p "\$PROTENIX_MSA_CACHE_DIR/.locks" 2>/dev/null; then
            PROTENIX_MSA_CACHE_DIR="\$PWD/msa_prepared/cache"
            mkdir -p "\$PROTENIX_MSA_CACHE_DIR"
            echo "[PROTENIX-COMPLEX] Shared MSA cache unavailable; using task-local cache at \$PROTENIX_MSA_CACHE_DIR"
        else
            echo "[PROTENIX-COMPLEX] Using shared MSA cache at \$PROTENIX_MSA_CACHE_DIR"
        fi
        python3 ${params.code_root}/scripts/prepare_protenix_msa.py \\
            --input_json "\$PROTENIX_INPUT_JSON" \\
            --output_json prepared_input.json \\
            --out_dir msa_prepared \\
            --report_json msa_prepared/msa_report.json \\
            --backend "${msa_backend}" \\
            --binder-chain-ids "${binderChainCsv}" \\
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
            ${msa_cache_only_flag} \\
            ${msa_allow_cpu_fallback_flag} \\
            2>&1 | tee protenix_msa_prep.log
        PROTENIX_INPUT_JSON="prepared_input.json"
    fi
    if [ "${anchor_target}" = "true" ] && [ -n "${params.fixed_target_source_sequence ?: ''}" ]; then
        TARGET_TEMPLATE_PDB_ID="\$(basename "\$RESOLVED_FIXED_TARGET_SOURCE_PATH" .pdb | tr '[:upper:]' '[:lower:]')"
        python3 ${params.code_root}/scripts/prepare_protenix_exact_templates.py \\
            --input_json "\$PROTENIX_INPUT_JSON" \\
            --output_json prepared_input_exact_templates.json \\
            --target_sequence "${params.fixed_target_source_sequence}" \\
            --template_pdb_id "\$TARGET_TEMPLATE_PDB_ID" \\
            --template_chains "${params.fixed_target_source_chains}" \\
            --out_dir exact_target_templates \\
            2>&1 | tee -a protenix_msa_prep.log
        PROTENIX_INPUT_JSON="prepared_input_exact_templates.json"
    fi

    ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS=""
    if [ "${anchor_target}" = "true" ] && [ -n "${params.fixed_target_source_sequence ?: ''}" ]; then
        ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS="\$TARGET_TEMPLATE_PDB_ID"
    fi

    # Run structure prediction
    echo "[PROTENIX-COMPLEX] Running structure prediction..."
    python3 ${params.code_root}/scripts/run_protenix_inference.py \\
        --input "\$PROTENIX_INPUT_JSON" \\
        --out_dir predictions/ \\
        --model_name ${effective_model} \\
        --seeds "${seeds}" \\
        --sample ${n_sample} \\
        --step ${n_step} \\
        --cycle ${n_cycle} \\
        --use_msa ${use_msa} \\
        --use_template ${use_template} \\
        --enable_cache ${enable_cache} \\
        --enable_fusion ${enable_fusion} \\
        ${anchor_target ? '--allow_exact_duplicate_template_pdb_ids "$ALLOW_EXACT_DUPLICATE_TEMPLATE_PDB_IDS"' : ''} \\
        2>&1 | tee protenix_complex.log

    first_cif="\$(find predictions/ -type f -name '*.cif' | head -n 1 || true)"
    if [ -z "\$first_cif" ]; then
        echo "[PROTENIX-COMPLEX] ERROR: Protenix returned without producing any CIF output" | tee -a protenix_complex.log
        if [ -d predictions/ERR ]; then
            echo "[PROTENIX-COMPLEX] Error reports under predictions/ERR:" | tee -a protenix_complex.log
            find predictions/ERR -type f | head -20 | tee -a protenix_complex.log || true
            first_err="\$(find predictions/ERR -type f | head -n 1 || true)"
            if [ -n "\$first_err" ]; then
                echo "[PROTENIX-COMPLEX] First error report (\$first_err):" | tee -a protenix_complex.log
                sed -n '1,80p' "\$first_err" | tee -a protenix_complex.log || true
            fi
        fi
        exit 86
    fi

    first_full_data="\$(find predictions/ -type f -name '*full_data*.json' | head -n 1 || true)"
    if [ -z "\$first_full_data" ]; then
        echo "[PROTENIX-COMPLEX] ERROR: Protenix returned without producing any full-data confidence JSON output" | tee -a protenix_complex.log
        exit 85
    fi

    if [ "${geometryMode}" != "flexible" ] && [ -n "${params.fixed_target_source_path ?: ''}" ] && [ -n "${params.fixed_target_source_chains ?: ''}" ] && [ -n "${params.target_chains ?: ''}" ]; then
        python3 ${params.code_root}/scripts/finalize_target_geometry.py \\
            --prediction_dir predictions \\
            --backend protenix \\
            --geometry_mode "${geometryMode}" \\
            --target_pdb "${params.fixed_target_source_path}" \\
            --reference_target_chains "${params.fixed_target_source_chains}" \\
            --predicted_target_chains "${params.target_chains}" \\
            ${params.fixed_target_model_number ? '--target_model_number ' + params.fixed_target_model_number : ''} \\
            2>&1 | tee -a protenix_complex.log
    fi

    echo "[PROTENIX-COMPLEX] Prediction complete. Listing outputs:"
    find predictions/ -type f \\( -name "*.cif" -o -name "*confidence*.json" \\) | head -20 || true
    """
}
