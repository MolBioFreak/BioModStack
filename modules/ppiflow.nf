def paramValueOrDefault(params, String key, defaultValue) {
    if (!params.containsKey(key) || params[key] == null) {
        return defaultValue
    }
    def value = params[key]
    if (value instanceof CharSequence && value.toString().trim() == '') {
        return defaultValue
    }
    return value
}

process IdentifyAnchorResidues {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_anchors.json"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_interface_score.json"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_rotamer_enrichment.json"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_enriched_complex.pdb"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_ppiflow_positions.txt"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_cdr_positions.txt"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*_cdr_positions_by_loop.json"

    input:
    tuple val(meta), path(complex_pdb)

    output:
    tuple val(meta), path(complex_pdb), path("${meta.id}_enriched_complex.pdb"), path("${meta.id}_anchors.json"), path("${meta.id}_ppiflow_positions.txt"), path("${meta.id}_cdr_positions.txt"), path("${meta.id}_cdr_positions_by_loop.json"), emit: anchor_inputs
    tuple val(meta), path("${meta.id}_interface_score.json"), emit: interface_scores
    tuple val(meta), path("${meta.id}_rotamer_enrichment.json"), emit: rotamer_enrichment

    script:
    def frameworkType = params.get('framework_type')
    def defaultAntibodyChains = frameworkType == 'nanobody' ? 'H' : 'H,L'
    def antibodyChains = params.antibody_chains ?: defaultAntibodyChains
    def antigenChains = params.antigen_chains ?: ''
    def energyThreshold = paramValueOrDefault(params, 'maturation_anchor_threshold', -5.0)
    def distanceCutoff = paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0)
    def enrichmentEnabled = params.ppiflow_rotamer_enrichment_enabled != null ? params.ppiflow_rotamer_enrichment_enabled : true
    def requireAnchors = params.ppiflow_require_anchors != null ? params.ppiflow_require_anchors : true
    def rotamerShellDistance = paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0))
    def relaxAntibodyBackboneShell = paramValueOrDefault(params, 'ppiflow_relax_antibody_backbone_shell', false)
    def regionMode = params.ppiflow_region_mode ?: 'selected_cdrs'
    def selectedLoopsSpec = params.ppiflow_selected_loops ?: ''
    def cdrPositionsByLoopJson = groovy.json.JsonOutput.toJson(params.get('cdr_positions_by_loop') ?: [:])
    def manualCdrDefinitionsJson = groovy.json.JsonOutput.toJson(params.get('manual_cdr_definitions') ?: [])
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    cat > cdr_positions_by_loop.json <<'JSON'
${cdrPositionsByLoopJson}
JSON

    cat > manual_cdr_definitions.json <<'JSON'
