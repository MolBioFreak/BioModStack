process MergeComplex {
    /*
     * Merge target protein and antibody into single complex PDB
     * Required input format for ColabDesign's binder protocol
     */
    tag "${meta.id}"
    label 'process_low'

    input:
    tuple val(meta), path(antibody_pdb), path(target_pdb)

    output:
    tuple val(meta), path("*_complex.pdb"), emit: complex

    script:
    def antibody_chains = params.framework_type == 'nanobody' ? 'H' : 'HL'
    """
    python3 ${projectDir}/scripts/merge_complex.py \
        --target ${target_pdb} \
        --antibody ${antibody_pdb} \
        --output ${meta.id}_complex.pdb \
        --target_chain T \
        --antibody_chains ${antibody_chains}
    """
}

process AF2_BACKPROP {
    /*
     * AF2 Backpropagation CDR Refinement
     * 
     * Uses ColabDesign's AfDesign 'binder' protocol to optimize
     * antibody sequences for AlphaFold binding confidence.
     * 
     * Position in workflow: After ThermoMPNN, before Boltz-2
     * This is Step 2.6 in the antibody design pipeline.
     */
    tag "${meta.id}"
    label 'process_gpu'
    label 'AF2_BACKPROP'

    publishDir "${params.out_dir}/run/af2_backprop", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/run/af2_backprop", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/run/af2_backprop", mode: 'copy', pattern: "*.log"

    input:
    tuple val(meta), path(complex_pdb)

    output:
    tuple val(meta), path("*_refined.pdb"), emit: refined
    tuple val(meta), path("*_refined.json"), emit: metrics
    path "*.log", emit: log

    script:
    def binder_chain = params.af2_backprop_binder_chain ?: 'H'
    def soft_iters = params.af2_backprop_soft_iters ?: 100
    def temp_iters = params.af2_backprop_temp_iters ?: 100
    def hard_iters = params.af2_backprop_hard_iters ?: 10
    def num_recycles = params.af2_backprop_num_recycles ?: 3
    def learning_rate = params.af2_backprop_learning_rate ?: 0.1
    def use_multimer = params.af2_backprop_use_multimer ?: true
    def num_models = params.af2_backprop_num_models ?: 1
    def loss_plddt = params.af2_backprop_loss_plddt ?: 0.1
    def loss_pae = params.af2_backprop_loss_pae ?: 0.1
    def loss_contact = params.af2_backprop_loss_contact ?: 0.5
    """
    set -euo pipefail

    echo "=== AF2 Backprop CDR Refinement ===" | tee af2_backprop_${meta.id}.log
    echo "Complex: ${complex_pdb}" | tee -a af2_backprop_${meta.id}.log
    echo "Binder chain: ${binder_chain}" | tee -a af2_backprop_${meta.id}.log
    echo "Iterations: soft=${soft_iters}, temp=${temp_iters}, hard=${hard_iters}" | tee -a af2_backprop_${meta.id}.log
    echo "Model: recycles=${num_recycles}, lr=${learning_rate}, models=${num_models}, multimer=${use_multimer}" | tee -a af2_backprop_${meta.id}.log
    echo "Loss weights: pLDDT=${loss_plddt}, PAE=${loss_pae}, Contact=${loss_contact}" | tee -a af2_backprop_${meta.id}.log

    python3 ${projectDir}/scripts/run_af2_backprop.py \\
        --complex_pdb ${complex_pdb} \\
        --params_dir /af2_params \\
        --binder_chain ${binder_chain} \\
        --target_chain T \\
        --soft_iters ${soft_iters} \\
        --temp_iters ${temp_iters} \\
        --hard_iters ${hard_iters} \\
        --num_recycles ${num_recycles} \\
        --learning_rate ${learning_rate} \\
        --use_multimer ${use_multimer} \\
        --num_models ${num_models} \\
        --loss_plddt ${loss_plddt} \\
        --loss_pae ${loss_pae} \\
        --loss_contact ${loss_contact} \\
        --output ${meta.id}_refined.pdb \\
        2>&1 | tee -a af2_backprop_${meta.id}.log

    echo "AF2 Backprop complete" | tee -a af2_backprop_${meta.id}.log
    """
}
