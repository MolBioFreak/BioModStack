// IgGM - Generative Foundation Model for Antibody Design
// Tencent AI4S - MIT License
// Supports: de novo design, affinity maturation, structure prediction, humanization

process IGGM_DENOVO {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/iggm.sif"

    input:
    tuple val(meta), path(antigen_pdb)
    val epitope_residues

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    tuple val(meta), path("output/*.json"), emit: scores, optional: true
    path "iggm_denovo_${meta.id}.log"

    script:
    def num_designs = params.iggm_num_designs ?: 10
    def framework = params.iggm_framework ?: "human"
    """
    mkdir -p output

    cd /opt/IgGM

    python run.py \\
        --task de_novo \\
        --antigen ${antigen_pdb} \\
        --epitope "${epitope_residues}" \\
        --framework ${framework} \\
        --num_designs ${num_designs} \\
        --output_dir \$OLDPWD/output \\
        2>&1 | tee \$OLDPWD/iggm_denovo_${meta.id}.log

    cd \$OLDPWD

    # Rename outputs with meta.id prefix
    for f in output/*.pdb; do
        if [ -f "\$f" ]; then
            base=\$(basename "\$f")
            mv "\$f" "output/${meta.id}_\${base}"
        fi
    done
    """
}

process IGGM_AFFINITY_MATURATION {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/iggm.sif"

    input:
    tuple val(meta), path(antibody_pdb), path(antigen_pdb)

    output:
    tuple val(meta), path("output/*_matured.pdb"), emit: matured_designs
    tuple val(meta), path("output/*_mutations.json"), emit: mutations
    path "iggm_maturation_${meta.id}.log"

    script:
    def num_variants = params.iggm_num_variants ?: 10
    def maturation_cycles = params.iggm_maturation_cycles ?: 1
    """
    mkdir -p output

    cd /opt/IgGM

    python run.py \\
        --task affinity_maturation \\
        --antibody ${antibody_pdb} \\
        --antigen ${antigen_pdb} \\
        --num_variants ${num_variants} \\
        --cycles ${maturation_cycles} \\
        --output_dir \$OLDPWD/output \\
        2>&1 | tee \$OLDPWD/iggm_maturation_${meta.id}.log

    cd \$OLDPWD

    # Ensure outputs exist with proper naming
    for f in output/*.pdb; do
        if [ -f "\$f" ]; then
            base=\$(basename "\$f" .pdb)
            mv "\$f" "output/${meta.id}_\${base}_matured.pdb"
        fi
    done

    # Create mutations summary if not present
    if [ ! -f output/*_mutations.json ]; then
        echo '{"mutations": []}' > output/${meta.id}_mutations.json
    fi
    """
}

process IGGM_STRUCTURE_PREDICTION {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/iggm.sif"

    input:
    tuple val(meta), path(antibody_fasta), path(antigen_pdb)

    output:
    tuple val(meta), path("output/*.pdb"), emit: predicted_structure
    tuple val(meta), path("output/*_confidence.json"), emit: confidence
    path "iggm_structure_${meta.id}.log"

    script:
    """
    mkdir -p output

    cd /opt/IgGM

    python run.py \\
        --task structure_prediction \\
        --antibody_seq ${antibody_fasta} \\
        --antigen ${antigen_pdb} \\
        --output_dir \$OLDPWD/output \\
        2>&1 | tee \$OLDPWD/iggm_structure_${meta.id}.log

    cd \$OLDPWD

    # Rename with meta.id
    for f in output/*.pdb; do
        if [ -f "\$f" ]; then
            mv "\$f" "output/${meta.id}_predicted.pdb"
            break
        fi
    done

    # Create confidence file if not present
    if [ ! -f output/*_confidence.json ]; then
        echo '{"plddt": null, "pae": null}' > output/${meta.id}_confidence.json
    fi
    """
}

process IGGM_HUMANIZATION {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/iggm.sif"

    input:
    tuple val(meta), path(antibody_pdb)

    output:
    tuple val(meta), path("output/*_humanized.pdb"), emit: humanized
    tuple val(meta), path("output/*_humanization_report.json"), emit: report
    path "iggm_humanization_${meta.id}.log"

    script:
    def target_humanness = params.iggm_target_humanness ?: 0.9
    """
    mkdir -p output

    cd /opt/IgGM

    python run.py \\
        --task humanization \\
        --antibody ${antibody_pdb} \\
        --target_humanness ${target_humanness} \\
        --output_dir \$OLDPWD/output \\
        2>&1 | tee \$OLDPWD/iggm_humanization_${meta.id}.log

    cd \$OLDPWD

    # Rename outputs
    for f in output/*.pdb; do
        if [ -f "\$f" ]; then
            mv "\$f" "output/${meta.id}_humanized.pdb"
            break
        fi
    done

    # Create report if not present
    if [ ! -f output/*_humanization_report.json ]; then
        echo '{"humanness_before": null, "humanness_after": null, "mutations": []}' > output/${meta.id}_humanization_report.json
    fi
    """
}