${manualCdrDefinitionsJson}
JSON

    enrichmentArgs=""
    if [ "${enrichmentEnabled}" = "true" ]; then
        enrichmentArgs="\${enrichmentArgs} --rotamer_enrichment"
    fi
    if [ "${relaxAntibodyBackboneShell}" = "true" ]; then
        enrichmentArgs="\${enrichmentArgs} --relax_antibody_backbone_shell"
    fi

    "\${PYTHON_BIN}" "${params.code_root}/scripts/prepare_ppiflow_maturation.py" \\
        --pdb "${complex_pdb}" \\
        --antibody_chains "${antibodyChains}" \\
        --antigen_chains "${antigenChains}" \\
        --energy_threshold ${energyThreshold} \\
        --distance_cutoff ${distanceCutoff} \\
        --rotamer_shell_distance ${rotamerShellDistance} \\
        --region_mode "${regionMode}" \\
        --selected_loops "${selectedLoopsSpec}" \\
        --cdr_positions_by_loop_json "cdr_positions_by_loop.json" \\
        --manual_cdr_definitions_json "manual_cdr_definitions.json" \\
        --output_enriched_pdb "${meta.id}_enriched_complex.pdb" \\
        --output_anchors "${meta.id}_anchors.json" \\
        --output_score "${meta.id}_interface_score.json" \\
        --output_rotamer_enrichment "${meta.id}_rotamer_enrichment.json" \\
        --output_positions "${meta.id}_ppiflow_positions.txt" \\
        --output_cdr_positions "${meta.id}_cdr_positions.txt" \\
        --output_cdr_positions_by_loop "${meta.id}_cdr_positions_by_loop.json" \\
        \${enrichmentArgs}

    anchorCount=\$("\${PYTHON_BIN}" - <<'PY'
import json
from pathlib import Path

anchor_path = Path("${meta.id}_anchors.json")
payload = json.loads(anchor_path.read_text())
print(int(payload.get("anchor_count") or 0))
PY
    )

    if [ "${requireAnchors}" = "true" ] && [ "\${anchorCount}" -le 0 ]; then
        echo "[PPIFlow] Warning: strict anchor requirement not satisfied for ${complex_pdb}; anchor_count=0. This structure will be skipped downstream instead of failing the entire child batch." >&2
    fi
    """
}

process RunPartialFlow {
    label 'gpu'
    label 'PPIFlow'
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "fixed_positions.txt"
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "ppiflow_mask_validation.json"

    input:
    tuple val(meta), path(original_complex_pdb), path(complex_pdb), path(anchors_json), path(ppiflow_positions), path(cdr_positions), path(cdr_positions_by_loop_json)

    output:
    tuple val(meta), path("ppiflow_backbones"), path("ppiflow_backbones_manifest.json"), emit: backbones

    script:
    def frameworkType = params.get('framework_type')
    def defaultAntibodyChains = frameworkType == 'nanobody' ? 'H' : 'H,L'
    def antibodyChains = params.antibody_chains ?: defaultAntibodyChains
    def antibodyList = antibodyChains.toString().split(',')*.trim().findAll { it }
    def heavyChain = params.ppiflow_heavy_chain ?: (antibodyList ? antibodyList[0] : 'H')
    def lightChain = params.ppiflow_light_chain ?: (antibodyList.size() > 1 ? antibodyList[1] : '')
    def antigenChain = params.ppiflow_antigen_chain ?: (params.antigen_chains ? params.antigen_chains.toString().replace(',', '') : '')
    def startT = paramValueOrDefault(params, 'ppiflow_start_t', 0.8)
    def samplesPerTarget = paramValueOrDefault(params, 'ppiflow_samples_per_target', 1)
    def retryLimit = paramValueOrDefault(params, 'ppiflow_retry_limit', 10)
    def configPath = params.ppiflow_config ?: "/app/ppiflow/configs/test_antibody.yaml"
    def defaultCheckpoint = frameworkType == 'nanobody' ? 'nanobody' : 'antibody'
    def checkpointName = params.ppiflow_checkpoint ?: defaultCheckpoint
    def checkpointPath = params.ppiflow_checkpoint_path ?: (params.ppiflow_weights_dir ? "/opt/ppiflow/ckpt/${checkpointName}.ckpt" : "")
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    export HOME="\$PWD"
    export XDG_CACHE_HOME="\$PWD/.cache"
    export TRITON_CACHE_DIR="\${XDG_CACHE_HOME}/triton"
    export TORCH_EXTENSIONS_DIR="\${XDG_CACHE_HOME}/torch_extensions"
    mkdir -p "\${TRITON_CACHE_DIR}" "\${TORCH_EXTENSIONS_DIR}"

    "\${PYTHON_BIN}" "${params.code_root}/scripts/anchors_to_ppiflow_positions.py" \\
        --anchors_json "${anchors_json}" \\
        --output fixed_positions.txt

    fixedPositionsSpec=\$(tr -d '\\n' < fixed_positions.txt)
    cdrPositionsSpec=\$(tr -d '\\n' < "${ppiflow_positions}")
    "\${PYTHON_BIN}" "${params.code_root}/scripts/validate_ppiflow_masks.py" \\
        --fixed_positions "\${fixedPositionsSpec}" \\
        --movable_positions "\${cdrPositionsSpec}" \\
        --report_json ppiflow_mask_validation.json
    hotspotsSpec="${params.epitope_residues ?: ''}"

    heavyChain="${heavyChain}"
    lightChain="${lightChain}"
    antigenChainBash="${antigenChain}"

    detectedChains=\$("\${PYTHON_BIN}" - <<'PY'
from pathlib import Path

chains = []
with open(Path("${complex_pdb}")) as handle:
    for line in handle:
        if line.startswith("ATOM"):
            chain = line[21].strip()
            if chain and chain not in chains:
                chains.append(chain)
print("".join(chains))
PY
    )

    cdrChains=\$("\${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import re

chains = []
for token in Path("${ppiflow_positions}").read_text().strip().split(","):
    token = token.strip()
    if not token:
        continue
    match = re.match(r"([A-Za-z])", token)
    if match:
        chain = match.group(1)
        if chain not in chains:
            chains.append(chain)
print("".join(chains))
PY
    )

    if ! printf '%s' "\${detectedChains}" | grep -qF "\${heavyChain}"; then
        inferredHeavy=\$(printf '%s' "\${cdrChains}" | cut -c1)
        if [ -z "\${inferredHeavy}" ] || ! printf '%s' "\${detectedChains}" | grep -qF "\${inferredHeavy}"; then
            inferredHeavy=\$("\${PYTHON_BIN}" - <<'PY'
from pathlib import Path

chains = []
with open(Path("${complex_pdb}")) as handle:
    for line in handle:
        if line.startswith("ATOM"):
            chain = line[21].strip()
            if chain and chain not in chains:
                chains.append(chain)
print(chains[0] if chains else "")
PY
            )
        fi
        if [ -n "\${inferredHeavy}" ]; then
            echo "[PPIFlow] Warning: heavy chain '${heavyChain}' not found in ${complex_pdb}; using detected chain '\${inferredHeavy}' instead" >&2
            heavyChain="\${inferredHeavy}"
        else
            echo "[PPIFlow] ERROR: heavy chain '${heavyChain}' not found in ${complex_pdb}; detected chains: \${detectedChains}" >&2
            exit 1
        fi
    fi

    if [ -z "\${lightChain}" ] || ! printf '%s' "\${detectedChains}" | grep -qF "\${lightChain}"; then
        inferredLight=\$(printf '%s' "\${cdrChains}" | cut -c2)
        if [ -n "\${inferredLight}" ] && [ "\${inferredLight}" != "\${heavyChain}" ] && printf '%s' "\${detectedChains}" | grep -qF "\${inferredLight}"; then
            if [ -n "\${lightChain}" ]; then
                echo "[PPIFlow] Warning: light chain '\${lightChain}' not found in ${complex_pdb}; using CDR-derived chain '\${inferredLight}' instead" >&2
            fi
            lightChain="\${inferredLight}"
        elif [ -n "\${lightChain}" ]; then
            echo "[PPIFlow] Warning: light chain '\${lightChain}' not found in ${complex_pdb}; continuing in single-chain mode" >&2
            lightChain=""
        fi
    fi

    if [ -n "\${antigenChainBash}" ] && [ "\${antigenChainBash}" = "\${heavyChain}" ]; then
        echo "[PPIFlow] Warning: antigen chain '\${antigenChainBash}' overlaps inferred heavy chain; re-inferring antigen chain" >&2
        antigenChainBash=""
    fi

    if [ -z "\${antigenChainBash}" ] || ! printf '%s' "\${detectedChains}" | grep -qF "\${antigenChainBash}"; then
        antigenChainBash=\$(PPI_HEAVY_CHAIN="\${heavyChain}" PPI_LIGHT_CHAIN="\${lightChain}" "\${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

pdb_path = Path("${complex_pdb}")
chains = []
with open(pdb_path) as f:
    for line in f:
        if line.startswith("ATOM"):
            chain = line[21].strip()
            if chain and chain not in chains:
                chains.append(chain)

heavy = os.environ.get("PPI_HEAVY_CHAIN", "")
light = os.environ.get("PPI_LIGHT_CHAIN", "")
ab = {c for c in (heavy, light) if c}
chains = [c for c in chains if c not in ab]
print("".join(chains))
PY
        )
    fi

    if [ -z "\${antigenChainBash}" ]; then
        echo "[PPIFlow] ERROR: unable to infer antigen chain for ${complex_pdb}; detected chains: \${detectedChains}, antibody chains: \${heavyChain}\${lightChain}" >&2
        exit 1
    fi

    hotspotsSpec=\$(PPI_HOTSPOTS_SPEC="\${hotspotsSpec}" PPI_ANTIGEN_CHAIN="\${antigenChainBash}" PPI_HEAVY_CHAIN="\${heavyChain}" PPI_LIGHT_CHAIN="\${lightChain}" "\${PYTHON_BIN}" - <<'PY'
import os
import re

hotspots = os.environ.get("PPI_HOTSPOTS_SPEC", "").strip()
antigen = os.environ.get("PPI_ANTIGEN_CHAIN", "").strip()
heavy = os.environ.get("PPI_HEAVY_CHAIN", "").strip()
light = os.environ.get("PPI_LIGHT_CHAIN", "").strip()

if not hotspots:
    print("")
    raise SystemExit(0)

tokens = [token.strip() for token in hotspots.split(",") if token.strip()]
chains = []
for token in tokens:
    match = re.match(r"([A-Za-z])", token)
    if not match:
        print("")
        raise SystemExit(0)
    chain = match.group(1)
    if chain not in chains:
        chains.append(chain)

antigen_set = set(antigen)
antibody_set = {c for c in (heavy, light) if c}

if set(chains).issubset(antigen_set):
    print(",".join(tokens))
    raise SystemExit(0)

if len(chains) == 1 and len(antigen) == 1:
    old_chain = chains[0]
    new_chain = antigen
    remapped = [re.sub(r"^[A-Za-z]", new_chain, token, count=1) for token in tokens]
    print(",".join(remapped))
    raise SystemExit(0)

if set(chains).issubset(antibody_set):
    print("")
    raise SystemExit(0)

print("")
PY
    )

    hotspotArg=""
    if [ -n "\${hotspotsSpec}" ]; then
        if [ "${params.epitope_residues ?: ''}" != "\${hotspotsSpec}" ]; then
            echo "[PPIFlow] Warning: remapped hotspot residues from '${params.epitope_residues ?: ''}' to '\${hotspotsSpec}' to match antigen chain '\${antigenChainBash}'" >&2
        fi
        hotspotArg="--specified_hotspots \${hotspotsSpec}"
    elif [ -n "${params.epitope_residues ?: ''}" ]; then
        echo "[PPIFlow] Warning: dropping hotspot residues '${params.epitope_residues ?: ''}' because they do not match inferred antigen chain '\${antigenChainBash}'" >&2
    fi

    if [ -z "${checkpointPath}" ]; then
        echo "[PPIFlow] ERROR: No checkpoint path configured (ppiflow_checkpoint_path)." >&2
        exit 1
    fi

    ppiflow_script="/app/ppiflow/sample_antibody_nanobody_partial.py"
    if [ ! -f "\${ppiflow_script}" ]; then
        ppiflow_script="/app/ppiflow/sample_antibody_partial_flow.py"
    fi

    if [ ! -f "\${ppiflow_script}" ]; then
        echo "[PPIFlow] ERROR: No antibody partial-flow entrypoint found in container" >&2
        exit 1
    fi

    # Upstream sample_antibody_nanobody_partial.py already expands
    # args.samples_per_target into repeated input rows and intentionally keeps
    # ppi_dataset.samples_per_target = 1 in the generated config. Overriding
    # that config value multiplies outputs to N x N and breaks downstream
    # expectations.
    configPathBash="${configPath}"
    if [ "\${configPathBash}" = "/app/ppiflow/configs/inference_nanobody.yaml" ] && [ -f "/app/ppiflow/configs/test_antibody.yaml" ]; then
        echo "[PPIFlow] Warning: remapping config '\${configPathBash}' to '/app/ppiflow/configs/test_antibody.yaml' for antibody partial-flow compatibility" >&2
        configPathBash="/app/ppiflow/configs/test_antibody.yaml"
    fi

    if [ ! -f "\${configPathBash}" ]; then
        echo "[PPIFlow] ERROR: PPIFlow config not found: \${configPathBash}" >&2
        exit 1
    fi

    lightChainArg=""
    if [ -n "\${lightChain}" ]; then
        lightChainArg="--light_chain \${lightChain}"
    fi

    nativeCommand=("\${PYTHON_BIN}" "\${ppiflow_script}")
    if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ]; then
        nativeCommand=("\${PYTHON_BIN}" "${params.code_root}/scripts/maturation_native_adapter.py"
            --producer ppiflow --root /app/ppiflow --reference "${original_complex_pdb}"
            --binder "\${heavyChain},\${lightChain}" --target "${params.antigen_chains ?: antigenChain}"
            --selected "${ppiflow_positions}" --loops "${cdr_positions_by_loop_json}" -- "\${ppiflow_script}")
    fi
    "\${nativeCommand[@]}" \\
        --complex_pdb "${complex_pdb}" \\
        --fixed_positions "\${fixedPositionsSpec}" \\
        --cdr_position "\${cdrPositionsSpec}" \\
        --start_t ${startT} \\
        --samples_per_target ${samplesPerTarget} \\
        --output_dir "ppiflow_out" \\
        --retry_Limit ${retryLimit} \\
        --config "\${configPathBash}" \\
        --model_weights "${checkpointPath}" \\
        --antigen_chain "\${antigenChainBash}" \\
        --heavy_chain "\${heavyChain}" \\
        \${lightChainArg} \\
        \${hotspotArg} \\
        --name "${meta.id}"

"\${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import json
import shutil

pdbs = sorted(Path("ppiflow_out").rglob("*.pdb"))
if not pdbs:
    raise SystemExit("No PPIFlow PDB outputs found")
expected = int("${samplesPerTarget}")
if len(pdbs) != expected:
    raise SystemExit(f"[PPIFlow] ERROR: expected {expected} output PDBs but found {len(pdbs)} in ppiflow_out")
out_dir = Path("ppiflow_backbones")
out_dir.mkdir(exist_ok=True)
manifest = []
for i, pdb in enumerate(pdbs):
    out_name = f"${meta.id}_ppiflow_sample{i}.pdb"
    shutil.copy2(pdb, out_dir / out_name)
    comparison_path = None
    if "${params.get('core_protein_scientific_contract') ?: ''}" == "1":
        comparison = Path(str(pdb) + '.comparison.json')
        if not comparison.is_file():
            raise ValueError('native comparison publication missing')
        comparison_path = str((out_dir / (out_name + '.comparison.json')).resolve())
        shutil.copy2(comparison, comparison_path)
    manifest.append({
        "comparison_path": comparison_path,
        "sample_index": i,
        "name": out_name,
        "path": str((out_dir / out_name).resolve()),
    })
Path("ppiflow_backbones_manifest.json").write_text(json.dumps(manifest, indent=2))
PY
    """
}

