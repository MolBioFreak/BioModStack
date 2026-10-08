// Structure Prediction from Sequence
// Modules for predicting 3D protein structure directly from amino acid sequence

process GenerateRemoteMSA {
    label 'CPU'
    // Running on CPU to save GPU - generates MSA ONCE per unique sequence
    // Uses local cache to avoid redundant ColabFold API calls
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "*.a3m"

    input:
    tuple val(sequence), val(sequence_name)

    output:
    tuple val(sequence), val(sequence_name), path("${sequence_name}.a3m"), emit: msa
    path "*.log"

    script:
    def cacheDir = params.msa_cache_dir ?: "${projectDir}/data/msa_cache"
    def dbPath = params.db_path ?: "${projectDir}/platform/api/proteindj.db"
    def maxAgeDays = params.msa_cache_max_age_days ?: 30
    def forceRefresh = params.msa_force_refresh ? '--force_refresh' : ''
    """
    python3 ${projectDir}/scripts/fetch_colabfold_msa.py \\
        --sequence "${sequence}" \\
        --name "${sequence_name}" \\
        --out_dir . \\
        --cache_dir ${cacheDir} \\
        --db_path ${dbPath} \\
        --max_age_days ${maxAgeDays} \\
        ${forceRefresh} \\
        2>&1 | tee msa_${sequence_name}.log
    """
}

