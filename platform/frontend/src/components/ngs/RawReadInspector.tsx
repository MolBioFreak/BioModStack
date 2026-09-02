import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchOntRawSignalWaveform, requestOntRawSignalWaveform, type OntRawSignalWaveform } from '../../lib/api';
import {
    buildFastqDownload,
    alignmentReadIgvLocus,
    createLatestRequestGuard,
    DEFAULT_READ_QUERY_DEBOUNCE_MS,
    fetchAlignmentRead,
    fetchSortableAlignmentReads,
    isAlignmentReadScanTruncatedError,
    type AlignmentLocusSlice,
    type AlignmentRead,
    type SortableReadField,
} from '../../lib/ngsAlignmentSession';

const RAW_WAVEFORM_POLL_ATTEMPTS = 130;

interface ReadLocus {
    contig: string;
    start: number;
    end: number;
}

interface RawReadInspectorProps {
    jobId: string;
    sessionId: string;
    currentLocus?: ReadLocus | null;
    locusSlice?: AlignmentLocusSlice | null;
    rawSignalBinding?: { runId: string; observedGeneration: number; representationId: string } | null;
    onOpenRawSignal?: (read: AlignmentRead) => void;
    onNavigateIgv?: (read: AlignmentRead) => void;
}

interface GovernedRawSignalWaveformProps {
    runId: string;
    observedGeneration: number;
    representationId: string;
    readId: string;
}

const sortableFields: ReadonlyArray<{ value: SortableReadField; label: string; unit?: string }> = [
    { value: 'mean_quality', label: 'Mean basecall quality' },
    { value: 'length', label: 'Query length', unit: 'bp' },
    { value: 'mapq', label: 'Mapping quality' },
    { value: 'aligned_query_bases', label: 'Aligned query bases', unit: 'bp' },
    { value: 'aligned_reference_bases', label: 'Aligned reference bases', unit: 'bp' },
    { value: 'reference_substitution_count', label: 'Reference substitutions' },
    { value: 'inserted_bases', label: 'Inserted bases', unit: 'bp' },
    { value: 'deleted_bases', label: 'Deleted bases', unit: 'bp' },
    { value: 'clipped_bases', label: 'Clipped bases', unit: 'bp' },
    { value: 'reference_disagreement_rate', label: 'Reference disagreement rate' },
    { value: 'sample_count', label: 'Signal sample count' },
    { value: 'duration_seconds', label: 'Full-read duration', unit: 's' },
    { value: 'current_mean_pa', label: 'Mean current', unit: 'pA' },
    { value: 'current_median_pa', label: 'Median current', unit: 'pA' },
    { value: 'current_stddev_pa', label: 'Current SD', unit: 'pA' },
    { value: 'current_mad_pa', label: 'Current MAD', unit: 'pA' },
    { value: 'current_min_pa', label: 'Minimum current', unit: 'pA' },
    { value: 'current_max_pa', label: 'Maximum current', unit: 'pA' },
    { value: 'channel_number', label: 'Channel' },
    { value: 'start_mux', label: 'Mux' },
    { value: 'acquisition_start_seconds', label: 'Acquisition start', unit: 's' },
    { value: 'time_since_mux_change_seconds', label: 'Time since mux change', unit: 's' },
    { value: 'median_before_pa', label: 'Median before', unit: 'pA' },
    { value: 'open_pore_level_pa', label: 'Open-pore level', unit: 'pA' },
    { value: 'minknow_event_rate_per_second', label: 'MinKNOW event-call rate', unit: 'events/s' },
    { value: 'dorado_emission_rate_bases_per_second', label: 'Dorado move emission rate', unit: 'bases/s' },
    { value: 'mapped_signal_span_samples', label: 'Mapped signal span', unit: 'samples' },
    { value: 'samples_per_aligned_reference_base', label: 'Samples per aligned reference base' },
    { value: 'read_id', label: 'Read ID' },
];

function requestWasAborted(reason: unknown): boolean {
    const name = (reason as { name?: string } | null)?.name;
    return name === 'AbortError' || name === 'CanceledError';
}

function metricValue(read: AlignmentRead, field: SortableReadField): unknown {
    return (read as unknown as Record<string, unknown>)[field];
}