process PrepMaturationRedesign {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "fixed_positions.txt"
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "mpnn_fixed_chains.json"
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "fampnn.csv"

    input:
    tuple val(meta), path(backbone_pdbs), path(anchors_json), path(cdr_positions), path(cdr_positions_by_loop), path(comparison_requests)

    output:
    tuple val(meta), path("fampnn_input/*.pdb"), path("fampnn.csv"), path("fampnn_transport"), emit: prep

    script:
    def frameworkType = params.get('framework_type')
    def defaultAntibodyChains = frameworkType == 'nanobody' ? 'H' : 'H,L'
    def antibodyChains = params.antibody_chains ?: defaultAntibodyChains
    def designModeRaw = params.maturation_design_mode ?: 'inherit'
    def designMode = designModeRaw == 'inherit' ? (params.antibody_design_mode ?: 'cdr_only') : designModeRaw
    def selectedLoopsSpec = (params.ppiflow_region_mode ?: 'selected_cdrs').toString() == 'selected_cdrs'
        ? (params.ppiflow_selected_loops ?: '')
        : ''
    def selectedLoopsArg = selectedLoopsSpec ? " --design_loops \"${selectedLoopsSpec}\"" : ""
    def effectiveDesignMode = selectedLoopsSpec && designMode == 'cdr_only' ? 'cdr_selective' : designMode
    def protectTetrad = params.protect_vhh_tetrad != null ? params.protect_vhh_tetrad : true
    def extraFixedJson = params.manual_mutation_fixed_positions_json ? " \\\\\n        --extra_fixed_positions_json \\\"${params.manual_mutation_fixed_positions_json}\\\"" : ""
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    mkdir -p input_pdbs
    cp ${backbone_pdbs} ./input_pdbs/
    mkdir -p fampnn_transport
    prepTransport=()
    if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ]; then
        cp ${comparison_requests} ./input_pdbs/
        prepTransport=(--maturation_transport)
    fi

    "\${PYTHON_BIN}" "${params.code_root}/scripts/anchors_to_ppiflow_positions.py" \\
        --anchors_json "${anchors_json}" \\
        --output fixed_positions.txt

    anchors_spec=\$(cat fixed_positions.txt | tr -d '\\n')

    "\${PYTHON_BIN}" "${params.code_root}/scripts/prep_fampnn_designs.py" \\
        --input_dir "./input_pdbs" \\
        --out_dir "fampnn_input" "\${prepTransport[@]}"
    if [ "\${#prepTransport[@]}" -gt 0 ]; then cp fampnn_input/*.comparison.json fampnn_transport/; fi

    cdr_positions=\$(cat "${cdr_positions}" | tr -d '\\n')

    "\${PYTHON_BIN}" "${params.code_root}/scripts/prep_antibody_constraints.py" \\
        --input_dir "./" \\
        --out_fampnn "fampnn.csv" \\
        --out_mpnn "mpnn_fixed_chains.json" \\
        --design_mode "${effectiveDesignMode}" \\
        --protect_tetrad "${protectTetrad}"${selectedLoopsArg} \\
        --antibody_chains "${antibodyChains}" \\
        --lock_target_chains "${params.lock_target_chains != null ? params.lock_target_chains : true}" \\
        --lock_antibody_framework "${params.lock_antibody_framework != null ? params.lock_antibody_framework : true}" \\
        --extra_fixed_positions "\${anchors_spec}"${extraFixedJson} \\
        --cdr_positions "\${cdr_positions}" \\
        --cdr_positions_by_loop "${cdr_positions_by_loop}"
    """
}

process RunMaturationFAMPNN {
    label 'FAMPNN'
    label 'gpu_light'
    publishDir "${params.out_dir}/run/ppiflow", mode: 'copy', pattern: "*.log"
    // Keep pre-filter redesign artifacts out of the final results directory.
    // FilterByMaturation is the stage that should decide which matured designs
    // are exposed as child outputs to the parent workflow and ingester.
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "results/*.pdb", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/run/ppiflow/redesign_debug", mode: 'copy', pattern: "results/*.json", saveAs: { fn -> fn.replace('results/', '') }

    input:
    tuple val(meta), path(pdbs), path(csv), path(transport_dir)

    output:
    tuple val(meta), path("matured_pdbs/*.pdb"), path("matured_jsons/*.json"), emit: redesigned

    script:
    def analysisChain = params.analysis_chain_id ?: 'all_chains'
    def temperature = paramValueOrDefault(params, 'maturation_redesign_temp', paramValueOrDefault(params, 'fampnn_temperature', 0.1))
    def numSteps = paramValueOrDefault(params, 'maturation_redesign_steps', paramValueOrDefault(params, 'fampnn_num_steps', 100))
    def checkpointPreset = (params.fampnn_checkpoint ?: 'fampnn_0_0.pt').toString().trim()
    def checkpointOverride = (params.fampnn_checkpoint_path ?: '').toString().trim()
    def checkpointMap = [
        'fampnn_0_0.pt': '/app/fampnn/weights/fampnn_0_0.pt',
        'fampnn_0_3.pt': '/app/fampnn/weights/fampnn_0_3.pt',
        'fampnn_0_3_cath.pt': '/app/fampnn/weights/fampnn_0_3_cath.pt',
    ]
    def checkpointPath = checkpointOverride ?: checkpointMap.get(checkpointPreset, checkpointPreset)
    if (!checkpointPath) {
        throw new IllegalArgumentException("FAMPNN checkpoint not configured for PPIFlow redesign. Set params.fampnn_checkpoint or params.fampnn_checkpoint_path.")
    }
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    mkdir -p results
    # PyTorch >=2.6 defaults torch.load(..., weights_only=True), which breaks
    # legacy FAMPNN checkpoints saved with OmegaConf/defaultdict metadata.
    nativeCommand=("\${PYTHON_BIN}" /app/fampnn/fampnn/inference/seq_design.py)
    if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ]; then
        cp ${transport_dir}/*.comparison.json ./
        nativeCommand=("\${PYTHON_BIN}" "${params.code_root}/scripts/maturation_native_adapter.py"
            --producer fampnn --root /app/fampnn -- /app/fampnn/fampnn/inference/seq_design.py)
    fi
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 "\${nativeCommand[@]}" \\
        batch_size=1 \\
        checkpoint_path=${checkpointPath} \\
        exclude_cys=${params.fampnn_exclude_cys != null ? params.fampnn_exclude_cys : true} \\
        fixed_pos_csv=${csv} \\
        num_seqs_per_pdb=1 \\
        pdb_dir="./" \\
        presort_by_length=true \\
        psce_threshold=${paramValueOrDefault(params, 'fampnn_psce_threshold', 0.3)} \\
        temperature=${temperature} \\
        seq_only=${paramValueOrDefault(params, 'fampnn_seq_only', false)} \\
        repack_last=${paramValueOrDefault(params, 'fampnn_repack_last', true)} \\
        timestep_schedule.num_steps=${numSteps} \\
        out_dir="fampnn_output" \\
        ${params.fampnn_extra_config ? params.fampnn_extra_config : ''} \\
        2>&1 | tee fampnn_redesign_${task.index}.log

    for file in fampnn_output/samples/*_sample*.pdb; do
        base_name=\$(basename "\$file")
        new_name=\$(echo "\$base_name" | sed 's/sample/seq_/')
        cp "\$file" "results/\$new_name"
        if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ]; then
            cp "\$file.comparison.json" "results/\$new_name.comparison.json"
        fi
    done

    "\${PYTHON_BIN}" "${params.code_root}/scripts/analyse_fampnn.py" \\
        --input_dir results \\
        --chain_id ${analysisChain} \\
        --ignore_cbeta \\
        --out_dir results

    mkdir -p matured_pdbs matured_jsons
    cp results/*.pdb matured_pdbs/
    cp results/*.json matured_jsons/

    if [ -n "${params.out_dir}" ]; then
        mkdir -p "${params.out_dir}/run/ppiflow/redesign_debug" 2>/dev/null || true
        cp results/*.pdb "${params.out_dir}/run/ppiflow/redesign_debug/" 2>/dev/null || true
        cp results/*.json "${params.out_dir}/run/ppiflow/redesign_debug/" 2>/dev/null || true
    fi
    """
}

def maturationScientificContractArg(params) {
    def value = params.get('core_protein_scientific_contract')
    if (value == null) return ''
    if (value instanceof Boolean || value.toString() != '1') {
        throw new IllegalArgumentException('core_protein_scientific_contract must be exactly 1')
    }
    return '--core-protein-scientific-contract 1'
}

process ScoreMaturationImprovement {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "scores/*_maturation_score.json", saveAs: { fn -> fn.replace('scores/', '') }

    input:
    tuple val(meta), path(original_pdb), path(matured_pdbs), path(ppiflow_positions), path(cdr_positions_by_loop_json)

    output:
    tuple val(meta), path("scores/*_maturation_score.json"), emit: scores

    script:
    def scientificContractArg = maturationScientificContractArg(params)
    def frameworkType = params.get('framework_type')
    def defaultAntibodyChains = frameworkType == 'nanobody' ? 'H' : 'H,L'
    def antibodyChains = params.antibody_chains ?: (scientificContractArg ? '' : defaultAntibodyChains)
    def antigenChains = params.antigen_chains ?: ''
    def distanceCutoff = paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0)
    def objectiveMode = paramValueOrDefault(params, 'ppiflow_objective_mode', 'selected_interface')
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    mkdir -p scores
    for matured_pdb in ${matured_pdbs}; do
        base_name=\$(basename "\$matured_pdb" .pdb)
        case "\$matured_pdb" in *.pdb) ;; *) continue ;; esac
        comparisonArg=()
        if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ] && [ -f "\$matured_pdb.comparison.json" ]; then
            comparisonArg=(--comparison-request "\$matured_pdb.comparison.json")
        fi
        "\${PYTHON_BIN}" "${params.code_root}/scripts/score_maturation.py" \\
            ${scientificContractArg} "\${comparisonArg[@]}" \\
            --original_pdb "${original_pdb}" \\
            --matured_pdb "\$matured_pdb" \\
            --antibody_chains "${antibodyChains}" \\
            --antigen_chains "${antigenChains}" \\
            --distance_cutoff ${distanceCutoff} \\
            --epitope_residues "${params.epitope_residues ?: ''}" \\
            --selected_positions "\$(tr -d '\\n' < "${ppiflow_positions}")" \\
            --cdr_positions_by_loop_json "${cdr_positions_by_loop_json}" \\
            --objective_mode "${objectiveMode}" \\
            --output "scores/\${base_name}_maturation_score.json"
    done
    """
}

process ScorePartialFlowImprovement {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "scores/*_partial_flow_score.json", saveAs: { fn -> fn.replace('scores/', '') }

    input:
    tuple val(meta), path(original_pdb), path(matured_pdbs), path(ppiflow_positions), path(cdr_positions_by_loop_json)

    output:
    tuple val(meta), path("scores/*_partial_flow_score.json"), emit: scores

    script:
    def scientificContractArg = maturationScientificContractArg(params)
    def frameworkType = params.get('framework_type')
    def defaultAntibodyChains = frameworkType == 'nanobody' ? 'H' : 'H,L'
    def antibodyChains = params.antibody_chains ?: (scientificContractArg ? '' : defaultAntibodyChains)
    def antigenChains = params.antigen_chains ?: ''
    def distanceCutoff = paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0)
    def objectiveMode = paramValueOrDefault(params, 'ppiflow_objective_mode', 'selected_interface')
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    mkdir -p scores
    for matured_pdb in ${matured_pdbs}; do
        base_name=\$(basename "\$matured_pdb" .pdb)
        case "\$matured_pdb" in *.pdb) ;; *) continue ;; esac
        comparisonArg=()
        if [ "${params.get('core_protein_scientific_contract') ?: ''}" = "1" ] && [ -f "\$matured_pdb.comparison.json" ]; then
            comparisonArg=(--comparison-request "\$matured_pdb.comparison.json")
        fi
        "\${PYTHON_BIN}" "${params.code_root}/scripts/score_maturation.py" \\
            ${scientificContractArg} "\${comparisonArg[@]}" \\
            --original_pdb "${original_pdb}" \\
            --matured_pdb "\$matured_pdb" \\
            --antibody_chains "${antibodyChains}" \\
            --antigen_chains "${antigenChains}" \\
            --distance_cutoff ${distanceCutoff} \\
            --epitope_residues "${params.epitope_residues ?: ''}" \\
            --selected_positions "\$(tr -d '\\n' < "${ppiflow_positions}")" \\
            --cdr_positions_by_loop_json "${cdr_positions_by_loop_json}" \\
            --objective_mode "${objectiveMode}" \\
            --output "scores/\${base_name}_partial_flow_score.json"
    done
    """
}

process FilterByMaturation {
    label 'process_low'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "filtered_output/*.pdb", saveAs: { fn -> fn.replace('filtered_output/', '') }
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "filter_reports/*_maturation_filter.json", saveAs: { fn -> fn.replace('filter_reports/', '') }

    input:
    tuple val(meta), path(matured_pdbs), path(score_jsons)

    output:
    tuple val(meta), path("filtered_output/*.pdb"), emit: pdbs, optional: true
    path ("filter_reports/*_maturation_filter.json"), emit: filter_reports

    script:
    def scientificContractArg = maturationScientificContractArg(params)
    def rawMinImprovement = paramValueOrDefault(params, 'maturation_min_improvement', null)
    def minImprovement = rawMinImprovement != null ? rawMinImprovement as Double : null
    def percentile = paramValueOrDefault(params, 'maturation_filter_percentile', null)
    def objectiveMode = paramValueOrDefault(params, 'ppiflow_objective_mode', 'selected_interface').toString()
    def rawObjectiveThreshold = paramValueOrDefault(params, 'ppiflow_objective_threshold', null)
    def objectiveThreshold = rawObjectiveThreshold != null ? rawObjectiveThreshold as Double : null
    def objectiveThresholdActive = objectiveMode != 'selected_interface' && objectiveThreshold != null
    def filterDisabled = !objectiveThresholdActive && (minImprovement == null || minImprovement >= 0.0) && !(percentile != null && percentile > 0)
    def minImprovementArg = (!filterDisabled && minImprovement != null) ? "--min_improvement ${minImprovement}" : ""
    def percentileArg = (!filterDisabled && percentile != null && percentile > 0) ? "--percentile ${percentile}" : ""
    def objectiveThresholdArg = (objectiveThreshold != null) ? "--objective_threshold ${objectiveThreshold}" : ""
    def disableFilterArg = filterDisabled ? "--disable_filter" : ""
    """
    PYTHON_BIN=\$(command -v python3 || command -v python)
    [ -n "\${PYTHON_BIN}" ] || { echo "[PPIFlow] ERROR: python interpreter not found" >&2; exit 127; }

    mkdir -p filtered_output filter_reports

    "\${PYTHON_BIN}" -c '
import json
from pathlib import Path
scores = []
for p in Path(".").glob("*.json"):
    if "score.json" in p.name:
        with open(p) as f:
            data = json.load(f)
            scores.append(data)
with open("scores_manifest.json", "w") as f:
    json.dump(scores, f)
'

    for matured_pdb in ${matured_pdbs}; do
        base_name=\$(basename "\$matured_pdb" .pdb)
        
        score_json=""
        for candidate in "\${base_name}_maturation_score.json" "\${base_name}_partial_flow_score.json"; do
            if [ -f "\${candidate}" ]; then
                score_json="\${candidate}"
                break
            fi
        done

        if [ -z "\${score_json}" ]; then
            score_json=\$(find . -maxdepth 1 -type f -name "\${base_name}_*_score.json" | head -1)
        fi

        if [ -z "\${score_json}" ]; then
            echo "[PPIFlow] ERROR: No score JSON found for \${base_name}" >&2
            exit 1
        fi

        "\${PYTHON_BIN}" "${params.code_root}/scripts/filter_maturation.py" \\
            ${scientificContractArg} \\
            --score_json "\${score_json}" \\
            --pdb_path "\$matured_pdb" \\
            --output_dir "filtered_output" \\
            --objective_mode "${objectiveMode}" \\
            ${objectiveThresholdArg} \\
            ${minImprovementArg} \\
            ${percentileArg} \\
            ${disableFilterArg} \\
            --scores_manifest "scores_manifest.json" \\
            --report_json "filter_reports/\${base_name}_maturation_filter.json"
    done
    """
}
