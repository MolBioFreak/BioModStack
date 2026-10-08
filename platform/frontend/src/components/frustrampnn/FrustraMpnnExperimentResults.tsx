import type { FrustraMpnnExperimentScopeItem } from '../../lib/projectManager.js';

export function FrustraMpnnExperimentResults({
    items,
    globalRevisionId,
    domainRevisionId,
}: {
    items: readonly { item: FrustraMpnnExperimentScopeItem; href: string | null }[];
    globalRevisionId: string;
    domainRevisionId: string;
}) {
    return (
        <section aria-label="Whole-experiment FrustraMPNN results" className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <h2 className="text-sm font-semibold">Whole-experiment results</h2>
            <p className="mt-1 text-xs text-slate-500">Global revision {globalRevisionId} · Domain revision {domainRevisionId}</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {items.map(({ item, href }) => {
                    const content = <>
                        <span className="block font-semibold text-cyan-100">{item.operator_label}</span>
                        <span className="mt-1 block text-xs font-semibold uppercase tracking-wide text-slate-300">{item.state}</span>
                        <span className="mt-1 block text-xs text-slate-400">Design {item.source_identity.design_id ?? 'not applicable'} · Artifact {item.source_identity.artifact_id}</span>
                        <span className="block text-xs text-slate-500">Candidate {item.source_identity.candidate_id} · Job {item.parent_job_id ?? 'not started'}</span>
                        <span className="block text-xs text-slate-500">Manifest {item.manifest_sha256 ?? 'not published'}</span>
                        <span className="block text-xs text-slate-500">Statistics analysis: {item.statistics_analysis.state}</span>
                        {item.diagnostic && <span className="mt-1 block text-xs text-amber-200">{item.diagnostic}</span>}
                        {item.statistics_analysis.diagnostic && <span className="mt-1 block text-xs text-amber-200">Analysis: {item.statistics_analysis.diagnostic}</span>}
                    </>;
                    const className = "rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm";
                    return href
                        ? <a key={item.result_receipt_id} href={href} className={`${className} hover:border-cyan-400/50`}>{content}</a>
                        : <article key={item.result_receipt_id} className={className}>{content}</article>;
                })}
            </div>
        </section>
    );
}
