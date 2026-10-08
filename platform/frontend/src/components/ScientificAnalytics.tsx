import { useMemo, useState } from 'react';
import { CohortAnalytics } from './CohortAnalytics';
import { metricLabel } from '../lib/cohortAnalytics';
import type { ScientificCohort, ScientificPoint } from '../lib/scientificAnalytics';

const panel = 'min-w-0 rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)] p-3';
const control = 'rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2 text-sm';

/** Adapt canonical observations to the existing dashboard and Plotly Lab. Never
 * merge producer cohorts or reinterpret native values through Design aliases. */
export function ScientificAnalytics({ points, cohorts }: { points: ScientificPoint[]; cohorts: ScientificCohort[] }) {
    const groups = useMemo(() => {
        const grouped = new Map<string, ScientificPoint[]>();
        for (const point of points) {
            const group = grouped.get(point.cohort_key) ?? [];
            group.push(point);
            grouped.set(point.cohort_key, group);
        }
        return [...grouped];
    }, [points]);
    return <section aria-label="Scientific result analytics" className="min-w-0 space-y-4 text-[var(--text-primary)]">
        {groups.map(([key, rows], index) => <ScientificCohortCharts key={key} points={rows}
            cohort={cohorts.find(cohort => cohort.cohort_key === key)}
            title={groups.length > 1 ? `Recorded measurements · cohort ${index + 1}` : 'Recorded measurements'} />)}
    </section>;
}

function ScientificCohortCharts({ points, cohort, title }: { points: ScientificPoint[]; cohort?: ScientificCohort; title: string }) {
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [activeId, setActiveId] = useState<string>();
    const [sortMetric, setSortMetric] = useState('');
    const [showDetails, setShowDetails] = useState(false);
    const descriptors = Object.assign({}, ...points.map(point => point.metric_descriptors)) as ScientificPoint['metric_descriptors'];
    const label = (key: string) => {
        const descriptor = descriptors[key];
        return descriptor ? `${metricLabel(key)} · ${descriptor.scope.replaceAll('_', ' ')} (${descriptor.unit})` : metricLabel(key);
    };
    const rows = useMemo(() => points.map(point => ({
        id: point.id, label: point.name,
        values: Object.fromEntries(Object.entries(point.metric_states).map(([key, state]) => [key, state.state === 'ok' ? state.value : undefined])),
    })), [points]);
    // Prefer a populated published pair; arbitrary axes remain configurable in Lab.
    const pair = Object.values(cohort?.pairs ?? {}).sort((a, b) => b.pair_count - a.pair_count)[0];
    const incomplete = points.filter(point => point.publication_state || Object.values(point.metric_states).some(state => state.state !== 'ok')).length;
    const active = points.find(point => point.id === activeId);
    const ordered = sortMetric ? [...points].sort((a, b) => {
        const av = a.metrics[sortMetric], bv = b.metrics[sortMetric];
        return av === undefined ? bv === undefined ? 0 : 1 : bv === undefined ? -1 : av - bv;
    }) : points;
    return <section aria-label={title} className="min-w-0 space-y-3">
        <header className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold">{title}</h2>
            <span className="text-sm text-[var(--text-secondary)]">{points.length} records · native units</span>
        </header>
        {incomplete > 0 && <p role="status" className={`${panel} text-sm`}>{incomplete} of {points.length} records have unavailable measurements. Charts use observed values only; missing values are not zero.</p>}
        <CohortAnalytics rows={rows} selectedIds={selectedIds} activeId={activeId}
            onInspect={id => { setActiveId(id); setShowDetails(true); }} onSelect={ids => setSelectedIds(previous => [...new Set([...previous, ...ids])])}
            mode="analytics" getMetricLabel={label} inspectionHint="click a point to inspect its recorded measurements"
            initialMetrics={pair ? { x: pair.x_metric, y: pair.y_metric, distribution: pair.x_metric } : undefined} />
        {(active || selectedIds.length > 0) && <div className={`${panel} flex flex-wrap items-center gap-3 text-sm`}>
            {active && <span>Inspecting measurements: {active.name}</span>}
            {selectedIds.length > 0 && <><span>{selectedIds.length} records selected in charts</span><button type="button" className={control} onClick={() => setSelectedIds([])}>Clear chart selection</button></>}
        </div>}
        <details className={`${panel} text-sm`} open={showDetails} onToggle={event => setShowDetails(event.currentTarget.open)}>
            <summary className="cursor-pointer">Measurement details{active ? ` · ${active.name}` : ''}</summary>
            <label className="my-3 flex flex-wrap items-center gap-2">Sort measurements
                <select className={control} aria-label="Sort measurements" value={sortMetric} onChange={event => setSortMetric(event.target.value)}>
                    <option value="">Publication order</option>
                    {Object.keys(descriptors).map(key => <option key={key} value={key}>{label(key)}</option>)}
                </select>
            </label>
            <p className="mb-2 text-xs text-[var(--text-secondary)]">Ascending; unavailable measurements last. Exact native keys and source evidence are retained here.</p>
            <div className="max-h-80 overflow-auto">
                <table className="w-full text-left text-xs"><thead><tr><th className="p-2">Record</th><th>Measurement / scope / unit</th><th>Value or reason</th></tr></thead>
                    <tbody>{ordered.filter(point => !active || point.id === active.id).flatMap(point => [
                        ...(point.publication_state ? [<tr key={`${point.id}:publication`}><td className="p-2">{point.name}</td><td>Publication</td><td>{point.publication_state.reason_code}</td></tr>] : []),
                        ...Object.entries(point.metric_states).map(([key, state]) => <tr key={`${point.id}:${key}`} className="border-t border-[var(--border-color)]">
                            <td className="p-2" title={point.id}>{point.name}</td>
                            <td title={JSON.stringify(point.metric_sources[key])}>{key} / {descriptors[key].scope} / {descriptors[key].unit}</td>
                            <td>{state.state === 'ok' ? state.value : `${state.state}: ${state.reason_code}`}</td>
                        </tr>),
                    ])}</tbody>
                </table>
            </div>
            {active && <button type="button" className={`${control} mt-2`} onClick={() => setActiveId(undefined)}>Show all records</button>}
        </details>
    </section>;
}
