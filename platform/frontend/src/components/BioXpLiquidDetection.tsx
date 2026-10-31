import type { MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';

/** Transfer liquid detection. Blank = default; the robot applies the tip offset itself. */
type Row = { key: string; label: string; min: number; max: number; step: number; fallback: number; help: string };
const ROWS: Row[] = [
    { key: 'submerge_mm', label: 'Depth below surface', min: 0, max: 5, step: 0.1, fallback: 1, help: 'How far each tip goes below the liquid it found.' },
    { key: 'follow_mm', label: 'Follow down while aspirating', min: 0, max: 10, step: 0.1, fallback: 0, help: 'Extra descent during the draw, for larger volumes.' },
    { key: 'floor_clearance_mm', label: 'Stop above calibrated low', min: 0, max: 5, step: 0.1, fallback: 1, help: 'The search never goes below this.' },
    { key: 'mismatch_tolerance_mm', label: 'Mismatch allowance (height)', min: 0, max: 5, step: 0.1, fallback: 1, help: 'Smallest height difference that counts as a mismatch.' },
    { key: 'level_tolerance_mm', label: 'Same level within', min: 0, max: 5, step: 0.1, fallback: 0.5, help: 'Channels this close aspirate together (by level).' },
];
const WHEN = [['first_draw', 'First draw from each source well'], ['every_draw', 'Every draw'], ['off', 'Off (calibrated height)']] as const;
const MODES = [['by_level', 'By level: highest liquid first, each at its own depth'], ['together', 'Together: all channels once the last one touches'], ['one_at_a_time', 'One channel at a time'], ['on_contact', 'As each touches: aspirate the moment a channel finds liquid']] as const;
const RESPONSES = [['pause', 'Pause for review'], ['proceed', 'Report and continue'], ['fail', 'Stop the run']] as const;
const num = (v: unknown): number | null => { const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN; return Number.isFinite(n) ? n : null; };

export function BioXpLiquidDetection({ value, onChange }: { value: unknown; onChange: (v: MethodValue | undefined) => void }) {
    const current = object(value);
    const set = (key: string, v: unknown) => { const next: MethodValue = { ...current }; if (v === undefined) delete next[key]; else next[key] = v as MethodValue[string]; onChange(next); };
    // No setting at all = a transfer saved before liquid detection: it runs at calibrated height.
    const when = value === undefined ? 'off' : typeof current.when === 'string' ? current.when : 'first_draw';
    const mode = typeof current.mode === 'string' ? current.mode : 'by_level';
    const off = when === 'off';
    return <fieldset className="bioxp-transfer-speeds bioxp-liquid-detection" aria-label="Liquid detection">
        <legend>Liquid detection</legend>
        <label>Detect<select aria-label="Detect liquid" value={when} onChange={e => set('when', e.target.value === 'first_draw' ? undefined : e.target.value)}>{WHEN.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label>Descent<select aria-label="Descent mode" value={mode} disabled={off} onChange={e => set('mode', e.target.value === 'by_level' ? undefined : e.target.value)}>{MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label>Sensing<select aria-label="Sensing mode" value={typeof current.sensing === 'string' ? current.sensing : 'hybrid'} disabled={off} onChange={e => set('sensing', e.target.value === 'hybrid' ? undefined : e.target.value)}><option value="hybrid">Hybrid: capacitive + pressure (conductive tips)</option><option value="capacitive">Capacitive only</option><option value="pressure">Pressure only</option></select></label>
        {mode === 'on_contact' && <div className="bioxp-speed-row"><label htmlFor="lld-delay">Wait after touch</label><input id="lld-delay" type="range" aria-label="Wait after touch slider" min={0} max={1000} step={10} value={num(current.on_contact_delay_ms) ?? 0} disabled={off} onChange={e => set('on_contact_delay_ms', Number(e.target.value) || undefined)} /><input type="number" aria-label="Wait after touch (ms)" min={0} max={5000} value={num(current.on_contact_delay_ms) ?? ''} placeholder="0" disabled={off} onChange={e => set('on_contact_delay_ms', e.target.value === '' ? undefined : Number(e.target.value))} /><span>ms</span><small>Z keeps descending, so a wait adds depth for the first tips.</small></div>}
        <label>Volume differs from plan<select aria-label="Volume mismatch response" value={typeof current.mismatch_response === 'string' ? current.mismatch_response : 'pause'} disabled={off} onChange={e => set('mismatch_response', e.target.value === 'pause' ? undefined : e.target.value)}>{RESPONSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <div className="bioxp-speed-row"><label htmlFor="lld-tol-pct">Mismatch allowance</label><input id="lld-tol-pct" type="range" aria-label="Mismatch allowance slider" min={0} max={50} step={1} value={num(current.mismatch_tolerance_percent) ?? 15} disabled={off} onChange={e => set('mismatch_tolerance_percent', Number(e.target.value))} /><input type="number" aria-label="Mismatch allowance (%)" min={0} max={100} value={num(current.mismatch_tolerance_percent) ?? ''} placeholder="15" disabled={off} onChange={e => set('mismatch_tolerance_percent', e.target.value === '' ? undefined : Number(e.target.value))} /><span>%</span><small>Or 1 mm, whichever is larger. Checked only when the plate has a measured height-to-volume table.</small></div>
        <label><input type="checkbox" aria-label="Liquid expected" checked={current.expected !== false} disabled={off} onChange={e => set('expected', e.target.checked ? undefined : false)} />Liquid expected: a well the plan lists as filled, with no liquid found, pauses the run</label>
        {ROWS.map(row => {
            const own = num(current[row.key]);
            return <div key={row.key} className="bioxp-speed-row">
                <label htmlFor={`lld-${row.key}`}>{row.label}</label>
                <input id={`lld-${row.key}`} type="range" aria-label={`${row.label} slider`} min={row.min} max={row.max} step={row.step} value={own ?? row.fallback} disabled={off} onChange={e => set(row.key, Number(e.target.value))} />
                <input type="number" aria-label={`${row.label} (mm)`} min={row.min} max={20} step={row.step} value={own ?? ''} placeholder={String(row.fallback)} disabled={off} onChange={e => set(row.key, e.target.value === '' ? undefined : Number(e.target.value))} />
                <span>mm</span>
                {own !== null ? <button type="button" onClick={() => set(row.key, undefined)}>Use default</button> : <small>{row.help}</small>}
            </div>;
        })}
        <small>The search starts at the station's calibrated high and moves down until every active channel touches liquid or reaches the stop height. Heights are recorded per channel in the run.</small>
    </fieldset>;
}
