import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { MetricScatter } from './MetricCharts';
import { StructureWorkbench } from '../structureViewer/StructureWorkbench';
import { fetchNativeBinderGeneration, nativeCandidateRoute, type NativeGenerationRecord, type NativeGenerationDocument } from '../lib/nativeBinderResults';

interface Props {
    jobId: string; status?: string; launchContextId?: string | null;
    selectedDesignId?: string; selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
    onInspectDocument?: (row: NativeGenerationRecord, document?: NativeGenerationDocument) => void;
    artifactId?: string | null; targetState?: string | null;
}
function OnDemand({ title, children }: { title: string; children: ReactNode }) {
    const [open, setOpen] = useState(false);
    return <details className="rounded-lg border border-[var(--border-color)] p-3" onToggle={event => setOpen(event.currentTarget.open)}><summary className="cursor-pointer text-sm font-medium">{title}</summary>{open && <div className="max-h-96 overflow-auto py-2">{children}</div>}</details>;
}
const metrics = (row: NativeGenerationRecord): Record<string, unknown> => row.metrics && typeof row.metrics === 'object' && !Array.isArray(row.metrics) ? row.metrics as Record<string, unknown> : {};
const valueText = (value: unknown) => value === undefined ? 'Not reported' : value === null ? 'Explicit null' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const dataUrl = (value: unknown) => `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(value, null, 2))}`;
const rowKey = (row: NativeGenerationRecord, index: number) => row.candidate_key ?? row.design_id ?? String(index);
const metricLabels: Record<string, string> = { seq_length: 'Length', dsasa: 'dSASA', num_ca_ca_clashes: 'CA clashes' };
const metricLabel = (key: string) => metricLabels[key] ?? key;
const cellText = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : valueText(value);

