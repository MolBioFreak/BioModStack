nextflow.enable.dsl = 2

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

def boolString(value) {
    return value?.toString()?.toLowerCase() in ['true', '1', 'yes', 'y', 'on'] ? 'true' : 'false'
}

process PrepConforNetsRequest {
    label 'local_cpu'

    publishDir "${params.out_dir}/inputs/confornets", mode: 'copy', pattern: 'confornets_request.json'
    publishDir "${params.out_dir}/inputs/confornets/assets", mode: 'copy', pattern: 'confornets_assets/**/*', saveAs: { filename -> filename.replace('confornets_assets/', '') }
    publishDir "${params.out_dir}/run/confornets", mode: 'copy', pattern: '*.log'

    output:
    path 'confornets_request.json', emit: request
    path 'confornets_assets', emit: assets_dir
    path '*.log'

    script:
    def jobId = shellQuote(params.get('job_id', ''))
    def jobName = shellQuote(params.get('batch_name', 'confornets_experimental'))
    def task = shellQuote(params.cn_task ?: 'diversity')
    def sequence = shellQuote(params.cn_sequence)
    def chainId = shellQuote(params.cn_chain_id ?: 'A')
    def benchmarkName = shellQuote(params.cn_benchmark_name ?: 'bms_confornets')
    def testCaseName = shellQuote(params.cn_test_case_name ?: 'monomer_case')
    def refPdb1 = shellQuote(params.cn_reference_pdb_1 ?: '')
    def refName1 = shellQuote(params.cn_reference_name_1 ?: 'ref_a')
    def refPdb2 = shellQuote(params.cn_reference_pdb_2 ?: '')
    def refName2 = shellQuote(params.cn_reference_name_2 ?: 'ref_b')
    def checkpointPath = shellQuote(params.cn_checkpoint_path ?: '')
    def configYaml = shellQuote(params.cn_config_yaml ?: '')
    def confornetsRepoPath = shellQuote(params.cn_confornets_repo_path ?: '')
    def skipMsa = shellQuote(boolString(params.cn_skip_msa))
    def computeConfidence = shellQuote(boolString(params.cn_compute_confidence))
    def saveFullConfidence = shellQuote(boolString(params.cn_save_full_confidence))
    def computeEvaluation = shellQuote(boolString(params.cn_compute_evaluation == null ? true : params.cn_compute_evaluation))
    def confornetPath = shellQuote(params.cn_confornet_path ?: '')
    def mseDir = shellQuote(params.cn_mse_dir ?: '')
    def sourceTestCases = shellQuote(params.cn_source_test_cases ?: '')
    def numRuns = params.cn_num_runs ?: 2
    def kConfornets = params.cn_k_confornets ?: 2
    def numSamples = params.cn_num_samples ?: 5
    def maxSteps = params.cn_max_steps ?: 21
    def saveSteps = shellQuote(params.cn_save_steps ?: '5,10,15,20')
    def numRecycles = params.cn_num_recycles ?: 0
    def numDiffusionSteps = params.cn_num_diffusion_steps ?: 200
    def lr = params.cn_lr ?: 0.001
    def gradClip = params.cn_grad_clip ?: 10.0
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/prep_confornets_request.py \
        --job-id ${jobId} \
        --job-name ${jobName} \
        --task ${task} \
        --sequence ${sequence} \
        --chain-id ${chainId} \
        --benchmark-name ${benchmarkName} \
        --test-case-name ${testCaseName} \
        --reference-pdb-1 ${refPdb1} \
        --reference-name-1 ${refName1} \
        --reference-pdb-2 ${refPdb2} \
        --reference-name-2 ${refName2} \
        --checkpoint-path ${checkpointPath} \
        --config-yaml ${configYaml} \
        --confornets-repo-path ${confornetsRepoPath} \
        --skip-msa ${skipMsa} \
        --num-runs ${numRuns} \
        --k-confornets ${kConfornets} \
        --num-samples ${numSamples} \
        --max-steps ${maxSteps} \
        --save-steps ${saveSteps} \
        --num-recycles ${numRecycles} \
        --num-diffusion-steps ${numDiffusionSteps} \
        --lr ${lr} \
        --grad-clip ${gradClip} \
        --compute-confidence ${computeConfidence} \
        --save-full-confidence ${saveFullConfidence} \
        --compute-evaluation ${computeEvaluation} \
        --confornet-path ${confornetPath} \
        --mse-dir ${mseDir} \
        --source-test-cases ${sourceTestCases} \
        --assets-dir confornets_assets \
        --output confornets_request.json \
        2>&1 | tee prep_confornets_request.log
    """
}

process RunConforNets {
    label 'ConforNets'
    label 'gpu'

    publishDir "${params.out_dir}/run/confornets", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/raw/confornets", mode: 'copy', pattern: 'confornets_results/raw/**/*', saveAs: { filename -> filename.replace('confornets_results/raw/', '') }
    publishDir "${params.out_dir}/processed/confornets", mode: 'copy', pattern: 'confornets_results/**/*', saveAs: { filename -> filename.replace('confornets_results/', '') }

    input:
    path request_json
    path assets_dir

    output:
    path 'confornets_results', emit: results_dir
    path '*.log'

    script:
    def requestPath = shellQuote(request_json.toString())
    def assetsPath = shellQuote(assets_dir.toString())
    """
    set -euo pipefail
    python3 /scripts/run_confornets_inference.py \
        --request ${requestPath} \
        --assets-dir ${assetsPath} \
        --output-dir confornets_results \
        2>&1 | tee run_confornets.log
    """
}

process FinalizeConforNetsOutputs {
    label 'ConforNets'

    publishDir "${params.out_dir}/final/confornets", mode: 'copy', pattern: 'final_confornets_results/**/*', saveAs: { filename -> filename.replace('final_confornets_results/', '') }
    publishDir "${params.out_dir}/run/confornets", mode: 'copy', pattern: '*.log'

    input:
    path results_dir

    output:
    path 'final_confornets_results', emit: results_dir
    path 'final_confornets_results/conformers/*.cif', emit: cifs, optional: true
    path 'final_confornets_results/**/*.json', emit: jsons, optional: true
    path 'final_confornets_results/**/*.csv', emit: csvs, optional: true
    path 'final_confornets_results/**/*.pt', emit: states, optional: true
    path '*.log'

    script:
    def resultsPath = shellQuote(results_dir.toString())
    def finalPublishPath = shellQuote("${params.out_dir}/final/confornets")
    """
    set -euo pipefail
    export RESULTS_DIR=${resultsPath}
    export FINAL_PUBLISH_DIR=${finalPublishPath}
    python3 - <<'PY' 2>&1 | tee finalize_confornets.log
import json
import os
import shutil
from pathlib import Path

source = Path(os.environ['RESULTS_DIR'])
dest = Path('final_confornets_results')
publish_dest = Path(os.environ['FINAL_PUBLISH_DIR'])
manifest_path = source / 'artifact_manifest.json'
samples_path = source / 'samples.json'
if not manifest_path.exists():
    raise SystemExit(f'Missing ConforNets artifact manifest: {manifest_path}')
if not samples_path.exists():
    raise SystemExit(f'Missing ConforNets samples manifest: {samples_path}')
samples = json.loads(samples_path.read_text(encoding='utf-8'))
if not samples:
    raise SystemExit('ConforNets produced no CIF samples; refusing to publish empty results')
for sample in samples:
    sample_path = source / sample['relative_path']
    if not sample_path.exists():
        raise SystemExit(f'Missing normalized sample listed in manifest: {sample_path}')
if dest.exists():
    shutil.rmtree(dest)
shutil.copytree(source, dest)
publish_dest.parent.mkdir(parents=True, exist_ok=True)
if publish_dest.exists():
    shutil.rmtree(publish_dest)
shutil.copytree(dest, publish_dest)
print(f'Finalized {len(samples)} ConforNets samples into {dest}')
print(f'Published finalized ConforNets samples to {publish_dest}')
PY
    """
}
