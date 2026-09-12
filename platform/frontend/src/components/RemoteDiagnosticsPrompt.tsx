import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, pullRemoteJobDiagnostics, type Job, type RemoteDiagnosticsRecord } from '../lib/api';
import { remotePullError } from './remoteResultsState';

type DiagnosticsJob = Pick<Job, 'id' | 'status' | 'execution_target_id' | 'remote_attempt_id' | 'execution_source_revision' | 'execution_source_tree' | 'execution_bundle_sha256' | 'provenance'>;
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function remoteDiagnosticsState(job: DiagnosticsJob) {
    if (job.status !== 'failed' && job.status !== 'cancelled') return null;
    const identity = {
        attempt_id: job.remote_attempt_id,
        execution_target_id: job.execution_target_id,
        source_revision: job.execution_source_revision,
        source_tree: job.execution_source_tree,
        execution_envelope_sha256: job.execution_bundle_sha256,
    };
    const receipt = object(job.provenance?.remote_execution_receipt);
    const digest = receipt.result_manifest_sha256;
    if (!Object.entries(identity).every(([key, value]) => typeof value === 'string' && value.trim() !== '' && value !== 'None' && receipt[key] === value)
        || !['failed', 'cancelled', 'succeeded'].includes(String(receipt.state))
        || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) return null;
    const record = object(job.provenance?.remote_diagnostics);
    const priorIdentity = object(record.identity);
    const matches = priorIdentity.schema === 'bms.remote-result-pull.v1'
        && Object.entries(identity).every(([key, value]) => priorIdentity[key] === value)
        && record.result_manifest_sha256 === digest;
    return {
        identity, digest,
        state: matches && ['returning', 'returned', 'failed'].includes(String(record.state)) ? record.state as RemoteDiagnosticsRecord['state'] : null,
        error: matches && typeof record.error === 'string' ? record.error : null,
    };
}

export function RemoteDiagnosticsPrompt({ job }: { job: DiagnosticsJob }) {
    const client = useQueryClient();
    const eligible = remoteDiagnosticsState(job);
    // Independent detail polling also works in the prop-driven details panel. GETs
    // never resume a claim; only an explicit POST can do that after a restart.
    const queryKey = ['remote-diagnostics-job', job.id, eligible?.identity, eligible?.digest, job.provenance?.remote_diagnostics];
    const detail = useQuery({
        queryKey,
        queryFn: async () => (await api.get<Job>(`/api/jobs/${encodeURIComponent(job.id)}`)).data,
        initialData: job,
        enabled: !!eligible,
        staleTime: Infinity,
        refetchInterval: query => query.state.data && remoteDiagnosticsState(query.state.data)?.state === 'returning' ? 3000 : false,
    });
    const state = remoteDiagnosticsState(detail.data);
    const archive = useQuery({
        queryKey: ['remote-diagnostic-artifacts', job.id, state?.digest],
        queryFn: async () => (await api.get<{
            generation: number;
            artifacts: { relative_path: string; size_bytes: number; sha256: string; download_url: string }[];
        }>(`/api/jobs/${encodeURIComponent(job.id)}/remote-artifacts?diagnostics=true`)).data,
        enabled: state?.state === 'returned',
        retry: false,
    });
    const mutationKey = ['remote-diagnostics-pull', job.id];
    const active = useIsMutating({ mutationKey });
    const mutation = useMutation({
        mutationKey,
        mutationFn: () => pullRemoteJobDiagnostics(job.id),
        retry: false,
        onSuccess: response => { client.setQueryData(queryKey, response.data); },
        onSettled: async () => {
            await Promise.all([['job', job.id], ['jobs'], ['job-logs', job.id]].map(queryKey => client.invalidateQueries({ queryKey })));
        },
    });
    if (!eligible || !state) return null;
    const busy = mutation.isPending || active > 0;
    const error = mutation.error ? remotePullError(mutation.error) : state.state === 'failed' ? state.error || 'Diagnostic pull failed. Retry explicitly.' : null;
    const pull = () => {
        if (busy || state.state === 'returned' || client.isMutating({ mutationKey }) > 0) return;
        mutation.mutate();
    };
    return <section data-remote-diagnostics-job={job.id} className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 p-3">
        <p className="text-sm font-medium text-amber-200">Remote diagnostics</p>
        <p className="mt-1 text-xs text-slate-300">Retrieve failed-attempt files and logs; job remains {job.status}. This may transfer the entire attempt archive, including partial scientific files. It does not import scientific results or mark the job successful.</p>
        <p role="status" aria-live="polite" className="mt-1 text-xs text-slate-300">
            {state.state === 'returned' ? 'Diagnostics returned to the controller.' : state.state === 'returning' ? 'Diagnostic pull is returning. Check/retry safely resumes an interrupted controller claim.' : state.state === 'failed' ? 'Diagnostic retrieval failed; diagnostics remain unavailable here until retrieved.' : 'Diagnostics remain on the worker and are unavailable here until you explicitly pull them.'}
        </p>
        {error && <p role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
        {state.state === 'returned' && <details className="mt-2 text-xs text-slate-300">
            <summary>Browse/download returned diagnostics and logs</summary>
            {archive.isPending && <p>Loading verified archive…</p>}
            {archive.error && <p role="alert">{remotePullError(archive.error)}</p>}
            {archive.data && <><p>Attempt {job.remote_attempt_id}, generation {archive.data.generation}</p>
                <ul className="max-h-64 overflow-auto">{archive.data.artifacts.map(file => <li key={file.relative_path}>
                    <a href={file.download_url} download className="text-cyan-300 underline">{file.relative_path}</a> ({file.size_bytes} bytes)
                </li>)}</ul></>}
        </details>}
        {state.state !== 'returned' && <button type="button" onClick={pull} disabled={busy} aria-busy={busy} className="mt-2 rounded bg-amber-600/30 px-3 py-1 text-xs text-amber-100 disabled:opacity-50">
            {busy ? 'Requesting diagnostic pull…' : state.state === 'returning' ? 'Check/retry diagnostic pull' : error || state.state === 'failed' ? 'Retry diagnostics' : 'Pull diagnostics'}
        </button>}
    </section>;
}
