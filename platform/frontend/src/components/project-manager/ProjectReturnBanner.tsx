import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getLaunchContext, getProjectResultContext, verifiedProjectReturnUri } from '../../lib/projectManager';
import { parseFrustraMpnnExperimentContext } from '../frustrampnn/workflowResultViewState';

export function ProjectReturnBanner() {
    const [searchParams] = useSearchParams();
    const launchContextId = searchParams.get('launch_context_id');
    const launchQuery = useQuery({
        queryKey: ['launch-context', launchContextId],
        queryFn: ({ signal }) => getLaunchContext(launchContextId as string, signal),
        enabled: Boolean(launchContextId),
        retry: false,
    });
    // Reopening a result is a read, not a new launch. v1 issuance is retired;
    // resolve the same exact hierarchy through its existing read authorities.
    const requested = parseFrustraMpnnExperimentContext(searchParams.toString());
    const requestedReturn = searchParams.get('return_uri');
    const resultQuery = useQuery({
        queryKey: ['project-result-context', requested, requestedReturn],
        queryFn: async () => {
            const context = await getProjectResultContext(requested!.projectId, requested!.globalExperimentId, requested!.domainExperimentId, requestedReturn!);
            if (context.global_experiment_revision_id !== requested!.globalExperimentRevisionId || context.domain_revision_id !== requested!.domainRevisionId) {
                throw new Error('The requested Project result hierarchy revision is no longer current.');
            }
            return context;
        },
        enabled: !launchContextId && Boolean(requested && requestedReturn),
        retry: false,
    });
    const context = launchContextId ? launchQuery.data : resultQuery.data;
    const returnUri = context ? verifiedProjectReturnUri(context) : null;
    if (!returnUri || !context) return null;

    return (
        <aside className="border-b border-accent/30 bg-accent/10 px-4 py-2 text-content" aria-label="Project return context">
            <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
                <div className="min-w-0 text-xs text-content-secondary">
                    <p className="truncate">Project {context.project_id} / Experiment {context.global_experiment_id} / Domain {context.domain_experiment_id}</p>
                    {'schema' in context && context.schema === 'bms.launch-context.v2' && (
                        <p className="truncate">Prepared workflow {context.workflow_id} / Preparation {context.preparation_id} / Attempt {context.run_attempt_id}</p>
                    )}
                </div>
                <Link to={returnUri} aria-label="Return to Project context" className="shrink-0 rounded-lg border border-accent px-3 py-1.5 text-xs font-semibold text-accent focus:ring-2 focus:ring-accent">Return to Project</Link>
            </div>
        </aside>
    );
}
