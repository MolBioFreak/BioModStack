// Structure Prediction from Sequence
// Modules for predicting 3D protein structure directly from amino acid sequence

// Generate MSA using local MMseqs2 database - GPU ACCELERATED!
// Uses full ColabFold database at /mnt/BioModStack/colabfold_db
// Hybrid scheduling: GPU when available, falls back to CPU
process GenerateLocalMSA {
    label 'CPU'
    // Runs MMseqs2 locally against UniRef30 + ColabFoldDB
    // No internet required, no API rate limits
    // GPU-accelerated when available (~5-10 sec vs ~2-3 min CPU)
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "*.a3m"

    input:
    tuple val(sequence), val(sequence_name)

    output:
    tuple val(sequence), val(sequence_name), path("${sequence_name}.a3m"), emit: msa
    path "*.log"

    script:
    def dbPath = params.msa_local_db ?: "/mnt/BioModStack/colabfold_db"
    def cacheDir = params.msa_cache_dir ?: "/mnt/BioModStack/msa_cache"
    def threads = params.msa_threads ?: 32
    def useGpu = params.msa_use_gpu != false ? "" : "--cpu-only"
    def refSeq = params.msa_reference_sequence ? "--reference-sequence \"${params.msa_reference_sequence}\"" : ""
    """
    python3 ${projectDir}/scripts/run_local_msa.py \\
        --sequence "${sequence}" \\
        --name "${sequence_name}" \\
        --out_dir . \\
        --db_path ${dbPath} \\
        --cache_dir ${cacheDir} \\
        --threads ${threads} \\
        ${useGpu} \\
        ${refSeq} \\
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
    def dbPath = params.msa_local_db ?: "/mnt/BioModStack/colabfold_db"
    def cacheDir = params.msa_cache_dir ?: "/mnt/BioModStack/msa_cache"
    def maxParallel = params.msa_max_parallel ?: 4
    def refSeqArg = reference_sequence ? "--reference_sequence '${reference_sequence}'" : ""
    """
    python3 ${projectDir}/scripts/batch_msa.py \\
        --sequences '${sequences_json}' \\
        --output_dir . \\
        --db_path ${dbPath} \\
        --cache_dir ${cacheDir} \\
        --max_parallel ${maxParallel} \\
        ${refSeqArg} \\
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
    def msaDbPath = params.msa_local_db ?: '/mnt/BioModStack/colabfold_db'
    def msaThreads = params.msa_threads ?: 32
    def useMsa = params.boltz_use_msa == null || params.boltz_use_msa.toString() == 'true'
    """
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
    
    # Run Boltz-2 prediction (NO --use_msa_server - MSA is pre-computed!)
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
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
    done
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
    # Setup temp directories for containerized execution
    mkdir -p tmp yamls predictions
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Generate proper multi-chain YAML using Python
    # Handles colon-separated sequences (e.g., "VH_SEQ:VL_SEQ" -> chains A, B)
    python3 << 'PYEOF'
import yaml
from pathlib import Path

sequence_input = "${sequence}"
sequence_name = "${sequence_name}"
msa_file = "${msa_file}"

# Split by colon for multi-chain input
chains = sequence_input.split(':')
chain_ids = [chr(ord('A') + i) for i in range(len(chains))]

# Build Boltz YAML structure
boltz_yaml = {"version": 1, "sequences": []}

msa_path = str(Path(msa_file).resolve()) if Path(msa_file).exists() else None

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
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
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
    done
    """
}

