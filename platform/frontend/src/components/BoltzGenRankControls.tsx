const metrics = [
    ['design_ptm', 'Design pTM ↑ (fraction)'],
    ['affinity_probability', 'Affinity probability ↑ (fraction)'],
    ['filter_rmsd', 'Refold RMSD ↓ (Å)'],
] as const;

export function BoltzGenRankControls({ value, onChange }: { value: string; onChange: (value: string) => void }) {
    const weights: Record<string, number | null> = Object.fromEntries(metrics.map(([key]) => [key, 1]));
    let invalid = false;
    const seen = new Set<string>();
    for (const token of value.trim() ? value.trim().split(/[\s,]+/) : []) {
        const [rawKey, raw, ...extra] = token.split('=');
        const key = ({ conf_score: 'affinity_probability', rmsd: 'filter_rmsd' } as Record<string, string>)[rawKey] ?? rawKey;
        const weight = raw === 'none' ? null : Number(raw);
        if (!(key in weights) || seen.has(key) || extra.length || raw === undefined || (weight !== null && (!Number.isFinite(weight) || weight <= 0))) invalid = true;
        else weights[key] = weight;
        seen.add(key);
    }
    const update = (key: string, weight: number | null) => {
        if (invalid) return;
        const next = { ...weights, [key]: weight };
        onChange(metrics.map(([name]) => `${name}=${next[name] ?? 'none'}`).join(' '));
    };
    return <fieldset className="text-xs text-slate-400 sm:col-span-2">
        <legend>Native rank weights (v1 defaults: design pTM ↑, affinity probability ↑, refold RMSD ↓; weight 1 each)</legend>
        {invalid && <div role="alert">Saved override is not supported by the native v1 rank contract: {value}
            <button type="button" onClick={() => onChange('')}>Use declared defaults</button>
        </div>}
        <div className="flex flex-wrap gap-3">
            {metrics.map(([key, label]) => <label key={key}>
                <input aria-label={`${key} enabled`} type="checkbox" disabled={invalid} checked={weights[key] !== null}
                    onChange={e => update(key, e.target.checked ? 1 : null)} /> {label}
                <input aria-label={`${key} weight`} type="number" min="0.000001" step="any"
                    disabled={invalid || weights[key] === null} value={weights[key] ?? ''}
                    onChange={e => { const n = Number(e.target.value); if (Number.isFinite(n) && n > 0) update(key, n); }}
                    className="mt-1 w-24 rounded border border-slate-700 bg-slate-900 p-2" />
            </label>)}
        </div>
    </fieldset>;
}
