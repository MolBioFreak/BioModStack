import { useEffect } from 'react';
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import {
    activateExecutionTarget,
    deactivateExecutionTarget,
    dismissExecutionTarget,
    EXECUTION_TARGET_STORAGE_KEY,
    fetchExecutionTargets,
    refreshVastExecutionTargets,
    type ExecutionTarget,
} from '../lib/api';
import { formatDuration } from './telemetryCards';

type ExecutionTargetsQuery = Pick<UseQueryResult<Awaited<ReturnType<typeof fetchExecutionTargets>>>,
    'data' | 'error' | 'isError' | 'isSuccess'>;

function vastOperationErrorMessage(error: unknown): string {
    const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    return error instanceof Error && error.message ? error.message : 'Unknown error';
}

function sessionCost(target: ExecutionTarget): { uptime: string; cost: string } | null {
    const hourly = typeof target.pricing.hourly_rate === 'number' ? target.pricing.hourly_rate : null;
    const started = typeof target.pricing.provider_started_at === 'string' ? Date.parse(target.pricing.provider_started_at) : NaN;
    if (hourly == null || !Number.isFinite(started)) return null;
    const elapsed = Math.max(0, Date.now() - started);
    return { uptime: formatDuration(elapsed), cost: `$${(elapsed / 3_600_000 * hourly).toFixed(2)}` };
}

