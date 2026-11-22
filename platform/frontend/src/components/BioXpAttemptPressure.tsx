import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText } from '../lib/bioxpClient';
import type { MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';

type Address = { generation: number; jobId: string; gateId: string; occurrence: string; actionId: string; attempt: number };
type Point = { sequence: number; pressure: number };

function ChannelChart({ chunk, stream }: { chunk: MethodValue; stream: MethodValue }) {
    const retained = object(chunk.scientific_result), metadata = object(retained.scientific_result);
    const samples = Array.isArray(retained.samples) ? retained.samples : [];
    const complete = stream.terminal_state === 'completed' && !metadata.interrupted && metadata.ok !== false && metadata.source_return_completed !== false;
    // A loss counter does not locate the missing samples. Never guess a break position.
    const connect = complete && stream.loss_count === 0 && metadata.lost_frame_count === 0 && metadata.malformed_frame_count === 0 && !(Number(metadata.lost_sample_count) > 0) && !(Number(chunk.lost_sample_count) > 0);
    const runs: Point[][] = [];
    let last: Point | undefined, xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const raw of samples) {
        const { sample_sequence: sequence, raw_pressure: pressure } = object(raw);
        if (typeof sequence !== 'number' || !Number.isSafeInteger(sequence) || typeof pressure !== 'number' || !Number.isFinite(pressure)) { last = undefined; continue; }
        if (!connect || !last || sequence !== last.sequence + 1) runs.push([]);
        last = { sequence, pressure }; runs[runs.length - 1].push(last);
        xmin = Math.min(xmin, sequence); xmax = Math.max(xmax, sequence); ymin = Math.min(ymin, pressure); ymax = Math.max(ymax, pressure);
    }
    const x = (p: Point) => xmax === xmin ? 160 : 4 + 312 * (p.sequence - xmin) / (xmax - xmin);
    const y = (p: Point) => ymax === ymin ? 50 : 96 - 92 * (p.pressure - ymin) / (ymax - ymin);
    return <figure className="min-w-0"><figcaption>Channel {String(chunk.channel)} · Raw controller pressure counts</figcaption>
        {runs.length ? <><svg className="w-full" viewBox="0 0 320 100" role="img" aria-label={`Channel ${chunk.channel}: raw controller pressure counts by sample sequence`}>
            {runs.map((run, i) => run.length === 1 ? <circle key={i} cx={x(run[0])} cy={y(run[0])} r="1.5" fill="currentColor" /> : <polyline key={i} points={run.map(p => `${x(p)},${y(p)}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="1.5" />)}
        </svg><p>Sample sequence: {xmin}–{xmax} · Raw counts: {ymin} to {ymax}</p></> : <p>No plottable retained samples.</p>}
        <p>Lost frames: {String(metadata.lost_frame_count ?? 'unknown')} · Lost samples: {String(metadata.lost_sample_count ?? 'unknown')} · Malformed frames: {String(metadata.malformed_frame_count ?? 'unknown')}</p>
        {!connect && <p>Capture incomplete or continuity uncertain. Only retained points are shown; gap locations are not inferred.</p>}
    </figure>;
}
function Capture({ id, address }: { id: string; address: Address }) {
    const read = useQuery({
        queryKey: ['bioxp', 'failed-attempt-pressure', address, id], staleTime: Infinity, retry: false, retryOnMount: false,
        queryFn: async ({ signal }) => {
            const { data } = await api.get<MethodValue>(`/api/bioxp/pipettes/pressure-captures/${encodeURIComponent(id)}`, { params: { expected_connection_generation: address.generation }, signal });
            if (object(data?.stream).stream_session_id !== id || !Array.isArray(data.chunks) || data.chunks.some(c => object(c).stream_session_id !== id)) throw new Error('Retained capture does not match its reference.');
            return { stream: object(data.stream), chunks: data.chunks.map(object) };
        },
    });
    if (read.isPending) return <p role="status">Reading pressure trace {id}…</p>;
    if (read.isError) return <p role="alert">Pressure trace {id} unavailable: {bioXpErrorText(read.error)}</p>;
    return <div><p>Pressure capture {id} · {String(read.data.stream.terminal_state ?? 'state unknown')}</p>
        {read.data.chunks.length ? read.data.chunks.map((chunk, i) => <ChannelChart key={String(chunk.chunk_id ?? i)} chunk={chunk} stream={read.data.stream} />) : <p>No retained sample chunks.</p>}
    </div>;
}
export function BioXpAttemptPressure({ refs, warning, address }: { refs: unknown; warning: unknown; address: Address }) {
    const ids = Array.isArray(refs) ? refs.map(ref => object(ref).stream_session_id).filter((id): id is string => typeof id === 'string' && !!id.trim()) : [];
    const malformed = refs != null && (!Array.isArray(refs) || ids.length !== refs.length);
    return <section aria-label="Failed attempt pressure traces"><h5>Pressure trace</h5>
        {typeof warning === 'string' && warning && <p role="status">{warning}</p>}
        {malformed && <p role="status">Pressure trace reference unavailable.</p>}
        {!ids.length && !warning && !malformed && <p>No pressure capture reported for this attempt.</p>}
        {[...new Set(ids)].map(id => <Capture key={id} id={id} address={address} />)}
    </section>;
}
