import * as React from 'react';

import type { CmStateLandscapeAnalysisRowsPage, CmStateLandscapeAnalysisSummary, CmStateLandscapeRow } from './conformationalMappingApi.js';
import { stateLandscapeMetricText, stateLandscapeRowKey } from './stateLandscapeWorkspace.js';

export type StateLandscapeMetricName = keyof CmStateLandscapeRow['metrics'];

interface Props {
    readonly summary: CmStateLandscapeAnalysisSummary;
    readonly page: CmStateLandscapeAnalysisRowsPage | null;
    readonly selectedPairId: string;
    readonly selectedStateRowKey: string | null | undefined;
    readonly selectedMetric: StateLandscapeMetricName;
    readonly inspectorMinimized: boolean;
    readonly loading?: boolean;
    readonly error?: string | null;
    readonly residueSelectionReason?: string | null;
    readonly onSelectPair: (pairId: string) => void;
    readonly onSelectRow: (row: CmStateLandscapeRow) => void;
    readonly onInspectCandidate: (candidateId: string) => void;
    readonly onSelectMetric: (metric: StateLandscapeMetricName) => void;
    readonly onToggleInspector: () => void;
    readonly onLoadMore: () => void;
}

const METRICS: Array<{ value: StateLandscapeMetricName; label: string }> = [
    { value: 'native_score', label: 'Native score' },
    { value: 'high_non_native_highly_frustrated_fraction', label: 'High-frustrated fraction' },
    { value: 'maximum_non_native_substitution_delta_relative_to_native', label: 'Maximum substitution delta' },
    { value: 'native_class', label: 'Native class transition' },
];

const rowLabel = (row: CmStateLandscapeRow): string => `${row.identity.auth_asym_id}:${row.identity.auth_seq_id}${row.identity.insertion_code} · sequence ${row.identity.sequence_index} · ${row.identity.validated_wt}`;
const unavailable = (reason: string): React.ReactElement => <span className="text-amber-200">Unavailable: {reason}</span>;

const MetricDetail = ({ label, metric }: { label: string; metric: CmStateLandscapeRow['metrics'][StateLandscapeMetricName] }) => (
    <div className="rounded border border-slate-800 bg-slate-950/50 p-2">
        <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
        <dd className="mt-1 font-mono text-[11px] text-slate-200">{metric.status === 'unavailable' ? unavailable(metric.reason) : stateLandscapeMetricText(metric)}</dd>
    </div>
);

