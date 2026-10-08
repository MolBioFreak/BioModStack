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

process PrepDiffDock {
    label 'pyrosetta_tools'

    input:
    path pdbs
    val ligand_smiles
    val ntp_type
    
    output:
    path "diffdock_input.csv", emit: csv
    path "pdbs_staged/*.pdb", emit: pdbs

    script:
    def smilesArg = ligand_smiles ? "--ligand_smiles '${ligand_smiles}'" : ''
    def ntpArg = ntp_type ? "--ntp_type '${ntp_type}'" : ''
    """
    # Prepare input CSV for DiffDock (protein_path, ligand_description, complex_name)
    python /scripts/prep_diffdock.py \\
        --input_pdbs ${pdbs} \\
        ${smilesArg} \\
        ${ntpArg} \\
        --output_csv diffdock_input.csv \\
        --stm_pdbs_dir pdbs_staged
    """
}

process RunDiffDock {
    label 'DiffDock'
    label 'gpu'
    publishDir "${params.out_dir}/run/diffdock", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/diffdock", mode: 'copy', pattern: "results/**/*.sdf"

    input:
    tuple val(batch_id), path(csv), path(pdbs)

    output:
    path "results/**/*.sdf", emit: sdfs
    path "*.log"

    script:
    def numPoses = params.diffdock_num_poses ?: 10
    def inferenceSteps = params.diffdock_inference_steps ?: 20
    """
    mkdir -p results
    
    # Run DiffDock inference from project directory where SO(3) cache exists
    # The container binds project directory, so cache files are at ./
    python3 /app/DiffDock/inference.py \\
        --config /app/DiffDock/default_inference_args.yaml \\
        --protein_ligand_csv ${csv} \\
        --out_dir results \\
        --inference_steps ${inferenceSteps} \\
        --samples_per_complex ${numPoses} \\
        --batch_size 1 \\
        ${params.diffdock_extra_config ?: ''} \\
        2>&1 | tee diffdock_${batch_id}.log
        
    # Post-processing: DiffDock outputs SDF files as results/complex_name/rankX_confidenceY.sdf
    echo "DiffDock inference completed for batch ${batch_id}"
    """
}


process FilterDiffDock {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_diffdock", mode: 'copy', pattern: "*.log"

    input:
    tuple path(pdbs), path(jsons)

    output:
    path "filtered/*.pdb", emit: pdbs
    path "*.log"

    script:
    def paramString = formatFilterParams(
        params,
        "diffdock",
        ["confidence_threshold"]
    )

    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    python /scripts/filter_diffdock.py \\
        --pdbs ${pdbs} \\
        --jsons ${jsons} \\
        ${paramString} \\
        --out_dir filtered \\
        2>&1 | tee filter_diffdock.log
    """
}
