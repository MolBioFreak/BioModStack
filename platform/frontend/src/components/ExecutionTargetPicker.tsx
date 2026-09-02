import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
    EXECUTION_TARGET_STORAGE_KEY,
    fetchExecutionTargets,
} from '../lib/api';

export function ExecutionTargetPicker() {
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

    return (
        <section className="mb-5 rounded-xl border border-slate-700 bg-slate-900/80 p-4" aria-label="Execution target">
            <div>
                <h2 className="text-sm font-semibold text-slate-100">Execution target</h2>
                <p className="mt-1 text-xs text-slate-400">
                    BMS keeps the Job, scheduler, lineage, and results local. A selected Vast worker executes the compiled workflow only.
                </p>
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

            {targetsQuery.error && (
                <p role="alert" className="mt-3 text-xs text-red-300">
                    Execution targets are unavailable. Local remains authoritative.
                </p>
            )}
        </section>
    );
}