/** Publication is the authority, including historical rows without a Design join. */
export function NativeBinderGenerationResults(props: Props) {
    // Remount local pagination/inspection when the source job changes.
    return <NativeGenerationWorkbench key={props.jobId} {...props} />;
}
function NativeGenerationWorkbench({ jobId, status, launchContextId, selectedDesignId, selectedDesignIds = [], onSelectedDesignIdsChange, onInspectDocument, artifactId, targetState }: Props) {
    const [offset, setOffset] = useState(0);
    const [local, setLocal] = useState<{ scope: string; key: string; document?: NativeGenerationDocument }>();
    const [tab, setTab] = useState<'structure' | 'metrics'>('structure');
    const [xChoice, setXChoice] = useState('');
    const [yChoice, setYChoice] = useState('');
    const [allColumns, setAllColumns] = useState(false);
    const [viewerToolsOpen, setViewerToolsOpen] = useState(false);
    const scope = JSON.stringify([selectedDesignId, artifactId, targetState, offset]);
    const query = useQuery({ queryKey: ['native-binder-generation', jobId, offset],
        queryFn: () => fetchNativeBinderGeneration(jobId, offset), retry: false,
        refetchInterval: status === 'queued' || status === 'running' ? 5000 : false });
    const page = query.data;
    const rows = page?.records ?? [];
    const localSelection = local?.scope === scope ? local : undefined;
    const row = localSelection ? rows.find((item, i) => rowKey(item, i) === localSelection.key)
        : selectedDesignId ? rows.find(item => item.design_id === selectedDesignId) : rows[0];
    const documents = row?.structures ?? [];
    const explicit = localSelection?.document != null || (!localSelection && (artifactId != null || targetState != null));
    const requestedArtifact = localSelection?.document?.artifact_id ?? artifactId;
    const requestedState = localSelection?.document ? localSelection.document.target_state : targetState;
    const document = explicit
        ? documents.find(doc => (requestedArtifact == null || doc.artifact_id === requestedArtifact) && (requestedState == null || doc.target_state === requestedState))
        : documents.find(doc => doc.primary) ?? documents[0];
    const keys = Array.from(new Set(rows.flatMap(item => Object.keys(metrics(item)))));
    const numericKeys = keys.filter(key => rows.some(item => typeof metrics(item)[key] === 'number' && Number.isFinite(metrics(item)[key])));
    const preferredKeys = ['seq_length', 'dsasa', 'num_ca_ca_clashes'].filter(key => keys.includes(key));
    const tableKeys = allColumns ? keys : preferredKeys.length ? preferredKeys : keys.slice(0, 3);
    const xKey = numericKeys.includes(xChoice) ? xChoice : numericKeys.includes('seq_length') ? 'seq_length' : numericKeys[0];
    const yKey = numericKeys.includes(yChoice) ? yChoice : numericKeys.includes('dsasa') ? 'dsasa' : numericKeys[1] ?? numericKeys[0];
    const points = rows.flatMap((item, i) => {
        const x = metrics(item)[xKey], y = metrics(item)[yKey];
        return typeof x === 'number' && Number.isFinite(x) && typeof y === 'number' && Number.isFinite(y) ? [{ x, y, id: rowKey(item, i) }] : [];
    });
    const inspect = (item: NativeGenerationRecord, doc?: NativeGenerationDocument) => {
        if (onInspectDocument && item.design_id) { setLocal(undefined); onInspectDocument(item, doc); }
        else setLocal({ scope, key: rowKey(item, rows.indexOf(item)), document: doc });
        setTab('structure');
    };
    const pageIds = rows.flatMap(item => item.design_id ? [item.design_id] : []);
    const allPageSelected = pageIds.length > 0 && pageIds.every(id => selectedDesignIds.includes(id));
    const selectPage = (checked: boolean) => onSelectedDesignIdsChange?.(checked ? Array.from(new Set([...selectedDesignIds, ...pageIds])) : selectedDesignIds.filter(id => !pageIds.includes(id)));
    const csv = [['candidate_key', 'design_id', ...keys], ...rows.map(item => [item.candidate_key, item.design_id, ...keys.map(key => valueText(metrics(item)[key]))])]
        .map(cells => cells.map(cell => `"${String(cell ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
    return <section aria-label="Native initial-generation results" className="space-y-3 rounded-lg border border-[var(--border-color)] p-4">
        <h3 className="font-semibold">Native initial-generation results</h3>
        {query.isLoading && <p role="status">Reading published generation records…</p>}
        {query.isError && <p role="status">Native publication is not available: {String(query.error)} <button type="button" onClick={() => void query.refetch()}>Retry native readback</button></p>}
        {page && <>
            <p className="text-sm text-[var(--text-secondary)]">{page.total} native records. These are native model outputs, not experimentally validated binders.</p>
            {page.total === 0 && <p>No native candidate records were emitted. This published zero-yield result remains available for review.</p>}
            {rows.length > 0 && <div className="grid gap-4 lg:grid-cols-[minmax(280px,2fr)_minmax(0,3fr)]">
                <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap justify-between gap-2 text-sm">
                        {onSelectedDesignIdsChange && <label className="flex items-center gap-2"><input aria-label="Select this page" type="checkbox" checked={allPageSelected} disabled={!pageIds.length} onChange={event => selectPage(event.target.checked)} /> Select this page</label>}
                        <label className="flex items-center gap-2"><input aria-label="All native metric columns" type="checkbox" checked={allColumns} onChange={event => setAllColumns(event.target.checked)} /> All metric columns</label>
                    </div>
                    <div className="max-h-64 lg:max-h-[480px] overflow-auto rounded border border-[var(--border-color)]">
                        <table className="w-full text-sm"><thead className="sticky top-0 bg-[var(--bg-primary)] text-left"><tr><th className="p-2">Candidate</th>{tableKeys.map(key => <th className="px-2 py-3 text-right" title={key} key={key}>{metricLabel(key)}</th>)}</tr></thead>
                            <tbody>{rows.map((item, i) => <tr key={rowKey(item, i)} aria-selected={item === row} className={`border-t border-[var(--border-color)] ${item === row ? 'bg-blue-500/15' : 'hover:bg-blue-500/5'}`}>
                                <td className="p-2"><div className="flex items-center gap-2">
                                    {onSelectedDesignIdsChange && item.design_id && <input aria-label={`Select ${item.candidate_key ?? item.design_id}`} type="checkbox" checked={selectedDesignIds.includes(item.design_id)} onChange={event => onSelectedDesignIdsChange(event.target.checked ? Array.from(new Set([...selectedDesignIds, item.design_id!])) : selectedDesignIds.filter(id => id !== item.design_id))} />}
                                    <button type="button" className="whitespace-nowrap py-2 text-left font-medium hover:underline" onClick={() => inspect(item)}>{item.candidate_key ?? item.design_id ?? `Native record ${page.offset + i + 1}`}</button>
                                </div>{!item.design_id && <small>No Design join</small>}</td>
                                {tableKeys.map(key => <td className="whitespace-nowrap px-2 text-right tabular-nums" title={valueText(metrics(item)[key])} key={key}>{cellText(metrics(item)[key])}</td>)}
                            </tr>)}</tbody></table>
                    </div>
                    <p className="text-xs">Publication order · {selectedDesignIds.length} selected across pages. Inspection does not bulk-select.</p>
                    <div className="flex flex-wrap gap-3 text-xs underline"><a href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`} download={`${jobId}-native-page-${page.offset}.csv`}>Export page metrics CSV</a><a href={dataUrl(page.records)} download={`${jobId}-native-page-${page.offset}.json`}>Export page records JSON</a></div>
                </div>
                <div className="min-w-0 space-y-2">
                    <div role="tablist" aria-label="Native result review" className="flex gap-2 border-b border-[var(--border-color)] pb-2">{(['structure', 'metrics'] as const).map(value => <button key={value} type="button" role="tab" aria-selected={tab === value} className={`rounded-lg px-4 py-2 text-sm font-medium ${tab === value ? 'bg-blue-500/20' : 'hover:bg-blue-500/10'}`} onClick={() => setTab(value)}>{value === 'structure' ? 'Structure' : 'Native metrics'}</button>)}</div>
                    <div role="tabpanel" hidden={tab !== 'structure'}>
                        {!row ? <p role="status">Requested candidate is not on this publication page. Use pagination to locate it; another candidate is not substituted.</p> : <>
                            <h4 className="font-semibold">{row.candidate_key ?? row.design_id ?? 'Native record'}</h4>
                            <label className="my-2 block text-xs">Published document <select className="mt-1 block w-full min-w-0 rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2 text-sm" aria-label="Published document" value={document ? String(documents.indexOf(document)) : ''} onChange={event => { const doc = documents[Number(event.target.value)]; if (doc) inspect(row, doc); }}>
                                {!document && <option value="">Requested document unavailable</option>}
                                {documents.map((doc, i) => <option key={`${doc.artifact_id}:${doc.target_state}:${i}`} value={i}>{doc.logical_path ?? doc.artifact_id} · {doc.target_state ?? 'Native state'}{doc.primary ? ' · primary' : ''}</option>)}
                            </select></label>
                            {document?.download_url ? <StructureWorkbench key={`${document.artifact_id}:${document.target_state}:${document.download_url}`} mode="standard" structureUrl={document.download_url}
                                format={/\.(cif|mmcif)(?:$|\?)/i.test(document.logical_path ?? document.download_url) ? 'cif' : 'pdb'}
                                structureDocumentId={document.artifact_id} structureContentSha256={document.sha256}
                                alphafoldView={false} height={440} showSequenceTrack showMeasurements showM6Workbench hideControls={false} workbenchCollapsed={!viewerToolsOpen} />
                                : <p role="status">{explicit ? 'Requested native document is unavailable. The Design primary structure is not substituted.' : 'No downloadable native structure was published for this record.'}</p>}
                            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                                {document?.download_url && <><button className="rounded border border-[var(--border-color)] px-3 py-2" type="button" aria-expanded={viewerToolsOpen} onClick={() => setViewerToolsOpen(value => !value)}>{viewerToolsOpen ? 'Hide viewer tools' : 'Measurements and exports'}</button><a className="underline" href={document.download_url} download>Download exact native document</a></>}
                                {row.design_id && <a className="underline" href={nativeCandidateRoute(jobId, row.design_id, document, launchContextId)}>Open candidate workbench</a>}
                            </div>
                        </>}
                    </div>{tab === 'metrics' && <div role="tabpanel">
                        <p className="text-sm">Native numeric observations on this page only. No confidence score or binding verdict is inferred. Null, missing, boolean and nonnumeric values are not plotted.</p>
                        {numericKeys.length ? <><label>X metric <select value={xKey} onChange={event => setXChoice(event.target.value)}>{numericKeys.map(key => <option key={key}>{key}</option>)}</select></label> <label>Y metric <select value={yKey} onChange={event => setYChoice(event.target.value)}>{numericKeys.map(key => <option key={key}>{key}</option>)}</select></label>
                            <MetricScatter data={points} xLabel={xKey} yLabel={yKey} title="Native candidate observations" height={300} onPointSelect={id => { const item = rows.find((candidate, i) => rowKey(candidate, i) === id); if (item) inspect(item); }} />
                            <p>{points.length} plotted; {rows.length - points.length} omitted for unavailable numeric pairs.</p>
                        </> : <p>No finite numeric observations were published on this page.</p>}
                    </div>}
                </div>
            </div>}
            <nav aria-label="Native result pages" className="flex gap-3"><button type="button" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - page.limit))}>Previous native records</button><span>{page.offset + (rows.length ? 1 : 0)}–{page.offset + rows.length} of {page.total}</span><button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => setOffset(page.offset + page.limit)}>Next native records</button></nav>
            {row && <OnDemand title="Selected record: complete native readback"><BindCraft2SettingsReadback value={row} /><a href={dataUrl(row)} download={`${jobId}-native-record.json`}>Export selected record JSON</a></OnDemand>}
            <OnDemand title="Native receipt and accounting"><BindCraft2SettingsReadback value={page.receipt} /><a href={dataUrl(page.receipt)} download={`${jobId}-native-receipt.json`}>Export receipt JSON</a></OnDemand>
            <OnDemand title="Native files and provenance">
                {page.artifacts?.map((file, i) => <p key={i}>{file.path} {file.download_url && <a href={file.download_url} download>Download native file</a>}</p>)}
                <BindCraft2SettingsReadback value={page.publication} /><a href={dataUrl(page.publication)} download={`${jobId}-native-publication.json`}>Export publication JSON</a>
            </OnDemand>
        </>}
    </section>;
}
