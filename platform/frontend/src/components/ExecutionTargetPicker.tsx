import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import {
    EXECUTION_TARGET_STORAGE_KEY,
    fetchExecutionTargets,
} from '../lib/api';

interface ExecutionTargetPickerProps {
    value?: string | null;
    onChange?: (targetId: string | null) => void;
    disabled?: boolean;
}

export function ExecutionTargetPicker({ value, onChange, disabled = false }: ExecutionTargetPickerProps = {}) {
    const controlled = value !== undefined;
    const [storedTargetId, setSelectedTargetId] = useState(() => (
        controlled || typeof window === 'undefined'
            ? ''
            : window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || ''
    ));
    const selectedTargetId = controlled ? value ?? '' : storedTargetId;
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
        if (controlled || !selectedTargetId || targetsQuery.isLoading) return;
        if (!readyTargets.some((target) => target.id === selectedTargetId)) {
            setSelectedTargetId('');
            window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
        }
    }, [controlled, readyTargets, selectedTargetId, targetsQuery.isLoading]);

    const selectTarget = (targetId: string) => {
        if (controlled) { onChange?.(targetId || null); return; }
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
                    aria-pressed={selectedTargetId === ''}
                    disabled={disabled}
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
                        aria-pressed={selectedTargetId === target.id}
                        disabled={disabled}
                        className={`rounded-lg border px-3 py-2 text-sm ${selectedTargetId === target.id
                            ? 'border-emerald-400 bg-emerald-500/15 text-emerald-100'
                            : 'border-slate-700 bg-slate-950 text-slate-300'}`}
                    >
                        Vast · {target.name ?? target.provider_instance_id}
                    </button>
                ))}
            </div>

            {controlled && selectedTargetId && (() => {
                const selected = readyTargets.find((target) => target.id === selectedTargetId);
                return selected ? (
                    <p className="mt-3 text-xs text-slate-300">{String(selected.capabilities.gpu_count ?? '?')} × {String(selected.capabilities.gpu_name ?? 'GPU')}</p>
                ) : (
                    <p role="alert" className="mt-3 text-xs text-red-300">Selected worker {selectedTargetId} is unavailable. Choose Local or a ready worker, or wait for inventory recovery.</p>
                );
            })()}
            {targetsQuery.error && (
                <p role="alert" className="mt-3 text-xs text-red-300">
                    {controlled ? 'Execution targets could not be refreshed. Remote submission is blocked until inventory recovers.' : 'Execution targets are unavailable. Local remains authoritative.'}
                </p>
            )}
        </section>
    );
}
