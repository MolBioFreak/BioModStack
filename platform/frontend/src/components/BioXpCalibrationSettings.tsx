import { useEffect, useRef, useState } from 'react';
import { bioXpErrorText } from '../lib/bioxpClient';
import { calibrationFields, readCalibrationSettings, saveCalibrationSettings,
    type CalibrationField, type CalibrationPatch, type CalibrationSettings } from '../lib/bioxpCalibration';

type Drafts = Record<string, Partial<Record<CalibrationField, string>>>;
export function BioXpCalibrationSettings({ generation, connected }: { generation: number; connected: boolean }) {
    const [data, setData] = useState<CalibrationSettings | null>(null);
    const [station, setStation] = useState('');
    const [drafts, setDrafts] = useState<Drafts>({});
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [message, setMessage] = useState('');
    const busy = useRef(false);
    const epoch = useRef(0);
    const current = useRef({ generation, connected }); current.current = { generation, connected };
    useEffect(() => {
        const token = ++epoch.current;
        setData(null); setDrafts({}); setMessage(''); setError(null);
        if (connected) void readCalibrationSettings(generation).then(result => {
            if (epoch.current !== token) return;
            setData(result); setStation(result.saved_positions[0]?.name ?? '');
        }).catch(cause => { if (epoch.current === token) setError(bioXpErrorText(cause)); });
        return () => { ++epoch.current; };
    }, [generation, connected]);
    async function read() {
        const token = epoch.current; setError(null);
        try { const result = await readCalibrationSettings(generation); if (epoch.current === token) setData(result); }
        catch (cause) { if (epoch.current === token) setError(bioXpErrorText(cause)); }
    }
    async function save() {
        if (busy.current || !connected) return;
        busy.current = true; setPending(true); setError(null); setMessage('');
        const token = epoch.current;
        let committed: string | undefined;
        try {
            const positions: CalibrationPatch[] = Object.entries(drafts).map(([name, fields]) => {
                const row: CalibrationPatch = { name };
                for (const field of calibrationFields) if (fields[field] !== undefined) {
                    const raw = fields[field]!; const value = Number(raw);
                    if (!raw.trim() || !Number.isInteger(value) || value < -2147483648 || value > 2147483647)
                        throw new Error(`${name} ${field}: enter a signed 32-bit integer.`);
                    row[field] = value;
                }
                return row;
            });
            if (!positions.length) throw new Error('Edit at least one saved field.');
            if (current.current.generation !== generation || !current.current.connected) throw new Error('Connection changed before save.');
            const saved = await saveCalibrationSettings(generation, positions); committed = saved.committed_revision_id;
            if (epoch.current !== token) return;
            const readback = await readCalibrationSettings(generation);
            if (epoch.current !== token) return;
            setData(readback); setDrafts({});
            setMessage(readback.saved_revision_id === committed
                ? `Saved revision ${committed} read back. No live application or motion.`
                : `Committed revision ${committed ?? 'unavailable'}; latest saved revision differs. No live application or motion.`);
        } catch (cause) {
            if (epoch.current === token) setError(`${committed ? `Save returned revision ${committed}, but readback failed. ` : ''}${bioXpErrorText(cause)}`);
        } finally { busy.current = false; setPending(false); }
    }
    const saved = data?.saved_positions.find(row => row.name === station);
    const active = data?.active_positions.find(row => row.name === station);
    const savedMotion = data?.saved_motion_positions.find(row => row.location_id === station);
    const activeMotion = data?.active_motion_positions.find(row => row.location_id === station);
    const projection = (row: typeof savedMotion, field: CalibrationField | 'zHigh') => !row ? 'unavailable'
        : field === 'x' || field === 'y' ? row.base_coordinates[field]
        : field === 'inc_factor' ? row.inc_factor : field === 'zLow' ? row.z_low : field === 'zDelta' ? row.z_delta : row.z_high;
    return <section aria-label="Calibration settings" className="mt-4 space-y-3 rounded border border-slate-700 p-4">
        <h3>PositionTable calibration settings</h3>
        <p className="text-sm">Edit final OEM table values, not raw probe measurements. Related station edits save as one batch. No automatic measurement-to-offset conversion.</p>
        <button type="button" disabled={!connected || pending} onClick={() => void read()}>Read saved and active settings</button>
        {data && <>
            <p>Active startup revision: {data.active_revision_id ?? 'captured baseline'}. Saved revision: {data.saved_revision_id ?? 'captured baseline'}.</p>
            <p role="status">{data.pending_restart ? 'Saved configuration pending next ordinary startup.' : 'Configuration bound at startup; physical calibration not verified.'}</p>
            <p className="text-xs">{data.application_semantics}. Saving never restarts, homes or rebinds the robot.</p>
            <label>Calibration station<select aria-label="Calibration station" value={station} onChange={e => setStation(e.target.value)}>{data.saved_positions.map(row => <option key={row.name}>{row.name}</option>)}</select></label>
            {saved && <>
                <table className="w-full text-sm"><thead><tr><th>Field</th><th>Saved value (editable)</th><th>Active raw</th><th>Saved motion projection</th><th>Active motion projection</th></tr></thead>
                    <tbody>{calibrationFields.map(field => <tr key={field}><th>{field} ({field === 'inc_factor' ? 'dimensionless' : 'steps'})</th>
                        <td><input aria-label={`Saved ${field}`} type="number" step="1" min="-2147483648" max="2147483647" disabled={pending}
                            value={drafts[station]?.[field] ?? saved[field]} onChange={e => setDrafts(previous => ({ ...previous, [station]: { ...previous[station], [field]: e.target.value } }))} /></td>
                        <td>{active?.[field] ?? 'unavailable'}</td><td>{projection(savedMotion, field)}</td><td>{projection(activeMotion, field)}</td></tr>)}
                        <tr><th>zHigh (derived steps)</th><td>{saved.zHigh}</td><td>{active?.zHigh ?? 'unavailable'}</td><td>{projection(savedMotion, 'zHigh')}</td><td>{projection(activeMotion, 'zHigh')}</td></tr>
                    </tbody></table>
                <p className="text-xs">Saved source: {saved.source ?? 'unavailable'}. Active source: {active?.source ?? 'unavailable'}.</p>
                <p className="text-xs">Motion columns show saved/active loader projections, not unsaved edits. TECAN zDelta normalizes to 53000; shared loader rules can change XY and zHigh.</p>
                {(['saved', 'active'] as const).map(which => <p key={which} className="text-xs">{which} loader adjustments: {data[`${which}_loader_adjustments`].filter(row => row.location_id === station).map(row => row.kind).join(', ') || 'none reported'}</p>)}
            </>}
            <p>{Object.keys(drafts).length} station(s) edited</p>
            <button type="button" disabled={!connected || pending} onClick={() => void save()}>Save for next startup</button>
        </>}
        {pending && <p role="status">Saving and reading back…</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
