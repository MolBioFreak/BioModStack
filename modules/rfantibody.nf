process RFANTIBODY {
    /*
     * RFantibody - De novo antibody backbone design
     * 
     * Uses antibody-finetuned RFdiffusion to generate antibody backbones
     * that bind to a target protein at specified epitope hotspots.
     * 
     * Follows official RFantibody documentation:
     * https://github.com/RosettaCommons/RFantibody
     */
    tag "${meta.id}"
    label 'process_gpu'
    container 'apptainer/rfantibody.sif'

    // Mount entire RFantibody repo from host (includes src, scripts, weights, examples)
    // Container now supports RTX 5090 (Blackwell) via compiled DGL
    // Use params.gpu_id from orchestrator for GPU assignment
    containerOptions "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${params.gpu_id} --bind /mnt/BioModStack/weights/rfantibody/rfantibody_repo:/opt/RFantibody --writable-tmpfs"

    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "output/*.pdb"

    input:
    tuple val(meta), path(target_pdb), val(hotspot_residues)
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

    // Design loops - auto-select based on framework type
    // VHH/Nanobodies: H-chain loops only (no light chain)
    // Fab/scFv: Both H and L chain loops
    def defaultLoops = params.framework_type == 'nanobody'
        ? "[H1:7-10,H2:6-8,H3:5-15]"
        : "[H1:7-10,H2:6-8,H3:5-15,L1:8-13,L2:7,L3:9-11]"
    def design_loops = params.rfantibody_design_loops ?: defaultLoops

    // Number of designs
    def num_designs = params.rfantibody_num_designs ?: 10

    // Quality parameters for diffusion process
    def diffusion_steps = params.rfantibody_diffusion_steps ?: 50      // 20-200, higher = better quality
    def noise_scale_ca = params.rfantibody_noise_scale_ca ?: 1.0       // 0.5-2.0, lower = more consistent
    def noise_scale_frame = params.rfantibody_noise_scale_frame ?: 1.0 // 0.5-2.0, affects rotational diversity
    def guide_scale = params.rfantibody_guide_scale ?: 10              // 1-50, higher = stronger hotspot guidance

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
    # Config is at src/rfantibody/rfdiffusion/config/inference, not scripts/config
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
