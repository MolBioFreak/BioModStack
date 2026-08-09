import type { FrustraMpnnResultDetail, FrustraMpnnStatistics } from '../lib/frustraMpnnApi.js';
import { FrustraMpnnRequestedEffectiveSummary } from './frustrampnn/FrustraMpnnRequestedEffectiveSummary.js';

const fmt = (value: number | null) => value == null ? '—' : Number(value).toFixed(3);
const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;

export function FrustraMpnnStatisticsSummary({ statistics }: { statistics: FrustraMpnnStatistics }) {
    return <section aria-label="Canonical FrustraMPNN statistics" className="rounded-xl border border-sky-500/25 bg-sky-950/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2"><div><h2 className="font-semibold">Canonical statistics</h2><p className="mt-1 text-xs text-slate-400">Persisted support, distributions, class burdens, and rankings from the governed statistics authority.</p></div><span className="font-mono text-[10px] text-sky-200">{shortHash(statistics.statistics_sha256)}</span></div>
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
            {([
                ['Selected residues', statistics.support.selected_residue_count],
                ['Scoreable residues', statistics.support.scoreable_residue_count],
                ['Scoreable slots', statistics.support.scoreable_slot_count],
                ['Mean score', statistics.distributions.overall.mean == null ? 'missing' : fmt(statistics.distributions.overall.mean)],
                ['Highly frustrated', statistics.class_burden.all.counts.high],
                ['Ranked alternatives', statistics.ranked_non_native_alternatives.best_to_worst.length],
            ] as const).map(([label, value]) => <div key={label} className="rounded border border-slate-800 bg-slate-950/40 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div><div className="mt-1 font-mono text-slate-100">{value}</div></div>)}
        </div>
        {(statistics.support.missing_residue_count > 0 || statistics.support.missing_slot_count > 0) && <div role="status" className="mt-3 rounded border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-100">Historical/statistical missingness: {statistics.support.missing_residue_count.toLocaleString()} residues and {statistics.support.missing_slot_count.toLocaleString()} slots.</div>}
    </section>;
}

export function FrustraMpnnResultAuthoritySurface({ detail }: { detail: FrustraMpnnResultDetail }) {
    return <>
        <section aria-label="FrustraMPNN result authority" className={`rounded-xl border p-4 ${detail.authority_version === 'v2' ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-amber-500/30 bg-amber-500/5'}`}>
            <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Result authority: {detail.authority_version}</h2><span className="text-xs">{detail.availability ? 'available' : 'partially available'}</span></div>
            {detail.authority_version === 'historical_v1' && <p className="mt-2 text-xs text-amber-100">Historical v1 result: settings, execution-receipt, statistics, or comparison identities may be unavailable. Missing fields remain explicit and are not inferred.</p>}
            {detail.missing_fields.length > 0 && <p className="mt-2 text-xs text-slate-400">Missing authority: {detail.missing_fields.join(', ')}</p>}
            {detail.execution_receipt && <p className="mt-2 text-xs text-slate-400">Execution receipt {detail.execution_receipt.execution_configuration_sha256 ? shortHash(detail.execution_receipt.execution_configuration_sha256) : 'configuration unavailable'} · {detail.execution_receipt.command_count ?? 'unknown'} governed command(s) · GPU {detail.execution_receipt.gpu_provenance?.physical_device_id ?? 'unassigned'}</p>}
        </section>
        <section aria-label="Requested and effective FrustraMPNN settings" className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <h2 className="font-semibold">Requested and effective settings</h2>
            {detail.effective_settings_json ? <div className="mt-2 space-y-3">
                <div className="grid gap-2 text-xs sm:grid-cols-3"><div>Requested: {detail.effective_settings_json.requested_settings.protein_selection.mode}</div><div>Resolved chains: {detail.effective_settings_json.resolved_chains.length}</div><div>Effective authority: <span className="font-mono">{shortHash(detail.effective_settings_json.effective_settings_sha256)}</span></div></div>
                <FrustraMpnnRequestedEffectiveSummary effective={detail.effective_settings_json} />
            </div> : <p className="mt-2 text-xs text-amber-100">Effective settings were not recorded for this historical result.</p>}
        </section>
        {detail.statistics_json
            ? <FrustraMpnnStatisticsSummary statistics={detail.statistics_json} />
            : <section aria-label="Canonical FrustraMPNN statistics" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-100"><h2 className="font-semibold">Canonical statistics unavailable</h2><p className="mt-1">{detail.authority_version === 'historical_v1' ? 'This historical result predates persisted statistics authority. Missing statistics remain explicit and are not reconstructed.' : `Typed missingness: ${detail.missing_fields.filter((field) => field.includes('statistics')).join(', ') || 'statistics_json'}.`}</p></section>}
    </>;
}
