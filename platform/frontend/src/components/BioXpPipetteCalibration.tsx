import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText, type BioXpDeckDestinationV1 } from '../lib/bioxpClient';
import { methodsGet, methodsPost, type MethodValue } from '../lib/bioxpMethods';
import { compileImmediate, liquidLevels, useBioXpImmediateRun } from '../lib/bioxpImmediateRun';

const STEPS_PER_MM = 2015.748;
const ROWS = [...'ABCDEFGH'];
const mm = (steps: number) => (steps / STEPS_PER_MM).toFixed(2);
type TipRow = { tip_type: number; offset_steps: number; measured: boolean; datasheet_length_mm: number | null;
    datasheet_delta_steps: number | null; source: string | null; confirmed?: { offset_steps: number; source: string; confirmed_at: string } | null };

/** Station + reference well, as two compact selects. */
function Target({ destinations, location, setLocation, well, setWell, prefix }: { destinations: BioXpDeckDestinationV1[];
    location: string; setLocation: (v: string) => void; well: string; setWell: (v: string) => void; prefix: string }) {
    const row = well.slice(0, 1) || 'A', col = well.slice(1) || '1';
    return <div className="flex flex-wrap gap-3">
        <label>Station<select aria-label={`${prefix} station`} value={location} onChange={e => setLocation(e.target.value)} className="ml-2">
            <option value="">Select a station</option>
            {destinations.map(d => <option key={d.target} value={d.location_id}>{d.label}</option>)}
        </select></label>
        <label>Well<select aria-label={`${prefix} row`} value={row} onChange={e => setWell(e.target.value + col)} className="ml-2">{ROWS.map(r => <option key={r}>{r}</option>)}</select>
            <select aria-label={`${prefix} column`} value={col} onChange={e => setWell(row + e.target.value)} className="ml-1">{Array.from({ length: 12 }, (_, i) => <option key={i}>{i + 1}</option>)}</select></label>
    </div>;
}

function Channels({ value, onChange, prefix }: { value: number[]; onChange: (v: number[]) => void; prefix: string }) {
    return <fieldset className="flex flex-wrap gap-3"><legend className="sr-only">{prefix} channels</legend>
        {[0, 1, 2, 3].map(c => <label key={c}><input type="checkbox" aria-label={`${prefix} pipette ${c + 1}`} checked={value.includes(c)}
            onChange={e => onChange(e.target.checked ? [...value, c].sort() : value.filter(x => x !== c))} /> P{c + 1}</label>)}
    </fieldset>;
}

