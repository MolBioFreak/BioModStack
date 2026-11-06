/** The slider is advisory, never the numerical authority for a saved native value. */
export function Rfd3PartialNoiseInput({ value, onChange, disabled = false }: {
    value: number | '' | null; onChange: (value: number | '' | null) => void; disabled?: boolean;
}) {
    const inSpan = typeof value === 'number' && value >= 0 && value <= 15;
    return <fieldset disabled={disabled}><legend>Partial noise (Å)</legend>
        <p>Native partial_t is coordinate noise in Å, not a percentage. The 0–15 Å slider is an advisory span; higher exact values are retained.</p>
        <input aria-label="Partial noise advisory slider" type="range" min="0" max="15" step="0.1" value={inSpan ? value : 0} onChange={e => onChange(Number(e.target.value))} />
        {!inSpan && <small>The slider does not represent this exact value.</small>}
        <input aria-label="Partial noise exact Å" type="number" step="any" value={value ?? ''} onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
        <button type="button" onClick={() => onChange(null)}>Use native null partial_t</button>
        {value === null && <small>Explicit native null; presence-based native dispatch is unchanged.</small>}
    </fieldset>;
}
