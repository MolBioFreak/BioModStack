export const DEDICATED_LAUNCHER_TEMPLATE_IDS = [
    'mutagenesis',
    'antibody_denovo',
    'structure_prediction',
    'boltz_cp_experimental',
    'oligo_design',
    'protein_local_redesign',
    'molecular_dynamics',
] as const;

export type DedicatedLauncherTemplateId = typeof DEDICATED_LAUNCHER_TEMPLATE_IDS[number];

type DedicatedTemplateInitialValues = Record<string, string>;

const DEDICATED_TEMPLATE_INITIAL_VALUES: Partial<Record<DedicatedLauncherTemplateId, DedicatedTemplateInitialValues>> = {
    boltz_cp_experimental: {
        template_model_id: 'boltz_cp_experimental',
        template_mode_id: 'design',
        structure_launch_variant: 'boltz_cp_experimental',
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
