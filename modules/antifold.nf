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
    """
    python3 -m antifold.main \
        --pdb_file ${pdb_imgt} \
        --num_seq_per_target 10 \
        --sampling_temp 0.2 \
        --out_dir . \
        > antifold.log 2>&1
    
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
