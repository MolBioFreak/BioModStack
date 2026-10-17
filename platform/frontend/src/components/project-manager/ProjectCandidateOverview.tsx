import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { CandidateAttempt, ProjectCandidateData, ValidationKind } from '../../lib/projectCandidateOverview';
export { useProjectCandidateData } from '../../lib/projectCandidateOverview';

const stageLabels: Record<ValidationKind, string> = { monomer: 'Monomer', trimer1: 'Trimer · 1 binder', trimer3: 'Trimer · 3 binders', other: 'Other validation' };
const failed = (a: CandidateAttempt) => a.status === 'failed' || a.status === 'cancelled';
function AttemptLinks({ attempts }: { attempts: CandidateAttempt[] }) {
    if (!attempts.length) return <span className="text-content-muted" title="No linked result in this inventory; this does not establish that it was not run.">—</span>;
    return <ul className="space-y-2">{attempts.map((a, index) => <li key={a.id}>
        <Link className="text-accent hover:underline" to={a.href} title={`Job ${a.id}${a.parentId ? `; recorded parent ${a.parentId}` : ''}${a.retryOf ? `; retry of ${a.retryOf}` : ''}`}>
            {a.status === 'completed' ? 'View result' : a.status} {attempts.length > 1 ? index + 1 : ''}
        </Link><div className="text-xs text-content-secondary">{a.model}{a.retryOf ? ' · retry' : ''}</div>
    </li>)}</ul>;
}
export function ProjectCandidateOverview({ data, onOpenRecords }: { data: ProjectCandidateData; onOpenRecords: () => void }) {
    const [params, setParams] = useSearchParams();
    const [query, setQuery] = useState(() => params.get('candidate_search') ?? '');
    const [source, setSource] = useState(() => params.get('candidate_source') ?? '');
    const [family, setFamily] = useState(() => params.get('candidate_family') ?? '');
    // Keep typing synchronous; router navigation may be scheduled as a transition.
    // Persist the committed filters for result -> Back and cold reopening.
    useEffect(() => {
        const next = new URLSearchParams(params);
        for (const [key, value] of Object.entries({ candidate_search: query, candidate_source: source, candidate_family: family })) {
            if (value) next.set(key, value); else next.delete(key);
        }
        if (next.toString() !== params.toString()) setParams(next, { replace: true });
    }, [query, source, family, params, setParams]);
    const families = useMemo(() => [...new Map(data.candidates.filter(c => !source || c.sourceId === source).map(c => [c.family, c.familyLabel])).entries()], [data.candidates, source]);
    const candidates = data.candidates.filter(c => (!source || c.sourceId === source) && (!family || c.family === family)
        && `${c.sourceLabel} ${c.familyLabel} ${c.variant} ${c.nativeId} ${c.length ?? ''}`.toLowerCase().includes(query.toLowerCase().trim()));
    return <section className="w-full min-w-0 space-y-5 p-4 md:p-6" aria-label="Candidate overview">
        <header className="flex flex-wrap items-start justify-between gap-4">
            <div><h2 className="text-xl font-semibold">Candidates with validation work</h2>
                <p className="mt-1 text-sm text-content-secondary">Compare the same candidate across target states. These are related validations, not a causal parent chain.</p></div>
            <button type="button" onClick={onOpenRecords} className="rounded border border-border-primary px-3 py-2 text-sm">All project records</button>
        </header>
        <div className="flex flex-wrap gap-3">{data.sources.map((s, index) => <Link key={s.id} to={`/designs/${encodeURIComponent(s.id)}`} title={s.label}
            className="rounded-lg border border-border-primary bg-surface-secondary px-4 py-3 text-sm hover:border-accent">
            <span className="block font-medium">{s.label.length <= 60 ? s.label : `BC2 source ${index + 1}`}</span><span className="text-accent">Open all original BC2 outputs →</span>
        </Link>)}</div>
        {data.errors.length > 0 && <div role="alert" className="rounded border border-amber-700 p-3 text-sm">Some native reads are unavailable. This is a partial overview; use Records or refresh to retry.<ul>{data.errors.map(e => <li key={e}>{e}</li>)}</ul></div>}
        <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-48 flex-1 text-sm">Search candidates<input aria-label="Search candidates" value={query} onChange={e => setQuery(e.target.value)} placeholder="Family, variant, length or native ID" className="mt-1 block w-full rounded border border-border-primary bg-surface p-2" /></label>
            <label className="text-sm">Source / site<select aria-label="Source / site" value={source} onChange={e => { setSource(e.target.value); setFamily(''); }} className="mt-1 block rounded border border-border-primary bg-surface p-2"><option value="">All sources</option>{data.sources.map((s, i) => <option key={s.id} value={s.id}>{s.label.length <= 60 ? s.label : `BC2 source ${i + 1}`}</option>)}</select></label>
            <label className="text-sm">Family<select aria-label="Family" value={family} onChange={e => setFamily(e.target.value)} className="mt-1 block rounded border border-border-primary bg-surface p-2"><option value="">All families</option>{families.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        </div>
        <p className="text-sm text-content-secondary">Showing {candidates.length} of {data.candidates.length} candidates with linked validation work. Original output links include candidates outside this table. Completed means execution completed, not scientific acceptance.</p>
        <div className="w-full overflow-x-auto rounded-lg border border-border-primary">
            <table className="w-full min-w-[980px] text-left text-sm">
                <caption className="sr-only">Original BC2 candidates and their independently linked validation results</caption>
                <thead className="bg-surface-secondary"><tr>{['Source / site', 'Candidate', 'Original BC2 candidate', 'Monomer result', 'Trimer · 1 binder', 'Trimer · 3 binders', 'Other / history'].map(h => <th key={h} scope="col" className="px-4 py-3 font-medium">{h}</th>)}</tr></thead>
                <tbody>{candidates.map(c => <tr key={c.key} className="border-t border-border-primary align-top">
                    <td className="max-w-48 px-4 py-4"><span title={c.sourceLabel}>{c.sourceLabel.length <= 60 ? c.sourceLabel : `BC2 source ${data.sources.findIndex(s => s.id === c.sourceId) + 1}`}</span></td>
                    <th scope="row" className="px-4 py-4 font-normal"><div className="font-semibold">{c.familyLabel}</div><div>{c.variant}{c.length !== null ? ` · ${c.length} aa` : ''}</div></th>
                    <td className="max-w-56 px-4 py-4"><Link to={c.href} className="text-accent hover:underline">Open exact candidate</Link><details className="mt-2 text-xs text-content-secondary"><summary className="cursor-pointer">Native identity</summary><code className="break-all">{c.nativeId}</code></details></td>
                    {(['monomer', 'trimer1', 'trimer3'] as const).map(kind => <td key={kind} className="px-4 py-4"><AttemptLinks attempts={c.attempts.filter(a => a.kind === kind && !failed(a))} /></td>)}
                    <td className="px-4 py-4">{c.attempts.some(a => a.kind === 'other' && !failed(a)) && <details><summary className="cursor-pointer">Other validation</summary><AttemptLinks attempts={c.attempts.filter(a => a.kind === 'other' && !failed(a))} /></details>}
                        {c.attempts.some(failed) ? <details className="mt-1"><summary className="cursor-pointer text-content-secondary">Failed / cancelled ({c.attempts.filter(failed).length})</summary>{c.attempts.filter(failed).map(a => <div key={a.id} className="mt-2"><span className="text-xs text-content-secondary">{stageLabels[a.kind]}</span><AttemptLinks attempts={[a]} /></div>)}</details> : '—'}</td>
                </tr>)}</tbody>
            </table>
            {!candidates.length && <p className="p-6 text-content-secondary">No candidates match this view. Other work and unresolved candidate links remain in Records.</p>}
        </div>
        <p className="text-xs text-content-secondary">“—” means no linked result shown, not “not run”. {data.unlinkedJobCount > 0 ? `${data.unlinkedJobCount} related jobs could not be assigned to an exact candidate here. ` : ''}Other sources, target references and full provenance remain in <button type="button" onClick={onOpenRecords} className="text-accent underline">Records</button>.</p>
    </section>;
}
