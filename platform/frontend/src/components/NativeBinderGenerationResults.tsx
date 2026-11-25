import { resolveApiResourceUrl } from '../runtime/tailnetEnvironment';
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { materializeExactStructure, type StructureSourceSelection } from '../lib/api';
import { BinderPredictionEvidence } from './BinderPredictionEvidence';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { metricKeys, numericMetricKeys, formatMetric, summarizeMetric, type CohortRow } from '../lib/cohortAnalytics';
import { fetchNativeBinderGeneration, nativeCandidateRoute, nativeDocumentLabel, nativeGenerationMetrics, nativeGenerationMetricDetail, nativeGenerationMetricUnit, nativeGenerationExport, type NativeGenerationPage, type NativeGenerationRecord, type NativeGenerationDocument } from '../lib/nativeBinderResults';

import { readBinderSelection, writeBinderSelection, readBinderNativeSources, writeBinderNativeSources, type NativeCandidateSource } from '../lib/binderContinuation';
import { BinderCandidateSets } from './BinderCandidateSets';
import { candidateKey, candidateIdentity, uniqueCandidates, readRoundStorage, writeRoundStorage, type CandidateRef } from '../lib/binderSelectedRound';
import { continuationHref, continuationLabel, rfd4Destinations } from '../lib/deNovoContinuation';
import { DeNovoRepeatSettings } from './DeNovoRepeatSettings';
import { NativeGeneratedSequences } from './NativeGeneratedSequences';
import { Rfd4RetainedStateContinuation } from './Rfd4RetainedStateContinuation';
import { buildResultsViewerMolecularDynamicsRoute } from './gen2StartingStructureState';
import type { CohortInitialMetrics } from './CohortAnalytics';
import { CohortMetricPicker } from './CohortMetricPicker';
import { describeCohortMetric } from '../lib/cohortMetricPresentation';

// Load useful visualization and operation owners only when their view is demanded.
const CohortAnalytics = lazy(() => import('./CohortAnalytics').then(module => ({ default: module.CohortAnalytics })));
const StructureWorkbench = lazy(() => import('../structureViewer/StructureWorkbench').then(module => ({ default: module.StructureWorkbench })));

function NativeDocumentWorkbench({ jobId, document, collapsed, height = 520 }: { jobId: string; document: NativeGenerationDocument; collapsed: boolean; height?: number }) {
    const encoded = /\.(?:cif|mmcif|bcif)(?:\.(?:gz|zst))?(?:$|\?)/i.test(document.logical_path ?? document.download_url ?? '')
        && /(?:\.(?:gz|zst)|\.bcif)(?:$|\?)/i.test(document.logical_path ?? document.download_url ?? '');
    const acquired = useQuery({ queryKey: ['native-document-view', jobId, document.artifact_id, document.target_state, document.model, document.sha256],
        enabled: encoded, retry: false, staleTime: Infinity,
        queryFn: () => materializeExactStructure({ job_id: jobId, document: { artifact_id: document.artifact_id, target_state: document.target_state ?? undefined },
            ...(document.model != null ? { model_number: document.model } : {}), expected_sha256: document.sha256, output_format: 'native' }) });
    if (encoded && !acquired.data) return <p role="status">{acquired.isError ? 'Exact native document could not be decoded. Original download remains available.' : 'Decoding exact native document…'}</p>;
    const decoded = acquired.data;
    const path = document.model != null ? decoded?.path : decoded?.decoded_path ?? decoded?.path;
    const url = encoded && path ? `/api/files/download/${path.split('/').map(encodeURIComponent).join('/')}` : document.download_url;
    return <StructureWorkbench mode="standard" structureUrl={url}
        format={encoded || /\.(cif|mmcif)(?:$|\?)/i.test(document.logical_path ?? document.download_url ?? '') ? 'cif' : 'pdb'}
        structureDocumentId={document.artifact_id} structureContentSha256={encoded ? document.model != null ? decoded?.sha256 : decoded?.decoded_sha256 ?? decoded?.sha256 : document.sha256}
        alphafoldView={false} height={height} showSequenceTrack showMeasurements showM6Workbench hideControls={false} workbenchCollapsed={collapsed} />;
}

