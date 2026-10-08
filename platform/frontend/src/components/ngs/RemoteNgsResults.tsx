import { useMutation, useQueryClient } from '@tanstack/react-query';
import { pullRemoteNgsResults, type Job } from '../../lib/api';

export function RemoteNgsResults({ job }: { job: Job }) {
    const queries = useQueryClient();
    const pull = useMutation({
        mutationFn: () => pullRemoteNgsResults(job.id, job.remote_attempt_id!),
        onSuccess: () => { void queries.invalidateQueries({ queryKey: ['jobs'] }); void queries.invalidateQueries({ queryKey: ['full-job', job.id] }); },
    });
    if (job.remote_state !== 'remote_finished_results_waiting' || !job.remote_attempt_id) return null;
    return <section aria-label="Remote NGS results" className="rounded border p-3 space-y-2">
        <p>Execution stopped on the selected worker. Scientific artifacts remain remote until you pull them. Native validation runs before local acceptance; no analysis is rerun.</p>
        <button type="button" disabled={pull.isPending} onClick={() => pull.mutate()} className="rounded border px-3 py-2">
            {pull.isPending ? 'Pulling and validating…' : 'Pull remote results'}
        </button>
        {pull.isError && <p role="alert">{pull.error instanceof Error ? pull.error.message : 'Transfer failed. Remote artifacts are retained; retry explicitly.'}</p>}
    </section>;
}
