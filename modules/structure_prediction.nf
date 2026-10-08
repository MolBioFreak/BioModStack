// Structure Prediction from Sequence
// Modules for predicting 3D protein structure directly from amino acid sequence
// Supported predictors: Boltz-2, RF3 (RoseTTAFold3), Protenix

include { ProtenixPredict ; ProtenixFromComplex ; PrepProtenixComplex } from './protenix.nf'
// Generate MSA using local MMseqs2 database - GPU ACCELERATED!
// Uses ColabFold database via params.msa_local_db
// Hybrid scheduling: GPU when available, falls back to CPU
process GenerateLocalMSA {
    label 'CPU'
    // Runs MMseqs2 locally against UniRef30 + ColabFoldDB
    // No internet required, no API rate limits
    // GPU-accelerated when available (~5-10 sec vs ~2-3 min CPU)
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "*.a3m"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "*_msa_quality.json"

    input:
    tuple val(sequence), val(sequence_name)

    output:
    tuple val(sequence), val(sequence_name), path("${sequence_name}.a3m"), emit: msa
    path "*_msa_quality.json", emit: quality_report, optional: true
    path "*.log"

    script:
    def dbPath = params.msa_local_db
    def cacheDir = params.msa_cache_dir
    def threads = params.msa_threads ?: 32
    def useGpu = params.msa_use_gpu != false ? "" : "--cpu-only"
    def gpuMode = params.msa_gpu_mode ?: "auto"
    def gpuThreshold = params.msa_gpu_threshold ?: 80
    def preferredGpus = params.msa_preferred_gpus ? "--preferred-gpus \"${params.msa_preferred_gpus}\"" : ""
    def excludedGpus = params.msa_excluded_gpus ? "--excluded-gpus \"${params.msa_excluded_gpus}\"" : ""
    def gpuServerMode = params.msa_gpu_server_mode ?: "persistent"
    def gpuServerWaitTimeout = params.msa_gpu_server_wait_timeout ?: 120
    def gpuServerDbLoadMode = params.msa_gpu_server_db_load_mode ?: 0
    def gpuServerStartupWait = params.msa_gpu_server_startup_wait ?: 1.0
    def msaProvider = params.msa_provider ?: "local"
    def colabfoldApiHost = params.colabfold_api_host ?: "https://api.colabfold.com"
    def colabfoldApiMinInterval = params.colabfold_api_min_interval ?: 6.0
    def colabfoldApiPollInterval = params.colabfold_api_poll_interval ?: 6.0
    def refSeq = params.msa_reference_sequence ? "--reference-sequence \"${params.msa_reference_sequence}\"" : ""
    def forceRefresh = params.msa_force_refresh ? "--force_refresh" : ""
    def cacheOnly = params.msa_cache_only ? "--cache-only" : ""
    // MSA Quality Preset (Maximum/Balanced/Fast) - default: fast (quick search)
    def msaPreset = params.msa_preset ?: "fast"
    // MSA Quality Parameters (can override preset)
    def evalue = params.msa_evalue ? "--evalue ${params.msa_evalue}" : ""
    def sensitivity = params.msa_sensitivity ? "--sensitivity ${params.msa_sensitivity}" : ""
    def maxSeqs = params.msa_max_seqs ? "--max-seqs ${params.msa_max_seqs}" : ""
    def minSeqId = params.msa_min_seq_id ? "--min-seq-id ${params.msa_min_seq_id}" : ""
    def minCoverage = params.msa_min_coverage ? "--min-coverage ${params.msa_min_coverage}" : ""
    def taxonList = params.msa_taxon_list ? "--taxon-list \"${params.msa_taxon_list}\"" : ""
    def minDepthWarning = params.msa_min_depth_warning ?: 100
    def minDepthFail = params.msa_min_depth_fail ?: 0  // 0 = warn but don't fail
    // NEW: expansion, envdb, and iteration overrides
    def useExpand = params.msa_use_expand != null ? "--use-expand ${params.msa_use_expand ? 1 : 0}" : ""
    def useEnv = params.msa_use_env != null ? "--use-env ${params.msa_use_env ? 1 : 0}" : ""
    def numIterations = params.msa_num_iterations ? "--num-iterations ${params.msa_num_iterations}" : ""
    """
    python3 ${params.code_root}/scripts/run_local_msa.py \\
        --sequence "${sequence}" \\
        --name "${sequence_name}" \\
        --out_dir . \\
        --db_path ${dbPath} \\
        --cache_dir ${cacheDir} \\
        --threads ${threads} \\
        --gpu-mode ${gpuMode} \\
        --gpu-threshold ${gpuThreshold} \\
        --gpu-server-mode ${gpuServerMode} \\
        --gpu-server-wait-timeout ${gpuServerWaitTimeout} \\
        --gpu-server-db-load-mode ${gpuServerDbLoadMode} \\
        --gpu-server-startup-wait ${gpuServerStartupWait} \\
        --msa-provider ${msaProvider} \\
        --colabfold-api-host "${colabfoldApiHost}" \\
        --colabfold-api-min-interval ${colabfoldApiMinInterval} \\
        --colabfold-api-poll-interval ${colabfoldApiPollInterval} \\
        --preset ${msaPreset} \\
        --min-depth-warning ${minDepthWarning} \\
        --min-depth-fail ${minDepthFail} \\
        ${useGpu} \\
        ${preferredGpus} \\
        ${excludedGpus} \\
        ${refSeq} \\
        ${forceRefresh} \\
        ${cacheOnly} \\
        ${evalue} \\
        ${sensitivity} \\
        ${maxSeqs} \\
        ${minSeqId} \\
        ${minCoverage} \\
        ${taxonList} \\
        ${useExpand} \\
        ${useEnv} \\
        ${numIterations} \\
        2>&1 | tee msa_${sequence_name}.log
    """
}


// Batch MSA Generation - processes multiple sequences in parallel
// Used by orchestrator for MSA batch jobs
process BatchMSAGeneration {
    label 'GPU'
    // Uses GPU for MSA generation
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "*.a3m"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa_manifest.json"

    input:
    val sequences_json
    // JSON string with array of {name, sequence} objects
    val reference_sequence

    output:
    path ("msa_manifest.json"), emit: manifest
    path ("*.a3m"), emit: msas, optional: true
    path "*.log"

    script:
    def dbPath = params.msa_local_db
    def cacheDir = params.msa_cache_dir
    def refSeqArg = reference_sequence ? "--reference_sequence '${reference_sequence}'" : ""
    def forceRefresh = params.msa_force_refresh ? "--force_refresh" : ""
    def maxSeqsArg = params.msa_max_seqs ? "--max-seqs ${params.msa_max_seqs}" : ""
    """
    python3 ${params.code_root}/scripts/batch_msa.py \\
        --sequences '${sequences_json}' \\
        --output_dir . \\
        --db_path ${dbPath} \\
        --cache_dir ${cacheDir} \\
        ${refSeqArg} \\
        ${forceRefresh} \\
        ${maxSeqsArg} \\
        2>&1 | tee batch_msa.log
    """
}

