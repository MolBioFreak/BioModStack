import type { FrustraMpnnEffectiveSettingsProjection } from '../../lib/frustraMpnnApi.js';
import { buildFrustraMpnnRequestedEffectiveSummary } from './frustraMpnnSettingsSummary.js';

const originLabel = (origin: string): string => origin.replaceAll('_', ' ');
const altlocLabel = (altloc: string): string => altloc || 'blank';

export function FrustraMpnnRequestedEffectiveSummary({
    effective,
}: {
    effective: FrustraMpnnEffectiveSettingsProjection;
}) {
    const summary = buildFrustraMpnnRequestedEffectiveSummary(effective);
    return (
        <div className="space-y-3 text-xs" data-frustrampnn-requested-effective-summary>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded border border-slate-700/70 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">Requested model</div><div className="mt-1 font-mono">{summary.model.requested}</div></div>
                <div className="rounded border border-slate-700/70 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">Effective model</div><div className="mt-1 font-mono">{summary.model.effective}</div><div className="text-[10px] text-slate-500">{originLabel(summary.model.origin)}</div></div>
                <div className="rounded border border-slate-700/70 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">Requested altloc</div><div className="mt-1 font-mono">{altlocLabel(summary.altloc.requested)}</div></div>
                <div className="rounded border border-slate-700/70 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">Effective altloc</div><div className="mt-1 font-mono">{altlocLabel(summary.altloc.effective)}</div><div className="text-[10px] text-slate-500">{originLabel(summary.altloc.origin)}</div></div>
            </div>
            <div className="grid gap-2 sm:grid-cols-3">
                <div className="rounded border border-slate-700/70 p-2"><div className="text-slate-500">Classification mode</div><div className="font-mono">{summary.thresholds.mode}</div></div>
                <div className="rounded border border-slate-700/70 p-2"><div className="text-slate-500">Highly frustrated ≤</div><div className="font-mono">{summary.thresholds.highMax}</div></div>
                <div className="rounded border border-slate-700/70 p-2"><div className="text-slate-500">Minimally frustrated ≥</div><div className="font-mono">{summary.thresholds.minimalMin}</div></div>
            </div>
            <div className="grid gap-2 sm:grid-cols-5">
                {([
                    ['Selected entities', summary.counts.selectedEntities],
                    ['Selected residues', summary.counts.selectedResidues],
                    ['Resolved entities', summary.counts.resolvedEntities],
                    ['Resolved chains', summary.counts.resolvedChains],
                    ['Resolved residues', summary.counts.resolvedResidues],
                ] as const).map(([label, count]) => <div key={label} className="rounded border border-slate-700/70 p-2"><div className="text-slate-500">{label}</div><div className="font-mono">{count}</div></div>)}
            </div>
            <details className="rounded border border-slate-700/70 p-2">
                <summary className="cursor-pointer font-medium">Selected and resolved identities</summary>
                <div className="mt-2 grid gap-3 lg:grid-cols-2">
                    <div><div className="font-medium">Requested selection: {summary.selectionMode}</div>
                        {summary.selectedEntities.length === 0 && summary.selectedResidues.length === 0
                            ? <p className="mt-1 text-slate-500">No explicit identity selectors; all protein entities were requested.</p>
                            : <ul className="mt-1 list-disc pl-4">{[...summary.selectedEntities, ...summary.selectedResidues].map((identity) => <li key={identity}>{identity}</li>)}</ul>}
                    </div>
                    <div><div className="font-medium">Resolved entity-chain-residue identities</div>
                        {summary.resolvedEntities.length === 0
                            ? <p className="mt-1 text-slate-500">No resolved protein chains.</p>
                            : <><ul className="mt-1 list-disc pl-4">{summary.resolvedEntities.map((identity) => <li key={identity}>{identity}</li>)}</ul>{summary.resolvedResidues.length > 0 && <ul className="mt-2 max-h-40 list-disc overflow-auto pl-4">{summary.resolvedResidues.map((identity) => <li key={identity}>{identity}</li>)}</ul>}</>}
                    </div>
                </div>
            </details>
            <details className="rounded border border-slate-700/70 p-2">
                <summary className="cursor-pointer font-medium">Field-level value origins</summary>
                <dl className="mt-2 grid gap-1 sm:grid-cols-2">{Object.entries(summary.valueOrigins).map(([field, origin]) => <div key={field}><dt className="inline text-slate-500">{field}: </dt><dd className="inline font-mono">{originLabel(origin)}</dd></div>)}</dl>
            </details>
        </div>
    );
}
