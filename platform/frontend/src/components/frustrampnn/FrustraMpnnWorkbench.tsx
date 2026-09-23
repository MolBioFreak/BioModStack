import { lazy, Suspense, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import type { Job } from '../../lib/api.js';
import {
    parseFrustraMpnnExperimentContext,
    type FrustraMpnnExperimentContext,
    type FrustraMpnnResultScope,
} from './workflowResultViewState.js';

const FrustraMpnnResultsViewer = lazy(() => import('../FrustraMpnnResultsViewer.js'));

export interface FrustraMpnnWorkbenchProps {
    job: Job;
    preferredInvocationId?: string;
    onBack: () => void;
    backLabel?: string;
    onOpenJob: (jobId: string) => void;
    scope?: FrustraMpnnResultScope;
    onScopeChange?: (scope: FrustraMpnnResultScope) => void;
    experimentContext?: FrustraMpnnExperimentContext | null;
}

/**
 * Producer-neutral global FrustraMPNN result workbench.
 *
 * Workflow viewers may add context around this component. They must provide the
 * authoritative parent Job identity and must not fork its analysis surface.
 */
export function FrustraMpnnWorkbench({
    job,
    preferredInvocationId,
    onBack,
    backLabel,
    onOpenJob,
    scope,
    onScopeChange,
    experimentContext,
}: FrustraMpnnWorkbenchProps) {
    const location = useLocation();
    const resolvedExperimentContext = useMemo(
        () => experimentContext ?? parseFrustraMpnnExperimentContext(location.search),
        [experimentContext, location.search],
    );
    return (
        <div data-frustrampnn-workbench="global">
            <p className="px-6 pt-4 text-sm text-slate-400" data-frustrampnn-inference-scope="chain-local">
                FrustraMPNN predicts a single-residue mutation landscape for each selected protein chain,
                retaining that chain’s intrachain domain context. A binder designed against a target may
                carry target-specific design history, but this inference is chain-local: changing only the
                target while leaving binder atoms unchanged does not change its binder prediction. This is
                exploratory binder-local evidence, not direct target-conditioned inference, binding evidence,
                or cross-chain contact frustration. Binder and target are never joined into one chain here.
            </p>
            <Suspense fallback={<div role="status" className="p-6 text-sm text-slate-400">Loading global FrustraMPNN workbench…</div>}>
                <FrustraMpnnResultsViewer
                    job={job}
                    preferredInvocationId={preferredInvocationId}
                    onBack={onBack}
                    backLabel={backLabel}
                    onOpenJob={onOpenJob}
                    scope={scope}
                    onScopeChange={onScopeChange}
                    experimentContext={resolvedExperimentContext}
                />
            </Suspense>
        </div>
    );
}

export default FrustraMpnnWorkbench;