process BoltzFromSequence {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz_seq", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*.a3m"

    input:
    tuple val(sequence), val(sequence_name)

    output:
    path "predictions/*.pdb", emit: pdbs, optional: true
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/*.json", emit: jsons, optional: true
    path "msa/*.a3m", emit: msa, optional: true
    path "*.log"

    script:
    def recycling = params.boltz_recycling_steps ?: 3
    def sampling = params.boltz_sampling_steps ?: 50
    def numSamples = params.boltz_diffusion_samples ?: params.boltz_num_samples ?: 1
    def msaDbPath = params.msa_local_db
    def msaCacheDir = params.msa_cache_dir
    def msaThreads = params.msa_threads ?: 32
    def useMsa = params.boltz_use_msa == null || params.boltz_use_msa.toString() == 'true'
    def msaForceRefresh = params.msa_force_refresh ? "true" : "false"
    """
    set -o pipefail  # Propagate exit codes through pipes
    
    # Setup temp directories for containerized execution
    mkdir -p tmp yamls predictions msa
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Write sequence to FASTA for MSA generation
    echo ">${sequence_name}" > msa/${sequence_name}.fasta
    echo "${sequence}" >> msa/${sequence_name}.fasta
    
    # Generate MSA locally if enabled (NO API CALLS!)
    MSA_PATH=""
    if [ "${useMsa}" = "true" ]; then
        MMSEQS="${msaDbPath}/mmseqs/bin/mmseqs"
        UNIREF_DB="${msaDbPath}/uniref30_2302_db"
        
        echo "Generating MSA locally using mmseqs at ${msaDbPath}..."
        
        # Create query database
        \${MMSEQS} createdb msa/${sequence_name}.fasta msa/query_db
        
        # Search against UniRef30 (split memory to avoid OOM with parallel jobs)
        mkdir -p msa/tmp
        \${MMSEQS} search msa/query_db \${UNIREF_DB} msa/result_db msa/tmp \\
            --threads ${msaThreads} -s 8.0 --max-seqs 10000 -e 0.001 --split-memory-limit 32G
        
        # Convert to A3M format (use mode 2 = aligned FASTA)
        \${MMSEQS} result2msa msa/query_db \${UNIREF_DB} msa/result_db msa/${sequence_name}.a3m
        
        if [ -f "msa/${sequence_name}.a3m" ]; then
            # Strip null bytes - mmseqs adds trailing 0x00 that break Boltz parser
            tr -d '\\0' < msa/${sequence_name}.a3m > msa/${sequence_name}_clean.a3m
            mv msa/${sequence_name}_clean.a3m msa/${sequence_name}.a3m
            
            MSA_PATH=\$(readlink -f msa/${sequence_name}.a3m)
            echo "Generated local MSA: \${MSA_PATH}"
        else
            echo "WARNING: MSA generation failed, running without MSA"
        fi
    fi
    
    # Generate proper multi-chain YAML using Python
    # Handles colon-separated sequences (e.g., "VH_SEQ:VL_SEQ" -> chains A, B)
    python3 << 'PYEOF'
import yaml
from pathlib import Path
import os

sequence_input = "${sequence}"
sequence_name = "${sequence_name}"

# Check if MSA was generated
msa_path = None
msa_check = f"msa/{sequence_name}.a3m"
if Path(msa_check).exists():
    msa_path = str(Path(msa_check).resolve())
    print(f"Using MSA: {msa_path}")
else:
    # msa_path stays None - will use "empty" for single-sequence mode
    print("No MSA available - using single-sequence mode (msa: empty)")

# Split by colon for multi-chain input
chains = sequence_input.split(':')
chain_ids = [chr(ord('A') + i) for i in range(len(chains))]

# Build Boltz YAML structure
boltz_yaml = {"version": 1, "sequences": []}

for chain_id, chain_seq in zip(chain_ids, chains):
    entry = {
        "protein": {
            "id": [chain_id],
            "sequence": chain_seq.strip()
        }
    }
    # Use proper Boltz-2 API: msa path if available, otherwise "empty" for single-sequence mode
    if msa_path:
        entry["protein"]["msa"] = msa_path
    else:
        entry["protein"]["msa"] = "empty"  # Boltz-2 API for single-sequence mode
    boltz_yaml["sequences"].append(entry)

# Write YAML
yaml_path = f"yamls/{sequence_name}.yaml"
with open(yaml_path, "w") as f:
    yaml.dump(boltz_yaml, f, default_flow_style=False)

print(f"Generated Boltz YAML with {len(chains)} chain(s): {chain_ids}")
print(yaml.dump(boltz_yaml, default_flow_style=False))
PYEOF
    
    # Run Boltz-2 prediction (NO --use_msa_server - MSA is pre-computed!)
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
        ${params.boltz_predict_affinity ? '--sampling_steps_affinity ' + (params.boltz_sampling_steps_affinity ?: 200) + ' --diffusion_samples_affinity ' + (params.boltz_diffusion_samples_affinity ?: 5) : ''} \\
        ${params.boltz_affinity_mw_correction ? '--affinity_mw_correction' : ''} \\
        --cache /boltzcache \\
        ${params.boltz_extra_config ?: ''} \\
        2>&1 | tee boltz_seq_${sequence_name}.log
    
    # Move outputs to predictions directory
    for dir in boltz_results_yamls/predictions/*/; do
        # Copy all model files
        for model_file in \${dir}/*.pdb \${dir}/*.cif; do
            if [ -f "\$model_file" ]; then cp "\$model_file" predictions/; fi
        done
        # Copy confidence JSON
        for json_file in \${dir}/*.json; do
            if [ -f "\$json_file" ]; then cp "\$json_file" predictions/; fi
        done
        # Copy affinity JSONs (generated when --sampling_steps_affinity is set)
        cp "\${dir}"/affinity_*.json predictions/ 2>/dev/null || :
    done
    
    # Output validation: fail if no structure files produced
    if [ -z "\$(ls predictions/*.pdb predictions/*.cif 2>/dev/null)" ]; then
        echo "ERROR: Boltz produced no output files"
        exit 1
    fi
    """
}

// Boltz with pre-computed MSA (no rate limiting!)
process BoltzFromSequenceWithMSA {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz_seq", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.json", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(sequence), val(sequence_name), path(msa_file)

    output:
    path "predictions/*.pdb", emit: pdbs, optional: true
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/*.json", emit: jsons, optional: true
    path "*.log"

    script:
    def recycling = params.boltz_recycling_steps ?: 3
    def sampling = params.boltz_sampling_steps ?: 50
    def numSamples = params.boltz_diffusion_samples ?: params.boltz_num_samples ?: 1
    """
    set -o pipefail  # Propagate exit codes through pipes
    
    # Setup temp directories for containerized execution
    mkdir -p tmp yamls predictions msa
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # CRITICAL: Copy MSA file to local directory to resolve symlinks
    # Inside containers, symlinks pointing to host paths don't work
    MSA_LOCAL="msa/\$(basename ${msa_file})"
    cp -L "${msa_file}" "\$MSA_LOCAL" 2>/dev/null || cp "${msa_file}" "\$MSA_LOCAL" 2>/dev/null || true
    MSA_PATH="\$(readlink -f \$MSA_LOCAL 2>/dev/null || realpath \$MSA_LOCAL 2>/dev/null || echo '')"
    echo "MSA file: ${msa_file} -> \$MSA_PATH"
    
    # Generate proper multi-chain YAML using Python
    # Handles colon-separated sequences (e.g., "VH_SEQ:VL_SEQ" -> chains A, B)
    python3 << PYEOF
import yaml
from pathlib import Path
import os

sequence_input = "${sequence}"
sequence_name = "${sequence_name}"
msa_local = os.environ.get('MSA_PATH', '') or "\$MSA_PATH"

# Check local MSA from shell variable
msa_path = None
if msa_local and msa_local.strip():
    msa_check = Path(msa_local.strip())
    if msa_check.exists():
        msa_path = str(msa_check.resolve())
        print(f"Using MSA: {msa_path}")
else:
    # Fallback: check for any .a3m in msa/ directory
    msa_files = list(Path("msa").glob("*.a3m"))
    if msa_files:
        msa_path = str(msa_files[0].resolve())
        print(f"Found MSA in msa/: {msa_path}")
    else:
        print("WARNING: No MSA file found, proceeding without MSA")

# Split by colon for multi-chain input
chains = sequence_input.split(':')
chain_ids = [chr(ord('A') + i) for i in range(len(chains))]

# Build Boltz YAML structure
boltz_yaml = {"version": 1, "sequences": []}

for chain_id, chain_seq in zip(chain_ids, chains):
    entry = {
        "protein": {
            "id": [chain_id],
            "sequence": chain_seq.strip()
        }
    }
    # Apply MSA to all chains to avoid "Cannot mix custom and auto-generated MSAs" error
    # Boltz2/ColabFold style MSAs typically cover the full complex or related chains
    if msa_path:
        entry["protein"]["msa"] = msa_path
    boltz_yaml["sequences"].append(entry)

# Write YAML
yaml_path = f"yamls/{sequence_name}.yaml"
with open(yaml_path, "w") as f:
    yaml.dump(boltz_yaml, f, default_flow_style=False)

print(f"Generated Boltz YAML with {len(chains)} chain(s): {chain_ids}")
print(yaml.dump(boltz_yaml, default_flow_style=False))
PYEOF
    
    # Run Boltz-2 prediction with cached MSA (NO --use_msa_server!)
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
        ${params.boltz_predict_affinity ? '--sampling_steps_affinity ' + (params.boltz_sampling_steps_affinity ?: 200) + ' --diffusion_samples_affinity ' + (params.boltz_diffusion_samples_affinity ?: 5) : ''} \\
        ${params.boltz_affinity_mw_correction ? '--affinity_mw_correction' : ''} \\
        --cache /boltzcache \\
        ${params.boltz_extra_config ?: ''} \\
        2>&1 | tee boltz_seq_${sequence_name}.log
    
    # Move outputs to predictions directory
    for dir in boltz_results_yamls/predictions/*/; do
        for model_file in \${dir}/*.pdb \${dir}/*.cif; do
            if [ -f "\$model_file" ]; then cp "\$model_file" predictions/; fi
        done
        for json_file in \${dir}/*.json; do
            if [ -f "\$json_file" ]; then cp "\$json_file" predictions/; fi
        done
        # Copy affinity JSONs (generated when --sampling_steps_affinity is set)
        cp "\${dir}"/affinity_*.json predictions/ 2>/dev/null || :
    done
    
    # Output validation: fail if no structure files produced
    if [ -z "\$(ls predictions/*.pdb predictions/*.cif 2>/dev/null)" ]; then
        echo "ERROR: Boltz produced no output files"
        exit 1
    fi
    """
}

// Complex prep stage: generate chain-level MSAs and Boltz YAML on host/runtime CPU label.
// This decouples MSA scheduling from Boltz folding GPU assignment.
process PrepareComplexWithMSA {
    label 'CPU'
    publishDir "${params.out_dir}/run/boltz_complex", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*.a3m"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*_msa_quality.json"
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/complex_msa_manifest.json"

    input:
    tuple val(complex_name), path(complex_json), path(msa_files)

    output:
    tuple val(complex_name), path("yamls/${complex_name}.yaml"), path("msa"), emit: prepared
    path "msa/*.a3m", emit: msa, optional: true
    path "msa/*_msa_quality.json", emit: quality_report, optional: true
    path "msa/complex_msa_manifest.json", emit: msa_manifest, optional: true
    path "*.log"

    script:
    def msaDbPath = params.msa_local_db
    def msaCacheDir = params.msa_cache_dir
    def msaThreads = params.msa_threads ?: 32
    def msaUseGpuEnabled = params.msa_use_gpu != false ? "true" : "false"
    def msaForceRefresh = params.msa_force_refresh ? "true" : "false"
    def msaCacheOnly = params.msa_cache_only ? "true" : "false"
    def useMsa = params.boltz_use_msa == null || params.boltz_use_msa.toString() == 'true'
    // MSA Quality Parameters - default: fast (quick search)
    def msaPreset = params.msa_preset ?: "fast"
    def msaTaxonList = params.msa_taxon_list ?: ""
    // Keep empty by default so run_local_msa.py preset controls e-value.
    def msaEvalue = params.msa_evalue ?: ""
    def msaMaxSeqs = params.msa_max_seqs ?: ""
    def msaMinSeqId = params.msa_min_seq_id ?: ""
    def msaMinCoverage = params.msa_min_coverage ?: ""
    def msaMinDepthWarning = params.msa_min_depth_warning ?: 100
    def msaMinDepthFail = params.msa_min_depth_fail ?: 0  // 0 = warn but don't fail
    // NEW: expansion, envdb, and iteration overrides
    def msaUseExpand = params.msa_use_expand != null ? params.msa_use_expand : ""
    def msaUseEnv = params.msa_use_env != null ? params.msa_use_env : ""
    def msaNumIterations = params.msa_num_iterations ?: ""
    // GPU policy for MSA so folding workloads can retain priority
    def msaGpuMode = params.msa_gpu_mode ?: "auto"
    def msaGpuThreshold = params.msa_gpu_threshold ?: 80
    def msaPreferredGpus = params.msa_preferred_gpus ?: ""
    def msaExcludedGpus = params.msa_excluded_gpus ?: ""
    def msaGpuServerMode = params.msa_gpu_server_mode ?: "persistent"
    def msaGpuServerWaitTimeout = params.msa_gpu_server_wait_timeout ?: 120
    def msaGpuServerDbLoadMode = params.msa_gpu_server_db_load_mode ?: 0
    def msaGpuServerStartupWait = params.msa_gpu_server_startup_wait ?: 1.0
    def msaProvider = params.msa_provider ?: "local"
    def colabfoldApiHost = params.colabfold_api_host ?: "https://api.colabfold.com"
    def colabfoldApiMinInterval = params.colabfold_api_min_interval ?: 6.0
    def colabfoldApiPollInterval = params.colabfold_api_poll_interval ?: 6.0
    def msaAllowEmptyFallback = params.msa_allow_empty_fallback != null ? params.msa_allow_empty_fallback.toString() : "false"
    def anchorTarget = params.boltz_anchor_target != null ? params.boltz_anchor_target.toString() : "false"
    def fixedTargetSourcePath = params.fixed_target_source_path ?: ""
    def fixedTargetSourceChains = params.fixed_target_source_chains ?: ""
    def fixedTargetModelNumber = params.fixed_target_model_number ?: ""
    def targetChains = params.target_chains ?: ""
    // Per-chain MSA timeout in seconds for complex prep; set <=0 to disable timeout.
    def msaChainTimeoutSeconds = params.msa_chain_timeout_seconds ?: 3600
    """
    set -o pipefail
    
    mkdir -p yamls msa
    
    # Convert JSON complex definition to Boltz-2 YAML format
    # AND generate local MSA for each protein chain
    python3 << 'PYEOF' 2>&1 | tee prep_complex_${complex_name}.log
import json
import yaml
import subprocess
import os
from pathlib import Path

with open("${complex_json}") as f:
    complex_def = json.load(f)

boltz_yaml = {"version": 1, "sequences": []}
binder_chain = None

msa_db_path = "${msaDbPath}"
cache_dir = "${msaCacheDir}"
msa_threads = int("${msaThreads}")
msa_use_gpu_enabled = "${msaUseGpuEnabled}" == "true"
use_msa = "${useMsa}" == "true"
force_refresh = "${msaForceRefresh}" == "true"
cache_only = "${msaCacheOnly}" == "true"
complex_name = "${complex_name}"
# MSA Quality params
msa_preset = "${msaPreset}"
msa_taxon_list = "${msaTaxonList}"
msa_evalue = "${msaEvalue}"
msa_max_seqs = "${msaMaxSeqs}"
msa_min_seq_id = "${msaMinSeqId}"
msa_min_coverage = "${msaMinCoverage}"
msa_min_depth_warning = "${msaMinDepthWarning}"
msa_min_depth_fail = "${msaMinDepthFail}"
# NEW: expansion, envdb, iteration overrides
msa_use_expand = "${msaUseExpand}"
msa_use_env = "${msaUseEnv}"
msa_num_iterations = "${msaNumIterations}"
msa_gpu_mode = "${msaGpuMode}"
msa_gpu_threshold = int("${msaGpuThreshold}")
msa_preferred_gpus = "${msaPreferredGpus}"
msa_excluded_gpus = "${msaExcludedGpus}"
msa_gpu_server_mode = "${msaGpuServerMode}"
msa_gpu_server_wait_timeout = "${msaGpuServerWaitTimeout}"
msa_gpu_server_db_load_mode = "${msaGpuServerDbLoadMode}"
msa_gpu_server_startup_wait = "${msaGpuServerStartupWait}"
msa_provider = "${msaProvider}"
colabfold_api_host = "${colabfoldApiHost}"
colabfold_api_min_interval = "${colabfoldApiMinInterval}"
colabfold_api_poll_interval = "${colabfoldApiPollInterval}"
msa_allow_empty_fallback = "${msaAllowEmptyFallback}".strip().lower() == "true"
anchor_target = "${anchorTarget}".strip().lower() == "true"
fixed_target_source_path = "${fixedTargetSourcePath}".strip()
fixed_target_source_chains = [token.strip() for token in "${fixedTargetSourceChains}".split(",") if token.strip()]
fixed_target_model_number = "${fixedTargetModelNumber}".strip()
target_chain_ids = {token.strip() for token in "${targetChains}".split(",") if token.strip()}
target_template_threshold = float("${params.target_template_threshold_angstrom ?: 2.0}")
msa_chain_timeout_seconds = int("${msaChainTimeoutSeconds}")
code_root = Path("${params.code_root}")
msa_fallback_path = "${msa_files}"
fallback_msa = None
try:
    msa_path_obj = Path(msa_fallback_path)
    if msa_path_obj.exists() and msa_path_obj.name != "NO_MSA":
        fallback_msa = str(msa_path_obj.resolve())
except Exception:
    fallback_msa = None

msa_chain_timeout = None if msa_chain_timeout_seconds <= 0 else msa_chain_timeout_seconds

# Track sequence -> MSA path mappings for homodimer support
# Boltz-2 requires identical sequences to share the same MSA
seq_to_msa = {}
msa_failures = []
msa_records = []
template_cif_path = None

if fixed_target_source_path and not Path(fixed_target_source_path).exists():
    candidate = code_root / fixed_target_source_path
    if candidate.exists():
        fixed_target_source_path = str(candidate.resolve())

if anchor_target:
    if not fixed_target_source_path:
        raise RuntimeError("boltz_anchor_target requires fixed_target_source_path")
    if not fixed_target_source_chains:
        raise RuntimeError("boltz_anchor_target requires fixed_target_source_chains")
    if not target_chain_ids:
        raise RuntimeError("boltz_anchor_target requires target_chains")
    extract_cmd = [
        "python3",
        "${params.code_root}/scripts/extract_target_templates.py",
        "--pdb_files",
        fixed_target_source_path,
        "--target_chains",
        ",".join(fixed_target_source_chains),
        "--out_dir",
        "target_templates/mmcif",
        "--manifest",
        "target_templates/manifest.json",
    ]
    if fixed_target_model_number:
        extract_cmd.extend(["--model_number", fixed_target_model_number])
    subprocess.run(extract_cmd, check=True)
    manifest = json.loads(Path("target_templates/manifest.json").read_text())
    if not manifest:
        raise RuntimeError("Fixed-target template extraction produced an empty manifest")
    template_info = next(iter(manifest.values()))
    template_cif_path = str(Path(template_info["cif"]).resolve())
    print(f"Prepared fixed-target template: {template_cif_path}")

for comp in complex_def.get("components", []):
    comp_type = comp.get("type", "protein")
    comp_id = comp.get("id", "A")
    
    if comp_type == "protein":
        sequence = comp.get("sequence", "")
        entry = {"protein": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": sequence}}
        record = {
            "component_id": comp_id,
            "component_type": comp_type,
            "sequence_length": len(sequence),
            "msa_mode": None,
            "msa_path": None,
        }
        
        # Check for pre-existing MSA path
        msa_path = comp.get("msa_path")
        if msa_path and Path(msa_path).exists():
            resolved = str(Path(msa_path).resolve())
            entry["protein"]["msa"] = resolved
            record["msa_mode"] = "provided"
            record["msa_path"] = resolved
        elif fallback_msa:
            entry["protein"]["msa"] = fallback_msa
            record["msa_mode"] = "fallback"
            record["msa_path"] = fallback_msa
        elif use_msa and sequence:
            # Check if we've already generated MSA for this exact sequence (homodimer support)
            if sequence in seq_to_msa:
                print(f"Reusing MSA for chain {comp_id} - identical sequence already has MSA")
                entry["protein"]["msa"] = seq_to_msa[sequence]
                record["msa_mode"] = "reused"
                record["msa_path"] = seq_to_msa[sequence]
            else:
                # Generate MSA using run_local_msa.py with file-based locking to prevent parallel OOM
                chain_id = comp_id[0] if isinstance(comp_id, list) else comp_id
                msa_dir = "msa"
                msa_file = f"msa/{complex_name}_{chain_id}.a3m"
                # Optional shared reference sequence for cache key reuse (mutagenesis support)
                ref_seq = comp.get("reference_sequence") or os.environ.get("MSA_REFERENCE_SEQUENCE", "")
                
                print(f"Generating local MSA for chain {chain_id} using run_local_msa.py...")
                try:
                    cmd = [
                        "python3", "${params.code_root}/scripts/run_local_msa.py",
                        "--sequence", sequence,
                        "--name", f"{complex_name}_{chain_id}",
                        "--out_dir", msa_dir,
                        "--db_path", msa_db_path,
                        "--cache_dir", cache_dir,
                        "--threads", str(msa_threads),
                        "--preset", msa_preset,
                        "--gpu-mode", msa_gpu_mode,
                        "--gpu-threshold", str(msa_gpu_threshold),
                        "--gpu-server-mode", msa_gpu_server_mode,
                        "--gpu-server-wait-timeout", msa_gpu_server_wait_timeout,
                        "--gpu-server-db-load-mode", msa_gpu_server_db_load_mode,
                        "--gpu-server-startup-wait", msa_gpu_server_startup_wait,
                        "--msa-provider", msa_provider,
                        "--colabfold-api-host", colabfold_api_host,
                        "--colabfold-api-min-interval", colabfold_api_min_interval,
                        "--colabfold-api-poll-interval", colabfold_api_poll_interval,
                    ]
                    if msa_preferred_gpus:
                        cmd.extend(["--preferred-gpus", msa_preferred_gpus])
                    if msa_excluded_gpus:
                        cmd.extend(["--excluded-gpus", msa_excluded_gpus])
                    if not msa_use_gpu_enabled:
                        cmd.append("--cpu-only")
                    if ref_seq:
                        cmd.extend(["--reference-sequence", ref_seq])
                    if force_refresh:
                        cmd.append("--force_refresh")
                    if cache_only:
                        cmd.append("--cache-only")
                    # Add MSA quality params (can override preset)
                    if msa_taxon_list:
                        cmd.extend(["--taxon-list", msa_taxon_list])
                    if msa_evalue:
                        cmd.extend(["--evalue", msa_evalue])
                    if msa_max_seqs:
                        cmd.extend(["--max-seqs", msa_max_seqs])
                    if msa_min_seq_id:
                        cmd.extend(["--min-seq-id", msa_min_seq_id])
                    if msa_min_coverage:
                        cmd.extend(["--min-coverage", msa_min_coverage])
                    cmd.extend(["--min-depth-warning", msa_min_depth_warning])
                    cmd.extend(["--min-depth-fail", msa_min_depth_fail])
                    # NEW: expansion, envdb, iteration overrides
                    if msa_use_expand:
                        cmd.extend(["--use-expand", "1" if msa_use_expand == "true" else "0"])
                    if msa_use_env:
                        cmd.extend(["--use-env", "1" if msa_use_env == "true" else "0"])
                    if msa_num_iterations:
                        cmd.extend(["--num-iterations", msa_num_iterations])
                    
                    result = subprocess.run(cmd, text=True, timeout=msa_chain_timeout)
                    if result.returncode != 0:
                        raise RuntimeError(f"MSA script failed with code {result.returncode}")
                    
                    if Path(msa_file).exists():
                        msa_resolved = str(Path(msa_file).resolve())
                        entry["protein"]["msa"] = msa_resolved
                        # Cache this sequence->MSA mapping for homodimer reuse
                        seq_to_msa[sequence] = msa_resolved
                        record["msa_mode"] = "generated"
                        record["msa_path"] = msa_resolved
                        print(f"Generated MSA: {msa_file}")
                except Exception as e:
                    print(f"MSA generation failed for chain {chain_id}: {e}")
                    msa_failures.append(f"protein chain {chain_id}: {e}")
        
        # Fallback policy: by default fail hard for missing protein-chain MSA when use_msa=true.
        if "msa" not in entry["protein"]:
            if use_msa and not msa_allow_empty_fallback:
                chain_id = comp_id[0] if isinstance(comp_id, list) else comp_id
                reason = (
                    f"No MSA available for protein chain {chain_id} while use_msa=true "
                    "(set msa_allow_empty_fallback=true to allow msa: empty)"
                )
                print(f"ERROR: {reason}")
                msa_failures.append(reason)
            else:
                entry["protein"]["msa"] = "empty"
                record["msa_mode"] = "empty"
                print(f"No MSA available for chain {comp_id} - using single-sequence mode")

        if anchor_target and template_cif_path:
            entry_chain_ids = entry["protein"]["id"] if isinstance(entry["protein"]["id"], list) else [entry["protein"]["id"]]
            for chain_id in entry_chain_ids:
                if chain_id in target_chain_ids:
                    entry["protein"]["templates"] = [{
                        "cif": template_cif_path,
                        "chain_id": chain_id,
                        "template_id": chain_id,
                        "force": True,
                        "threshold": target_template_threshold,
                    }]
                    record["anchored_template_cif"] = template_cif_path
                    break
        msa_records.append(record)
                
    elif comp_type == "ligand":
        entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id}}
        
        if binder_chain is None:
            binder_chain = comp_id

        cofactor_smiles = {
            "ATP": "Nc1ncnc2n(cnc12)[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O",
            "ADP": "Nc1ncnc2n(cnc12)[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O",
            "GTP": "Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1",
            "GDP": "Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1",
            "HEM": "CC1=C(CCC(=O)O)C2=CC3=C(C)C(C=C)=C([NH]3)C=C4C(C=C)=C(C)C(=N4)C=C1N2"
        }

        if comp.get("ccd"):
            ccd_code = comp["ccd"]
            if ccd_code in cofactor_smiles:
                 entry["ligand"]["smiles"] = cofactor_smiles[ccd_code]
            else:
                 entry["ligand"]["ccd"] = ccd_code
        elif comp.get("smiles"):
            entry["ligand"]["smiles"] = comp["smiles"]
    elif comp_type == "ion":
        ccd_code = comp.get("ccd", "MG")
        ion_smiles = {
            "MG": "[Mg+2]", "ZN": "[Zn+2]", "CA": "[Ca+2]", "NA": "[Na+]",
            "CL": "[Cl-]", "K": "[K+]", "MN": "[Mn+2]", "FE": "[Fe+2]",
            "CO": "[Co+2]", "NI": "[Ni+2]", "CU": "[Cu+2]"
        }
        if ccd_code in ion_smiles:
            entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "smiles": ion_smiles[ccd_code]}}
        else:
            entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "ccd": ccd_code}}
    elif comp_type == "dna":
        dna_seq = comp.get("sequence", "")
        entry = {"dna": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": dna_seq}}
    elif comp_type == "rna":
        rna_seq = comp.get("sequence", "")
        entry = {"rna": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": rna_seq}}
    elif comp_type == "peptide":
        peptide_seq = comp.get("sequence", "").upper()
        entry = {"protein": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": peptide_seq}}
        record = {
            "component_id": comp_id,
            "component_type": comp_type,
            "sequence_length": len(peptide_seq),
            "msa_mode": None,
            "msa_path": None,
        }
        
        # Peptides < 30 residues: use msa: empty (too short for meaningful MSA hits)
        # Peptides >= 30 residues: try MSA generation like regular proteins
        PEPTIDE_MSA_THRESHOLD = 30
        
        if len(peptide_seq) < PEPTIDE_MSA_THRESHOLD:
            # Short peptides use single-sequence mode to avoid MSA consistency errors
            entry["protein"]["msa"] = "empty"
            record["msa_mode"] = "empty_short_peptide"
        elif use_msa and peptide_seq:
            # Longer peptides: try MSA generation using same logic as proteins
            if peptide_seq in seq_to_msa:
                print(f"Reusing MSA for peptide chain {comp_id}")
                entry["protein"]["msa"] = seq_to_msa[peptide_seq]
                record["msa_mode"] = "reused"
                record["msa_path"] = seq_to_msa[peptide_seq]
            else:
                chain_id = comp_id[0] if isinstance(comp_id, list) else comp_id
                msa_file = f"msa/{complex_name}_{chain_id}.a3m"
                print(f"Generating MSA for peptide chain {chain_id} ({len(peptide_seq)} aa)...")
                try:
                    cmd = [
                        "python3", "${params.code_root}/scripts/run_local_msa.py",
                        "--sequence", peptide_seq,
                        "--name", f"{complex_name}_{chain_id}",
                        "--out_dir", "msa",
                        "--db_path", msa_db_path,
                        "--cache_dir", cache_dir,
                        "--threads", str(msa_threads),
                        "--preset", msa_preset,
                        "--gpu-mode", msa_gpu_mode,
                        "--gpu-threshold", str(msa_gpu_threshold),
                        "--gpu-server-mode", msa_gpu_server_mode,
                        "--gpu-server-wait-timeout", msa_gpu_server_wait_timeout,
                        "--gpu-server-db-load-mode", msa_gpu_server_db_load_mode,
                        "--gpu-server-startup-wait", msa_gpu_server_startup_wait,
                        "--msa-provider", msa_provider,
                        "--colabfold-api-host", colabfold_api_host,
                        "--colabfold-api-min-interval", colabfold_api_min_interval,
                        "--colabfold-api-poll-interval", colabfold_api_poll_interval,
                    ]
                    if msa_preferred_gpus:
                        cmd.extend(["--preferred-gpus", msa_preferred_gpus])
                    if msa_excluded_gpus:
                        cmd.extend(["--excluded-gpus", msa_excluded_gpus])
                    if not msa_use_gpu_enabled:
                        cmd.append("--cpu-only")
                    if cache_only:
                        cmd.append("--cache-only")
                    # Add quality overrides if set
                    if msa_max_seqs:
                        cmd.extend(["--max-seqs", msa_max_seqs])
                    if msa_use_expand:
                        cmd.extend(["--use-expand", "1" if msa_use_expand == "true" else "0"])
                    if msa_use_env:
                        cmd.extend(["--use-env", "1" if msa_use_env == "true" else "0"])
                    if msa_num_iterations:
                        cmd.extend(["--num-iterations", msa_num_iterations])
                    result = subprocess.run(cmd, text=True, timeout=msa_chain_timeout)
                    if result.returncode == 0 and Path(msa_file).exists():
                        msa_resolved = str(Path(msa_file).resolve())
                        entry["protein"]["msa"] = msa_resolved
                        seq_to_msa[peptide_seq] = msa_resolved
                        record["msa_mode"] = "generated"
                        record["msa_path"] = msa_resolved
                        print(f"Generated peptide MSA: {msa_file}")
                    else:
                        # MSA failed - fall back to empty
                        print("Peptide MSA generation returned no results, using single-sequence mode")
                        entry["protein"]["msa"] = "empty"
                        record["msa_mode"] = "empty"
                except Exception as e:
                    print(f"Peptide MSA generation failed: {e}, using single-sequence mode")
                    entry["protein"]["msa"] = "empty"
                    record["msa_mode"] = "empty"
        else:
            # MSA disabled globally - use empty
            entry["protein"]["msa"] = "empty"
            record["msa_mode"] = "empty"
        msa_records.append(record)
    else:
        continue
    boltz_yaml["sequences"].append(entry)

if binder_chain:
    boltz_yaml["properties"] = [{"binder": [binder_chain] if isinstance(binder_chain, str) else binder_chain}]

if msa_failures:
    print("ERROR: Aborting complex preparation because required protein-chain MSA generation failed.")
    for msg in msa_failures:
        print(f"  - {msg}")
    raise SystemExit(2)

manifest_payload = {
    "complex_name": complex_name,
    "use_msa": use_msa,
    "msa_provider": msa_provider,
    "anchor_target": anchor_target,
    "target_chain_ids": sorted(target_chain_ids),
    "fixed_target_source_path": fixed_target_source_path or None,
    "fixed_target_source_chains": fixed_target_source_chains,
    "protein_components": msa_records,
}
Path("msa/complex_msa_manifest.json").write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

yaml_path = f"yamls/{complex_name}.yaml"
with open(yaml_path, "w") as f:
    yaml.dump(boltz_yaml, f, default_flow_style=False)
print(yaml.dump(boltz_yaml, default_flow_style=False))
print(f"Prepared complex YAML: {yaml_path}")
PYEOF

    if [ ! -f "yamls/${complex_name}.yaml" ]; then
        echo "ERROR: Failed to prepare complex YAML for ${complex_name}"
        exit 1
    fi
    """
}

// Boltz folding stage: consumes prepared YAML + precomputed MSAs.
process BoltzFromComplex {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz_complex", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.npz", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(complex_name), path(complex_yaml), path(msa_dir)

    output:
    path "predictions/*.pdb", emit: pdbs, optional: true
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/*.json", emit: jsons, optional: true
    path "predictions/*.npz", emit: npz, optional: true
    path "*.log"

    script:
    def recycling = params.boltz_recycling_steps ?: 3
    def sampling = params.boltz_sampling_steps ?: 50
    def numSamples = params.boltz_diffusion_samples ?: params.boltz_num_samples ?: 1
    def geometryMode = params.boltz_target_geometry_mode ?: (params.boltz_anchor_target ? 'conditioned' : 'flexible')
    """
    set -o pipefail

    mkdir -p tmp yamls predictions msa
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp

    cp -L "${complex_yaml}" "yamls/${complex_name}.yaml"
    if [ -d "${msa_dir}" ]; then
        cp -L "${msa_dir}"/*.a3m msa/ 2>/dev/null || true
    fi

    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
        ${params.boltz_predict_affinity ? '--sampling_steps_affinity ' + (params.boltz_sampling_steps_affinity ?: 200) + ' --diffusion_samples_affinity ' + (params.boltz_diffusion_samples_affinity ?: 5) : ''} \\
        ${params.boltz_affinity_mw_correction ? '--affinity_mw_correction' : ''} \\
        --cache /boltzcache \\
        ${params.boltz_extra_config ?: ''} \\
        2>&1 | tee boltz_complex_${complex_name}.log

    for dir in boltz_results_yamls/predictions/*/; do
        for model_file in \${dir}/*.pdb \${dir}/*.cif; do
            if [ -f "\${model_file}" ]; then cp "\${model_file}" predictions/; fi
        done
        for json_file in \${dir}/*.json; do
            if [ -f "\${json_file}" ]; then cp "\${json_file}" predictions/; fi
        done
        cp "\${dir}"/pae_*.npz predictions/ 2>/dev/null || :
        cp "\${dir}"/affinity_*.json predictions/ 2>/dev/null || :
    done

    if [ -z "\$(ls predictions/*.pdb predictions/*.cif 2>/dev/null)" ]; then
        echo "ERROR: Boltz produced no output files. Check log for errors."
        echo "Common causes: CCD component not found, malformed YAML, GPU OOM"
        exit 1
    fi

    if [ "${geometryMode}" != "flexible" ] && [ -n "${params.fixed_target_source_path ?: ''}" ] && [ -n "${params.fixed_target_source_chains ?: ''}" ] && [ -n "${params.target_chains ?: ''}" ]; then
        python3 ${params.code_root}/scripts/finalize_target_geometry.py \\
            --prediction_dir predictions \\
            --backend boltz \\
            --geometry_mode "${geometryMode}" \\
            --target_pdb "${params.fixed_target_source_path}" \\
            --reference_target_chains "${params.fixed_target_source_chains}" \\
            --predicted_target_chains "${params.target_chains}" \\
            ${params.fixed_target_model_number ? '--target_model_number ' + params.fixed_target_model_number : ''} \\
            2>&1 | tee -a boltz_complex_${complex_name}.log
    fi
    """
}

process RF3FromSequence {
    label 'Foundry'
    label 'gpu'
    publishDir "${params.out_dir}/run/rf3_seq", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/rf3", mode: 'copy', pattern: "output/**/*.cif"
    publishDir "${params.out_dir}/pdb_files/rf3", mode: 'copy', pattern: "output/**/*.json"

    input:
    tuple val(sequence), val(sequence_name), path(msa)

    output:
    path "output/**/*.pdb", emit: pdbs, optional: true
    path "output/**/*.cif", emit: cifs, optional: true
    path "output/**/*.json", emit: jsons, optional: true
    path "*.log"

    script:
    def numRecycles = params.rf3_num_recycles ?: 10
    def earlyStop = params.rf3_early_stopping_plddt ?: 0.5
    def use_msa = msa.name != 'NO_MSA'

    """
    mkdir -p output inputs
    
    # Setup environment
    export PROJECT_ROOT=\$(pwd)
    
    # Write sequence to JSON with MSA path if available
    # RF3 uses msa_path field in JSON components array
    MSA_ABS_PATH=\$(readlink -f ${msa})
    
    if [ "${msa.name}" != "NO_MSA" ]; then
        # Include MSA path in JSON for better predictions
        cat > inputs/${sequence_name}.json << JSONEOF
{
  "name": "${sequence_name}",
  "components": [
    {
      "seq": "${sequence}",
      "msa_path": "\${MSA_ABS_PATH}"
    }
  ]
}
JSONEOF
        echo "Using pre-computed MSA: \${MSA_ABS_PATH}"
    else
        # No MSA available - RF3 will predict without alignments
        cat > inputs/${sequence_name}.json << 'JSONEOF'
{
  "name": "${sequence_name}",
  "components": [
    {
      "seq": "${sequence}"
    }
  ]
}
JSONEOF
        echo "No MSA provided - running without alignments"
    fi
    
    # WORKAROUND for rc-foundry cli.py bug: 
    # The 'rf3 fold' CLI has a bug where it computes config_path as Path(__file__).parent.parent.parent / "configs"
    # which goes up 3 levels from cli.py to /usr/local/lib/python3.12/ instead of staying in the rf3 package.
    # We bypass the CLI and call rf3.inference directly with the correct config path.
    
    (python3 << 'PYEOF'
import sys
import os
from pathlib import Path

# Find the RF3 package and its CORRECT configs directory
import rf3
rf3_pkg = Path(rf3.__file__).parent
config_path = str(rf3_pkg / "configs")

print(f"RF3 package: {rf3_pkg}", flush=True)
print(f"Config path: {config_path}", flush=True)

# WORKAROUND: Set PROJECT_ROOT that rf3/inference.py expects
# and mock rootutils.setup_root to prevent it from failing
os.environ["PROJECT_ROOT"] = str(rf3_pkg.parent.parent.parent)  # foundry project root

import rootutils
original_setup_root = rootutils.setup_root
def mock_setup_root(*args, **kwargs):
    print("Bypassing rootutils.setup_root()", flush=True)
    return Path(os.environ["PROJECT_ROOT"])
rootutils.setup_root = mock_setup_root

from hydra import initialize_config_dir, compose

with initialize_config_dir(config_dir=config_path, version_base="1.3"):
    cfg = compose(config_name="inference", overrides=[
        "inputs=inputs/${sequence_name}.json",
        "ckpt_path=/root/.foundry/checkpoints/rf3_foundry_01_24_latest_remapped.ckpt",
        "out_dir=output",
        "n_recycles=${numRecycles}",
        "early_stopping_plddt_threshold=${earlyStop}",
        "inference_engine=rf3"
    ])
    
    # Now import and run - rootutils is mocked
    from rf3.inference import run_inference
    run_inference(cfg)

print("RF3 inference completed successfully", flush=True)
PYEOF
    ) 2>&1 | tee rf3_seq_${sequence_name}.log
    
    if [ ! -f output/*.cif ] && [ ! -f output/*.pdb ]; then
        echo "RF3 produced no output files"
        touch output/rf3_failed.txt
    fi
    """
}

