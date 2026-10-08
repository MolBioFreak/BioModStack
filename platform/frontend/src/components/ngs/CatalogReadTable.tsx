import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { withAlignmentAccessRecovery, describeNgsError, catalogAlignmentRead, type AlignmentRead } from '../../lib/ngsAlignmentSession';

interface ReadRow {
    [key: string]: unknown;
    read_id: string; alignment_state: string; source_record_count: number;
    length: number | null; mapq: number | null; contig: string | null;
    start_1based: number | null; alignment_end_1based: number | null;
}
interface Page {
    schema: string; job_id: string; session_id: string; population_id: string;
    catalog_authority_sha256: string; reads: ReadRow[]; source_read_count: number;
    signal_cache_retry_sha256: string | null;
    filtered_read_count: number; next_cursor: string | null; sort_by: string; sort_direction: string; signal_metrics_state: string;
}
interface RecordRow {
    source_record_ordinal: number; record_class: string; cigar: string | null;
    contig: string | null; start_1based: number | null; alignment_end_1based: number | null;
    sequence?: string | null; quality?: string | null;
}
interface Detail {
    population_id: string; read: ReadRow; record: RecordRow | null; sequence_available: boolean;
}
const sorts = ['read_id', 'length', 'mean_quality', 'mapq', 'aligned_query_bases', 'aligned_reference_bases',
    'inserted_bases', 'deleted_bases', 'clipped_bases', 'edit_distance', 'reference_substitution_count',
    'reference_substitution_rate', 'aligned_fraction', 'clipped_fraction', 'reference_disagreement_rate',
    'sample_count', 'duration_seconds', 'sampling_rate_hz', 'current_mean_pa', 'current_median_pa',
    'current_stddev_pa', 'current_mad_pa', 'current_min_pa', 'current_max_pa', 'channel_number', 'start_mux',
    'acquisition_start_seconds', 'time_since_mux_change_seconds', 'median_before_pa', 'open_pore_level_pa',
    'minknow_event_rate_per_second', 'dorado_emission_rate_bases_per_second', 'mapped_signal_span_samples',
    'samples_per_aligned_reference_base'];
const states = ['mapped_primary', 'ambiguous_primary', 'unmapped', 'no_primary'];
const digest = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
function object(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid catalog response.');
    return value as Record<string, unknown>;
}
function owner(value: unknown, jobId: string, sessionId: string, authority: string, schema: string) {
    const row = object(value);
    if (row.schema !== schema || row.job_id !== jobId || row.session_id !== sessionId
        || row.catalog_authority_sha256 !== authority || !digest(row.population_id)) {
        throw new Error('Catalog response does not match the selected source.');
    }
    return row;
}
function read(value: unknown): ReadRow {
    const row = object(value);
    if (typeof row.read_id !== 'string' || !row.read_id || !states.includes(String(row.alignment_state))
        || !count(row.source_record_count)) throw new Error('Invalid catalog read.');
    for (const key of ['length', 'mapq', 'start_1based', 'alignment_end_1based']) {
        if (row[key] !== null && !count(row[key])) throw new Error('Invalid alignment coordinate or metric.');
    }
    if (row.contig !== null && typeof row.contig !== 'string') throw new Error('Invalid reference name.');
    for (const key of sorts.filter((key) => key !== 'read_id')) {
        if (row[key] !== null && row[key] !== undefined && (typeof row[key] !== 'number' || !Number.isFinite(row[key]))) throw new Error('Invalid catalog metric.');
    }
    return row as unknown as ReadRow;
}
function records(value: unknown): RecordRow[] {
    if (!Array.isArray(value)) throw new Error('Invalid record page.');
    return value.map((value) => {
        const row = object(value);
        if (!count(row.source_record_ordinal) || !['primary', 'secondary', 'supplementary', 'unmapped'].includes(String(row.record_class))) {
            throw new Error('Invalid alignment record identity.');
        }
        for (const key of ['contig', 'cigar', 'sequence', 'quality']) {
            if (row[key] !== undefined && row[key] !== null && typeof row[key] !== 'string') throw new Error('Invalid alignment record.');
        }
        return row as unknown as RecordRow;
    });
}

