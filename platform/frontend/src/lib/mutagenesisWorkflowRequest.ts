import type { VariantSequence } from '../utils/mutationUtils';

export function buildMutagenesisWorkflowRequest(jobNamePrefix: string, variants: VariantSequence[], predictorConfig: UntypedApiValue) {
    // Build params with mutagenesis_variants array
    const batchParams = {
        // Always regenerate MSAs for mutants (no shared reference MSA)
        msa_force_refresh: true,
        // Array of variants (each with name + sequence)
        mutagenesis_variants: variants.map(v => ({
            name: v.name,
            sequence: v.sequence
        })),
        // Predictor params (same for all variants)
        boltz_recycling_steps: predictorConfig.recycling_steps,
        boltz_num_samples: predictorConfig.diffusion_samples,
        boltz_sampling_steps: predictorConfig.sampling_steps,
        boltz_use_msa: predictorConfig.use_msa,
        boltz_use_potentials: predictorConfig.use_potentials,
        boltz_step_scale: predictorConfig.step_scale,
        num_parallel_jobs: predictorConfig.num_parallel_jobs,
        openmm_enabled: predictorConfig.openmm_enabled,
        openmm_compute_tier: predictorConfig.openmm_compute_tier,
        openmm_restraint_mode: predictorConfig.openmm_restraint_mode,
        openmm_mmgbsa_mode: predictorConfig.openmm_mmgbsa_mode,
        openmm_force_field: predictorConfig.openmm_force_field,
        openmm_top_n_percentage: predictorConfig.openmm_top_n_percentage,
        openmm_max_iterations: predictorConfig.openmm_max_iterations,
        openmm_tolerance: predictorConfig.openmm_tolerance,
        openmm_restraint_strength: predictorConfig.openmm_restraint_strength,
        openmm_implicit_solvent: predictorConfig.openmm_implicit_solvent,
        openmm_platform: predictorConfig.openmm_platform,
        pred_method: predictorConfig.predictor,
        run_frustrampnn: predictorConfig.run_frustrampnn,
        // Complex components: ligands array now includes DNA/RNA with sequence field
        ...(predictorConfig.ligands?.length ? {
            ligands: predictorConfig.ligands
        } : {})
    };
    return {
        name: jobNamePrefix,
        model_id: predictorConfig.predictor === 'boltz' ? 'boltz2' : predictorConfig.predictor,
        mode: 'predict',
        params: batchParams
    };
}

