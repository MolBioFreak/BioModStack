import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
    buildFastqDownload,
    createLatestRequestGuard,
    DEFAULT_READ_QUERY_DEBOUNCE_MS,
    fetchAlignmentRead,
    fetchAlignmentReads,
    isAlignmentReadScanTruncatedError,
    type AlignmentRead,
} from '../../lib/ngsAlignmentSession';

interface ReadLocus {
    contig: string;
    start: number;
    end: number;
}

interface RawReadInspectorProps {
    jobId: string;
    sessionId: string;
    currentLocus?: ReadLocus | null;
}

function requestWasAborted(reason: unknown): boolean {
    const name = (reason as { name?: string } | null)?.name;
    return name === 'AbortError' || name === 'CanceledError';
}

export function RawReadInspector({ jobId, sessionId, currentLocus = null }: RawReadInspectorProps) {
    const [query, setQuery] = useState('');
    const [debouncedQuery, setDebouncedQuery] = useState('');
    const [contig, setContig] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [reads, setReads] = useState<AlignmentRead[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [scanTruncated, setScanTruncated] = useState(false);
    const [selected, setSelected] = useState<AlignmentRead | null>(null);
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
        setSelected(null);
        setError(null);
        setScanTruncated(false);
        setLoading(false);
    }, [jobId, sessionId]);

    useEffect(() => () => {
        listAbortRef.current?.abort();
        detailAbortRef.current?.abort();
        listGuardRef.current.reset();
        detailGuardRef.current.reset();
    }, []);

    const parsedLocus = useMemo(() => {
        const parsedStart = Number(start);
        const parsedEnd = Number(end);
        if (!contig.trim() || !Number.isInteger(parsedStart) || !Number.isInteger(parsedEnd) || parsedStart < 1 || parsedEnd < parsedStart) {
            return undefined;
        }
        return { contig: contig.trim(), start: parsedStart, end: parsedEnd };
    }, [contig, end, start]);

    const loadPage = useCallback(async (nextCursor?: string) => {
        listAbortRef.current?.abort();
        const controller = new AbortController();
        listAbortRef.current = controller;
        const requestToken = listGuardRef.current.begin();
        setLoading(true);
        setError(null);
        if (!nextCursor) {
            setSelected(null);
            setScanTruncated(false);
        }
        try {
            const page = await fetchAlignmentReads(jobId, sessionId, {
                q: debouncedQuery || undefined,
                cursor: nextCursor,
                limit: 50,
                ...parsedLocus,
                signal: controller.signal,
            });
            if (!listGuardRef.current.isCurrent(requestToken)) return;
            setReads(nextCursor ? (current) => [...current, ...page.reads] : page.reads);
            setCursor(page.next_cursor);
            setScanTruncated(page.scan_truncated);
        } catch (reason) {
            if (!requestWasAborted(reason) && listGuardRef.current.isCurrent(requestToken)) {
                setError(reason instanceof Error ? reason.message : String(reason));
            }
        } finally {
            if (listGuardRef.current.isCurrent(requestToken)) setLoading(false);
        }
    }, [debouncedQuery, jobId, parsedLocus, sessionId]);

    useEffect(() => {
        void loadPage();
    }, [loadPage]);

    const inspectRead = async (readId: string) => {
        detailAbortRef.current?.abort();
        const controller = new AbortController();
        detailAbortRef.current = controller;
        const requestToken = detailGuardRef.current.begin();
        setLoading(true);
        setError(null);
        setSelected(null);
        try {
            const detail = await fetchAlignmentRead(jobId, sessionId, readId, {
                ...parsedLocus,
                signal: controller.signal,
            });
            if (detailGuardRef.current.isCurrent(requestToken)) setSelected(detail);
        } catch (reason) {
            if (!requestWasAborted(reason) && detailGuardRef.current.isCurrent(requestToken)) {
                if (isAlignmentReadScanTruncatedError(reason)) setScanTruncated(true);
                setError(reason instanceof Error ? reason.message : String(reason));
            }
        } finally {
            if (detailGuardRef.current.isCurrent(requestToken)) setLoading(false);
        }
    };

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

    const useCurrentLocus = () => {
        if (!currentLocus) return;
        setContig(currentLocus.contig);
        setStart(String(currentLocus.start));
        setEnd(String(currentLocus.end));
    };

    return (
        <aside className="absolute right-2 top-2 bottom-2 z-20 w-[410px] overflow-hidden rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)]/95 shadow-xl flex flex-col">
            <div className="px-3 py-2 border-b border-[var(--border-primary)]">
                <div className="text-xs font-semibold text-[var(--text-primary)]">Basecalled read inspector</div>
                <div className="text-[10px] text-amber-300">Raw electrical signal is unavailable in this BAM-backed session.</div>
                <div className="flex gap-1 mt-1">
                    <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Filter by read ID (debounced)"
                        className="min-w-0 flex-1 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)]"
                    />
                    <button type="button" onClick={() => void loadPage()} disabled={loading} className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs">
                        Search
                    </button>
                </div>
                <div className="mt-1 grid grid-cols-[1fr_72px_72px_auto] gap-1">
                    <input value={contig} onChange={(event) => setContig(event.target.value)} placeholder="Contig" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-0.5 text-[10px]" />
                    <input value={start} onChange={(event) => setStart(event.target.value)} inputMode="numeric" placeholder="Start" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-0.5 text-[10px]" />
                    <input value={end} onChange={(event) => setEnd(event.target.value)} inputMode="numeric" placeholder="End" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-0.5 text-[10px]" />
                    <button type="button" onClick={useCurrentLocus} disabled={!currentLocus} className="rounded border border-[var(--border-primary)] px-1 py-0.5 text-[10px] disabled:opacity-40">IGV locus</button>
                </div>
                {scanTruncated && <div className="mt-1 text-[11px] text-amber-300">Scan budget exhausted; this result set may be incomplete.</div>}
                {error && <div className="mt-1 text-[11px] text-red-300">{error}</div>}
            </div>
            <div className="flex-1 min-h-0 overflow-auto">
                {reads.map((read) => (
                    <button
                        type="button"
                        key={`${read.read_id}:${read.start_1based ?? 0}:${read.flags}`}
                        onClick={() => void inspectRead(read.read_id)}
                        className="w-full text-left px-3 py-1.5 border-b border-[var(--border-primary)] hover:bg-[var(--bg-tertiary)]"
                    >
                        <div className="text-xs font-mono text-[var(--text-primary)] truncate">{read.read_id}</div>
                        <div className="text-[10px] text-[var(--text-secondary)]">
                            {read.length ?? 'n/a'} bp · Q{read.mean_quality?.toFixed(1) ?? 'n/a'} · {read.contig || 'unmapped'}{read.start_1based ? `:${read.start_1based}` : ''} · {read.strand} · MAPQ {read.mapq ?? 'n/a'} · {read.cigar || 'no CIGAR'} · flags {read.flags}
                        </div>
                    </button>
                ))}
                {cursor && (
                    <button type="button" onClick={() => void loadPage(cursor)} disabled={loading} className="w-full px-3 py-2 text-xs text-[var(--accent-secondary)]">
                        {loading ? 'Loading…' : 'Load 50 more'}
                    </button>
                )}
                {!loading && reads.length === 0 && <div className="p-3 text-xs text-[var(--text-secondary)]">No reads matched.</div>}
            </div>
            {selected && (
                <div className="max-h-[48%] overflow-auto border-t border-[var(--border-primary)] p-3 text-[11px]">
                    <div className="font-mono text-[var(--text-primary)] break-all">{selected.read_id}</div>
                    <div className="text-[var(--text-secondary)] mt-1">
                        {selected.contig || 'unmapped'} · {selected.start_1based ?? 'n/a'} · {selected.strand} · MAPQ {selected.mapq ?? 'n/a'} · {selected.cigar || 'no CIGAR'} · flags {selected.flags}
                    </div>
                    {selected.sequence && (
                        <>
                            <div className="mt-2 text-[10px] text-[var(--text-secondary)]">Basecalled sequence</div>
                            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-primary)] p-2 font-mono text-[10px] text-[var(--text-primary)]">{selected.sequence}</pre>
                            <div className="mt-2 text-[10px] text-[var(--text-secondary)]">FASTQ quality string</div>
                            {selected.quality ? (
                                <pre className="max-h-20 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-primary)] p-2 font-mono text-[10px] text-[var(--text-primary)]">{selected.quality}</pre>
                            ) : (
                                <div className="rounded bg-amber-500/10 p-2 text-amber-300">Quality unavailable; FASTQ export is disabled.</div>
                            )}
                            <div className="flex gap-2 mt-2">
                                <button type="button" onClick={() => void copySequence()} className="rounded border border-[var(--border-primary)] px-2 py-1">Copy sequence</button>
                                <button type="button" onClick={downloadRead} disabled={!buildFastqDownload(selected)} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40">Download FASTQ</button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </aside>
    );
}
