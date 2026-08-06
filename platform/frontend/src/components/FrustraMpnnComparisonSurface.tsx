import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
    createFrustraMpnnGuidance,
    fetchFrustraMpnnComparison,
    fetchFrustraMpnnComparisonRows,
    type FrustraMpnnClass,
    type FrustraMpnnGuidancePlan,
} from '../lib/frustraMpnnApi.js';

interface FrustraMpnnComparisonSurfaceProps {
    referenceJobId: string;
    referenceInvocationId: string;
    targetJobId: string;
    targetInvocationId: string;
    onResidueSelect?: (residue: { authAsymId: string; authSeqId: number; insertionCode: string }) => void;
}

const classTone: Record<FrustraMpnnClass | 'missing' | 'unmapped', string> = {
    high: 'bg-red-500/70 text-white',
    neutral: 'bg-slate-600 text-slate-100',
    minimal: 'bg-emerald-500/70 text-white',
    missing: 'bg-amber-500/50 text-amber-50',
    unmapped: 'bg-purple-500/50 text-purple-50',
};

const displayClass = (value: FrustraMpnnClass | null, status: string): FrustraMpnnClass | 'missing' | 'unmapped' => {
    if (status === 'unmapped') return 'unmapped';
    return value ?? 'missing';
};

export default function FrustraMpnnComparisonSurface({
    referenceJobId,
    referenceInvocationId,
    targetJobId,
    targetInvocationId,
    onResidueSelect,
}: FrustraMpnnComparisonSurfaceProps) {
    const [selectedSequenceIndex, setSelectedSequenceIndex] = useState<number | null>(null);
    const [direction, setDirection] = useState<'higher_is_better' | 'lower_is_better'>('higher_is_better');
    const [rationale, setRationale] = useState('');
    const comparisonQuery = useQuery({
        queryKey: ['frustrampnn', 'comparison', referenceJobId, referenceInvocationId, targetJobId, targetInvocationId],
        queryFn: ({ signal }) => fetchFrustraMpnnComparison(referenceJobId, referenceInvocationId, targetJobId, targetInvocationId, signal),
        staleTime: 30_000,
    });
    const comparison = comparisonQuery.data;
    const rowsQuery = useQuery({
        queryKey: ['frustrampnn', 'comparison-rows', comparison?.comparison_id],
        queryFn: ({ signal }) => fetchFrustraMpnnComparisonRows(comparison!.comparison_id, 5000, 0, signal),
        enabled: Boolean(comparison?.comparison_id),
        staleTime: 30_000,
    });
    const rows = rowsQuery.data?.items ?? [];
    const sequence = useMemo(() => {
        const byIndex = new Map<number, { sequenceIndex: number; wt: string; authAsymId: string; authSeqId: number; insertionCode: string }>();
        for (const row of rows) {
            if (row.sequence_index == null || !row.wt || byIndex.has(row.sequence_index)) continue;
            byIndex.set(row.sequence_index, {
                sequenceIndex: row.sequence_index,
                wt: row.wt,
                authAsymId: row.residue_key.auth_asym_id,
                authSeqId: row.residue_key.auth_seq_id,
                insertionCode: row.residue_key.insertion_code,
            });
        }
        return [...byIndex.values()].sort((left, right) => left.sequenceIndex - right.sequenceIndex);
    }, [rows]);
    const selectedRows = useMemo(
        () => rows.filter((row) => row.sequence_index === selectedSequenceIndex),
        [rows, selectedSequenceIndex],
    );
    const selectedResidue = sequence.find((residue) => residue.sequenceIndex === selectedSequenceIndex) ?? null;
    const guidanceMutation = useMutation<FrustraMpnnGuidancePlan, Error>({
        mutationFn: () => {
            if (!selectedResidue) throw new Error('Select a residue first');
            return createFrustraMpnnGuidance({
                source_job_id: referenceJobId,
                source_invocation_id: referenceInvocationId,
                region: {
                    region_type: 'residue_set',
                    residues: [{
                        auth_asym_id: selectedResidue.authAsymId,
                        auth_seq_id: selectedResidue.authSeqId,
                        insertion_code: selectedResidue.insertionCode,
                    }],
                },
                objective: { objective_type: 'score_aggregate', direction, aggregation: 'mean' },
                constraints: {},
                ranking: { mode: 'lexicographic', tie_break: 'sequence_index_then_mutation' },
                rationale,
            });
        },
    });

    const selectResidue = (residue: typeof selectedResidue) => {
        if (!residue) return;
        setSelectedSequenceIndex(residue.sequenceIndex);
        onResidueSelect?.({ authAsymId: residue.authAsymId, authSeqId: residue.authSeqId, insertionCode: residue.insertionCode });
    };

    return (
        <section aria-label="FrustraMPNN residue comparison" className="overflow-hidden rounded-xl border border-indigo-500/30 bg-indigo-950/10">
            <header className="border-b border-indigo-500/20 p-4">
                <h2 className="font-semibold">Residue-aligned FrustraMPNN comparison</h2>
                <p className="mt-1 text-xs text-slate-400">Reference and target landscapes are joined by chain/entity/residue identity. Raw score deltas, class transitions, unmapped residues, and missing slots remain separate evidence states.</p>
            </header>
            {comparisonQuery.isLoading && <div role="status" className="p-4 text-sm text-slate-400">Preparing persisted comparison…</div>}
            {comparisonQuery.isError && <div role="alert" className="p-4 text-sm text-red-300">Comparison unavailable: {comparisonQuery.error instanceof Error ? comparisonQuery.error.message : 'request failed'}</div>}
            {comparison && <div className="space-y-4 p-4">
                {comparison.comparability.status !== 'comparable' && <div role="alert" className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">Numeric deltas are suppressed because the landscapes are incompatible: {comparison.comparability.reasons.join(', ') || 'unspecified incompatibility'}.</div>}
                <div className="grid gap-2 sm:grid-cols-4">
                    {Object.entries(comparison.summary).map(([key, value]) => <div key={key} className="rounded border border-slate-800 bg-slate-950/40 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">{key.replaceAll('_', ' ')}</div><div className="font-mono text-lg text-slate-100">{value}</div></div>)}
                </div>
                {rowsQuery.isError && <div role="alert" className="text-sm text-red-300">Comparison rows unavailable.</div>}
                {rowsQuery.data?.next_offset != null && <div role="alert" className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-100">Only the first {rows.length.toLocaleString()} of {rowsQuery.data.total.toLocaleString()} rows are loaded; filter or page before making a complete claim.</div>}
                <div>
                    <h3 className="text-sm font-medium text-slate-200">Full aligned sequence</h3>
                    <p className="mb-2 text-xs text-slate-500">Select a residue to inspect every substitution slot and optionally create a guidance plan.</p>
                    <div className="flex gap-1 overflow-x-auto rounded border border-slate-800 bg-slate-950/50 p-2" role="list" aria-label="Aligned reference sequence">
                        {sequence.map((residue) => {
                            const residueRows = rows.filter((row) => row.sequence_index === residue.sequenceIndex);
                            const hasUnmapped = residueRows.some((row) => row.mapping_state === 'unmapped');
                            return <button
                                key={`${residue.authAsymId}-${residue.authSeqId}-${residue.insertionCode}-${residue.sequenceIndex}`}
                                type="button"
                                role="listitem"
                                aria-label={`Residue ${residue.wt}${residue.authSeqId}${residue.insertionCode}`}
                                onClick={() => selectResidue(residue)}
                                className={`min-w-10 rounded border px-1 py-1 text-center ${selectedSequenceIndex === residue.sequenceIndex ? 'border-indigo-300 ring-2 ring-indigo-400/40' : 'border-slate-700'} ${hasUnmapped ? 'bg-purple-950/50' : 'bg-slate-900'}`}
                            ><span className="block font-mono text-sm text-slate-100">{residue.wt}</span><span className="block text-[9px] text-slate-500">{residue.sequenceIndex}</span></button>;
                        })}
                    </div>
                </div>
                {selectedResidue && <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
                    <article className="overflow-x-auto rounded border border-slate-800 bg-slate-950/40 p-3">
                        <div className="mb-2 flex items-center justify-between"><h3 className="text-sm font-medium">Exact evidence for {selectedResidue.wt}{selectedResidue.authSeqId}{selectedResidue.insertionCode}</h3><span className="font-mono text-xs text-slate-500">sequence index {selectedResidue.sequenceIndex}</span></div>
                        <table className="w-full min-w-[720px] text-left text-xs"><thead className="text-slate-500"><tr><th className="p-2">Substitution</th><th className="p-2">Reference</th><th className="p-2">Target</th><th className="p-2">Delta</th><th className="p-2">Transition</th><th className="p-2">State</th></tr></thead><tbody>{selectedRows.map((row) => { const referenceClass = displayClass(row.reference.class, row.reference.status); const targetClass = displayClass(row.target.class, row.target.status); return <tr key={`${row.mutation_aa}-${row.missingness_state}`} className="border-t border-slate-800"><td className="p-2 font-mono">{row.wt}:{row.mutation_aa}</td><td className="p-2"><span className={`rounded px-1.5 py-0.5 ${classTone[referenceClass]}`}>{row.reference.score ?? '—'} · {referenceClass}</span></td><td className="p-2"><span className={`rounded px-1.5 py-0.5 ${classTone[targetClass]}`}>{row.target.score ?? '—'} · {targetClass}</span></td><td className="p-2 font-mono">{row.raw_score_delta == null ? '—' : row.raw_score_delta.toFixed(4)}</td><td className="p-2">{row.classification_transition ?? '—'}</td><td className="p-2 text-slate-400">{row.missingness_state} · {row.biological_status}</td></tr>; })}</tbody></table>
                    </article>
                    <aside className="rounded border border-indigo-500/30 bg-indigo-950/20 p-3">
                        <h3 className="text-sm font-medium">Decision-support guidance</h3>
                        <p className="mt-1 text-xs text-slate-400">This creates an immutable computational plan only. It does not control instruments or assert an experimental outcome.</p>
                        <label className="mt-3 block text-xs text-slate-400">Objective direction<select value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"><option value="higher_is_better">Higher raw score is better</option><option value="lower_is_better">Lower raw score is better</option></select></label>
                        <label className="mt-3 block text-xs text-slate-400">Scientific rationale<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="State the hypothesis for this region and direction" className="mt-1 h-24 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200" /></label>
                        <button type="button" disabled={!rationale.trim() || guidanceMutation.isPending} onClick={() => guidanceMutation.mutate()} className="mt-3 w-full rounded bg-indigo-500 px-3 py-2 text-sm text-white disabled:opacity-40">{guidanceMutation.isPending ? 'Persisting plan…' : 'Create immutable guidance plan'}</button>
                        {guidanceMutation.isError && <div role="alert" className="mt-2 text-xs text-red-300">{guidanceMutation.error.message}</div>}
                        {guidanceMutation.data && <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-100"><div>Guidance persisted: <span className="font-mono">{guidanceMutation.data.guidance_id}</span></div><div className="mt-1">Top ranked slots:</div><ol className="mt-1 list-inside list-decimal">{guidanceMutation.data.ranked_slots.slice(0, 5).map((slot) => <li key={`${slot.rank}-${slot.sequence_index}-${slot.mutation_aa}`}>{slot.wt}:{slot.mutation_aa} · {slot.score.toFixed(4)}</li>)}</ol></div>}
                    </aside>
                </div>}
            </div>}
        </section>
    );
}
