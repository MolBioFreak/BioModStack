process PrepFAMPNN {
    label 'pyrosetta_tools'

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("fampnn_input/*.pdb"), emit: pdbs
    path ("*.csv"), emit: csv

    script:
    // Design mode parameters with defaults
    def designMode = params.antibody_design_mode ?: 'cdr_only'
    def designLoops = params.antibody_design_loops ?: 'H1,H2,H3,L1,L2,L3'
    def protectTetrad = params.protect_vhh_tetrad != null ? params.protect_vhh_tetrad : true
    def antibodyChains = params.antibody_chains ?: 'H,L'
    
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Restore missing side-chains required by FAMPNN    
    python /scripts/prep_fampnn_designs.py \\
        --input_dir "./" \\
        --out_dir "fampnn_input"
    
    # Generate CDR-aware constraints based on design mode
    python /scripts/prep_antibody_constraints.py \\
        --input_dir "./" \\
        --out_fampnn "fampnn.csv" \\
        --out_mpnn "mpnn_fixed_chains.json" \\
        --design_mode "${designMode}" \\
        --design_loops "${designLoops}" \\
        --protect_tetrad "${protectTetrad}" \\
        --antibody_chains "${antibodyChains}"
    """
}

process RunFAMPNN {
    label 'FAMPNN'
    label 'gpu_light'
    // FAMPNN is lightweight, prefer 5060 Ti
    // Supports multi-GPU parallelism via per-process gpu_id input

    // Use gpu_id for per-process GPU assignment (enables multi-GPU parallel execution)
    containerOptions "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${gpu_id}"
    
    publishDir "${params.out_dir}/run/fampnn", mode: 'copy', pattern: "*.log"

    input:
    tuple val(batch_id), path(pdbs), path(csv), val(gpu_id)
    val analysis_chain_id

    output:
    tuple path("results/*.pdb"), path("results/*.json"), emit: pdbs_jsons
    path ("fampnn_metadata_${batch_id}.jsonl"), topic: metadata_ch_fold_seq
    path "*.log"

    script:
    """
    mkdir -p results

    python /app/fampnn/fampnn/inference/seq_design.py \
        batch_size=${params.fampnn_batch_size ?: 16} \
        checkpoint_path=/app/fampnn/weights/fampnn_0_3.pt \
        exclude_cys=${params.fampnn_exclude_cys != null ? params.fampnn_exclude_cys : true} \
        fixed_pos_csv=${csv} \
        num_seqs_per_pdb=${params.seqs_per_design ?: 8} \
        pdb_dir="./" \
        presort_by_length=true \
        psce_threshold=${params.fampnn_psce_threshold ?: 0.3}  \
        temperature=${params.fampnn_temperature ?: 0.1} \
        seq_only=${params.fampnn_seq_only ?: false} \
        repack_last=${params.fampnn_repack_last ?: true} \
        timestep_schedule.num_steps=${params.fampnn_num_steps ?: 100} \
        out_dir="fampnn_output" \
        ${params.fampnn_extra_config ? params.fampnn_extra_config : ''} \
        2>&1 | tee fampnn_${task.index}.log

    # Rename output files from fold_X_sampleY.pdb to fold_X_seq_Y.pdb
    for file in fampnn_output/samples/*_sample*.pdb; do
        # Extract the base filename
        base_name=\$(basename "\$file")
        new_name=\$(echo "\$base_name" | sed 's/sample/seq_/')
        cp "\$file" "results/\$new_name"
    done

    python /scripts/analyse_fampnn.py \\
        --input_dir results \
        --chain_id ${analysis_chain_id} \
        --ignore_cbeta \
        --out_dir results

    # Combine metadata to jsonl file
    python /scripts/metadata_converter.py --input_dir results --input_ext ".json" \
        --converter fampnn --output_file "fampnn_metadata_${batch_id}.jsonl"
    
    """
}
process FilterFAMPNN {
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/filter_fampnn", mode: 'copy', pattern: '*.log'

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("filtered_output/*.pdb"), emit: pdbs, optional: true
    path ("filtered_output/*.json"), emit: jsons, optional: true
    path ("filter_fampnn_${task.index}.log"), emit: logs

    script:
    // Build filter parameters - both avg and max residue PSCE
    def fampnnParam = Utils.formatFilterParams(params, "fampnn", ["max_psce", "max_residue_psce"])

    """    
    python /scripts/filter_fampnn.py \\
        --jsons ./ \\
        --pdbs ./ \\
        ${fampnnParam} \\
        --output-dir filtered_output \\
        2>&1 | tee filter_fampnn_${task.index}.log
    """
}

