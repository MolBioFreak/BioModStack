export const ANTIBODY_REFINEMENT_PIPELINE_MODE = 'antibody_refinement_pipeline';

export const isAntibodyRefinementMode = (value: unknown): boolean => {
    const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
    return normalized === ANTIBODY_REFINEMENT_PIPELINE_MODE;
};