/** Pipette calibration: tip offsets, liquid detection test and plate measurement. */
export function BioXpPipetteCalibration({ generation, connected, destinations, visible = true }: {
    generation: number; connected: boolean; destinations: BioXpDeckDestinationV1[]; visible?: boolean;
}) {
    const client = useQueryClient();
    const runner = useBioXpImmediateRun(generation, connected, visible);
    const offsets = useQuery({ queryKey: ['bioxp', 'tip-offsets', generation], enabled: connected && visible, retry: false,
        queryFn: async () => (await api.get<{ tips: TipRow[] }>('/api/bioxp/tip-offsets', { params: { expected_connection_generation: generation } })).data });
    const plates = useQuery({ queryKey: ['bioxp', 'methods', 'plates'], queryFn: () => methodsGet<MethodValue>('plates'), enabled: visible, retry: false, staleTime: Infinity });
    const [location, setLocation] = useState('');
    const [well, setWell] = useState('A1');
    const [channels, setChannels] = useState<number[]>([0, 1, 2, 3]);
    const [sensing, setSensing] = useState<'hybrid' | 'capacitive' | 'pressure'>('hybrid');
    const [speed, setSpeed] = useState(300);
    const [clearance, setClearance] = useState(1);
    const [edits, setEdits] = useState<Record<number, string>>({});
    const [saveError, setSaveError] = useState<string | null>(null);
    const [plateId, setPlateId] = useState('thermo-4483485-enduraplate-fast');
    const [knownUl, setKnownUl] = useState<Record<number, string>>({});
    const [gap, setGap] = useState('');
    const [points, setPoints] = useState<[number, number][]>([]);
    const [plateMessage, setPlateMessage] = useState<string | null>(null);
    const loc = Number(location);
    const ready = runner.enabled && location !== '';
    const move = (flag: 0 | 1 | 2) => ({ step_id: `move-${flag}`, type: 'action', action: 'native_intent', inputs: { operation: 'move', location_id: loc, well, position_flag: flag } });
    const lift = { step_id: 'lift', type: 'action', action: 'native_intent', inputs: { operation: 'lift', location_id: loc, height_steps: null } };
    const search = { step_id: 'search', type: 'action', action: 'cavro_application', inputs: { application: { implementation: 'original-adp.application.v1', operations: [{
        operation: 'liquid_search', channels, timeout_ms: 15000, location_id: loc, sensing, search_speed_native: speed,
        floor_clearance_steps: Math.round(clearance * STEPS_PER_MM) }] } } };
    const levels = liquidLevels(runner.job);
    const latest = levels[levels.length - 1];
    const tips = offsets.data?.tips ?? [];
    const profiles = (Array.isArray(plates.data?.profiles) ? plates.data!.profiles : []) as MethodValue[];
    const profile = profiles.find(p => p.id === plateId);

    async function saveOffset(row: TipRow) {
        setSaveError(null);
        const value = Number(edits[row.tip_type]);
        if (!Number.isFinite(value)) { setSaveError('Enter the offset in mm.'); return; }
        try {
            await api.put('/api/bioxp/tip-offsets', { expected_connection_generation: generation, tip_type: row.tip_type,
                offset_steps: Math.round(value * STEPS_PER_MM), source: `Operator check move ${new Date().toISOString().slice(0, 10)}` });
            setEdits(e => { const next = { ...e }; delete next[row.tip_type]; return next; });
            await client.invalidateQueries({ queryKey: ['bioxp', 'tip-offsets', generation] });
        } catch (cause) { setSaveError(bioXpErrorText(cause)); }
    }
    function addPoints() {
        if (!latest) return;
        const rows: [number, number][] = [];
        for (const [key, value] of Object.entries(latest.channels ?? {}) as [string, any][]) {
            const volume = Number(knownUl[Number(key)]);
            if (!value?.found || value.height_above_floor_mm == null || !Number.isFinite(volume)) continue;
            rows.push([Number(value.height_above_floor_mm) + clearance, volume]);   // height above calibrated low
        }
        if (!rows.length) { setPlateMessage('No channel found liquid with a known volume entered.'); return; }
        setPoints(p => [...p, ...rows].sort((a, b) => a[1] - b[1])); setPlateMessage(null);
    }
    async function savePlate() {
        setPlateMessage(null);
        const g = Number(gap);
        if (!profile) { setPlateMessage('Choose a plate.'); return; }
        if (!gap.trim() || !Number.isFinite(g)) { setPlateMessage('Enter how far calibrated low sits above the well bottom (mm).'); return; }
        if (points.length < 2) { setPlateMessage('Record at least two volumes.'); return; }
        const table = [[0, 0], ...points.map(([h, v]) => [Number((h + g).toFixed(3)), v])];
        const { saved_record_id: _ignored, ...base } = profile as MethodValue & { saved_record_id?: string };
        const measured = { ...base, vessel_geometry: { ...(base.vessel_geometry as MethodValue ?? {}), height_to_volume: table, calibrated_low_gap_mm: g },
            source: `${String(base.source ?? '')} Measured on BioXP ${new Date().toISOString().slice(0, 10)}.`.trim() };
        try {
            await methodsPost('plate-profiles', { method: { schema: 'bms.bioxp-plate-profile.v1', name: `${String(base.name ?? plateId)} (measured)`, profile: measured } });
            await client.invalidateQueries({ queryKey: ['bioxp', 'methods', 'plates'] });
            setPlateMessage(`Saved ${points.length} points. Workflows volume checks now use this table.`); setPoints([]);
        } catch (cause) { setPlateMessage(bioXpErrorText(cause)); }
    }

    return <section aria-label="Pipette calibration" className="space-y-4 text-sm">
        <Target destinations={destinations} location={location} setLocation={setLocation} well={well} setWell={setWell} prefix="Calibration" />
        <p className="text-xs text-slate-400">Every button below moves the head. Use Stop if anything looks wrong.</p>

        <section aria-label="Tip offsets" className="rounded border border-slate-700 p-3">
            <h4 className="font-semibold">Tip offsets</h4>
            <p className="text-xs text-slate-400">How much lower (+) or higher (−) each tip type goes than a 50 µL tip. Load a tip, move to calibrated low, check the tip end, then save the corrected value. Saving only records it.</p>
            {offsets.error && <p role="alert">Tip offsets unavailable: {bioXpErrorText(offsets.error)}</p>}
            <table aria-label="Tip offset values" className="mt-2 w-full text-left text-xs [&_td]:px-2 [&_td]:py-1 [&_th]:px-2">
                <thead className="text-slate-400"><tr><th>Tip</th><th>In use</th><th>Datasheet</th><th>Source</th><th>New value (mm)</th><th /></tr></thead>
                <tbody>{tips.map(row => <tr key={row.tip_type}>
                    <th scope="row">{row.tip_type === 201 ? '200 (201)' : row.tip_type} µL</th>
                    <td>{mm(row.offset_steps)} mm</td>
                    <td>{row.datasheet_delta_steps == null ? '—' : `${mm(row.datasheet_delta_steps)} mm`}</td>
                    <td>{row.confirmed ? `confirmed ${row.confirmed.confirmed_at.slice(0, 10)}` : row.measured ? 'datasheet default' : 'none'}</td>
                    <td><input aria-label={`Tip ${row.tip_type} offset (mm)`} type="number" step="0.05" className="w-24" value={edits[row.tip_type] ?? ''}
                        placeholder={mm(row.offset_steps)} onChange={e => setEdits(x => ({ ...x, [row.tip_type]: e.target.value }))} /></td>
                    <td><button type="button" disabled={!connected || edits[row.tip_type] === undefined} onClick={() => void saveOffset(row)}>Save</button></td>
                </tr>)}</tbody>
            </table>
            {saveError && <p role="alert">{saveError}</p>}
            <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={!ready} onClick={() => void runner.run(() => compileImmediate('Tip offset check', [move(2)]))}>Move to calibrated low</button>
                <button type="button" disabled={!ready} onClick={() => void runner.run(() => compileImmediate('Lift', [lift]))}>Lift to calibrated high</button>
            </div>
        </section>

        <section aria-label="Liquid detection test" className="rounded border border-slate-700 p-3 space-y-2">
            <h4 className="font-semibold">Liquid detection test</h4>
            <p className="text-xs text-slate-400">One search at the selected well: starts at calibrated high, stops at the first of every channel touching or the stop height, then lifts. Nothing is aspirated.</p>
            <Channels value={channels} onChange={setChannels} prefix="Detection" />
            <div className="flex flex-wrap gap-3">
                <label>Sensing<select aria-label="Detection sensing" value={sensing} onChange={e => setSensing(e.target.value as typeof sensing)} className="ml-2">
                    <option value="hybrid">Hybrid (capacitive + pressure)</option><option value="capacitive">Capacitive</option><option value="pressure">Pressure</option></select></label>
                <label>Search speed<input aria-label="Detection search speed" type="number" min={1} value={speed} onChange={e => setSpeed(Number(e.target.value) || 300)} className="ml-2 w-20" /></label>
                <label>Stop above calibrated low (mm)<input aria-label="Detection stop height (mm)" type="number" min={0} step={0.1} value={clearance} onChange={e => setClearance(Number(e.target.value))} className="ml-2 w-20" /></label>
            </div>
            <button type="button" disabled={!ready || channels.length === 0} onClick={() => void runner.run(() => compileImmediate('Liquid detection test', [move(0), search, lift]))}>Find liquid</button>
            {latest && <table aria-label="Detected liquid" className="w-full text-left text-xs [&_td]:px-2 [&_th]:px-2">
                <thead className="text-slate-400"><tr><th>Channel</th><th>Found</th><th>Above calibrated low</th><th>Z (steps)</th><th>Method</th></tr></thead>
                <tbody>{Object.entries(latest.channels ?? {}).map(([key, value]: [string, any]) => <tr key={key}>
                    <th scope="row">P{Number(key) + 1}</th><td>{value.found ? 'yes' : 'no liquid'}</td>
                    <td>{value.height_above_floor_mm == null ? '—' : `${(Number(value.height_above_floor_mm) + clearance).toFixed(2)} mm`}</td>
                    <td>{value.contact_steps ?? '—'}</td><td>{value.method}</td></tr>)}</tbody>
            </table>}
        </section>

        <section aria-label="Plate measurement" className="rounded border border-slate-700 p-3 space-y-2">
            <h4 className="font-semibold">Plate measurement</h4>
            <p className="text-xs text-slate-400">Pipette known volumes into wells under each channel, run Find liquid above, then add the points. Two or more points plus the well-bottom gap make the plate's height-to-volume table used by Workflows volume checks.</p>
            <label>Plate<select aria-label="Plate profile" value={plateId} onChange={e => { setPlateId(e.target.value); setPoints([]); }} className="ml-2">
                {profiles.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.name ?? p.id)}</option>)}</select></label>
            <div className="flex flex-wrap gap-3">{[0, 1, 2, 3].map(c => <label key={c}>P{c + 1} known µL<input aria-label={`Known volume pipette ${c + 1}`} type="number" min={0} step="any" className="ml-1 w-20"
                value={knownUl[c] ?? ''} onChange={e => setKnownUl(k => ({ ...k, [c]: e.target.value }))} /></label>)}</div>
            <button type="button" disabled={!latest} onClick={addPoints}>Add points from last search</button>
            {points.length > 0 && <ol aria-label="Plate points" className="text-xs">{points.map(([h, v], i) => <li key={i}>{v} µL at {h.toFixed(2)} mm above calibrated low
                <button type="button" aria-label={`Remove point ${i + 1}`} onClick={() => setPoints(p => p.filter((_, j) => j !== i))} className="ml-2">×</button></li>)}</ol>}
            <label>Calibrated low above well bottom (mm)<input aria-label="Well-bottom gap (mm)" type="number" step="0.05" min={0} value={gap} onChange={e => setGap(e.target.value)} className="ml-2 w-20" /></label>
            <button type="button" disabled={points.length < 2} onClick={() => void savePlate()}>Save plate table</button>
            {plateMessage && <p role="status">{plateMessage}</p>}
        </section>

        {runner.pending && <p role="status">Submitting…</p>}
        {runner.job && <p role="status">Robot job: {runner.job.command?.status ?? runner.job.status} · {runner.job.execution?.runtime_state.workflow?.phase ?? '—'}</p>}
        {runner.query.error && <p role="alert">Job readback unavailable: {bioXpErrorText(runner.query.error)}</p>}
        {runner.error && <p role="alert">{runner.error}</p>}
    </section>;
}
