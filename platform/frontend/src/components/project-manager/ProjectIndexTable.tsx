import { useDeferredValue, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
    projectManagerErrorMessage,
    searchProjects,
    type ProjectListItem,
} from '../../lib/projectManager';
import { ProjectFormDialog, ProjectLifecycleDialog } from './ProjectFormDialog';

function formatShortDate(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value.slice(0, 10);
    return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function CountCell({ value }: { value: number }) {
    return (
        <span
            className={value > 0
                ? 'inline-flex min-w-[2rem] justify-center rounded-md border border-slate-600 bg-slate-800 px-2 py-0.5 text-xs font-semibold text-slate-100'
                : 'inline-flex min-w-[2rem] justify-center rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs font-medium text-slate-300'}
        >
            {value}
        </span>
    );
}

export function ProjectIndexTable() {
    const navigate = useNavigate();
    const [queryText, setQueryText] = useState('');
    const deferredQuery = useDeferredValue(queryText);
    const [archiveFilter, setArchiveFilter] = useState<'active' | 'archived'>('active');
    const [createOpen, setCreateOpen] = useState(false);
    const [lifecycleTarget, setLifecycleTarget] = useState<{
        action: 'archive' | 'restore';
        project: { id: string; name: string; head_generation: number };
    } | null>(null);

    const projectsQuery = useQuery({
        queryKey: ['projects-index', deferredQuery, archiveFilter],
        queryFn: ({ signal }) => searchProjects({
            query: deferredQuery,
            archive: archiveFilter,
            projectScope: 'all',
            limit: 100,
            signal,
        }),
    });

    const capabilities = projectsQuery.data?.capabilities ?? {
        has_in_silico: true,
        has_molbio: true,
        has_ngs_ont: true,
        has_stats: false,
    };
    const hasInSilico = capabilities.has_in_silico;

    const visibleProjects = useMemo(() => {
        const items = projectsQuery.data?.items ?? [];
        if (hasInSilico) {
            // Full BMS lists global projects in the main index (linked local projects appear inside them).
            return items.filter((item) => (item.project_scope ?? 'global') !== 'ngs_molbio_local');
        }
        // Standalone NGS/MolBio lists local projects directly.
        return items.filter((item) => item.project_scope === 'ngs_molbio_local');
    }, [hasInSilico, projectsQuery.data?.items]);

    const defaultCreateScope: 'global' | 'ngs_molbio_local' = hasInSilico ? 'global' : 'ngs_molbio_local';

    return (
        <main className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6 sm:px-6">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-slate-100">Projects</h1>
                    <p className="mt-1 text-sm text-slate-300">
                        Reference hub across your saved work. Opening a project reads stored records without recomputing.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => setCreateOpen(true)}
                    className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500 focus:outline-none focus:ring-2 focus:ring-cyan-400"
                >
                    + New Project
                </button>
            </header>

            <div className="flex flex-wrap items-center justify-between gap-3">
                <input
                    type="search"
                    aria-label="Search projects"
                    value={queryText}
                    onChange={(event) => setQueryText(event.target.value)}
                    placeholder="Search projects by name or description…"
                    className="w-full max-w-md rounded-lg border border-slate-600 bg-slate-900 px-3.5 py-2 text-sm text-slate-100 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none"
                />
                <div className="inline-flex rounded-lg border border-slate-700 bg-slate-900 p-0.5 text-xs font-semibold" role="group" aria-label="Project lifecycle filter">
                    <button
                        type="button"
                        aria-pressed={archiveFilter === 'active'}
                        onClick={() => setArchiveFilter('active')}
                        className={archiveFilter === 'active'
                            ? 'rounded-md bg-slate-700 px-3 py-1.5 text-slate-100'
                            : 'rounded-md px-3 py-1.5 text-slate-300 hover:text-slate-100'}
                    >
                        Active
                    </button>
                    <button
                        type="button"
                        aria-pressed={archiveFilter === 'archived'}
                        onClick={() => setArchiveFilter('archived')}
                        className={archiveFilter === 'archived'
                            ? 'rounded-md bg-slate-700 px-3 py-1.5 text-slate-100'
                            : 'rounded-md px-3 py-1.5 text-slate-300 hover:text-slate-100'}
                    >
                        Archived
                    </button>
                </div>
            </div>

            {projectsQuery.isError && (
                <p role="alert" className="rounded-xl border border-red-500/50 bg-red-950/50 px-4 py-3 text-sm text-red-200">
                    {projectManagerErrorMessage(projectsQuery.error)}
                </p>
            )}

            <section aria-label="Projects table" className="overflow-x-auto rounded-xl border border-slate-700 bg-slate-900/90">
                <table className="w-full border-collapse text-left text-sm">
                    <thead>
                        <tr className="border-b border-slate-700 bg-slate-800/80 text-xs font-semibold uppercase tracking-wide text-slate-300">
                            <th className="px-4 py-3">Project</th>
                            {hasInSilico && <th className="px-3 py-3 text-center">In silico</th>}
                            <th className="px-3 py-3 text-center">Sequences</th>
                            <th className="px-3 py-3 text-center">Operations</th>
                            <th className="px-3 py-3 text-center">NGS / Alignments</th>
                            <th className="px-4 py-3">Updated</th>
                            <th className="px-4 py-3 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800">
                        {projectsQuery.isLoading ? (
                            <tr>
                                <td colSpan={hasInSilico ? 7 : 6} className="px-4 py-8 text-center text-sm text-slate-300">
                                    Loading projects…
                                </td>
                            </tr>
                        ) : visibleProjects.length === 0 ? (
                            <tr>
                                <td colSpan={hasInSilico ? 7 : 6} className="px-4 py-8 text-center text-sm text-slate-300">
                                    {deferredQuery.trim()
                                        ? 'No matching projects.'
                                        : archiveFilter === 'archived'
                                            ? 'No archived projects.'
                                            : 'No projects yet. Create one to start indexing your work.'}
                                </td>
                            </tr>
                        ) : (
                            visibleProjects.map((project: ProjectListItem) => {
                                const counts = project.counts ?? {
                                    in_silico: 0,
                                    molecular_sequences: 0,
                                    molecular_operations: 0,
                                    ngs_ont_and_alignments: 0,
                                };
                                const isArchived = project.lifecycle_state === 'archived' || project.status === 'archived';
                                return (
                                    <tr
                                        key={project.id}
                                        className="transition-colors hover:bg-slate-800/60"
                                    >
                                        <td className="px-4 py-3.5 align-top">
                                            <Link
                                                to={`/projects/${encodeURIComponent(project.id)}`}
                                                className="font-semibold text-slate-100 hover:text-cyan-300 focus:outline-none focus:underline"
                                            >
                                                {project.name}
                                            </Link>
                                            {project.description && (
                                                <p className="mt-0.5 line-clamp-2 max-w-xl text-xs text-slate-300">
                                                    {project.description}
                                                </p>
                                            )}
                                        </td>
                                        {hasInSilico && (
                                            <td className="px-3 py-3.5 text-center align-middle">
                                                <CountCell value={counts.in_silico} />
                                            </td>
                                        )}
                                        <td className="px-3 py-3.5 text-center align-middle">
                                            <CountCell value={counts.molecular_sequences} />
                                        </td>
                                        <td className="px-3 py-3.5 text-center align-middle">
                                            <CountCell value={counts.molecular_operations} />
                                        </td>
                                        <td className="px-3 py-3.5 text-center align-middle">
                                            <CountCell value={counts.ngs_ont_and_alignments} />
                                        </td>
                                        <td className="whitespace-nowrap px-4 py-3.5 align-middle text-xs text-slate-300">
                                            {formatShortDate(project.updated_at)}
                                        </td>
                                        <td className="whitespace-nowrap px-4 py-3.5 text-right align-middle">
                                            <button
                                                type="button"
                                                aria-label={isArchived ? `Restore Project ${project.name}` : `Archive Project ${project.name}`}
                                                onClick={() => setLifecycleTarget({
                                                    action: isArchived ? 'restore' : 'archive',
                                                    project: {
                                                        id: project.id,
                                                        name: project.name,
                                                        head_generation: project.head_generation,
                                                    },
                                                })}
                                                className="rounded-lg border border-slate-600 px-3 py-1 text-xs font-semibold text-slate-200 hover:border-slate-400 hover:bg-slate-800"
                                            >
                                                {isArchived ? 'Restore' : 'Archive'}
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })
                        )}
                    </tbody>
                </table>
            </section>

            <ProjectFormDialog
                open={createOpen}
                mode="create"
                defaultScope={defaultCreateScope}
                onClose={() => setCreateOpen(false)}
                onSaved={(created) => {
                    setCreateOpen(false);
                    navigate(`/projects/${encodeURIComponent(created.id)}`);
                }}
            />

            <ProjectLifecycleDialog
                open={lifecycleTarget !== null}
                action={lifecycleTarget?.action ?? 'archive'}
                project={lifecycleTarget?.project ?? null}
                onClose={() => setLifecycleTarget(null)}
                onCompleted={() => setLifecycleTarget(null)}
            />
        </main>
    );
}
