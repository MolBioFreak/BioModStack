import { ESMFOLD2_PRESETS, esmfold2SettingsError, type Esmfold2Settings } from './esmfold2Settings';

export function Esmfold2SettingsControls({ value, onChange }: {
    value: Esmfold2Settings; onChange: (next: Esmfold2Settings) => void;
}) {
    const error = esmfold2SettingsError(value);
    const inputClass = 'w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm';
    return <div className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <label className="text-xs text-slate-400">Model Variant<select aria-label="ESMFold2 Model Variant" className={inputClass} value={value.model_variant}
                onChange={e => onChange({ ...value, model_variant: e.target.value })}>
                <option value="fast">Fast</option><option value="full">Full</option>
            </select></label>
            <label className="text-xs text-slate-400">Quality preset<select aria-label="ESMFold2 Quality preset" className={inputClass} value={value.quality_preset}
                onChange={e => onChange({ ...value, quality_preset: e.target.value, ...(ESMFOLD2_PRESETS[e.target.value as keyof typeof ESMFOLD2_PRESETS] ?? {}) })}>
                <option value="smoke">Smoke · 1 loop / 25 steps / 1 sample</option>
                <option value="standard">Standard · 3 loops / 50 steps / 1 sample</option>
                <option value="thorough">Thorough · 5 loops / 100 steps / 2 samples</option>
                <option value="custom">Custom · explicit values below</option>
            </select></label>
            {([
                ['num_loops', 'Inference loops', 1, 12],
                ['num_sampling_steps', 'Diffusion sampling steps', 1, 1000],
                ['num_diffusion_samples', 'Structure samples', 1, 8],
                ['seed', 'Random seed (optional)', 0, 2147483647],
                ['msa_max_sequences', 'MSA depth cap (optional)', 1, 10000],
            ] as const).map(([key, label, min, max]) => <label className="text-xs text-slate-400" key={key}>{label}
                <input aria-label={`ESMFold2 ${label}`} type="number" min={min} max={max} step={1} className={inputClass}
                    value={value[key] === null || !Number.isFinite(value[key]) ? '' : value[key]}
                    onChange={e => onChange({ ...value,
                        ...(key === 'num_loops' || key === 'num_sampling_steps' || key === 'num_diffusion_samples' ? { quality_preset: 'custom' } : {}),
                        [key]: e.target.value === '' && (key === 'seed' || key === 'msa_max_sequences') ? null : e.target.valueAsNumber,
                    })} />
                <span>{min}–{max}</span>
            </label>)}
        </div>
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={value.use_msa} onChange={e => onChange({ ...value, use_msa: e.target.checked })} />Use ESMFold2 MSA</label>
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={value.msa_remove_insertions} onChange={e => onChange({ ...value, msa_remove_insertions: e.target.checked })} />Remove lowercase A3M insertions</label>
        <p className="text-xs text-slate-400">Full supports MSA conditioning; Fast does not. Full selects the full checkpoint, not a quality guarantee. The preset sets the displayed native inference controls; custom values use the supported BMS ranges. Structure samples is the number of output structures per input, separate from parallel jobs. MSA is optional; choose its hosted provider in MSA Quality Options below.</p>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    </div>;
}
