import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { familyReferences, familyRoot, fetchBinderResultFamily } from '../lib/binderResultFamily';

export interface BinderResultFamilyProps {
    jobId: string;
    launchContextId?: string | null;
    onOpenJob: (id: string) => void;
    onCompareJobs?: (ids: string[]) => void;
}

export function BinderResultFamily({ jobId, launchContextId, onOpenJob, onCompareJobs }: BinderResultFamilyProps) {
    const [selected, setSelected] = useState<string[]>([]);
    const query = useQuery({ queryKey: ['binder-result-family', jobId],
        queryFn: ({ signal }) => fetchBinderResultFamily(jobId, signal), staleTime: 0 });
    const jobs = query.data ?? [];
    const current = jobs.find(job => job.id === jobId);
    const root = current ? familyRoot(current, jobs) : null;
    const refs = current ? familyReferences(current) : null;
    const compareIds = selected.filter(id => jobs.some(job => job.id === id));
    const link = (id: string) => <button type="button" className="text-sky-300 underline" onClick={() => onOpenJob(id)}>{jobs.find(job => job.id === id)?.name ?? id}</button>;
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
