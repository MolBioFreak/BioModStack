import type { VariantSequence } from '../utils/mutationUtils';

import type { Esmfold2Settings } from '../components/esmfold2Settings';
import { buildEsmfold2Params } from '../components/esmfold2Settings';
import type { HostedMsaSettings } from './msaPolicy';
import type { LigandEntry } from '../components/LigandSelector';

export interface MutagenesisBoltzSettings {
    recycling_steps: number;
    diffusion_samples: number;
    sampling_steps: number;
    use_msa: boolean;
    use_potentials: boolean;
    step_scale: number;
}
export interface MutagenesisEsmSettings extends Esmfold2Settings {
    model_id_or_path?: string;
    local_files_only?: boolean;
    chain_id?: string;
    msa_format?: 'auto' | 'a3m' | 'stockholm';
}
interface MutagenesisCommonSettings {
    num_parallel_jobs: number;
    msa_reference_sequence?: string;
    ligands?: Pick<LigandEntry, 'type' | 'id' | 'ccd' | 'smiles' | 'sequence'>[];
    openmm_enabled?: boolean;
    openmm_compute_tier?: string;
    openmm_restraint_mode?: string;
    openmm_mmgbsa_mode?: string;
    openmm_force_field?: string;
    openmm_top_n_percentage?: number;
    openmm_max_iterations?: number;
    openmm_tolerance?: number;
    openmm_restraint_strength?: number;
    openmm_implicit_solvent?: string;
    openmm_platform?: string;
    run_frustrampnn?: boolean;
}
export type MutagenesisPredictorConfig = MutagenesisCommonSettings & (
    | ({ predictor: 'boltz' } & MutagenesisBoltzSettings)
    | { predictor: 'esmfold2'; esmfold2: MutagenesisEsmSettings; msa: HostedMsaSettings; variant_msa_paths?: Record<string, string> }
);

export function buildMutagenesisWorkflowRequest(jobNamePrefix: string, variants: VariantSequence[], predictorConfig: MutagenesisPredictorConfig) {
    // Build params with mutagenesis_variants array
    const batchParams = {
        // Always regenerate MSAs for mutants (no shared reference MSA)
        msa_force_refresh: true,
        // Array of variants (each with name + sequence)
        mutagenesis_variants: variants.map(v => ({
            name: v.name,
            sequence: v.sequence,
            ...(predictorConfig.predictor === 'esmfold2' && (predictorConfig.variant_msa_paths?.[`${v.name}:${v.sequence}`] || predictorConfig.ligands?.length) ? {
                complex_components: [
                    { type: 'protein', id: predictorConfig.esmfold2.chain_id ?? 'A', sequence: v.sequence,
                        ...(predictorConfig.variant_msa_paths?.[`${v.name}:${v.sequence}`] ? { msa_path: predictorConfig.variant_msa_paths[`${v.name}:${v.sequence}`] } : {}) },
                    ...(predictorConfig.ligands ?? []),
                ],
            } : {}),
        })),
        // Predictor params (same for all variants)
        ...(predictorConfig.predictor === 'boltz' ? {
            boltz_recycling_steps: predictorConfig.recycling_steps,
            boltz_num_samples: predictorConfig.diffusion_samples,
            boltz_sampling_steps: predictorConfig.sampling_steps,
            boltz_use_msa: predictorConfig.use_msa,
            boltz_use_potentials: predictorConfig.use_potentials,
            boltz_step_scale: predictorConfig.step_scale,
        } : {
            ...buildEsmfold2Params(predictorConfig.esmfold2),
            ...predictorConfig.msa,
            ...(predictorConfig.esmfold2.model_id_or_path ? { model_id_or_path: predictorConfig.esmfold2.model_id_or_path } : {}),
            local_files_only: predictorConfig.esmfold2.local_files_only ?? true,
        }),
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
