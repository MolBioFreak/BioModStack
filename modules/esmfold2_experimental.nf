nextflow.enable.dsl = 2


def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}


def boolString(value) {
    return value?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'
}


// Internal channel-driven ESMFold2 predictor. The historical filename is retained
// only because active parent workflows import this implementation path.
process ESMFold2Predict {
    tag "${sequence_name}"
    label 'ESMFold2'
    label 'gpu'

    publishDir "${params.out_dir}/final/esmfold2/${sequence_name}", mode: 'copy', pattern: 'esmfold2_results/*.cif'
    publishDir "${params.out_dir}/final/esmfold2/${sequence_name}", mode: 'copy', pattern: 'esmfold2_results/*.json'
    publishDir "${params.out_dir}/final/esmfold2/${sequence_name}", mode: 'copy', pattern: 'esmfold2_results/*.tsv'
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'esmfold2_results/*.cif'

    input:
    tuple val(sequence), val(sequence_name)

    output:
    path 'esmfold2_results/*.cif', emit: cifs
    path "esmfold2_results/*.metrics.json", emit: metrics
    tuple val(sequence_name), path('esmfold2_results/*.cif'), path('esmfold2_results/*.metrics.json'), emit: shape_result
    path "esmfold2_results/*.telemetry.json", emit: telemetry
    path "esmfold2_results/manifest.json", emit: manifest
    path "esmfold2_results/summary.tsv", emit: summary

    script:
    def boolString = { value, fallback ->
        def normalized = value == null ? fallback : value
        def enabled = normalized instanceof Boolean ? normalized : normalized.toString().trim().toBoolean()
        return enabled ? 'true' : 'false'
    }
    def shellQuote = { value ->
        return "'" + (value == null ? '' : value.toString()).replace("'", "'\"'\"'") + "'"
    }
    def shapeMode = params.modification_mode == 'shape_blueprint'
    def modelVariant = shapeMode ? (params.get('shape_esmf_model_variant') ?: 'fast') : (params.get('esmf_model_variant') ?: params.get('model_variant') ?: 'fast')
    def modelIdOrPath = params.get('esmf_model_id_or_path') ?: params.get('model_id_or_path') ?: ''
    def localFilesOnly = boolString(params.get('esmf_local_files_only') ?: params.get('local_files_only'), true)
    def numLoops = shapeMode ? (params.get('shape_esmf_num_loops') ?: 3) : (params.get('esmf_num_loops') ?: params.get('num_loops') ?: 1)
    def numSamplingSteps = shapeMode ? (params.get('shape_esmf_num_sampling_steps') ?: 50) : (params.get('esmf_num_sampling_steps') ?: params.get('sampling_steps') ?: 5)
    def numDiffusionSamples = shapeMode ? 1 : (params.get('esmf_num_diffusion_samples') ?: params.get('num_diffusion_samples') ?: 1)
    def seed = shapeMode ? params.get('shape_seed') : (params.get('esmf_seed') ?: params.get('seed'))
    def device = params.get('esmf_device') ?: params.get('device') ?: 'cuda'
    def complexComponents = params.get('esmf_complex_components') ?: params.get('complex_components') ?: []
    def complexComponentsFile = params.get('esmf_complex_components_file')
    if (!(complexComponents instanceof Collection && !complexComponents.isEmpty()) && complexComponentsFile) {
        def parsedComplexComponents = new groovy.json.JsonSlurper().parse(new File(complexComponentsFile.toString()))
        complexComponents = parsedComplexComponents instanceof Map ? parsedComplexComponents.components : parsedComplexComponents
    }
    def normalizedName = (sequence_name ?: 'esmfold2_prediction').toString().replaceAll(/[^A-Za-z0-9._-]+/, '_')
    def hasComplexComponents = complexComponents instanceof Collection && !complexComponents.isEmpty()
    def sequenceArg = shellQuote(hasComplexComponents ? '' : (sequence?.toString()?.trim() ?: ''))
    def complexArg = complexComponents instanceof Collection && !complexComponents.isEmpty() ? shellQuote(groovy.json.JsonOutput.toJson(complexComponents)) : "''"
    def seedArg = seed == null || seed.toString().trim().isEmpty() ? '' : "--seed ${seed}"
    def telemetryPath = shellQuote("esmfold2_results/${normalizedName}.telemetry.json")

    """
    set -euo pipefail
    python3 /scripts/bms_gpu_run_telemetry.py \
        --label ESMFold2Predict \
        --output-json ${telemetryPath} \
        -- python3 /scripts/run_esmfold2_inference.py \
        --sequence ${sequenceArg} \
        --sequence-name ${shellQuote(normalizedName)} \
        --chain-id 'A' \
        --complex-components-json ${complexArg} \
        --model-variant ${shellQuote(modelVariant)} \
        --model-id-or-path ${shellQuote(modelIdOrPath)} \
        --local-files-only ${localFilesOnly} \
        --num-loops ${numLoops} \
        --num-sampling-steps ${numSamplingSteps} \
        --num-diffusion-samples ${numDiffusionSamples} \
        ${seedArg} \
        --device ${shellQuote(device)} \
        --output-dir esmfold2_results \
        2>&1 | tee run_esmfold2.log
    """
}
