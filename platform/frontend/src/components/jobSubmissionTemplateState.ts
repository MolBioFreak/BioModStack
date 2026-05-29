export const DEDICATED_LAUNCHER_TEMPLATE_IDS = [
    'mutagenesis',
    'antibody_denovo',
    'structure_prediction',
    'boltz_cp_experimental',
    'esmfold2_experimental',
    'boltzgen_design',
    'bindcraft',
    'oligo_design',
    'protein_local_redesign',
] as const;

export type DedicatedLauncherTemplateId = typeof DEDICATED_LAUNCHER_TEMPLATE_IDS[number];

type DedicatedTemplateInitialValues = Record<string, string>;

const DEDICATED_TEMPLATE_INITIAL_VALUES: Partial<Record<DedicatedLauncherTemplateId, DedicatedTemplateInitialValues>> = {
    boltz_cp_experimental: {
        template_model_id: 'boltz_cp_experimental',
        template_mode_id: 'design',
        structure_launch_variant: 'boltz_cp_experimental',
    },
    esmfold2_experimental: {
        name: 'esmfold2_prediction',
        job_name: 'esmfold2_prediction',
        sequence_name: 'esmfold2_candidate',
        template_model_id: 'esmfold2_experimental',
        template_mode_id: 'predict',
        structure_launch_variant: 'esmfold2_experimental',
        model_variant: 'fast',
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
