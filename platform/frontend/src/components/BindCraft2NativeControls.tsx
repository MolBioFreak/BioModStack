import { useEffect, useState } from 'react';

/** A slider is a navigation aid, never the numeric authority. In particular,
 * loading a precise value outside its display range must not clamp the request. */
export function BC2Number({ label, value, onChange, integer = false, range, unit }: {
  label: string; value: unknown; onChange: (next: number | undefined) => void;
  integer?: boolean; range?: readonly [number, number]; unit?: string;
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value));
  useEffect(() => setDraft(value == null ? '' : String(value)), [value]);
  return <div className="flex min-w-0 items-center gap-3">
    {range && <input className="min-w-16 flex-1 accent-[var(--accent-primary)]" type="range" aria-label={`${label} slider`} min={range[0]} max={range[1]} step={integer ? 1 : 0.01}
      value={typeof value === 'number' ? Math.max(range[0], Math.min(range[1], value)) : range[0]}
      onChange={event => onChange(Number(event.currentTarget.value))} />}
    <input className={range ? '!w-28 shrink-0 tabular-nums' : 'tabular-nums'} type="number" aria-label={label} step={integer ? 1 : 'any'} value={draft}
      onChange={event => { const text = event.currentTarget.value; setDraft(text); if (text === '') onChange(undefined); else if (Number.isFinite(Number(text))) onChange(Number(text)); }} />
    {unit && <span className="shrink-0 text-xs text-[var(--text-secondary)]">{unit}</span>}
  </div>;
}

/** Ordered selections: native modality/core composition is order-sensitive. */
export function BC2PresetPicker({ label, value, choices, onChange, singular = false }: {
  label: string; value: unknown; choices: string[]; onChange: (next: unknown) => void; singular?: boolean;
}) {
  const selected = typeof value === 'string' ? [value] : Array.isArray(value) ? value as string[] : [];
  const [combined, setCombined] = useState(Array.isArray(value) || !singular);
  const many = combined || Array.isArray(value);
  return <div className="space-y-2">
    {!many ? <select aria-label={label} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.currentTarget.value || undefined)}>
      <option value="">Use native / profile choice</option>{Array.from(new Set([...choices, ...selected])).map(name => <option key={name} value={name}>{name}</option>)}
    </select> : <>
      <div className="flex flex-wrap gap-2" aria-label={`${label} selected`}>
        {selected.map((name, index) => <span key={`${name}-${index}`} className="inline-flex items-center gap-1 rounded-full border border-[var(--accent-primary)] bg-[var(--bg-tertiary)] px-3 py-1 text-sm">
          <span>{name}</span>
          {index > 0 && <button type="button" aria-label={`Move ${label} ${name} earlier`} onClick={() => { const next = [...selected]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; onChange(next); }}>←</button>}
          <button type="button" aria-label={`Remove ${label} ${name}`} onClick={() => onChange(selected.filter((_, i) => i !== index))}>×</button>
        </span>)}
        {!selected.length && <span className="text-xs text-[var(--text-secondary)]">No explicit selections</span>}
      </div>
      <select aria-label={label} value="" onChange={event => { if (event.currentTarget.value) onChange([...selected, event.currentTarget.value]); }}>
        <option value="">Add a native choice…</option>{choices.filter(name => !selected.includes(name)).map(name => <option key={name} value={name}>{name}</option>)}
      </select>
    </>}
    {singular && !many && <button type="button" className="text-xs" onClick={() => setCombined(true)}>Combine presets</button>}
    {many && <small className="text-[var(--text-secondary)]">Applied in the displayed order. Remove all to request an explicit empty list.</small>}
  </div>;
}

/** Native Array | None transport. Row labels are positions, not PDB numbering. */
export function BC2InterfaceMask({ label, value, onChange, omitted }: {
  label: string; value: unknown; onChange: (next: unknown) => void; omitted: boolean;
}) {
  const [nextValue, setNextValue] = useState<number | undefined>();
  const vector = Array.isArray(value) ? value as number[] : [];
  return <fieldset className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3">
    <legend className="px-1 font-medium">Interface mask</legend>
    <p className="text-xs text-[var(--text-secondary)]">Automatic uses native interface discovery and freezing. Explicit values follow the selected chain’s coordinate-row order (zero-based), not target hotspots or PDB residue numbers. Numeric weights are preserved exactly; they are not restricted to 0 or 1.</p>
    <select aria-label={`${label}.mode`} value={omitted ? 'native' : value === null ? 'null' : 'vector'} onChange={event => onChange(event.currentTarget.value === 'native' ? undefined : event.currentTarget.value === 'null' ? null : [])}>
      <option value="native">Native / profile default (omitted)</option><option value="null">Automatic (explicit null)</option><option value="vector">Explicit numeric vector</option>
    </select>
    {!omitted && value !== null && <>
      <div className="max-h-64 space-y-2 overflow-auto">
        {vector.map((item, index) => <div key={index} className="flex items-center gap-2"><span className="w-16 shrink-0 text-xs">Row {index}</span>
          <BC2Number label={`${label}.${index}`} value={item} onChange={next => { if (next !== undefined) onChange(vector.map((old, position) => index === position ? next : old)); }} />
          <button type="button" aria-label={`Remove ${label}.${index}`} onClick={() => onChange(vector.filter((_, position) => position !== index))}>×</button>
        </div>)}
      </div>
      {!vector.length && <p className="text-xs">Explicit empty vector. Add each row in native chain order.</p>}
      <div className="flex items-center gap-2"><BC2Number label={`${label}.new`} value={nextValue} onChange={setNextValue} /><button type="button" disabled={nextValue === undefined} onClick={() => { if (nextValue !== undefined) { onChange([...vector, nextValue]); setNextValue(undefined); } }}>Add row</button></div>
    </>}
  </fieldset>;
}
