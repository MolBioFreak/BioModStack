import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { bioXpErrorText } from '../lib/bioxpClient';
import { type MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';
import { PipetteValue } from './BioXpPipetteResults';

function PressureChunk({ chunk }: { chunk: MethodValue }) {
    const [page, setPage] = useState(0);
    // Native retained-store reader returns {scientific_result:{scientific_result,samples}}.
    const retained = object(chunk.scientific_result), capture = object(retained.scientific_result);
    const samples = Array.isArray(retained.samples) ? retained.samples as MethodValue[] : [];
    const start = page * 50;
    return <section className="min-w-0 border-t border-slate-700 py-2" aria-label={`Pressure channel ${String(chunk.channel)}`}>
        <h5>Channel {String(chunk.channel)} · {String(chunk.units ?? 'units not reported')}</h5>
        <p>Chunk: {String(chunk.chunk_id)} · Samples retained: {String(chunk.sample_count ?? 'unknown')}</p>
        <dl><dt>Capture outcome / interruption</dt><dd><PipetteValue value={{ ok: capture.ok, source_return_completed: capture.source_return_completed, error: capture.error }} /></dd>
            <dt>Descriptive statistics (native)</dt><dd><PipetteValue value={capture.baseline_statistics} /></dd>
            <dt>Lost frames</dt><dd>{String(capture.lost_frame_count ?? 'Unknown')}</dd><dt>Lost samples</dt><dd>{String(capture.lost_sample_count ?? 'Unknown')}</dd>
            <dt>Malformed frames</dt><dd>{String(capture.malformed_frame_count ?? 'Unknown')}</dd>
            <dt>Sample-period basis</dt><dd>{String(capture.sample_period_basis ?? 'Not reported')}</dd>
            <dt>Controller timestamps available</dt><dd>{String(capture.controller_timestamps_available ?? 'Unknown')}</dd>
        </dl>
        <p>Raw controller pressure counts are not calibrated pressure, volume delivery or an anomaly classification. Robot receive time is not a controller sample timestamp.</p>
        <div className="max-w-full overflow-x-auto"><table><thead><tr>{['Sequence', 'Raw pressure', 'Robot receive time', 'Controller timestamp', 'Receive sequence', 'Packet sample'].map(label => <th key={label}>{label}</th>)}</tr></thead>
            <tbody>{samples.slice(start, start + 50).map((sample, index) => <tr key={index}>{['sample_sequence', 'raw_pressure', 'robot_receive_time', 'controller_timestamp', 'receive_sequence', 'packet_sample_index'].map(key => <td key={key}>{String(sample[key] ?? 'Unknown')}</td>)}</tr>)}</tbody></table></div>
        {samples.length > 0 ? <p>Showing {start + 1}–{Math.min(start + 50, samples.length)} of {samples.length} retained samples</p> : <p>No retained samples in this chunk.</p>}
        <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous samples</button>
        <button type="button" disabled={start + 50 >= samples.length} onClick={() => setPage(page + 1)}>Next samples</button>
        <details><summary>Capture identity and authored request</summary><PipetteValue value={{ reader_generation: capture.reader_generation, requested: capture.requested, started_at: capture.started_at, finished_at: capture.finished_at, analysis_basis: capture.analysis_basis }} /></details>
    </section>;
}
export function BioXpPressureResult({ result }: { result: MethodValue }) {
    return <section aria-label="Retained pressure result" className="min-w-0 break-words"><h4>Retained bounded capture</h4>
        <PipetteValue value={result.stream} />
        {(Array.isArray(result.chunks) ? result.chunks as MethodValue[] : []).map((chunk, index) => <PressureChunk key={String(chunk.chunk_id ?? index)} chunk={chunk} />)}
    </section>;
}
export function BioXpPressureCapture({ visible, connected, generation }: { visible: boolean; connected: boolean; generation: number }) {
    const [open, setOpen] = useState(false), [id, setId] = useState('');
    const [result, setResult] = useState<{ generation: number; id: string; value: MethodValue } | null>(null);
    const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
    const lock = useRef(false), authority = useRef(generation); authority.current = generation;
    async function read() {
        if (lock.current) return;
        const current = generation, requested = id;
        lock.current = true; setBusy(true); setError(null);
        try {
            const response = await api.get<MethodValue>(`/api/bioxp/pipettes/pressure-captures/${encodeURIComponent(requested)}`, { params: { expected_connection_generation: current } });
            if (authority.current === current) setResult({ generation: current, id: requested, value: response.data });
        } catch (e) { if (authority.current === current) setError(bioXpErrorText(e)); }
        finally { lock.current = false; setBusy(false); }
    }
    return <section hidden={!visible} className="mt-4 min-w-0 rounded border border-slate-700 p-3" aria-label="Pressure capture reader">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Pressure capture result</button>
        <div hidden={!open}><p>Read a retained finite capture by its stream session ID. This never enables streaming, queries hardware or starts acquisition.</p>
            <label>Stream session ID<input className="w-full" aria-label="Pressure stream session ID" value={id} onChange={e => setId(e.target.value)} /></label>
            <button type="button" disabled={!connected || busy || !id} onClick={() => void read()}>Read retained capture</button>
        </div>
        {busy && <p role="status">Reading retained pressure samples…</p>}{error && <p role="alert">{error}</p>}
        {result?.generation === generation && <><p>Requested capture: {result.id}</p><BioXpPressureResult result={result.value} /></>}
    </section>;
}
