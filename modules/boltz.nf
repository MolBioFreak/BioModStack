def formatFilterParams(params, paramPrefix, paramNames) {
    return paramNames.collect { name ->
        def paramValue = params["${paramPrefix}_${name}"]
        if (paramValue != null) {
            def cmdParam = name.replaceAll('_', '-')
            return "--${paramPrefix}-${cmdParam} ${paramValue}"
        }
        return ""
    }.findAll { value -> value != "" }.join(' ')
}

process PrepBoltz {
    label 'pyrosetta_tools'
    errorStrategy { params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate' }

    input:
    path pdb_files

    output:
    path ("yamls/*.yaml"), emit: yamls

    script:
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Generate yaml files containing sequences for Boltz-2 prediction 
    python ${params.code_root}/scripts/prep_boltz_yaml.py \
        --input "./" \
        --output "yamls"
    """
}

// PrepBoltz WITH MSA Generation
// Generates MSAs for each unique chain sequence using GPU MMseqs2, then creates YAMLs with MSA paths
process PrepBoltzWithMSA {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*.a3m"

    input:
    path pdb_files

    output:
    path ("yamls/*.yaml"), emit: yamls
    path ("msa/*.a3m"), emit: msas, optional: true

    script:
    def dbPath = params.msa_local_db
    def cacheDir = params.msa_cache_dir
    def threads = params.msa_threads ?: 32
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta
    
    mkdir -p yamls msa
    
    # Call external prep_boltz_with_msa.py script
    python3 ${params.code_root}/scripts/prep_boltz_with_msa.py \\
        --input "./" \\
        --output "yamls" \\
        --msa_output "msa" \\
        --db_path "${dbPath}" \\
        --cache_dir "${cacheDir}" \\
        --threads ${threads} \\
        --msa_script "${params.code_root}/scripts/run_local_msa.py"
    """
}

process RunBoltz {
    label 'Boltz'
    label 'gpu'
    errorStrategy { params.containsKey('plr_validator_suite_active') && params.plr_validator_suite_active == true ? 'ignore' : 'terminate' }
    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "*.log"
    tag "B${batch_id}"

    input:
    tuple val(batch_id), path(yamls)

    output:
    tuple path("predictions/*.pdb"), path("predictions/*.json"), emit: pdbs_jsons
    path "boltz_completion.json", emit: completion
    path ("predictions/*.npz"), emit: pae_npz, optional: true
    path ("*.log"), emit: logs

    script:
    """
        # We specify tmp directories as some python packages try to write to the user home directory outside the container
        mkdir tmp
        export NUMBA_CACHE_DIR=tmp
        export XDG_CONFIG_HOME=tmp
        export TRITON_CACHE_DIR=tmp
        export HOME=tmp
        
        mkdir yamls
        cp -L -- *.yaml ./yamls/

        python3 ${params.code_root}/scripts/run_boltz_wrapper.py predict \
        ${params.boltz_seed != null ? "--seed " + params.boltz_seed : ""} \
            ./yamls/ \
            --output_format pdb \
            --write_full_pae \
            --diffusion_samples ${params.boltz_diffusion_samples != null ? params.boltz_diffusion_samples : (params.boltz_num_samples != null ? params.boltz_num_samples : 1)} \
            ${params.boltz_max_parallel_samples != null ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \
            --recycling_steps ${(params.boltz_recycling_steps != null ? params.boltz_recycling_steps : 3)} \
            --sampling_steps ${(params.boltz_sampling_steps != null ? params.boltz_sampling_steps : 50)} \
            ${params.boltz_method ? '--method ' + params.boltz_method : ''} \
            ${params.boltz_use_potentials ? '--use_potentials' : ''} \
            ${params.boltz_step_scale != null ? '--step_scale ' + params.boltz_step_scale : ''} \
            ${params.boltz_predict_affinity ? '--sampling_steps_affinity ' + ((params.boltz_sampling_steps_affinity != null ? params.boltz_sampling_steps_affinity : 200)) + ' --diffusion_samples_affinity ' + ((params.boltz_diffusion_samples_affinity != null ? params.boltz_diffusion_samples_affinity : 5)) : ''} \
            ${params.boltz_affinity_mw_correction ? '--affinity_mw_correction' : ''} \
            --cache /boltzcache \
            ${params.boltz_extra_config ? params.boltz_extra_config : ''} \
            2>&1 | tee boltz_${batch_id}.log
 
        # Preserve every native ranked sample and its exact original YAML ID.
        python3 ${params.code_root}/scripts/publish_boltz_samples.py \
            --native-root boltz_results_yamls/predictions --inputs yamls --output predictions

        python3 - <<'PY'
import json
from pathlib import Path

expected = sorted(path.stem for path in Path('yamls').glob('*.yaml'))
samples = json.loads(Path('predictions/boltz_samples.manifest').read_text())['samples']
completed = sorted({row['source_id'] for row in samples if row['confidence_present']})
Path('boltz_completion.json').write_text(
    json.dumps(
        {
            'schema_version': 1,
            'validator': 'boltz2',
            'expected_candidates': expected,
            'completed_candidates': completed,
            'native_samples': samples,
        },
        sort_keys=True,
        separators=(',', ':'),
    ) + '\\n',
    encoding='utf-8',
)
PY

        """
}
process AlignBoltz {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/align", mode: 'copy', pattern: "alignment_*.log"

    input:
    tuple path(pdb_files), path(json_files)
    path designs
    val design_type

    output:
    tuple path("aligned/*.pdb"), path("aligned/*.json"), emit: pdbs_jsons
    path "alignment_*.log"
    path ("boltz_metadata_*.jsonl"), topic: metadata_ch_fold_seq

    script:

    def num_processes = task.cpus - 1
    // Admission-owned marker, never inferred from prediction sidecars.
    def revision = params.get('core_protein_scientific_contract')
    if (revision != null && (revision instanceof Boolean || revision.toString() != '1')) {
        throw new IllegalArgumentException('core_protein_scientific_contract must be exactly 1')
    }
    def scientificArgs = revision == null ? '' : '--core_protein_scientific_contract 1'
    def quoteRole = { value -> "'" + (value ?: '').toString().replace("'", "'\"'\"'") + "'" }
    // No A/B inference for marked binder requests. Missing roles reach the strict gate.
    def roleArgs = revision == null ? '' : "--binder_chains ${quoteRole(params.antibody_chains ?: params.binder_chains)} --target_chains ${quoteRole(params.antigen_chains ?: params.target_chains)}"

    """
    ${revision == null ? '' : 'set -euo pipefail'}
    export MAMBA_ROOT_PREFIX=/opt/conda/
    
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Script to align predictions to RFdiffusion designs and calculate RMSD
    # Also, extracts and renames metadata from json files
    python ${params.code_root}/scripts/align_boltz.py \
        --design_dir ./ \
        --boltz_dir ./ \
        --output_dir aligned \
        --design_type ${design_type} ${scientificArgs} ${roleArgs} \
        --ncpus ${num_processes} \
        2>&1 | tee alignment_${task.index}.log
    
    # metadata convert script to combine json files into jsonl
    python /scripts/metadata_converter.py \
        --converter boltz \
        --input_dir aligned \
        --input_ext .json \
        --output_file boltz_metadata_${task.index}.jsonl
    """
}
process FilterBoltz {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_boltz", mode: 'copy', pattern: '*.log'

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("output/*.pdb"), emit: pdbs, optional: true
    path ("filter_boltz_${task.index}.log"), emit: log
    path ("filtered.jsonl"), emit: jsonl, optional: true

    script:
    def paramString = formatFilterParams(
        params,
        "boltz",
        [
            "max_overall_rmsd",
            "max_binder_rmsd",
            "max_target_rmsd",
            "min_conf_score",
            "min_ptm",
            "min_ptm_interface",
            "min_plddt",
            "min_plddt_interface",
            "max_pde",
            "max_pde_interface",
        ],
    )

    """
    python -u /scripts/filter_boltz.py \\
        --json-directory ./ \\
        ${paramString} \\
        --output-directory output \\
        2>&1 | tee filter_boltz_${task.index}.log
    """
}
