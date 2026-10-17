import { useState } from 'react';
import { predictionJobLabel } from '../lib/designFriendlyLabel';
import { useQuery } from '@tanstack/react-query';
import { familyReferences, familyRoot, fetchBinderResultFamily, buildProjectCandidateData, fetchBinderCandidateData } from '../lib/binderResultFamily';
import { object } from '../lib/bindcraft2Results';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

export interface BinderResultFamilyProps {
    jobId: string;
    launchContextId?: string | null;
    onOpenJob?: (id: string) => void;
    candidate?: NativeGenerationRecord;
    candidateLabel?: string;
    onCompareJobs?: (ids: string[]) => void;
}

export function BinderResultFamily({ jobId, launchContextId, onOpenJob, onCompareJobs, candidate, candidateLabel }: BinderResultFamilyProps) {
    const [selected, setSelected] = useState<string[]>([]);
    const query = useQuery({ queryKey: ['binder-result-family', jobId],
        queryFn: ({ signal }) => fetchBinderResultFamily(jobId, signal), staleTime: 0 });
    const backboneId = candidate && object(candidate.native_record).stage === 'trajectory' ? String(object(candidate.native_record).design ?? '') : '';
    const backbone = useQuery({ queryKey: ['binder-candidate-links', jobId], enabled: Boolean(backboneId),
        queryFn: ({ signal }) => fetchBinderCandidateData([{ id: jobId, label: 'Source' }], signal), retry: false, refetchOnWindowFocus: false });
    const jobs = query.data ?? [];
    const current = jobs.find(job => job.id === jobId);
    const root = current ? familyRoot(current, jobs) : null;
    const refs = current ? familyReferences(current) : null;
    const compareIds = selected.filter(id => jobs.some(job => job.id === id));
    const link = (id: string) => <button type="button" className="text-sky-300 underline" onClick={() => onOpenJob ? onOpenJob(id) : window.location.assign(`/designs/${encodeURIComponent(id)}`)}>{jobs.find(job => job.id === id) ? predictionJobLabel(jobs.find(job => job.id === id)!) : 'Related run'}</button>;
    if (backboneId) {
        const sequences = backbone.data?.candidates.filter(c => c.family === backboneId) ?? [];
        return <section aria-label="Backbone sequence and experiment links" className="rounded-lg border border-[var(--border-color)] p-3 text-sm">
            <h4 className="font-semibold">Sequences & linked work</h4>
            {backbone.isPending && <p role="status">Loading sequence links…</p>}
            {backbone.isError && <p role="status">Sequence links could not be loaded. <button className="underline" onClick={() => void backbone.refetch()}>Retry</button></p>}
            <ul>{sequences.map(c => <li key={c.key} className="mt-3 border-t border-[var(--border-color)] pt-3">
                <a href={c.href} className="text-accent hover:underline">{c.variant}{c.length ? ` · ${c.length} aa` : ''}</a>
                <p className="text-xs text-[var(--text-secondary)]">{c.attempts.length} linked {c.attempts.length === 1 ? 'run' : 'runs'} · open structure and results</p>
            </li>)}</ul>
            {backbone.isSuccess && !sequences.length && <p>No candidate-linked follow-up found for this backbone.</p>}
        </section>;
    }
    if (candidate) {
        const native = object(candidate.native_record);
        const draw = { ...native, design: native.scored_design ?? native.design,
            sequence: native.sequence ?? candidate.sequence };
        const result = current ? buildProjectCandidateData([{ id: jobId, label: 'Source', job: current, draws: [draw] }], jobs) : undefined;
        const attempts = [...(result?.candidates[0]?.attempts ?? [])].sort((a, b) => (a.createdAt ?? '').localeCompare(b.createdAt ?? '') || a.id.localeCompare(b.id));
        const failed = attempts.filter(run => run.status === 'failed' || run.status === 'cancelled');
        const completed = attempts.filter(run => !failed.includes(run));
        const returnTo = `${window.location.pathname}${window.location.search}`;
        const resultLink = (run: typeof attempts[number], index: number, list: typeof attempts) => {
            const url = new URL(run.href, window.location.origin);
            if (run.status === 'completed') {
                url.searchParams.set('return_to', returnTo);
                url.searchParams.set('return_label', candidateLabel ?? 'BC2 candidate');
            }
            const repeated = list.filter(item => item.label === run.label && item.model === run.model).length > 1;
            return <li key={run.id} className="border-t border-[var(--border-color)] py-3">
                <a href={`${url.pathname}${url.search}`} className="text-accent hover:underline" title={jobs.find(job => job.id === run.id)?.name}>
                    {run.label ?? 'Follow-up result'}{repeated ? ` · Run ${index + 1}` : ''}
                </a>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">{run.model} · {run.status}{run.retryOf ? ' after retry' : ''}</p>
            </li>;
        };
        return <section aria-label="Candidate validation and follow-up results" className="min-w-0 rounded-lg border border-[var(--border-color)] p-3 text-sm">
            <h4 className="font-semibold">Validation & follow-up</h4>
            <p className="mt-1 mb-2 text-xs text-[var(--text-secondary)]">All linked runs for this candidate, across models and target states.</p>
            {query.isPending && <p role="status">Loading linked results…</p>}
            {query.isError && <p role="alert">Linked results could not be loaded. <button type="button" className="underline" onClick={() => void query.refetch()}>Retry</button></p>}
            {!!completed.length && <ul>{completed.map((run, index) => resultLink(run, index, completed))}</ul>}
            {!!failed.length && <details><summary className="cursor-pointer">Earlier attempts ({failed.length})</summary><ul>{failed.map((run, index) => resultLink(run, index, failed))}</ul></details>}
            {!query.isPending && !query.isError && !attempts.length && <p className="text-[var(--text-secondary)]">No linked validation or follow-up found for this candidate.</p>}
        </section>;
    }
    return <details aria-label="Scientific result family" className="mb-2 rounded-lg border border-[var(--border-color)] p-3 text-sm" title={launchContextId ? `Destination: ${launchContextId}` : undefined}>
        <summary className="cursor-pointer font-medium">Campaign history{jobs.length > 0 ? ` (${jobs.length} runs)` : ''}</summary>
        <button type="button" className="float-right underline" onClick={() => void query.refetch()}>Refresh family</button>
        {query.isPending && <p role="status">Loading family…</p>}
        {query.isError && <p role="alert">Family could not be loaded. Refresh to retry.</p>}
        {root && <p>Root: {link(root)}{refs?.source && <> · Source: {link(refs.source)}</>}</p>}
        {refs?.scheduler && <p className="text-xs">Execution parent: {link(refs.scheduler)}</p>}
        {!query.isPending && !query.isError && !current && <p>Current job is no longer in the persisted listing.</p>}
        <ul className="mt-2 max-h-48 overflow-auto space-y-1">
            {jobs.map(job => <li key={job.id} aria-current={job.id === jobId ? 'true' : undefined}>
                {onCompareJobs && <input type="checkbox" aria-label={`Compare ${job.name}`} checked={compareIds.includes(job.id)}
                    onChange={event => setSelected(ids => event.target.checked ? [...ids, job.id] : ids.filter(id => id !== job.id))} />}
                {' '}{link(job.id)} · {job.model_id} · {job.status}
                {job.id === jobId ? ' · Current' : job.id === root ? ' · Root' : ' · Related output'}
            </li>)}
        </ul>
        {onCompareJobs && <button type="button" disabled={compareIds.length < 2} onClick={() => onCompareJobs(compareIds)}>Compare selected jobs</button>}
    </details>;
}
