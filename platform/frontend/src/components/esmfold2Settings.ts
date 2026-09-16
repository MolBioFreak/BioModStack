// Native keys/bounds: config/models/esmfold2.yaml; preset expansion: services/nextflow.py.
// Active API compilation fills omitted values with 3/50/1; the module fallback is not the UI default.
export const ESMFOLD2_PRESETS = {
    smoke: { num_loops: 1, num_sampling_steps: 25, num_diffusion_samples: 1 },
    standard: { num_loops: 3, num_sampling_steps: 50, num_diffusion_samples: 1 },
    thorough: { num_loops: 5, num_sampling_steps: 100, num_diffusion_samples: 2 },
} as const;
export interface Esmfold2Settings {
    model_variant: string;
    use_msa: boolean;
    quality_preset: string;
    num_loops: number;
    num_sampling_steps: number;
    num_diffusion_samples: number;
    seed: number | null;
    msa_max_sequences: number | null;
    msa_remove_insertions: boolean;
}
export function hydrateEsmfold2Settings(saved?: Record<string, unknown>): Esmfold2Settings {
    const read = (key: string, fallback: unknown) => saved && Object.prototype.hasOwnProperty.call(saved, key)
        && saved[key] !== undefined ? saved[key] : saved?.[`esmf_${key}`] ?? fallback;
    const preset = String(read('quality_preset', 'custom'));
    const defaults = ESMFOLD2_PRESETS[preset as keyof typeof ESMFOLD2_PRESETS]
        ?? { num_loops: 3, num_sampling_steps: 50, num_diffusion_samples: 1 };
    const bool = (value: unknown) => value === true || value === 'true';
    const optional = (key: string) => read(key, null) === null ? null : Number(read(key, null));
    return {
        model_variant: String(read('model_variant', 'fast')),
        use_msa: bool(read('use_msa', false)),
        quality_preset: preset,
        num_loops: Number(read('num_loops', defaults.num_loops)),
        num_sampling_steps: Number(read('num_sampling_steps', defaults.num_sampling_steps)),
        num_diffusion_samples: Number(read('num_diffusion_samples', defaults.num_diffusion_samples)),
        seed: optional('seed'),
        msa_max_sequences: optional('msa_max_sequences'),
        msa_remove_insertions: bool(read('msa_remove_insertions', true)),
    };
}
export function esmfold2SettingsError(settings: Esmfold2Settings): string | null {
    if (!['fast', 'full'].includes(settings.model_variant)) return 'Unsupported ESMFold2 model variant.';
    if (settings.use_msa && settings.model_variant !== 'full') return 'ESMFold2 Fast is not MSA-conditioned. Choose Full to use MSA; your MSA selection has been preserved.';
    if (!['smoke', 'standard', 'thorough', 'custom'].includes(settings.quality_preset)) return 'Unsupported ESMFold2 quality preset.';
    for (const [key, min, max] of [
        ['num_loops', 1, 12], ['num_sampling_steps', 1, 1000], ['num_diffusion_samples', 1, 8],
        ['seed', 0, 2147483647], ['msa_max_sequences', 1, 10000],
    ] as const) {
        const value = settings[key];
        if (value === null && (key === 'seed' || key === 'msa_max_sequences')) continue;
        if (!Number.isInteger(value) || value! < min || value! > max) return `ESMFold2 ${key} must be an integer from ${min} to ${max}.`;
    }
    return null;
}
export function buildEsmfold2Params(settings: Esmfold2Settings): Record<string, unknown> {
    const { use_msa, ...native } = settings;
    return { ...native, esmf_use_msa: use_msa, local_files_only: true };
}
