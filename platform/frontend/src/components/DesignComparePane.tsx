import { lazy, Suspense, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchDesignById, fetchDesignResidueMetrics, type NativeStructureDocumentResponse } from '../lib/api';
import { fetchNativeComparison } from '../lib/nativeBinderResults';
import type { CandidateDocuments } from '../lib/binderDiagnosticSelection';
import type { NativeCandidateSource } from '../lib/binderContinuation';
import { parseScientificNativeMetric, type ScientificNativeMetric } from '../lib/scientificViewerIdentity';
import { comparisonChain, comparisonProfiles, type ComparisonProfile } from './nativeComparisonProfiles';
import { useThemeColors } from './useThemeColors';
const Plot = lazy(() => import('react-plotly.js'));

interface Design { id: string; name: string; plddt_overall: number | null }
/** Model-qualified display subset, using native labels, never array-offset correspondence. */
function selectedModel(metric: ScientificNativeMetric, model?: number): ScientificNativeMetric {
    if (metric.status !== 'ok' || model == null) return metric;
    const indices = metric.residues.flatMap((residue, index) => residue.modelId === String(model) ? [index] : []);
    if (!indices.length) return { status: 'unavailable', reason: `Native confidence unavailable for MODEL ${model}` };
    return { ...metric, residues: indices.map(index => metric.residues[index]), values: indices.map(index => metric.values[index]) };
}
interface DesignComparePaneProps {
    designs: Design[]; preSelectedId?: string; selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
    candidateDocuments?: Record<string, CandidateDocuments[string] & { model?: number }>;
    selectedNativeSources?: NativeCandidateSource[];
}

