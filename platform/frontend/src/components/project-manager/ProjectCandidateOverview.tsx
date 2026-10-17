import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { ProjectCandidateData } from '../../lib/binderResultFamily';
export { useProjectCandidateData } from '../../lib/projectCandidateOverview';

const failed = (a: { status: string }) => a.status === 'failed' || a.status === 'cancelled';
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
            <div><h2 className="text-xl font-semibold">Backbones & sequences</h2>
                <p className="mt-1 text-sm text-content-secondary">This is a link index. Structures, scores and analysis stay in their existing results workspaces.</p></div>
            <button type="button" onClick={onOpenRecords} className="rounded border border-border-primary px-3 py-2 text-sm">Experiments & project records</button>
        </header>
        <div className="flex flex-wrap gap-3">{data.sources.map((s, index) => <Link key={s.id} to={`/designs/${encodeURIComponent(s.id)}`} title={s.label}
            className="rounded-lg border border-border-primary bg-surface-secondary px-4 py-3 text-sm hover:border-accent">
            <span className="block font-medium">{s.label.length <= 60 ? s.label : `BC2 source ${index + 1}`}</span><span className="text-accent">Open all original BC2 outputs →</span>
        </Link>)}</div>
        {data.errors.length > 0 && <div role="alert" className="rounded border border-amber-700 p-3 text-sm">Some native reads are unavailable. This is a partial overview; use Records or refresh to retry.<ul>{data.errors.map(e => <li key={e}>{e}</li>)}</ul></div>}
        <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-48 flex-1 text-sm">Search candidates<input aria-label="Search candidates" value={query} onChange={e => setQuery(e.target.value)} placeholder="Design number, variant or length" className="mt-1 block w-full rounded border border-border-primary bg-surface p-2" /></label>
            <label className="text-sm">Source / site<select aria-label="Source / site" value={source} onChange={e => { setSource(e.target.value); setFamily(''); }} className="mt-1 block rounded border border-border-primary bg-surface p-2"><option value="">All sources</option>{data.sources.map((s, i) => <option key={s.id} value={s.id}>{s.label.length <= 60 ? s.label : `BC2 source ${i + 1}`}</option>)}</select></label>
            <label className="text-sm">Design<select aria-label="Family" value={family} onChange={e => setFamily(e.target.value)} className="mt-1 block rounded border border-border-primary bg-surface p-2"><option value="">All designs</option>{families.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        </div>
        <p className="text-sm text-content-secondary">Showing {candidates.length} of {data.candidates.length} sequences with linked work. Open the original outputs above for the complete source runs. Completed means execution completed, not scientific acceptance.</p>
        <div className="w-full overflow-x-auto rounded-lg border border-border-primary">
            <table className="w-full min-w-[980px] text-left text-sm">
                <caption className="sr-only">Original BC2 candidates and their independently linked validation results</caption>
                <thead className="bg-surface-secondary"><tr>{['Source', 'Backbone', 'Sequence', 'Results workspace', 'Linked work'].map(h => <th key={h} scope="col" className="px-4 py-3 font-medium">{h}</th>)}</tr></thead>
                <tbody>{candidates.map(c => <tr key={c.key} className="border-t border-border-primary align-top">
                    <td className="max-w-48 px-4 py-4"><span>{c.sourceLabel}</span></td>
                    <th scope="row" className="px-4 py-4 font-normal"><Link to={c.backboneHref ?? c.href} className="font-semibold text-accent hover:underline">{c.familyLabel}</Link></th>
                    <td className="px-4 py-4"><Link to={c.href} className="text-accent hover:underline">{c.variant}{c.length !== null ? ` · ${c.length} aa` : ''}</Link><details className="mt-2 text-xs text-content-secondary"><summary className="cursor-pointer">Original identity</summary><code className="break-all">{c.nativeId}</code></details></td>
                    <td className="px-4 py-4"><Link to={c.href} className="text-accent hover:underline">Open structure & all results</Link></td>
                    <td className="px-4 py-4">
                        <details><summary className="cursor-pointer text-accent">{c.attempts.filter(a => !failed(a)).length} result {c.attempts.filter(a => !failed(a)).length === 1 ? 'link' : 'links'}</summary><ul className="mt-2 space-y-2">{c.attempts.filter(a => !failed(a)).map(a => <li key={a.id}><Link to={a.href} className="text-accent hover:underline">{a.label ?? 'Result'} · {a.model}</Link></li>)}</ul></details>
                        {c.attempts.some(failed) && <details className="mt-2"><summary className="cursor-pointer text-content-secondary">Attempt history ({c.attempts.filter(failed).length})</summary><ul className="mt-2 space-y-2">{c.attempts.filter(failed).map(a => <li key={a.id}><Link to={a.href} className="text-accent hover:underline">{a.label ?? 'Attempt'} · {a.status}</Link></li>)}</ul></details>}
                    </td>
                </tr>)}</tbody>
            </table>
            {!candidates.length && <p className="p-6 text-content-secondary">No candidates match this view. Other work and unresolved candidate links remain in Records.</p>}
        </div>
        <p className="text-xs text-content-secondary">{data.unlinkedJobCount > 0 ? `${data.unlinkedJobCount} related jobs could not be assigned to an exact candidate here. ` : ''}Other sources, target references and full provenance remain in <button type="button" onClick={onOpenRecords} className="text-accent underline">Records</button>.</p>
    </section>;
}
