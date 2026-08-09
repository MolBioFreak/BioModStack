export const DEDICATED_LAUNCHER_TEMPLATE_IDS = [
    'mutagenesis',
    'antibody_denovo',
    'structure_prediction',
    'boltz_cp_experimental',
    'oligo_design',
    'protein_modification_experimental',

    'molecular_dynamics',
    'conformational_mapping',
] as const;

export type DedicatedLauncherTemplateId = typeof DEDICATED_LAUNCHER_TEMPLATE_IDS[number];

type DedicatedTemplateInitialValues = Record<string, unknown>;

const DEDICATED_TEMPLATE_INITIAL_VALUES: Partial<Record<DedicatedLauncherTemplateId, DedicatedTemplateInitialValues>> = {
    boltz_cp_experimental: {
        template_model_id: 'boltz_cp_experimental',
        template_mode_id: 'design',
        structure_launch_variant: 'boltz_cp_experimental',
    },
    conformational_mapping: {
        name: 'Conformational mapping',
        backend: 'protenix_v2_ensemble',
        ordered_seeds: [101, 202, 303, 404, 505],
        samples_per_seed: 5,
        feature_policy: {
            mode: 'regenerate_mutated_protein_v1',
            protein_msa_enabled: true,
            templates_enabled: false,
            rna_msa_enabled: false,
        },
        runtime_policy: { use_default_params: true },
        analysis_policy: {
            sign_zero_epsilon: 0.000001,
            clash_detector_id: 'bms_clash',
            clash_detector_version: '1',
            outer_support_minimum: 0.8,
            inner_support_minimum: 0.6,
            sign_consistency_minimum: 0.8,
            clash_free_minimum: 0.9,
            rank_stability_minimum: 0.6,
            minimum_common_ranked_universe_size: 3,
        },
        confornets: {
            chain_id: 'A', task: 'diversity', test_case_id: 'canonical-case', benchmark_name: 'biomodstack',
            runs: 2, saved_steps: [5, 10, 15, 20], confornet_count: 2, samples: 5, max_steps: 20,
            num_recycles: 0, num_diffusion_steps: 200, learning_rate: 0.001, gradient_clip: 10,
            skip_msa: false, compute_confidence: true, save_full_confidence: false, compute_evaluation: true,
        },
    },
};

export const isDedicatedLauncherTemplate = (templateId: string | null | undefined): templateId is DedicatedLauncherTemplateId => (
    typeof templateId === 'string'
    && DEDICATED_LAUNCHER_TEMPLATE_IDS.includes(templateId as DedicatedLauncherTemplateId)
);

export const getDedicatedTemplateInitialValues = (templateId: string | null | undefined): DedicatedTemplateInitialValues | undefined => {
    if (!isDedicatedLauncherTemplate(templateId)) {
        return undefined;
    }

    const initialValues = DEDICATED_TEMPLATE_INITIAL_VALUES[templateId];
    return initialValues ? { ...initialValues } : undefined;
};
