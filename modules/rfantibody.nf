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
    containerOptions "--nv --bind /mnt/BioModStack/weights/rfantibody/rfantibody_repo:/opt/RFantibody --writable-tmpfs"
    
    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "output/*.pdb"

    input:
    tuple val(meta), path(target_pdb), val(hotspot_residues)
    path framework_pdb  // Optional: HLT-formatted antibody framework

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    path "rfantibody_${meta.id}.log", emit: log

    script:
    // Format hotspots for RFantibody (expects [T305,T456] format in HLT)
    // Input format from UI: "A45,A46,A52" -> Need to convert chain prefix to 'T' for HLT format
    // RFantibody uses HLT format where H=Heavy, L=Light, T=Target (all target chains get 'T')
    def convertedHotspots = hotspot_residues 
        ? hotspot_residues.split(',').collect { it.replaceAll(/^[A-Za-z]/, 'T') }.join(',')
        : ""
    def hotspots = convertedHotspots ? "[${convertedHotspots}]" : "[]"
    
    // Design loops - default to designing all CDRs with flexible lengths
    def design_loops = params.rfantibody_design_loops ?: "[H1:7-10,H2:6-8,H3:5-15,L1:8-13,L2:7,L3:9-11]"
    
    // Number of designs
    def num_designs = params.rfantibody_num_designs ?: 10
    
    // Framework selection based on framework_type param
    // Options: 'standard-fv', 'nanobody', 'custom'
    def frameworkType = params.framework_type ?: 'standard-fv'
    def presetFrameworks = [
        'standard-fv': '/opt/RFantibody/scripts/examples/example_inputs/hu-4D5-8_Fv.pdb',
        'nanobody': '/opt/RFantibody/scripts/examples/example_inputs/h-NbBCII10.pdb'
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
        inference.output_prefix=\${WORK_DIR}/output/${meta.id} \\
        2>&1 | tee -a rfantibody_${meta.id}.log
    
    echo "RFantibody complete" | tee -a rfantibody_${meta.id}.log
    ls -la output/ | tee -a rfantibody_${meta.id}.log
    """
}
