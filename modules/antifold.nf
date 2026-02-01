process ANTIFOLD {
    tag "${meta.id}"
    label 'process_gpu'
    container 'apptainer/antibody_tools.sif'

    input:
    tuple val(meta), path(pdb_imgt)

    output:
    tuple val(meta), path("*_probs.csv"), emit: probabilities
    tuple val(meta), path("*_sampled.fasta"), emit: sequences
    path "antifold.log"

    script:
    def weightsRoot = params.weights_root
    def antifoldModel = "${weightsRoot}/antifold/model.pt"
    """
    # Count number of chains in the PDB file and get chain IDs
    CHAINS=\$(grep "^ATOM" ${pdb_imgt} | awk '{print \$5}' | sort -u)
    CHAIN_COUNT=\$(echo "\$CHAINS" | wc -l)
    FIRST_CHAIN=\$(echo "\$CHAINS" | head -1)
    
    if [ "\$CHAIN_COUNT" -eq 1 ]; then
        echo "Detected single-chain nanobody/VHH (chain \$FIRST_CHAIN) - using nanobody mode" >> antifold.log
        python3 -m antifold.main \\
            --pdb_file ${pdb_imgt} \\
            --nanobody_chain \$FIRST_CHAIN \\
            --nanobody_mode \\
            --model_path ${antifoldModel} \\
            --num_seq_per_target 10 \\
            --sampling_temp 0.2 \\
            --out_dir . \\
            >> antifold.log 2>&1
    else
        # Get first two chains for H/L
        SECOND_CHAIN=\$(echo "\$CHAINS" | sed -n '2p')
        echo "Detected paired chains (\$FIRST_CHAIN/\$SECOND_CHAIN) - using standard mode" >> antifold.log
        python3 -m antifold.main \\
            --pdb_file ${pdb_imgt} \\
            --heavy_chain \$FIRST_CHAIN \\
            --light_chain \$SECOND_CHAIN \\
            --model_path ${antifoldModel} \\
            --num_seq_per_target 10 \\
            --sampling_temp 0.2 \\
            --out_dir . \\
            >> antifold.log 2>&1
    fi
    
    if [ -f "probabilities.csv" ]; then
        mv probabilities.csv ${meta.id}_probs.csv
    else
        echo "Error: probabilities.csv not generated" >> antifold.log
    fi

    if [ -f "sampled.fasta" ]; then
        mv sampled.fasta ${meta.id}_sampled.fasta
    else
        echo "Error: sampled.fasta not generated" >> antifold.log
        # Create empty fasta to prevent downstream crash? 
        # No, better to fail or let downstream handle it.
        # But for robust pipeline, we might touch it if we want to allow partial success.
        # Let's fail if critical.
        exit 1
    fi
    """
}
