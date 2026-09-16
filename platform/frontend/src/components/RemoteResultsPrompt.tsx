import { useEffect, useRef } from 'react';
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query';
import { pullRemoteJobResults } from '../lib/api';
import { remotePullError, remoteResultQueryKeys, remoteResultsState, type RemoteResultsJob } from './remoteResultsState';

export function RemoteResultsPrompt({ job }: { job: RemoteResultsJob }) {
    const queryClient = useQueryClient();
    const clickInFlight = useRef(false);
    const mutationKey = ['remote-results-pull', job.id];
    const activePulls = useIsMutating({ mutationKey });
    const state = remoteResultsState(job);
    const wasAwaitingResults = useRef(false);
    useEffect(() => {
        if (state) wasAwaitingResults.current = true;
        if (wasAwaitingResults.current && job.status === 'completed') {
            wasAwaitingResults.current = false;
            for (const queryKey of remoteResultQueryKeys(job.id)) {
                void queryClient.invalidateQueries({ queryKey });
            }
        }
    }, [job.id, job.status, state, queryClient]);
    const mutation = useMutation({
        mutationKey,
        mutationFn: () => pullRemoteJobResults(job.id),
        retry: false, // Retrying a transfer always requires a new operator click.
        onSettled: async () => {
            await Promise.all(remoteResultQueryKeys(job.id).map(queryKey => queryClient.invalidateQueries({ queryKey })));
        },
    });
    if (!state) return null;
    const busy = state.busy || mutation.isPending || activePulls > 0;
    const error = remotePullError(mutation.error)
        || (state.failed ? job.error_message || job.remote_waiting_reason || (state.received ? 'Native import failed; verified bytes are retained locally.' : 'Result pull failed. Results remain on the worker.') : null);
    const handlePull = () => {
        if (busy || clickInFlight.current || queryClient.isMutating({ mutationKey }) > 0) return;
        clickInFlight.current = true;
        mutation.mutate(undefined, { onSettled: () => { clickInFlight.current = false; } });
    };
    return (
        <div className="mt-2 rounded border border-emerald-500/30 bg-emerald-500/10 p-3" data-remote-results-job={job.id}>
            <p className="text-sm font-medium text-emerald-300">{state.received ? 'Results received; native import pending' : 'Results ready on worker'}</p>
            <p role="status" aria-live="polite" className="mt-1 text-xs text-slate-300">
                {state.received
                    ? busy ? 'Importing received results…' : 'Verified bytes are retained locally. Retry native import without rerunning science or contacting the worker.'
                    : busy ? 'Execution finished; pulling results from worker and importing outputs…' : 'Execution finished; results remain on worker. Pull only when you are ready.'}
            </p>
            {error && <p role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
            <button type="button" onClick={handlePull} disabled={busy} aria-busy={busy}
                className="mt-2 rounded bg-emerald-600/30 px-3 py-1 text-xs font-medium text-emerald-200 hover:bg-emerald-600/50 disabled:opacity-50">
                {busy ? state.received ? 'Importing received results…' : 'Pulling results…' : error ? state.received ? 'Retry import' : 'Retry pull' : state.label}
            </button>
        </div>
    );
}
