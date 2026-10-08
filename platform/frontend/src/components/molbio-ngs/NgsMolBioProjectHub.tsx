import { Link } from 'react-router-dom';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

type NgsMolBioProjectHubProps = {
    presentation?: 'inline' | 'launcher-dialog';
};

const BUTTON = 'inline-flex items-center rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm font-semibold text-content-primary hover:border-primary/60 focus:ring-2 focus:ring-accent';

export default function NgsMolBioProjectHub({ presentation = 'inline' }: NgsMolBioProjectHubProps) {
    const { workspaceId, globalExperimentId, domainExperimentId, stateRevisionId, selectedWorkspace } = useGlobalExperimentContext();
    const params = new URLSearchParams();
    if (globalExperimentId) params.set('focus', globalExperimentId);
    if (domainExperimentId) params.set('selected', `domain_experiment:${domainExperimentId}`);
    if (stateRevisionId) params.set('state_revision_id', stateRevisionId);
    const projectManagerHref = workspaceId
        ? `/projects/${encodeURIComponent(workspaceId)}?${params}`
        : '/projects?scope=ngs-molbio';

    if (presentation === 'launcher-dialog') return <Link to={projectManagerHref} className={BUTTON}>Projects</Link>;

    const workspaceParams = new URLSearchParams();
    if (workspaceId) workspaceParams.set('workspace_id', workspaceId);
    if (globalExperimentId) workspaceParams.set('global_experiment_id', globalExperimentId);
    if (domainExperimentId) workspaceParams.set('domain_experiment_id', domainExperimentId);
    if (stateRevisionId) workspaceParams.set('state_revision_id', stateRevisionId);

    return (
        <nav aria-label="NGS/MolBio Project context" className="flex flex-wrap items-center justify-between gap-2 border-b border-border-primary px-4 py-2">
            <span className="truncate text-sm text-content-secondary">{selectedWorkspace?.name ?? 'No Project selected'}</span>
            <div className="flex gap-2">
                {workspaceId && globalExperimentId && domainExperimentId && <>
                    <Link to={`/designer?${workspaceParams}&section=overview`} className={BUTTON}>Project data</Link>
                    <Link to={`/ngs?${workspaceParams}&section=workflow-plans`} className={BUTTON}>Plans &amp; Runs</Link>
                </>}
                <Link to={projectManagerHref} className={BUTTON}>{workspaceId ? 'Project Manager' : 'Choose Project'}</Link>
            </div>
        </nav>
    );
}
