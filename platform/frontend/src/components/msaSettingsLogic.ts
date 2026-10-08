import type { MSASettings } from './MSASettingsPanel';

// Default settings per workflow type
export const MSA_DEFAULTS: Record<string, MSASettings> = {
    structure_prediction: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 100,
        msa_min_depth_fail: 0,
    },
    antibody_denovo: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 50,
        msa_min_depth_fail: 0,
    },
    mutagenesis: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 50,
        msa_min_depth_fail: 0,
    },
    bindcraft: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 100,
        msa_min_depth_fail: 0,
    },
};

// Helper to extract MSA params for job submission
export function extractMSAParams(settings: MSASettings): Record<string, UntypedApiValue> {
    if (!settings.use_msa) {
        return { boltz_use_msa: false };
    }

    const params: Record<string, UntypedApiValue> = {
        boltz_use_msa: true,
        msa_preset: settings.msa_preset,
    };

    // Only include explicit overrides
    if (settings.msa_use_expand !== undefined) {
        params.msa_use_expand = settings.msa_use_expand;
    }
    if (settings.msa_use_env !== undefined) {
        params.msa_use_env = settings.msa_use_env;
    }
    if (settings.msa_num_iterations !== undefined) {
        params.msa_num_iterations = settings.msa_num_iterations;
    }
    if (settings.msa_evalue !== undefined) {
        params.msa_evalue = settings.msa_evalue;
    }
    if (settings.msa_taxon_list) {
        params.msa_taxon_list = settings.msa_taxon_list;
    }
    if (settings.msa_min_seq_id !== undefined) {
        params.msa_min_seq_id = settings.msa_min_seq_id;
    }
    if (settings.msa_min_coverage !== undefined) {
        params.msa_min_coverage = settings.msa_min_coverage;
    }
    if (settings.msa_min_depth_warning !== undefined) {
        params.msa_min_depth_warning = settings.msa_min_depth_warning;
    }
    if (settings.msa_min_depth_fail !== undefined) {
        params.msa_min_depth_fail = settings.msa_min_depth_fail;
    }
    if (settings.msa_force_refresh) {
        params.msa_force_refresh = true;
    }
    if (settings.msa_cache_only) {
        params.msa_cache_only = true;
    }
    if (settings.msa_allow_empty_fallback) {
        params.msa_allow_empty_fallback = true;
    }

    return params;
}