// Workflow for structure prediction from sequence
workflow structure_prediction_wf {
    take:
    input_ch // Channel of [sequence, sequence_name]

    main:
    def pred_method = params.pred_method ?: 'boltz'
    def toBool = { v, defVal ->
        if (v == null) return defVal
        if (v instanceof Boolean) return v
        return v.toString().equalsIgnoreCase('true')
    }
    def boltz_use_msa = toBool(params.boltz_use_msa, false)
    def rf3_use_msa = toBool(params.rf3_use_msa, false)
    def protenix_use_msa = toBool(params.protenix_use_msa, true)

    structures = channel.empty()

    // Determine which predictors need MSA
    def need_boltz_msa  = (pred_method in ['boltz', 'both', 'all'] && boltz_use_msa)
    def need_rf3_msa    = (pred_method in ['rf3', 'both', 'all'] && rf3_use_msa)
    // Protenix resolves its own MSA backend in the prediction module.
    // Do not trigger parent GenerateLocalMSA just because Protenix MSA is enabled.
    def need_msa = need_boltz_msa || need_rf3_msa

    if (need_msa) {
        def provided_msa = params.msa_path ? file(params.msa_path) : null
        def hasProvidedMsa = provided_msa && provided_msa.exists()

        if (hasProvidedMsa) {
            // Use precomputed MSA (e.g., from MSA batch job)
            def inputs_with_msa = input_ch.map { seq, name -> tuple(seq, name, provided_msa) }

            if (pred_method == 'boltz' || pred_method == 'both' || pred_method == 'all') {
                BoltzFromSequenceWithMSA(inputs_with_msa)
                structures = structures.mix(BoltzFromSequenceWithMSA.out.pdbs, BoltzFromSequenceWithMSA.out.cifs)
            }

            if (pred_method == 'rf3' || pred_method == 'both' || pred_method == 'all') {
                RF3FromSequence(inputs_with_msa)
                structures = structures.mix(RF3FromSequence.out.pdbs, RF3FromSequence.out.cifs)
            }

            if (pred_method == 'protenix' || pred_method == 'all') {
                // Protenix takes [sequence, name] and handles MSA internally via protenix prep
                ProtenixPredict(input_ch)
                structures = structures.mix(ProtenixPredict.out.cifs)
            }
        } else {
            // STEP 1: Generate MSA ONCE per unique sequence
            def base_seq = input_ch
                .first()
                .map { seq, _name -> tuple(seq, "base_msa") }

            GenerateLocalMSA(base_seq)

            // STEP 2: Combine the single MSA with all job inputs
            def msa_ch = GenerateLocalMSA.out.msa.map { _seq, _name, msa_file -> msa_file }
            def inputs_with_msa = input_ch.combine(msa_ch)

            // STEP 3: Run predictions with cached MSA
            if (pred_method == 'boltz' || pred_method == 'both' || pred_method == 'all') {
                BoltzFromSequenceWithMSA(inputs_with_msa)
                structures = structures.mix(BoltzFromSequenceWithMSA.out.pdbs, BoltzFromSequenceWithMSA.out.cifs)
            }

            if (pred_method == 'rf3' || pred_method == 'both' || pred_method == 'all') {
                RF3FromSequence(inputs_with_msa)
                structures = structures.mix(RF3FromSequence.out.pdbs, RF3FromSequence.out.cifs)
            }

            if (pred_method == 'protenix' || pred_method == 'all') {
                ProtenixPredict(input_ch)
                structures = structures.mix(ProtenixPredict.out.cifs)
            }
        }
    }
    else {
        // No MSA needed - run directly
        if (pred_method == 'boltz' || pred_method == 'both' || pred_method == 'all') {
            BoltzFromSequence(input_ch)
            structures = structures.mix(BoltzFromSequence.out.pdbs, BoltzFromSequence.out.cifs)
        }

        if (pred_method == 'rf3' || pred_method == 'both' || pred_method == 'all') {
            def dummy_msa = file("${params.code_root}/NO_MSA")
            def inputs_no_msa = input_ch.map { seq, name -> tuple(seq, name, dummy_msa) }
            RF3FromSequence(inputs_no_msa)
            structures = structures.mix(RF3FromSequence.out.pdbs, RF3FromSequence.out.cifs)
        }

        if (pred_method == 'protenix' || pred_method == 'all') {
            // Protenix handles its own MSA via built-in protenix prep or ESM
            ProtenixPredict(input_ch)
            structures = structures.mix(ProtenixPredict.out.cifs)
        }
    }

    emit:
    structures
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPLEX PREDICTION WORKFLOW
// ─────────────────────────────────────────────────────────────────────────────
// Centralized routing for complex (multi-chain + ligand) structure predictions.
// Dispatches to the appropriate predictor based on params.pred_method.
// Input: channel of [name, complex_json, msa_file] tuples

workflow complex_prediction_wf {
    take:
    input_ch  // Channel of [name, complex_json, msa_file]

    main:
    def pred_method = params.pred_method ?: 'boltz'

    structures = channel.empty()

    if (pred_method == 'protenix') {
        // Convert BMS JSON → Protenix-format JSON, then predict
        PrepProtenixComplex(input_ch)
        ProtenixFromComplex(PrepProtenixComplex.out.protenix_json)
        structures = ProtenixFromComplex.out.structures
    }
    else if (pred_method == 'all') {
        // Run both Boltz + Protenix in parallel
        PrepareComplexWithMSA(input_ch)
        BoltzFromComplex(PrepareComplexWithMSA.out.prepared)

        PrepProtenixComplex(input_ch)
        ProtenixFromComplex(PrepProtenixComplex.out.protenix_json)

        structures = BoltzFromComplex.out.pdbs.mix(ProtenixFromComplex.out.structures)
    }
    else {
        // Default: Boltz-2 complex prediction
        PrepareComplexWithMSA(input_ch)
        BoltzFromComplex(PrepareComplexWithMSA.out.prepared)
        structures = BoltzFromComplex.out.pdbs
    }

    emit:
    structures
}
