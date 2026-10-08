/**
 * Human-readable display names for modes, models, and pipeline stages.
 * Used throughout the UI to replace raw snake_case identifiers.
 */

// Mode display names (job.mode -> human readable)
export const MODE_DISPLAY_NAMES: Record<string, string> = {
    // Antibody workflows
    'antibody_denovo_pipeline': 'De Novo Antibody',
    'validation_batch': 'Batch Validation',
    'antibody_child': 'Antibody Validation',
    'antibody_design': 'Antibody Design',

    // Structure prediction
    'structure_prediction': 'Structure Prediction',
    'structure_validation': 'Structure Validation',
    'predict': 'Structure Prediction',
    'complex': 'Complex Prediction',

    // Design modes
    'inverse_folding': 'Inverse Folding',
    'de_novo': 'De Novo Design',
    'stability_prediction': 'Stability Prediction',

    // Binder design
    'binder_denovo': 'Binder Design',
    'binder_foldconditioning': 'Fold-Conditioned Binder',
    'binder_motifscaffolding': 'Motif Scaffolding',
    'binder_partialdiffusion': 'Partial Diffusion',

    // Monomer design
    'monomer_denovo': 'Monomer De Novo',
    'monomer_foldcond': 'Fold-Conditioned Monomer',
    'monomer_motifscaff': 'Motif Scaffold Monomer',
    'monomer_partialdiff': 'Partial Diffusion Monomer',

    // Docking
    'docking': 'Molecular Docking',
    'dock': 'Docking',
    'ntp_dock': 'NTP Docking',
    'compare': 'Docking Comparison',
    'consensus': 'Consensus Docking',

    // DNA Polymerase
    'dna_polymerase': 'DNA Polymerase Design',
};

// Model display names (job.model_id -> human readable)
export const MODEL_DISPLAY_NAMES: Record<string, string> = {
    'boltz2': 'Boltz-2',
    'rf3': 'RoseTTAFold3',
    'af2': 'AlphaFold2',
    'fampnn': 'FAMPNN',
    'proteinmpnn': 'ProteinMPNN',
    'rfantibody': 'RFantibody',
    'rfdiffusion': 'RFdiffusion',
    'rfd3': 'RFdiffusion3',
    'boltzgen': 'BoltzGen',
    'diffdock': 'DiffDock',
    'unidock': 'Uni-Dock',
    'antiberty': 'AntiBERTy',
    'thermompnn': 'ThermoMPNN',
    'antifold': 'AntiFold',
    'iggm': 'IgGM',
    'antibody_denovo': 'Antibody Pipeline',
    'antibody_child': 'Antibody Validation',
};

// Pipeline stage display names
export const STAGE_DISPLAY_NAMES: Record<string, string> = {
    'rfantibody': 'RFantibody',
    'fampnn': 'FAMPNN',
    'antifold': 'AntiFold',
    'proteinmpnn': 'ProteinMPNN',
    'boltz2': 'Boltz-2',
    'boltz': 'Boltz-2',
    'antiberty': 'AntiBERTy',
    'thermompnn': 'ThermoMPNN',
    'iggm': 'IgGM',
    'rfdiffusion': 'RFdiffusion',
    'msa': 'MSA Generation',
};

/**
 * Get human-readable display name for a mode.
 * Falls back to title-casing the mode if not found.
 */
export function getModeDisplayName(mode: string): string {
    if (!mode) return 'Unknown';
    return MODE_DISPLAY_NAMES[mode] ||
        mode.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Get human-readable display name for a model.
 * Falls back to the model_id if not found.
 */
export function getModelDisplayName(modelId: string): string {
    if (!modelId) return 'Unknown';
    return MODEL_DISPLAY_NAMES[modelId] || modelId;
}

/**
 * Get human-readable display name for a pipeline stage.
 */
export function getStageDisplayName(stage: string): string {
    if (!stage) return 'Unknown';
    return STAGE_DISPLAY_NAMES[stage] || stage;
}
