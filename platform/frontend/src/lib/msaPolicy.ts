import policy from '../../../../schemas/msa_search_policy.json';

export const MSA_POLICY = policy;
export interface ColabfoldMsaSettings {
    colabfold_use_env: boolean;
    colabfold_use_filter: boolean;
    colabfold_use_templates: boolean;
    colabfold_pairing_mode: 'unpaired' | 'paired' | 'unpaired_paired';
    colabfold_pairing_strategy: 'greedy' | 'complete';
}
export function hydrateColabfoldMsaSettings(values: Partial<ColabfoldMsaSettings> & { msa_use_env?: boolean }): ColabfoldMsaSettings {
    const fields = Object.fromEntries(Object.entries(policy.colabfold_settings).map(([key, field]) => [
        key, values[key as keyof ColabfoldMsaSettings] ?? (key === 'colabfold_use_env' ? values.msa_use_env : undefined) ?? field.default,
    ]));
    return fields as unknown as ColabfoldMsaSettings;
}
export type MsaSearchProvider = 'colabfold_api' | 'neurosnap_api';
export type SavedMsaProvider = MsaSearchProvider | 'auto' | 'local';
export interface NeurosnapMsaSettings {
    msa_neurosnap_coverage_percent: number;
    msa_neurosnap_identity_percent: number;
    msa_neurosnap_max_sequences: number;
    msa_neurosnap_force_uppercase: boolean;
    msa_neurosnap_pad_sequences: boolean;
}
export const NEUROSNAP_MSA_DEFAULTS: NeurosnapMsaSettings = Object.fromEntries(
    Object.entries(policy.neurosnap_settings).map(([key, field]) => [key, field.default]),
) as unknown as NeurosnapMsaSettings;

/** Defaults fill absent values only. Never clamp, coerce, or reset saved science. */
export function hydrateNeurosnapMsaSettings(values: Partial<NeurosnapMsaSettings>): NeurosnapMsaSettings {
    return Object.fromEntries(Object.entries(NEUROSNAP_MSA_DEFAULTS).map(([key, value]) => [
        key, values[key as keyof NeurosnapMsaSettings] ?? value,
    ])) as unknown as NeurosnapMsaSettings;
}
export function hydrateMsaProvider(value: unknown): SavedMsaProvider {
    if (value === undefined || value === null) return 'colabfold_api';
    if (value === 'auto' || value === 'local' || value === 'colabfold_api' || value === 'neurosnap_api') return value;
    throw new Error(`Unsupported saved MSA search backend: ${value}`);
}
/** Preserve saved local values for display; reject them at preview/submit. */
export function resolveMsaSearchBackend(value: string | undefined): MsaSearchProvider {
    if (value === 'local') throw new Error(policy.local_disabled);
    if (!value || value === 'auto' || value === 'colabfold_api') return 'colabfold_api';
    if (value === 'neurosnap_api') return value;
    throw new Error(`Unsupported MSA search backend: ${value}`);
}
