import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import {
    fetchOntExternalPod5Candidates,
    fetchOntPod5Restore,
    fetchOntRawSignalOpenedReads,
    fetchOntRawSignalReads,
    fetchOntRawSignalRetention,
    fetchOntRawSignalSources,
    fetchOntRawSignalWaveform,
    importOntRawSignalUpload,
    registerOntExternalPod5Candidate,
    requestOntBarcodeBasecallHandoff,
    requestOntBlow5Preparation,
    requestOntPod5Restore,
    requestOntRawSignalWaveform,
    saveOntRawSignalRetention,
    uploadFile,
    type OntRawSignalSource,
    type OntRawSignalWaveform,
} from '../../lib/api';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

const READ_PAGE = 50;
const WAVEFORM_POLL_ATTEMPTS = 130;
const CONVERTING = ['requested', 'running', 'publishing', 'deferred'];
const CONVERSION_WORDS: Record<string, string> = {
    requested: 'Waiting to convert', running: 'Converting…', publishing: 'Finishing conversion…',
    deferred: 'Waiting for free resources', failed: 'Conversion failed', cancelled: 'Conversion cancelled',
};

interface OntRawSignalPanelProps {
    /** Opens Data Analysis, optionally with the ONT form filled in. */
    onAnalyzeExistingData: (values?: Record<string, unknown>) => void;
}

const fileSize = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
const day = (value: string | null) => value
    ? new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
const count = (value: number) => value.toLocaleString('en-US');

function failure(error: unknown, fallback: string): string {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof detail === 'string' ? detail : error instanceof Error ? error.message : fallback;
}

function sourceState(source: OntRawSignalSource): { label: string; tone: 'ok' | 'busy' | 'bad' | 'idle' } {
    if (source.blow5) return { label: 'BLOW5 ready', tone: 'ok' };
    const state = source.conversion?.state;
    if (state && CONVERTING.includes(state)) return { label: CONVERSION_WORDS[state], tone: 'busy' };
    if (state === 'failed') return { label: 'Conversion failed', tone: 'bad' };
    if (!source.source) return { label: source.origin === 'sequenced' && !['completed', 'stopped'].includes(source.status) ? `Run ${source.status}` : 'No signal files', tone: 'idle' };
    return { label: 'Not converted', tone: 'idle' };
}

const TONE = {
    ok: 'border-[color-mix(in_srgb,var(--success)_50%,transparent)] text-[var(--success)]',
    busy: 'border-[color-mix(in_srgb,var(--accent-primary)_50%,transparent)] text-[var(--accent-primary)]',
    bad: 'border-[color-mix(in_srgb,var(--error)_50%,transparent)] text-[var(--error)]',
    idle: 'border-[var(--border-primary)] text-[var(--text-secondary)]',
};
const BUTTON = 'rounded-lg border border-[var(--border-secondary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50';
const PRIMARY = 'rounded-lg border border-[var(--accent-primary)] px-3 py-2 text-sm font-semibold text-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-50';
const HEADING = 'text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]';

const PLOT = { width: 860, height: 300, left: 58, right: 10, top: 10, bottom: 26 };

