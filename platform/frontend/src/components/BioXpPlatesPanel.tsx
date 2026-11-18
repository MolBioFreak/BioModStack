import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText } from '../lib/bioxpClient';
import { readCalibrationSettings } from '../lib/bioxpCalibration';

type Depth = { tip_type: number | null; location: string | null; offset_mm: number; source?: string; saved_at?: string };
type Grip = { z_low_offset_steps: number; source?: string; saved_at?: string };
type Profiles = { plates: Record<string, { name: string; gripper: Record<string, Grip | null>; depth_offsets: Depth[] }>;
    loaded: Record<string, string>; stations: Record<string, string>; station_depth_offsets: Depth[] };
type LibraryPlate = { id: string; name: string; short_name?: string; vessel_geometry?: { max_volume_ul?: number | null } };
type DepthTable = { standard: { offset_mm: number; label: string; source: string }[]; max_mm: number };

// Plate (gripper) stations the robot applies a plate's saved height at.
const GRIP_STATIONS: [string, string][] = [['LOC_P_MS', 'Magnet station'], ['LOC_P_TC', 'Thermal cycler'], ['LOC_P_OC', 'Output chiller']];
const DEPTH_STATIONS: [string, string][] = [['LOC_STRIP1', 'Strip 1'], ['LOC_STRIP2', 'Strip 2'], ['LOC_STRIP3', 'Strip 3'], ['LOC_STRIP4', 'Strip 4'],
    ['LOC_RC', 'Reagent chiller'], ['LOC_OC', 'Output chiller'], ['LOC_TC', 'Thermal cycler'], ['LOC_MS', 'Magnet station'], ['LOC_TROUGH', 'Bulk holder']];
const TIPS = [10, 50, 200];
const stationName = (id: string | null) => DEPTH_STATIONS.find(([v]) => v === id)?.[1] ?? GRIP_STATIONS.find(([v]) => v === id)?.[1] ?? id ?? 'any';
const steps = (n: number) => `${n < 0 ? '\u2212' : ''}${Math.abs(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ')}`;
const mm = (n: number) => `${n > 0 ? '+' : n < 0 ? '\u2212' : ''}${Math.abs(n).toFixed(1)}`;
const tipLabel = (t: number | null) => t === null ? 'any' : `${t} µL`;
const today = () => `Operator ${new Date().toISOString().slice(0, 10)}`;

const card = 'rounded-[10px] border border-[var(--border-primary)] bg-[var(--card-bg)] px-[18px] py-4';
const tile = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2.5';
const btn = 'whitespace-nowrap rounded-md border border-[var(--border-secondary)] bg-[var(--bg-tertiary)] px-[11px] py-[5px] text-xs text-[var(--text-primary)] disabled:opacity-40';
const primary = 'whitespace-nowrap rounded-md border border-[var(--accent-primary)] bg-[var(--accent-primary)] px-[11px] py-[5px] text-xs font-semibold text-black disabled:opacity-40';
const ghost = 'whitespace-nowrap px-1.5 py-[5px] text-xs text-[var(--accent-primary)] disabled:opacity-40';
const field = 'rounded-md border border-[var(--border-secondary)] bg-[var(--bg-tertiary)] px-2 py-[5px] text-xs text-[var(--text-primary)]';
const label = 'text-[11px] uppercase tracking-[.06em] text-[var(--text-muted)]';
const muted = 'text-xs text-[var(--text-muted)]';

function Info({ text }: { text: string }) {
    return <span title={text} aria-label={text} className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-[var(--border-secondary)] text-[10px] text-[var(--text-muted)]">i</span>;
}

