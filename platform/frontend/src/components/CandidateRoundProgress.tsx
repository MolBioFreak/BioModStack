import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, submitJob, type Job } from '../lib/api';
import { evidenceText } from '../lib/binderEvidence';
import { familyReferences, familyRoot, fetchBinderResultFamily } from '../lib/binderResultFamily';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';

const control = 'rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm';

interface RoundStep { state: string; job_id?: string; error?: string; superseded_by?: string; metadata?: Record<string, unknown>; request?: Partial<Job>; review?: unknown }
interface RoundReadback { job_id: string; state: string; steps: Record<string, RoundStep>; errors: Record<string, unknown> }
export function CandidateRoundProgress({ jobId, kind = 'binder' }: { jobId: string; kind?: 'binder' | 'sequence' }) {
    const sequenceOnly = kind === 'sequence';
    const cache = useQueryClient();
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const url = `/api/binder-continuation/${encodeURIComponent(jobId)}/round`;
    const query = useQuery({ queryKey: ['binder-round-progress', jobId], retry: false, refetchInterval: 5000,
        queryFn: async ({ signal }) => {
            const { data } = await api.get<RoundReadback>(url, { signal });
            if (typeof data.job_id !== 'string' || !data.job_id || typeof data.state !== 'string' || !data.steps || Array.isArray(data.steps) || typeof data.steps !== 'object') throw Error('Round progress readback unavailable');
            if (data.job_id !== jobId) {
                // This endpoint resolves the scientific root, not the viewed Job.
                // Bind a differing identity to the existing native family context;
                // an arbitrary successful response is not a valid root readback.
                const family = await cache.fetchQuery({ queryKey: ['binder-result-family', jobId],
                    queryFn: ({ signal }) => fetchBinderResultFamily(jobId, signal), staleTime: 30_000 });
                const source = family.find(job => job.id === jobId);
                const root = family.find(job => job.id === data.job_id);
                const rootRefs = root && familyReferences(root);
                const rootParent = rootRefs && (rootRefs.root ?? rootRefs.source ?? rootRefs.scheduler);
                if (!source || !root || familyRoot(source, family) !== data.job_id || (rootParent && rootParent !== data.job_id)) throw Error('Round root context unavailable');
            }
            return data;
        } });
    const act = async (request?: Partial<Job>) => {
        setBusy(true); setError('');
        try {
            if (request) await submitJob(request, { launchContext: Boolean(request.launch_context_id) });
            else await api.post(`${url}/retry`, {});
            const readback = await query.refetch();
            if (readback.isError) throw Error('Action returned, but round readback failed. Refresh to inspect its persisted state.');
            await cache.invalidateQueries({ queryKey: ['binder-evidence', jobId] });
        } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
        finally { setBusy(false); }
    };
    if (query.isError) return <p role="status">Round progress unavailable. <button type="button" onClick={() => void query.refetch()}>Refresh round progress</button></p>;
    const progress = query.data;
    if (!progress) return <p role="status">Reading round progress…</p>;
    return <section aria-label={sequenceOnly ? 'Sequence design progress' : 'Binder round progress'} className="space-y-2">
        <p>{sequenceOnly ? 'Sequence design' : 'Round'}: {progress.state}</p>
        {progress.job_id !== jobId && <p className="text-xs">Progress for the resolved workflow root. <a className="underline" href={`/designs/${encodeURIComponent(progress.job_id)}`}>Open root results</a></p>}
        {Object.entries(progress.errors ?? {}).map(([source, reason]) => <p key={source} role="status">Source {source}: {evidenceText(reason)}</p>)}
        {Object.entries(progress.steps).map(([id, step]) => <div key={id} className="text-sm">
            <span>{evidenceText(step.metadata?.stage)} · source {evidenceText(step.metadata?.source_design_id)}{!sequenceOnly && <> · target {evidenceText(step.metadata?.target_state)}</>} · {step.state}</span>
            {step.job_id && <><a className="ml-2 underline" href={`/jobs/${encodeURIComponent(step.job_id)}`}>Open child Job</a><a className="ml-2 underline" href={`/designs/${encodeURIComponent(step.job_id)}`}>Open results</a></>}
            {step.error && <p role="status">{step.error}</p>}
            {step.superseded_by && <p>Retained history; retry step {step.superseded_by}</p>}
            {step.state === 'review_required' && step.request && <><button className={control} type="button" disabled={busy} onClick={() => void act(step.request)}>Review prepared remote step {id}</button><details><summary>Retained preparation</summary><BindCraft2SettingsReadback value={{ request: step.request, review: step.review }} /></details></>}
        </div>)}
        {['needs_retry', 'completed_with_errors'].includes(progress.state) && <button className={control} type="button" disabled={busy} onClick={() => void act()}>{sequenceOnly ? 'Retry sequence design' : 'Retry binder round'}</button>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