process BoltzFromSequence {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz_seq", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files/predictions", mode: 'copy', pattern: "predictions/*.json", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(sequence), val(sequence_name)

    output:
    path "predictions/*.pdb", emit: pdbs, optional: true
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/*.json", emit: jsons, optional: true
    path "*.log"

    script:
    def recycling = params.boltz_recycling_steps ?: 3
    def sampling = params.boltz_sampling_steps ?: 50
    def numSamples = params.boltz_diffusion_samples ?: params.boltz_num_samples ?: 1
    def useMsaServer = params.boltz_use_msa ? '--use_msa_server' : ''
    """
    # Setup temp directories for containerized execution
    mkdir -p tmp yamls predictions
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Write sequence to YAML format expected by Boltz-2
    cat > yamls/${sequence_name}.yaml << 'EOF'
version: 1
sequences:
  - protein:
      id: ['A']
      sequence: ${sequence}
EOF
    
    # Run Boltz-2 prediction with optional MSA server
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
        --cache /boltzcache \\
        ${useMsaServer} \\
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
    
    # Write sequence to YAML format with pre-computed MSA
    # Use absolute path for MSA so Boltz can find it from any context
    MSA_ABS_PATH=\$(readlink -f ${msa_file})
    cat > yamls/${sequence_name}.yaml << EOF
version: 1
sequences:
  - protein:
      id: ['A']
      sequence: ${sequence}
      msa: \${MSA_ABS_PATH}
EOF
    
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

    input:
    tuple val(complex_name), path(complex_json), path(msa_files)

    output:
    path "predictions/*.pdb", emit: pdbs, optional: true
    path "predictions/*.cif", emit: cifs, optional: true
    path "predictions/*.json", emit: jsons, optional: true
    path "*.log"

    script:
    def recycling = params.boltz_recycling_steps ?: 3
    def sampling = params.boltz_sampling_steps ?: 50
    def numSamples = params.boltz_diffusion_samples ?: params.boltz_num_samples ?: 1
    def use_msa_flag = params.boltz_use_msa == null || params.boltz_use_msa.toString() == 'true' ? '--use_msa_server' : ''
    """
    mkdir -p tmp yamls predictions
    export NUMBA_CACHE_DIR=tmp
    export XDG_CONFIG_HOME=tmp
    export TRITON_CACHE_DIR=tmp
    export HOME=tmp
    
    # Convert JSON complex definition to Boltz-2 YAML format
    python3 << 'PYEOF'
import json
import yaml
from pathlib import Path

with open("${complex_json}") as f:
    complex_def = json.load(f)

boltz_yaml = {"version": 1, "sequences": []}
binder_chain = None

for comp in complex_def.get("components", []):
    comp_type = comp.get("type", "protein")
    comp_id = comp.get("id", "A")
    
    if comp_type == "protein":
        entry = {"protein": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": comp.get("sequence", "")}}
        msa_path = comp.get("msa_path")
        if msa_path and Path(msa_path).exists():
            entry["protein"]["msa"] = str(Path(msa_path).resolve())
    elif comp_type == "ligand":
        entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id}}
        
        # Track the first real ligand as the binder for affinity prediction
        if binder_chain is None:
            binder_chain = comp_id

        # Common cofactor SMILES to bypass CCD lookup failure
        cofactor_smiles = {
            "ATP": "Nc1ncnc2n(cnc12)[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O",
            "ADP": "Nc1ncnc2n(cnc12)[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O",
            "GTP": "Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1",
            "GDP": "Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1",
            "HEM": "CC1=C(CCC(=O)O)C2=CC3=C(C)C(C=C)=C([NH]3)C=C4C(C=C)=C(C)C(=N4)C=C1N2" # Simplified Heme
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
        # Common ion SMILES map to bypass CCD lookup failure
        ion_smiles = {
            "MG": "[Mg+2]",
            "ZN": "[Zn+2]",
            "CA": "[Ca+2]",
            "NA": "[Na+]",
            "CL": "[Cl-]",
            "K": "[K+]",
            "MN": "[Mn+2]",
            "FE": "[Fe+2]", # Default to +2
            "CO": "[Co+2]",
            "NI": "[Ni+2]",
            "CU": "[Cu+2]"
        }
        
        # Prefer SMILES if available (more robust)
        if ccd_code in ion_smiles:
            entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "smiles": ion_smiles[ccd_code]}}
        else:
            entry = {"ligand": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "ccd": ccd_code}}
            
    elif comp_type == "dna":
        # Pass DNA sequence as is (standard is uppercase A, T, G, C)
        dna_seq = comp.get("sequence", "")
        entry = {"dna": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": dna_seq}}
    elif comp_type == "rna":
        # Pass RNA sequence as is (standard is uppercase A, U, G, C)
        rna_seq = comp.get("sequence", "")
        entry = {"rna": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": rna_seq}}
    elif comp_type == "peptide":
        # Peptides are treated as short protein chains
        peptide_seq = comp.get("sequence", "").upper()
        entry = {"protein": {"id": [comp_id] if isinstance(comp_id, str) else comp_id, "sequence": peptide_seq}}
    else:
        continue
    boltz_yaml["sequences"].append(entry)

# Enable binding affinity prediction if a binder was identified
if binder_chain:
    boltz_yaml["properties"] = [{"binder": [binder_chain] if isinstance(binder_chain, str) else binder_chain}]

with open(f"yamls/${complex_name}.yaml", "w") as f:
    yaml.dump(boltz_yaml, f, default_flow_style=False)
print(yaml.dump(boltz_yaml, default_flow_style=False))
PYEOF
    
    boltz predict \\
        ./yamls/ \\
        --output_format pdb \\
        --diffusion_samples ${numSamples} \\
        --recycling_steps ${recycling} \\
        --sampling_steps ${sampling} \\
        ${params.boltz_use_potentials ? '--use_potentials' : ''} \\
        ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \\
        --cache /boltzcache \\
        ${use_msa_flag} \\
        ${params.boltz_extra_config ?: ''} \\
        2>&1 | tee boltz_complex_${complex_name}.log
    
    for dir in boltz_results_yamls/predictions/*/; do
        for model_file in \${dir}/*.pdb \${dir}/*.cif; do
            if [ -f "\${model_file}" ]; then cp "\${model_file}" predictions/; fi
        done
        for json_file in \${dir}/*.json; do
            if [ -f "\${json_file}" ]; then cp "\${json_file}" predictions/; fi
        done
        # Copy affinity files if they exist (ignore error if none)
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

        GenerateRemoteMSA(base_seq)

        // STEP 2: Combine the single MSA with all job inputs
        // GenerateRemoteMSA.out.msa = [sequence, "base_msa", path(msa)]
        def msa_ch = GenerateRemoteMSA.out.msa.map { _seq, _name, msa_file -> msa_file }

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