export interface NativeWorkbenchAdapter {
    key: string; title: string; recordLabel?: string;
    fetchPage: (offset: number, signal?: AbortSignal) => Promise<NativeGenerationPage>;
    label: (row: NativeGenerationRecord) => string;
    preferredColumns?: string[];
    metricLabel?: (key: string) => string | undefined;
    preferredDocument?: (row: NativeGenerationRecord) => NativeGenerationDocument | undefined;
    initialMetrics?: (numericKeys: string[]) => CohortInitialMetrics;
    compact?: boolean;
    inspect?: (row: NativeGenerationRecord) => ReactNode;
    related?: (row: NativeGenerationRecord) => ReactNode;
    summary?: (rows: NativeGenerationRecord[]) => ReactNode;
}
export interface Props {
    adapter?: NativeWorkbenchAdapter;
    jobId: string; status?: string; launchContextId?: string | null;
    selectedDesignId?: string; selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
    selectedRecordIds?: string[];
    onSelectedRecordIdsChange?: (ids: string[]) => void;
    selectedNativeSources?: NativeCandidateSource[];
    onSelectedNativeSourcesChange?: (sources: NativeCandidateSource[]) => void;
    onInspectDocument?: (row: NativeGenerationRecord, document?: NativeGenerationDocument) => void;
    /** S04 adoption callback: opens a derivative; publication bytes remain immutable. */
    onEditExactDocument?: (source: StructureSourceSelection, context: { candidateRef: CandidateRef; returnTo: string }) => void;
    artifactId?: string | null; targetState?: string | null;
}
type View = 'candidates' | 'dashboard' | 'analytics' | 'table' | 'structure';
type Filter = { key: string; kind: 'range' | 'missing' | 'null' | 'nonNumeric'; min: string; max: string };
const control = 'rounded-lg border-0 bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50';
const panel = 'min-w-0 rounded-xl border border-[var(--border-color)] bg-[var(--bg-secondary)]';
const values = nativeGenerationMetrics;
const rowKey = (row: NativeGenerationRecord, index: number) => row.candidate_key ?? row.design_id ?? `record:${index}`;
const dataUrl = (value: unknown) => `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(value, null, 2))}`;
const nativeText = (value: unknown) => value === undefined ? 'Not reported' : value === null ? 'Explicit null' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
// Protect spreadsheet text cells without changing native JSON or numeric values.
const csvCell = (value: unknown) => {
    const text = nativeText(value);
    return `"${(typeof value === 'string' && /^[=+\-@\t\r]/.test(text) ? `'${text}` : text).replaceAll('"', '""')}"`;
};
function OnDemand({ title, children }: { title: string; children: ReactNode }) {
    const [open, setOpen] = useState(false);
    return <details className={`${panel} p-3`} onToggle={event => setOpen(event.currentTarget.open)}><summary className="cursor-pointer text-sm font-medium">{title}</summary>{open && <div className="max-h-96 overflow-auto py-2">{children}</div>}</details>;
}
function Download({ name, content, mime, children }: { name: string; content: string; mime: string; children: ReactNode }) {
    const [url, setUrl] = useState('');
    useEffect(() => {
        const next = URL.createObjectURL(new Blob([content], { type: mime }));
        setUrl(next);
        return () => URL.revokeObjectURL?.(next);
    }, [content, mime]);
    return <a className={`${control} inline-block hover:border-[var(--text-secondary)]`} href={url || undefined} download={name}>{children}</a>;
}

/** Native publication remains the authority. Views never modify the records or eligibility. */
export function NativeBinderGenerationResults(props: Props) {
    return <NativeGenerationWorkbench key={`${props.jobId}:${props.adapter?.key ?? "native"}`} {...props} />;
}
/** Reward and timing CSV rows are measurements of the run, not generated candidates. */
function observationLabel(item: NativeGenerationRecord): string {
    const file = String(item.artifact ?? '').split('/').pop() || 'Observation';
    return typeof item.row === 'number' ? `${file} · row ${item.row + 1}` : file;
}
function NativeGenerationWorkbench({ adapter, jobId, status, launchContextId, selectedDesignId, selectedDesignIds = [], onSelectedDesignIdsChange, selectedRecordIds, onSelectedRecordIdsChange, selectedNativeSources, onSelectedNativeSourcesChange, onInspectDocument, onEditExactDocument, artifactId, targetState }: Props) {
    const [view, setView] = useState<View>(() => selectedDesignId || artifactId || new URLSearchParams(window.location.search).has('native_candidate') ? 'structure' : 'candidates');
    const [structureOpened, setStructureOpened] = useState(view === 'structure' || view === 'candidates');
    const [analyticsOpened, setAnalyticsOpened] = useState(view === 'dashboard' || view === 'analytics');
    useEffect(() => {
        if (view === 'structure' || view === 'candidates') setStructureOpened(true);
        if (view === 'dashboard' || view === 'analytics') setAnalyticsOpened(true);
    }, [view]);
    const [review] = useState(() => readRoundStorage<{ search?: string; population?: string; filters?: Filter[]; page?: number }>(jobId, `review:${adapter?.key ?? 'native'}`, {}));
    const [search, setSearch] = useState(review.search ?? '');
    const [population, setPopulation] = useState(review.population ?? 'all');
    const [rankScope, setRankScope] = useState<'matching' | 'selected'>('matching');
    const [filters, setFilters] = useState<Filter[]>(review.filters ?? []);
    const [selectedOnly, setSelectedOnly] = useState(false);
    const [sort, setSort] = useState({ key: '', descending: false });
    const [pageIndex, setPageIndex] = useState(review.page ?? 0);
    useEffect(() => { writeRoundStorage(jobId, `review:${adapter?.key ?? 'native'}`, { search, population, filters, page: pageIndex }); }, [jobId, adapter?.key, search, population, filters, pageIndex]);
    const [pageSize, setPageSize] = useState(25);
    const [columns, setColumns] = useState<string[] | null>(null);
    const [columnSearch, setColumnSearch] = useState('');
    const [exportScope, setExportScope] = useState<'filtered' | 'selected' | 'all'>('filtered');
    const [local, setLocal] = useState<{ scope: string; key: string; document?: NativeGenerationDocument } | undefined>(() => {
        const saved = readRoundStorage<{ scope: string; key: string; document?: NativeGenerationDocument } | undefined>(jobId, `inspection:${adapter?.key ?? 'native'}`, undefined);
        const requested = new URLSearchParams(window.location.search).get('native_candidate');
        if (artifactId != null || targetState != null) return undefined; // An exact return route supersedes saved inspection.
        return requested && saved?.key !== requested ? undefined : saved;
    });
    useEffect(() => { if (local) writeRoundStorage(jobId, `inspection:${adapter?.key ?? 'native'}`, local); }, [local, jobId, adapter?.key]);
    const [viewerToolsOpen, setViewerToolsOpen] = useState(false);
    const inspector = useRef<HTMLDivElement>(null);
    const scope = JSON.stringify([selectedDesignId, artifactId, targetState]);
    const query = useInfiniteQuery({
        queryKey: ['native-binder-generation', jobId, 'cohort', adapter?.key ?? 'native'], initialPageParam: 0,
        queryFn: ({ pageParam, signal }) => adapter ? adapter.fetchPage(pageParam, signal) : fetchNativeBinderGeneration(jobId, pageParam, 1000, signal),
        getNextPageParam: page => page.records.length && page.offset + page.records.length < page.total ? page.offset + page.records.length : undefined,
        retry: false, refetchOnWindowFocus: false,
        refetchInterval: status === 'queued' || status === 'running' ? 5000 : false,
    });
    // Fill the cohort through the existing paginated reader. Failed later pages leave earlier data usable.
    useEffect(() => {
        if (query.hasNextPage && !query.isFetching && !query.isFetchNextPageError) void query.fetchNextPage();
    }, [query.hasNextPage, query.isFetching, query.isFetchNextPageError, query.fetchNextPage, query.data?.pages.length]);
    const page = query.data?.pages[0];
    const rfd4 = page?.model_id === 'rfd4_proteina';
    const complexa = page?.model_id === 'complexa';
    const rows = useMemo(() => query.data?.pages.flatMap(part => part.records) ?? [], [query.data]);
    useEffect(() => {
        const inventory = readRoundStorage<Record<string, NativeCandidateSource>>(jobId, 'native-documents', {});
        for (const item of rows) {
            const ref = item.candidate_ref;
            if (ref?.kind !== 'native_document') continue;
            const document = item.structures?.find(doc => doc.artifact_id === ref.artifact_id && (ref.target_state == null || doc.target_state === ref.target_state));
            if (document) inventory[candidateKey(ref)] = { job_id: ref.job_id, artifact_id: ref.artifact_id, document: { ...document, ...(ref.model != null ? { model: ref.model } : {}) } };
        }
        writeRoundStorage(jobId, 'native-documents', inventory);
    }, [jobId, rows]);
    const cohort = useMemo<CohortRow[]>(() => rows.map((item, i) => ({ id: rowKey(item, i), label: adapter?.label(item) ?? (rfd4 ? String(item.label ?? (item.record_kind === 'observation' ? item.event ?? 'Observation' : `Candidate ${i + 1}`)) : complexa && item.record_kind === 'observation' ? observationLabel(item) : `Candidate ${i + 1}`), values: values(item) })), [rows, adapter, rfd4]);
    const keys = useMemo(() => metricKeys(cohort), [cohort]);
    const numericKeys = useMemo(() => numericMetricKeys(cohort), [cohort]);
    const label = (key: string) => { const nativeLabel = adapter?.metricLabel?.(key); if (nativeLabel) return nativeLabel; const unit = rows.map(item => nativeGenerationMetricUnit(item, key)).find(Boolean); return `${describeCohortMetric(key, keys).label}${unit ? ` (${unit})` : ''}`; };
    const preferred = (adapter?.preferredColumns ?? ['seq_length', 'dsasa', 'radius_of_gyration', 'num_ca_ca_clashes']).filter(key => keys.includes(key));
    const selectionScope = `${jobId}:records:${adapter?.key ?? 'native'}`;
    const [nativeSelected, setNativeSelected] = useState(() => readBinderSelection(selectionScope));
    const [savedSources, setSavedSources] = useState(() => readBinderNativeSources(jobId));
    const [savedRefs, setSavedRefs] = useState<CandidateRef[]>(() => readRoundStorage(jobId, 'selection', []));
    useEffect(() => {
        const sync = (event: Event) => {
            const detail = (event as CustomEvent).detail;
            if (detail.jobId !== jobId) return;
            setSavedRefs(detail.refs);
            const inventory = readRoundStorage<Record<string, NativeCandidateSource>>(jobId, 'native-documents', {});
            const sourceKey = (source: NativeCandidateSource) => candidateKey({
                kind: 'native_document', job_id: source.job_id, artifact_id: source.artifact_id,
                ...(source.document?.target_state != null ? { target_state: source.document.target_state } : {}),
                ...(source.document?.model != null ? { model: source.document.model } : {}),
            });
            // Non-replacement round events update only the round-owned refs.
            // Keep independently selected source documents; remove a mirrored
            // ref only when it disappears from the round's previous selection.
            const mirrored = new Set(savedRefs.filter(ref => ref.kind === 'native_document').map(candidateKey));
            const independent = detail.replace ? [] : readBinderNativeSources(jobId).filter(source => !mirrored.has(sourceKey(source)));
            const incoming = (detail.refs as CandidateRef[]).flatMap(ref => {
                if (ref.kind !== 'native_document') return [];
                return [inventory[candidateKey(ref)] ?? { job_id: ref.job_id, artifact_id: ref.artifact_id,
                    document: { artifact_id: ref.artifact_id, target_state: ref.target_state, model: ref.model } }];
            });
            const sources = [...new Map([...independent, ...incoming].map(source => [sourceKey(source), source])).values()];
            setSavedSources(sources); writeBinderNativeSources(jobId, sources); onSelectedNativeSourcesChange?.(sources);
            if (detail.replace) { setNativeSelected([]); writeBinderSelection(selectionScope, []); }
        };
        window.addEventListener('bms:binder-round-selection', sync);
        return () => window.removeEventListener('bms:binder-round-selection', sync);
    }, [jobId, selectionScope, onSelectedNativeSourcesChange, savedRefs]);
    const nativeSources = selectedNativeSources ?? savedSources;
    const changeSources = (sources: NativeCandidateSource[]) => {
        const unique = [...new Map(sources.map(source => [JSON.stringify([source.job_id, source.artifact_id, source.document?.target_state, source.document?.model]), source])).values()];
        setSavedSources(unique); writeBinderNativeSources(jobId, unique); onSelectedNativeSourcesChange?.(unique);
    };
    const selectionId = (item: NativeGenerationRecord) => rowKey(item, rows.indexOf(item));
    const matchesRef = (item: NativeGenerationRecord, ref: CandidateRef) => {
        // An explicit producer identity must not fall through to a same-named
        // document in another population, state, or descendant job.
        if (item.candidate_ref) return candidateKey(item.candidate_ref) === candidateKey(ref);
        if (ref.job_id !== jobId) return false;
        if (ref.kind === 'design') return ref.design_id === item.design_id;
        return ref.kind === 'native_document' && !!item.structures?.some(doc => doc.artifact_id === ref.artifact_id
            && (ref.target_state == null || doc.target_state === ref.target_state)
            && (ref.model == null || doc.model === ref.model));
    };
    const matchesSource = (item: NativeGenerationRecord, source: NativeCandidateSource) => matchesRef(item, {
        kind: 'native_document', job_id: source.job_id, artifact_id: source.artifact_id,
        ...(source.document?.target_state != null ? { target_state: source.document.target_state } : {}),
        ...(source.document?.model != null ? { model: source.document.model } : {}),
    });
    const selection = Array.from(new Set([...(selectedRecordIds ?? nativeSelected).filter(id => !rows.some(item => selectionId(item) === id && item.candidate_ref)), ...rows.filter(item => (item.design_id && selectedDesignIds.includes(item.design_id)) || savedRefs.some(ref => matchesRef(item, ref)) || nativeSources.some(source => matchesSource(item, source))).map(selectionId)]));
    const tableKeys = columns ?? (preferred.length || adapter?.preferredColumns !== undefined ? preferred : keys.slice(0, 6));
    const byId = useMemo(() => new Map(cohort.map((entry, index) => [entry.id, rows[index]])), [cohort, rows]);
    const selectedSet = useMemo(() => new Set(selection), [selection]);
    const selectedCohortIds = useMemo(() => cohort.filter(entry => { const item = byId.get(entry.id); const id = item && selectionId(item); return id && selectedSet.has(id); }).map(entry => entry.id), [cohort, byId, selectedSet]);
    const matched = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const result = cohort.filter(entry => {
            const source = byId.get(entry.id)!;
            const outcome = String(entry.values.outcome ?? source.outcome ?? '').toLowerCase();
            if (population.startsWith('native:') && source.population !== population.slice(7)) return false;
            if (population === 'passed' && !['pass', 'passed', 'accepted'].includes(outcome)) return false;
            if (population === 'rejected' && !['reject', 'rejected', 'failed', 'filtered'].includes(outcome)) return false;
            if (population === 'ranked' && entry.values.rank == null && source.rank == null) return false;
            if (selectedOnly && (!selectionId(source) || !selectedSet.has(selectionId(source)!))) return false;
            if (needle && ![entry.id, entry.label, source.candidate_id, source.candidate_ref, source.design_id, source.native_input_id, source.source_row_index, ...Object.values(entry.values)].some(value => nativeText(value).toLowerCase().includes(needle))) return false;
            return filters.every(filter => {
                if (!filter.key) return true;
                const value = entry.values[filter.key];
                if (filter.kind === 'missing') return value === undefined;
                if (filter.kind === 'null') return value === null;
                if (filter.kind === 'nonNumeric') return value != null && !finite(value);
                if (!filter.min.trim() && !filter.max.trim()) return true;
                if (!finite(value)) return false;
                return (!filter.min.trim() || value >= Number(filter.min)) && (!filter.max.trim() || value <= Number(filter.max));
            });
        });
        if (sort.key) result.sort((a, b) => {
            const av = sort.key === '$candidate' ? a.label : a.values[sort.key];
            const bv = sort.key === '$candidate' ? b.label : b.values[sort.key];
            const aMissing = av == null || (typeof av === 'number' && !Number.isFinite(av));
            const bMissing = bv == null || (typeof bv === 'number' && !Number.isFinite(bv));
            if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
            const order = finite(av) && finite(bv) ? av - bv : nativeText(av).localeCompare(nativeText(bv), undefined, { numeric: true });
            return sort.descending ? -order : order;
        });
        return result;
    }, [cohort, byId, search, selectedOnly, selectedSet, filters, sort, population]);
    const displayedPage = Math.min(pageIndex, Math.max(0, Math.ceil(matched.length / pageSize) - 1));
    const navigationRows = matched.map(entry => byId.get(entry.id)!);
    const pageRows = matched.slice(displayedPage * pageSize, (displayedPage + 1) * pageSize);
    const returnCandidate = new URLSearchParams(window.location.search).get('native_candidate');
    const localSelection = local && (returnCandidate == null || local.key === returnCandidate)
        && (local.scope === scope || (!selectedDesignId && artifactId == null && targetState == null)) ? local : undefined;
    const activeId = localSelection?.key ?? (selectedDesignId ? cohort.find(entry => byId.get(entry.id)?.design_id === selectedDesignId)?.id : returnCandidate ?? (artifactId ? cohort.find(entry => byId.get(entry.id)?.structures?.some(doc => doc.artifact_id === artifactId))?.id : cohort[0]?.id));
    const row = activeId ? byId.get(activeId) : undefined;
    const focusedInspection = useRef(false);
    useEffect(() => {
        if (view !== 'structure' || !row || focusedInspection.current) return;
        const frame = requestAnimationFrame(() => { inspector.current?.scrollIntoView?.({ block: 'start' }); focusedInspection.current = true; });
        return () => cancelAnimationFrame(frame);
    }, [view, row]);
    const documents = row?.structures ?? [];
    const explicit = localSelection?.document != null || (!localSelection && (artifactId != null || targetState != null));
    const requestedArtifact = localSelection?.document?.artifact_id ?? artifactId;
    const requestedState = localSelection?.document ? localSelection.document.target_state : targetState;
    const nativeRef = row?.candidate_ref;
    const candidateDocument = nativeRef?.kind === 'design' ? nativeRef.document : nativeRef?.kind === 'native_document' ? nativeRef : undefined;
    const searchParams = new URLSearchParams(window.location.search);
    const routeModel = searchParams.get('model_number');
    const matchesCandidate = (doc: NativeGenerationDocument) => candidateDocument?.artifact_id === doc.artifact_id
        && (candidateDocument.target_state ?? null) === (doc.target_state ?? null);
    const matches = documents.filter(doc => (requestedArtifact == null || doc.artifact_id === requestedArtifact)
        && (localSelection?.document ? (doc.target_state ?? null) === (requestedState ?? null) : requestedState == null || doc.target_state === requestedState));
    const requestedMember = localSelection?.document?.model ?? (routeModel ? Number(routeModel) : undefined);
    const member = requestedMember ?? candidateDocument?.model;
    const document = explicit
        ? localSelection?.document ? matches.find(doc => doc.model === localSelection.document?.model)
            : matches.find(doc => member != null && doc.model === member && (requestedMember != null || matchesCandidate(doc)))
                ?? (matches.length === 1 ? matches[0] : matches.find(doc => doc.model == null))
        : documents.find(doc => matchesCandidate(doc) && (doc.model == null || doc.model === candidateDocument?.model))
            ?? documents.find(doc => doc.primary) ?? (row ? adapter?.preferredDocument?.(row) : undefined) ?? (row?.design_id || documents.length === 1 ? documents[0] : undefined);
    const sourceModel = document?.model ?? requestedMember ?? (document && matchesCandidate(document) ? candidateDocument?.model : undefined);
    const continuationSource = document ? { job_id: jobId, expected_sha256: document.sha256,
        ...(row?.design_id ? { design_id: row.design_id } : {}),
        document: { artifact_id: document.artifact_id, ...(document.target_state != null ? { target_state: document.target_state } : {}) },
        ...(sourceModel != null ? { model_number: sourceModel } : {}),
    } : undefined;
    const inspect = (item: NativeGenerationRecord, doc?: NativeGenerationDocument) => {
        const exact = doc ?? item.structures?.find(value => value.primary) ?? adapter?.preferredDocument?.(item) ?? (item.structures?.length === 1 ? item.structures[0] : undefined);
        setLocal({ scope, key: rowKey(item, rows.indexOf(item)), document: exact });
        const url = new URL(window.location.href);
        url.searchParams.set('native_candidate', rowKey(item, rows.indexOf(item)));
        if (adapter) url.searchParams.set('native_scope', adapter.key);
        if (item.design_id) url.searchParams.set('design_id', item.design_id); else url.searchParams.delete('design_id');
        if (exact) url.searchParams.set('artifact_id', exact.artifact_id); else url.searchParams.delete('artifact_id');
        if (exact?.target_state != null) url.searchParams.set('target_state', exact.target_state); else url.searchParams.delete('target_state');
        if (exact?.model != null) url.searchParams.set('model_number', String(exact.model)); else url.searchParams.delete('model_number');
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
        onInspectDocument?.(item, exact);
        if (view !== 'candidates') setView('structure');
    };
    const changeSelection = (ids: string[], checked: boolean) => {
        if (!checked) {
            const refs = savedRefs.filter(ref => !rows.some(item => ids.includes(selectionId(item)) && matchesRef(item, ref)));
            setSavedRefs(refs); writeRoundStorage(jobId, 'selection', refs);
            window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId, refs } }));
        }
        const next = checked ? Array.from(new Set([...selection, ...ids])) : selection.filter(id => !ids.includes(id));
        onSelectedRecordIdsChange?.(next);
        const recordSelection = next.filter(id => !rows.some(item => selectionId(item) === id && item.design_id));
        setNativeSelected(recordSelection); writeBinderSelection(selectionScope, recordSelection);
        const changedDesignIds = rows.filter(item => ids.includes(selectionId(item)) && item.design_id && (!checked || !item.candidate_ref)).map(item => item.design_id!);
        onSelectedDesignIdsChange?.(checked ? Array.from(new Set([...selectedDesignIds, ...changedDesignIds])) : selectedDesignIds.filter(id => !changedDesignIds.includes(id)));
        const affected = rows.filter(item => ids.includes(selectionId(item)) && (!item.design_id || item.candidate_ref));
        const remaining = nativeSources.filter(source => !affected.some(item => matchesSource(item, source)));
        // A sole document is unambiguous. Multiple states require an explicit document choice.
        const added = checked ? affected.flatMap(item => {
            const ref = item.candidate_ref;
            if (ref?.kind === 'native_document') {
                const doc = item.structures?.find(value => value.artifact_id === ref.artifact_id && (ref.target_state == null || value.target_state === ref.target_state));
                return doc ? [{ job_id: ref.job_id, artifact_id: ref.artifact_id, document: { ...doc, ...(ref.model != null ? { model: ref.model } : {}) } }] : [];
            }
            if (ref) return [];
            const chosen = nativeSources.filter(source => matchesSource(item, source));
            if (chosen.length) return chosen;
            const primary = item.structures?.find(doc => doc.primary) ?? (item.structures?.length === 1 ? item.structures[0] : undefined);
            return primary?.artifact_id ? [{ job_id: jobId, artifact_id: primary.artifact_id, document: primary }] : [];
        }) : [];
        changeSources([...remaining, ...added]);
        const suppliedRefs = affected.flatMap(item => { const ref = item.candidate_ref as CandidateRef | undefined; return ref?.kind && ref.job_id ? [ref] : []; });
        if (suppliedRefs.length) {
            const previous = readRoundStorage<CandidateRef[]>(jobId, 'selection', []);
            const affectedKeys = new Set(suppliedRefs.map(candidateKey));
            const refs = uniqueCandidates([...previous.filter(ref => !affectedKeys.has(candidateKey(ref))), ...(checked ? suppliedRefs : [])]);
            writeRoundStorage(jobId, 'selection', refs);
            window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId, refs } }));
        }
    };
    const designIds = (entries: CohortRow[]) => entries.flatMap(entry => { const id = selectionId(byId.get(entry.id)!); return id ? [id] : []; });
    const pageIds = designIds(pageRows);
    const allPageSelected = pageIds.length > 0 && pageIds.every(id => selectedSet.has(id));
    const filterChanged = (next: Filter[]) => { setFilters(next); setPageIndex(0); };
    const toggleSort = (key: string) => { setSort({ key, descending: sort.key === key && !sort.descending }); setPageIndex(0); };
    const complete = !!page && rows.length === page.total;
    const scopeText = complete ? 'All records loaded' : `${rows.length} of ${page?.total ?? '…'} loaded`;
    const exportRows = useMemo(() => exportScope === 'all' ? rows : exportScope === 'selected' ? rows.filter(item => selectionId(item) && selectedSet.has(selectionId(item)!)) : matched.map(entry => byId.get(entry.id)!), [rows, exportScope, selectedSet, matched, byId]);
    const csv = useMemo(() => [['candidate_key', 'design_id', ...keys].map(csvCell).join(','), ...exportRows.map(item => [item.candidate_key, item.design_id, ...keys.map(key => values(item)[key])].map(csvCell).join(','))].join('\r\n'), [exportRows, keys]);
    const selectedExportRefs: unknown[] = [
        ...savedRefs, ...selectedDesignIds.map(design_id => ({ kind: 'design', job_id: jobId, design_id })),
        ...nativeSources.map(source => ({ kind: 'native_document', job_id: source.job_id, artifact_id: source.artifact_id,
            ...(source.document?.target_state != null ? { target_state: source.document.target_state } : {}),
            ...(source.document?.model != null ? { model: source.document.model } : {}) })),
        ...selection.map(candidate_key => ({ kind: 'record', job_id: jobId, candidate_key })),
    ];
    const unresolvedExportRefs = selectedExportRefs.filter(ref => {
        const selected = ref as CandidateRef & { candidate_key?: string };
        return selected.candidate_key != null ? !byId.has(selected.candidate_key) : !rows.some(item => matchesRef(item, selected));
    });
    const exportEnvelope = nativeGenerationExport(exportRows, {
        jobId, adapter: adapter?.key ?? 'native', page, loaded: rows.length, scope: exportScope,
        filter: { search, population, filters, selected_only: selectedOnly }, rank: { ...sort, scope: rankScope, missing: 'last' },
        readError: query.isError ? String(query.error) : null, loading: query.isFetching,
        selected: selectedExportRefs, unresolved: unresolvedExportRefs,
    });
    const exportJson = JSON.stringify(exportEnvelope, null, 2);
    const { records: exportedRecords, ...exportMetadata } = exportEnvelope;
    const exportCsvMetadata = JSON.stringify({ ...exportMetadata, csv_file: `${jobId}-native-${exportScope}.csv`,
        metric_columns: keys, records: exportedRecords.map(({ native_record: _raw, record: _record, ...identity }, index) => ({ csv_row: index + 2, ...identity,
            native_metrics: exportRows[index].native_metrics ?? null, metric_descriptors: exportRows[index].metric_descriptors ?? null })) }, null, 2);
    const initialMetrics = adapter?.initialMetrics?.(numericKeys);
    const headlineKeys = (initialMetrics?.headline?.length ? initialMetrics.headline : adapter ? ['duration_seconds', ...numericKeys] : ['seq_length', 'dsasa', ...numericKeys]).filter((key, i, all) => numericKeys.includes(key) && all.indexOf(key) === i).slice(0, 2);
    const headlineMetrics = headlineKeys.map(key => ({ key, ...summarizeMetric(matched, key) }));
    const candidateRef = (item: NativeGenerationRecord): CandidateRef | undefined => {
        if (item.candidate_ref) return candidateIdentity(item.candidate_ref);
        if (item.design_id) return { kind: 'design', job_id: jobId, design_id: item.design_id };
        const selected = nativeSources.find(source => source.job_id === jobId && item.structures?.some(doc => doc.artifact_id === source.artifact_id));
        const exact = selected?.document ?? item.structures?.find(doc => doc.primary) ?? (item.structures?.length === 1 ? item.structures[0] : undefined);
        if (exact?.artifact_id) return { kind: 'native_document', job_id: jobId, artifact_id: exact.artifact_id, ...(exact.target_state != null ? { target_state: exact.target_state } : {}) };
        const ref = item.candidate_ref as CandidateRef | undefined;
        return ref?.kind && ref.job_id ? ref : undefined;
    };
    const rankedMembers = (rankScope === 'selected' ? matched.filter(entry => selectedSet.has(entry.id)) : matched).flatMap(entry => { const ref = candidateRef(byId.get(entry.id)!); return ref ? [ref] : []; });
    const table = <section className={`${panel} overflow-hidden`} aria-label="Candidate data table">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-color)] p-4">
            <div><h4 className="font-semibold">{adapter?.recordLabel ?? 'Candidate data'}</h4><p className="text-xs text-[var(--text-secondary)]">{matched.length} matching · {rows.length} loaded · choose a row to inspect its measurements and any available structure</p></div>
            <div className="flex flex-wrap items-center gap-2">
                {(adapter || onSelectedDesignIdsChange || onSelectedNativeSourcesChange) && <><button className={control} type="button" onClick={() => changeSelection(designIds(matched), true)}>Select all matching ({designIds(matched).length})</button><button className={control} type="button" onClick={() => changeSelection(designIds(matched), false)}>Deselect matching</button></>}
                <details className="relative"><summary className={`${control} cursor-pointer`}>Columns ({tableKeys.length})</summary><div className="absolute right-0 z-20 mt-2 max-h-72 w-64 overflow-auto rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3 shadow-xl">
                    <label className="mb-2 flex gap-2 border-b border-[var(--border-color)] pb-2"><input aria-label="All native metric columns" type="checkbox" checked={tableKeys.length === keys.length} onChange={event => setColumns(event.target.checked ? keys : null)} />All native metric columns</label>
                    <input type="search" aria-label="Find a table column" placeholder="Find a measurement…" className={`${control} mb-2 w-full`} value={columnSearch} onChange={event => setColumnSearch(event.target.value)} />
                    {keys.filter(key => `${label(key)} ${key}`.toLowerCase().includes(columnSearch.toLowerCase())).map(key => <label key={key} className="flex gap-2 py-1 text-sm" title={key}><input aria-label={`Column ${key}`} type="checkbox" checked={tableKeys.includes(key)} onChange={event => setColumns(event.target.checked ? [...tableKeys, key] : tableKeys.filter(item => item !== key))} />{label(key)}</label>)}
                </div></details>
            </div>
        </div>
        <div className="overflow-x-auto" aria-label="Candidate table horizontal scroll">
            <table className="w-full border-collapse text-sm"><thead className="sticky top-0 z-10 bg-[var(--bg-primary)] text-left"><tr>
                <th className="sticky left-0 z-10 min-w-48 bg-[var(--bg-primary)] px-4 py-3" aria-sort={sort.key === '$candidate' ? sort.descending ? 'descending' : 'ascending' : 'none'}><div className="flex items-center gap-3">{(adapter || onSelectedDesignIdsChange || onSelectedNativeSourcesChange) && <input type="checkbox" aria-label="Select this page" checked={allPageSelected} disabled={!pageIds.length} onChange={event => changeSelection(pageIds, event.target.checked)} />}<button type="button" onClick={() => toggleSort('$candidate')}>Candidate {sort.key === '$candidate' ? sort.descending ? '↓' : '↑' : '↕'}</button></div></th>
                {tableKeys.map(key => <th key={key} className="whitespace-nowrap px-4 py-3 text-right" title={key} aria-sort={sort.key === key ? sort.descending ? 'descending' : 'ascending' : 'none'}><button type="button" aria-label={`Sort by ${key}`} onClick={() => toggleSort(key)}>{label(key)} {sort.key === key ? sort.descending ? '↓' : '↑' : '↕'}</button></th>)}
            </tr></thead><tbody>{pageRows.map(entry => { const item = byId.get(entry.id)!; return <tr key={entry.id} aria-selected={entry.id === activeId} className={`border-t border-[var(--border-color)] ${entry.id === activeId ? 'bg-blue-500/10' : 'hover:bg-blue-500/5'}`}>
                <td className="sticky left-0 bg-[var(--bg-secondary)] px-4 py-2"><div className="flex items-center gap-3">{(adapter || onSelectedDesignIdsChange || onSelectedNativeSourcesChange) && selectionId(item) && <input aria-label={`Select ${entry.label}`} type="checkbox" checked={selectedSet.has(selectionId(item)!)} onChange={event => changeSelection([selectionId(item)!], event.target.checked)} />}<button type="button" className="max-w-72 break-words py-1 text-left font-medium text-[var(--text-primary)] hover:underline" onClick={() => inspect(item)}>{entry.label}</button></div>{!item.design_id && <small className="text-[var(--text-secondary)]">{!item.structures?.length ? 'No saved molecule' : item.structures.length > 1 ? 'Choose a saved structure in inspection' : 'Saved structure available'}</small>}</td>
                {tableKeys.map(key => <td key={key} className="whitespace-nowrap px-4 py-3 text-right tabular-nums" title={[nativeText(entry.values[key]), nativeGenerationMetricDetail(item, key)].filter(Boolean).join(' · ')}>{formatMetric(entry.values[key])}</td>)}
            </tr>; })}</tbody></table>
            {!matched.length && <p className="p-8 text-center text-[var(--text-secondary)]">No candidates match this view. Clear filters to return to the publication.</p>}
        </div>
        <nav aria-label="Native result pages" className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border-color)] px-4 py-3 text-sm">
            <label>Rows per page <select aria-label="Rows per page" className={control} value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPageIndex(0); }}>{[25, 50, 100].map(size => <option key={size}>{size}</option>)}</select></label>
            <span>{matched.length ? displayedPage * pageSize + 1 : 0}–{Math.min((displayedPage + 1) * pageSize, matched.length)} of {matched.length} matching</span>
            <div className="flex gap-2"><button className={control} type="button" disabled={displayedPage === 0} onClick={() => setPageIndex(displayedPage - 1)}>Previous native records</button><button className={control} type="button" disabled={(displayedPage + 1) * pageSize >= matched.length} onClick={() => setPageIndex(displayedPage + 1)}>Next native records</button></div>
        </nav>
    </section>;
    const [showUnreported, setShowUnreported] = useState(false);
    const [showSavedMolecules, setShowSavedMolecules] = useState(false);
    const [showTable, setShowTable] = useState(false);
    const [showFilters, setShowFilters] = useState(false);
    const compact = Boolean(adapter?.compact || rfd4);
    const cardMetrics = tableKeys.slice(0, 3);
    const continuationNav = document?.artifact_id ? (
        <div className={`${panel} p-3`}>
            <div className="mb-2 text-xs font-semibold text-[var(--text-primary)]">Continue from {cohort.find(entry => entry.id === activeId)?.label ?? 'candidate'}</div>
            <nav aria-label="Continue exact native document" className="flex flex-wrap items-center gap-1.5 text-xs">
                {(rfd4 ? [...rfd4Destinations, 'redesign', 'sequence', 'prediction'] as const : ['redesign', 'sequence', 'prediction'] as const).map(destination => {
                    const href = new URL(continuationHref(continuationSource!, destination), window.location.origin);
                    const back = new URL(href.searchParams.get('return_to')!, window.location.origin);
                    back.searchParams.set('native_candidate', activeId!); back.searchParams.set('artifact_id', document.artifact_id);
                    if (document.target_state != null) back.searchParams.set('target_state', document.target_state); else back.searchParams.delete('target_state');
                    if (sourceModel != null) back.searchParams.set('model_number', String(sourceModel)); else back.searchParams.delete('model_number');
                    if (adapter) back.searchParams.set('native_scope', adapter.key);
                    if (launchContextId) { href.searchParams.set('launch_context_id', launchContextId); back.searchParams.set('launch_context_id', launchContextId); }
                    href.searchParams.set('return_to', `${back.pathname}${back.search}${back.hash}`);
                    return <a className="rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-[var(--text-primary)] hover:border-[var(--accent-primary)] whitespace-nowrap" key={destination} href={`${href.pathname}${href.search}`}>{continuationLabel(destination)}</a>;
                })}
                <button className="rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-[var(--text-primary)] hover:border-[var(--accent-primary)] whitespace-nowrap" type="button" onClick={() => {
                    const selected = { artifact_id: document.artifact_id, ...(document.target_state != null ? { target_state: document.target_state } : {}), ...(sourceModel != null ? { model: sourceModel } : {}) };
                    const ref: CandidateRef = row?.design_id ? { kind: 'design', job_id: jobId, design_id: row.design_id, document: selected } : { kind: 'native_document', job_id: jobId, ...selected };
                    const back = new URL(window.location.href);
                    back.searchParams.set('native_candidate', activeId!); back.searchParams.set('artifact_id', document.artifact_id);
                    if (document.target_state != null) back.searchParams.set('target_state', document.target_state); else back.searchParams.delete('target_state');
                    if (adapter) back.searchParams.set('native_scope', adapter.key);
                    if (sourceModel != null) back.searchParams.set('model_number', String(sourceModel)); else back.searchParams.delete('model_number');
                    const href = buildResultsViewerMolecularDynamicsRoute(jobId, row?.design_id ?? null, { sourceRef: { kind: 'candidate', id: jobId, candidate_ref: ref }, returnTo: `${back.pathname}${back.search}${back.hash}` });
                    window.location.assign(launchContextId ? `${href}&launch_context_id=${encodeURIComponent(launchContextId)}` : href);
                }}>Use as MD starting structure</button>
                {onEditExactDocument && continuationSource && <button className="rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-[var(--text-primary)] hover:border-[var(--accent-primary)] whitespace-nowrap" type="button" onClick={() => {
                    const back = new URL(window.location.href);
                    back.searchParams.set('native_candidate', activeId!);
                    back.searchParams.set('artifact_id', document.artifact_id);
                    if (document.target_state != null) back.searchParams.set('target_state', document.target_state); else back.searchParams.delete('target_state');
                    if (sourceModel != null) back.searchParams.set('model_number', String(sourceModel)); else back.searchParams.delete('model_number');
                    const selector = { artifact_id: document.artifact_id, ...(document.target_state != null ? { target_state: document.target_state } : {}), ...(sourceModel != null ? { model: sourceModel } : {}) };
                    onEditExactDocument(continuationSource, { candidateRef: row?.design_id ? { kind: 'design', job_id: jobId, design_id: row.design_id, document: selector } : { kind: 'native_document', job_id: jobId, ...selector }, returnTo: `${back.pathname}${back.search}${back.hash}` });
                }}>Edit this exact document</button>}
            </nav>
        </div>
    ) : null;

    const candidateRail = (
        <div className={`flex min-w-0 flex-col gap-3 ${view === 'structure' ? 'lg:order-2' : 'lg:order-1'}`}>
            <div className={`${panel} overflow-hidden`}>
                <div className="flex items-center justify-between gap-2 border-b border-[var(--border-color)] px-3 py-2">
                    <span className="text-xs font-semibold text-[var(--text-primary)]">Candidates ({matched.length})</span>
                    <div className="flex items-center gap-1.5">
                        <details className="relative">
                            <summary className="cursor-pointer list-none rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] whitespace-nowrap">Export ({exportRows.length})</summary>
                            <div className="absolute right-0 z-30 mt-2 w-72 max-w-[85vw] space-y-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] p-4 shadow-xl">
                                <label className="block text-sm">Export scope<select aria-label="Export scope" className={`${control} mt-2 w-full`} value={exportScope} onChange={event => setExportScope(event.target.value as typeof exportScope)}><option value="filtered">Matching records</option><option value="selected">Selected records</option><option value="all">All loaded records</option></select></label>
                                <p className="text-xs">{exportRows.length} records · all native metric columns</p>
                                <div className="flex flex-wrap gap-2"><Download name={`${jobId}-native-${exportScope}.csv`} content={csv} mime="text/csv;charset=utf-8">Metrics CSV</Download><Download name={`${jobId}-native-${exportScope}.metadata.json`} content={exportCsvMetadata} mime="application/json">CSV scope metadata</Download><Download name={`${jobId}-native-${exportScope}.json`} content={exportJson} mime="application/json">Native JSON</Download></div>
                            </div>
                        </details>
                    </div>
                </div>
                <div className="space-y-2 p-2.5" aria-label="Cohort controls">
                    <div className="bms-cohort-search flex items-center gap-1.5">
                        <input aria-label="Search candidates" title="Search candidates, source IDs or metric values" className={`${control} min-w-0 flex-1 py-1.5 text-xs`} type="search" placeholder="Search candidates…" value={search} onChange={event => { setSearch(event.target.value); setPageIndex(0); }} />
                        {(search || filters.length > 0 || selectedOnly) && <button className="rounded-md border border-[var(--border-color)] bg-[var(--bg-tertiary)] px-2 py-1 text-xs" type="button" onClick={() => { setSearch(''); setFilters([]); setSelectedOnly(false); setPageIndex(0); }}>Clear filters</button>}
                    </div>
                    <div className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 px-2.5 py-1.5">
                        <button type="button" aria-expanded={showFilters} onClick={() => setShowFilters(v => !v)} className="flex w-full cursor-pointer items-center justify-between text-left text-xs font-medium text-[var(--text-secondary)]"><span>Filter &amp; rank {filters.length > 0 ? `(${filters.length} active)` : ''}</span><span>{showFilters ? 'Hide' : 'Show'}</span></button>
                        <div hidden={!showFilters} className="mt-2 space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                                <button className={`${control} py-1 text-xs`} type="button" onClick={() => filterChanged([...filters, { key: numericKeys[0] ?? keys[0] ?? '', kind: 'range', min: '', max: '' }])}>Add metric filter</button>
                                <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" aria-label="Selected candidates only" checked={selectedOnly} onChange={event => { setSelectedOnly(event.target.checked); setPageIndex(0); }} />Selected only</label>
                            </div>
                            {filters.map((filter, index) => <div key={index} className="flex flex-wrap items-center gap-1.5 text-xs">
                                <CohortMetricPicker label={`Filter ${index + 1} metric`} keys={keys} value={filter.key} onChange={key => filterChanged(filters.map((item, i) => i === index ? { ...item, key } : item))} />
                                <select aria-label={`Filter ${index + 1} condition`} className={`${control} py-1 text-xs`} value={filter.kind} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, kind: event.target.value as Filter['kind'] } : item))}><option value="range">Numeric range (inclusive)</option><option value="missing">Not reported</option><option value="null">Explicit null</option><option value="nonNumeric">Nonnumeric value</option></select>
                                {filter.kind === 'range' && <><input aria-label={`Filter ${index + 1} minimum`} className={`${control} w-20 py-1 text-xs`} type="number" step="any" placeholder="Min" value={filter.min} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, min: event.target.value } : item))} /><span>to</span><input aria-label={`Filter ${index + 1} maximum`} className={`${control} w-20 py-1 text-xs`} type="number" step="any" placeholder="Max" value={filter.max} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, max: event.target.value } : item))} /></>}
                                <button aria-label={`Remove filter ${index + 1}`} className={`${control} py-1 text-xs`} type="button" onClick={() => filterChanged(filters.filter((_, i) => i !== index))}>Remove</button>
                            </div>)}
                            <div className="bms-cohort-ranking space-y-1.5 text-xs">
                                <label className="block">Ranking scope<select className={`${control} mt-1 w-full py-1 text-xs`} aria-label="Ranking scope" value={rankScope} onChange={event => setRankScope(event.target.value as typeof rankScope)}><option value="matching">Matching population</option><option value="selected">Selected in this population</option></select></label>
                                <CohortMetricPicker label="Rank by" keys={numericKeys} value={sort.key} onChange={key => setSort(previous => ({ ...previous, key }))} />
                                <label className="block">Direction<select className={`${control} mt-1 w-full py-1 text-xs`} aria-label="Ranking direction" value={sort.descending ? 'descending' : 'ascending'} onChange={event => setSort(previous => ({ ...previous, descending: event.target.value === 'descending' }))}><option value="ascending">Low first</option><option value="descending">High first</option></select></label>
                            </div>
                            <p className="text-[11px] text-[var(--text-secondary)]">Missing values last. Native rank is unchanged.</p>
                            <p className="text-[11px] text-[var(--text-secondary)]">{matched.length} of {rows.length} loaded records in view · {numericKeys.length} numeric metrics{!adapter?.compact && " · Filters do not change acceptance or selection."}</p>
                        </div>
                    </div>
                </div>
                <div className="max-h-[380px] space-y-1.5 overflow-y-auto px-2.5 pb-2.5">
                    {pageRows.map(entry => {
                        const item = byId.get(entry.id)!;
                        const isActive = entry.id === activeId;
                        const selId = selectionId(item);
                        return <div key={entry.id} className={`rounded-lg border p-2.5 transition-colors ${isActive ? 'border-[var(--accent-primary)] bg-blue-500/10' : 'border-[var(--border-color)] bg-[var(--bg-tertiary)]/50 hover:border-[var(--text-secondary)]'}`}>
                            <div className="flex items-center justify-between gap-2">
                                <div className="flex min-w-0 items-center gap-2">
                                    {(adapter || onSelectedDesignIdsChange || onSelectedNativeSourcesChange) && selId && <input aria-label={`Select card ${entry.label}`} type="checkbox" checked={selectedSet.has(selId)} onChange={event => changeSelection([selId], event.target.checked)} />}
                                    <button type="button" className="truncate text-left text-xs font-semibold text-[var(--text-primary)] hover:underline" onClick={() => inspect(item)}>{entry.label}</button>
                                </div>
                                {entry.values.chain_of_interest != null && <span className="rounded bg-blue-500/15 px-1.5 py-0.5 text-[10px] text-[var(--accent-primary)] whitespace-nowrap">Chain {String(entry.values.chain_of_interest)}</span>}
                            </div>
                            {cardMetrics.length > 0 && <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                                {cardMetrics.map(key => <div key={key} className="rounded bg-[var(--bg-primary)]/60 px-1.5 py-1" title={label(key)}>
                                    <div className="truncate text-[10px] text-[var(--text-secondary)]">{label(key).split(' · ')[0]}</div>
                                    <div className="truncate text-xs font-semibold tabular-nums">{formatMetric(entry.values[key])}</div>
                                </div>)}
                            </div>}
                        </div>;
                    })}
                    {!matched.length && <p className="py-4 text-center text-xs text-[var(--text-secondary)]">No candidates match this view.</p>}
                </div>
            </div>
            {continuationNav}
        </div>
    );

    return <section aria-label="Native initial-generation results" className="min-w-0 space-y-3 text-[var(--text-primary)]">
        <header style={view === 'analytics' ? { display: 'none' } : undefined} className={`${panel} flex flex-wrap items-center justify-between gap-2 px-3 py-2`}>
            <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{adapter?.title ?? 'Candidates'}</h3>
                <span className="rounded-full border border-[var(--border-color)] px-2.5 py-0.5 text-xs text-[var(--text-secondary)]">{scopeText}</span>
                <DeNovoRepeatSettings jobId={jobId} />
            </div>
            {page && page.total > 0 && <div role="tablist" aria-label="Native result review" className="flex flex-wrap gap-1">
                {([['candidates', 'Candidates'], ['structure', 'Structure'], ['table', 'Data table'], ['dashboard', 'Dashboard'], ['analytics', 'Custom charts']] as const).map(([key, tabLabel]) => <button key={key} type="button" role="tab" aria-selected={view === key} className={`rounded-md px-3 py-1.5 text-xs font-medium ${view === key ? 'border border-[var(--accent-primary)]/50 bg-blue-500/15 text-[var(--text-primary)]' : 'border border-transparent text-[var(--text-secondary)] hover:bg-blue-500/5 hover:text-[var(--text-primary)]'}`} onClick={event => { setView(key); const section = event?.currentTarget?.closest('section'); if (section && key === 'analytics') requestAnimationFrame(() => section.scrollIntoView({ block: 'start' })); }}>{adapter && key === 'structure' && !documents.length ? 'Inspect record' : tabLabel}</button>)}
            </div>}
        </header>
        {query.isLoading && <p role="status">Reading published generation records…</p>}
        {query.isError && <p role="status" className={`${panel} p-4`}>Native publication {rows.length ? 'is partially loaded' : 'is not available'}: {String(query.error)} <button className={control} type="button" onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>Retry native readback</button></p>}
        {query.isFetchingNextPage && <p role="status">Loading the full cohort: {rows.length} of {page?.total} records. Current plots and exports cover loaded records only.</p>}
        {page && <>
            <div hidden={view === 'analytics' || view === 'structure'}>{adapter?.summary?.(matched.map(entry => byId.get(entry.id)!))}</div>
            {page.total === 0 ? <div className={`${panel} p-5`}><p>No native candidate records were emitted. This published zero-yield result remains available for review.</p><Download name={`${jobId}-native-${exportScope}.json`} content={exportJson} mime="application/json">Native JSON</Download><Download name={`${jobId}-native-${exportScope}.csv`} content={csv} mime="text/csv;charset=utf-8">Metrics CSV</Download><Download name={`${jobId}-native-${exportScope}.metadata.json`} content={exportCsvMetadata} mime="application/json">CSV scope metadata</Download></div> : <>
                {view === 'dashboard' && matched.length !== rows.length && <p className="text-sm text-[var(--text-secondary)]">Showing {matched.length} of {rows.length} records after filtering. <button type="button" className="underline" onClick={() => setView('table')}>Adjust filters</button></p>}
                <div hidden={view !== 'dashboard' && view !== 'analytics'}>{(analyticsOpened || view === 'dashboard' || view === 'analytics') && <Suspense fallback={<p role="status">Loading cohort charts…</p>}><CohortAnalytics getMetricLabel={label} getMetricDescription={key => describeCohortMetric(key, keys).description} renderMetricPicker={props => <CohortMetricPicker {...props} />} initialMetrics={initialMetrics} rows={matched} selectedIds={selectedCohortIds} activeId={activeId} mode={view === 'analytics' ? 'analytics' : 'dashboard'} onInspect={id => { const item = byId.get(id); if (item) inspect(item); }} onSelect={ids => changeSelection(designIds(matched.filter(entry => ids.includes(entry.id))), true)} /></Suspense>}</div>

                {view === 'table' && table}

                <div hidden={view !== 'candidates' && view !== 'structure'} className={view === 'structure' ? 'grid min-w-0 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_300px]' : 'grid min-w-0 items-start gap-3 lg:grid-cols-[340px_minmax(0,1fr)]'}>
                    {candidateRail}
                    <div ref={inspector} className={`${panel} p-3 ${view === 'structure' ? 'lg:order-1' : 'lg:order-2'}`} aria-label="Candidate structure inspector">
                        {(structureOpened || view === 'structure' || view === 'candidates') && <Suspense fallback={<p role="status">Loading native structure…</p>}>{!row ? <p role="status">{!complete ? 'Loading the requested candidate from the publication. ' : 'Requested candidate is unavailable in this publication. '}Another candidate is not substituted.</p> : <>
                            <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <h4 title={String(row.native_input_id ?? row.candidate_key ?? '')} className="text-sm font-semibold">{cohort.find(entry => entry.id === activeId)?.label ?? 'Candidate'}</h4>
                                    {(!adapter || documents.length > 0) && <label className="text-xs text-[var(--text-secondary)]">Structure <select className={`${control} ml-1 max-w-full py-1 text-xs`} aria-label="Published document" value={document ? String(documents.indexOf(document)) : ''} onChange={event => { const doc = documents[Number(event.target.value)]; if (doc) inspect(row, doc); }}>
                                        {!document && <option value="">{explicit ? 'Requested document unavailable' : 'Choose a saved structure'}</option>}{documents.map((doc, i) => <option key={`${doc.artifact_id}:${doc.target_state}:${i}`} value={i}>{nativeDocumentLabel(doc)}{doc.primary ? ' · primary' : ''}</option>)}
                                    </select></label>}
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <button className={`${control} py-1 text-xs`} type="button" disabled={navigationRows.indexOf(row) <= 0} onClick={() => inspect(navigationRows[navigationRows.indexOf(row) - 1])}>Previous candidate</button>
                                    <button className={`${control} py-1 text-xs`} type="button" disabled={navigationRows.indexOf(row) >= navigationRows.length - 1} onClick={() => inspect(navigationRows[navigationRows.indexOf(row) + 1])}>Next candidate</button>
                                </div>
                            </div>
                            <div className={adapter?.related ? 'grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]' : 'min-w-0'}>
                                <div className="min-w-0">
                                    {(!adapter || documents.length > 0) && <div className="min-w-0" aria-label="Published molecule and measurements">
                                        {document?.download_url ? <NativeDocumentWorkbench key={`${document.artifact_id}:${document.target_state}:${document.download_url}`} jobId={jobId} document={document} collapsed={!viewerToolsOpen} height={view === 'structure' ? 680 : 520} /> : <p role="status">{explicit ? 'Requested native document is unavailable. The Design primary structure is not substituted.' : documents.some(doc => doc.download_url) ? 'Choose a saved structure above to inspect this candidate.' : 'No downloadable native structure was published for this record.'}</p>}
                                    </div>}
                                </div>
                                {adapter?.related?.(row)}
                            </div>
                            <NativeGeneratedSequences row={row} />
                            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                                {document?.download_url && <><button className={`${control} py-1 text-xs`} type="button" aria-expanded={viewerToolsOpen} onClick={() => setViewerToolsOpen(value => !value)}>{viewerToolsOpen ? 'Hide viewer tools' : 'Measurements and exports'}</button><a className="underline" href={resolveApiResourceUrl(document.download_url)} download>Download exact native document</a></>}
                                {row.design_id && <a className="underline" href={nativeCandidateRoute(jobId, row.design_id, document, launchContextId)}>Open candidate workbench</a>}
                            </div>
                            {rfd4 && continuationSource && <Rfd4RetainedStateContinuation key={`${document!.artifact_id}:${document!.target_state}:${sourceModel}`} candidate={activeId ?? undefined} page={page} source={{ ...continuationSource, expected_sha256: document!.sha256 }} />}
                            {adapter?.inspect?.(row)}
                            <div className="mt-2 grid gap-2 md:grid-cols-2">
                                {(!adapter || documents.length > 0) && <details className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 px-3 py-2"><summary className="cursor-pointer text-xs font-medium">Native measurements ({Object.entries(values(row)).filter(([, value]) => value != null).length})</summary><aside className="mt-2 min-w-0"><dl aria-label="Native measurements" className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,14rem),1fr))] gap-x-4 text-xs">{Object.entries(values(row)).filter(([, value]) => value != null).map(([key, value]) => <div key={key} className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-baseline gap-2 border-b border-[var(--border-color)] py-1.5" title={[key, nativeGenerationMetricDetail(row, key)].filter(Boolean).join(' · ')}><dt className="min-w-0 [overflow-wrap:anywhere] text-[var(--text-secondary)]">{label(key)}</dt><dd className="min-w-0 [overflow-wrap:anywhere] text-right tabular-nums">{formatMetric(value)}</dd></div>)}</dl>{Object.entries(values(row)).some(([, value]) => value == null) && <div className="mt-2 text-xs text-[var(--text-secondary)]"><button type="button" className="underline" aria-expanded={showUnreported} onClick={() => setShowUnreported(v => !v)}>{showUnreported ? 'Hide unreported fields' : 'Unreported fields'}</button>{showUnreported && Object.entries(values(row)).filter(([, value]) => value == null).map(([key, value]) => <p key={key}>{label(key)}: {formatMetric(value)}</p>)}</div>}</aside></details>}
                                <details className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 px-3 py-2 text-xs"><summary className="cursor-pointer font-medium">Candidate details</summary><label className="mt-1.5 block">Exact candidate identifier<input className="mt-1 block w-full rounded bg-[var(--bg-primary)] px-2 py-1 font-mono text-xs" aria-label="Exact candidate identifier" readOnly value={String(row.candidate_key ?? row.native_input_id ?? '')} onFocus={event => event.target.select()} /></label></details>
                            </div>
                            {!row.design_id && <div className="mt-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)]/40 px-3 py-2"><button type="button" className="flex w-full items-center justify-between text-left text-xs font-medium" aria-expanded={showSavedMolecules} onClick={() => setShowSavedMolecules(v => !v)}><span>Select saved structures for another operation</span><span className="text-[var(--text-secondary)]">{showSavedMolecules ? 'Hide' : 'Show'}</span></button>
                                <div hidden={!showSavedMolecules}>{!documents.length ? <p className="mt-1 text-xs">No saved molecule. This record remains available for inspection and export.</p> : documents.filter(doc => doc.artifact_id).map(doc => doc === document && sourceModel != null ? { ...doc, model: sourceModel } : doc).map((doc, index) => <div key={`${doc.artifact_id}:${doc.target_state}:${doc.model}`} className="my-2 space-y-1 text-xs"><label className="flex items-start gap-2"><input type="checkbox" aria-label={`Use native document ${doc.artifact_id}`} checked={nativeSources.some(source => source.job_id === jobId && source.artifact_id === doc.artifact_id && source.document?.target_state === doc.target_state && source.document?.model === doc.model)} onChange={event => {
                                    const remaining = nativeSources.filter(source => source.job_id !== jobId || source.artifact_id !== doc.artifact_id || source.document?.target_state !== doc.target_state || source.document?.model !== doc.model);
                                    changeSources(event.target.checked ? [...remaining, { job_id: jobId, artifact_id: doc.artifact_id, document: doc }] : remaining);
                                    if (event.target.checked) { const next = Array.from(new Set([...nativeSelected, selectionId(row)])); setNativeSelected(next); writeBinderSelection(selectionScope, next); }
                                }} /><span>Use saved molecule {index + 1} · {nativeDocumentLabel(doc)}{doc.primary ? ' · primary' : ''}</span></label><details className="text-xs text-[var(--text-secondary)]"><summary>Exact saved molecule identity</summary><code className="block break-all">{doc.logical_path}</code><code className="block break-all">{doc.artifact_id}</code></details></div>)}</div>
                            </div>}
                        </>}</Suspense>}
                    </div>
                </div>

                <div className="grid gap-2 md:grid-cols-2">
                    {view !== 'table' && <div className={`${panel} p-3`}>
                        <button type="button" className="flex w-full items-center justify-between gap-2 text-left text-sm font-medium" aria-expanded={showTable} onClick={() => setShowTable(v => !v)}>
                            <span>Candidate data table</span>
                            <span className="text-xs text-[var(--text-secondary)]">{matched.length} rows · {tableKeys.length} metrics · {showTable ? 'Hide' : 'Show'}</span>
                        </button>
                        <div hidden={!showTable} className="mt-3">{table}</div>
                    </div>}
                    <details className={`${panel} p-3`}>
                        <summary className="flex cursor-pointer items-center justify-between gap-2 text-sm font-medium">
                            <span>Cohort summary &amp; populations</span>
                            <span className="text-xs text-[var(--text-secondary)]">{page.total} total · {selection.length} selected</span>
                        </summary>
                        <div className="mt-3 space-y-3">
                            <div className="grid grid-cols-2 gap-2" aria-label="Cohort summary">
                                {[
                                    [adapter?.recordLabel ?? 'Publication records', page.total, `${matched.length} in view · ${rows.length} loaded`],
                                    ...(page.record_counts ? [['Molecular records', page.record_counts.molecular_records, 'Not a unique candidate count'], ['Observations', page.record_counts.observations, 'Events, not generated candidates']] : []),
                                    ...headlineMetrics.map(metric => [`Median ${label(metric.key)}`, metric.median == null ? '—' : formatMetric(metric.median), `${metric.observed} observations in this view`]),
                                    ['Selected', selection.length, 'Records for export; saved molecules for operations'],
                                ].map(([metricTitle, count, detail]) => <div key={String(metricTitle)} className={`${panel} flex min-w-0 items-center justify-between gap-2 px-3 py-2`}><div title={String(metricTitle)} className="truncate text-xs text-[var(--text-secondary)]">{metricTitle}</div><div title={String(detail)} className="text-base font-semibold tabular-nums">{count}</div></div>)}
                            </div>
                            <nav aria-label="Native populations" className="flex flex-wrap items-center gap-2 text-xs">
                                {(rfd4 ? [['all', 'All records'], ...Object.keys(page.record_counts?.populations ?? {}).map(key => [`native:${key}`, key.replaceAll('_', ' ')])] : [['all', 'All'], ['passed', 'Passed'], ['ranked', 'Ranked'], ['rejected', 'Rejected']]).map(([key, popTitle]) => <button type="button" className={`${control} py-1 text-xs`} aria-pressed={population === key} key={key} onClick={() => { setPopulation(key); setPageIndex(0); }}>{popTitle}</button>)}
                                <span className="text-[var(--text-secondary)]">{selection.length} checked in this population · {selection.filter(id => !matched.some(entry => entry.id === id)).length} outside this view</span>
                            </nav>
                        </div>
                    </details>
                    <div className={`${panel} p-3`}>
                        <BinderCandidateSets jobId={jobId} candidates={rankedMembers} ranking={{ metric: sort.key, direction: sort.descending ? 'descending' : 'ascending', missing: 'last', scope: rankScope }} filterState={{ search, filters, population }} onLoad={refs => { writeRoundStorage(jobId, 'selection', refs); window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId, refs, replace: true } })); }} />
                    </div>
                    {!adapter && <BinderPredictionEvidence jobId={jobId} sourceDesignId={row?.design_id} launchContextId={launchContextId} />}
                </div>
                <p className="text-xs text-[var(--text-secondary)]">Model predictions are not experimental validation. {adapter && 'Phase plots show recorded optimization updates, not acceptance scores. '}{!complete && 'Only loaded records are included.'}</p>
            </>}
            {(page.execution_status || page.population_completeness) && <p role="status" className="text-xs text-[var(--text-secondary)]">Execution: {page.execution_status ?? 'Not reported'} · Population: {page.population_completeness ?? 'Not reported'}</p>}
            <div className="grid gap-2 md:grid-cols-3">
                {!!page.observation_errors?.length && <OnDemand title="Native observation errors"><BindCraft2SettingsReadback value={page.observation_errors} /></OnDemand>}
                {row && <OnDemand title="Selected record: complete native readback"><BindCraft2SettingsReadback value={row.native_record ?? row} /><a href={dataUrl(row.native_record ?? row)} download={`${jobId}-native-record.json`}>Export selected record JSON</a></OnDemand>}
                <OnDemand title="Native receipt and accounting"><BindCraft2SettingsReadback value={page.receipt} /><a href={dataUrl(page.receipt)} download={`${jobId}-native-receipt.json`}>Export receipt JSON</a></OnDemand>
                <OnDemand title="Native files and provenance">{page.artifacts?.map((file, i) => <p key={i}>{file.path} {file.download_url && <a href={resolveApiResourceUrl(file.download_url)} download>Download native file</a>}</p>)}<BindCraft2SettingsReadback value={page.publication} /><a href={dataUrl(page.publication)} download={`${jobId}-native-publication.json`}>Export publication JSON</a></OnDemand>
            </div>
        </>}
    </section>;
}