function formatMetric(value: unknown, field: SortableReadField): string {
    if (value == null) return 'unavailable';
    if (typeof value === 'string') return value;
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'unavailable';
    if (field.endsWith('_rate') || field.includes('disagreement')) return `${(value * 100).toFixed(2)}%`;
    if (Number.isInteger(value)) return value.toLocaleString();
    return Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(3);
}

export function GovernedRawSignalWaveform({ runId, observedGeneration, representationId, readId }: GovernedRawSignalWaveformProps) {
    const requestGenerationRef = useRef(0);
    const [waveform, setWaveform] = useState<OntRawSignalWaveform | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const identityKey = `${runId}:${observedGeneration}:${representationId}:${readId.trim()}`;

    useEffect(() => {
        requestGenerationRef.current += 1;
        setWaveform(null);
        setLoading(false);
        setError(null);
    }, [identityKey]);
    useEffect(() => () => { requestGenerationRef.current += 1; }, []);

    const waveformPoints = useMemo(() => {
        const samples = waveform?.state === 'ready' ? waveform.samples : null;
        if (!samples?.length) return '';
        const min = Math.min(...samples);
        const max = Math.max(...samples);
        return samples.map((value, index) => {
            const x = samples.length > 1 ? (index * 600) / (samples.length - 1) : 0;
            const y = max > min ? 118 - ((value - min) * 116) / (max - min) : 60;
            return `${x},${y}`;
        }).join(' ');
    }, [waveform]);

    const inspectWaveform = async () => {
        const exactReadId = readId.trim();
        if (!exactReadId) return;
        const requestGeneration = requestGenerationRef.current + 1;
        requestGenerationRef.current = requestGeneration;
        setLoading(true);
        setWaveform(null);
        setError(null);
        try {
            let current = await requestOntRawSignalWaveform(runId, observedGeneration, representationId, exactReadId);
            for (let attempt = 0; attempt < RAW_WAVEFORM_POLL_ATTEMPTS && (current.state === 'requested' || current.state === 'running'); attempt += 1) {
                await new Promise((resolve) => window.setTimeout(resolve, 1000));
                if (requestGeneration !== requestGenerationRef.current) return;
                current = await fetchOntRawSignalWaveform(current.lookup_id);
            }
            if (requestGeneration !== requestGenerationRef.current) return;
            if (current.run_id !== runId || current.observed_generation !== observedGeneration
                || current.representation_id !== representationId || current.read_id !== exactReadId) {
                throw new Error('Raw waveform response did not match the exact requested run generation and read.');
            }
            setWaveform(current);
            if (current.state !== 'ready') setError(current.reason_code);
        } catch (reason) {
            if (requestGeneration === requestGenerationRef.current && !requestWasAborted(reason)) {
                setError(reason instanceof Error ? reason.message : String(reason));
            }
        } finally {
            if (requestGeneration === requestGenerationRef.current) setLoading(false);
        }
    };

    return (
        <div className="space-y-2 rounded border border-emerald-500/40 bg-emerald-500/5 p-3 text-[10px]">
            <div className="font-semibold text-[var(--text-primary)]">Raw waveform</div>
            <button type="button" onClick={() => void inspectWaveform()} disabled={!readId.trim() || loading} className="rounded border border-emerald-500/50 px-2 py-1 font-semibold text-emerald-200 disabled:opacity-40">
                {loading ? 'Loading…' : 'Load waveform'}
            </button>
            {error && <div role="alert" className="text-amber-200">Waveform unavailable: {error}</div>}
            {waveform?.state === 'ready' && waveformPoints && (
                <div className="space-y-1">
                    <div>{waveform.sample_count ?? waveform.samples?.length ?? 0} samples · {waveform.samples?.length ?? 0} shown · pA</div>
                    <svg aria-label="Raw electrical signal waveform" viewBox="0 0 600 120" className="h-36 w-full rounded bg-slate-950" preserveAspectRatio="none">
                        <polyline points={waveformPoints} fill="none" stroke="rgb(52 211 153)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                    </svg>
                </div>
            )}
        </div>
    );
}

