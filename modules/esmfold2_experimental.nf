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

    publishDir "${params.out_dir}/final/esmfold2", mode: 'copy', pattern: 'esmfold2_results/*.cif'
    publishDir "${params.out_dir}/final/esmfold2", mode: 'copy', pattern: 'esmfold2_results/*.json'
    publishDir "${params.out_dir}/final/esmfold2", mode: 'copy', pattern: 'esmfold2_results/*.tsv'
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'esmfold2_results/*.cif'

    input:
    tuple val(sequence), val(sequence_name)

    output:
    path 'esmfold2_results/*.cif', emit: cifs
    path "esmfold2_results/*.metrics.json", emit: metrics
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
    def modelVariant = params.esmf_model_variant ?: params.model_variant ?: 'fast'
    def modelIdOrPath = params.esmf_model_id_or_path ?: params.model_id_or_path ?: ''
    def localFilesOnly = boolString(params.esmf_local_files_only ?: params.local_files_only, true)
    def numLoops = params.esmf_num_loops ?: params.num_loops ?: 1
    def numSamplingSteps = params.esmf_num_sampling_steps ?: params.sampling_steps ?: 5
    def numDiffusionSamples = params.esmf_num_diffusion_samples ?: params.num_diffusion_samples ?: 1
    def seed = params.esmf_seed ?: params.seed
    def device = params.esmf_device ?: params.device ?: 'cuda'
    def complexComponents = params.esmf_complex_components ?: params.complex_components ?: []
    def normalizedName = (sequence_name ?: 'esmfold2_prediction').toString().replaceAll(/[^A-Za-z0-9._-]+/, '_')
    def sequenceArg = shellQuote(sequence?.toString()?.trim() ?: '')
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