/** Complete catalog and exact detail do not await or request a preview. */
export interface CatalogSignalSelection { raw_run_id: string; raw_observed_generation: number; raw_representation_id: string }
export type CatalogReadAction = (sessionId: string, read: AlignmentRead, action: "detail" | "igv" | "signal") => void;
export function CatalogReadTable({ jobId, sessionId, authority, signal, onReadAction, onSelectionIntent, selectedReadId }: {
    jobId: string; sessionId: string; authority: string; signal?: CatalogSignalSelection;
    onReadAction?: CatalogReadAction; onSelectionIntent?: () => (() => boolean); selectedReadId?: string | null;
}) {
    const base = `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions/${encodeURIComponent(sessionId)}/reads`;
    const [input, setInput] = useState('');
    const [search, setSearch] = useState('');
    const [state, setState] = useState('');
    const [sortBy, setSortBy] = useState('read_id');
    const [direction, setDirection] = useState('asc');
    const [contig, setContig] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [minimum, setMinimum] = useState('');
    const [maximum, setMaximum] = useState('');
    const [filters, setFilters] = useState<Record<string, string | number>>({});
    const [cursors, setCursors] = useState<Array<string | null>>([null]);
    const [page, setPage] = useState<Page | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [revision, setRevision] = useState(0);
    const [cacheRetryBusy, setCacheRetryBusy] = useState(false);
    const [selected, setSelected] = useState<string | null>(null);
    const [detail, setDetail] = useState<Detail | null>(null);
    const [detailError, setDetailError] = useState<string | null>(null);
    const [recordRows, setRecordRows] = useState<RecordRow[]>([]);
    const [recordCursor, setRecordCursor] = useState<string | null>(null);
    const [recordBusy, setRecordBusy] = useState(false);
    const [recordTotal, setRecordTotal] = useState<number | null>(null);
    const selectionToken = useRef(0);
    const detailRequest = useRef<AbortController | null>(null);
    const cursor = cursors[cursors.length - 1];
    const signalKey = signal ? `${signal.raw_run_id}:${signal.raw_observed_generation}:${signal.raw_representation_id}` : '';
    const currentSignal = useRef(signal); currentSignal.current = signal;
    useEffect(() => {
        const controller = new AbortController();
        selectionToken.current += 1;
        detailRequest.current?.abort();
        setDetail(null); setSelected(null); setRecordRows([]); setRecordCursor(null); setRecordTotal(null); setRecordBusy(false);
        setPage(null); setError(null); setBusy(true);
        void withAlignmentAccessRecovery(jobId, () => api.get(base, { signal: controller.signal, params: { ...currentSignal.current, ...filters, search, alignment_state: state || undefined, sort_by: sortBy, sort_direction: direction, limit: 50, cursor } }))
            .then(({ data }) => {
                if (controller.signal.aborted) return;
                const row = owner(data, jobId, sessionId, authority, 'bms.ngs.read-page.v3');
                if (!Array.isArray(row.reads) || row.reads.length > 50 || !count(row.source_read_count)
                    || !count(row.filtered_read_count) || Number(row.filtered_read_count) > Number(row.source_read_count)
                    || row.sort_by !== sortBy || row.sort_direction !== direction
                    || !['ready', 'unavailable', 'invalid'].includes(String(row.signal_metrics_state))
                    || (row.signal_cache_retry_sha256 !== null && !digest(row.signal_cache_retry_sha256))
                    || !digest(row.signal_snapshot_id)
                    || (row.preview_authority !== null && !digest(row.preview_authority))
                    || (row.next_cursor !== null && typeof row.next_cursor !== 'string')) throw new Error('Invalid complete-read page.');
                setPage({ ...row, reads: row.reads.map(read) } as unknown as Page);
            }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(describeNgsError(reason, 'Catalog could not be loaded.')); })
            .finally(() => { if (!controller.signal.aborted) setBusy(false); });
        return () => { controller.abort(); selectionToken.current += 1; detailRequest.current?.abort(); };
    }, [base, jobId, sessionId, authority, search, state, sortBy, direction, filters, signalKey, cursor, revision]);

    const retrySignalCache = async () => {
        if (cacheRetryBusy || !signal || !page?.signal_cache_retry_sha256) return;
        setCacheRetryBusy(true); setError(null);
        try {
            // Same exact source receipt; no automatic mutation or scientific rerun.
            await api.post(`${base}/signal/cache/retry`, { ...signal, artifact_sha256: page.signal_cache_retry_sha256 });
            setCursors([null]); setRevision((value) => value + 1);
        } catch (reason) { setError(describeNgsError(reason, 'Signal delivery cache retry failed.')); }
        finally { setCacheRetryBusy(false); }
    };

    const selectRead = async (readId: string, fromParent = false) => {
        if (!page) return;
        const parentIsCurrent = !fromParent && onSelectionIntent ? onSelectionIntent() : () => true;
        const token = ++selectionToken.current;
        detailRequest.current?.abort();
        const controller = new AbortController(); detailRequest.current = controller;
        setSelected(readId); setDetail(null); setDetailError(null); setRecordRows([]); setRecordCursor(null); setRecordTotal(null); setRecordBusy(false);
        try {
            const { data } = await withAlignmentAccessRecovery(jobId, () => api.post(`${base}/lookup`, { ...currentSignal.current, schema: 'bms.ngs.read-lookup-request.v2', read_id: readId,
                include_sequence: true, population_id: page.population_id }, { signal: controller.signal }));
            if (token !== selectionToken.current || controller.signal.aborted || !parentIsCurrent()) return;
            const row = owner(data, jobId, sessionId, authority, 'bms.ngs.read-lookup.v2');
            const selectedRead = read(row.read);
            if (row.population_id !== page.population_id || selectedRead.read_id !== readId || typeof row.sequence_available !== 'boolean') {
                throw new Error('Exact read response changed selection.');
            }
            const record = row.record === null ? null : records([row.record])[0];
            setDetail({ population_id: page.population_id, read: selectedRead, record, sequence_available: row.sequence_available });
            if (!fromParent) onReadAction?.(sessionId, catalogAlignmentRead(row.read, page.population_id, authority), "detail");
        } catch (reason) {
            if (token === selectionToken.current && !controller.signal.aborted && parentIsCurrent()) setDetailError(describeNgsError(reason, 'Exact read is unavailable.'));
        }
    };
    useEffect(() => {
        if (selectedReadId && page && selected !== selectedReadId) void selectRead(selectedReadId, true);
        // Restore the parent identity once per population, not on every page object.
    }, [selectedReadId, page?.population_id]);
    const loadRecords = async () => {
        if (!detail || !selected || recordBusy) return;
        const token = selectionToken.current;
        setRecordBusy(true); setDetailError(null);
        try {
            const { data } = await withAlignmentAccessRecovery(jobId, () => api.post(`${base}/records/query`, { ...currentSignal.current, schema: 'bms.ngs.alignment-record-query.v2', read_id: selected,
                include_sequence: false, population_id: detail.population_id, cursor: recordCursor, limit: 50 }, { signal: detailRequest.current?.signal }));
            if (token !== selectionToken.current) return;
            const row = owner(data, jobId, sessionId, authority, 'bms.ngs.alignment-record-page.v2');
            if (row.population_id !== detail.population_id || row.read_id !== selected || !count(row.total_record_count)
                || (row.next_cursor !== null && typeof row.next_cursor !== 'string')) throw new Error('Record page authority changed.');
            const nextRows = records(row.records);
            if (nextRows.length > 50 || nextRows.some((item, index) => index > 0 && item.source_record_ordinal <= nextRows[index - 1].source_record_ordinal)) {
                throw new Error('Record order is invalid.');
            }
            setRecordRows(nextRows); setRecordCursor(row.next_cursor as string | null); setRecordTotal(Number(row.total_record_count));
        } catch (reason) {
            if (token === selectionToken.current) setDetailError(describeNgsError(reason, 'Records are unavailable.'));
        } finally { if (token === selectionToken.current) setRecordBusy(false); }
    };
    return <section aria-label="Complete reads" className="mt-3 min-w-0 space-y-2">
        <h3 className="font-semibold">Complete reads</h3>
        <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault();
            const locus = Boolean(contig || start || end);
            if ((locus && (!contig || !Number.isSafeInteger(Number(start)) || !Number.isSafeInteger(Number(end)) || Number(start) < 1 || Number(end) < Number(start)))
                || (minimum !== '' && !Number.isFinite(Number(minimum))) || (maximum !== '' && !Number.isFinite(Number(maximum)))
                || (minimum !== '' && maximum !== '' && Number(minimum) > Number(maximum))) {
                setError('Supply a complete 1-based reference span and valid metric bounds.'); return;
            }
            setFilters({ ...(locus ? { contig, start_1based: Number(start), end_1based: Number(end) } : {}),
                ...(sortBy !== 'read_id' && minimum !== '' ? { metric_min: Number(minimum) } : {}),
                ...(sortBy !== 'read_id' && maximum !== '' ? { metric_max: Number(maximum) } : {}) });
            setSearch(input); setCursors([null]); setRevision((n) => n + 1); }}>
            <input aria-label="Literal read ID search" className="min-w-0 flex-1 rounded border bg-transparent p-1" value={input} onChange={(event) => setInput(event.target.value)} />
            <button type="submit" className="rounded border px-2">Search</button>
            <button type="button" disabled={!input || !page} className="rounded border px-2 disabled:opacity-50" onClick={() => { void selectRead(input); }}>Exact detail</button>
            <select aria-label="Alignment state" value={state} className="rounded border bg-[var(--bg-secondary)] p-1" onChange={(event) => { setState(event.target.value); setCursors([null]); }}>
                <option value="">All alignment states</option>{states.map((value) => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}
            </select>
            <select aria-label="Sort metric" value={sortBy} className="max-w-full rounded border bg-[var(--bg-secondary)] p-1" onChange={(event) => {
                setSortBy(event.target.value); setMinimum(''); setMaximum('');
                setFilters((previous) => { const { metric_min: _min, metric_max: _max, ...rest } = previous; return rest; }); setCursors([null]);
            }}>{sorts.map((name) => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select>
            <select aria-label="Sort direction" value={direction} className="rounded border bg-[var(--bg-secondary)] p-1" onChange={(event) => { setDirection(event.target.value); setCursors([null]); }}>
                <option value="asc">Ascending</option><option value="desc">Descending</option>
            </select>
            <input aria-label="Reference contig filter" placeholder="Reference contig" className="min-w-0 rounded border bg-transparent p-1" value={contig} onChange={(event) => setContig(event.target.value)} />
            <input aria-label="Reference start (1-based)" placeholder="Start (1-based)" type="number" min="1" step="1" className="w-36 rounded border bg-transparent p-1" value={start} onChange={(event) => setStart(event.target.value)} />
            <input aria-label="Reference end (inclusive)" placeholder="End (inclusive)" type="number" min="1" step="1" className="w-36 rounded border bg-transparent p-1" value={end} onChange={(event) => setEnd(event.target.value)} />
            <input aria-label="Minimum selected metric" placeholder="Metric minimum" disabled={sortBy === 'read_id'} type="number" step="any" className="w-36 rounded border bg-transparent p-1" value={minimum} onChange={(event) => setMinimum(event.target.value)} />
            <input aria-label="Maximum selected metric" placeholder="Metric maximum" disabled={sortBy === 'read_id'} type="number" step="any" className="w-36 rounded border bg-transparent p-1" value={maximum} onChange={(event) => setMaximum(event.target.value)} />
            <span className="w-full text-xs">Search applies the reference span and metric bounds. Null metrics sort last.</span>
        </form>
        {busy && <p role="status">Loading complete catalog…</p>}
        {error && <p role="alert">{error} <button type="button" className="underline" onClick={() => { setCursors([null]); setRevision((n) => n + 1); }}>Reload catalog</button></p>}
        {page && <>
            <p>{page.filtered_read_count.toLocaleString()} matching / {page.source_read_count.toLocaleString()} logical reads · {page.sort_by.replaceAll('_', ' ')} ({page.sort_direction})</p>
            <p className="text-xs">Signal metrics: {page.signal_metrics_state}. Alignment access does not depend on signal preparation.</p>
            {signal && page.signal_cache_retry_sha256 && <button type="button" disabled={cacheRetryBusy} className="text-xs underline disabled:opacity-50"
                onClick={() => { void retrySignalCache(); }}>{cacheRetryBusy ? 'Retrying signal cache…' : 'Retry signal delivery cache'}</button>}
            <div className="overflow-x-auto"><table className="w-full text-left"><thead><tr>{['Read', 'State', 'Length', 'MAPQ', 'Reference span', 'Records', sortBy.replaceAll('_', ' '), ''].map((name, index) => <th key={index} className="p-1">{name}</th>)}</tr></thead>
                <tbody>{page.reads.map((row) => <tr key={row.read_id} aria-selected={selected === row.read_id}>
                    <td className="max-w-72 break-all p-1">{row.read_id}</td><td className="p-1">{row.alignment_state.replaceAll('_', ' ')}</td>
                    <td className="p-1">{row.length ?? '—'}</td><td className="p-1">{row.mapq ?? '—'}</td>
                    <td className="p-1">{row.contig ? `${row.contig}:${row.start_1based ?? '—'}–${row.alignment_end_1based ?? '—'}` : '—'}</td>
                    <td className="p-1">{row.source_record_count}</td><td className="p-1">{typeof row[sortBy] === 'number' || typeof row[sortBy] === 'string' ? String(row[sortBy]) : '—'}</td><td><button type="button" className="rounded border px-2 py-1" onClick={() => { void selectRead(row.read_id); }}>Detail</button></td>
                </tr>)}</tbody></table></div>
            {page.reads.length === 0 && <p>No reads match this query.</p>}
            <div className="flex gap-2"><button type="button" disabled={cursors.length === 1} onClick={() => setCursors((values) => values.slice(0, -1))} className="rounded border px-2 py-1 disabled:opacity-50">Previous</button>
                <button type="button" disabled={!page.next_cursor} onClick={() => setCursors((values) => [...values, page.next_cursor])} className="rounded border px-2 py-1 disabled:opacity-50">Next</button></div>
        </>}
        {selected && <section aria-label="Exact read detail" className="space-y-2 rounded border p-2">
            <h4 className="break-all font-semibold">{selected}</h4>
            {detailError && <p role="alert">{detailError}</p>}
            {!detail && !detailError && <p role="status">Loading exact detail…</p>}
            {detail && <>
                {onReadAction && <div className="flex gap-2">
                    <button type="button" disabled={detail.read.in_preview !== true && detail.read.overlay_eligible !== true} className="rounded border px-2 py-1 disabled:opacity-50"
                        onClick={() => onReadAction(sessionId, catalogAlignmentRead(detail.read, detail.population_id, authority), "igv")}>IGV</button>
                    <button type="button" disabled={detail.read.signal_available !== true} className="rounded border px-2 py-1 disabled:opacity-50"
                        onClick={() => onReadAction(sessionId, catalogAlignmentRead(detail.read, detail.population_id, authority), "signal")}>Signal</button>
                </div>}
                <p>{detail.read.source_record_count} source records · {detail.read.alignment_state.replaceAll('_', ' ')}</p>
                {detail.sequence_available ? <><div>Sequence</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{detail.record?.sequence}</pre><div>Quality</div><pre className="max-h-24 overflow-auto whitespace-pre-wrap break-all">{detail.record?.quality ?? 'Unavailable'}</pre></> : <p>No unambiguous canonical sequence is available. Inspect the source records.</p>}
                {(recordTotal === null || recordCursor) && <button type="button" disabled={recordBusy} className="rounded border px-2 py-1" onClick={() => { void loadRecords(); }}>{recordBusy ? 'Loading records…' : recordTotal === null ? 'Inspect source records' : 'Next record page'}</button>}
                {recordTotal !== null && <p>{recordTotal} total records; {recordRows.length} on this page.</p>}
                <div className="overflow-x-auto"><table><tbody>{recordRows.map((record) => <tr key={record.source_record_ordinal}><td className="p-1">{record.source_record_ordinal}</td><td className="p-1">{record.record_class}</td><td className="p-1">{record.contig ?? '—'}:{record.start_1based ?? '—'}–{record.alignment_end_1based ?? '—'}</td><td className="max-w-64 break-all p-1">{record.cigar ?? '—'}</td></tr>)}</tbody></table></div>
            </>}
        </section>}
    </section>;
}
