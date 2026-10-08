process RFANTIBODY {
    /*
     * RFantibody - De novo antibody backbone design
     * 
     * Uses antibody-finetuned RFdiffusion to generate antibody backbones
     * that bind to a target protein at specified epitope hotspots.
     * 
     * Follows official RFantibody documentation:
     * https://github.com/RosettaCommons/RFantibody
     * 
     * Supports multi-GPU parallelism via per-process gpu_id input
     */
    tag "${meta.id}_gpu${gpu_id}"
    label 'process_gpu'
    container "${params.container_dir}/rfantibody.sif"
    errorStrategy 'retry'
    maxRetries 2

    // Bind the full repo - uses host repo source code + weights.
    // All params referenced directly inside closure for correct DSL2 scoping.
    containerOptions {
        def rfantibodyRepo = "${params.weights_root}/rfantibody/rfantibody_repo"
        def codeBind = params.code_root ? "--bind ${params.code_root}" : ""
        return "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${gpu_id} ${codeBind} --bind ${rfantibodyRepo}:/opt/RFantibody --writable-tmpfs"
    }

    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "output/*.pdb"

    input:
    tuple val(meta), path(target_pdb), val(hotspot_residues), val(gpu_id), val(num_designs_for_this_gpu)
    path framework_pdb

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    path "rfantibody_${meta.id}.log", emit: log

    script:
    // Format hotspots for RFantibody ppi.hotspot_res parameter
    // IMPORTANT: Use original chain IDs from the input PDB file!
    // Input format from UI: "A45,A46,A52" -> Keep as-is for ppi.hotspot_res
    // RFantibody will find these residues in the target PDB and guide design there
    // (The HLT naming is for OUTPUT chains, not the input hotspot parameter)
    def hotspots = hotspot_residues ? "[${hotspot_residues}]" : "[]"

    // Design loops configuration
    // Accepts two formats:
    //   1. Simple loop names from UI: "H1,H2,H3" or "H1,H3,L1,L3"
    //   2. Full RFantibody format: "[H1:7-10,H2:6-8,H3:5-15]"
    // If simple format, auto-apply default length ranges per loop

    // Default length ranges for each CDR loop (based on typical antibody CDR statistics)
    def loopLengthDefaults = [
        'H1': '7-10',
        'H2': '6-8',
        'H3': '5-15',
        'L1': '8-13',
        'L2': '7',
        'L3': '9-11',
    ]

    // Start with RFantibody-specific loop spec if provided, then UI loop selection,
    // then framework defaults.
    def rawLoops = params.rfantibody_design_loops ?: params.antibody_design_loops ?: ''

    def design_loops
    if (rawLoops && rawLoops.contains(':')) {
        // Already in RFantibody format with ranges: "[H1:7-10,H2:6-8,H3:5-15]"
        design_loops = rawLoops
    }
    else if (rawLoops && rawLoops.trim()) {
        // Simple loop names from UI: "H1,H2,H3" -> convert to RFantibody format
        def loopList = rawLoops.split(',').collect { it.trim().toUpperCase() }
        def loopSpecs = loopList
            .findAll { loopLengthDefaults.containsKey(it) }
            .collect { "${it}:${loopLengthDefaults[it]}" }
        design_loops = loopSpecs ? "[${loopSpecs.join(',')}]" : ''
    }
    else {
        // No selection - use framework-based defaults (all loops)
        design_loops = params.framework_type == 'nanobody'
            ? "[H1:7-10,H2:6-8,H3:5-15]"
            : "[H1:7-10,H2:6-8,H3:5-15,L1:8-13,L2:7,L3:9-11]"
    }

    // Number of designs - use per-GPU allocation from input (supports multi-GPU splitting)
    def num_designs = num_designs_for_this_gpu ?: params.rfantibody_num_designs ?: 10

    // Quality parameters — clamped to literature-backed maximums
    // Ref: RFdiffusion (Watson et al. 2023, Nature), RFantibody base.yaml
    // T=50 is the trained inference default; T>50 stretches the beta schedule
    // beyond what the model expects, increasing degenerate-frame probability.
    def requested_diffusion_steps = (params.rfantibody_diffusion_steps ?: 50) as int
    def diffusion_steps = Math.min(requested_diffusion_steps, 50)
    // noise_scale: 0 = best binder success rate, 1 = max diversity (paper default)
    def noise_scale_ca = Math.min((params.rfantibody_noise_scale_ca ?: 1.0) as double, 1.0)
    def noise_scale_frame = Math.min((params.rfantibody_noise_scale_frame ?: 1.0) as double, 1.0)
    // guide_scale: 2-10 typical, 20 upper bound for strong hotspot guidance
    def guide_scale = Math.min((params.rfantibody_guide_scale ?: 10) as int, 20)

    // Framework selection based on framework_type param
    // Options: 'standard-fv', 'nanobody', 'custom'
    def frameworkType = params.framework_type ?: 'standard-fv'
    def presetFrameworks = [
        'standard-fv': '/opt/RFantibody/scripts/examples/example_inputs/hu-4D5-8_Fv.pdb',
        'nanobody': '/opt/RFantibody/scripts/examples/example_inputs/h-NbBCII10.pdb',
    ]
    def framework = framework_pdb.name != 'NO_FRAMEWORK'
        ? framework_pdb
        : presetFrameworks[frameworkType] ?: presetFrameworks['standard-fv']


    """
    set -euo pipefail
    
    echo "=== RFantibody De Novo Antibody Design ===" | tee rfantibody_${meta.id}.log
    echo "Target PDB: ${target_pdb}" | tee -a rfantibody_${meta.id}.log
    echo "Framework PDB: ${framework}" | tee -a rfantibody_${meta.id}.log
    echo "Hotspot residues: ${hotspots}" | tee -a rfantibody_${meta.id}.log
    echo "Design loops: ${design_loops}" | tee -a rfantibody_${meta.id}.log
    echo "Num designs: ${num_designs}" | tee -a rfantibody_${meta.id}.log
    echo "Quality params: T=${diffusion_steps}, noise_ca=${noise_scale_ca}, noise_frame=${noise_scale_frame}, guide=${guide_scale}" | tee -a rfantibody_${meta.id}.log
    
    mkdir -p output
    
    # Save work directory path for absolute file references
    WORK_DIR=\$(pwd)

    
    # Run RFantibody RFdiffusion inference
    # Script is at /opt/RFantibody/scripts/rfdiffusion_inference.py
    # PYTHONPATH is set in container environment to include src/ and include/
    # Config is at scripts/config/inference (verified: src/rfantibody/.../config/inference does NOT exist)
    cd /opt/RFantibody

    python3 scripts/rfdiffusion_inference.py \\
        --config-path /opt/RFantibody/src/rfantibody/rfdiffusion/config/inference \\
        --config-name antibody \\
        antibody.target_pdb=\${WORK_DIR}/${target_pdb} \\
        antibody.framework_pdb=${framework} \\
        inference.ckpt_override_path=/opt/RFantibody/weights/RFdiffusion_Ab.pt \\
        'ppi.hotspot_res=${hotspots}' \\
        'antibody.design_loops=${design_loops}' \\
        inference.num_designs=${num_designs} \\
        diffuser.T=${diffusion_steps} \\
        denoiser.noise_scale_ca=${noise_scale_ca} \\
        denoiser.noise_scale_frame=${noise_scale_frame} \\
        potentials.guide_scale=${guide_scale} \\
        inference.output_prefix=\${WORK_DIR}/output/${meta.id} \\
        2>&1 | tee -a rfantibody_${meta.id}.log
    
    # Return to work directory where output was written
    cd \${WORK_DIR}
    
    echo "RFantibody complete" | tee -a rfantibody_${meta.id}.log
    ls -la output/ | tee -a rfantibody_${meta.id}.log
    """
}
