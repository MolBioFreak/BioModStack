process IdentifyAnchorResidues {
    label 'pyrosetta_tools'

    input:
    tuple val(meta), path(complex_pdb)

    output:
    tuple val(meta), path(complex_pdb), path("${meta.id}_anchors.json"), path("${meta.id}_cdr_positions.txt"), emit: anchor_inputs
    tuple val(meta), path("${meta.id}_interface_score.json"), emit: interface_scores

    script:
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def antigenChains = params.antigen_chains ?: ''
    def energyThreshold = params.maturation_anchor_threshold ?: -5.0
    def distanceCutoff = params.maturation_anchor_distance_cutoff ?: 8.0
    """
    python /scripts/identify_anchors.py \\
        --pdb "${complex_pdb}" \\
        --antibody_chains "${antibodyChains}" \\
        --antigen_chains "${antigenChains}" \\
        --energy_threshold ${energyThreshold} \\
        --distance_cutoff ${distanceCutoff} \\
        --output_anchors "${meta.id}_anchors.json" \\
        --output_score "${meta.id}_interface_score.json" \\
        --output_cdr_positions "${meta.id}_cdr_positions.txt"
    """
}

process RunPartialFlow {
    label 'gpu'
    container "${params.container_dir}/ppiflow.sif"
    ext.containerOptions = { params.ppiflow_weights_dir ? "--bind ${params.ppiflow_weights_dir}:/opt/ppiflow/ckpt" : "" }

    input:
    tuple val(meta), path(complex_pdb), path(anchors_json), path(cdr_positions)

    output:
    tuple val(meta), path("${meta.id}_ppiflow_backbone.pdb"), emit: backbones

    script:
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def antibodyList = antibodyChains.toString().split(',')*.trim().findAll { it }
    def heavyChain = params.ppiflow_heavy_chain ?: (antibodyList ? antibodyList[0] : 'H')
    def lightChain = params.ppiflow_light_chain ?: (antibodyList.size() > 1 ? antibodyList[1] : '')
    def antigenChain = params.ppiflow_antigen_chain ?: (params.antigen_chains ? params.antigen_chains.toString().replace(',', '') : '')
    def startT = params.ppiflow_start_t ?: 0.8
    def samplesPerTarget = params.ppiflow_samples_per_target ?: 1
    def retryLimit = params.ppiflow_retry_limit ?: 10
    def configPath = params.ppiflow_config ?: "/app/ppiflow/configs/inference_nanobody.yaml"
    def checkpointPath = params.ppiflow_checkpoint_path ?: (params.ppiflow_weights_dir ? "/opt/ppiflow/ckpt/${params.ppiflow_checkpoint ?: 'antibody'}.ckpt" : "")
    def hotspots = params.epitope_residues ?: ''
    def hotspotArg = hotspots ? "--specified_hotspots \"${hotspots}\"" : ""
    def lightChainArg = lightChain ? "--light_chain ${lightChain}" : ""
    """
    python /scripts/anchors_to_ppiflow_positions.py \\
        --anchors_json "${anchors_json}" \\
        --output fixed_positions.txt

    fixed_positions=\$(cat fixed_positions.txt | tr -d '\\n')
    cdr_positions=\$(cat "${cdr_positions}" | tr -d '\\n')

    if [ -z "${antigenChain}" ]; then
        antigenChain=\$(python - <<'PY'
from pathlib import Path

pdb_path = Path("${complex_pdb}")
chains = []
with open(pdb_path) as f:
    for line in f:
        if line.startswith("ATOM"):
            chain = line[21].strip()
            if chain and chain not in chains:
                chains.append(chain)

ab = "${heavyChain}${lightChain}"
chains = [c for c in chains if c not in list(ab)]
print("".join(chains))
PY
        )
    fi

    if [ -z "${checkpointPath}" ]; then
        echo "[PPIFlow] ERROR: No checkpoint path configured (ppiflow_checkpoint_path)." >&2
        exit 1
    fi

    ppiflow_script="/app/ppiflow/sample_antibody_partial_flow.py"
    if [ ! -f "\${ppiflow_script}" ]; then
        ppiflow_script="/app/ppiflow/sample_antibody_nanobody_partial.py"
    fi

    python "\${ppiflow_script}" \\
        --complex_pdb "${complex_pdb}" \\
        --fixed_positions "\${fixed_positions}" \\
        --cdr_position "\${cdr_positions}" \\
        --start_t ${startT} \\
        --samples_per_target ${samplesPerTarget} \\
        --output_dir "ppiflow_out" \\
        --retry_Limit ${retryLimit} \\
        --config "${configPath}" \\
        --model_weights "${checkpointPath}" \\
        --antigen_chain "\${antigenChain}" \\
        --heavy_chain "${heavyChain}" \\
        ${lightChainArg} \\
        ${hotspotArg} \\
        --name "${meta.id}"

    python - <<'PY'
from pathlib import Path
import shutil

pdbs = sorted(Path("ppiflow_out").rglob("*.pdb"))
if not pdbs:
    raise SystemExit("No PPIFlow PDB outputs found")
shutil.copy2(pdbs[0], "${meta.id}_ppiflow_backbone.pdb")
PY
    """
}

