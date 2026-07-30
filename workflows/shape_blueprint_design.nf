nextflow.enable.dsl = 2

include { ValidateShapeBundle } from '../modules/shape_blueprint'

workflow {
    if (!params.shape_request_path) {
        error "--shape_request_path is required"
    }
    if (!params.shape_geometry_manifest_path) {
        error "--shape_geometry_manifest_path is required"
    }
    if (!params.shape_vertices_path || !params.shape_faces_path || !params.shape_points_path) {
        error "canonical Shape vertices, faces, and points are required"
    }

    ValidateShapeBundle(
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_vertices_path, checkIfExists: true),
        file(params.shape_faces_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
    )
}
