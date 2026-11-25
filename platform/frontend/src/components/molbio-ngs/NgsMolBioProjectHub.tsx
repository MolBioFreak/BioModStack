import { useMemo } from 'react';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

type NgsMolBioProjectHubProps = {
    presentation?: 'inline' | 'launcher-dialog' | 'viewer';
};

const BUTTON = 'inline-flex items-center rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm font-semibold text-content-primary hover:border-primary/60 focus:ring-2 focus:ring-accent';

export default function NgsMolBioProjectHub({ presentation = 'inline' }: NgsMolBioProjectHubProps) {
    const {
        workspaceId,
        selectedWorkspace,
    } = useGlobalExperimentContext();

    const projectManagerHref = useMemo(() => {
        if (!workspaceId) return '/projects';
        return `/projects/${encodeURIComponent(workspaceId)}`;
    }, [workspaceId]);

    if (presentation === 'launcher-dialog') {
        return <a href={projectManagerHref} className={BUTTON}>Projects</a>;
    }

    if (presentation === 'viewer') {
        return (
            <section aria-label="NGS/MolBio Project context" className="shrink-0 border-b border-border-primary bg-surface-secondary px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="text-content-secondary">
                        Project: <strong className="text-content-primary">{selectedWorkspace?.name ?? 'No Project selected'}</strong>
                    </span>
                    <a href={projectManagerHref} className="rounded border border-border-primary px-3 py-1 text-sm text-content-primary hover:bg-surface">
                        {workspaceId ? 'Open Project Hub' : 'Choose Project'}
                    </a>
                </div>
            </section>
        );
    }

    return (
        <section aria-label="NGS/MolBio Project context" className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border-primary bg-surface-secondary px-4 py-3">
                <div>
                    <p className="text-xs font-semibold text-content-muted">Project</p>
                    <p className="text-base font-semibold text-content-primary">{selectedWorkspace?.name ?? 'No Project selected'}</p>
                </div>
                <a href={projectManagerHref} className={BUTTON}>
                    {workspaceId ? 'Open Project Hub' : 'Choose Project'}
                </a>
            </div>
        </section>
    );
}
