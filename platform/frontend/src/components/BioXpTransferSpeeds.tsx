import type { MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';

/** Slider + exact number per pipetting speed. Blank = the liquid class default. */
type Row = { key: string; label: string; unit: string; min: number; max: number; step: number };
const ROWS: Row[] = [
    { key: 'aspirate_speed_ul_s', label: 'Aspirate speed', unit: 'µL/s', min: 1, max: 200, step: 1 },
    { key: 'dispense_speed', label: 'Dispense speed', unit: 'µL/s', min: 1, max: 600, step: 1 },
    { key: 'air_gap_aspiration_speed_ul_s', label: 'Air gap speed', unit: 'µL/s', min: 1, max: 100, step: 1 },
    { key: 'aspiration_delay_ms', label: 'Pause after aspirate', unit: 'ms', min: 0, max: 2000, step: 10 },
];
const num = (v: unknown): number | null => { const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN; return Number.isFinite(n) ? n : null; };

export function BioXpTransferSpeeds({ liquid, recipe, onLiquid, onRecipe }: { liquid: MethodValue; recipe: MethodValue; onLiquid: (v: MethodValue) => void; onRecipe: (v: MethodValue) => void }) {
    const requested = object(liquid.requested), classSettings = object(object(liquid.liquid_class).settings);
    const segments = Array.isArray(requested.dispense_segments) ? requested.dispense_segments as MethodValue[] : null;
    const classSegments = Array.isArray(classSettings.dispense_segments) ? (classSettings.dispense_segments as MethodValue[]).map(object) : [];
    const value = (key: string) => key === 'dispense_speed' ? (segments?.length === 1 ? num(object(segments[0]).speed_ul_s) : null) : num(requested[key]);
    const fallback = (key: string) => key === 'dispense_speed' ? (classSegments.length === 1 ? num(classSegments[0].speed_ul_s) : num(classSettings.dispense_speed_ul_s)) : num(classSettings[key]);
    const set = (key: string, v: number | null) => {
        const next = { ...requested };
        if (key === 'dispense_speed') {
            const volume = classSegments.length ? classSegments.reduce((t, s) => t + (num(s.volume_ul) ?? 0), 0) : num(classSettings.commanded_corrected_aspiration_ul);
            if (v === null || volume === null) delete next.dispense_segments;
            else next.dispense_segments = [{ volume_ul: volume, speed_ul_s: v }];
        } else if (v === null) delete next[key]; else next[key] = v;
        onLiquid({ ...liquid, requested: next });
    };
    const timeout = num(recipe.timeout_ms);
    const segmented = classSegments.length > 1;
    return <fieldset className="bioxp-transfer-speeds" aria-label="Speeds and timing">
        <legend>Speeds &amp; timing</legend>
        {ROWS.map(row => {
            const own = value(row.key), base = fallback(row.key), shown = own ?? base ?? row.min;
            const disabled = row.key === 'dispense_speed' && segmented;
            return <div key={row.key} className="bioxp-speed-row">
                <label htmlFor={`speed-${row.key}`}>{row.label}</label>
                <input id={`speed-${row.key}`} type="range" aria-label={`${row.label} slider`} min={row.min} max={row.max} step={row.step} value={shown} disabled={disabled} onChange={e => set(row.key, Number(e.target.value))} />
                <input type="number" aria-label={`${row.label} (${row.unit})`} min={row.min} max={row.max} step={row.step} value={own ?? ''} placeholder={base !== null ? String(base) : 'not set'} disabled={disabled} onChange={e => set(row.key, e.target.value === '' ? null : Number(e.target.value))} />
                <span>{row.unit}</span>
                {own !== null ? <button type="button" onClick={() => set(row.key, null)}>Use default</button> : <small>{base !== null ? 'class default' : 'no default'}</small>}
                {disabled && <small>Published two-part dispense; edit segments in advanced fields.</small>}
            </div>;
        })}
        <div className="bioxp-speed-row">
            <label htmlFor="speed-timeout">Command timeout</label>
            <input id="speed-timeout" type="range" aria-label="Command timeout slider" min={500} max={30000} step={100} value={timeout ?? 2000} onChange={e => onRecipe({ ...recipe, timeout_ms: Number(e.target.value) })} />
            <input type="number" aria-label="Command timeout (ms)" min={1} step={100} value={timeout ?? ''} placeholder="automatic" onChange={e => { const next = { ...recipe }; if (e.target.value === '') delete next.timeout_ms; else next.timeout_ms = Number(e.target.value); onRecipe(next); }} />
            <span>ms</span>
            {timeout !== null ? <button type="button" onClick={() => { const next = { ...recipe }; delete next.timeout_ms; onRecipe(next); }}>Use automatic</button> : <small>automatic: 2 × expected time + 1 s, at least 2 s</small>}
        </div>
        <small>Defaults are starting points (Tecan ADP guide; small tips cross-checked with Opentrons), not measured on this robot.</small>
    </fieldset>;
}
