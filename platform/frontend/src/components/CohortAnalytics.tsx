import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import Plot from 'react-plotly.js';
import type { Config, Data, Layout, PlotMouseEvent, PlotSelectionEvent } from 'plotly.js';
import { correlateMetrics, formatMetric, isFiniteMetric, metricKeys, metricLabel, numericMetricKeys, secondaryStructureComposition, summarizeMetric, type CohortRow } from '../lib/cohortAnalytics';

interface Props {
    rows: CohortRow[];
    selectedIds: string[];
    activeId?: string;
    onInspect: (id: string) => void;
    onSelect: (ids: string[]) => void;
    mode: 'dashboard' | 'analytics';
}
const CONFIG: Partial<Config> = {
    responsive: true, displayModeBar: true, displaylogo: false,
    toImageButtonOptions: { format: 'svg', filename: 'native-cohort', width: 1000, height: 650, scale: 1 },
};
const control = 'min-w-0 max-w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2 text-sm text-[var(--text-primary)]';
const card = 'min-w-0 rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)] p-3';
const caption = (key: string) => `${metricLabel(key)} (${key})`;
const escape = (text: string) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

function usePlotTheme() {
    const [theme, setTheme] = useState({ text: '#94a3b8', grid: '#64748b', background: 'transparent' });
    useEffect(() => {
        const update = () => {
            const style = getComputedStyle(document.documentElement);
            setTheme({ text: style.getPropertyValue('--text-primary').trim() || '#94a3b8',
                grid: style.getPropertyValue('--border-color').trim() || '#64748b',
                background: style.getPropertyValue('--bg-secondary').trim() || 'transparent' });
        };
        update();
        const observer = new MutationObserver(update);
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
        observer.observe(document.body, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
        const media = window.matchMedia('(prefers-color-scheme: dark)');
        media.addEventListener('change', update);
        return () => { observer.disconnect(); media.removeEventListener('change', update); };
    }, []);
    return theme;
}

/** Observe the actual panel, including side-panel and tab layout changes, not just window resizes. */
function CohortPlot({ label, data, layout, onClick, onSelected, height = 350 }: {
    label: string; data: Data[]; layout: Partial<Layout>; height?: number;
    onClick?: (event: PlotMouseEvent) => void; onSelected?: (event: PlotSelectionEvent) => void;
}) {
    const host = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState<number>();
    useEffect(() => {
        const element = host.current;
        if (!element) return;
        const resize = () => { if (element.clientWidth > 0) setWidth(element.clientWidth); };
        resize();
        const observer = new ResizeObserver(resize);
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    return <div ref={host} role="region" aria-label={label} className="min-w-0 overflow-hidden">
        <Plot data={data} layout={{ ...layout, autosize: true, width, height }} config={CONFIG}
            useResizeHandler style={{ width: '100%', height }} onClick={onClick} onSelected={onSelected} />
    </div>;
}

export function CohortAnalytics({ rows, selectedIds, activeId, onInspect, onSelect, mode }: Props): JSX.Element {
    const numeric = useMemo(() => numericMetricKeys(rows), [rows]);
    const allKeys = useMemo(() => metricKeys(rows), [rows]);
    const [xChoice, setXChoice] = useState('seq_length');
    const [yChoice, setYChoice] = useState('dsasa');
    const [distributionChoice, setDistributionChoice] = useState('dsasa');
    const [colorChoice, setColorChoice] = useState('');
    const [dragMode, setDragMode] = useState<'zoom' | 'select' | 'lasso'>('zoom');
    const [heatChoice, setHeatChoice] = useState<string[] | null>(null);
    const [metricSearch, setMetricSearch] = useState('');
    const [metricPage, setMetricPage] = useState(0);
    const [zChoice, setZChoice] = useState('radius_of_gyration');
    const [color3dChoice, setColor3dChoice] = useState('');
    const [barChoice, setBarChoice] = useState('dsasa');
    const theme = usePlotTheme();
    const xKey = numeric.includes(xChoice) ? xChoice : numeric.includes('seq_length') ? 'seq_length' : numeric[0] ?? '';
    const yKey = numeric.includes(yChoice) ? yChoice : numeric.includes('dsasa') ? 'dsasa' : numeric[1] ?? numeric[0] ?? '';
    const distKey = numeric.includes(distributionChoice) ? distributionChoice : yKey;
    const zKey = numeric.includes(zChoice) ? zChoice : numeric.find(key => key !== xKey && key !== yKey) ?? '';
    const barKey = numeric.includes(barChoice) ? barChoice : yKey;
    const color3dKey = numeric.includes(color3dChoice) ? color3dChoice : '';
    const colorKey = mode === 'analytics' && numeric.includes(colorChoice) ? colorChoice : '';
    const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
    const pairs = useMemo(() => rows.filter(row => isFiniteMetric(row.values[xKey]) && isFiniteMetric(row.values[yKey])), [rows, xKey, yKey]);
    const distribution = useMemo(() => rows.map(row => row.values[distKey]).filter(isFiniteMetric), [rows, distKey]);
    const distributionSummary = useMemo(() => summarizeMetric(rows, distKey), [rows, distKey]);
    const barRows = useMemo(() => rows.filter(row => isFiniteMetric(row.values[barKey]))
        .sort((a, b) => (b.values[barKey] as number) - (a.values[barKey] as number)).slice(0, 20), [rows, barKey]);
    const active = rows.find(row => row.id === activeId);
    const composition = secondaryStructureComposition(active);
    const spacePoints = rows.filter(row => isFiniteMetric(row.values[xKey]) && isFiniteMetric(row.values[yKey]) && isFiniteMetric(row.values[zKey]));
    const heatKeys = (heatChoice ?? numeric.slice(0, 6)).filter(key => numeric.includes(key));
    const heatKeyIdentity = JSON.stringify(heatKeys);
    const correlations = useMemo(() => {
        const keys: string[] = JSON.parse(heatKeyIdentity);
        return mode === 'analytics' ? keys.map(y => keys.map(x => correlateMetrics(rows, x, y))) : [];
    }, [rows, heatKeyIdentity, mode]);
    const searchedKeys = allKeys.filter(key => `${key} ${metricLabel(key)}`.toLowerCase().includes(metricSearch.toLowerCase()));
    const page = Math.min(metricPage, Math.max(0, Math.ceil(searchedKeys.length / 20) - 1));
    const summaryKeys = searchedKeys.slice(page * 20, (page + 1) * 20);
    const summaries = useMemo(() => mode === 'analytics' ? allKeys.map(key => ({ key, ...summarizeMetric(rows, key) })) : [], [rows, allKeys, mode]);
    const summaryMap = new Map(summaries.map(summary => [summary.key, summary]));
    const axis = (title: string) => ({ title: { text: escape(title), font: { size: 11 } }, automargin: true, gridcolor: theme.grid, zeroline: false });
    const base: Partial<Layout> = { paper_bgcolor: theme.background, plot_bgcolor: theme.background,
        font: { color: theme.text, size: 11 }, margin: { l: 58, r: 24, t: 24, b: 65 }, showlegend: false, hovermode: 'closest' };
    const picker = (label: string, value: string, change: (value: string) => void, optional = false) => <label className="flex min-w-0 flex-1 items-center gap-2 text-xs">
        <span className="shrink-0">{label.replace(' metric', '')}</span><select className={`${control} w-full`} aria-label={label} title={caption(value)} value={value} onChange={event => change(event.target.value)}>
            {optional && <option value="">No color metric</option>}
            {numeric.map(key => <option key={key} value={key} title={key}>{metricLabel(key)}</option>)}
        </select>
    </label>;
    const trace = (points: CohortRow[], colored: boolean): Data => ({
        type: 'scatter', mode: 'markers', name: colored ? caption(colorKey) : colorKey ? 'Color unavailable' : 'Candidates',
        x: points.map(row => row.values[xKey] as number), y: points.map(row => row.values[yKey] as number),
        customdata: points.map(row => row.id), text: points.map(row => escape(row.label)),
        hovertemplate: `%{text}<br>${escape(caption(xKey))}: %{x}<br>${escape(caption(yKey))}: %{y}${colorKey ? colored ? `<br>${escape(caption(colorKey))}: %{marker.color}` : '<br>Color metric: unavailable' : ''}<extra></extra>`,
        marker: { size: points.map(row => row.id === activeId ? 14 : selected.has(row.id) ? 11 : 8),
            symbol: points.map(row => row.id === activeId ? 'star' : selected.has(row.id) ? 'diamond' : 'circle'),
            color: colored ? points.map(row => row.values[colorKey] as number) : colorKey ? '#94a3b8' : '#3b82f6',
            opacity: 0.8, colorscale: 'Viridis', showscale: colored,
            colorbar: { title: { text: escape(metricLabel(colorKey)), side: 'right' }, thickness: 12, len: 0.85 },
            line: { color: points.map(row => selected.has(row.id) || row.id === activeId ? theme.text : theme.background),
                width: points.map(row => selected.has(row.id) || row.id === activeId ? 2 : 0.5) } },
    });
    const scatterData: Data[] = colorKey
        ? [trace(pairs.filter(row => isFiniteMetric(row.values[colorKey])), true), trace(pairs.filter(row => !isFiniteMetric(row.values[colorKey])), false)]
        : [trace(pairs, false)];
    const validIds = new Set(rows.map(row => row.id));
    const inspect = (event: PlotMouseEvent) => {
        const id = event.points?.[0]?.customdata;
        if (typeof id === 'string' && validIds.has(id)) onInspect(id);
    };
    const selectPoints = (event: PlotSelectionEvent) => {
        const ids = [...new Set((event?.points ?? []).flatMap(point => typeof point.customdata === 'string' && validIds.has(point.customdata) ? [point.customdata] : []))];
        if (ids.length) onSelect(ids);
    };
    // The earlier nanobody Plotly Lab's X/Y/Z/color controls use Design metrics.
    // Adapt that scatter to the native-row authority without imputing missing color.
    const spaceTrace = (points: CohortRow[], colored: boolean): Data => ({
        type: 'scatter3d', mode: 'markers',
        x: points.map(row => row.values[xKey] as number), y: points.map(row => row.values[yKey] as number),
        z: points.map(row => row.values[zKey] as number), customdata: points.map(row => row.id),
        text: points.map(row => escape(row.label)),
        hovertemplate: `%{text}<br>${escape(caption(xKey))}: %{x}<br>${escape(caption(yKey))}: %{y}<br>${escape(caption(zKey))}: %{z}<extra></extra>`,
        marker: { size: points.map(row => row.id === activeId ? 8 : selected.has(row.id) ? 7 : 5),
            color: colored ? points.map(row => row.values[color3dKey] as number) : color3dKey ? '#94a3b8' : '#3b82f6',
            colorscale: 'Viridis', showscale: colored, opacity: 0.85,
            colorbar: { title: { text: escape(metricLabel(color3dKey)) }, thickness: 12 } },
    });
    return <section aria-label={mode === 'dashboard' ? 'Cohort dashboard charts' : 'Cohort exploratory analytics'} className="space-y-3 text-[var(--text-primary)]">
        {!numeric.length ? <p role="status" className={card}>No finite numeric observations in this cohort. Missing, explicit null, boolean, string and nonfinite values are not plotted.</p> : <>
            <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
                <article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Candidate landscape</h4><span className="text-xs text-[var(--text-secondary)]">{pairs.length} plotted · {rows.length - pairs.length} omitted</span></div>
                    <div className="grid min-w-0 grid-cols-2 gap-2">{picker('X metric', xKey, setXChoice)}{picker('Y metric', yKey, setYChoice)}</div>
                    {pairs.length ? <CohortPlot label={`Candidate scatter: ${caption(xKey)} versus ${caption(yKey)}`} data={scatterData}
                        layout={{ ...base, xaxis: axis(caption(xKey)), yaxis: axis(caption(yKey)), dragmode: dragMode,
                            uirevision: JSON.stringify([xKey, yKey, rows.map(row => row.id)]) }} onClick={inspect} onSelected={selectPoints} />
                        : <p className="flex h-[350px] items-center justify-center text-sm" role="status">No complete finite pairs for these axes. Choose other metrics.</p>}
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="flex items-center gap-2">Drag <select className={control} aria-label="Scatter drag mode" value={dragMode} onChange={event => setDragMode(event.target.value as typeof dragMode)}>
                            <option value="zoom">Zoom</option><option value="select">Box select</option><option value="lasso">Lasso select</option>
                        </select></label>
                        <span>★ inspected · ◆ selected</span>
                        {mode === 'analytics' && picker('Color metric', colorKey, setColorChoice, true)}
                    </div>
                    {colorKey && <p className="mt-1 text-xs text-[var(--text-secondary)]">{pairs.filter(row => !isFiniteMetric(row.values[colorKey])).length} with unavailable color shown gray.</p>}
                </article>
                <article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Metric distribution</h4><span className="text-xs text-[var(--text-secondary)]">{distribution.length} observed · {rows.length - distribution.length} omitted</span></div>
                    {picker('Distribution metric', distKey, setDistributionChoice)}
                    <CohortPlot label={`Histogram and box summary: ${caption(distKey)}`} data={[
                        { type: 'histogram', x: distribution, nbinsx: Math.min(40, Math.max(1, Math.ceil(Math.sqrt(distribution.length)))),
                            marker: { color: '#3b82f6', line: { color: theme.background, width: 1 } },
                            hovertemplate: 'Bin: %{x}<br>Records: %{y}<extra></extra>' } as Data,
                        { type: 'box', q1: [distributionSummary.q1!], median: [distributionSummary.median!], q3: [distributionSummary.q3!],
                            lowerfence: [distributionSummary.min!], upperfence: [distributionSummary.max!],
                            yaxis: 'y2', orientation: 'h', name: 'Summary',
                            boxpoints: false, marker: { color: '#14b8a6' }, line: { color: '#14b8a6' } } as Data,
                    ]} layout={{ ...base, xaxis: axis(caption(distKey)), yaxis: { ...axis('Record count'), domain: [0, 0.73], rangemode: 'tozero' },
                        yaxis2: { domain: [0.82, 1], showticklabels: false, showgrid: false, zeroline: false },
                        uirevision: JSON.stringify([distKey, rows.map(row => row.id)]) }} />
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">Median {formatMetric(distributionSummary.median)} · Q1–Q3 {formatMetric(distributionSummary.q1)}–{formatMetric(distributionSummary.q3)} · Whiskers: min–max.</p>
                </article>
                {barRows.length > 0 && <article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Candidates by native measurement</h4><span className="text-xs text-[var(--text-secondary)]">{barRows.length} of {rows.filter(row => isFiniteMetric(row.values[barKey])).length} observed</span></div>
                    {picker('Bar metric', barKey, setBarChoice)}
                    <CohortPlot label={`Candidate bars: ${caption(barKey)}`} data={[{
                        type: 'bar', x: barRows.map(row => row.label), y: barRows.map(row => row.values[barKey] as number),
                        customdata: barRows.map(row => row.id), marker: { color: barRows.map(row => row.id === activeId ? '#14b8a6' : selected.has(row.id) ? '#a78bfa' : '#3b82f6') },
                        hovertemplate: `%{x}<br>${escape(caption(barKey))}: %{y}<extra></extra>`,
                    } as Data]} layout={{ ...base, margin: { l: 60, r: 24, t: 24, b: 95 },
                        xaxis: { type: 'category', tickangle: -45, automargin: true, color: theme.text }, yaxis: axis(caption(barKey)) }} onClick={inspect} />
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">Highest 20 observed values in this filtered view, not a binder-quality rank. Click a bar to inspect its exact document.</p>
                </article>}
                {composition && <article className={card}>
                    <h4 className="mb-2 text-sm font-semibold">Selected candidate: secondary structure</h4>
                    <p className="text-xs text-[var(--text-secondary)]">{active?.label} · native residue fractions</p>
                    <CohortPlot label="Selected candidate native secondary-structure composition" data={[{
                        type: 'pie', labels: ['Coil', 'Helix', 'Strand'], values: composition, hole: 0.45,
                        marker: { colors: ['#3b82f6', '#14b8a6', '#a78bfa'] },
                        textinfo: 'label+percent', hovertemplate: '%{label}: %{value:.4f} of residues<extra></extra>',
                    } as Data]} layout={{ ...base, margin: { l: 24, r: 24, t: 24, b: 24 }, showlegend: false }} height={300} />
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">Shown only when this record reports a complete coil/helix/strand partition. Not a campaign or binding composition.</p>
                </article>}
            </div>
            <p className="text-xs text-[var(--text-secondary)]">Full filtered cohort · Finite observations only, native scales unchanged; not a verdict. Click to inspect; box/lasso adds selection (also available in the table). Double-click resets zoom; camera exports SVG. Metric controls expose original keys on hover.</p>
        </>}
        {mode === 'analytics' && <>
            {numeric.length >= 3 && <article className={card}>
                <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Explore native metrics in 3D</h4><span className="text-xs text-[var(--text-secondary)]">{spacePoints.length} plotted · {rows.length - spacePoints.length} omitted</span></div>
                <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    {picker('3D X metric', xKey, setXChoice)}{picker('3D Y metric', yKey, setYChoice)}
                    {picker('3D Z metric', zKey, setZChoice)}{picker('3D color metric', color3dKey, setColor3dChoice, true)}
                </div>
                {spacePoints.length ? <CohortPlot label={`Native 3D scatter: ${caption(xKey)}, ${caption(yKey)}, ${caption(zKey)}`}
                    height={440} data={color3dKey ? [spaceTrace(spacePoints.filter(row => isFiniteMetric(row.values[color3dKey])), true), spaceTrace(spacePoints.filter(row => !isFiniteMetric(row.values[color3dKey])), false)] : [spaceTrace(spacePoints, false)]}
                    layout={{ ...base, margin: { l: 0, r: 0, t: 18, b: 0 }, scene: {
                        xaxis: { title: { text: escape(metricLabel(xKey)) }, color: theme.text, gridcolor: theme.grid },
                        yaxis: { title: { text: escape(metricLabel(yKey)) }, color: theme.text, gridcolor: theme.grid },
                        zaxis: { title: { text: escape(metricLabel(zKey)) }, color: theme.text, gridcolor: theme.grid },
                        bgcolor: theme.background }, uirevision: JSON.stringify([xKey, yKey, zKey, rows.map(row => row.id)]) }} onClick={inspect} />
                    : <p role="status" className="py-6 text-sm">No records have complete finite coordinates for these three metrics.</p>}
                <p className="pt-2 text-xs text-[var(--text-secondary)]">Rotate to explore; click a point to inspect its exact native document. Missing color observations remain gray, not zero.</p>
            </article>}
            {numeric.length > 0 && <article className={card}>
                <h4 className="text-sm font-semibold">Pairwise correlation</h4>
                <p className="my-2 text-xs text-[var(--text-secondary)]">Pearson r, pairwise complete observations; at least 3 pairs and nonzero variance. Blank cells are undefined, not zero. No p-values or causal claims. Select up to 8 metrics ({heatKeys.length} of {numeric.length} selected{heatChoice === null ? '; initial selection is the first 6 available metrics' : ''}).</p>
                <label className="flex max-w-lg flex-col gap-1 text-xs">Correlation metrics (Ctrl/Cmd-click to choose)
                    <select multiple size={Math.min(6, numeric.length)} aria-label="Correlation metrics" className={control} value={heatKeys}
                        onChange={event => { const keys = Array.from(event.target.selectedOptions, option => option.value); if (keys.length <= 8) setHeatChoice(keys); }}>
                        {numeric.map(key => <option key={key} value={key} disabled={heatKeys.length >= 8 && !heatKeys.includes(key)}>{caption(key)}</option>)}
                    </select>
                </label>
                {heatKeys.length ? <CohortPlot label="Pearson correlation heatmap" height={Math.max(380, heatKeys.length * 52)} data={[
                    { type: 'heatmap', x: heatKeys, y: heatKeys, z: correlations.map(row => row.map(cell => cell.r)),
                        customdata: correlations.map(row => row.map(cell => `n=${cell.pairs}; ${cell.reason ?? `r=${formatMetric(cell.r)}`}`)),
                        hovertemplate: '%{x}<br>%{y}<br>%{customdata}<extra></extra>', hoverongaps: false,
                        zmin: -1, zmax: 1, zmid: 0, colorscale: 'RdBu', reversescale: true,
                        colorbar: { title: { text: 'Pearson r' }, thickness: 14 } } as Data,
                    { type: 'scatter', mode: 'text', x: correlations.flatMap(row => row.flatMap((cell, x) => cell.r === null ? [heatKeys[x]] : [])),
                        y: correlations.flatMap((row, y) => row.flatMap(cell => cell.r === null ? [heatKeys[y]] : [])),
                        text: correlations.flatMap(row => row.flatMap(cell => cell.r === null ? ['—'] : [])),
                        customdata: correlations.flatMap(row => row.flatMap(cell => cell.r === null ? [`${cell.reason}; n=${cell.pairs}`] : [])),
                        hovertemplate: '%{x}<br>%{y}<br>%{customdata}<extra></extra>', textfont: { color: theme.text } },
                ]} layout={{ ...base, margin: { l: 110, r: 60, t: 25, b: 100 },
                    xaxis: { ...axis(''), type: 'category', tickangle: -30 }, yaxis: { ...axis(''), type: 'category', autorange: 'reversed' } }} />
                    : <p className="py-6 text-sm">Choose metrics to plot their pairwise correlations.</p>}
                <details className="mt-2 text-xs"><summary className="cursor-pointer">Pair counts and undefined reasons (accessible table)</summary>
                    <div className="max-h-64 overflow-auto"><table className="w-full text-left"><thead><tr><th>X</th><th>Y</th><th>Complete pairs</th><th>Pearson r / reason</th></tr></thead>
                        <tbody>{correlations.flatMap((row, y) => row.map((cell, x) => <tr key={`${x}:${y}`}><td className="p-2">{heatKeys[x]}</td><td>{heatKeys[y]}</td><td>{cell.pairs}</td><td>{cell.reason ?? formatMetric(cell.r)}</td></tr>))}</tbody></table></div>
                </details>
            </article>}
            <article className={card}>
                <h4 className="text-sm font-semibold">Descriptive metric summary</h4>
                <p className="my-2 text-xs text-[var(--text-secondary)]">All filtered records, independently per metric. Quartiles use linear interpolation at (n − 1) × p. SD is sample SD (n − 1); unavailable below 2 observations. “—” means undefined or outside numeric range. Missing includes absent/undefined; null and nonnumeric (including nonfinite numbers) are counted separately. Native values are never coerced.</p>
                <label className="flex max-w-md flex-col gap-1 text-xs">Find summary metrics<input className={control} type="search" aria-label="Find summary metrics" value={metricSearch} onChange={event => { setMetricSearch(event.target.value); setMetricPage(0); }} /></label>
                <div className="mt-3 max-h-[480px] overflow-auto"><table className="w-full text-right text-xs tabular-nums">
                    <caption className="sr-only">Descriptive statistics for the full filtered cohort</caption>
                    <thead className="sticky top-0 bg-[var(--bg-primary)]"><tr><th className="p-2 text-left">Metric / native key</th>{['n', 'Missing', 'Null', 'Nonnumeric', 'Min', 'Q1', 'Median', 'Q3', 'Max', 'Mean', 'Sample SD'].map(name => <th key={name} className="whitespace-nowrap p-2">{name}</th>)}</tr></thead>
                    <tbody>{summaryKeys.map(key => { const summary = summaryMap.get(key)!; return <tr key={key} className="border-t border-[var(--border-color)]">
                        <th scope="row" className="p-2 text-left font-normal">{metricLabel(key)}<code className="block text-[var(--text-secondary)]">{key}</code></th>
                        {(['observed', 'missing', 'nulls', 'nonNumeric', 'min', 'q1', 'median', 'q3', 'max', 'mean', 'stdDev'] as const).map(field => <td key={field} className="whitespace-nowrap p-2" title={summary[field] === null ? 'Undefined or outside numeric range' : String(summary[field])}>{summary[field] === null ? '—' : formatMetric(summary[field])}</td>)}
                    </tr>; })}</tbody>
                </table></div>
                {!searchedKeys.length && <p className="py-3 text-sm">No matching metric keys.</p>}
                <nav aria-label="Metric summary pages" className="mt-2 flex items-center gap-3 text-xs"><button type="button" className={control} disabled={page === 0} onClick={() => setMetricPage(page - 1)}>Previous metrics</button>
                    <span>{searchedKeys.length ? page * 20 + 1 : 0}–{Math.min((page + 1) * 20, searchedKeys.length)} of {searchedKeys.length} metrics</span>
                    <button type="button" className={control} disabled={(page + 1) * 20 >= searchedKeys.length} onClick={() => setMetricPage(page + 1)}>Next metrics</button></nav>
            </article>
        </>}
    </section>;
}