process PrepMaturationRedesign {
    label 'pyrosetta_tools'

    input:
    tuple val(meta), path(backbone_pdb), path(anchors_json), path(cdr_positions), path(cdr_positions_by_loop)

    output:
    tuple val(meta), path("fampnn_input/*.pdb"), path("fampnn.csv"), emit: prep

    script:
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def designModeRaw = params.maturation_design_mode ?: 'inherit'
    def designMode = designModeRaw == 'inherit' ? (params.antibody_design_mode ?: 'cdr_only') : designModeRaw
    def protectTetrad = params.protect_vhh_tetrad != null ? params.protect_vhh_tetrad : true
    """
    cp "${backbone_pdb}" ./input.pdb

    python /scripts/anchors_to_ppiflow_positions.py \\
        --anchors_json "${anchors_json}" \\
        --output fixed_positions.txt

    anchors_spec=\$(cat fixed_positions.txt | tr -d '\\n')

    python /scripts/prep_fampnn_designs.py \\
        --input_dir "./" \\
        --out_dir "fampnn_input"

    cdr_positions=\$(cat "${cdr_positions}" | tr -d '\\n')

    python /scripts/prep_antibody_constraints.py \\
        --input_dir "./" \\
        --out_fampnn "fampnn.csv" \\
        --out_mpnn "mpnn_fixed_chains.json" \\
        --design_mode "${designMode}" \\
        --protect_tetrad "${protectTetrad}" \\
        --antibody_chains "${antibodyChains}" \\
        --extra_fixed_positions "\${anchors_spec}" \\
        --cdr_positions "\${cdr_positions}" \\
        --cdr_positions_by_loop "${cdr_positions_by_loop}"
    """
}

