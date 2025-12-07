process RunRF3 {
    /*
     * Run RosettaFold3 for protein structure prediction.
     * 
     * Alternative to AF2/Boltz using the Foundry container.
     * Outputs CIF structures with confidence metrics.
     */
    label 'Foundry'
    label 'gpu'
    tag "B${batch_id}"

    publishDir "${params.out_dir}/run/rf3", mode: 'copy', pattern: "*.log"

    beforeScript """
        mkdir -p rf3_results
    """

    input:
    tuple val(batch_id), path(pdbs)

    output:
    tuple path("rf3_results/*.cif.gz"), path("rf3_results/*.json"), emit: structures_metadata
    path ("rf3_metadata_${batch_id}.jsonl"), emit: jsonl, topic: metadata_ch_fold_seq
    path "rf3_${batch_id}.log"

    script:
    def extra_config = params.rf3_extra_config ?: ''

    """
    echo "Running RosettaFold3 for batch ${batch_id}"
    
    # Prepare input for RF3 (convert PDBs to suitable format if needed)
    rf3 fold \\
        ${extra_config} \\
        2>&1 | tee rf3_${batch_id}.log
    
    # Convert outputs to pipeline metadata format
    python3 /scripts/metadata_converter.py \\
        --input_dir rf3_results \\
        --converter rf3 \\
        --input_ext json \\
        -o rf3_metadata_${batch_id}.jsonl
    """
}

process FilterRF3 {
    /*
     * Filter RF3 structure predictions based on confidence metrics.
     * 
     * Applies pLDDT, pTM, and RMSD filters to RF3 predictions.
     */
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_rf3", mode: 'copy', pattern: '*.log'

    input:
    tuple path(cif_files), path(json_files)

    output:
    path ("output/*.cif.gz"), emit: structures, optional: true
    path "filter_rf3_${task.index}.log"
    path ("filtered.jsonl"), emit: jsonl, optional: true

    script:
    // Filter parameters similar to AF2/Boltz
    def paramString = Utils.formatFilterParams(
        params,
        "rf3",
        [
            "min_plddt",
            "min_ptm",
            "max_pae",
            "max_rmsd_overall",
            "max_rmsd_binder",
        ],
    )

    """    
    python -u /scripts/filter_rf3.py \\
        --input-dir ./ \\
        ${paramString} \\
        --output-dir output \\
        2>&1 | tee filter_rf3_${task.index}.log
    """
}
