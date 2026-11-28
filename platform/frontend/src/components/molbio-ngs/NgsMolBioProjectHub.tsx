import { lazy, Suspense, useMemo, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { designerProjectSelection } from './designerProjectSelection';
const ProjectWork = lazy(() => import('../project-manager/ProjectHubPage').then(m => ({default: m.ProjectHubPage})));
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

type NgsMolBioProjectHubProps = {
    children?: ReactNode;
    presentation?: 'inline' | 'launcher-dialog' | 'viewer';
};

const BUTTON = 'inline-flex items-center rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm font-semibold text-content-primary hover:border-primary/60 focus:ring-2 focus:ring-accent';

export default function NgsMolBioProjectHub({ presentation = 'inline', children }: NgsMolBioProjectHubProps) {
    const {
        workspaceId,
        selectedWorkspace, workspaces, updateQueryParams,
    } = useGlobalExperimentContext();

    const [params] = useSearchParams();
    const projectId = params.get('project_id') || workspaceId || '';
    const showingWork = Boolean(projectId && params.get('project_view') === 'work');
    const projectManagerHref = useMemo(() => {
        if (!workspaceId) return '/projects';
        return `/projects/${encodeURIComponent(workspaceId)}`;
    }, [workspaceId]);

    if (presentation === 'launcher-dialog') {
        return <a href={projectManagerHref} className={BUTTON}>Projects</a>;
    }

    if (presentation === 'viewer') {
        return (
            <>
                <section aria-label="Molecular project" className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border-primary bg-surface-secondary px-3 py-2 text-sm">
                    <label className="flex min-w-0 items-center gap-2">Project:
                        <select aria-label="Select project" className="max-w-[55vw] rounded border border-border-primary bg-surface px-2 py-1 text-content-primary"
                            value={projectId} onChange={event => updateQueryParams(designerProjectSelection(event.target.value || null))}>
                            <option value="">No Project selected</option>
                            {projectId && !workspaces.some(p => p.id === projectId) && <option value={projectId}>{projectId}</option>}
                            {workspaces.map(p => <option value={p.id} key={p.id}>{p.name}{p.lifecycle_state === 'archived' ? ' (archived)' : ''}</option>)}
                        </select>
                    </label>
                    {projectId && <button type="button" className={BUTTON}
                        onClick={() => updateQueryParams({ project_view: showingWork ? null : 'work' })}>
                        {showingWork ? 'Sequence editor' : 'Show project work'}
                    </button>}
                </section>
                {showingWork && <section aria-label="Selected project work" className="min-h-0 flex-1 overflow-auto">
                    <Suspense fallback={<p role="status">Loading project work…</p>}><ProjectWork key={projectId} projectId={projectId} /></Suspense>
                </section>}
                {/* Keep unsaved editor tabs mounted while browsing project records. */}
                <div className={showingWork ? 'hidden' : 'flex min-h-0 flex-1 flex-col'}>{children}</div>
            </>
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
