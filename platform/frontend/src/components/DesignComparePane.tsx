import { lazy, Suspense, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchDesignById, fetchDesignResidueMetrics } from '../lib/api';
import { parseScientificNativeMetric } from '../lib/scientificViewerIdentity';
import { comparisonChain, comparisonProfiles, type ComparisonProfile } from './nativeComparisonProfiles';
import { useThemeColors } from './useThemeColors';
const Plot = lazy(() => import('react-plotly.js'));

interface Design { id: string; name: string; plddt_overall: number | null }
interface DesignComparePaneProps {
    designs: Design[]; preSelectedId?: string; selectedDesignIds?: string[];
    onSelectedDesignIdsChange?: (ids: string[]) => void;
}

export function DesignComparePane({ designs, preSelectedId, selectedDesignIds, onSelectedDesignIdsChange }: DesignComparePaneProps) {
    const colors = useThemeColors();
    const [localIds, setLocalIds] = useState<string[]>(preSelectedId ? [preSelectedId] : designs.slice(0, 3).map(d => d.id));
    const [chain, setChain] = useState('');
    const selectedIds = (selectedDesignIds ?? localIds).filter(id => designs.some(d => d.id === id));
    const metrics = useQuery({
        queryKey: ['native-comparison-profiles', [...selectedIds].sort()],
        queryFn: () => Promise.all(selectedIds.map(async (id): Promise<ComparisonProfile> => {
            try {
                const [design, raw] = await Promise.all([fetchDesignById(id), fetchDesignResidueMetrics(id)]);
                return { id, name: design.data.name, metric: parseScientificNativeMetric(raw.data,
                    design.data.id === id ? design.data.scientific_structure_document : null, 'residue_plddt', id) };
            } catch { return { id, name: id, metric: { status: 'unavailable', reason: 'Native readback failed' } }; }
        })), enabled: selectedIds.length > 0, staleTime: 300000,
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
        <p className="text-sm">Exact published chain, residue, atom, model and alternate-location labels define correspondence. No structural alignment or difference score is inferred. Each atom remains a separate measurement; native fractions are displayed on 0–100. Different confidence scopes are not pooled.</p>
        <div className="flex flex-wrap gap-3">{designs.map(d => <label key={d.id} title={d.id} className="text-sm">
            <input type="checkbox" checked={selectedIds.includes(d.id)} onChange={() => toggle(d.id)} /> {d.name} ({d.id})
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
        <a className="underline" download="native-confidence-comparison.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify({ correspondence: 'exact published identity labels; no alignment', profiles, chain }, null, 2))}`}>Export native values and identities</a>
        <div className="overflow-auto"><table className="w-full text-sm text-left"><thead><tr><th>Design / document</th><th>Native scope / units</th><th>Displayed points</th><th>Shared labels / unmatched</th><th>Availability</th></tr></thead>
            <tbody>{selectedIds.map(id => {
                const row = chart.rows.find(p => p.id === id);
                return <tr key={id}><td className="p-2">{designs.find(d => d.id === id)?.name} ({id})<br />{row?.metric.status === 'ok' ? row.metric.document.documentId : '—'}</td>
                    <td>{row?.metric.status === 'ok' ? `${row.metric.confidenceSource?.scope ?? row.metric.metric} / fraction` : 'Unavailable'}</td>
                    <td>{row?.metric.status === 'ok' ? row.count : '—'}</td><td>{row?.metric.status === 'ok' ? `${row.matched} / ${row.count - row.matched}` : '—'}</td>
                    <td>{row?.metric.status === 'ok' ? 'Native values' : row?.metric.reason ?? 'Not loaded'}</td></tr>;
            })}</tbody></table></div>
    </section>;
}