/** One record per plate (gripper height per plate station, pipetting depth) plus the station depth defaults. Saving only records. */
export function BioXpPlatesPanel({ generation, connected, visible = true }: { generation: number; connected: boolean; visible?: boolean }) {
    const client = useQueryClient();
    const key = ['bioxp', 'plate-profiles', generation];
    const profiles = useQuery({ queryKey: key, enabled: connected && visible, retry: false,
        queryFn: async () => (await api.get<Profiles>('/api/bioxp/plate-profiles', { params: { expected_connection_generation: generation } })).data });
    const library = useQuery({ queryKey: ['bioxp', 'plate-library-list'], enabled: visible, retry: false,
        queryFn: async () => (await api.get<{ profiles: LibraryPlate[] }>('/api/bioxp/methods/plates')).data.profiles ?? [] });
    const calibration = useQuery({ queryKey: ['bioxp', 'plate-station-z', generation], enabled: connected && visible, retry: false,
        queryFn: () => readCalibrationSettings(generation) });
    const depthTable = useQuery({ queryKey: ['bioxp', 'depth-offsets', generation], enabled: connected && visible, retry: false,
        queryFn: async () => (await api.get<DepthTable>('/api/bioxp/depth-offsets', { params: { expected_connection_generation: generation } })).data });
    const [selected, setSelected] = useState<string | null>(null);
    const [editing, setEditing] = useState<{ location: string; value: string } | null>(null);
    const [changing, setChanging] = useState<{ plate: string; station: string } | null>(null);
    const [plateDepth, setPlateDepth] = useState({ tip: '10', location: 'LOC_MS', value: '' });
    const [stationDepth, setStationDepth] = useState({ tip: '10', location: 'LOC_OC', value: '' });
    const [error, setError] = useState<string | null>(null);

    const data = profiles.data;
    const lib = library.data ?? [];
    const ids = [...new Set([...lib.map(p => p.id), ...Object.keys(data?.plates ?? {})])];
    const short = (id: string) => lib.find(p => p.id === id)?.short_name ?? data?.plates[id]?.name ?? id;
    const catalog = (id: string) => /\((\d+)\)\s*$/.exec(lib.find(p => p.id === id)?.name ?? data?.plates[id]?.name ?? '')?.[1];
    const volume = (id: string) => lib.find(p => p.id === id)?.vessel_geometry?.max_volume_ul;
    const loadedId = data?.loaded.OUTPUT_PLATE ?? null;
    const loadedAt = loadedId ? Object.entries(data?.stations ?? {}).find(([, v]) => v === loadedId)?.[0] ?? null : null;
    const current = selected ?? loadedId ?? ids[0] ?? null;
    const plate = current ? data?.plates[current] : undefined;
    const zLow = (location: string) => (calibration.data?.active_positions as { name: string; zLow?: number }[] | undefined)?.find(r => r.name === location)?.zLow;
    const maxMm = depthTable.data?.max_mm ?? 15;

    async function send(method: 'put' | 'post', url: string, body: Record<string, unknown>) {
        setError(null);
        try {
            await api[method](url, { expected_connection_generation: generation, ...body });
            await client.invalidateQueries({ queryKey: key });
            await client.invalidateQueries({ queryKey: ['bioxp', 'depth-offsets', generation] });
            return true;
        } catch (cause) { setError(bioXpErrorText(cause)); return false; }
    }
    async function saveGrip(location: string, value: number | null) {
        if (!current) return;
        if (value !== null && !Number.isInteger(value)) { setError('Gripper height is a whole number of steps.'); return; }
        if (await send('put', '/api/bioxp/plate-profiles/gripper', { plate_id: current, location, z_low_offset_steps: value, source: today() })) setEditing(null);
    }
    async function saveDepth(entry: { tip: string; location: string; value: string }, plateType: string | null) {
        const value = Number(entry.value);
        if (entry.value.trim() === '' || !Number.isFinite(value) || Math.abs(value) > maxMm) { setError(`Depth must be between -${maxMm} and ${maxMm} mm.`); return false; }
        return send('put', '/api/bioxp/depth-offsets', { tip_type: entry.tip === '' ? null : Number(entry.tip), location: entry.location,
            plate_type: plateType, offset_mm: value, source: today() });
    }
    const removeDepth = (row: Depth, plateType: string | null) =>
        send('post', '/api/bioxp/depth-offsets/remove', { tip_type: row.tip_type, location: row.location, plate_type: plateType });

    const own = plate?.depth_offsets ?? [];
    const defaults = data?.station_depth_offsets ?? [];
    const inherited = defaults.filter(d => !own.some(o => o.location === d.location && (o.tip_type ?? null) === (d.tip_type ?? null)));

    return <div className="space-y-[18px]" aria-label="Plates">
        <section aria-label="Plate profiles" className={card}>
            <div className="mb-3 flex items-center gap-2.5">
                <h3 className="m-0 text-[15px] font-semibold">Plate profiles</h3>
                <Info text="Each plate keeps its own gripper height per plate station (used to pick it up and to set it down) and its own pipetting depths. A plate with no gripper height uses the OEM height. Saving only records it; nothing moves." />
                <span className="flex-1" />
                {loadedId && <span data-testid="loaded-output-plate" className="whitespace-nowrap rounded-full border border-[var(--border-secondary)] px-2.5 py-[3px] text-xs text-[var(--text-secondary)]">
                    Output plate: <b className="font-semibold text-[var(--text-primary)]">{short(loadedId)}</b> · {loadedAt ? stationName(loadedAt) : 'location not recorded'}</span>}
                <button type="button" className={btn} disabled={!connected || !data} onClick={() => setChanging(changing ? null : { plate: loadedId ?? ids[0] ?? '', station: loadedAt ?? '' })}>Change</button>
            </div>
            {changing && <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-[var(--border-primary)] p-2.5">
                <span className={muted}>Output plate</span>
                <select aria-label="Loaded output plate" className={field} value={changing.plate} onChange={e => setChanging({ ...changing, plate: e.target.value })}>
                    {ids.map(id => <option key={id} value={id}>{short(id)}</option>)}</select>
                <span className={muted}>at</span>
                <select aria-label="Output plate station" className={field} value={changing.station} onChange={e => setChanging({ ...changing, station: e.target.value })}>
                    <option value="">not recorded</option>{DEPTH_STATIONS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}</select>
                <button type="button" className={primary} onClick={async () => {
                    if (!await send('put', '/api/bioxp/plate-profiles/loaded', { movable_object: 'OUTPUT_PLATE', plate_id: changing.plate })) return;
                    if (loadedAt && loadedAt !== changing.station) await send('put', '/api/bioxp/plate-profiles/station', { location: loadedAt, plate_id: null });
                    if (changing.station) await send('put', '/api/bioxp/plate-profiles/station', { location: changing.station, plate_id: changing.plate });
                    setChanging(null);
                }}>Save</button>
                <button type="button" className={ghost} onClick={() => setChanging(null)}>Cancel</button>
            </div>}
            {profiles.error && <p role="alert" className="text-sm">Plate profiles unavailable: {bioXpErrorText(profiles.error)}</p>}
            {data && <div className="grid grid-cols-[250px_minmax(0,1fr)] gap-4">
                <div className="flex flex-col gap-1.5" role="listbox" aria-label="Plates">
                    {ids.map(id => { const grips = Object.values(data.plates[id]?.gripper ?? {}).filter(Boolean).length; const v = volume(id);
                        return <button key={id} type="button" role="option" aria-selected={id === current} data-plate={id} onClick={() => { setSelected(id); setEditing(null); }}
                            className={`rounded-lg border px-[11px] py-[9px] text-left ${id === current ? 'border-[var(--accent-primary)] bg-[#16213d]' : 'border-[var(--border-primary)] bg-[var(--bg-primary)]'}`}>
                            <div className="whitespace-nowrap font-semibold" title={data.plates[id]?.name ?? id}>{short(id)}
                                {id === loadedId && <span className="ml-1.5 rounded bg-[#1f3a33] px-1.5 py-px align-[1px] text-[10.5px] text-[var(--success)]">loaded</span>}</div>
                            <div className="text-[11.5px] text-[var(--text-muted)]">{[catalog(id), v ? `${v} µL` : null, grips ? `${grips} gripper height${grips > 1 ? 's' : ''}` : 'OEM heights'].filter(Boolean).join(' · ')}</div>
                        </button>; })}
                </div>
                {current && <div className="min-w-0">
                    <div className={`${label} mb-2 mt-1`}>Gripper height · pick up and set down</div>
                    <div className="mb-4 grid grid-cols-3 gap-2.5">
                        {GRIP_STATIONS.map(([location, name]) => { const entry = plate?.gripper?.[location] ?? null; const base = zLow(location);
                            const isEditing = editing?.location === location;
                            return <div key={location} data-grip={`${current}:${location}`} className={`${tile} ${entry ? 'border-[#2f6b58]' : ''}`}>
                                <div className="text-xs text-[var(--text-secondary)]">{name}</div>
                                {entry ? <div className="my-1 whitespace-nowrap text-xl font-semibold text-[var(--success)]">{steps(entry.z_low_offset_steps)}</div>
                                    : <div className="my-1.5 text-[15px] text-[var(--text-muted)]">OEM</div>}
                                <div className="text-[11.5px] text-[var(--text-muted)]">{base === undefined ? 'Z not read' : `Z ${steps(base + (entry?.z_low_offset_steps ?? 0))}`}{entry?.source ? ` · ${entry.source}` : ''}</div>
                                {isEditing ? <div className="mt-2 flex items-center gap-1.5">
                                    <input aria-label={`Gripper steps ${location}`} type="number" step={1} className={`${field} w-24`} value={editing.value} onChange={e => setEditing({ location, value: e.target.value })} />
                                    <button type="button" className={primary} disabled={!connected || editing.value.trim() === ''} onClick={() => void saveGrip(location, Number(editing.value))}>Save</button>
                                    <button type="button" className={ghost} onClick={() => setEditing(null)}>Cancel</button>
                                </div> : <div className="mt-2 flex gap-1.5">
                                    <button type="button" className={btn} disabled={!connected} onClick={() => setEditing({ location, value: entry ? String(entry.z_low_offset_steps) : '' })}>{entry ? 'Edit' : 'Set'}</button>
                                    {entry && <button type="button" className={ghost} disabled={!connected} onClick={() => void saveGrip(location, null)}>Reset to OEM</button>}
                                </div>}
                            </div>; })}
                    </div>
                    <div className={`${label} mb-1`}>Pipetting depth · this plate</div>
                    <table aria-label="Plate depth offsets" className="w-full border-collapse text-[13px]">
                        <thead><tr className="text-left text-[11.5px] text-[var(--text-muted)] [&_th]:border-b [&_th]:border-[var(--border-primary)] [&_th]:px-2 [&_th]:py-1.5 [&_th]:font-medium">
                            <th className="w-[90px]">Tip</th><th>Station</th><th className="w-[110px]">Extra depth</th><th>Source</th><th className="w-[110px]" /></tr></thead>
                        <tbody className="[&_td]:border-b [&_td]:border-[#1d2a44] [&_td]:px-2 [&_td]:py-[7px]">
                            {own.map(row => <tr key={`own:${row.tip_type}:${row.location}`} data-depth={`${current}:${row.tip_type ?? 'any'}:${row.location}`}>
                                <td>{tipLabel(row.tip_type)}</td><td>{stationName(row.location)}</td><td className="font-semibold tabular-nums">{mm(row.offset_mm)} mm</td>
                                <td className="text-[11.5px] text-[var(--text-muted)]">{row.source ?? 'this plate'}</td>
                                <td className="text-right"><button type="button" className={ghost} disabled={!connected} onClick={() => void removeDepth(row, current)}>Remove</button></td></tr>)}
                            {inherited.map(row => <tr key={`def:${row.tip_type}:${row.location}`} className="text-[var(--text-muted)]">
                                <td>{tipLabel(row.tip_type)}</td><td>{stationName(row.location)}</td><td className="tabular-nums text-[var(--text-secondary)]">{mm(row.offset_mm)} mm</td>
                                <td className="text-[11.5px]">station default</td>
                                <td className="text-right"><button type="button" className={ghost} disabled={!connected}
                                    onClick={() => setPlateDepth({ tip: row.tip_type === null ? '' : String(row.tip_type), location: row.location ?? 'LOC_MS', value: String(row.offset_mm) })}>Override</button></td></tr>)}
                            {own.length === 0 && inherited.length === 0 && <tr><td colSpan={5} className="text-[var(--text-muted)]">None: calibrated low plus tip length everywhere.</td></tr>}
                        </tbody>
                    </table>
                    <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-dashed border-[var(--border-primary)] pt-2.5">
                        <span className={muted}>Add</span>
                        <select aria-label="Plate depth tip" className={field} value={plateDepth.tip} onChange={e => setPlateDepth({ ...plateDepth, tip: e.target.value })}>
                            {TIPS.map(t => <option key={t} value={t}>{t} µL</option>)}<option value="">any tip</option></select>
                        <select aria-label="Plate depth station" className={field} value={plateDepth.location} onChange={e => setPlateDepth({ ...plateDepth, location: e.target.value })}>
                            {DEPTH_STATIONS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}</select>
                        <input aria-label="Plate depth mm" type="number" step={0.1} placeholder="+0.0" className={`${field} w-20`} value={plateDepth.value} onChange={e => setPlateDepth({ ...plateDepth, value: e.target.value })} />
                        <span className={muted}>mm</span>
                        <button type="button" className={primary} disabled={!connected || plateDepth.value === ''}
                            onClick={async () => { if (await saveDepth(plateDepth, current)) setPlateDepth({ ...plateDepth, value: '' }); }}>Save</button>
                    </div>
                </div>}
            </div>}
        </section>

        <section aria-label="Station depth defaults" className={card}>
            <div className="mb-3 flex items-center gap-2.5">
                <h3 className="m-0 text-[15px] font-semibold">Station depth defaults</h3>
                <Info text="Extra depth below a station's calibrated low, for reaching the last liquid. Used when the plate at that station has no entry of its own. Tip racks never use one. Saving only records it." />
                <span className="flex-1" /><span className={muted}>Used when the plate has no entry of its own</span>
            </div>
            <div className="grid grid-cols-9 gap-2">
                {DEPTH_STATIONS.map(([location, name]) => { const rows = defaults.filter(d => d.location === location);
                    return <div key={location} data-station-default={location} className={`${tile} px-2 py-2 text-center`}>
                        <div className="whitespace-nowrap text-[11.5px] text-[var(--text-secondary)]">{name}</div>
                        {rows.length === 0 ? <div className="mt-1 text-[13px] text-[var(--text-muted)]">—</div> : rows.map(row =>
                            <div key={String(row.tip_type)} className="mt-1"><span className="text-base font-semibold">{mm(row.offset_mm)}</span>
                                <span className="ml-1 text-[11px] text-[var(--text-muted)]">{tipLabel(row.tip_type)}</span>
                                <button type="button" aria-label={`Remove default ${location} ${row.tip_type ?? 'any'}`} title="Remove" className="ml-1 text-[11px] text-[var(--text-muted)] disabled:opacity-40"
                                    disabled={!connected} onClick={() => void removeDepth(row, null)}>×</button></div>)}
                    </div>; })}
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-dashed border-[var(--border-primary)] pt-2.5">
                <span className={muted}>Tip</span>
                <select aria-label="Default depth tip" className={field} value={stationDepth.tip} onChange={e => setStationDepth({ ...stationDepth, tip: e.target.value })}>
                    {TIPS.map(t => <option key={t} value={t}>{t} µL</option>)}<option value="">any tip</option></select>
                <span className={muted}>Station</span>
                <select aria-label="Default depth station" className={field} value={stationDepth.location} onChange={e => setStationDepth({ ...stationDepth, location: e.target.value })}>
                    {DEPTH_STATIONS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}</select>
                <input aria-label="Default depth mm" type="number" step={0.1} placeholder="+0.0" className={`${field} w-20`} value={stationDepth.value} onChange={e => setStationDepth({ ...stationDepth, value: e.target.value })} />
                <span className={muted}>mm</span>
                <button type="button" className={primary} disabled={!connected || stationDepth.value === ''}
                    onClick={async () => { if (await saveDepth(stationDepth, null)) setStationDepth({ ...stationDepth, value: '' }); }}>Save default</button>
                <span className="flex-1" />
                {(depthTable.data?.standard ?? []).filter(s => s.offset_mm !== 0).length > 0 && <span className={muted}>Measured: {(depthTable.data?.standard ?? []).filter(s => s.offset_mm !== 0).map((s, i) =>
                    <span key={s.label}>{i > 0 && ' · '}<button type="button" className="text-[var(--accent-primary)]" title={s.source}
                        onClick={() => setStationDepth({ ...stationDepth, value: String(s.offset_mm) })}>{mm(s.offset_mm)}</button></span>)}</span>}
            </div>
        </section>
        {error && <p role="alert" className="text-sm text-[var(--error)]">{error}</p>}
    </div>;
}
