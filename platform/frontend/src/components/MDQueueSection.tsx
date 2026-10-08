import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { fetchMDQueue, fetchMDRun, type MDQueueRun } from '../lib/api';
import { jobPollingInterval } from '../lib/queryPolling';

const MD_QUEUE_LIMIT = 25;
const TERMINAL_PHASES = new Set(['completed', 'partial', 'failed', 'cancelled']);

function phaseLabel(phase: string): string {
    return phase.replaceAll('_', ' ');
}

function replicaSummary(run: MDQueueRun): string {
    const states = Object.entries(run.replica_summary);
    if (states.length === 0) return `${run.replica_count} replicas planned`;
    return states.map(([state, count]) => `${count} ${phaseLabel(state)}`).join(' · ');
}

function MDOperationalDetails({ jobId }: { jobId: string }) {
    const detail = useQuery({
        queryKey: ['md-run', jobId],
        queryFn: () => fetchMDRun(jobId),
        retry: false,
        refetchInterval: (query) => (
            query.state.data?.data.phase && !TERMINAL_PHASES.has(query.state.data.data.phase) ? 5_000 : false
        ),
    });

    if (detail.isLoading) {
        return <div className="mt-3 text-xs text-slate-400">Loading operational detail…</div>;
    }
    if (detail.isError || !detail.data?.data) {
        return <div role="alert" className="mt-3 text-xs text-rose-300">MD operational detail is unavailable.</div>;
    }

    const run = detail.data.data;
    return (
        <div className="mt-3 space-y-3 border-t border-cyan-500/20 pt-3" data-bms-md-operational-detail={jobId}>
            <div className="grid gap-2 text-xs text-slate-300 sm:grid-cols-3">
                <div><span className="text-slate-500">State version</span> {run.state_version}</div>
                <div><span className="text-slate-500">Accepted checkpoints</span> {run.checkpoints.length}</div>
                <div><span className="text-slate-500">Available operations</span> {run.allowed_actions.length ? run.allowed_actions.map(phaseLabel).join(', ') : 'none'}</div>
            </div>
            <div className="flex flex-wrap gap-2">
                {run.replicas.map((replica) => (
                    <span key={replica.id} className="rounded border border-slate-700 bg-slate-950/50 px-2 py-1 text-xs text-slate-300">
                        Replica {replica.replica_index} · attempt {replica.attempt} · {phaseLabel(replica.state)}
                    </span>
                ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="text-slate-500">Lifecycle controls and governed results remain owned by the MD results surface.</span>
                <Link
                    to={`/designs/${jobId}`}
                    className="rounded border border-cyan-400/40 bg-cyan-500/10 px-3 py-1.5 font-medium text-cyan-100 hover:bg-cyan-500/20"
                >
                    Open MD operations
                </Link>
            </div>
        </div>
    );
}

function MDQueueRow({ run }: { run: MDQueueRun }) {
    const [expanded, setExpanded] = useState(false);
    return (
        <article className="rounded-lg border border-cyan-500/20 bg-slate-900/60 p-3" data-bms-md-queue-run={run.job_id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-cyan-500/20 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-cyan-200">MD</span>
                        <span className="truncate font-medium text-white">{run.name}</span>
                        <span className="rounded bg-blue-500/20 px-2 py-0.5 text-xs text-blue-300">{phaseLabel(run.phase)}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
                        <span>{run.engine}</span>
                        <span>{replicaSummary(run)}</span>
                        <span>{run.simulated_time_ps.toFixed(2)} / {run.requested_time_ps.toFixed(2)} ps</span>
                        <span>{run.chemistry.profile_id}</span>
                    </div>
                </div>
                <button
                    type="button"
                    data-bms-md-queue-details={run.job_id}
                    aria-expanded={expanded}
                    onClick={() => setExpanded((value) => !value)}
                    className="rounded border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:border-cyan-500/50"
                >
                    {expanded ? 'Hide details' : 'Operational details'}
                </button>
            </div>
            {expanded ? <MDOperationalDetails jobId={run.job_id} /> : null}
        </article>
    );
}

export function MDQueueSection() {
    const queue = useQuery({
        queryKey: ['md-queue', MD_QUEUE_LIMIT],
        queryFn: () => fetchMDQueue(MD_QUEUE_LIMIT),
        retry: false,
        refetchInterval: (query) => jobPollingInterval(
            query.state.data?.data.runs.some((run) => !TERMINAL_PHASES.has(run.phase)) ? 5_000 : 15_000,
            query,
        ),
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    return (
        <section className="mb-4 rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-3" data-bms-md-queue="true">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h3 className="text-sm font-semibold text-cyan-100">Molecular Dynamics Queue</h3>
                    <p className="mt-0.5 text-[11px] text-slate-500">Dedicated bounded operational projection · newest {MD_QUEUE_LIMIT} runs maximum</p>
                </div>
                {queue.data?.data ? (
                    <span className="rounded bg-cyan-500/15 px-2 py-1 text-xs text-cyan-200">{queue.data.data.count} MD runs</span>
                ) : null}
            </div>
            {queue.isLoading ? (
                <div className="py-3 text-center text-xs text-slate-400">Loading MD queue…</div>
            ) : queue.isError ? (
                <div role="alert" className="py-3 text-center text-xs text-rose-300">MD queue is unavailable.</div>
            ) : queue.data?.data.runs.length ? (
                <div className="space-y-2">{queue.data.data.runs.map((run) => <MDQueueRow key={run.job_id} run={run} />)}</div>
            ) : (
                <div className="py-3 text-center text-xs text-slate-500">No durable MD runs yet</div>
            )}
        </section>
    );
}
