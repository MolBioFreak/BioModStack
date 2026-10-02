import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { BinderPredictionEvidence } from './BinderPredictionEvidence';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { metricKeys, numericMetricKeys, formatMetric, summarizeMetric, type CohortRow } from '../lib/cohortAnalytics';
import { fetchNativeBinderGeneration, nativeCandidateRoute, nativeGenerationMetrics, nativeGenerationMetricDetail, nativeGenerationMetricUnit, type NativeGenerationPage, type NativeGenerationRecord, type NativeGenerationDocument } from '../lib/nativeBinderResults';

import type { CohortInitialMetrics } from './CohortAnalytics';
import { CohortMetricPicker } from './CohortMetricPicker';
import { describeCohortMetric } from '../lib/cohortMetricPresentation';

// Load useful visualization and operation owners only when their view is demanded.
const CohortAnalytics = lazy(() => import('./CohortAnalytics').then(module => ({ default: module.CohortAnalytics })));
const StructureWorkbench = lazy(() => import('../structureViewer/StructureWorkbench').then(module => ({ default: module.StructureWorkbench })));

export interface NativeWorkbenchAdapter {
    key: string; title: string; recordLabel?: string;
    fetchPage: (offset: number, signal?: AbortSignal) => Promise<NativeGenerationPage>;
    label: (row: NativeGenerationRecord) => string;
    preferredColumns?: string[];
    initialMetrics?: (numericKeys: string[]) => CohortInitialMetrics;
    compact?: boolean;
    inspect?: (row: NativeGenerationRecord) => ReactNode;
    summary?: (rows: NativeGenerationRecord[]) => ReactNode;
}
export interface Props {
    adapter?: NativeWorkbenchAdapter;
    jobId: string; status?: string; launchContextId?: string | null;
    selectedDesignId?: string; selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
    onInspectDocument?: (row: NativeGenerationRecord, document?: NativeGenerationDocument) => void;
    artifactId?: string | null; targetState?: string | null;
}
type View = 'dashboard' | 'analytics' | 'table' | 'structure';
type Filter = { key: string; kind: 'range' | 'missing' | 'null' | 'nonNumeric'; min: string; max: string };
const control = 'rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50';
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
function NativeGenerationWorkbench({ adapter, jobId, status, launchContextId, selectedDesignId, selectedDesignIds = [], onSelectedDesignIdsChange, onInspectDocument, artifactId, targetState }: Props) {
    const [view, setView] = useState<View>(selectedDesignId || artifactId ? 'structure' : 'dashboard');
    const [structureOpened, setStructureOpened] = useState(view === 'structure');
    const [analyticsOpened, setAnalyticsOpened] = useState(view === 'dashboard' || view === 'analytics');
    useEffect(() => {
        if (view === 'structure') setStructureOpened(true);
        if (view === 'dashboard' || view === 'analytics') setAnalyticsOpened(true);
    }, [view]);
    const [search, setSearch] = useState('');
    const [filters, setFilters] = useState<Filter[]>([]);
    const [selectedOnly, setSelectedOnly] = useState(false);
    const [sort, setSort] = useState({ key: '', descending: false });
    const [pageIndex, setPageIndex] = useState(0);
    const [pageSize, setPageSize] = useState(25);
    const [columns, setColumns] = useState<string[] | null>(null);
    const [columnSearch, setColumnSearch] = useState('');
    const [exportScope, setExportScope] = useState<'filtered' | 'selected' | 'all'>('filtered');
    const [local, setLocal] = useState<{ scope: string; key: string; document?: NativeGenerationDocument }>();
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
    const rows = useMemo(() => query.data?.pages.flatMap(part => part.records) ?? [], [query.data]);
    const cohort = useMemo<CohortRow[]>(() => rows.map((item, i) => ({ id: rowKey(item, i), label: adapter?.label(item) ?? item.candidate_key ?? item.design_id ?? `Native record ${i + 1}`, values: values(item) })), [rows]);
    const keys = useMemo(() => metricKeys(cohort), [cohort]);
    const numericKeys = useMemo(() => numericMetricKeys(cohort), [cohort]);
    const label = (key: string) => { const unit = rows.map(item => nativeGenerationMetricUnit(item, key)).find(Boolean); return `${describeCohortMetric(key, keys).label}${unit ? ` (${unit})` : ''}`; };
    const preferred = (adapter?.preferredColumns ?? ['seq_length', 'dsasa', 'radius_of_gyration', 'num_ca_ca_clashes']).filter(key => keys.includes(key));
    const [nativeSelected, setNativeSelected] = useState<string[]>([]);
    const selectionId = (item: NativeGenerationRecord) => adapter ? item.candidate_key : item.design_id;
    const selection = adapter ? Array.from(new Set([...nativeSelected, ...rows.filter(item => item.design_id && selectedDesignIds.includes(item.design_id)).flatMap(item => item.candidate_key ? [item.candidate_key] : [])])) : selectedDesignIds;
    const tableKeys = columns ?? (preferred.length ? preferred : keys.slice(0, 6));
    const byId = useMemo(() => new Map(cohort.map((entry, index) => [entry.id, rows[index]])), [cohort, rows]);
    const selectedSet = useMemo(() => new Set(selection), [selection]);
    const selectedCohortIds = useMemo(() => cohort.filter(entry => { const item = byId.get(entry.id); const id = item && selectionId(item); return id && selectedSet.has(id); }).map(entry => entry.id), [cohort, byId, selectedSet]);
    const matched = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const result = cohort.filter(entry => {
            const source = byId.get(entry.id)!;
            if (selectedOnly && (!selectionId(source) || !selectedSet.has(selectionId(source)!))) return false;
            if (needle && ![entry.label, source.design_id, source.native_input_id, source.source_row_index, ...Object.values(entry.values)].some(value => nativeText(value).toLowerCase().includes(needle))) return false;
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
    }, [cohort, byId, search, selectedOnly, selectedSet, filters, sort]);
    const displayedPage = Math.min(pageIndex, Math.max(0, Math.ceil(matched.length / pageSize) - 1));
    const navigationRows = matched.map(entry => byId.get(entry.id)!);
    const pageRows = matched.slice(displayedPage * pageSize, (displayedPage + 1) * pageSize);
    const localSelection = local?.scope === scope ? local : undefined;
    const activeId = localSelection?.key ?? (selectedDesignId ? cohort.find(entry => byId.get(entry.id)?.design_id === selectedDesignId)?.id : cohort[0]?.id);
    const row = activeId ? byId.get(activeId) : undefined;
    const documents = row?.structures ?? [];
    const explicit = localSelection?.document != null || (!localSelection && (artifactId != null || targetState != null));
    const requestedArtifact = localSelection?.document?.artifact_id ?? artifactId;
    const requestedState = localSelection?.document ? localSelection.document.target_state : targetState;
    const document = explicit
        ? documents.find(doc => (requestedArtifact == null || doc.artifact_id === requestedArtifact) && (requestedState == null || doc.target_state === requestedState))
        : documents.find(doc => doc.primary) ?? documents[0];
    const inspect = (item: NativeGenerationRecord, doc?: NativeGenerationDocument) => {
        if (onInspectDocument && item.design_id) { setLocal(undefined); onInspectDocument(item, doc); }
        else setLocal({ scope, key: rowKey(item, rows.indexOf(item)), document: doc });
        setView('structure');
    };
    const changeSelection = (ids: string[], checked: boolean) => {
        const next = checked ? Array.from(new Set([...selection, ...ids])) : selection.filter(id => !ids.includes(id));
        if (adapter) { setNativeSelected(next); onSelectedDesignIdsChange?.(Array.from(new Set([...selectedDesignIds.filter(id => !rows.some(item => item.design_id === id)), ...rows.filter(item => item.candidate_key && next.includes(item.candidate_key) && item.design_id).map(item => item.design_id!)]))); }
        else onSelectedDesignIdsChange?.(next);
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
    const exportJson = useMemo(() => JSON.stringify(exportRows.map(item => item.native_record ?? item), null, 2), [exportRows]);
    const initialMetrics = adapter?.initialMetrics?.(numericKeys);
    const headlineKeys = (initialMetrics?.headline?.length ? initialMetrics.headline : adapter ? ['duration_seconds', ...numericKeys] : ['seq_length', 'dsasa', ...numericKeys]).filter((key, i, all) => numericKeys.includes(key) && all.indexOf(key) === i).slice(0, 2);
    const headlineMetrics = headlineKeys.map(key => ({ key, ...summarizeMetric(matched, key) }));
    const table = <section className={`${panel} overflow-hidden`} aria-label="Candidate data table">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-color)] p-4">
            <div><h4 className="font-semibold">{adapter?.recordLabel ?? 'Candidate data'}</h4><p className="text-xs text-[var(--text-secondary)]">{matched.length} matching · {rows.length} loaded · choose a row to inspect its measurements and any available structure</p></div>
            <div className="flex flex-wrap items-center gap-2">
                {(adapter || onSelectedDesignIdsChange) && <><button className={control} type="button" onClick={() => changeSelection(designIds(matched), true)}>Select all matching ({designIds(matched).length})</button><button className={control} type="button" onClick={() => changeSelection(designIds(matched), false)}>Deselect matching</button></>}
                <details className="relative"><summary className={`${control} cursor-pointer`}>Columns ({tableKeys.length})</summary><div className="absolute right-0 z-20 mt-2 max-h-72 w-64 overflow-auto rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3 shadow-xl">
                    <label className="mb-2 flex gap-2 border-b border-[var(--border-color)] pb-2"><input aria-label="All native metric columns" type="checkbox" checked={tableKeys.length === keys.length} onChange={event => setColumns(event.target.checked ? keys : null)} />All native metric columns</label>
                    <input type="search" aria-label="Find a table column" placeholder="Find a measurement…" className={`${control} mb-2 w-full`} value={columnSearch} onChange={event => setColumnSearch(event.target.value)} />
                    {keys.filter(key => `${label(key)} ${key}`.toLowerCase().includes(columnSearch.toLowerCase())).map(key => <label key={key} className="flex gap-2 py-1 text-sm" title={key}><input aria-label={`Column ${key}`} type="checkbox" checked={tableKeys.includes(key)} onChange={event => setColumns(event.target.checked ? [...tableKeys, key] : tableKeys.filter(item => item !== key))} />{label(key)}</label>)}
                </div></details>
            </div>
        </div>
        <div className="max-h-[520px] overflow-auto">
            <table className="w-full border-collapse text-sm"><thead className="sticky top-0 z-10 bg-[var(--bg-primary)] text-left"><tr>
                <th className="sticky left-0 z-10 min-w-48 bg-[var(--bg-primary)] px-4 py-3" aria-sort={sort.key === '$candidate' ? sort.descending ? 'descending' : 'ascending' : 'none'}><div className="flex items-center gap-3">{(adapter || onSelectedDesignIdsChange) && <input type="checkbox" aria-label="Select this page" checked={allPageSelected} disabled={!pageIds.length} onChange={event => changeSelection(pageIds, event.target.checked)} />}<button type="button" onClick={() => toggleSort('$candidate')}>Candidate {sort.key === '$candidate' ? sort.descending ? '↓' : '↑' : '↕'}</button></div></th>
                {tableKeys.map(key => <th key={key} className="whitespace-nowrap px-4 py-3 text-right" title={key} aria-sort={sort.key === key ? sort.descending ? 'descending' : 'ascending' : 'none'}><button type="button" aria-label={`Sort by ${key}`} onClick={() => toggleSort(key)}>{label(key)} {sort.key === key ? sort.descending ? '↓' : '↑' : '↕'}</button></th>)}
            </tr></thead><tbody>{pageRows.map(entry => { const item = byId.get(entry.id)!; return <tr key={entry.id} aria-selected={entry.id === activeId} className={`border-t border-[var(--border-color)] ${entry.id === activeId ? 'bg-blue-500/10' : 'hover:bg-blue-500/5'}`}>
                <td className="sticky left-0 bg-[var(--bg-secondary)] px-4 py-2"><div className="flex items-center gap-3">{(adapter || onSelectedDesignIdsChange) && selectionId(item) && <input aria-label={`Select ${entry.label}`} type="checkbox" checked={selectedSet.has(selectionId(item)!)} onChange={event => changeSelection([selectionId(item)!], event.target.checked)} />}<button type="button" className="max-w-72 break-words py-1 text-left font-medium text-[var(--text-primary)] hover:underline" onClick={() => inspect(item)}>{entry.label}</button></div>{!adapter && !item.design_id && <small className="text-[var(--text-secondary)]">Source record · no linked candidate</small>}</td>
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
    return <section aria-label="Native initial-generation results" className={`min-w-0 ${adapter?.compact ? "space-y-1" : "space-y-4"} text-[var(--text-primary)]`}>
        <header style={view === 'analytics' ? { display: 'none' } : undefined} className="flex flex-wrap items-center justify-between gap-3"><h3 className={`${adapter?.compact ? "text-base" : "text-xl"} font-semibold`}>{adapter?.title ?? "Generation dashboard"}</h3><span className="rounded-full border border-[var(--border-color)] px-3 py-1 text-xs">{scopeText}</span></header>
        {!adapter && <BinderPredictionEvidence jobId={jobId} sourceDesignId={row?.design_id} launchContextId={launchContextId} />}
        {query.isLoading && <p role="status">Reading published generation records…</p>}
        {query.isError && <p role="status" className={`${panel} p-4`}>Native publication {rows.length ? 'is partially loaded' : 'is not available'}: {String(query.error)} <button className={control} type="button" onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>Retry native readback</button></p>}
        {query.isFetchingNextPage && <p role="status">Loading the full cohort: {rows.length} of {page?.total} records. Current plots and exports cover loaded records only.</p>}
        {page && <>
            <div hidden={view === 'analytics'}>{adapter?.summary?.(matched.map(entry => byId.get(entry.id)!))}</div>
            {page.total === 0 ? <p className={`${panel} p-5`}>No native candidate records were emitted. This published zero-yield result remains available for review.</p> : <>
                <div className={`grid grid-cols-2 ${adapter?.compact ? "gap-2" : "gap-3"} lg:grid-cols-4`} aria-label="Cohort summary" style={view === 'analytics' ? { display: 'none' } : undefined}>
                    {[
                        [adapter?.recordLabel ?? 'Published candidates', page.total, `${matched.length} in view · ${rows.length} loaded`],
                        ...headlineMetrics.map(metric => [`Median ${label(metric.key)}`, metric.median == null ? '—' : formatMetric(metric.median), `${metric.observed} observations in this view`]),
                        ['Selected', selection.length, adapter ? 'Native record selection for export' : 'Shared with candidate operations'],
                    ].map(([label, count, detail]) => <div key={String(label)} className={`${panel} px-4 py-2 ${adapter?.compact ? "flex min-w-0 items-center justify-between gap-2" : ""}`}><div title={String(label)} className={`text-xs text-[var(--text-secondary)] ${adapter?.compact ? "truncate" : ""}`}>{label}</div><div title={String(detail)} className={`${adapter?.compact ? "text-lg" : "my-1 text-2xl"} font-semibold tabular-nums`}>{count}</div>{!adapter?.compact && <div className="text-xs text-[var(--text-secondary)]">{detail}</div>}</div>)}
                </div>
                <div role="tablist" aria-label="Native result review" className="flex flex-wrap gap-1 border-b border-[var(--border-color)]">{([['dashboard', 'Dashboard'], ['analytics', 'Plotly Lab'], ['table', 'Data table'], ['structure', 'Structure']] as const).map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={view === key} className={`border-b-2 px-4 py-2 text-sm font-medium ${view === key ? 'border-blue-500 bg-blue-500/10' : 'border-transparent hover:bg-blue-500/5'}`} onClick={event => { setView(key); const section = event?.currentTarget?.closest('section'); if (section && key === 'analytics') requestAnimationFrame(() => section.scrollIntoView({ block: 'start' })); }}>{adapter && key === 'structure' ? 'Inspect record' : label}</button>)}</div>
                {view === 'dashboard' && matched.length !== rows.length && <p className="text-sm text-[var(--text-secondary)]">Showing {matched.length} of {rows.length} records after filtering. <button type="button" className="underline" onClick={() => setView('table')}>Adjust filters</button></p>}
                <div hidden={view === 'dashboard' || view === 'structure'} className={`${panel} ${adapter?.compact ? "space-y-1 p-2" : "space-y-3 p-3"}`} aria-label="Cohort controls">
                    <div className="flex flex-wrap items-center gap-3"><input aria-label="Search candidates" className={`${control} min-w-0 flex-1`} type="search" placeholder="Search candidates, source IDs or metric values…" value={search} onChange={event => { setSearch(event.target.value); setPageIndex(0); }} />
                        <button className={control} type="button" onClick={() => filterChanged([...filters, { key: numericKeys[0] ?? keys[0] ?? '', kind: 'range', min: '', max: '' }])}>Add metric filter</button>
                        <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label="Selected candidates only" checked={selectedOnly} onChange={event => { setSelectedOnly(event.target.checked); setPageIndex(0); }} />Selected only</label>
                        {(search || filters.length > 0 || selectedOnly) && <button className={control} type="button" onClick={() => { setSearch(''); setFilters([]); setSelectedOnly(false); setPageIndex(0); }}>Clear filters</button>}
                        <details className="relative"><summary className={`${control} cursor-pointer`}>Export ({exportRows.length})</summary><div className="absolute right-0 z-30 mt-2 w-72 max-w-[85vw] space-y-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] p-4 shadow-xl"><label className="block text-sm">Export scope<select aria-label="Export scope" className={`${control} mt-2 w-full`} value={exportScope} onChange={event => setExportScope(event.target.value as typeof exportScope)}><option value="filtered">Matching records</option><option value="selected">Selected records</option><option value="all">All loaded records</option></select></label><p className="text-xs">{exportRows.length} records · all native metric columns</p><div className="flex flex-wrap gap-2"><Download name={`${jobId}-native-${exportScope}.csv`} content={csv} mime="text/csv;charset=utf-8">Metrics CSV</Download><Download name={`${jobId}-native-${exportScope}.json`} content={exportJson} mime="application/json">Native JSON</Download></div></div></details>
                    </div>
                    {filters.map((filter, index) => <div key={index} className="flex flex-wrap items-center gap-2 text-sm">
                        <CohortMetricPicker label={`Filter ${index + 1} metric`} keys={keys} value={filter.key} onChange={key => filterChanged(filters.map((item, i) => i === index ? { ...item, key } : item))} />
                        <select aria-label={`Filter ${index + 1} condition`} className={control} value={filter.kind} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, kind: event.target.value as Filter['kind'] } : item))}><option value="range">Numeric range (inclusive)</option><option value="missing">Not reported</option><option value="null">Explicit null</option><option value="nonNumeric">Nonnumeric value</option></select>
                        {filter.kind === 'range' && <><input aria-label={`Filter ${index + 1} minimum`} className={`${control} w-28`} type="number" step="any" placeholder="Minimum" value={filter.min} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, min: event.target.value } : item))} /><span>to</span><input aria-label={`Filter ${index + 1} maximum`} className={`${control} w-28`} type="number" step="any" placeholder="Maximum" value={filter.max} onChange={event => filterChanged(filters.map((item, i) => i === index ? { ...item, max: event.target.value } : item))} /></>}
                        <button aria-label={`Remove filter ${index + 1}`} className={control} type="button" onClick={() => filterChanged(filters.filter((_, i) => i !== index))}>Remove</button>
                    </div>)}
                    <p className="text-xs text-[var(--text-secondary)]">{matched.length} of {rows.length} loaded records in view · {numericKeys.length} numeric metrics{!adapter?.compact && " · Filters do not change acceptance or selection."}</p>
                </div>
                <div hidden={view !== 'dashboard' && view !== 'analytics'}>{(analyticsOpened || view === 'dashboard' || view === 'analytics') && <Suspense fallback={<p role="status">Loading cohort charts…</p>}><CohortAnalytics getMetricLabel={label} getMetricDescription={key => describeCohortMetric(key, keys).description} renderMetricPicker={props => <CohortMetricPicker {...props} />} initialMetrics={initialMetrics} rows={matched} selectedIds={selectedCohortIds} activeId={activeId} mode={view === 'analytics' ? 'analytics' : 'dashboard'} onInspect={id => { const item = byId.get(id); if (item) inspect(item); }} onSelect={ids => changeSelection(designIds(matched.filter(entry => ids.includes(entry.id))), true)} /></Suspense>}</div>
                {view !== 'structure' && table}
                <div ref={inspector} hidden={view !== 'structure'} className={`${panel} p-4`} aria-label="Candidate structure inspector">
                    {(structureOpened || view === 'structure') && <Suspense fallback={<p role="status">Loading native structure…</p>}>{!row ? <p role="status">{!complete ? 'Loading the requested candidate from the publication. ' : 'Requested candidate is unavailable in this publication. '}Another candidate is not substituted.</p> : <>
                        <div className="flex flex-wrap items-center justify-between gap-3"><div><h4 className="text-lg font-semibold">{adapter?.label(row) ?? row.candidate_key ?? row.design_id ?? 'Native record'}</h4><p className="text-xs text-[var(--text-secondary)]">{adapter ? "Native record and phase observations" : "Exact published structure · native measurements are shown without an inferred confidence score"}</p></div><div className="flex gap-2"><button className={control} type="button" disabled={navigationRows.indexOf(row) <= 0} onClick={() => inspect(navigationRows[navigationRows.indexOf(row) - 1])}>Previous candidate</button><button className={control} type="button" disabled={navigationRows.indexOf(row) >= navigationRows.length - 1} onClick={() => inspect(navigationRows[navigationRows.indexOf(row) + 1])}>Next candidate</button></div></div>
                        {adapter?.inspect?.(row)}
                        {(!adapter || documents.length > 0) && <div className="mt-4 grid min-w-0 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(220px,1fr)]"><div className="min-w-0">
                            <label className="mb-3 block text-xs">Published document <select className={`mt-1 block w-full min-w-0 ${control}`} aria-label="Published document" value={document ? String(documents.indexOf(document)) : ''} onChange={event => { const doc = documents[Number(event.target.value)]; if (doc) inspect(row, doc); }}>
                                {!document && <option value="">Requested document unavailable</option>}{documents.map((doc, i) => <option key={`${doc.artifact_id}:${doc.target_state}:${i}`} value={i}>{doc.logical_path ?? doc.artifact_id} · {doc.target_state ?? 'Native state'}{doc.primary ? ' · primary' : ''}</option>)}
                            </select></label>
                            {document?.download_url ? <StructureWorkbench key={`${document.artifact_id}:${document.target_state}:${document.download_url}`} mode="standard" structureUrl={document.download_url} format={/\.(cif|mmcif)(?:$|\?)/i.test(document.logical_path ?? document.download_url) ? 'cif' : 'pdb'} structureDocumentId={document.artifact_id} structureContentSha256={document.sha256} alphafoldView={false} height={520} showSequenceTrack showMeasurements showM6Workbench hideControls={false} workbenchCollapsed={!viewerToolsOpen} /> : <p role="status">{explicit ? 'Requested native document is unavailable. The Design primary structure is not substituted.' : 'No downloadable native structure was published for this record.'}</p>}
                        </div><aside className="min-w-0"><h5 className="mb-2 text-sm font-semibold">Native measurements</h5><dl className="max-h-[600px] overflow-auto text-sm">{Object.entries(values(row)).map(([key, value]) => <div key={key} className="flex justify-between gap-3 border-b border-[var(--border-color)] py-2" title={[key, nativeGenerationMetricDetail(row, key)].filter(Boolean).join(' · ')}><dt className="break-words text-[var(--text-secondary)]">{label(key)}</dt><dd className="max-w-[55%] break-words text-right tabular-nums">{formatMetric(value)}</dd></div>)}</dl></aside></div>}
                        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">{document?.download_url && <><button className={control} type="button" aria-expanded={viewerToolsOpen} onClick={() => setViewerToolsOpen(value => !value)}>{viewerToolsOpen ? 'Hide viewer tools' : 'Measurements and exports'}</button><a className="underline" href={document.download_url} download>Download exact native document</a></>}{row.design_id && <a className="underline" href={nativeCandidateRoute(jobId, row.design_id, document, launchContextId)}>Open candidate workbench</a>}</div>
                    </>}</Suspense>}
                </div>
                <p className="text-xs text-[var(--text-secondary)]">Model predictions are not experimental validation. {adapter && 'Phase plots show recorded optimization updates, not acceptance scores. '}{!complete && 'Only loaded records are included.'}</p>
            </>}
            {row && <OnDemand title="Selected record: complete native readback"><BindCraft2SettingsReadback value={row.native_record ?? row} /><a href={dataUrl(row.native_record ?? row)} download={`${jobId}-native-record.json`}>Export selected record JSON</a></OnDemand>}
            <OnDemand title="Native receipt and accounting"><BindCraft2SettingsReadback value={page.receipt} /><a href={dataUrl(page.receipt)} download={`${jobId}-native-receipt.json`}>Export receipt JSON</a></OnDemand>
            <OnDemand title="Native files and provenance">{page.artifacts?.map((file, i) => <p key={i}>{file.path} {file.download_url && <a href={file.download_url} download>Download native file</a>}</p>)}<BindCraft2SettingsReadback value={page.publication} /><a href={dataUrl(page.publication)} download={`${jobId}-native-publication.json`}>Export publication JSON</a></OnDemand>
        </>}
    </section>;
}
