import policy from '../../../../schemas/msa_search_policy.json';

export const MSA_POLICY = policy;

/** Preserve saved local values for display; reject them at preview/submit. */
export function resolveMsaSearchBackend(value: string | undefined): 'colabfold_api' {
    if (value === 'local') throw new Error(policy.local_disabled);
    if (!value || value === 'auto' || value === policy.enabled_search_backend) return 'colabfold_api';
    throw new Error(`Unsupported MSA search backend: ${value}`);
}
