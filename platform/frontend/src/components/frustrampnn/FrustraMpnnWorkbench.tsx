import { lazy, Suspense } from 'react';
import type { Job } from '../../lib/api.js';

const FrustraMpnnResultsViewer = lazy(() => import('../FrustraMpnnResultsViewer.js'));

export interface FrustraMpnnWorkbenchProps {
    job: Job;
    preferredInvocationId?: string;
    onBack: () => void;
    backLabel?: string;
    onOpenJob: (jobId: string) => void;
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
}: FrustraMpnnWorkbenchProps) {
    return (
        <div data-frustrampnn-workbench="global">
            <Suspense fallback={<div role="status" className="p-6 text-sm text-slate-400">Loading global FrustraMPNN workbench…</div>}>
                <FrustraMpnnResultsViewer
                    job={job}
                    preferredInvocationId={preferredInvocationId}
                    onBack={onBack}
                    backLabel={backLabel}
                    onOpenJob={onOpenJob}
                />
            </Suspense>
        </div>
    );
}

export default FrustraMpnnWorkbench;
