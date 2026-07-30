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