process RunMaturationFAMPNN {
    label 'FAMPNN'
    label 'gpu_light'
    publishDir "${params.out_dir}/run/ppiflow", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "results/*.pdb", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "results/*.json", saveAs: { fn -> fn.replace('results/', '') }

    input:
    tuple val(meta), path(pdbs), path(csv)

    output:
    tuple val(meta), path("${meta.id}_matured.pdb"), path("${meta.id}_matured.json"), emit: redesigned

    script:
    def analysisChain = params.analysis_chain_id ?: 'all_chains'
    def temperature = params.maturation_redesign_temp ?: (params.fampnn_temperature ?: 0.1)
    def numSteps = params.maturation_redesign_steps ?: (params.fampnn_num_steps ?: 100)
    """
    mkdir -p results
    python /app/fampnn/fampnn/inference/seq_design.py \\
        batch_size=1 \\
        checkpoint_path=/app/fampnn/weights/fampnn_0_3.pt \\
        exclude_cys=${params.fampnn_exclude_cys != null ? params.fampnn_exclude_cys : true} \\
        fixed_pos_csv=${csv} \\
        num_seqs_per_pdb=1 \\
        pdb_dir="./" \\
        presort_by_length=true \\
        psce_threshold=${params.fampnn_psce_threshold ?: 0.3} \\
        temperature=${temperature} \\
        seq_only=${params.fampnn_seq_only ?: false} \\
        repack_last=${params.fampnn_repack_last ?: true} \\
        timestep_schedule.num_steps=${numSteps} \\
        out_dir="fampnn_output" \\
        ${params.fampnn_extra_config ? params.fampnn_extra_config : ''} \\
        2>&1 | tee fampnn_redesign_${task.index}.log

    for file in fampnn_output/samples/*_sample*.pdb; do
        base_name=\$(basename "\$file")
        new_name=\$(echo "\$base_name" | sed 's/sample/seq_/')
        cp "\$file" "results/\$new_name"
        break
    done

    python /scripts/analyse_fampnn.py \\
        --input_dir results \\
        --chain_id ${analysisChain} \\
        --ignore_cbeta \\
        --out_dir results

    mature_pdb=\$(ls results/*.pdb | head -n 1)
    mature_json=\$(ls results/*.json | head -n 1)
    cp "\${mature_pdb}" "${meta.id}_matured.pdb"
    cp "\${mature_json}" "${meta.id}_matured.json"

    if [ -n "${params.out_dir}" ]; then
        mkdir -p "${params.out_dir}/run/ppiflow/results"
        cp results/*.pdb "${params.out_dir}/run/ppiflow/results/" 2>/dev/null || true
        cp results/*.json "${params.out_dir}/run/ppiflow/results/" 2>/dev/null || true
    fi
    """
}

process ScoreMaturationImprovement {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*maturation_score.json"

    input:
    tuple val(meta), path(original_pdb), path(matured_pdb)

    output:
    tuple val(meta), path("${meta.id}_maturation_score.json"), emit: scores

    script:
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def antigenChains = params.antigen_chains ?: ''
    def distanceCutoff = params.maturation_anchor_distance_cutoff ?: 8.0
    """
    python /scripts/score_maturation.py \\
        --original_pdb "${original_pdb}" \\
        --matured_pdb "${matured_pdb}" \\
        --antibody_chains "${antibodyChains}" \\
        --antigen_chains "${antigenChains}" \\
        --distance_cutoff ${distanceCutoff} \\
        --output "${meta.id}_maturation_score.json"
    """
}

process ScorePartialFlowImprovement {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*partial_flow_score.json"

    input:
    tuple val(meta), path(original_pdb), path(matured_pdb)

    output:
    tuple val(meta), path("${meta.id}_partial_flow_score.json"), emit: scores

    script:
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def antigenChains = params.antigen_chains ?: ''
    def distanceCutoff = params.maturation_anchor_distance_cutoff ?: 8.0
    """
    python /scripts/score_maturation.py \\
        --original_pdb "${original_pdb}" \\
        --matured_pdb "${matured_pdb}" \\
        --antibody_chains "${antibodyChains}" \\
        --antigen_chains "${antigenChains}" \\
        --distance_cutoff ${distanceCutoff} \\
        --output "${meta.id}_partial_flow_score.json"
    """
}

process FilterByMaturation {
    label 'process_low'
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "filtered_output/*.pdb", saveAs: { fn -> fn.replace('filtered_output/', '') }
    publishDir "${params.out_dir}/run/ppiflow/results", mode: 'copy', pattern: "*maturation_filter.json"

    input:
    tuple val(meta), path(matured_pdb), path(score_json)

    output:
    tuple val(meta), path("filtered_output/*.pdb"), emit: pdbs, optional: true
    path("${meta.id}_maturation_filter.json"), emit: filter_reports

    script:
    def minImprovement = params.maturation_min_improvement ?: -1.0
    def percentile = params.maturation_filter_percentile
    def percentileArg = (percentile != null && percentile > 0) ? "--percentile ${percentile}" : ""
    """
    python /scripts/filter_maturation.py \\
        --score_json "${score_json}" \\
        --pdb_path "${matured_pdb}" \\
        --output_dir "filtered_output" \\
        --min_improvement ${minImprovement} \\
        ${percentileArg} \\
        --report_json "${meta.id}_maturation_filter.json"
    """
}
