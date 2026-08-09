import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    createFrustraMpnnMultiComparison,
    fetchFrustraMpnnMultidimensionalPoints,
    type FrustraMpnnClass,
    type FrustraMpnnComparisonSide,
    type FrustraMpnnMultiComparison,
    type FrustraMpnnPairCompatibility,
    type FrustraMpnnResultReference,
} from '../lib/frustraMpnnApi.js';
import FrustraMpnnComparisonSurface from './FrustraMpnnComparisonSurface.js';
import {
    appendFrustraMpnnComparisonTarget,
    frustraMpnnResultReferenceKey,
    MAX_FRUSTRAMPNN_MULTI_TARGETS,
    moveFrustraMpnnComparisonTarget,
    removeFrustraMpnnComparisonTarget,
    type FrustraMpnnSelectableResult,
} from './frustrampnn/frustraMpnnComparisonSelection.js';

interface Props {
    referenceJobId: string;
    referenceInvocationId: string;
    availableTargets?: FrustraMpnnSelectableResult[];
}

const classTone: Record<FrustraMpnnClass | 'missing' | 'unmapped', string> = {
    high: 'bg-red-500/70 text-white',
    neutral: 'bg-slate-600 text-slate-100',
    minimal: 'bg-emerald-500/70 text-white',
    missing: 'bg-amber-500/50 text-amber-50',
    unmapped: 'bg-purple-500/50 text-purple-50',
};

const displaySide = (side: FrustraMpnnComparisonSide | null): { score: string; category: keyof typeof classTone } => {
    if (!side) return { score: '—', category: 'missing' };
    const category = side.status === 'unmapped' ? 'unmapped' : side.class ?? 'missing';
    return { score: side.score == null ? '—' : side.score.toFixed(4), category };
};

const domainTone = (safe: boolean): string => safe
    ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-100'
    : 'border-amber-500/40 bg-amber-500/10 text-amber-100';

function MultiTargetCompatibility({ pair }: { pair: FrustraMpnnPairCompatibility }) {
    const rawSafe = !pair.override_used && pair.compatibility_domains.raw_score.status === 'compatible';
    const classSafe = rawSafe && pair.compatibility_domains.classification.status === 'compatible';
    const sourceReasons = [
        ...pair.compatibility_domains.raw_score.reasons,
        ...pair.compatibility_domains.classification.reasons,
        ...pair.compatibility_domains.identity_alignment.reasons,
    ];
    return (
        <article className="rounded border border-slate-700 bg-slate-950/35 p-3" data-frustrampnn-target-compatibility={pair.target_label}>
            <h4 className="font-semibold">{pair.target_label} · {pair.target_id}</h4>
            <div className="mt-2 grid gap-2 lg:grid-cols-3">
                <div className={`rounded border p-2 ${domainTone(rawSafe)}`}><div className="font-medium">Raw score: {pair.compatibility_domains.raw_score.status}</div><div>{rawSafe ? 'Per-target raw deltas may be shown.' : 'Per-target raw deltas remain hidden.'}</div></div>
                <div className={`rounded border p-2 ${domainTone(classSafe)}`}><div className="font-medium">Classification: {pair.compatibility_domains.classification.status}</div><div>{classSafe ? 'Per-target transitions may be shown.' : 'Per-target transitions remain hidden.'}</div></div>
                <div className={`rounded border p-2 ${domainTone(pair.compatibility_domains.identity_alignment.status === 'exact')}`}><div className="font-medium">Alignment: {pair.compatibility_domains.identity_alignment.status}</div><div>{pair.compatibility_domains.identity_alignment.aligned_identity_count} aligned of {pair.compatibility_domains.identity_alignment.reference_identity_count} reference and {pair.compatibility_domains.identity_alignment.target_identity_count} target identities.</div></div>
            </div>
            {sourceReasons.length > 0 && <p className="mt-2 text-slate-400">Reasons: {[...new Set(sourceReasons)].join(', ')}</p>}
            {pair.override_used && <p role="status" className="mt-2 text-amber-100">Compatibility override persisted this target pair only. Unsafe delta and transition hidden.</p>}
        </article>
    );
}

