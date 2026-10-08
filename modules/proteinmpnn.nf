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

process PrepMPNN {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/mpnn", mode: 'copy', pattern: "mpnn_prep_*.log"

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("mpnn_fixed/*.pdb"), emit: pdbs
    path ("mpnn_prep_*.log")

    script:
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta
    
    python /scripts/prep_mpnn_designs.py \
        --input_dir "./" \
        --out_dir "${params.get('sequence_design_engine') == 'proteinmpnn' ? 'mpnn_fixed' : 'mpnn_input'}" ${params.get('sequence_design_engine') == 'proteinmpnn' ? '--generic' : ''}
    
    # Add unique ID to mpnn prep logfile
    cp mpnn_prep.log mpnn_prep_${task.index}.log

    if ${params.get('sequence_design_engine') == 'proteinmpnn' ? 'false' : 'true'}; then
    # Legacy antibody preparation retains its existing chain locks.
    # This is the official method used by dl_binder_design
    python /scripts/add_fixed_labels.py \
        --input_dir "mpnn_input" \
        --output_dir "mpnn_fixed" \
        --designable_chains "H,L"
    fi

    """
}

process RunMPNN {
    label 'MPNN'
    label 'gpu_light' // Native ProteinMPNN inference; shared GPU placement authority.

    publishDir "${params.out_dir}/run/mpnn", mode: 'copy', pattern: "*.log"

    publishDir "${params.out_dir}/run/mpnn", mode: 'copy', pattern: "mpnn_native_output", saveAs: { fn -> "raw" }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "{results,mpnn_canonical}/*.{pdb,json}", saveAs: { fn -> fn.replaceFirst(/^(results|mpnn_canonical)\//, '') }
    publishDir "${params.out_dir}/run/mpnn", mode: 'copy', pattern: "mpnn_metadata_*.jsonl"

    // Retry only when allow_retries is enabled (off by default for debugging)
    errorStrategy { params.allow_retries && task.exitStatus in [137, 139] ? 'retry' : 'terminate' }
    maxRetries { params.allow_retries ? 2 : 0 }

    input:
    path pdbs

    output:
    tuple path("${params.get('sequence_design_engine') == 'proteinmpnn' ? 'mpnn_canonical' : 'results'}/*.pdb"), path("${params.get('sequence_design_engine') == 'proteinmpnn' ? 'mpnn_canonical' : 'results'}/*.json"), emit: pdbs_jsons
    path "mpnn_native_output", emit: raw
    path ("mpnn_metadata_${task.index}.jsonl"), topic: metadata_ch_fold_seq
    path "*.log"

    script:
    def extraBase64 = (params.get('mpnn_extra_config') != null ? params.get('mpnn_extra_config') : '').toString().getBytes('UTF-8').encodeBase64().toString()
    """
    # Raw config remains argv data, never shell syntax or command substitution.
    python -c 'import base64,shlex,sys; args=shlex.split(base64.b64decode(sys.argv[1]).decode()); reserved=["-pdbdir", "-outpdbdir", "-augment_eps", "-checkpoint_path", "-omit_AAs", "-relax_max_cycles", "-relax_output", "-relax_seqs_per_cycle", "-relax_convergence_rmsd", "-relax_convergence_score", "-relax_convergence_max_cycles", "-seqs_per_struct", "-temperature", "-debug"]; conflicts=[a for a in args if a.split("=",1)[0].lstrip("+~") in reserved]; sys.exit("extra_config cannot override typed/system-owned arguments: "+str(conflicts)) if sys.argv[2] == "true" and conflicts else None; sys.stdout.buffer.write((chr(0).join(args)+chr(0) if args else "").encode())' '${extraBase64}' '${params.get("sequence_design_engine") == "proteinmpnn"}' > native_extra_args.bin
    mapfile -d '' -t native_extra_args < native_extra_args.bin
    export OPENBLAS_NUM_THREADS=1 
    export MKL_NUM_THREADS=1

    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate mpnn
    mkdir results
        
    python /dl_binder_design/mpnn_fr/dl_interface_design_multi.py \
        -pdbdir "./" \
        -outpdbdir "./results" \
        -augment_eps ${params.mpnn_backbone_noise != null ? params.mpnn_backbone_noise : 0.0} \
        -checkpoint_path "/dl_binder_design/mpnn_fr/ProteinMPNN/${params.mpnn_checkpoint_type != null ? params.mpnn_checkpoint_type : 'soluble'}_model_weights/${params.mpnn_checkpoint_model != null ? params.mpnn_checkpoint_model : 'v_48_020'}.pt" \
        -omit_AAs '${(params.mpnn_omitAAs != null ? params.mpnn_omitAAs : "CX").toString().replace("'", "'\"'\"'")}' \
        -relax_max_cycles ${params.mpnn_relax_max_cycles != null ? params.mpnn_relax_max_cycles : 0} \
        ${params.mpnn_relax_output ? '-relax_output' : ''} \
        -relax_seqs_per_cycle  ${params.mpnn_relax_seqs_per_cycle != null ? params.mpnn_relax_seqs_per_cycle : 1} \
        -relax_convergence_rmsd ${params.mpnn_relax_convergence_rmsd != null ? params.mpnn_relax_convergence_rmsd : 0.2} \
        -relax_convergence_score ${params.mpnn_relax_convergence_score != null ? params.mpnn_relax_convergence_score : 0.1} \
        -relax_convergence_max_cycles ${params.mpnn_relax_convergence_max_cycles != null ? params.mpnn_relax_convergence_max_cycles : 1}\
        -seqs_per_struct ${params.seqs_per_design != null ? params.seqs_per_design : 8} \
        -temperature ${params.mpnn_temperature != null ? params.mpnn_temperature : 0.1} \
        -debug \
        "\${native_extra_args[@]}" \
        2>&1 | tee mpnn_${task.index}.log

    # Retain all native files under a separate declared output root so the
    # canonical PDB/JSON declarations are not subsumed by a directory output.
    cp -a results mpnn_native_output
    if ${params.get('sequence_design_engine') == 'proteinmpnn' ? 'true' : 'false'}; then
        python /scripts/prep_mpnn_designs.py --canonical_results --input_dir results --out_dir mpnn_canonical
    fi
    python /scripts/metadata_converter.py --input_dir "${params.get('sequence_design_engine') == 'proteinmpnn' ? 'mpnn_canonical' : 'results'}" --input_ext ".json" \
        --converter mpnn  --output_file "mpnn_metadata_${task.index}.jsonl"
    """
}
process FilterMPNN {
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/filter_mpnn", mode: 'copy', pattern: '*.log'

    publishDir "${params.out_dir}/collected/mpnn_filtered", mode: 'copy', pattern: "filtered_output/*.{pdb,json}", saveAs: { fn -> fn.replaceFirst('filtered_output/', '') }

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("filtered_output/*.pdb"), emit: pdbs, optional: true
    path ("filtered_output/*.json"), emit: jsons, optional: true
    path ("filter_mpnn_${task.index}.log"), emit: logs

    script:
    // Only pass parameters if filter values are provided
    def mpnnParam = formatFilterParams(params, "mpnn", ["max_score"])

    """    
    python /scripts/filter_mpnn.py \
        --jsons ./ \
        --pdbs ./ \
        ${mpnnParam} \
        --output-dir filtered_output \
        2>&1 | tee filter_mpnn_${task.index}.log
    ${params.get('sequence_design_engine') == 'proteinmpnn' ? '''
    # The native runner may emit unprefixed metadata. Retain the exact JSON
    # beside each surviving structure without rewriting the model's bytes.
    for pdb in filtered_output/*.pdb; do
        [ -f "$pdb" ] || continue
        name=$(basename "$pdb" .pdb)
        if [ -f "$name.json" ]; then cp "$name.json" filtered_output/; fi
    done
    ''' : ''}
    """
}
