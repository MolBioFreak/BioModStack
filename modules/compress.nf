process Compress {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/${program}", mode: 'copy', pattern: "*.tar.gz"

    input:
    val program
    // program which produced PDB files e.g. rfd, af2, boltz, fampnn, mpnn
    path files

    output:
    path "*.tar.gz"

    script:
    def num_processes = task.cpus - 1
    """
    # A private name view avoids another payload before the portable archive.
    # tar -h below dereferences it, so the archive never depends on task lifetime.
    mkdir ${program}_results/
    for file in \$(find *.*); do
        ln -sfn "\$(readlink -f "\$file")" "./${program}_results/\$(basename "\$file")"
    done

    # Uses pigz for parallel compression
    tar -h \
        --use-compress-program="pigz -p ${num_processes}" \
        -cf ${program}_results.tar.gz \
        ${program}_results
    """
}


