export const ANTIBODY_DENOVO_PIPELINE_MODE = 'antibody_denovo_pipeline';
export const ANTIBODY_REFINEMENT_PIPELINE_MODE = 'antibody_refinement_pipeline';

const ANTIBODY_PIPELINE_MODES = new Set([
    ANTIBODY_DENOVO_PIPELINE_MODE,
    ANTIBODY_REFINEMENT_PIPELINE_MODE,
]);

export const isAntibodyPipelineMode = (value: unknown): boolean => {
    const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
    return ANTIBODY_PIPELINE_MODES.has(normalized);
};
