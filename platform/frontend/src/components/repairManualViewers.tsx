import { useRef, useState } from 'react';
import { bioXpErrorText } from '../lib/bioxpClient';
import { readCalibrationSettings, type CalibrationSettings } from '../lib/bioxpCalibration';
import { repairManualRead, type RepairManualReaderKind, type RepairManualReaderData } from '../lib/repairManualReaders';

const text = (value: unknown) => value === null ? 'null' : value === undefined ? 'Not reported' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export function RepairManualFields({ data }: { data: unknown }) {
    if (data === null || typeof data !== 'object') return <span>{text(data)}</span>;
    const entries = Object.entries(data);
    if (!entries.length) return <span>{Array.isArray(data) ? '[] (empty)' : '{} (empty)'}</span>;
    return <dl className="bx-reader-fields">{entries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>
        {value !== null && typeof value === 'object' ? <RepairManualFields data={value} /> : text(value)}
    </dd></div>)}</dl>;
}

export function RepairManualPositionTable({ data }: { data: RepairManualReaderData }) {
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<number | null>(null);
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const indexed = rows.map((row, index) => ({ row, index }));
    const filtered = indexed.filter(({ row }) => text(row).toLowerCase().includes(search.toLowerCase()));
    const columns = [...new Set(rows.flatMap(row => row !== null && typeof row === 'object' ? Object.keys(row) : ['value']))];
    return <div>
        <p>Bound PositionTable source: {text(data.source)}. These are loader-adjusted positions, not live coordinates.</p>
        <p>Coordinates and Z limits are steps; inc_factor is dimensionless. Nulls are retained, not zero. Selecting a row only inspects it.</p>
        <label>Search position table<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <p>{filtered.length} / {rows.length} returned rows · producer count: {text(data.position_table_count)}</p>
        {rows.length === 0 && <p>No position rows returned.</p>}
        <div className="bx-reader-scroll" tabIndex={0} aria-label="Full position table">
            <table><thead><tr><th>Inspect</th>{columns.map(key => <th key={key}>{key}</th>)}</tr></thead><tbody>
                {filtered.map(({ row, index }) => <tr key={index}><td><button type="button" aria-label={`Inspect position row ${index + 1}`} onClick={() => setSelected(index)}>Inspect</button></td>
                    {columns.map(key => <td key={key}>{text(row !== null && typeof row === 'object' ? row[key] : row)}</td>)}
                </tr>)}
            </tbody></table>
        </div>
        {selected !== null && selected < rows.length && <details open><summary>Inspected row {selected + 1} (read-only)</summary><RepairManualFields data={rows[selected]} /></details>}
        <details><summary>Table response provenance and units</summary><RepairManualFields data={Object.fromEntries(Object.entries(data).filter(([key]) => key !== 'rows'))} /></details>
    </div>;
}

// Each mounted reader owns its request ID and last-good data. No timers/query
// subscriptions; stale completions never replace a newer request or connection.
export function RepairManualViewer({ kind, generation, connected, disabled }: {
    kind: RepairManualReaderKind; generation: number; connected: boolean; disabled: boolean;
}) {
    const sequence = useRef(0);
    const scope = useRef({ generation, connected });
    if (scope.current.generation !== generation || scope.current.connected !== connected) {
        ++sequence.current; scope.current = { generation, connected };
    }
    const [state, setState] = useState<{ request: number; generation: number; pending: boolean; error?: string }>();
    const [good, setGood] = useState<{ request: number; generation: number; data: RepairManualReaderData }>();
    const [calibration, setCalibration] = useState<{ generation: number; data?: CalibrationSettings; error?: string }>();
    const current = state?.generation === generation && connected;
    async function read() {
        const request = ++sequence.current;
        setState({ request, generation, pending: true });
        const stillCurrent = () => sequence.current === request && scope.current.generation === generation && scope.current.connected;
        // Calibration is a separate passive owner; its failure cannot hide the
        // full bound reader or imply that saved values are live coordinates.
        if (kind === 'position-table') {
            void readCalibrationSettings(generation).then(data => { if (stillCurrent()) setCalibration({ generation, data }); },
                error => { if (stillCurrent()) setCalibration({ generation, error: bioXpErrorText(error) }); });
        }
        try {
            const data = await repairManualRead(kind, generation);
            if (!stillCurrent()) return;
            setGood({ request, generation, data });
            setState({ request, generation, pending: false });
        } catch (error) {
            if (stillCurrent()) setState({ request, generation, pending: false, error: bioXpErrorText(error) });
        }
    }
    const label = kind === 'settings' ? 'settings' : 'position table';
    return <section aria-label={`Full ${label} viewer`} className="bx-passive-viewer">
        <button type="button" disabled={disabled} onClick={() => void read()}>Show {label}</button>
        {current && state?.pending && <p role="status">Reading {label} · request {state.request}, connection {generation}…</p>}
        {current && state?.error && <p role="alert">{label} read failed: {state.error}</p>}
        {good && <>
            <p>{good.generation !== generation || !connected || state?.pending || state?.error ? 'Last-good' : 'Returned'} {label} · request {good.request}, connection {good.generation}. Read-only; no hardware collection.</p>
            {Object.keys(good.data).length === 0 && <p>Empty response.</p>}
            {kind === 'position-table' ? <RepairManualPositionTable data={good.data} /> : <div className="bx-reader-scroll" tabIndex={0} aria-label="Full settings"><RepairManualFields data={good.data} /></div>}
        </>}
        {kind === 'position-table' && calibration?.generation === generation && <details><summary>Active versus saved calibration (not live coordinates)</summary>
            {calibration.error ? <p role="alert">Calibration read failed: {calibration.error}</p> : <RepairManualFields data={calibration.data} />}
        </details>}
    </section>;
}
