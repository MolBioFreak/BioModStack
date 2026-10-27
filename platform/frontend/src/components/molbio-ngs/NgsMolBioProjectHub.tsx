import { useMemo } from 'react';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';
import DomainExperimentWorkspace from './DomainExperimentWorkspace';

type NgsMolBioProjectHubProps = {
    presentation?: 'inline' | 'launcher-dialog' | 'viewer';
};

const BUTTON = 'inline-flex items-center rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm font-semibold text-content-primary hover:border-primary/60 focus:ring-2 focus:ring-accent';

export default function NgsMolBioProjectHub({ presentation = 'inline' }: NgsMolBioProjectHubProps) {
    const {
        workspaceId,
        globalExperimentId,
        domainExperimentId,
        stateRevisionId,
        selectedWorkspace,
        selectedDomainExperiment,
    } = useGlobalExperimentContext();
    const hasScientificContext = Boolean(
        workspaceId
        && globalExperimentId
        && domainExperimentId
        && selectedDomainExperiment?.domain_experiment_id === domainExperimentId,
    );
    const projectManagerHref = useMemo(() => {
        if (!workspaceId) return '/projects?scope=ngs-molbio';
        const params = new URLSearchParams();
        if (globalExperimentId) params.set('focus', globalExperimentId);
        if (domainExperimentId) params.set('selected', `domain_experiment:${domainExperimentId}`);
        if (stateRevisionId) params.set('state_revision_id', stateRevisionId);
        const query = params.toString();
        return `/projects/${encodeURIComponent(workspaceId)}${query ? `?${query}` : ''}`;
    }, [domainExperimentId, globalExperimentId, stateRevisionId, workspaceId]);

    if (presentation === 'launcher-dialog') {
        return <a href={projectManagerHref} className={BUTTON}>Projects</a>;
    }

    if (presentation === 'viewer') {
        return <section aria-label="NGS/MolBio Project context" className="shrink-0 border-b border-border-primary bg-surface-secondary px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-content-secondary">Project: <strong className="text-content-primary">{selectedWorkspace?.name ?? 'No Project selected'}</strong></span>
                <a href={projectManagerHref} className="rounded border border-border-primary px-3 py-1 text-sm text-content-primary">{workspaceId ? 'Open in Project Manager' : 'Choose Project'}</a>
            </div>
            {hasScientificContext && <details className="mt-1 text-sm text-content-secondary">
                <summary className="w-fit cursor-pointer">Project sequences and NGS data</summary>
                <div className="mt-2 max-h-72 overflow-auto"><DomainExperimentWorkspace /></div>
            </details>}
        </section>;
    }

    return (
        <section aria-label="NGS/MolBio Project context" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-primary bg-surface-secondary px-4 py-3">
                <div>
                    <p className="text-xs font-semibold text-content-muted">Project</p>
                    <p className="text-base font-semibold text-content-primary">{selectedWorkspace?.name ?? 'No Project selected'}</p>
                </div>
                <a href={projectManagerHref} className={BUTTON}>{workspaceId ? 'Open in Project Manager' : 'Choose Project'}</a>
            </div>
            {hasScientificContext ? <DomainExperimentWorkspace /> : (
                <div className="rounded-xl border border-dashed border-border-primary bg-surface-secondary p-6 text-center">
                    <h2 className="text-lg font-semibold text-content-primary">Choose an NGS/MolBio Project</h2>
                    <p className="mt-2 text-sm text-content-secondary">Choose a Project to organize sequences and NGS data, or work with a sequence below and use Add to Project.</p>
                </div>
            )}
        </section>
    );
}