export function DesignComparePane({ designs, preSelectedId, selectedDesignIds, onSelectedDesignIdsChange, candidateDocuments = {}, selectedNativeSources = [] }: DesignComparePaneProps) {
    const colors = useThemeColors();
    const label = (id: string, fallback = '') => { const i = designs.findIndex(item => item.id === id); const name = (i >= 0 ? designs[i].name : fallback) || ''; return name && name.length <= 48 && !/[a-f0-9]{12}/i.test(name) ? name : `Candidate ${i >= 0 ? i + 1 : selectedIds.indexOf(id) + 1}`; };
    const [localIds, setLocalIds] = useState<string[]>(preSelectedId ? [preSelectedId] : designs.slice(0, 3).map(d => d.id));
    const [chain, setChain] = useState('');
    const selectedIds = selectedDesignIds ?? localIds;
    const metrics = useQuery({
        queryKey: ['native-comparison-profiles', [...selectedIds].sort().map(id => [id, candidateDocuments[id]]),
            selectedNativeSources.map(source => [source.job_id, source.artifact_id, source.document?.target_state, source.document?.model])],
        queryFn: () => Promise.all([
            ...selectedIds.map(async (id): Promise<ComparisonProfile> => {
                try {
                    const selected = candidateDocuments[id];
                    const selector = selected ? { ...selected, ...(selected.model != null ? { model_number: selected.model } : {}) } : undefined;
                    const [design, raw] = await Promise.all([fetchDesignById(id, undefined, selector), fetchDesignResidueMetrics(id, selector)]);
                    return { id, name: label(id, design.data.name), metric: selectedModel(parseScientificNativeMetric(raw.data,
                        design.data.id === id ? design.data.scientific_structure_document : null, 'residue_plddt', id), selected?.model) };
                } catch { return { id, name: label(id), metric: { status: 'unavailable', reason: 'Native readback failed' } }; }
            }),
            ...selectedNativeSources.map(async (source, index): Promise<ComparisonProfile> => {
                // Native identity stays artifact-scoped; it is not an ORM Design ID.
                const candidateId = `native:${source.job_id}:${source.artifact_id}`;
                const id = `${candidateId}:${JSON.stringify([source.document?.target_state, source.document?.model])}`, name = `Native candidate ${index + 1}`;
                try {
                    const [metadata, raw] = await Promise.all([
                        fetchNativeComparison<NativeStructureDocumentResponse>(source, 'document'),
                        fetchNativeComparison<unknown>(source, 'residue-metrics'),
                    ]);
                    return { id, name, metric: selectedModel(parseScientificNativeMetric(raw.data, metadata.data.document, 'residue_plddt', candidateId, source), source.document?.model) };
                } catch { return { id, name, metric: { status: 'unavailable', reason: 'Native readback failed' } }; }
            }),
        ]), enabled: selectedIds.length + selectedNativeSources.length > 0, staleTime: 300000,
    });
    const profiles = metrics.data ?? [];
    const chains = [...new Set(profiles.flatMap(p => p.metric.status === 'ok' ? p.metric.residues.map(comparisonChain) : []))];
    const chart = useMemo(() => comparisonProfiles(metrics.data ?? [], chain), [metrics.data, chain]);
    const ticks = chart.categories.filter((_, i) => i % Math.max(1, Math.ceil(chart.categories.length / 6)) === 0);
    const toggle = (id: string) => {
        const next = selectedIds.includes(id) ? selectedIds.filter(x => x !== id) : [...selectedIds, id];
        setLocalIds(next); onSelectedDesignIdsChange?.(next);
    };
    return <section className="flex flex-col gap-4 p-4 text-[var(--text-primary)]" aria-label="Native confidence comparison">
        <h2 className="text-lg font-semibold">Confidence overlay</h2>
        <details className="text-xs text-[var(--text-secondary)]"><summary>Profile correspondence</summary><p>Exact published chain, residue, atom, model and alternate-location labels define correspondence, not structural alignment. Native fractional confidence is displayed on 0–100; different scopes stay separate.</p></details>
        <div className="flex flex-wrap gap-3">{designs.map(d => <label key={d.id} title={d.id} className="text-sm">
            <input type="checkbox" checked={selectedIds.includes(d.id)} onChange={() => toggle(d.id)} /> {label(d.id)}
        </label>)}</div>
        <label>Chain <select aria-label="Comparison chain" value={chain} onChange={e => setChain(e.target.value)} className="bg-[var(--bg-secondary)] p-2">
            <option value="">All chains</option>{chains.map(id => <option key={id}>{id}</option>)}
        </select></label>
        {metrics.isLoading ? <p role="status">Loading native profiles…</p> : chart.traces.length ? <Suspense fallback={<p>Loading chart…</p>}><Plot
            data={chart.traces.map(t => ({ type: 'scatter', mode: 'lines', x: t.x, y: t.y, text: t.text, name: t.name,
                line: { shape: 'linear', width: 1.5 }, connectgaps: false,
                hovertemplate: '%{text}<br>pLDDT %{y:.2f} / 100<extra>%{fullData.name}</extra>' }))}
            layout={{ autosize: true, height: 450, paper_bgcolor: 'transparent', plot_bgcolor: colors.bgSecondary,
                font: { color: colors.textPrimary }, margin: { l: 55, r: 20, t: 90, b: 80 },
                xaxis: { type: 'category', categoryorder: 'array', categoryarray: chart.categories.map(([key]) => key),
                    tickmode: 'array', tickvals: ticks.map(([key]) => key), ticktext: ticks.map(([, label]) => label.split(' · ')[0]),
                    title: { text: 'Exact native identity labels (not structural alignment)' }, tickangle: 0, automargin: true },
                yaxis: { range: [0, 100], dtick: 20, title: { text: 'pLDDT (0–100; native fraction)' } },
                legend: { orientation: 'h', y: 1.05 }, hovermode: 'closest', dragmode: 'zoom' }}
            config={{ responsive: true, displaylogo: false, toImageButtonOptions: { format: 'svg', filename: 'native-confidence-comparison' } }}
            useResizeHandler style={{ width: '100%', minWidth: 0 }} /> </Suspense> : <p>Native confidence unavailable for this selection. Retained rows remain below.</p>}
        <a className="underline" download="native-confidence-comparison.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify({ schema: 'bms.native-confidence-comparison.v1', correspondence: 'exact published identity labels; no alignment', selection: { design_ids: selectedIds, documents: candidateDocuments, native_sources: selectedNativeSources }, read_status: metrics.isFetching ? 'loading' : metrics.isError ? 'failed' : 'loaded', profiles, chain }, null, 2))}`}>Export native values and identities</a>
        <details><summary className="text-sm">Profile sources</summary><div className="overflow-auto"><table className="w-full text-sm text-left"><thead><tr><th>Candidate / document</th><th>Native scope / units</th><th>Displayed points</th><th>Shared labels / unmatched</th><th>Availability</th></tr></thead>
            <tbody>{profiles.map(({ id }) => {
                const row = chart.rows.find(p => p.id === id);
                return <tr key={id}><td className="p-2">{designs.find(d => d.id === id)?.name ?? profiles.find(p => p.id === id)?.name} ({id})<br />{row?.metric.status === 'ok' ? row.metric.document.documentId : '—'}</td>
                    <td>{row?.metric.status === 'ok' ? `${row.metric.confidenceSource?.scope ?? row.metric.metric} / fraction` : 'Unavailable'}</td>
                    <td>{row?.metric.status === 'ok' ? row.count : '—'}</td><td>{row?.metric.status === 'ok' ? `${row.matched} / ${row.count - row.matched}` : '—'}</td>
                    <td>{row?.metric.status === 'ok' ? 'Native values' : row?.metric.reason ?? 'Not loaded'}</td></tr>;
            })}</tbody></table></div></details>
    </section>;
}
