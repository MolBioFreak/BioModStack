/**
 * Read-only guest viewer for a temporary MolBio share.
 *
 * Served by the public share server only. It reads the frozen bundle with the
 * link key (URL fragment) and passcode (typed by the guest, kept in memory),
 * and can request pure restriction-site computations. There are no write paths.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SequenceViewer } from '../components/MolBioToolkit/SequenceViewer';
import { DEFAULT_VISIBILITY } from '../components/MolBioToolkit/sequenceViewerConstants';
import type { SequenceData, VisibilityState } from '../components/MolBioToolkit/types';
import { guestSequenceData } from './guestSequenceData';
import {
    fetchRestrictionAnalysisBatch,
    fetchRestrictionCatalogBrowse,
    type RestrictionAnalysisBatch,
    type RestrictionCatalogReceipt,
    type RestrictionCatalogSummary,
} from '../lib/restrictionAnalysis';
import {
    GuestShareError,
    fetchGuestShare,
    formatGuestRemaining,
    linkKeyFromHash,
    normalizeGuestPasscode,
    shareTransport,
    type GuestOperation,
    type GuestSequence,
    type GuestShare,
    type ShareCredentials,
    type Transport,
} from './shareGuestApi';

type ViewMode = 'linear' | 'circular' | 'both';

const DEFAULT_ENZYMES = ['EcoRI', 'BamHI', 'HindIII', 'XhoI', 'NotI', 'XbaI', 'NcoI', 'NdeI', 'PstI', 'SalI', 'KpnI', 'SacI'];
const MAX_SELECTED_ENZYMES = 40;


function sequenceName(item: GuestSequence): string {
    return item.record.name || item.sequence_id;
}

function formatDate(value: string | null): string {
    if (!value) return '';
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : value;
}

// ---------------------------------------------------------------------------
// Passcode gate
// ---------------------------------------------------------------------------

function PasscodeGate({ onSubmit, error, busy }: { onSubmit: (passcode: string) => void; error: string | null; busy: boolean }) {
    const [value, setValue] = useState('');
    const normalized = normalizeGuestPasscode(value);
    return (
        <form
            className="mx-auto mt-16 w-full max-w-sm space-y-4 rounded-lg border border-slate-700 bg-slate-800 p-6"
            onSubmit={(event) => {
                event.preventDefault();
                if (normalized) onSubmit(normalized);
            }}
        >
            <h1 className="text-lg font-semibold text-slate-100">Shared MolBio data</h1>
            <p className="text-sm text-slate-400">Enter the passcode you were given with this link.</p>
            <input
                autoFocus
                aria-label="Passcode"
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
                value={value}
                maxLength={12}
                placeholder="XXXX-XXXX"
                onChange={(event) => setValue(event.target.value)}
                className="w-full rounded border border-slate-600 bg-slate-900 px-3 py-2 text-center font-mono text-lg tracking-widest text-slate-100"
            />
            {error && <div role="alert" className="text-sm text-red-300">{error}</div>}
            <button
                type="submit"
                disabled={!normalized || busy}
                className="min-h-11 w-full rounded bg-blue-600 text-sm font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-slate-600"
            >
                {busy ? 'Opening…' : 'Open'}
            </button>
        </form>
    );
}

// ---------------------------------------------------------------------------
// Restriction sites (pure computation on the share server)
// ---------------------------------------------------------------------------

interface CutSiteState {
    catalog: RestrictionCatalogReceipt | null;
    enzymes: RestrictionCatalogSummary[];
    error: string | null;
}

function useGuestCatalog(transport: Transport, enabled: boolean): CutSiteState {
    const [state, setState] = useState<CutSiteState>({ catalog: null, enzymes: [], error: null });
    useEffect(() => {
        if (!enabled || state.catalog) return;
        const controller = new AbortController();
        fetchRestrictionCatalogBrowse({ transport, signal: controller.signal })
            .then(({ catalog, items }) => setState({
                catalog,
                enzymes: items.filter((item) => item.analysis_capability !== 'recognition_only'),
                error: null,
            }))
            .catch((error) => {
                if (!controller.signal.aborted) setState((prev) => ({ ...prev, error: error instanceof Error ? error.message : 'Enzyme catalog unavailable.' }));
            });
        return () => controller.abort();
    }, [enabled, state.catalog, transport]);
    return state;
}

function EnzymePicker({ enzymes, selected, onChange }: {
    enzymes: RestrictionCatalogSummary[];
    selected: string[];
    onChange: (ids: string[]) => void;
}) {
    const [query, setQuery] = useState('');
    const byId = useMemo(() => new Map(enzymes.map((e) => [e.enzyme_id, e])), [enzymes]);
    const matches = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return [];
        return enzymes.filter((e) => e.canonical_name.toLowerCase().includes(q) || e.aliases.some((a) => a.toLowerCase().includes(q))).slice(0, 12);
    }, [enzymes, query]);
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap gap-1">
                {selected.map((id) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => onChange(selected.filter((v) => v !== id))}
                        className="rounded bg-slate-700 px-2 py-0.5 text-xs text-slate-200 hover:bg-red-900"
                        title="Remove"
                    >
                        {byId.get(id)?.canonical_name ?? id} ×
                    </button>
                ))}
                {selected.length === 0 && <span className="text-xs text-slate-500">No enzymes selected.</span>}
            </div>
            <input
                aria-label="Add enzyme"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Add enzyme…"
                className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1 text-sm text-slate-100"
            />
            {matches.length > 0 && (
                <ul className="max-h-40 overflow-y-auto rounded border border-slate-700 text-sm">
                    {matches.map((e) => (
                        <li key={e.enzyme_id}>
                            <button
                                type="button"
                                disabled={selected.includes(e.enzyme_id) || selected.length >= MAX_SELECTED_ENZYMES}
                                onClick={() => { onChange([...selected, e.enzyme_id]); setQuery(''); }}
                                className="flex w-full justify-between px-2 py-1 text-left text-slate-200 hover:bg-slate-700 disabled:opacity-40"
                            >
                                <span>{e.canonical_name}</span>
                                <span className="font-mono text-xs text-slate-400">{e.site_iupac}</span>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Detail panels
// ---------------------------------------------------------------------------

function SequenceDetails({ item, data, operations, onOpenOperation }: {
    item: GuestSequence;
    data: SequenceData | null;
    operations: Map<string, GuestOperation>;
    onOpenOperation: (id: string) => void;
}) {
    const derivedBy = item.derived_by_operation_id ? operations.get(item.derived_by_operation_id) : undefined;
    return (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            <dt className="text-slate-500">Length</dt><dd className="text-slate-200">{(data?.sequence.length ?? item.content_length ?? 0).toLocaleString()} {data?.sequenceType === 'rna' ? 'nt' : 'bp'}</dd>
            <dt className="text-slate-500">Topology</dt><dd className="text-slate-200">{data?.circular ? 'Circular' : 'Linear'}</dd>
            <dt className="text-slate-500">Features</dt><dd className="text-slate-200">{data?.features.length ?? 0}</dd>
            {item.revision_number != null && (<><dt className="text-slate-500">Revision</dt><dd className="text-slate-200">v{item.revision_number}</dd></>)}
            {item.record.organism && (<><dt className="text-slate-500">Organism</dt><dd className="text-slate-200">{item.record.organism}</dd></>)}
            {item.record.accession && (<><dt className="text-slate-500">Accession</dt><dd className="text-slate-200">{item.record.accession}</dd></>)}
            {item.created_at && (<><dt className="text-slate-500">Saved</dt><dd className="text-slate-200">{formatDate(item.created_at)}</dd></>)}
            {derivedBy && (
                <>
                    <dt className="text-slate-500">Made by</dt>
                    <dd><button type="button" onClick={() => onOpenOperation(derivedBy.operation_id)} className="text-cyan-300 hover:underline">{derivedBy.title}</button></dd>
                </>
            )}
            {item.project_tags.length > 0 && (<><dt className="text-slate-500">Tags</dt><dd className="text-slate-200">{item.project_tags.join(', ')}</dd></>)}
            {item.project_notes && (<><dt className="text-slate-500">Notes</dt><dd className="whitespace-pre-wrap text-slate-200">{item.project_notes}</dd></>)}
            {item.record.description && (<><dt className="text-slate-500">Description</dt><dd className="whitespace-pre-wrap text-slate-200">{item.record.description}</dd></>)}
        </dl>
    );
}

function OperationView({ operation, revisions, onOpenSequence }: {
    operation: GuestOperation;
    revisions: Map<string, GuestSequence>;
    onOpenSequence: (revisionId: string) => void;
}) {
    const edge = (title: string, edges: GuestOperation['inputs']) => edges.length > 0 && (
        <div>
            <div className="text-xs text-slate-500">{title}</div>
            <ul className="text-sm">
                {edges.map((e) => {
                    const target = revisions.get(e.revision_id);
                    return (
                        <li key={`${e.revision_id}:${e.position}`}>
                            {target ? (
                                <button type="button" onClick={() => onOpenSequence(e.revision_id)} className="text-cyan-300 hover:underline">
                                    {sequenceName(target)}{target.revision_number != null ? ` v${target.revision_number}` : ''}
                                </button>
                            ) : <span className="text-slate-400">{e.revision_id}</span>}
                            {e.role && <span className="ml-2 text-xs text-slate-500">{e.role}</span>}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
    const params = Object.entries(operation.parameters).filter(([key]) => key !== 'summary');
    return (
        <div className="space-y-3 p-4 text-slate-100">
            <div>
                <h2 className="text-base font-semibold">{operation.title}</h2>
                <div className="text-xs text-slate-400">
                    {operation.operation_kind.replace(/_/gu, ' ')}
                    {operation.status ? ` · ${operation.status}` : ''}
                    {operation.created_at ? ` · ${formatDate(operation.created_at)}` : ''}
                    {operation.role === 'ancestor' ? ' · lineage' : ''}
                </div>
            </div>
            {edge('Inputs', operation.inputs)}
            {edge('Outputs', operation.outputs)}
            {operation.warnings.length > 0 && (
                <div>
                    <div className="text-xs text-slate-500">Warnings</div>
                    <ul className="list-disc pl-5 text-sm text-amber-200">
                        {operation.warnings.map((w, i) => <li key={i}>{typeof w === 'string' ? w : JSON.stringify(w)}</li>)}
                    </ul>
                </div>
            )}
            {params.length > 0 && (
                <details>
                    <summary className="cursor-pointer w-fit text-xs text-slate-400 hover:text-slate-200">Parameters</summary>
                    <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                        {params.map(([key, value]) => (
                            <div key={key} className="contents">
                                <dt className="text-slate-500">{key.replace(/_/gu, ' ')}</dt>
                                <dd className="break-all font-mono text-slate-200">{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
                            </div>
                        ))}
                    </dl>
                </details>
            )}
            {operation.implementation && (
                <div className="text-xs text-slate-500">{operation.implementation}{operation.implementation_version ? ` ${operation.implementation_version}` : ''}</div>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Workspace
// ---------------------------------------------------------------------------

type Selection = { kind: 'sequence'; revisionId: string } | { kind: 'operation'; operationId: string };

function ShareWorkspace({ share, credentials, now }: { share: GuestShare; credentials: ShareCredentials; now: number }) {
    const { bundle, grant } = share;
    const transport = useMemo(() => shareTransport(credentials), [credentials]);
    const revisions = useMemo(() => new Map(bundle.sequences.map((s) => [s.revision_id, s])), [bundle.sequences]);
    const operations = useMemo(() => new Map(bundle.operations.map((o) => [o.operation_id, o])), [bundle.operations]);
    const scopedSequences = bundle.sequences.filter((s) => s.role === 'scoped');
    const ancestorSequences = bundle.sequences.filter((s) => s.role === 'ancestor');
    const scopedOperations = bundle.operations.filter((o) => o.role === 'scoped');
    const ancestorOperations = bundle.operations.filter((o) => o.role === 'ancestor');
    const first: Selection | null = bundle.sequences[0]
        ? { kind: 'sequence', revisionId: (scopedSequences[0] ?? bundle.sequences[0]).revision_id }
        : bundle.operations[0] ? { kind: 'operation', operationId: bundle.operations[0].operation_id } : null;
    const [selection, setSelection] = useState<Selection | null>(first);
    const [listOpen, setListOpen] = useState(false);
    const [viewMode, setViewMode] = useState<ViewMode>('both');
    const [visibility, setVisibility] = useState<VisibilityState>(DEFAULT_VISIBILITY);
    const [selectedEnzymes, setSelectedEnzymes] = useState<string[]>([]);
    const [analysis, setAnalysis] = useState<RestrictionAnalysisBatch | null>(null);
    const [analysisState, setAnalysisState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });

    const current = selection?.kind === 'sequence' ? revisions.get(selection.revisionId) ?? null : null;
    const currentOperation = selection?.kind === 'operation' ? operations.get(selection.operationId) ?? null : null;
    const data = useMemo(() => (current ? guestSequenceData(current) : null), [current]);
    const isDna = data?.sequenceType === 'dna';
    const catalog = useGuestCatalog(transport, visibility.cutsites && isDna);

    // Default enzyme set once the catalog arrives.
    useEffect(() => {
        if (!catalog.catalog || selectedEnzymes.length > 0) return;
        const byName = new Map(catalog.enzymes.map((e) => [e.canonical_name, e.enzyme_id]));
        setSelectedEnzymes(DEFAULT_ENZYMES.flatMap((name) => (byName.has(name) ? [byName.get(name)!] : [])));
    }, [catalog.catalog, catalog.enzymes, selectedEnzymes.length]);

    const requestRef = useRef(0);
    useEffect(() => {
        setAnalysis(null);
        if (!visibility.cutsites || !data || !isDna || !catalog.catalog || selectedEnzymes.length === 0) {
            setAnalysisState({ busy: false, error: null });
            return;
        }
        const token = ++requestRef.current;
        const controller = new AbortController();
        setAnalysisState({ busy: true, error: null });
        fetchRestrictionAnalysisBatch({
            source: { kind: 'inline_dna', name: (data.name || 'sequence').slice(0, 128), dna: data.sequence, topology: data.circular ? 'circular' : 'linear' },
            catalog: catalog.catalog,
            records: selectedEnzymes.map((enzyme_id) => ({ enzyme_id })),
            enzymeIds: selectedEnzymes,
            transport,
            signal: controller.signal,
        })
            .then((batch) => {
                if (token === requestRef.current) {
                    setAnalysis(batch);
                    setAnalysisState({ busy: false, error: null });
                }
            })
            .catch((error) => {
                if (!controller.signal.aborted && token === requestRef.current) {
                    setAnalysisState({ busy: false, error: error instanceof Error ? error.message : 'Cut-site analysis failed.' });
                }
            });
        return () => controller.abort();
    }, [catalog.catalog, data, isDna, selectedEnzymes, transport, visibility.cutsites]);

    const remaining = formatGuestRemaining(grant.expires_at, now);
    const pick = (next: Selection) => { setSelection(next); setListOpen(false); };

    const listSection = (title: string, children: React.ReactNode, count: number) => count > 0 && (
        <section className="space-y-1">
            <h3 className="px-3 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
            <ul>{children}</ul>
        </section>
    );
    const sequenceItem = (s: GuestSequence) => {
        const active = selection?.kind === 'sequence' && selection.revisionId === s.revision_id;
        return (
            <li key={s.revision_id}>
                <button
                    type="button"
                    onClick={() => pick({ kind: 'sequence', revisionId: s.revision_id })}
                    className={`block w-full px-3 py-2 text-left text-sm ${active ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
                >
                    <div className="truncate">{sequenceName(s)}{s.revision_number != null && s.role === 'ancestor' ? ` v${s.revision_number}` : ''}</div>
                    <div className="text-xs text-slate-500">
                        {(s.record.length ?? s.content_length ?? 0).toLocaleString()} {s.record.sequence_type === 'rna' ? 'nt' : 'bp'}
                        {s.record.is_circular ? ' · circular' : ''}
                    </div>
                </button>
            </li>
        );
    };
    const operationItem = (o: GuestOperation) => {
        const active = selection?.kind === 'operation' && selection.operationId === o.operation_id;
        return (
            <li key={o.operation_id}>
                <button
                    type="button"
                    onClick={() => pick({ kind: 'operation', operationId: o.operation_id })}
                    className={`block w-full px-3 py-2 text-left text-sm ${active ? 'bg-slate-700 text-white' : 'text-slate-300 hover:bg-slate-800'}`}
                >
                    <div className="truncate">{o.title}</div>
                    <div className="text-xs text-slate-500">{o.operation_kind.replace(/_/gu, ' ')}</div>
                </button>
            </li>
        );
    };

    const itemList = (
        <nav aria-label="Shared items" className="pb-4">
            {listSection('Sequences', scopedSequences.map(sequenceItem), scopedSequences.length)}
            {listSection('Experiments', scopedOperations.map(operationItem), scopedOperations.length)}
            {(ancestorSequences.length + ancestorOperations.length) > 0 && (
                <details className="mt-2">
                    <summary className="cursor-pointer px-3 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-300">
                        Lineage ({ancestorSequences.length + ancestorOperations.length})
                    </summary>
                    {listSection('Earlier sequences', ancestorSequences.map(sequenceItem), ancestorSequences.length)}
                    {listSection('Earlier experiments', ancestorOperations.map(operationItem), ancestorOperations.length)}
                </details>
            )}
        </nav>
    );

    return (
        <div className="flex h-dvh flex-col bg-slate-900 text-slate-100" data-share-guest-workspace="true">
            <header className="flex flex-wrap items-center gap-2 border-b border-slate-700 bg-slate-950 px-3 py-2">
                <button
                    type="button"
                    onClick={() => setListOpen((v) => !v)}
                    className="min-h-10 rounded border border-slate-600 px-3 text-sm md:hidden"
                    aria-expanded={listOpen}
                >
                    Items
                </button>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{grant.label || bundle.title}</div>
                    <div className="text-xs text-slate-400">Read-only share{remaining ? ` · ${remaining}` : ''}</div>
                </div>
            </header>
            <div className="relative flex min-h-0 flex-1">
                <aside className={`${listOpen ? 'absolute inset-y-0 left-0 z-20 w-72 shadow-xl' : 'hidden'} overflow-y-auto border-r border-slate-700 bg-slate-950 md:static md:block md:w-64`}>
                    {itemList}
                </aside>
                <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
                    {currentOperation && (
                        <div className="overflow-y-auto">
                            <OperationView
                                operation={currentOperation}
                                revisions={revisions}
                                onOpenSequence={(revisionId) => pick({ kind: 'sequence', revisionId })}
                            />
                        </div>
                    )}
                    {current && !data && (
                        <div className="p-4 text-sm text-slate-400">This item has no viewable nucleotide sequence.</div>
                    )}
                    {current && data && (
                        <>
                            <div className="flex flex-wrap items-center gap-2 border-b border-slate-700 bg-slate-800 px-3 py-2">
                                <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{data.name}</h2>
                                <div className="flex overflow-hidden rounded border border-slate-600 text-xs">
                                    {(['linear', 'both', 'circular'] as ViewMode[]).map((mode) => (
                                        <button
                                            key={mode}
                                            type="button"
                                            aria-pressed={viewMode === mode}
                                            onClick={() => setViewMode(mode)}
                                            className={`px-2 py-1 capitalize ${viewMode === mode ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
                                        >
                                            {mode}
                                        </button>
                                    ))}
                                </div>
                                {(['features', 'primers', 'cutsites'] as const).map((key) => (
                                    <label key={key} className={`flex items-center gap-1 text-xs ${key === 'cutsites' && !isDna ? 'opacity-40' : 'text-slate-300'}`}>
                                        <input
                                            type="checkbox"
                                            disabled={key === 'cutsites' && !isDna}
                                            checked={visibility[key]}
                                            onChange={() => setVisibility((v) => ({ ...v, [key]: !v[key] }))}
                                        />
                                        {key === 'cutsites' ? 'Cut sites' : key[0].toUpperCase() + key.slice(1)}
                                    </label>
                                ))}
                            </div>
                            <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
                                <div className="min-h-[320px] min-w-0 flex-1 overflow-hidden">
                                    <SequenceViewer
                                        key={current.revision_id}
                                        sequenceData={data}
                                        visibility={visibility}
                                        selectedEnzymes={visibility.cutsites ? selectedEnzymes : []}
                                        restrictionAnalysis={visibility.cutsites ? analysis : null}
                                        viewMode={viewMode}
                                        className="h-full"
                                    />
                                </div>
                                <div className="max-h-[40dvh] space-y-4 overflow-y-auto border-t border-slate-700 bg-slate-900 p-3 lg:max-h-none lg:w-72 lg:border-l lg:border-t-0">
                                    <SequenceDetails
                                        item={current}
                                        data={data}
                                        operations={operations}
                                        onOpenOperation={(operationId) => pick({ kind: 'operation', operationId })}
                                    />
                                    {visibility.cutsites && isDna && (
                                        <div className="space-y-2 border-t border-slate-700 pt-3">
                                            <div className="text-xs font-semibold text-slate-400">Enzymes</div>
                                            {catalog.error && <div className="text-xs text-red-300">{catalog.error}</div>}
                                            {!catalog.catalog && !catalog.error && <div className="text-xs text-slate-500">Loading enzyme catalog…</div>}
                                            {catalog.catalog && (
                                                <EnzymePicker enzymes={catalog.enzymes} selected={selectedEnzymes} onChange={setSelectedEnzymes} />
                                            )}
                                            {analysisState.busy && <div className="text-xs text-slate-500">Finding cut sites…</div>}
                                            {analysisState.error && <div role="alert" className="text-xs text-red-300">{analysisState.error}</div>}
                                            {analysis && (
                                                <ul className="space-y-0.5 text-xs">
                                                    {analysis.analysis.enzyme_summaries
                                                        .slice()
                                                        .sort((a, b) => a.canonical_name.localeCompare(b.canonical_name))
                                                        .map((s) => (
                                                            <li key={s.enzyme_id} className="flex justify-between gap-2 text-slate-300">
                                                                <span>{s.canonical_name}</span>
                                                                <span className="text-slate-500">
                                                                    {s.recognition_site_count_definite} site{s.recognition_site_count_definite === 1 ? '' : 's'}
                                                                </span>
                                                            </li>
                                                        ))}
                                                </ul>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </>
                    )}
                    {!selection && <div className="p-4 text-sm text-slate-400">This share is empty.</div>}
                </main>
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------------

export function ShareGuestApp({ hash = typeof window !== 'undefined' ? window.location.hash : '', transport }: { hash?: string; transport?: Transport }) {
    const key = useMemo(() => linkKeyFromHash(hash), [hash]);
    const [credentials, setCredentials] = useState<ShareCredentials | null>(null);
    const [share, setShare] = useState<GuestShare | null>(null);
    const [error, setError] = useState<GuestShareError | null>(null);
    const [busy, setBusy] = useState(false);
    const [now, setNow] = useState(() => Date.now());

    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), 30000);
        return () => window.clearInterval(timer);
    }, []);

    const open = useCallback(async (passcode: string) => {
        if (!key) return;
        const next = { key, passcode };
        setBusy(true);
        setError(null);
        try {
            const loaded = await fetchGuestShare(next, transport);
            setCredentials(next);
            setShare(loaded);
        } catch (err) {
            setError(err instanceof GuestShareError ? err : new GuestShareError('unavailable', 'The share is unavailable right now.'));
        } finally {
            setBusy(false);
        }
    }, [key, transport]);

    const expired = share?.grant.expires_at ? Date.parse(share.grant.expires_at) <= now : false;

    if (!key) {
        return <Message title="Link incomplete" body="This share link is missing its key. Open the full link you were sent." />;
    }
    if (expired) {
        return <Message title="Share ended" body="This share has expired. Ask the sender for a new link." />;
    }
    if (share && credentials) {
        return <ShareWorkspace share={share} credentials={credentials} now={now} />;
    }
    if (error && (error.kind === 'ended' || error.kind === 'invalid_link' || error.kind === 'blocked' || error.kind === 'locked')) {
        const title = { ended: 'Share ended', invalid_link: 'Link not valid', blocked: 'Access blocked', locked: 'Share locked' }[error.kind];
        return <Message title={title} body={error.message} />;
    }
    return (
        <div className="min-h-dvh bg-slate-900 px-4">
            <PasscodeGate onSubmit={open} error={error?.message ?? null} busy={busy} />
        </div>
    );
}

function Message({ title, body }: { title: string; body: string }) {
    return (
        <div className="min-h-dvh bg-slate-900 px-4">
            <div className="mx-auto mt-16 max-w-sm space-y-2 rounded-lg border border-slate-700 bg-slate-800 p-6 text-slate-100">
                <h1 className="text-lg font-semibold">{title}</h1>
                <p className="text-sm text-slate-400">{body}</p>
            </div>
        </div>
    );
}
