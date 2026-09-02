import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
    activateExecutionTarget,
    deactivateExecutionTarget,
    EXECUTION_TARGET_STORAGE_KEY,
    fetchExecutionTargets,
    refreshVastExecutionTargets,
    VAST_DISCOVERY_QUERY_KEY,
} from '../lib/api';

export function ExecutionTargetPicker() {
    const queryClient = useQueryClient();
    const [selectedTargetId, setSelectedTargetId] = useState(() => (
        typeof window === 'undefined'
            ? ''
            : window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || ''
    ));
    const targetsQuery = useQuery({
        queryKey: ['execution-targets'],
        queryFn: fetchExecutionTargets,
        refetchInterval: 15_000,
    });
    const inventoryQuery = useQuery({
        queryKey: VAST_DISCOVERY_QUERY_KEY,
        queryFn: refreshVastExecutionTargets,
        enabled: false,
        staleTime: Infinity,
    });
    const activateMutation = useMutation({
        mutationFn: activateExecutionTarget,
        onSuccess: (response) => {
            const targetId = response.data.id;
            setSelectedTargetId(targetId);
            window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, targetId);
            queryClient.invalidateQueries({ queryKey: ['execution-targets'] });
        },
    });
    const deactivateMutation = useMutation({
        mutationFn: deactivateExecutionTarget,
        onSuccess: () => {
            setSelectedTargetId('');
            window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
            queryClient.invalidateQueries({ queryKey: ['execution-targets'] });
        },
    });

    const targets = targetsQuery.isError ? [] : (targetsQuery.data?.data ?? []);
    const readyTargets = useMemo(
        () => targets.filter((target) => target.active && target.state === 'ready'),
        [targets],
    );
    useEffect(() => {
        if (!selectedTargetId || targetsQuery.isLoading) return;
        if (!readyTargets.some((target) => target.id === selectedTargetId)) {
            setSelectedTargetId('');
            window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
        }
    }, [readyTargets, selectedTargetId, targetsQuery.isLoading]);

    const selectTarget = (targetId: string) => {
        setSelectedTargetId(targetId);
        if (targetId) {
            window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, targetId);
        } else {
            window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
        }
    };
    const inventory = inventoryQuery.data?.data.instances ?? [];
    const activeTarget = readyTargets.find((target) => target.id === selectedTargetId) ?? null;

    return (
        <section className="mb-5 rounded-xl border border-slate-700 bg-slate-900/80 p-4" aria-label="Execution target">
            <div>
                <div>
                    <h2 className="text-sm font-semibold text-slate-100">Execution target</h2>
                    <p className="mt-1 text-xs text-slate-400">
                        BMS keeps the Job, scheduler, lineage, and results local. A selected Vast worker executes the compiled workflow only.
                    </p>
                </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
                <button
                    type="button"
                    onClick={() => selectTarget('')}
                    className={`rounded-lg border px-3 py-2 text-sm ${selectedTargetId === ''
                        ? 'border-blue-400 bg-blue-500/15 text-blue-100'
                        : 'border-slate-700 bg-slate-950 text-slate-300'}`}
                >
                    Local
                </button>
                {readyTargets.map((target) => (
                    <button
                        key={target.id}
                        type="button"
                        onClick={() => selectTarget(target.id)}
                        className={`rounded-lg border px-3 py-2 text-sm ${selectedTargetId === target.id
                            ? 'border-emerald-400 bg-emerald-500/15 text-emerald-100'
                            : 'border-slate-700 bg-slate-950 text-slate-300'}`}
                    >
                        Vast · {target.name ?? target.provider_instance_id}
                    </button>
                ))}
            </div>

            {activeTarget && (
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
                    <span>
                        Active worker: {activeTarget.name ?? activeTarget.provider_instance_id} · {String(activeTarget.capabilities.gpu_count ?? '?')} × {String(activeTarget.capabilities.gpu_name ?? 'GPU')}
                    </span>
                    <button
                        type="button"
                        onClick={() => deactivateMutation.mutate(activeTarget.id)}
                        disabled={deactivateMutation.isPending}
                        className="rounded border border-emerald-400/40 px-2 py-1 font-semibold disabled:opacity-50"
                    >
                        Detach
                    </button>
                </div>
            )}

            {inventory.length > 0 && (
                <div className="mt-4 space-y-2">
                    {inventory.map((instance) => {
                        const existing = targets.find(
                            (target) => target.provider_instance_id === instance.provider_instance_id,
                        );
                        const canActivate = instance.provider_state.toLowerCase() === 'running' && Boolean(instance.host && instance.port);
                        return (
                            <div key={instance.provider_instance_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2">
                                <div className="text-xs text-slate-300">
                                    <div className="font-semibold text-slate-100">{instance.name ?? `Vast ${instance.provider_instance_id}`}</div>
                                    <div className="mt-1">
                                        {instance.gpu_count} × {instance.gpu_name ?? 'GPU'} · {instance.provider_state}
                                        {instance.hourly_rate_usd != null ? ` · $${instance.hourly_rate_usd.toFixed(3)}/hr` : ''}
                                    </div>
                                </div>
                                {existing?.active && existing.state === 'ready' ? (
                                    <span className="text-xs font-semibold text-emerald-300">Attached</span>
                                ) : (
                                    <button
                                        type="button"
                                        onClick={() => activateMutation.mutate(instance.provider_instance_id)}
                                        disabled={!canActivate || activateMutation.isPending}
                                        className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-semibold text-emerald-200 disabled:opacity-40"
                                    >
                                        {activateMutation.isPending ? 'Attaching…' : 'Attach worker'}
                                    </button>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {(targetsQuery.error || activateMutation.error || deactivateMutation.error) && (
                <p role="alert" className="mt-3 text-xs text-red-300">
                    Execution target operation failed. The local execution target remains authoritative.
                </p>
            )}
        </section>
    );
}