/** One read's current trace. `samples` is the whole read, thinned evenly when it is longer than the display limit. */
function Waveform({ wave }: { wave: OntRawSignalWaveform }) {
    const samples = wave.samples ?? [];
    const plot = useMemo(() => {
        if (samples.length < 2) return null;
        let low = Infinity;
        let high = -Infinity;
        for (const value of samples) { if (value < low) low = value; if (value > high) high = value; }
        const span = high - low || 1;
        const width = PLOT.width - PLOT.left - PLOT.right;
        const height = PLOT.height - PLOT.top - PLOT.bottom;
        const points = samples.map((value, index) =>
            `${(PLOT.left + (index / (samples.length - 1)) * width).toFixed(1)},${(PLOT.top + height - ((value - low) / span) * height).toFixed(1)}`).join(' ');
        return { low, high, points, width, height };
    }, [samples]);
    if (!plot) return null;
    const total = wave.sample_count ?? samples.length;
    return (
        <svg role="img" aria-label="Raw electrical signal waveform" viewBox={`0 0 ${PLOT.width} ${PLOT.height}`} className="w-full">
            <rect x={PLOT.left} y={PLOT.top} width={plot.width} height={plot.height} fill="var(--bg-primary)" stroke="var(--border-primary)" />
            {[0.25, 0.5, 0.75].map((share) => (
                <line key={share} x1={PLOT.left + plot.width * share} x2={PLOT.left + plot.width * share} y1={PLOT.top} y2={PLOT.top + plot.height} stroke="var(--bg-tertiary)" />
            ))}
            <polyline points={plot.points} fill="none" stroke="var(--accent-primary)" strokeWidth="1" />
            <g fill="var(--text-secondary)" fontSize="12">
                <text x={PLOT.left - 6} y={PLOT.top + 10} textAnchor="end">{plot.high.toFixed(0)} pA</text>
                <text x={PLOT.left - 6} y={PLOT.top + plot.height} textAnchor="end">{plot.low.toFixed(0)} pA</text>
                <text x={PLOT.left} y={PLOT.height - 6}>0</text>
                <text x={PLOT.left + plot.width / 2} y={PLOT.height - 6} textAnchor="middle">sample</text>
                <text x={PLOT.left + plot.width} y={PLOT.height - 6} textAnchor="end">{count(total)}</text>
            </g>
        </svg>
    );
}