// Boltz with Complex Definition (Multi-chain + Ligands)
// Accepts a JSON file defining the complex components
process BoltzFromComplex {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz_complex", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*.a3m"

    input:
    tuple val(complex_name), path(complex_json), path(msa_files)

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
    def msaDbPath = params.msa_local_db ?: '/mnt/BioModStack/colabfold_db'
    def msaThreads = params.msa_threads ?: 32
    def useMsa = params.boltz_use_msa == null || params.boltz_use_msa.toString() == 'true'
    """
    mkdir -p tmp yamls predictions msa
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Convert JSON complex definition to Boltz-2 YAML format
    # AND generate local MSA for each protein chain
    python3 << 'PYEOF'
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
msa_threads = int("${msaThreads}")
use_msa = "${useMsa}" == "true"
complex_name = "${complex_name}"

for comp in complex_def.get("components", []):
    comp_type = comp.get("type", "protein")
    comp_id = comp.get("id", "A")
    
    if comp_type == "protein":
        sequence = comp.get("sequence", "")
        entry = {"protein": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": sequence}}
        
        # Check for pre-existing MSA path
        msa_path = comp.get("msa_path")
        if msa_path and Path(msa_path).exists():
            entry["protein"]["msa"] = str(Path(msa_path).resolve())
        elif use_msa and sequence:
            # Generate MSA using run_local_msa.py with file-based locking to prevent parallel OOM
            chain_id = comp_id[0] if isinstance(comp_id, list) else comp_id
            msa_dir = "msa"
            msa_file = f"msa/{complex_name}_{chain_id}.a3m"
            cache_dir = "/mnt/BioModStack/msa_cache"
            
            # Get reference sequence if set (for mutagenesis - all variants share WT MSA)
            ref_seq = comp.get("reference_sequence") or os.environ.get("MSA_REFERENCE_SEQUENCE", "")
            ref_seq_arg = f"--reference-sequence '{ref_seq}'" if ref_seq else ""
            
            print(f"Generating local MSA for chain {chain_id} using run_local_msa.py...")
            try:
                # Use run_local_msa.py which has:
                # 1. File-based locking to serialize parallel jobs
                # 2. Cache checking to avoid redundant MSA generation
                # 3. GPU/CPU auto-detection
                cmd = [
                    "python3", "${projectDir}/scripts/run_local_msa.py",
                    "--sequence", sequence,
                    "--name", f"{complex_name}_{chain_id}",
                    "--out_dir", msa_dir,
                    "--db_path", msa_db_path,
                    "--cache_dir", cache_dir,
                    "--threads", str(msa_threads)
                ]
                if ref_seq:
                    cmd.extend(["--reference-sequence", ref_seq])
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                if result.returncode != 0:
                    print(f"MSA generation stderr: {result.stderr}")
                    raise RuntimeError(f"MSA script failed with code {result.returncode}")
                print(result.stdout)
                
                if Path(msa_file).exists():
                    entry["protein"]["msa"] = str(Path(msa_file).resolve())
                    print(f"Generated MSA: {msa_file}")
            except Exception as e:
                print(f"MSA generation failed for chain {chain_id}: {e}")
                
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
    else:
        continue
    boltz_yaml["sequences"].append(entry)

if binder_chain:
    boltz_yaml["properties"] = [{"binder": [binder_chain] if isinstance(binder_chain, str) else binder_chain}]

with open(f"yamls/${complex_name}.yaml", "w") as f:
    yaml.dump(boltz_yaml, f, default_flow_style=False)
print(yaml.dump(boltz_yaml, default_flow_style=False))
PYEOF
    
    # Run Boltz-2 prediction (NO --use_msa_server - MSA is pre-computed!)
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
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
        cp "\${dir}"/affinity_*.json predictions/ 2>/dev/null || :
    done
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
    def boltz_use_msa = params.boltz_use_msa ?: false
    def rf3_use_msa = params.rf3_use_msa ?: false

    structures = channel.empty()

    // Determine if we need MSA for any predictor
    def need_msa = (pred_method in ['boltz', 'both'] && boltz_use_msa) || (pred_method in ['rf3', 'both'] && rf3_use_msa)

    if (need_msa) {
        // STEP 1: Generate MSA ONCE per unique sequence
        // Extract base sequence (first item if all are same sequence with different job IDs)
        def base_seq = input_ch
            .first()
            .map { seq, _name -> tuple(seq, "base_msa") }

        GenerateLocalMSA(base_seq)

        // STEP 2: Combine the single MSA with all job inputs
        // GenerateLocalMSA.out.msa = [sequence, "base_msa", path(msa)]
        def msa_ch = GenerateLocalMSA.out.msa.map { _seq, _name, msa_file -> msa_file }

        def inputs_with_msa = input_ch.combine(msa_ch)
        // Now: [sequence, job_name, msa_file]

        // STEP 3: Run predictions with cached MSA (no rate limiting!)
        if (pred_method == 'boltz' || pred_method == 'both') {
            BoltzFromSequenceWithMSA(inputs_with_msa)
            structures = structures.mix(BoltzFromSequenceWithMSA.out.pdbs, BoltzFromSequenceWithMSA.out.cifs)
        }

        if (pred_method == 'rf3' || pred_method == 'both') {
            RF3FromSequence(inputs_with_msa)
            structures = structures.mix(RF3FromSequence.out.pdbs, RF3FromSequence.out.cifs)
        }
    }
    else {
        // No MSA needed - run directly
        if (pred_method == 'boltz' || pred_method == 'both') {
            BoltzFromSequence(input_ch)
            structures = structures.mix(BoltzFromSequence.out.pdbs, BoltzFromSequence.out.cifs)
        }

        if (pred_method == 'rf3' || pred_method == 'both') {
            def dummy_msa = file("${projectDir}/NO_MSA")
            def inputs_no_msa = input_ch.map { seq, name -> tuple(seq, name, dummy_msa) }
            RF3FromSequence(inputs_no_msa)
            structures = structures.mix(RF3FromSequence.out.pdbs, RF3FromSequence.out.cifs)
        }
    }

    emit:
    structures
}