/** Owned Vast workers: discovery, attach, detach and list cleanup. Never creates or destroys rentals. */
export function VastWorkerFleet({ executionTargetsQuery, compact = false }: {
    executionTargetsQuery: ExecutionTargetsQuery;
    compact?: boolean;
}) {
    const queryClient = useQueryClient();
    const vastDiscoverMutation = useMutation({
        mutationFn: refreshVastExecutionTargets,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['execution-targets'] }),
    });
    const attachVastMutation = useMutation({
        mutationFn: (providerInstanceId: string) => {
            const target = executionTargetsQuery.data?.data.find(item => item.provider_instance_id === providerInstanceId);
            return activateExecutionTarget(providerInstanceId, target?.active
                ? { username: target.username ?? undefined, remote_root: target.remote_root } : undefined);
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: ['execution-targets'] }),
    });
    useEffect(() => {
        if (attachVastMutation.isError && !executionTargetsQuery.isError && executionTargetsQuery.data?.data.some(
            (target) => target.provider === 'vast'
                && target.provider_instance_id === attachVastMutation.variables
                && target.active && target.state === 'ready'
                && target.setup?.phase === 'ready' && !target.last_error,
        )) {
            attachVastMutation.reset();
        }
    }, [attachVastMutation.isError, attachVastMutation.variables, attachVastMutation.reset, executionTargetsQuery.data, executionTargetsQuery.isError]);
    const dismissVastMutation = useMutation({
        mutationFn: dismissExecutionTarget,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['execution-targets'] }),
    });
    const detachVastMutation = useMutation({
        mutationFn: deactivateExecutionTarget,
        onSuccess: (response) => {
            if (window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) === response.data.id) {
                window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
            }
            return queryClient.invalidateQueries({ queryKey: ['execution-targets'] });
        },
    });
    // Inventory refresh never changes placement. The picker blocks an unavailable selection.
    const vastTargets = executionTargetsQuery.isError
        ? []
        : (executionTargetsQuery.data?.data ?? []).filter((target) => target.provider === 'vast');

    return (
        <div className={compact ? 'space-y-2' : 'space-y-3'} aria-label="Remote workers" data-bms-worker-fleet="true">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">Remote workers</h3>
                <button
                    type="button"
                    onClick={() => vastDiscoverMutation.mutate()}
                    disabled={vastDiscoverMutation.isPending}
                    className="rounded-lg border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-primary))] px-2.5 py-1 text-[11px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] disabled:cursor-wait disabled:opacity-60"
                    title="Query Vast for running instances without creating, starting, stopping, or destroying provider resources"
                >
                    {vastDiscoverMutation.isPending ? 'Discovering Vast...' : 'Discover running Vast'}
                </button>
            </div>
            {vastDiscoverMutation.isError && (
                <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-200">
                    Vast discovery failed: {vastDiscoverMutation.error instanceof Error ? vastDiscoverMutation.error.message : 'unknown error'}
                </div>
            )}
            {vastDiscoverMutation.isSuccess && (
                <div className={`rounded-2xl border p-3 text-sm ${vastDiscoverMutation.data.data.available
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-amber-500/25 bg-amber-500/10 text-amber-200'}`} role="status">
                    {vastDiscoverMutation.data.data.available ? 'Vast discovery complete' : 'Vast inventory unavailable'}: {vastDiscoverMutation.data.data.message}
                </div>
            )}
            {executionTargetsQuery.isSuccess && vastTargets.length === 0 && (
                <div role="status" className="text-sm text-[var(--text-muted)]">No owned Vast instances. Local execution is available.</div>
            )}
            {executionTargetsQuery.isError && (
                <div role="alert" className="text-sm text-amber-200">Vast inventory unavailable or expired. Discover again; any selected worker is retained, not switched to Local.</div>
            )}
            {vastTargets.length > 0 && (
                <div className="grid gap-2 lg:grid-cols-2" aria-label="Vast workers">
                    {vastTargets.map((target) => {
                        const isReady = target.active && target.state === 'ready';
                        const isAttaching = target.state === 'probing'
                            || (attachVastMutation.isPending && attachVastMutation.variables === target.provider_instance_id);
                        const stateLabel = isReady
                            ? 'Ready'
                            : target.active ? 'Attached — runtime not ready'
                            : isAttaching
                                ? 'Checking readiness'
                                : target.state === 'unavailable'
                                    ? 'Unavailable'
                                    : target.state === 'inactive'
                                        ? 'Inactive'
                                        : 'Discovered';
                        const canAttach = !target.can_remove_from_list && !isReady && !isAttaching && Boolean(target.host && target.port);
                        const session = target.active ? sessionCost(target) : null;
                        const activeJobs = target.active_job_ids?.length ?? 0;
                        return (
                            <div
                                key={target.id}
                                className="flex flex-col items-stretch gap-3 rounded-2xl border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-secondary))] p-3 sm:flex-row sm:items-center"
                            >
                                <div className="min-w-0 flex-1 [overflow-wrap:anywhere] text-sm text-[var(--text-secondary)]">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className={`h-2 w-2 shrink-0 rounded-full ${isReady ? 'bg-emerald-400' : target.active ? 'bg-amber-400' : 'bg-slate-500'}`} aria-hidden="true" />
                                        <span className="font-semibold text-[var(--text-primary)]">
                                            {target.name ?? `Vast ${target.provider_instance_id}`}
                                        </span>
                                        <span className={isReady ? 'font-semibold text-emerald-300' : 'text-[var(--text-muted)]'}>
                                            {stateLabel}
                                        </span>
                                        {isReady && <span className="text-xs text-[var(--text-muted)]">{activeJobs === 0 ? 'idle' : `${activeJobs} active job${activeJobs === 1 ? '' : 's'}`}</span>}
                                    </div>
                                    <div className="mt-1 text-xs">
                                        {String(target.capabilities.gpu_count ?? '?')} × {String(target.capabilities.gpu_name ?? 'GPU')}
                                        {target.pricing.hourly_rate != null ? ` · $${target.pricing.hourly_rate.toFixed(3)}/hr` : ''}
                                        {session ? ` · up ${session.uptime} · ${session.cost} so far` : ''}
                                    </div>
                                    {target.setup && (target.active || target.setup.phase !== 'ready') && (
                                        <div role="status" className={`mt-1 text-xs ${target.setup.phase === 'failed' ? 'text-red-300' : 'text-[var(--text-muted)]'}`}>
                                            Setup · {target.setup.phase}: {target.setup.message}
                                        </div>
                                    )}
                                    {target.last_error && target.last_error !== target.setup?.message && (
                                        <div className="mt-1 text-xs text-red-300">{target.last_error}</div>
                                    )}
                                </div>
                                <div role="group" aria-label="Worker actions" className="flex max-w-full shrink-0 flex-wrap items-center justify-end gap-2 sm:ml-auto">
                                    {target.active && (
                                        <button
                                            type="button"
                                            onClick={() => detachVastMutation.mutate(target.id)}
                                            disabled={detachVastMutation.isPending || isAttaching}
                                            className="shrink-0 whitespace-nowrap rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-200 disabled:opacity-50"
                                        >
                                            {detachVastMutation.isPending ? 'Detaching…' : 'Detach'}
                                        </button>
                                    )}
                                    {target.can_remove_from_list && (
                                        <button
                                            type="button"
                                            onClick={() => dismissVastMutation.mutate(target.id)}
                                            disabled={dismissVastMutation.isPending}
                                            title="Hide this absent worker. Job history is retained; no rental is terminated."
                                            className="shrink-0 whitespace-nowrap rounded-xl border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-primary))] px-3 py-2 text-xs font-semibold text-[var(--text-secondary)] disabled:opacity-50"
                                        >
                                            {dismissVastMutation.isPending && dismissVastMutation.variables === target.id ? 'Removing…' : 'Remove from list'}
                                        </button>
                                    )}
                                    {!isReady && !target.can_remove_from_list && (
                                        <button
                                            type="button"
                                            onClick={() => attachVastMutation.mutate(target.provider_instance_id)}
                                            disabled={!canAttach || attachVastMutation.isPending}
                                            className="shrink-0 whitespace-nowrap rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-semibold text-emerald-200 disabled:opacity-40"
                                        >
                                            {isAttaching ? 'Attaching…' : target.active ? 'Retry setup' : 'Attach worker'}
                                        </button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
            {(executionTargetsQuery.isError || attachVastMutation.isError || detachVastMutation.isError || dismissVastMutation.isError) && (
                <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-200" role="alert">
                    {dismissVastMutation.isError ? 'Remove failed' : attachVastMutation.isError ? 'Attach failed' : detachVastMutation.isError ? 'Detach failed' : 'Worker inventory failed'}: {vastOperationErrorMessage(dismissVastMutation.error ?? attachVastMutation.error ?? detachVastMutation.error ?? executionTargetsQuery.error)}
                </div>
            )}
        </div>
    );
}