/** Responsive bounded C2 workspace. It presents persisted B2 projections only; no metric/pair/support reconstruction occurs here. */
export function StateLandscapeWorkspacePanel({
    summary, page, selectedPairId, selectedStateRowKey, selectedMetric, inspectorMinimized, loading = false, error, residueSelectionReason,
    onSelectPair, onSelectRow, onInspectCandidate, onSelectMetric, onToggleInspector, onLoadMore,
}: Props) {
    const selectedPair = summary.pairs.find((pair) => pair.pair_id === selectedPairId) ?? null;
    const selectedRow = page?.rows.find((row) => stateLandscapeRowKey(row) === selectedStateRowKey) ?? null;
    return (
        <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70" aria-label="State analysis workspace">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 p-4">
                <div><h2 className="font-semibold text-white">Synchronized state analysis</h2><p className="mt-1 text-xs text-slate-500">Bounded server projections in persisted pair order. Scientific values, rows, and pair ordering are not derived in this browser.</p></div>
                <div className="grid grid-cols-3 gap-2 text-right text-[11px] text-slate-400"><span>{summary.counts.pairs} pairs</span><span>{summary.counts.rows} rows</span><span>{summary.counts.exclusions} exclusions</span></div>
            </header>
            <div className="grid min-h-[520px] gap-px bg-slate-800 xl:grid-cols-[260px_minmax(0,1fr)_minmax(280px,360px)]">
                <aside className="max-h-[680px] overflow-auto bg-slate-900/95 p-3" aria-label="State-analysis pairs">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-300">State-analysis pairs</h3>
                    <p className="mt-1 text-[10px] leading-4 text-slate-500">Pair support is unavailable: the B2 summary exposes request-level counts only.</p>
                    <div className="mt-3 space-y-2">{summary.pairs.map((pair, index) => <div key={pair.pair_id} className={`rounded-lg border p-2 ${pair.pair_id === selectedPairId ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800'}`}>
                        <button type="button" onClick={() => onSelectPair(pair.pair_id)} className="w-full text-left"><div className="text-xs font-medium text-white">Pair {index + 1}</div><div className="mt-1 truncate font-mono text-[10px] text-slate-400">{pair.pair_id}</div></button>
                        <div className="mt-2 grid gap-1 text-[11px]"><div className="flex items-center justify-between gap-2"><span className="truncate text-slate-300">A: {pair.candidate_a_id}</span><button type="button" className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-sky-200 hover:border-sky-400" onClick={() => onInspectCandidate(pair.candidate_a_id)}>Inspect A</button></div><div className="flex items-center justify-between gap-2"><span className="truncate text-slate-300">B: {pair.candidate_b_id}</span><button type="button" className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-sky-200 hover:border-sky-400" onClick={() => onInspectCandidate(pair.candidate_b_id)}>Inspect B</button></div></div>
                    </div>)}</div>
                </aside>
                <div className="min-w-0 bg-slate-950/30">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 p-3"><div><h3 className="text-sm font-medium text-white">{selectedPair ? `${selectedPair.candidate_a_id} → ${selectedPair.candidate_b_id}` : 'No selected pair'}</h3><p className="mt-0.5 text-[11px] text-slate-500">Rows are fetched only for this pair in B2 pages.</p></div><label className="text-[11px] text-slate-400">Table metric <select value={selectedMetric} onChange={(event) => onSelectMetric(event.target.value as StateLandscapeMetricName)} className="ml-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">{METRICS.map((metric) => <option key={metric.value} value={metric.value}>{metric.label}</option>)}</select></label></div>
                    {error && <div role="alert" className="m-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">State analysis is unavailable: {error}</div>}
                    {loading && <p className="p-4 text-sm text-slate-500">Loading bounded state-analysis rows…</p>}
                    {!loading && !error && <><div className="max-h-[570px] overflow-auto"><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 z-10 bg-slate-900 text-slate-400"><tr><th className="p-2">Residue identity</th><th className="p-2">{METRICS.find((metric) => metric.value === selectedMetric)?.label}</th><th className="p-2">Native class</th></tr></thead><tbody>{(page?.rows ?? []).map((row) => { const key = stateLandscapeRowKey(row); const metric = row.metrics[selectedMetric]; return <tr key={key} className={`cursor-pointer border-t border-slate-800 align-top hover:bg-slate-800/60 ${key === selectedStateRowKey ? 'bg-orange-500/10' : ''}`} onClick={() => onSelectRow(row)}><td className="p-2"><div className="font-medium text-white">{rowLabel(row)}</div><div className="mt-1 font-mono text-[10px] text-slate-600">{row.identity.entity_instance_id} · {row.identity.target_id}</div></td><td className="p-2 font-mono text-[11px]">{metric.status === 'unavailable' ? unavailable(metric.reason) : stateLandscapeMetricText(metric)}</td><td className="p-2 font-mono text-[11px]">{row.metrics.native_class.status === 'unavailable' ? unavailable(row.metrics.native_class.reason) : `${row.metrics.native_class.a} → ${row.metrics.native_class.b}`}</td></tr>; })}</tbody></table></div>{!page?.rows.length && <p className="p-4 text-sm text-slate-500">No persisted state-analysis rows were returned for this pair.</p>}{page?.next_offset !== null && page?.next_offset !== undefined && <div className="border-t border-slate-800 p-3"><button type="button" onClick={onLoadMore} className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:border-orange-400">Load more persisted rows</button></div>}</>}</div>
                <aside className="max-h-[680px] overflow-auto bg-slate-900/95 p-3" aria-label="Docked residue inspector">
                    <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-300">Docked residue inspector</h3><button type="button" onClick={onToggleInspector} className="rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-300">{inspectorMinimized ? 'Expand' : 'Minimize'}</button></div>
                    {!inspectorMinimized && (selectedRow ? <div className="mt-3 space-y-3"><div className="rounded border border-orange-500/30 bg-orange-500/5 p-2"><div className="font-medium text-white">{rowLabel(selectedRow)}</div><div className="mt-1 font-mono text-[10px] text-slate-400">target {selectedRow.identity.target_id} · entity {selectedRow.identity.entity_instance_id}</div><div className="mt-1 font-mono text-[10px] text-slate-400">author {selectedRow.identity.auth_asym_id}:{selectedRow.identity.auth_seq_id}{selectedRow.identity.insertion_code} · sequence {selectedRow.identity.sequence_index}</div></div><dl className="grid gap-2"><MetricDetail label="Native score (A / B / delta)" metric={selectedRow.metrics.native_score} /><MetricDetail label="High-frustrated fraction (A / B / delta)" metric={selectedRow.metrics.high_non_native_highly_frustrated_fraction} /><MetricDetail label="Maximum substitution delta (A / B / delta)" metric={selectedRow.metrics.maximum_non_native_substitution_delta_relative_to_native} /><MetricDetail label="Native class / transition" metric={selectedRow.metrics.native_class} /></dl><div className="rounded border border-slate-800 p-2 text-[11px] text-slate-400"><div className="font-medium text-slate-300">Support and missingness</div><p className="mt-1">Pair support: unavailable — B2 does not expose a per-pair support ledger.</p><p className="mt-1">Exclusion detail: unavailable — this bounded rows endpoint returns eligible rows only. Metric availability reasons above remain authoritative.</p>{residueSelectionReason && <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-100">3D selection unavailable: {residueSelectionReason}</p>}</div></div> : <p className="mt-3 text-sm text-slate-500">Select a persisted row to inspect its exact canonical residue identity and stored A/B values.</p>)}
                </aside>
            </div>
        </section>
    );
}