export function RawReadInspector({
    jobId,
    sessionId,
    currentLocus = null,
    locusSlice = null,
    rawSignalBinding = null,
    onOpenRawSignal,
    onNavigateIgv,
}: RawReadInspectorProps) {
    const [query, setQuery] = useState('');
    const [debouncedQuery, setDebouncedQuery] = useState('');
    const [sortBy, setSortBy] = useState<SortableReadField>('mean_quality');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const [metricMin, setMetricMin] = useState('');
    const [metricMax, setMetricMax] = useState('');
    const [reads, setReads] = useState<AlignmentRead[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [population, setPopulation] = useState<{ selected: number; filtered: number } | null>(null);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selected, setSelected] = useState<AlignmentRead | null>(null);
    const [waveform, setWaveform] = useState<OntRawSignalWaveform | null>(null);
    const [waveformError, setWaveformError] = useState<string | null>(null);
    const listGuardRef = useRef(createLatestRequestGuard());
    const detailGuardRef = useRef(createLatestRequestGuard());
    const listAbortRef = useRef<AbortController | null>(null);
    const detailAbortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        const handle = window.setTimeout(() => setDebouncedQuery(query.trim()), DEFAULT_READ_QUERY_DEBOUNCE_MS);
        return () => window.clearTimeout(handle);
    }, [query]);

    useEffect(() => {
        listAbortRef.current?.abort();
        detailAbortRef.current?.abort();
        listGuardRef.current.reset();
        detailGuardRef.current.reset();
        setReads([]);
        setCursor(null);
        setPopulation(null);
        setSelected(null);
        setWaveform(null);
        setWaveformError(null);
        setError(null);
        setLoading(false);
    }, [jobId, sessionId, locusSlice?.slice_id]);

    useEffect(() => () => {
        listAbortRef.current?.abort();
        detailAbortRef.current?.abort();
        listGuardRef.current.reset();
        detailGuardRef.current.reset();
    }, []);

    const parsedMetricMin = metricMin.trim() === '' ? undefined : Number(metricMin);
    const parsedMetricMax = metricMax.trim() === '' ? undefined : Number(metricMax);
    const rawSignalRunId = rawSignalBinding?.runId;
    const rawSignalObservedGeneration = rawSignalBinding?.observedGeneration;
    const rawSignalRepresentationId = rawSignalBinding?.representationId;
    const selectedField = sortableFields.find((field) => field.value === sortBy) || sortableFields[0];
    const detailLocus = locusSlice
        ? { contig: locusSlice.contig, start: locusSlice.start_1based, end: locusSlice.end_1based }
        : currentLocus || undefined;

    useEffect(() => {
        detailAbortRef.current?.abort();
        detailGuardRef.current.reset();
        setSelected(null);
        setWaveform(null);
        setWaveformError(null);
    }, [rawSignalRunId, rawSignalObservedGeneration, rawSignalRepresentationId]);

    const loadPage = useCallback(async (nextCursor?: string) => {
        listAbortRef.current?.abort();
        const requestToken = listGuardRef.current.begin();
        const rootRequest = !nextCursor;
        if (rootRequest) {
            detailAbortRef.current?.abort();
            detailGuardRef.current.reset();
            setReads([]);
            setCursor(null);
            setPopulation(null);
            setSelected(null);
            setWaveform(null);
            setWaveformError(null);
        }
        setError(null);
        if (!locusSlice) {
            setLoading(false);
            return;
        }
        if ((parsedMetricMin !== undefined && !Number.isFinite(parsedMetricMin))
            || (parsedMetricMax !== undefined && !Number.isFinite(parsedMetricMax))) {
            setLoading(false);
            setError('Metric bounds must be finite numbers.');
            return;
        }
        const controller = new AbortController();
        listAbortRef.current = controller;
        setLoading(true);
        try {
            const page = await fetchSortableAlignmentReads(jobId, sessionId, locusSlice.slice_id, {
                sortBy,
                sortDirection,
                q: debouncedQuery || undefined,
                metricMin: parsedMetricMin,
                metricMax: parsedMetricMax,
                cursor: nextCursor,
                limit: 50,
                rawSignalBinding: rawSignalRunId !== undefined
                    && rawSignalObservedGeneration !== undefined
                    && rawSignalRepresentationId !== undefined
                    ? {
                        runId: rawSignalRunId,
                        observedGeneration: rawSignalObservedGeneration,
                        representationId: rawSignalRepresentationId,
                    }
                    : null,
                signal: controller.signal,
            });
            if (!listGuardRef.current.isCurrent(requestToken)) return;
            setReads(nextCursor ? (current) => [...current, ...page.reads] : page.reads);
            setCursor(page.next_cursor);
            setPopulation({
                selected: page.selected_read_count,
                filtered: page.filtered_read_count,
            });
        } catch (reason) {
            if (!requestWasAborted(reason) && listGuardRef.current.isCurrent(requestToken)) {
                setError(reason instanceof Error ? reason.message : String(reason));
            }
        } finally {
            if (listGuardRef.current.isCurrent(requestToken)) setLoading(false);
        }
    }, [
        debouncedQuery, jobId, locusSlice, parsedMetricMax, parsedMetricMin,
        rawSignalObservedGeneration, rawSignalRepresentationId, rawSignalRunId,
        sessionId, sortBy, sortDirection,
    ]);

    useEffect(() => { void loadPage(); }, [loadPage]);

    const inspectRead = async (read: AlignmentRead) => {
        detailAbortRef.current?.abort();
        const controller = new AbortController();
        detailAbortRef.current = controller;
        const requestToken = detailGuardRef.current.begin();
        const requestedRunId = rawSignalRunId;
        const requestedGeneration = rawSignalObservedGeneration;
        const requestedRepresentationId = rawSignalRepresentationId;
        const requestedReadId = read.read_id;
        setSelected(read);
        setWaveform(null);
        setWaveformError(null);
        try {
            const detail = await fetchAlignmentRead(jobId, sessionId, read.read_id, {
                ...detailLocus,
                signal: controller.signal,
            });
            if (detailGuardRef.current.isCurrent(requestToken)) setSelected({ ...read, ...detail });
            if (requestedRunId !== undefined && requestedGeneration !== undefined
                && requestedRepresentationId !== undefined && detailGuardRef.current.isCurrent(requestToken)) {
                let current = await requestOntRawSignalWaveform(
                    requestedRunId,
                    requestedGeneration,
                    requestedRepresentationId,
                    requestedReadId,
                );
                for (let attempt = 0; attempt < RAW_WAVEFORM_POLL_ATTEMPTS && (current.state === 'requested' || current.state === 'running'); attempt += 1) {
                    await new Promise((resolve) => window.setTimeout(resolve, 1000));
                    if (!detailGuardRef.current.isCurrent(requestToken)) return;
                    current = await fetchOntRawSignalWaveform(current.lookup_id);
                }
                if (detailGuardRef.current.isCurrent(requestToken)) {
                    if (current.run_id !== requestedRunId || current.observed_generation !== requestedGeneration
                        || current.representation_id !== requestedRepresentationId || current.read_id !== requestedReadId) {
                        throw new Error('Raw waveform response did not match the exact requested run generation and read.');
                    }
                    setWaveform(current);
                    if (current.state !== 'ready') setWaveformError(current.reason_code);
                }
            }
        } catch (reason) {
            if (!requestWasAborted(reason) && detailGuardRef.current.isCurrent(requestToken)) {
                if (isAlignmentReadScanTruncatedError(reason)) {
                    setError('Exact read detail scan ended before absence could be proved.');
                } else {
                    setError(reason instanceof Error ? reason.message : String(reason));
                }
            }
        }
    };

    const waveformPoints = useMemo(() => {
        const samples = waveform?.state === 'ready' ? waveform.samples : null;
        if (!samples?.length) return '';
        const min = Math.min(...samples);
        const max = Math.max(...samples);
        return samples.map((value, index) => {
            const x = samples.length > 1 ? (index * 400) / (samples.length - 1) : 0;
            const y = max > min ? 98 - ((value - min) * 96) / (max - min) : 50;
            return `${x},${y}`;
        }).join(' ');
    }, [waveform]);

    const copySequence = async () => {
        if (selected?.sequence) await navigator.clipboard.writeText(selected.sequence);
    };
    const downloadRead = () => {
        if (!selected) return;
        const fastq = buildFastqDownload(selected);
        if (!fastq) return;
        const blob = new Blob([fastq], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${selected.read_id.replace(/[^A-Za-z0-9_.-]/g, '_')}.fastq`;
        anchor.click();
        URL.revokeObjectURL(url);
    };

    return (
        <aside className="absolute right-2 top-2 bottom-2 z-20 w-[min(600px,calc(100%-1rem))] overflow-hidden rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)]/95 shadow-xl flex flex-col">
            <div className="space-y-2 border-b border-[var(--border-primary)] px-3 py-2">
                <div className="text-xs font-semibold text-[var(--text-primary)]">Reads</div>
                {!locusSlice && (
                    <div role="status" className="rounded border border-amber-400/30 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
                        Load reads for this locus to begin.
                    </div>
                )}
                {locusSlice && population && (
                    <div className="text-[10px] text-[var(--text-secondary)]">
                        {population.filtered.toLocaleString()} {population.filtered === 1 ? 'result' : 'results'} · {population.selected.toLocaleString()} loaded
                    </div>
                )}
                <div className="grid grid-cols-[minmax(150px,1fr)_minmax(190px,1.2fr)_82px] gap-1">
                    <input aria-label="Filter reads by ID" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by read ID" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                    <select aria-label="Sort reads by" value={sortBy} onChange={(event) => {
                        setSortBy(event.target.value as SortableReadField);
                        setMetricMin('');
                        setMetricMax('');
                    }} className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs">
                        {sortableFields.map((field) => <option key={field.value} value={field.value}>{field.label}</option>)}
                    </select>
                    <button aria-label="Toggle sort direction" type="button" onClick={() => setSortDirection((current) => current === 'asc' ? 'desc' : 'asc')} className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs">
                        {sortDirection === 'asc' ? '↑ Asc' : '↓ Desc'}
                    </button>
                </div>
                <div className="grid grid-cols-[1fr_1fr_auto] gap-1">
                    <input aria-label="Selected metric minimum" value={metricMin} onChange={(event) => setMetricMin(event.target.value)} disabled={sortBy === 'read_id'} inputMode="decimal" placeholder="Minimum" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-[10px]" />
                    <input aria-label="Selected metric maximum" value={metricMax} onChange={(event) => setMetricMax(event.target.value)} disabled={sortBy === 'read_id'} inputMode="decimal" placeholder="Maximum" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-[10px]" />
                    <button aria-label="Refresh sortable reads" type="button" onClick={() => void loadPage()} disabled={!locusSlice || loading} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Refresh</button>
                </div>
                {error && <div role="alert" className="text-[11px] text-red-300">{error}</div>}
            </div>

            <div className="flex-1 min-h-0 overflow-auto">
                <table className="w-full min-w-[700px] border-collapse text-[10px]">
                    <thead className="sticky top-0 z-10 bg-[var(--bg-secondary)] text-left text-[var(--text-secondary)]">
                        <tr>
                            <th className="border-b border-[var(--border-primary)] px-2 py-1">Read</th>
                            <th className="border-b border-[var(--border-primary)] px-2 py-1">{selectedField.label}{selectedField.unit ? ` (${selectedField.unit})` : ''}</th>
                            <th className="border-b border-[var(--border-primary)] px-2 py-1">Q / MAPQ</th>
                            <th className="border-b border-[var(--border-primary)] px-2 py-1">Alignment</th>
                            <th className="border-b border-[var(--border-primary)] px-2 py-1">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {reads.map((read) => (
                            <tr key={`${read.read_id}:${read.start_1based ?? 0}:${read.flags}`} className="border-b border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]">
                                <td className="max-w-[250px] truncate px-2 py-1 font-mono text-[var(--text-primary)]" title={read.read_id}>{read.read_id}</td>
                                <td className="px-2 py-1 font-mono text-[var(--accent-secondary)]">{formatMetric(metricValue(read, sortBy), sortBy)}</td>
                                <td className="px-2 py-1">Q{read.mean_quality?.toFixed(1) ?? 'n/a'} / {read.mapq ?? 'n/a'}</td>
                                <td className="px-2 py-1">{read.contig || 'unmapped'}{read.start_1based ? `:${read.start_1based}` : ''} · {read.cigar || 'no CIGAR'}</td>
                                <td className="whitespace-nowrap px-2 py-1">
                                    <button aria-label={`Navigate read ${read.read_id} in IGV`} type="button" onClick={() => onNavigateIgv?.(read)} disabled={!onNavigateIgv || !alignmentReadIgvLocus(read)} className="mr-1 rounded border border-cyan-500/40 px-1.5 py-0.5 text-cyan-200 disabled:opacity-35">IGV</button>
                                    <button aria-label={`Open signal for read ${read.read_id}`} type="button" onClick={() => onOpenRawSignal?.(read)} disabled={!rawSignalBinding || !onOpenRawSignal} className="mr-1 rounded border border-emerald-500/40 px-1.5 py-0.5 text-emerald-200 disabled:opacity-35">Signal</button>
                                    <button aria-label={`Inspect details for read ${read.read_id}`} type="button" onClick={() => void inspectRead(read)} className="rounded border border-[var(--border-primary)] px-1.5 py-0.5">Detail</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {cursor && <button type="button" onClick={() => void loadPage(cursor)} disabled={loading} className="w-full px-3 py-2 text-xs text-[var(--accent-secondary)]">{loading ? 'Loading…' : 'Load 50 more'}</button>}
                {!loading && locusSlice && reads.length === 0 && <div className="p-3 text-xs text-[var(--text-secondary)]">No reads matched.</div>}
                {loading && reads.length === 0 && <div className="p-3 text-xs text-[var(--text-secondary)]">Loading reads…</div>}
            </div>

            {selected && (
                <div className="max-h-[42%] overflow-auto border-t border-[var(--border-primary)] p-3 text-[11px]">
                    <div className="flex items-start justify-between gap-2">
                        <div>
                            <div className="break-all font-mono text-[var(--text-primary)]">{selected.read_id}</div>
                            <div className="mt-1 text-[var(--text-secondary)]">{selected.contig || 'unmapped'} · {selected.start_1based ?? 'n/a'} · {selected.strand} · MAPQ {selected.mapq ?? 'n/a'} · {selected.cigar || 'no CIGAR'}</div>
                        </div>
                        <div className="flex gap-1">
                            <button aria-label={`Navigate selected read ${selected.read_id} in IGV`} type="button" onClick={() => onNavigateIgv?.(selected)} disabled={!onNavigateIgv || !alignmentReadIgvLocus(selected)} className="rounded border border-cyan-500/40 px-2 py-1 text-cyan-200 disabled:opacity-35">Navigate IGV</button>
                            <button aria-label={`Open signal for selected read ${selected.read_id}`} type="button" onClick={() => onOpenRawSignal?.(selected)} disabled={!rawSignalBinding || !onOpenRawSignal} className="rounded border border-emerald-500/40 px-2 py-1 text-emerald-200 disabled:opacity-35">Open signal</button>
                        </div>
                    </div>
                    {waveform?.state === 'ready' && waveform.samples && (
                        <div className="mt-2">
                            <div className="text-[10px] text-[var(--text-secondary)]">{waveform.sample_count ?? waveform.samples.length} samples · {waveform.samples.length} shown · pA</div>
                            <svg viewBox="0 0 400 100" className="mt-1 h-24 w-full rounded bg-[var(--bg-primary)]" role="img" aria-label="Raw electrical signal waveform">
                                <polyline fill="none" stroke="currentColor" strokeWidth="1" className="text-cyan-300" points={waveformPoints} />
                            </svg>
                        </div>
                    )}
                    {waveformError && <div className="mt-2 text-[10px] text-amber-300">Raw signal: {waveformError}</div>}
                    {selected.sequence && (
                        <>
                            <div className="mt-2 text-[10px] text-[var(--text-secondary)]">Basecalled sequence</div>
                            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-primary)] p-2 font-mono text-[10px] text-[var(--text-primary)]">{selected.sequence}</pre>
                            <div className="mt-2 flex gap-2">
                                <button type="button" onClick={() => void copySequence()} className="rounded border border-[var(--border-primary)] px-2 py-1">Copy sequence</button>
                                <button type="button" onClick={downloadRead} disabled={!buildFastqDownload(selected)} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40">Download FASTQ</button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </aside>
    );
}
