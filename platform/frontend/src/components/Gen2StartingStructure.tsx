import type { ReactNode } from 'react';

const STEPS = ['Starting structure', 'Chemistry', 'Protocol and output', 'Review and launch'] as const;

export function Gen2StartingStructure({ returned = false, children }: { returned?: boolean; children?: ReactNode }) {
    return (
        <section aria-label="Molecular Dynamics workflow" className="space-y-5">
            {returned && (
                <div className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 px-4 py-3">
                    <div className="text-sm font-semibold text-cyan-100">Returned from Structure Prediction</div>
                    <div className="mt-1 text-xs text-cyan-200/70">Choose one completed Design, review its exact bytes in Mol*, then promote it explicitly.</div>
                </div>
            )}
            <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="Molecular Dynamics workflow steps">
                {STEPS.map((step, index) => (
                    <li key={step} className={`rounded-xl border px-3 py-2 text-xs font-semibold ${index === 0 ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-100' : 'border-slate-700 bg-slate-900/50 text-slate-400'}`}>
                        <span className="mr-2 font-mono text-[10px]">{index + 1}</span>{step}
                    </li>
                ))}
            </ol>
            {children}
        </section>
    );
}
