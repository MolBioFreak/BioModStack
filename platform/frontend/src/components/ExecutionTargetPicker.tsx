import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { WorkflowProvisionPanel } from './dashboard/IndependentProvisionPanel';

import {
    EXECUTION_TARGET_STORAGE_KEY,
    fetchExecutionTargets,
    prepareJobSubmission, prepareExecutionPlacement, type WorkflowProvisionRequest,
} from '../lib/api';

interface ExecutionTargetPickerProps {
    value?: string | null;
    onChange?: (targetId: string | null) => void;
    disabled?: boolean;
    workflowRequest?: WorkflowProvisionRequest | null;
    submissionOptions?: { launchContext?: boolean };
}

export function ExecutionTargetPicker({ value, onChange, disabled = false, workflowRequest, submissionOptions }: ExecutionTargetPickerProps = {}) {
    const [, policyChanged] = useState(0);
    useEffect(() => {
        const changed = () => policyChanged(version => version + 1);
        window.addEventListener('bms:execution-policy-change', changed);
        return () => window.removeEventListener('bms:execution-policy-change', changed);
    }, []);
    const controlled = value !== undefined;
    const [storedTargetId, setSelectedTargetId] = useState(() => (
        controlled || typeof window === 'undefined'
            ? ''
            : window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || ''
    ));
    useEffect(() => {
        if (controlled) return;
        const changed = () => setSelectedTargetId(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) || '');
        window.addEventListener('bms:execution-target-change', changed);
        return () => window.removeEventListener('bms:execution-target-change', changed);
    }, [controlled]);
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


    const selectTarget = (targetId: string) => {
        if (controlled) { onChange?.(targetId || null); return; }
        setSelectedTargetId(targetId);
        if (targetId) {
            window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, targetId);
        } else {
            window.sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
        }
        window.dispatchEvent(new Event('bms:execution-target-change'));
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

            {selectedTargetId && (() => {
                const selected = readyTargets.find((target) => target.id === selectedTargetId);
                return selected ? (
                    <p className="mt-3 text-xs text-slate-300">{String(selected.capabilities.gpu_count ?? '?')} × {String(selected.capabilities.gpu_name ?? 'GPU')}</p>
                ) : (
                    <p role="alert" className="mt-3 text-xs text-red-300">Selected worker {selectedTargetId} is unavailable. Choose Local or a ready worker, or wait for inventory recovery.</p>
                );
            })()}
            {targetsQuery.error && (
                <p role="alert" className="mt-3 text-xs text-red-300">
                    Execution targets could not be refreshed. The selected placement is preserved; unavailable remote capacity cannot fall back to Local.
                </p>
            )}
            {workflowRequest !== undefined && (() => {
                const target = targets.find(item => item.id === selectedTargetId);
                return target && workflowRequest ? <WorkflowProvisionPanel target={target}
                    workflowRequest={'workflow_type' in workflowRequest
                        ? workflowRequest.workflow_type === 'molecular_dynamics'
                            ? { ...workflowRequest, request: { ...workflowRequest.request, intent: prepareExecutionPlacement({ ...workflowRequest.request.intent, execution_target_id: target.id }) } }
                            : { ...workflowRequest, request: prepareExecutionPlacement({ ...workflowRequest.request, execution_target_id: target.id }) }
                        : prepareJobSubmission({ ...workflowRequest, execution_target_id: target.id }, submissionOptions)}
                    onChanged={() => targetsQuery.refetch()} />
                    : <p className="mt-3 text-xs text-slate-400">To provision without launching, select a worker and configure the workflow in its existing controls.</p>;
            })()}
        </section>
    );
}
