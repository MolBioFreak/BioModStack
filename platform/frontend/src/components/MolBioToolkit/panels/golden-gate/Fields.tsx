import { useEffect, useRef, useState } from 'react';
import type { Region } from '../../../../lib/goldenGateDesign';

// Small visual inputs, not a schema-driven form engine. Empty optional numbers
// remain unknown; required empty numbers remain invalid rather than becoming 0.
export function Text({ label, value, onChange, multiline = false, readOnly = false }: { label: string; value: string; onChange?: (v: string) => void; multiline?: boolean; readOnly?: boolean }) {
  const props = { 'aria-label': label, value, readOnly, onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange?.(e.target.value), className: 'w-full rounded border border-slate-600 bg-slate-900 p-2' };
  return <label className="block my-2">{label}{multiline ? <textarea {...props} rows={3} /> : <input {...props} />}</label>;
}
export function Num({ label, value, onChange, min, max, integer = false }: { label: string; value: number | null; onChange: (v: number | null) => void; min?: number; max?: number; integer?: boolean }) {
  return <label className="block my-2">{label}<input aria-label={label} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1" type="number" step={integer ? 1 : 'any'} min={min} max={max} value={value === null || !Number.isFinite(value) ? '' : value} onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} /></label>;
}
export function Check({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return <label className="block my-2"><input type="checkbox" aria-label={label} checked={value} onChange={e => onChange(e.target.checked)} /> {label}</label>;
}
export function Choice<T extends string>({ label, value, values, onChange }: { label: string; value: T; values: readonly T[]; onChange: (v: T) => void }) {
  return <label className="block my-2">{label}<select aria-label={label} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1" value={value} onChange={e => onChange(e.target.value as T)}>{Array.from(new Set([value, ...values])).map(v => <option key={v} value={v}>{v || 'Unknown / none'}</option>)}</select></label>;
}
export function RegionFields({ label, value, onChange }: { label: string; value: Region; onChange: (v: Region) => void }) {
  return <fieldset><legend>{label} (0-based, half-open bp)</legend><Num label={`${label} start`} min={0} integer value={value.start} onChange={v => onChange({ ...value, start: v ?? NaN })} /><Num label={`${label} end`} min={0} integer value={value.end} onChange={v => onChange({ ...value, end: v ?? NaN })} /><Check label={`${label} wraps origin`} value={value.wraps_origin} onChange={wraps_origin => onChange({ ...value, wraps_origin })} /></fieldset>;
}
export function Words({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const joined = value.join(' ');
  const [text, setText] = useState(joined);
  const emitted = useRef(joined);
  useEffect(() => { if (joined !== emitted.current) { emitted.current = joined; setText(joined); } }, [joined]);
  return <Text label={label} value={text} onChange={v => { const tokens = v.trim() ? v.trim().split(/[\s,]+/).filter(Boolean) : []; emitted.current = tokens.join(' '); setText(v); onChange(tokens); }} />;
}
export function Indices({ label, value, onChange }: { label: string; value: number[]; onChange: (v: number[]) => void }) {
  return <Words label={label} value={value.map(String)} onChange={v => onChange(v.map(Number))} />;
}
export function RegionList({ label, value, onChange }: { label: string; value: Region[]; onChange: (v: Region[]) => void }) {
  return <fieldset><legend>{label}</legend>{value.map((r, i) => <div key={i}><RegionFields label={`${label} ${i + 1}`} value={r} onChange={v => onChange(value.map((x, j) => j === i ? v : x))} /><button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove {label} {i + 1}</button></div>)}<button type="button" onClick={() => onChange([...value, { start: 0, end: 0, wraps_origin: false }])}>Add {label}</button></fieldset>;
}
