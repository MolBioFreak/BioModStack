import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import { fetchFrustraMpnnMultidimensionalPoints } from '../lib/frustraMpnnApi.js';
import { buildFrustraMpnn3dModel } from './frustraMpnnMultidimensionalModel.js';
import { useThemePlotlyLayout } from './useThemeColors.js';

interface Props {
    currentDatasetId: string;
}

const FALLBACK_METRICS = ['mean_score', 'high_fraction', 'minimal_fraction', 'scoreable_fraction'];
const PLOT_CONFIG = { responsive: true, displaylogo: false, scrollZoom: true, toImageButtonOptions: { format: 'png' as const, filename: 'frustrampnn_cross_dataset' } };

export default function FrustraMpnnCrossDatasetExplorer({ currentDatasetId }: Props) {
    const baseLayout = useThemePlotlyLayout();
    const [scope, setScope] = useState<'all' | 'current'>('all');
    const [xMetric, setXMetric] = useState(FALLBACK_METRICS[0]);
    const [yMetric, setYMetric] = useState(FALLBACK_METRICS[1]);
    const [zMetric, setZMetric] = useState(FALLBACK_METRICS[2]);
    const [colorMetric, setColorMetric] = useState(FALLBACK_METRICS[3]);
    const query = useQuery({
        queryKey: ['frustrampnn', 'multidimensional', scope, currentDatasetId],
        queryFn: ({ signal }) => fetchFrustraMpnnMultidimensionalPoints(scope === 'current' ? [currentDatasetId] : [], 1000, signal),
        staleTime: 30_000,
    });
    const page = query.data;
    const numericDimensions = useMemo(() => page?.dimensions.filter((dimension) => ['number', 'fraction'].includes(dimension.kind)) ?? [], [page]);
    const metricLabel = (id: string) => id.replaceAll('_', ' ');
    const model = useMemo(() => page ? buildFrustraMpnn3dModel(page, xMetric, yMetric, zMetric, colorMetric) : null, [page, xMetric, yMetric, zMetric, colorMetric]);
    const plot3d: Data[] = model ? [{
        type: 'scatter3d', mode: 'markers', x: model.x, y: model.y, z: model.z,
        customdata: model.hover, text: model.pointIds,
        marker: { size: 5, opacity: 0.82, color: model.color, colorscale: 'Viridis', showscale: true, colorbar: { title: { text: metricLabel(colorMetric) } } },
        hovertemplate: '%{customdata}<br>Point %{text}<br>X %{x:.4f}<br>Y %{y:.4f}<br>Z %{z:.4f}<extra></extra>',
        name: 'FrustraMPNN results',
    }] : [];
    const splomMetrics = [xMetric, yMetric, zMetric, colorMetric].filter((value, index, values) => values.indexOf(value) === index);
    const splom: Data[] = page ? [{
        type: 'splom',
        dimensions: splomMetrics.map((metric) => ({ label: metricLabel(metric), values: page.items.map((item) => item.metrics[metric] as number | null) })),
        text: page.items.map((item) => `${item.dataset_id} · ${item.design_id ?? 'unlinked'} · ${item.candidate_id}`),
        marker: { color: page.items.map((item) => item.metrics[colorMetric] as number | null), colorscale: 'Viridis', size: 5, opacity: 0.7, showscale: true },
        hovertemplate: '%{text}<extra></extra>',
        diagonal: { visible: false },
        showupperhalf: false,
    } as Data] : [];

    const downloadJson = () => {
        if (!page) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(page, null, 2)], { type: 'application/json' }));
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `frustrampnn-${scope}-multidimensional-v1.json`;
        anchor.click();
        URL.revokeObjectURL(url);
    };

    return (
        <section aria-label="Cross-dataset FrustraMPNN explorer" className="overflow-hidden rounded-xl border border-indigo-500/30 bg-indigo-950/10">
            <div className="border-b border-indigo-500/20 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h2 className="font-semibold">Cross-dataset multidimensional explorer</h2>
                        <p className="mt-1 max-w-5xl text-xs text-slate-400">Purpose: compare hundreds of canonical FrustraMPNN results without one API request per result. One bounded server response supplies stable workflow, dataset, job, design, invocation, provenance, and metric dimensions. The 3D axes plus color encode four independently selectable dimensions; every point remains traceable on hover and in the JSON export.</p>
                    </div>
                    <button type="button" onClick={downloadJson} disabled={!page} className="rounded border border-indigo-400/40 px-3 py-1.5 text-xs text-indigo-100 disabled:opacity-40">Export machine-readable JSON</button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <button type="button" onClick={() => setScope('all')} className={`rounded px-3 py-1.5 ${scope === 'all' ? 'bg-indigo-500 text-white' : 'border border-slate-700 text-slate-300'}`}>All persisted datasets</button>
                    <button type="button" onClick={() => setScope('current')} className={`rounded px-3 py-1.5 ${scope === 'current' ? 'bg-indigo-500 text-white' : 'border border-slate-700 text-slate-300'}`}>Current dataset only</button>
                    {page && <span className="self-center font-mono text-slate-500">{page.items.length.toLocaleString()} of {page.total.toLocaleString()} result points · schema {page.schema_version}</span>}
                </div>
            </div>
            {query.isLoading && <div role="status" className="p-4 text-sm text-slate-400">Loading bounded cross-dataset analytics…</div>}
            {query.isError && <div role="alert" className="p-4 text-sm text-red-300">Cross-dataset analytics unavailable: {query.error instanceof Error ? query.error.message : 'request failed'}</div>}
            {page && <div className="p-3">
                {page.next_offset != null && <div role="alert" className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-100">Showing the first {page.items.length.toLocaleString()} of {page.total.toLocaleString()} ordered results. Filter to explicit datasets before drawing conclusions from omitted points.</div>}
                <div className="grid gap-2 md:grid-cols-4">
                    {([['X', xMetric, setXMetric], ['Y', yMetric, setYMetric], ['Z', zMetric, setZMetric], ['Color', colorMetric, setColorMetric]] as const).map(([label, value, setter]) => <label key={label} className="text-xs text-slate-400">{label} dimension
                        <select value={value} onChange={(event) => setter(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">
                            {numericDimensions.map((dimension) => <option key={dimension.id} value={dimension.id}>{metricLabel(dimension.id)}</option>)}
                        </select>
                    </label>)}
                </div>
                <article className="mt-3 rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Traceable 3D result space</h3>
                    <p className="px-2 text-xs text-slate-500">Question: which results occupy similar or exceptional regions across four selected FrustraMPNN dimensions? This is a point-estimate comparison; no uncertainty is inferred.</p>
                    <Plot data={plot3d} layout={{ ...baseLayout, height: 560, margin: { l: 0, r: 0, b: 0, t: 35 }, scene: { xaxis: { title: { text: metricLabel(xMetric) } }, yaxis: { title: { text: metricLabel(yMetric) } }, zaxis: { title: { text: metricLabel(zMetric) } } }, title: { text: `${model?.pointIds.length ?? 0} canonical result points` } } as Partial<Layout>} config={PLOT_CONFIG} className="h-[560px] w-full" useResizeHandler />
                </article>
                {splomMetrics.length >= 2 && <article className="mt-3 rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Pairwise multidimensional structure</h3>
                    <p className="px-2 text-xs text-slate-500">Question: which selected dimensions covary, separate workflow datasets, or expose outliers? Lower-triangle panels show the same traceable result points without projecting them into a single 3D camera angle.</p>
                    <Plot data={splom} layout={{ ...baseLayout, height: 620, margin: { l: 70, r: 45, b: 70, t: 30 }, dragmode: 'select' }} config={PLOT_CONFIG} className="h-[620px] w-full" useResizeHandler />
                </article>}
                <details className="mt-3 rounded border border-slate-800 p-3 text-xs text-slate-400"><summary className="cursor-pointer font-medium text-slate-300">Machine-readable dimension formulas</summary><dl className="mt-2 grid gap-2 md:grid-cols-2">{numericDimensions.map((dimension) => <div key={dimension.id}><dt className="font-mono text-slate-300">{dimension.id}</dt><dd>{dimension.formula ?? dimension.description ?? 'Persisted dimension'}{dimension.unit ? ` · ${dimension.unit}` : ''}</dd></div>)}</dl></details>
            </div>}
        </section>
    );
}
