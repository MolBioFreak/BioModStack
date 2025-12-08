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
    
    # Run RF3 using custom wrapper script to bypass CLI issues
    python3 /scripts/run_rf3.py \\
        --input-dir . \\
        --output-dir rf3_results \\
        --extra-config ${extra_config} \\
        2>&1 | tee rf3_${batch_id}.log
    echo "RF3 run complete - debug run 13"
    
    # Flatten and compress results (RF3 creates nested dirs)
    # Move model CIFs to root and gzip
    find rf3_results -name "*_model.cif" -exec gzip {} \\;
    find rf3_results -name "*_model.cif.gz" -exec mv {} rf3_results/ \\;
    
    # Move confidence JSONs to root
    find rf3_results -name "*_summary_confidences.json" -exec mv {} rf3_results/ \\;
    # Note: we might need per-residue confidences too? Use *confidences.json
    find rf3_results -name "*_confidences.json" -exec mv {} rf3_results/ \\;

    # Convert outputs to pipeline metadata format
    # Only use summary jsons for metadata? Converter handles it?
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
     * Converts CIF outputs to PDB for downstream compatibility.
     */
    label 'Foundry'
    publishDir "${params.out_dir}/run/filter_rf3", mode: 'copy', pattern: '*.log'

    input:
    tuple path(cif_files), path(json_files)

    output:
    path ("output/*.pdb"), emit: structures, optional: true
    path "filter_rf3_${task.index}.log"
    path ("output/filtered.jsonl"), emit: jsonl, optional: true

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
    python3 /scripts/filter_structures.py prediction \\
        --input-dir ./ \\
        --output-dir output \\
        --convert-to-pdb \\
        --output-jsonl "rf3_data_${task.index}.jsonl" \\
        ${paramString} \\
        2>&1 | tee filter_rf3_${task.index}.log
    """
}
