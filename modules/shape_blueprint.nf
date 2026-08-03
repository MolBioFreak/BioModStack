nextflow.enable.dsl = 2

process ValidateShapeBundle {
    label 'process_low'
    stageInMode 'copy'

    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'shape_input_receipt.json'

    input:
    path request_json
    path geometry_manifest
    path vertices_f64
    path faces_u32
    path points_f32le
    path sdf_f32le

    output:
    path 'shape_input_receipt.json', emit: receipt

    script:
    """
    python3 ${params.code_root}/scripts/shape_blueprint/validate_bundle.py \\
        --request ${request_json} \\
        --manifest ${geometry_manifest} \\
        --vertices ${vertices_f64} \\
        --faces ${faces_u32} \\
        --points ${points_f32le} \\
        --sdf ${sdf_f32le} \\
        --output shape_input_receipt.json
    """
}

process PlanRFD3Batches {
    label 'ShapePlanning'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_batches", mode: 'copy', pattern: 'rfd3_batch_plan.json'

    input:
    path request_json

    output:
    path 'rfd3_batch_plan.json', emit: plan
    path 'batch_requests/*.json', emit: batch_requests

    script:
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/shape_blueprint/plan_rfd3_batches.py \\
        --request ${request_json} \\
        --output-dir . \\
        --gpu-memory-gib ${params.shape_gpu_memory_gib ?: 32}
    """
}

process RunShapeRFD3 {
    label 'ShapeRFD3'
    label 'gpu'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_rfd3", mode: 'copy', pattern: '*.json'
    publishDir "${params.out_dir}/run/shape_rfd3", mode: 'copy', pattern: 'rfd3_results/shape_guidance_steps.jsonl', saveAs: { 'shape_guidance_steps.jsonl' }
    publishDir "${params.out_dir}/run/shape_rfd3", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/run/shape_rfd3/native", mode: 'copy', pattern: 'rfd3_results/*.cif.gz'
    publishDir "${params.out_dir}/run/shape_rfd3/native", mode: 'copy', pattern: 'rfd3_results/*.json'

    input:
    path request_json
    path geometry_manifest
    path points_f32le
    path sdf_f32le
    path validation_receipt

    output:
    path 'rfd3_results/*.cif.gz', emit: structures
    tuple path('rfd3_results/*.cif.gz'), path('rfd3_results/*.json'), emit: structures_metadata
    path 'shape_rfd3_runtime_receipt.json', emit: runtime_receipt
    path 'rfd3_results/shape_guidance_steps.jsonl', emit: guidance_receipt
    path 'shape_rfd3_input.json', emit: input_spec
    path 'shape_rfd3.log', emit: log

    script:
    """
    python3 ${params.code_root}/scripts/shape_blueprint/run_shape_rfd3.py \
        --request ${request_json} \
        --manifest ${geometry_manifest} \
        --points ${points_f32le} \
        --sdf ${sdf_f32le} \
        --num-timesteps ${params.shape_rfd3_num_timesteps ?: 200} \
        --output-dir rfd3_results \
        --receipt shape_rfd3_runtime_receipt.json \
        > shape_rfd3.log 2>&1
    """
}

process AdmitRFD3InitialCandidate {
    tag "${candidate.simpleName}"
    label 'ShapeAdmission'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_initial_admission", mode: 'copy'

    input:
    path candidate
    path request_json
    path geometry_manifest
    path points_f32le
    path sdf_f32le

    output:
    tuple path(candidate), path('initial_admission.json'), emit: admitted

    script:
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/shape_blueprint/evaluate_rfd3_initial_candidate.py \\
        --candidate ${candidate} \\
        --request ${request_json} \\
        --manifest ${geometry_manifest} \\
        --points ${points_f32le} \\
        --sdf ${sdf_f32le} \\
        --output initial_admission.json
    """
}

