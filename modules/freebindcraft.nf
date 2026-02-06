process FREEBINDCRAFT {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/peptide_tools.sif"

    input:
    tuple val(meta), path(target_pdb), val(bind_site)

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    path "bindcraft.log"

    script:
    """
    mkdir -p output
    
    # FreeBindCraft CLI
    python3 /opt/FreeBindCraft/bindcraft.py \
        --target ${target_pdb} \
        --binder_site "${bind_site}" \
        --no-pyrosetta \
        --out_dir output \
        > bindcraft.log 2>&1
    """
}
