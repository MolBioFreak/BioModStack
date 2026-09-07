nextflow.enable.dsl = 2


def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}


def boolString(value) {
    return value?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'
}


def esmfold2ContractInputs(inputs, settings) {
    def marker = settings.get('core_protein_scientific_contract')
    if (!(marker instanceof Integer) || marker != 1) {
        throw new IllegalArgumentException('core_protein_scientific_contract must be integer 1')
    }
    def request = new LinkedHashMap(settings as Map)
    def componentsFile = request.get('esmf_complex_components_file')
    if (componentsFile) {
        def parsed = new groovy.json.JsonSlurper().parse(new File(componentsFile.toString()))
        request.esmf_complex_components = parsed instanceof Map ? parsed.components : parsed
        request.remove('esmf_complex_components_file')
    }
    def components = request.get('esmf_complex_components') ?: request.get('complex_components') ?: []
    def rawComponents = request.get('esmf_complex_components_json') ?: request.get('complex_components_json')
    if (rawComponents) {
        if (components) throw new IllegalArgumentException('conflicting component sources')
        components = new groovy.json.JsonSlurper().parseText(rawComponents.toString())
        request.esmf_complex_components = components
    }
    def sources = []
    ['msa_path', 'pdb_sequence_path'].each { key ->
        def a = request.get(key)
        def b = request.get('esmf_' + key)
        if (a != null && b != null && a != b) throw new IllegalArgumentException("conflicting aliases: ${key}")
        if (a ?: b) sources << (a ?: b).toString()
    }
    components.each { component -> if (component.msa_path) sources << component.msa_path.toString() }
    sources = sources.unique()
    def files = sources.collect { source -> file(source, checkIfExists: true) }
    return inputs.map { meta, sequence, name ->
        def payload = new LinkedHashMap(request)
        if (!components) payload.esmf_sequence = sequence.toString()
        payload.esmf_sequence_name = name.toString()
        tuple(meta, payload, sources, files)
    }
}


// Future-only contract lane; legacy callers retain their existing arity/defaults.
process ESMFold2MSAPredict {
    tag "${producer_meta.id}"
    label 'ESMFold2'
    label 'gpu'
    stageInMode 'copy'
    publishDir "${params.out_dir}/final/esmfold2/${request.esmf_sequence_name}", mode: 'copy', pattern: 'esmfold2_results/*'
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'esmfold2_results/*.cif'
    input:
    tuple val(producer_meta), val(request), val(source_paths), path(staged_files, stageAs: 'inputs/input??/*')
    output:
    tuple val(producer_meta), path('esmfold2_results/*.cif'), emit: typed_cifs
    path 'esmfold2_results/*.metrics.json', emit: metrics
    path 'esmfold2_results/*.telemetry.json', emit: telemetry
    path 'esmfold2_results/manifest.json', emit: manifest
    path 'esmfold2_results/summary.tsv', emit: summary
    path 'esmfold2_results/effective_settings.json', emit: effective_settings
    script:
    def files = staged_files instanceof Collection ? staged_files : [staged_files]
    def mapping = [:]
    source_paths.eachWithIndex { source, index -> mapping[source] = files[index].toString() }
    def root = task.ext.scripts_root ?: '/scripts'
    def payload = groovy.json.JsonOutput.toJson([request: request, staged_paths: mapping]).bytes.encodeBase64().toString()
    """
    set -euo pipefail
    python3 - <<'WP06PY'
import base64, json, os, sys, subprocess
from pathlib import Path
sys.path.insert(0, ${groovy.json.JsonOutput.toJson(root)})
from run_esmfold2_inference import compile_workflow_request
payload = json.loads(base64.b64decode('${payload}'))
argv, receipt = compile_workflow_request(payload['request'], payload['staged_paths'])
Path('esmfold2_results').mkdir(exist_ok=True)
Path('esmfold2_results/effective_settings.json').write_text(json.dumps(receipt, allow_nan=False, sort_keys=True))
subprocess.run(['python3', ${groovy.json.JsonOutput.toJson(root + '/bms_gpu_run_telemetry.py')},
    '--label', 'ESMFold2Predict', '--output-json', 'esmfold2_results/runtime.telemetry.json', '--',
    'python3', ${groovy.json.JsonOutput.toJson(root + '/run_esmfold2_inference.py')}] + argv, check=True,
    env=dict(os.environ, BMS_ESMFOLD2_EFFECTIVE_SETTINGS=str(Path('esmfold2_results/effective_settings.json').resolve())))
WP06PY
    """
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
    tuple val(producer_meta), val(sequence), val(sequence_name)

    output:
    tuple val(producer_meta), path('esmfold2_results/*.cif'), emit: typed_cifs
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


// PDB-sequence-source variant used by governed redesign validators.
process ESMFold2FromPdb {
    tag "${candidate_name}"
    label 'ESMFold2'
    label 'gpu'
    errorStrategy 'ignore'

    publishDir "${params.out_dir}/validation/esmfold2/${candidate_name}", mode: 'copy', pattern: 'esmfold2_results/*'

    input:
    tuple val(producer_meta), path(source_pdb), val(candidate_name)

    output:
    tuple val(producer_meta), val('esmfold2'), path('esmfold2_results/*.cif'), path('esmfold2_results/*.metrics.json'), emit: typed_results

    script:
    def modelVariant = params.get('esmf_model_variant') ?: params.get('model_variant') ?: 'fast'
    def modelIdOrPath = params.get('esmf_model_id_or_path') ?: params.get('model_id_or_path') ?: ''
    def localFilesOnly = boolString(params.get('esmf_local_files_only') == null ? true : params.get('esmf_local_files_only'))
    def numLoops = params.get('esmf_num_loops') ?: params.get('num_loops') ?: 1
    def numSamplingSteps = params.get('esmf_num_sampling_steps') ?: params.get('sampling_steps') ?: 5
    def numDiffusionSamples = params.get('esmf_num_diffusion_samples') ?: params.get('num_diffusion_samples') ?: 1
    def seed = params.get('esmf_seed') ?: params.get('seed')
    def seedArg = seed == null || seed.toString().trim().isEmpty() ? '' : "--seed ${seed}"
    """
    set -euo pipefail
    mkdir -p esmfold2_results
    python3 /scripts/bms_gpu_run_telemetry.py \
        --label ESMFold2FromPdb \
        --output-json esmfold2_results/${candidate_name}.telemetry.json \
        -- python3 /scripts/run_esmfold2_inference.py \
        --sequence-name ${shellQuote(candidate_name)} \
        --pdb-sequence-path ${shellQuote(source_pdb)} \
        --model-variant ${shellQuote(modelVariant)} \
        --model-id-or-path ${shellQuote(modelIdOrPath)} \
        --local-files-only ${localFilesOnly} \
        --num-loops ${numLoops} \
        --num-sampling-steps ${numSamplingSteps} \
        --num-diffusion-samples ${numDiffusionSamples} \
        ${seedArg} \
        --device ${shellQuote(params.get('esmf_device') ?: params.get('device') ?: 'cuda')} \
        --output-dir esmfold2_results \
        2>&1 | tee esmfold2_results/run_esmfold2.log
    """
}