process BuildRFD3Aggregate {
    label 'ShapeAdmission'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_batches", mode: 'copy', pattern: 'rfd3_aggregate_manifest.json'

    input:
    path batch_plan
    path admission_files, arity: '1..*'

    output:
    path 'rfd3_aggregate_manifest.json', emit: aggregate

    script:
    def admissionDir = 'initial_admission_records'
    def admissionArgs = admission_files.collect { file -> "${file}" }.join(' ')
    """
    set -euo pipefail
    mkdir -p ${admissionDir}
    cp ${admissionArgs} ${admissionDir}/
    python3 ${params.code_root}/scripts/shape_blueprint/build_rfd3_aggregate.py \\
        --plan ${batch_plan} \\
        --admission-dir ${admissionDir} \\
        --output rfd3_aggregate_manifest.json
    """
}

process RunShapeProteinMPNN {
    tag "${backbone.simpleName}"
    label 'MPNN'
    label 'gpu_light'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_sequences/proteinmpnn", mode: 'copy'

    input:
    path backbone
    val sequence_count
    val seed

    output:
    path "${backbone.simpleName}_proteinmpnn", emit: bundle

    script:
    def bundle = "${backbone.simpleName}_proteinmpnn"
    """
    set -euo pipefail
    eval "\$(/bin/micromamba shell hook --shell bash)"
    micromamba activate mpnn
    python ${params.code_root}/scripts/shape_blueprint/run_shape_sequence.py \\
        --engine proteinmpnn \\
        --backbone ${backbone} \\
        --output-dir ${bundle} \\
        --receipt ${bundle}/runtime_receipt.json \\
        --count ${sequence_count} \\
        --seed ${seed}
    """
}

process RunShapeFAMPNN {
    tag "${backbone.simpleName}"
    label 'FAMPNN'
    label 'gpu_light'
    stageInMode 'copy'

    publishDir "${params.out_dir}/run/shape_sequences/fampnn", mode: 'copy'

    input:
    path backbone
    val sequence_count
    val seed

    output:
    path "${backbone.simpleName}_fampnn", emit: bundle

    script:
    def bundle = "${backbone.simpleName}_fampnn"
    """
    set -euo pipefail
    /opt/venv/bin/python ${params.code_root}/scripts/shape_blueprint/run_shape_sequence.py \\
        --engine fampnn \\
        --backbone ${backbone} \\
        --output-dir ${bundle} \\
        --receipt ${bundle}/runtime_receipt.json \\
        --count ${sequence_count} \\
        --seed ${seed}
    """
}

process EvaluateShapeCandidate {
    tag "${sequence_name}"
    label 'ShapeEvaluate'
    stageInMode 'copy'

    input:
    tuple val(sequence_name), path(structure), path(esm_metrics), path(source_backbone)
    path request_json
    path geometry_manifest
    path point_pool
    path sdf_grid

    output:
    path "shape_candidate_bundle_${task.index}", emit: bundle

    script:
    def bundle = "shape_candidate_bundle_${task.index}"
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/shape_blueprint/evaluate_shape_candidate.py \\
        --sequence-name '${sequence_name}' \\
        --source-backbone ${source_backbone} \\
        --structure ${structure} \\
        --esm-metrics ${esm_metrics} \\
        --request ${request_json} \\
        --geometry-manifest ${geometry_manifest} \\
        --points ${point_pool} \\
        --sdf ${sdf_grid} \\
        --output-dir ${bundle}
    """
}

process BuildShapeResult {
    label 'ShapeEvaluate'
    stageInMode 'copy'

    publishDir "${params.out_dir}", mode: 'copy', saveAs: { published ->
        published.startsWith('final_shape_result/') ? published.substring('final_shape_result/'.length()) : published
    }

    input:
    path candidate_bundles, arity: '0..*'
    path request_json
    val job_id

    output:
    path 'final_shape_result/results', emit: results

    script:
    def bundleArgs = candidate_bundles.collect { bundle -> "--candidate-bundle ${bundle}" }.join(' ')
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/shape_blueprint/build_shape_result.py \\
        --job-id '${job_id}' \\
        --request ${request_json} \\
        ${bundleArgs} \\
        --output-dir final_shape_result
    """
}