/** Raw signal of a Project: runs sequenced here and imported files, their BLOW5, and one read's trace. */
export function OntRawSignalPanel({ onAnalyzeExistingData }: OntRawSignalPanelProps) {
    const { selectedDomainExperiment, contextHref } = useGlobalExperimentContext();
    const projectId = selectedDomainExperiment?.domain_experiment_id ?? null;
    const queryClient = useQueryClient();
    const requestedRunId = new URLSearchParams(useLocation().search).get('run_id');
    const [selectedRunId, setSelectedRunId] = useState<string | null>(requestedRunId);
    useEffect(() => { if (requestedRunId) setSelectedRunId(requestedRunId); }, [requestedRunId]);
    const [readFilter, setReadFilter] = useState('');
    const [readOffset, setReadOffset] = useState(0);
    const [openRead, setOpenRead] = useState<{ runId: string; readId: string } | null>(null);
    const [adding, setAdding] = useState(false);
    const [notice, setNotice] = useState('');

    const sourcesQuery = useQuery({
        queryKey: ['ont-raw-signal-sources', projectId],
        queryFn: async () => (await fetchOntRawSignalSources(projectId as string)).sources,
        enabled: projectId !== null,
        retry: false,
        refetchInterval: (query) => query.state.data?.some((item) =>
            (item.conversion && CONVERTING.includes(item.conversion.state)) || ['starting', 'running', 'stopping'].includes(item.status)) ? 3000 : false,
    });
    const sources = useMemo(() => sourcesQuery.data ?? [], [sourcesQuery.data]);
    const selected = sources.find((item) => item.run_id === selectedRunId) ?? sources[0] ?? null;
    const blow5Id = selected?.blow5?.representation_id ?? null;
    const refreshSources = () => void queryClient.invalidateQueries({ queryKey: ['ont-raw-signal-sources', projectId] });

    useEffect(() => { setReadFilter(''); setReadOffset(0); setNotice(''); }, [selected?.run_id]);

    const readsQuery = useQuery({
        queryKey: ['ont-raw-signal-reads', blow5Id, readFilter, readOffset],
        queryFn: () => fetchOntRawSignalReads(selected!.run_id, selected!.observed_generation, blow5Id as string,
            { offset: readOffset, limit: READ_PAGE, query: readFilter }),
        enabled: Boolean(selected && blow5Id),
        retry: false,
        placeholderData: (previous) => previous,
    });
    const openedQuery = useQuery({
        queryKey: ['ont-raw-signal-opened', selected?.run_id, selected?.observed_generation],
        queryFn: async () => (await fetchOntRawSignalOpenedReads(selected!.run_id, selected!.observed_generation)).lookups,
        enabled: Boolean(selected && blow5Id),
        retry: false,
    });
    const opened = (openedQuery.data ?? []).filter((item) => item.state === 'ready' && item.representation_id === blow5Id);
    const shownRead = openRead && selected && openRead.runId === selected.run_id ? openRead.readId : null;

    const waveformQuery = useQuery({
        queryKey: ['ont-raw-signal-waveform', blow5Id, shownRead],
        queryFn: async () => {
            let current = await requestOntRawSignalWaveform(selected!.run_id, selected!.observed_generation, blow5Id as string, shownRead as string);
            for (let attempt = 0; attempt < WAVEFORM_POLL_ATTEMPTS && (current.state === 'requested' || current.state === 'running'); attempt += 1) {
                await new Promise((resolve) => window.setTimeout(resolve, 1000));
                current = await fetchOntRawSignalWaveform(current.lookup_id);
            }
            void queryClient.invalidateQueries({ queryKey: ['ont-raw-signal-opened', selected!.run_id, selected!.observed_generation] });
            return current;
        },
        enabled: Boolean(selected && blow5Id && shownRead),
        retry: false,
        staleTime: Infinity,
    });
    const wave = waveformQuery.data;

    const convert = useMutation({
        mutationFn: () => requestOntBlow5Preparation(selected!.run_id, selected!.observed_generation, selected!.source!.representation_id),
        onSuccess: () => { setNotice(''); refreshSources(); },
        onError: (error) => setNotice(failure(error, 'The conversion could not be started.')),
    });
    const restoreQuery = useQuery({
        queryKey: ['ont-pod5-restore', selected?.run_id, selected?.observed_generation, blow5Id],
        queryFn: () => fetchOntPod5Restore(selected!.run_id, selected!.observed_generation, blow5Id as string),
        enabled: Boolean(selected && blow5Id),
        retry: false,
        refetchInterval: (query) => (query.state.data?.state === 'running' ? 5000 : false),
    });
    const restore = useMutation({
        mutationFn: () => requestOntPod5Restore(selected!.run_id, selected!.observed_generation, blow5Id as string),
        onSuccess: () => void restoreQuery.refetch(),
        onError: (error) => setNotice(failure(error, 'POD5 could not be restored.')),
    });
    const basecallRun = useMutation({
        mutationFn: async () => (await requestOntBarcodeBasecallHandoff(selected!.run_id)).data,
        onSuccess: (handoff) => onAnalyzeExistingData({
            selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: handoff.pod5_dir, runModkit: false, runFastqQc: false, runAssembly: false,
            barcodeKit: handoff.barcode_kit ?? '', sampleSheet: handoff.sample_sheet ?? '',
        }),
        onError: (error) => setNotice(failure(error, 'The run could not be opened for basecalling.')),
    });

    const state = selected ? sourceState(selected) : null;
    const converting = Boolean(selected?.conversion && CONVERTING.includes(selected.conversion.state));
    const restored = restoreQuery.data;
    const readPage = readsQuery.data;
    const group = (origin: OntRawSignalSource['origin']) => sources.filter((item) => item.origin === origin);

    const list = (title: string, items: OntRawSignalSource[], empty: string) => (
        <div className="space-y-2">
            <h3 className={HEADING}>{title}</h3>
            {items.length ? items.map((item) => {
                const itemState = sourceState(item);
                return (
                    <button key={item.run_id} type="button" aria-pressed={selected?.run_id === item.run_id} onClick={() => setSelectedRunId(item.run_id)}
                        className={`grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-1 rounded-lg border px-3 py-2 text-left ${selected?.run_id === item.run_id
                            ? 'border-[var(--accent-primary)] bg-[var(--bg-secondary)]' : 'border-[var(--border-primary)] hover:bg-[var(--bg-secondary)]'}`}>
                        <span className="truncate text-sm font-semibold text-[var(--text-primary)]" title={item.name}>{item.name}</span>
                        <span className={`self-start whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] ${TONE[itemState.tone]}`}>{itemState.label}</span>
                        <span className="col-span-2 whitespace-nowrap text-xs text-[var(--text-secondary)]">
                            {item.origin === 'sequenced' ? 'Sequenced' : 'Imported'} {day(item.created_at)}
                            {item.source ? ` · ${item.source.format.toUpperCase()} ${fileSize(item.source.bytes)}` : ''}
                        </span>
                    </button>
                );
            }) : <p className="rounded-lg border border-dashed border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-muted)]">{empty}</p>}
        </div>
    );

    return (
        <section className="space-y-4 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-semibold text-[var(--text-primary)]">Raw signal</h2>
                <button type="button" disabled={!projectId} onClick={() => setAdding(true)} className={PRIMARY}>+ Add raw signal</button>
            </div>
            {!projectId ? <p role="status" className="text-sm text-[var(--text-secondary)]">Choose a Project (Projects button above) to see its raw signal.</p> : null}
            {sourcesQuery.isError ? <p role="alert" className="text-sm text-[var(--warning)]">Raw signal could not be loaded: {failure(sourcesQuery.error, 'unknown error')}</p> : null}
            {projectId && !sourcesQuery.isError ? (
                <div className="grid items-start gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
                    <div className="space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                        {sourcesQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading…</p> : <>
                            {list('Sequenced on this system', group('sequenced'), 'No runs sequenced here in this Project yet.')}
                            {list('Imported files', group('imported'), 'Nothing imported yet. Use Add raw signal.')}
                        </>}
                    </div>
                    {selected && state ? (
                        <div data-testid="ont-raw-signal-detail" className="min-w-0 space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                            <div className="truncate text-base font-semibold text-[var(--text-primary)]" title={selected.name}>{selected.name}</div>
                            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-[var(--text-primary)]">
                                <div><dt className={HEADING}>{selected.origin === 'sequenced' ? 'Sequenced' : 'Imported'}</dt><dd className="font-semibold">{day(selected.created_at)}</dd></div>
                                <div><dt className={HEADING}>Original</dt><dd className="font-semibold">
                                    {selected.source ? `${selected.source.format.toUpperCase()} ${fileSize(selected.source.bytes)}, ${selected.source.removed ? 'removed after conversion' : 'kept'}` : 'none on record'}
                                </dd></div>
                                <div><dt className={HEADING}>Indexed BLOW5</dt><dd className="font-semibold">
                                    {selected.blow5 ? `ready${selected.blow5.read_count != null ? `, ${count(selected.blow5.read_count)} reads` : ''}` : state.label}
                                </dd></div>
                            </dl>
                            <div className="flex flex-wrap items-center gap-2">
                                {!selected.blow5 && selected.source && !selected.source.removed ? (
                                    <button type="button" disabled={converting || convert.isPending} onClick={() => convert.mutate()} className={PRIMARY}>
                                        {converting ? CONVERSION_WORDS[selected.conversion!.state] : selected.source.format === 'blow5' ? 'Check indexed BLOW5' : 'Convert to indexed BLOW5'}
                                    </button>
                                ) : null}
                                {selected.blow5 ? (
                                    <button type="button" disabled={restore.isPending || restored?.state === 'running'} onClick={() => restore.mutate()} className={BUTTON}
                                        title="Rebuild POD5 from this BLOW5 so the reads can be basecalled">
                                        {restored?.state === 'running' ? 'Restoring POD5…' : 'Restore POD5 for basecalling'}
                                    </button>
                                ) : null}
                                {restored?.state === 'ready' && restored.directory ? (
                                    <button type="button" className={BUTTON}
                                        onClick={() => onAnalyzeExistingData({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: restored.directory })}>
                                        Basecall restored POD5{restored.restored_read_count != null ? ` (${count(restored.restored_read_count)} reads)` : ''}
                                    </button>
                                ) : null}
                                {selected.origin === 'sequenced' && ['completed', 'stopped'].includes(selected.status) ? (
                                    <button type="button" disabled={basecallRun.isPending} onClick={() => basecallRun.mutate()} className={BUTTON}>Basecall and demultiplex this run</button>
                                ) : null}
                            </div>
                            {selected.conversion?.state === 'failed' ? <p role="alert" className="text-sm text-[var(--warning)]">Conversion failed: {selected.conversion.reason_code.replace(/_/g, ' ')}. Convert again to retry.</p> : null}
                            {restored?.state === 'failed' ? <p role="alert" className="text-sm text-[var(--warning)]">POD5 restore failed: {restored.error}</p> : null}
                            {notice ? <p role="alert" className="text-sm text-[var(--warning)]">{notice}</p> : null}

                            {selected.blow5 ? (
                                <div className="grid items-start gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
                                    <div className="space-y-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                                        <h3 className={HEADING}>Reads</h3>
                                        <input aria-label="Find a read" value={readFilter} placeholder="Find by read ID"
                                            onChange={(event) => { setReadFilter(event.target.value.replace(/[^A-Za-z0-9_.-]/g, '')); setReadOffset(0); }}
                                            className="w-full rounded-lg border border-[var(--border-secondary)] bg-[var(--bg-primary)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]" />
                                        {opened.length && !readFilter ? (
                                            <div className="space-y-1">
                                                <h4 className={HEADING}>Opened before</h4>
                                                <ul aria-label="Reads opened before">
                                                    {opened.slice(0, 6).map((item) => (
                                                        <li key={item.lookup_id}>
                                                            <button type="button" aria-pressed={shownRead === item.read_id} onClick={() => setOpenRead({ runId: selected.run_id, readId: item.read_id })}
                                                                className={`flex w-full justify-between gap-2 rounded px-2 py-1 text-left text-xs ${shownRead === item.read_id ? 'bg-[color-mix(in_srgb,var(--accent-primary)_16%,transparent)] text-[var(--accent-primary)]' : 'text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'}`}>
                                                                <span className="truncate font-mono" title={item.read_id}>{item.read_id}</span>
                                                                <span className="whitespace-nowrap text-[var(--text-secondary)]">{item.sample_count != null ? count(item.sample_count) : ''}</span>
                                                            </button>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                        <div className="space-y-1">
                                            <h4 className={HEADING}>{readFilter ? 'Matching reads' : 'All reads'}</h4>
                                            {readsQuery.isError ? <p role="alert" className="text-xs text-[var(--warning)]">Reads could not be listed: {failure(readsQuery.error, 'unknown error')}</p> : null}
                                            <ul aria-label="Reads in this file">
                                                {(readPage?.read_ids ?? []).map((readId) => (
                                                    <li key={readId}>
                                                        <button type="button" aria-pressed={shownRead === readId} onClick={() => setOpenRead({ runId: selected.run_id, readId })} title={readId}
                                                            className={`block w-full truncate rounded px-2 py-1 text-left font-mono text-xs ${shownRead === readId ? 'bg-[color-mix(in_srgb,var(--accent-primary)_16%,transparent)] text-[var(--accent-primary)]' : 'text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'}`}>
                                                            {readId}
                                                        </button>
                                                    </li>
                                                ))}
                                            </ul>
                                            {readPage && !readPage.read_ids.length ? <p className="text-xs text-[var(--text-muted)]">No read ID contains “{readFilter}”.</p> : null}
                                            {readPage && readPage.matched > 0 ? (
                                                <div className="flex items-center justify-between gap-2 pt-1 text-xs text-[var(--text-secondary)]">
                                                    <span>{count(readPage.offset + 1)}–{count(readPage.offset + readPage.read_ids.length)} of {count(readPage.matched)}</span>
                                                    <span className="flex gap-1">
                                                        <button type="button" aria-label="Previous reads" disabled={readOffset === 0} onClick={() => setReadOffset(Math.max(0, readOffset - READ_PAGE))} className="rounded border border-[var(--border-primary)] px-2 py-0.5 disabled:opacity-40">‹</button>
                                                        <button type="button" aria-label="Next reads" disabled={readOffset + READ_PAGE >= readPage.matched} onClick={() => setReadOffset(readOffset + READ_PAGE)} className="rounded border border-[var(--border-primary)] px-2 py-0.5 disabled:opacity-40">›</button>
                                                    </span>
                                                </div>
                                            ) : null}
                                        </div>
                                    </div>
                                    <div className="min-w-0 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                                        {!shownRead ? <p className="text-sm text-[var(--text-secondary)]">Pick a read to see its signal.</p> : null}
                                        {shownRead ? (
                                            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                                                <span className="break-all font-mono text-sm text-[var(--text-primary)]">{shownRead}</span>
                                                {wave?.state === 'ready' ? <span className="whitespace-nowrap text-xs text-[var(--text-secondary)]">{count(wave.sample_count ?? 0)} samples{(wave.samples?.length ?? 0) < (wave.sample_count ?? 0) ? ` · ${count(wave.samples?.length ?? 0)} drawn` : ''}</span> : null}
                                            </div>
                                        ) : null}
                                        {shownRead && waveformQuery.isFetching ? <p className="text-sm text-[var(--text-secondary)]">Reading the signal…</p> : null}
                                        {shownRead && waveformQuery.isError ? <p role="alert" className="text-sm text-[var(--warning)]">Signal could not be read: {failure(waveformQuery.error, 'unknown error')}</p> : null}
                                        {shownRead && wave && wave.state !== 'ready' && !waveformQuery.isFetching ? <p role="alert" className="text-sm text-[var(--warning)]">Signal could not be read: {wave.reason_code.replace(/_/g, ' ')}</p> : null}
                                        {shownRead && wave?.state === 'ready' && !waveformQuery.isFetching ? <Waveform wave={wave} /> : null}
                                    </div>
                                </div>
                            ) : (
                                <p className="text-sm text-[var(--text-secondary)]">
                                    {selected.source ? 'Reads and their signal show here once the file is converted to indexed BLOW5. The conversion is lossless and keeps the original.' : 'This run has no raw signal files on record yet.'}
                                </p>
                            )}

                            <div className="space-y-2">
                                <h3 className={HEADING}>Results from this signal</h3>
                                {selected.results.length ? (
                                    <ul aria-label="Results from this signal" className="space-y-1">
                                        {selected.results.slice(0, 8).map((result) => (
                                            <li key={result.job_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm">
                                                <span className="min-w-0 truncate text-[var(--text-primary)]" title={result.name}>{result.name} <span className="text-xs text-[var(--text-secondary)]">{day(result.created_at)}</span></span>
                                                <span className="flex gap-3 whitespace-nowrap">
                                                    {result.signal_workbench ? <Link className="text-[var(--text-action)]" to={contextHref('/ngs', { section: 'analyses', job_id: result.job_id, view: 'workbench' })}>Squiggle views</Link> : null}
                                                    <Link className="text-[var(--text-action)]" to={contextHref('/ngs', { section: 'analyses', job_id: result.job_id, view: null })}>Open result</Link>
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                ) : <p className="text-sm text-[var(--text-secondary)]">None yet. Squigualiser views (bases over the signal) open from a basecalled or aligned result of this file.</p>}
                            </div>
                        </div>
                    ) : !sourcesQuery.isLoading ? (
                        <div className="rounded-xl border border-dashed border-[var(--border-primary)] p-6 text-sm text-[var(--text-secondary)]">
                            This Project has no raw signal yet. Runs started from Instrument setup appear here on their own; use Add raw signal for POD5, SLOW5 or BLOW5 from elsewhere.
                        </div>
                    ) : null}
                </div>
            ) : null}
            {adding && projectId ? (
                <AddRawSignal projectId={projectId} onClose={() => setAdding(false)}
                    onAdded={(runId) => { setAdding(false); setSelectedRunId(runId); refreshSources(); }} />
            ) : null}
        </section>
    );
}

function AddRawSignal({ projectId, onClose, onAdded }: { projectId: string; onClose: () => void; onAdded: (runId: string) => void }) {
    const queryClient = useQueryClient();
    const [mode, setMode] = useState<'upload' | 'existing'>('upload');
    const [file, setFile] = useState<File | null>(null);
    const [index, setIndex] = useState<File | null>(null);
    const [candidateId, setCandidateId] = useState('');
    const directory = useRef(`inputs/ont-raw-signal/${crypto.randomUUID()}`);
    const uploaded = useRef(new Map<File, string>());
    const format = file?.name.split('.').pop()?.toLowerCase();
    const needsIndex = format === 'blow5';

    const candidates = useQuery({ queryKey: ['ont-external-pod5-candidates'], queryFn: fetchOntExternalPod5Candidates, retry: false, enabled: mode === 'existing' });
    const retention = useQuery({ queryKey: ['ont-raw-signal-retention'], queryFn: fetchOntRawSignalRetention, staleTime: 60_000 });
    const saveRetention = useMutation({
        mutationFn: saveOntRawSignalRetention,
        onSuccess: (settings) => queryClient.setQueryData(['ont-raw-signal-retention'], settings),
    });
    const add = useMutation({
        mutationFn: async () => {
            if (mode === 'existing') return registerOntExternalPod5Candidate(candidateId, projectId);
            if (!file || (format !== 'pod5' && format !== 'slow5' && format !== 'blow5')) throw new Error('Choose a POD5, SLOW5 or BLOW5 file.');
            if (needsIndex && !index) throw new Error('Choose the matching .blow5.idx index.');
            const upload = async (item: File, name: string) => {
                let path = uploaded.current.get(item);
                if (!path) {
                    path = (await uploadFile(directory.current, new File([item], name))).data.path;
                    uploaded.current.set(item, path);
                }
                return path;
            };
            const name = `source.${format}`;
            return importOntRawSignalUpload({
                file_path: await upload(file, name), format,
                index_file_path: needsIndex && index ? await upload(index, `${name}.idx`) : null,
                experiment_group: projectId, sample_id: null,
            });
        },
        onSuccess: (registration) => onAdded(registration.run_id),
    });
    const pick = (next: File | null) => { setFile(next); setIndex(null); uploaded.current = new Map(); directory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`; };
    const ready = mode === 'existing' ? Boolean(candidateId) : Boolean(file) && (!needsIndex || Boolean(index));

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-[var(--overlay-scrim)] p-6 pt-24">
            <div role="dialog" aria-modal="true" aria-label="Add raw signal" className="w-full max-w-xl space-y-4 rounded-2xl border border-[var(--border-secondary)] bg-[var(--bg-secondary)] p-5 text-[var(--text-primary)] shadow-2xl">
                <h3 className="text-lg font-semibold">Add raw signal</h3>
                <div role="radiogroup" aria-label="Raw-signal source" className="grid gap-2 sm:grid-cols-2">
                    {([
                        ['upload', 'Upload a file', 'POD5, SLOW5 or BLOW5 with its index'],
                        ['existing', 'Use a POD5 on this computer', 'Registered in place, not moved or changed'],
                    ] as const).map(([value, label, detail]) => (
                        <button key={value} type="button" role="radio" aria-checked={mode === value} onClick={() => setMode(value)}
                            className={`rounded-lg border px-3 py-2 text-left text-sm ${mode === value ? 'border-[var(--accent-primary)] bg-[var(--bg-primary)] font-semibold' : 'border-[var(--border-primary)] text-[var(--text-secondary)]'}`}>
                            <span className="block">{label}</span>
                            <span className="block text-xs font-normal text-[var(--text-secondary)]">{detail}</span>
                        </button>
                    ))}
                </div>
                {mode === 'upload' ? (
                    <fieldset disabled={add.isPending} className="space-y-3">
                        <label className="block text-xs text-[var(--text-secondary)]">Raw-signal file
                            <input aria-label="Raw-signal file" type="file" accept=".pod5,.slow5,.blow5" onChange={(event) => pick(event.target.files?.[0] ?? null)}
                                className="mt-1 block w-full rounded-lg border border-dashed border-[var(--border-secondary)] p-3 text-sm text-[var(--text-primary)] file:mr-3 file:rounded file:border-0 file:bg-[var(--bg-tertiary)] file:px-3 file:py-1.5 file:text-[var(--text-primary)]" />
                        </label>
                        {needsIndex ? (
                            <label className="block text-xs text-[var(--text-secondary)]">Matching BLOW5 index (.blow5.idx)
                                <input aria-label="BLOW5 index" type="file" accept=".idx" onChange={(event) => { setIndex(event.target.files?.[0] ?? null); uploaded.current = new Map(); directory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`; }}
                                    className="mt-1 block w-full rounded-lg border border-dashed border-[var(--border-secondary)] p-3 text-sm text-[var(--text-primary)] file:mr-3 file:rounded file:border-0 file:bg-[var(--bg-tertiary)] file:px-3 file:py-1.5 file:text-[var(--text-primary)]" />
                            </label>
                        ) : null}
                    </fieldset>
                ) : (
                    <div className="space-y-2">
                        {candidates.isError ? <p role="alert" className="text-sm text-[var(--warning)]">POD5 files on this computer could not be listed: {failure(candidates.error, 'unknown error')}</p> : null}
                        <select aria-label="Existing POD5 candidate" value={candidateId} onChange={(event) => setCandidateId(event.target.value)} disabled={candidates.isLoading || add.isPending}
                            className="w-full rounded-lg border border-[var(--border-secondary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50">
                            <option value="">{candidates.isLoading ? 'Looking for POD5 files…' : 'Choose a POD5…'}</option>
                            {(candidates.data?.candidates ?? []).map((candidate) => (
                                <option key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.display_name} · {fileSize(candidate.size_bytes)}</option>
                            ))}
                        </select>
                    </div>
                )}
                <label className="flex items-center gap-2 text-sm" title="Saves about half the storage. The BLOW5 keeps the full signal; basecalling such a file again first restores its POD5 from the BLOW5.">
                    <input type="checkbox" aria-label="Remove POD5 after verified BLOW5" checked={retention.data?.remove_pod5_after_verified_blow5 === true}
                        disabled={!retention.data || saveRetention.isPending} onChange={(event) => saveRetention.mutate(event.target.checked ? 'blow5_only' : 'pod5_and_blow5')} />
                    Remove POD5 after a verified BLOW5 is made
                    <span aria-hidden="true" className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-[var(--text-muted)] text-[10px] text-[var(--text-muted)]">i</span>
                </label>
                {add.isError ? <p role="alert" className="text-sm text-[var(--warning)]">{failure(add.error, 'The raw signal could not be added.')}</p> : null}
                <div className="flex justify-end gap-2">
                    <button type="button" onClick={onClose} className={BUTTON}>Cancel</button>
                    <button type="button" disabled={!ready || add.isPending} onClick={() => add.mutate()} className={PRIMARY}>
                        {add.isPending ? 'Adding…' : mode === 'existing' ? 'Register selected POD5' : 'Import raw signal'}
                    </button>
                </div>
            </div>
        </div>
    );
}
