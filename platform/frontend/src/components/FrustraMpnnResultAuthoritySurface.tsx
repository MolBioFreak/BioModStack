import type {
    FrustraMpnnResultDetail,
    FrustraMpnnStatistics,
    FrustraMpnnStatisticsAnalysis,
} from '../lib/frustraMpnnApi.js';
import { FrustraMpnnRequestedEffectiveSummary } from './frustrampnn/FrustraMpnnRequestedEffectiveSummary.js';

const fmt = (value: number | null) => value == null ? '—' : Number(value).toFixed(3);

const boundedDiagnostic = (value: string | null): string | null => {
    if (value === null) return null;
    const limit = 320;
    return value.length <= limit ? value : `${value.slice(0, limit)}…`;
};

export function FrustraMpnnStatisticsAnalysisPanel({
    analysis,
    canRetry,
    retryPending = false,
    onRetry,
}: {
    analysis: FrustraMpnnStatisticsAnalysis;
    canRetry: boolean;
    retryPending?: boolean;
    onRetry: () => void;
}) {
    const diagnostic = boundedDiagnostic(analysis.diagnostic);
    return <section aria-label="FrustraMPNN statistics analysis lifecycle" className="rounded-xl border border-violet-500/25 bg-violet-950/10 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
                <h2 className="font-semibold">Statistics analysis {analysis.state}</h2>
                <p className="mt-1 text-xs text-slate-400">Attempt {analysis.attempt_count}</p>
            </div>
            {analysis.state === 'failed' && canRetry && <button type="button" disabled={retryPending} onClick={onRetry} className="rounded-lg border border-violet-400/50 bg-violet-500/10 px-3 py-2 text-xs text-violet-100 disabled:opacity-40">{retryPending ? 'Retrying…' : 'Retry analysis'}</button>}
        </div>
        {analysis.state === 'queued' && <p role="status" className="mt-3 text-xs text-violet-100">Queued for derived-statistics execution.</p>}
        {analysis.state === 'running' && <p role="status" className="mt-3 text-xs text-violet-100">Derived statistics are running.</p>}
        {analysis.state === 'completed' && <p role="status" className="mt-3 text-xs text-emerald-200">Derived statistics completed.</p>}
        {analysis.state === 'failed' && <div role="alert" className="mt-3 rounded border border-red-500/30 bg-red-500/5 p-2 text-xs text-red-100">{diagnostic || 'Derived statistics failed without a diagnostic.'}</div>}
    </section>;
}

export function FrustraMpnnStatisticsSummary({ statistics }: { statistics: FrustraMpnnStatistics }) {
    return <section aria-label="Canonical FrustraMPNN statistics" className="rounded-xl border border-sky-500/25 bg-sky-950/10 p-4">
        <h2 className="font-semibold">Statistics</h2>
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
            {([
                ['Selected residues', statistics.support.selected_residue_count],
                ['Residues with scores', statistics.support.scoreable_residue_count],
                ['Scores available', statistics.support.scoreable_slot_count],
                ['Mean score', statistics.distributions.overall.mean == null ? 'missing' : fmt(statistics.distributions.overall.mean)],
                ['Highly frustrated scores', statistics.class_burden.all.counts.high],
                ['Ranked substitutions', statistics.ranked_non_native_alternatives.best_to_worst.length],
            ] as const).map(([label, value]) => <div key={label} className="rounded border border-slate-800 bg-slate-950/40 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div><div className="mt-1 font-mono text-slate-100">{value}</div></div>)}
        </div>
        {(statistics.support.missing_residue_count > 0 || statistics.support.missing_slot_count > 0) && <div role="status" className="mt-3 rounded border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-100">Missing scores: {statistics.support.missing_residue_count.toLocaleString()} residues and {statistics.support.missing_slot_count.toLocaleString()} slots.</div>}
    </section>;
}

export function FrustraMpnnResultAuthoritySurface({
    detail,
    statisticsOverride,
}: {
    detail: FrustraMpnnResultDetail;
    statisticsOverride?: FrustraMpnnStatistics | null;
}) {
    const statistics = statisticsOverride ?? null;
    return <>
        <details aria-label="Requested and effective FrustraMPNN settings" className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <summary className="cursor-pointer font-semibold">Settings used</summary>
            {detail.effective_settings_json ? <div className="mt-2 space-y-3">
                <FrustraMpnnRequestedEffectiveSummary effective={detail.effective_settings_json} />
            </div> : <p className="mt-2 text-xs text-amber-100">Effective settings were not recorded for this result.</p>}
        </details>
        {statistics
            ? <FrustraMpnnStatisticsSummary statistics={statistics} />
            : <section aria-label="Canonical FrustraMPNN statistics" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-100"><h2 className="font-semibold">Statistics unavailable</h2><p className="mt-1">Statistics are loaded separately; the saved structure and residue scores remain available.</p></section>}
    </>;
}
