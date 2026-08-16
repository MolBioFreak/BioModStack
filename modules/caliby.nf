def formatCalibyFilterParams(params) {
    def mappings = [
        [key: 'caliby_max_potts_energy', flag: '--max-potts-energy'],
        [key: 'caliby_min_sc_plddt', flag: '--min-sc-plddt'],
        [key: 'caliby_max_sc_rmsd', flag: '--max-sc-rmsd'],
    ]
    return mappings.collect { entry ->
        def value = params[entry.key]
        value != null ? "${entry.flag} ${value}" : ""
    }.findAll { flagValue -> flagValue }.join(' ')
}

process RunCaliby {
    label 'Caliby'
    label 'gpu'

    publishDir "${params.out_dir}/run/caliby", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/collected/caliby_raw", mode: 'copy', pattern: "results/*.pdb", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/collected/caliby_raw", mode: 'copy', pattern: "results/generator_*.json", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "results/*.pdb", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "results/generator_*.json", saveAs: { fn -> fn.replace('results/', '') }

    input:
    tuple val(meta), path(pdb_files)

    output:
    tuple path("results/*.pdb"), path("results/generator_*.json"), emit: pdbs_jsons
    path("caliby_metadata_${task.index}.jsonl"), emit: metadata
    path "*.log"

    script:
    def designMode = params.antibody_design_mode ?: 'cdr_only'
    def designLoops = params.antibody_design_loops ?: 'H1,H2,H3,L1,L2,L3'
    def protectTetrad = params.protect_vhh_tetrad != null ? params.protect_vhh_tetrad : true
    def antibodyChains = params.antibody_chains ?: 'H,L'
    def customCdrPositions = ''
    def customLoopSpec = params.get('rfantibody_design_loops_custom')
    if (customLoopSpec) {
        customCdrPositions = customLoopSpec.replace('[', '').replace(']', '')
    }
    def customCdrFlag = customCdrPositions ? "--cdr_positions \\\"${customCdrPositions}\\\"" : ""
    def extraFixedJson = params.manual_mutation_fixed_positions_json ? " \\\\\n        --extra_fixed_positions_json \\\"${params.manual_mutation_fixed_positions_json}\\\"" : ""

    """
    mkdir -p results

    python3 ${params.code_root}/scripts/prep_caliby_antibody_constraints.py \\
        --input_dir "./" \\
        --out_csv "caliby_constraints.csv" \\
        --design_mode "${designMode}" \\
        --design_loops "${designLoops}" \\
        ${customCdrFlag} \\
        --protect_tetrad "${protectTetrad}" \\
        --antibody_chains "${antibodyChains}" \\
        --lock_target_chains "${params.lock_target_chains != null ? params.lock_target_chains : true}" \\
        --lock_antibody_framework "${params.lock_antibody_framework != null ? params.lock_antibody_framework : true}" \\
        --fixed_pos_override_seq "${params.caliby_fixed_pos_override_seq ?: ''}" \\
        --pos_restrict_aatype "${params.caliby_pos_restrict_aatype ?: ''}" \\
        --symmetry_pos "${params.caliby_symmetry_pos ?: ''}"${extraFixedJson}

    python3 ${params.code_root}/scripts/run_caliby_sequence_design.py \\
        --input-dir "./" \\
        --output-dir results \\
        --model-name "${params.caliby_model_name ?: 'soluble_caliby_v1'}" \\
        --num-seqs-per-pdb ${params.seqs_per_design ?: 8} \\
        --batch-size ${params.caliby_batch_size ?: 4} \\
        --num-workers ${params.caliby_num_workers ?: 8} \\
        --clean-num-workers ${params.caliby_clean_num_workers ?: 2} \\
        --temperature ${params.caliby_temperature ?: 0.1} \\
        --omit-aas "${params.caliby_omit_aas ?: 'C'}" \\
        --pos-constraint-csv "caliby_constraints.csv" \\
        --run-self-consistency-eval "${params.caliby_run_self_consistency_eval ?: false}" \\
        --self-consistency-num-models ${params.caliby_self_consistency_num_models ?: 5} \\
        --self-consistency-num-recycles ${params.caliby_self_consistency_num_recycles ?: 3} \\
        --self-consistency-use-multimer "${params.caliby_self_consistency_use_multimer ?: false}" \\
        --sampling-overrides-json '${params.caliby_sampling_overrides_json ?: ''}' \\
        2>&1 | tee caliby_${task.index}.log

    cp results/caliby_metadata.jsonl "caliby_metadata_${task.index}.jsonl"
    """
}

process RunCalibyBinder {
    label 'Caliby'
    label 'gpu'

    publishDir "${params.out_dir}/run/caliby_binder", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/collected/binder_generation/caliby", mode: 'copy', pattern: "results/*.pdb", saveAs: { fn -> fn.replace('results/', '') }
    publishDir "${params.out_dir}/collected/binder_generation/caliby", mode: 'copy', pattern: "results/generator_*.json", saveAs: { fn -> fn.replace('results/', '') }

    input:
    path(pdb_files)

    output:
    path("results/*.pdb"), emit: pdbs
    path("results/generator_*.json"), emit: jsons
    path("caliby_metadata.jsonl"), emit: metadata
    path("caliby_binder.log"), emit: log

    script:
    """
    mkdir -p results
    python3 ${params.code_root}/scripts/prep_caliby_binder_constraints.py \\
        --input-dir ./ \\
        --out-csv caliby_constraints.csv \\
        --binder-chains "${params.binder_chains ?: 'A'}" \\
        --target-chains "${params.target_chains ?: 'B'}"

    python3 ${params.code_root}/scripts/run_caliby_sequence_design.py \\
        --input-dir ./ \\
        --output-dir results \\
        --model-name "${params.caliby_model_name ?: 'soluble_caliby_v1'}" \\
        --num-seqs-per-pdb ${params.num_sequences ?: 4} \\
        --batch-size ${params.caliby_batch_size ?: 4} \\
        --num-workers ${params.caliby_num_workers ?: 8} \\
        --clean-num-workers ${params.caliby_clean_num_workers ?: 2} \\
        --temperature ${params.caliby_temperature ?: 0.1} \\
        --omit-aas "${params.caliby_omit_aas ?: 'C'}" \\
        --pos-constraint-csv caliby_constraints.csv \\
        --run-self-consistency-eval false \\
        2>&1 | tee caliby_binder.log

    cp results/caliby_metadata.jsonl caliby_metadata.jsonl
    """
}

process FilterCaliby {
    label 'Caliby'

    publishDir "${params.out_dir}/run/filter_caliby", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/collected/caliby", mode: 'copy', pattern: 'filtered_output/*.pdb', saveAs: { fn -> fn.replace('filtered_output/', '') }
    publishDir "${params.out_dir}/collected/caliby", mode: 'copy', pattern: 'filtered_output/generator_*.json', saveAs: { fn -> fn.replace('filtered_output/', '') }

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path("filtered_output/*.pdb"), emit: pdbs, optional: true
    path("filtered_output/generator_*.json"), emit: jsons, optional: true
    path("filter_caliby_${task.index}.log"), emit: logs

    script:
    def calibyFilterParams = formatCalibyFilterParams(params)

    """
    python3 ${params.code_root}/scripts/filter_caliby.py \\
        --jsons ./ \\
        --pdbs ./ \\
        ${calibyFilterParams} \\
        --output-dir filtered_output \\
        2>&1 | tee filter_caliby_${task.index}.log
    """
}