export function FrustraMpnnMultiComparisonView({ comparison }: { comparison: FrustraMpnnMultiComparison }) {
    const pairByLabel = new Map(comparison.pair_compatibility.map((pair) => [pair.target_label, pair]));
    const targetSources = comparison.source_result_references.filter((source) => source.role === 'target');
    return (
        <section aria-label="FrustraMPNN multi-result comparison" className="mt-4 space-y-4 rounded-lg border border-indigo-500/30 bg-indigo-950/10 p-4">
            <div><h3 className="font-semibold">Ordered multi-result comparison</h3><p className="mt-1 text-xs text-slate-400">Each target retains its own compatibility domains, missingness, delta, and transition authority.</p></div>
            {comparison.comparability.status !== 'comparable' && <div role="alert" className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-100">One or more target pairs are incompatible: {comparison.comparability.reasons.join(', ') || 'unspecified incompatibility'}.</div>}
            <div className="grid gap-2 sm:grid-cols-4">{([
                ['Target count', comparison.summary.target_count],
                ['Total rows', comparison.summary.total_rows],
                ['Biologically scored', comparison.summary.biologically_scored],
                ['Partially scored', comparison.summary.partially_scored],
                ['Missing', comparison.summary.missing],
                ['Unmapped', comparison.summary.unmapped],
                ['Incompatible', comparison.summary.incompatible],
                ['Transitions', comparison.summary.transitions],
            ] as const).map(([label, value]) => <div key={label} className="rounded border border-slate-800 bg-slate-950/40 p-2"><div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div><div className="font-mono text-lg">{value}</div></div>)}</div>
            <div className="space-y-2 text-xs">{comparison.target_labels.map((targetLabel) => {
                const pair = pairByLabel.get(targetLabel);
                if (!pair) return null;
                const source = targetSources.find((item) => item.target_label === targetLabel);
                return <div key={targetLabel}>
                    <MultiTargetCompatibility pair={pair} />
                    {source && <p className="mt-1 px-1 font-mono text-[10px] text-slate-500">{source.parent_job_id} · {source.invocation_id}</p>}
                </div>;
            })}</div>
            <div className="overflow-x-auto rounded border border-slate-800 bg-slate-950/40">
                <table className="w-full min-w-[900px] text-left text-xs">
                    <thead className="text-slate-500"><tr><th className="p-2">Exact residue / mutation</th><th className="p-2">Reference</th>{comparison.target_labels.map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
                    <tbody>{comparison.rows.map((row, rowIndex) => {
                        const reference = displaySide(row.reference);
                        return <tr key={`${row.residue_key.entity_instance_id}:${row.residue_key.auth_asym_id}:${row.residue_key.auth_seq_id}:${row.residue_key.insertion_code}:${row.mutation_aa}:${rowIndex}`} className="border-t border-slate-800">
                            <td className="p-2 font-mono">{row.residue_key.auth_asym_id}:{row.residue_key.auth_seq_id}{row.residue_key.insertion_code} → {row.mutation_aa}<div className="text-[10px] text-slate-500">sequence {row.sequence_index ?? 'unmapped'} · {row.biological_status}</div></td>
                            <td className="p-2"><span className={`rounded px-1.5 py-0.5 ${classTone[reference.category]}`}>{reference.score} · {reference.category}</span></td>
                            {comparison.target_labels.map((targetLabel, targetIndex) => {
                                const pair = pairByLabel.get(targetLabel);
                                const target = displaySide(row.targets[targetIndex] ?? null);
                                const rawSafe = Boolean(pair && !pair.override_used && pair.compatibility_domains.raw_score.status === 'compatible');
                                const classSafe = Boolean(rawSafe && pair?.compatibility_domains.classification.status === 'compatible');
                                const delta = rawSafe ? row.raw_score_deltas[targetIndex] : null;
                                const transition = classSafe ? row.classification_transitions[targetIndex] : null;
                                return <td key={targetLabel} className="p-2 align-top"><span className={`rounded px-1.5 py-0.5 ${classTone[target.category]}`}>{target.score} · {target.category}</span><div className="mt-1 font-mono">Δ {delta == null ? '—' : delta.toFixed(4)}</div><div className="mt-1">{transition ?? '—'}</div><div className="mt-1 text-[10px] text-slate-500">{row.missingness_by_target[targetIndex] ?? row.missingness_state}</div></td>;
                            })}
                        </tr>;
                    })}</tbody>
                </table>
            </div>
        </section>
    );
}

function WorkbenchControls({ reference, availableTargets }: { reference: FrustraMpnnResultReference; availableTargets: FrustraMpnnSelectableResult[] }) {
    const [mode, setMode] = useState<'pair' | 'multi'>('pair');
    const [pairJobId, setPairJobId] = useState(reference.parent_job_id);
    const [pairInvocationId, setPairInvocationId] = useState('');
    const [candidateKey, setCandidateKey] = useState('');
    const [selectedTargets, setSelectedTargets] = useState<FrustraMpnnSelectableResult[]>([]);
    const [allowIncompatible, setAllowIncompatible] = useState(false);
    const [multiComparison, setMultiComparison] = useState<FrustraMpnnMultiComparison | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const candidateByKey = new Map(availableTargets.map((target) => [frustraMpnnResultReferenceKey(target), target]));
    const effectiveCandidateKey = candidateKey || (availableTargets[0] ? frustraMpnnResultReferenceKey(availableTargets[0]) : '');
    const addTarget = () => {
        const candidate = candidateByKey.get(effectiveCandidateKey);
        if (!candidate) return;
        setSelectedTargets((current) => appendFrustraMpnnComparisonTarget(current, candidate, reference));
    };
    const createMulti = async () => {
        setPending(true);
        setError(null);
        setMultiComparison(null);
        try {
            setMultiComparison(await createFrustraMpnnMultiComparison(reference, selectedTargets, allowIncompatible));
        } catch (caught) {
            setError(caught instanceof Error ? caught.message : 'Multi-result comparison failed.');
        } finally {
            setPending(false);
        }
    };
    return (
        <section className="rounded-xl border border-indigo-500/30 bg-indigo-950/5 p-4" aria-label="FrustraMPNN comparison workbench">
            <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">Compare persisted FrustraMPNN landscapes</h2><p className="mt-1 text-xs text-slate-500">Use pair mode for one explicit target or multi mode for an ordered, bounded set of persisted result references.</p></div><label className="text-xs text-slate-400">Comparison mode<select aria-label="Comparison mode" value={mode} onChange={(event) => { setMode(event.target.value as typeof mode); setError(null); setMultiComparison(null); }} className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200"><option value="pair">Pair</option><option value="multi">Multi-result</option></select></label></div>
            <div className="mt-3 text-xs text-slate-500">Reference: <span className="font-mono">{reference.parent_job_id} · {reference.invocation_id}</span></div>
            {mode === 'pair' ? <>
                <div className="mt-3 grid gap-3 md:grid-cols-2 text-xs"><label className="text-slate-400">Target job ID<input value={pairJobId} onChange={(event) => setPairJobId(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-slate-200" /></label><label className="text-slate-400">Target invocation ID<input value={pairInvocationId} onChange={(event) => setPairInvocationId(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-slate-200" /></label></div>
                {pairJobId.trim() && pairInvocationId.trim() && <div className="mt-4"><FrustraMpnnComparisonSurface referenceJobId={reference.parent_job_id} referenceInvocationId={reference.invocation_id} targetJobId={pairJobId.trim()} targetInvocationId={pairInvocationId.trim()} /></div>}
            </> : <>
                <div className="mt-3 flex flex-wrap items-end gap-2 text-xs"><label className="min-w-[320px] flex-1 text-slate-400">Available comparison target<select aria-label="Available comparison target" value={effectiveCandidateKey} onChange={(event) => setCandidateKey(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200">{availableTargets.map((target) => <option key={frustraMpnnResultReferenceKey(target)} value={frustraMpnnResultReferenceKey(target)}>{target.label}</option>)}</select></label><button type="button" onClick={addTarget} disabled={!effectiveCandidateKey || selectedTargets.length >= MAX_FRUSTRAMPNN_MULTI_TARGETS} className="rounded border border-indigo-400/40 px-3 py-1.5 text-indigo-100 disabled:opacity-40">Add target</button></div>
                <p className="mt-2 text-[11px] text-slate-500">Ordered targets: {selectedTargets.length}/{MAX_FRUSTRAMPNN_MULTI_TARGETS}. The server assigns target-0001… labels in this exact order.</p>
                <ol className="mt-2 space-y-1">{selectedTargets.map((target, index) => <li key={frustraMpnnResultReferenceKey(target)} data-frustrampnn-selected-target className="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/40 p-2 text-xs"><span className="w-7 font-mono text-indigo-200">{index + 1}</span><span className="min-w-0 flex-1 truncate">{target.label}</span><button type="button" aria-label={`Move ${target.label} up`} disabled={index === 0} onClick={() => setSelectedTargets((current) => moveFrustraMpnnComparisonTarget(current, index, -1))} className="rounded border border-slate-700 px-2 disabled:opacity-30">↑</button><button type="button" aria-label={`Move ${target.label} down`} disabled={index === selectedTargets.length - 1} onClick={() => setSelectedTargets((current) => moveFrustraMpnnComparisonTarget(current, index, 1))} className="rounded border border-slate-700 px-2 disabled:opacity-30">↓</button><button type="button" aria-label={`Remove ${target.label}`} onClick={() => setSelectedTargets((current) => removeFrustraMpnnComparisonTarget(current, index))} className="rounded border border-red-500/40 px-2 text-red-200">Remove</button></li>)}</ol>
                <label className="mt-3 flex items-start gap-2 text-xs text-amber-100"><input type="checkbox" checked={allowIncompatible} onChange={(event) => setAllowIncompatible(event.target.checked)} /><span>Persist incompatible pairs with a safe override. This never authorizes incompatible raw deltas or classification transitions.</span></label>
                <button type="button" disabled={selectedTargets.length === 0 || pending} onClick={() => void createMulti()} className="mt-3 rounded bg-indigo-500 px-3 py-2 text-sm text-white disabled:opacity-40">{pending ? 'Creating comparison…' : 'Create ordered multi-result comparison'}</button>
                {error && <div role="alert" className="mt-2 text-xs text-red-300">{error}</div>}
                {multiComparison && <FrustraMpnnMultiComparisonView comparison={multiComparison} />}
            </>}
        </section>
    );
}

function DiscoveredWorkbench({ reference }: { reference: FrustraMpnnResultReference }) {
    const query = useQuery({
        queryKey: ['frustrampnn', 'comparison-targets', reference.parent_job_id, reference.invocation_id],
        queryFn: ({ signal }) => fetchFrustraMpnnMultidimensionalPoints([], 200, signal),
        staleTime: 30_000,
    });
    const availableTargets = useMemo(() => {
        const seen = new Set<string>();
        return (query.data?.items ?? []).flatMap((point) => {
            const target = {
                parent_job_id: point.job_id,
                invocation_id: point.invocation_id,
                label: `${point.candidate_id} · ${point.job_id} · ${point.invocation_id}`,
            };
            const key = frustraMpnnResultReferenceKey(target);
            if (key === frustraMpnnResultReferenceKey(reference) || seen.has(key)) return [];
            seen.add(key);
            return [target];
        });
    }, [query.data?.items, reference]);
    return <>
        {query.isError && <div role="alert" className="mb-2 text-xs text-red-300">Available comparison results could not be loaded.</div>}
        {query.data?.next_offset != null && <div role="status" className="mb-2 text-xs text-amber-100">Target discovery is bounded to the first {query.data.items.length} of {query.data.total} persisted results.</div>}
        <WorkbenchControls reference={reference} availableTargets={availableTargets} />
    </>;
}

export default function FrustraMpnnComparisonWorkbench({ referenceJobId, referenceInvocationId, availableTargets }: Props) {
    const reference = { parent_job_id: referenceJobId, invocation_id: referenceInvocationId };
    return availableTargets
        ? <WorkbenchControls reference={reference} availableTargets={availableTargets} />
        : <DiscoveredWorkbench reference={reference} />;
}
