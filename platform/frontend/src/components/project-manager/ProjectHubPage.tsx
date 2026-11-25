import { useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    getProject,
    getProjectHub,
    projectManagerErrorMessage,
    type ProjectHubItem,
    type ProjectStatus,
} from '../../lib/projectManager';
import { removeMolecularProjectMember } from '../../lib/molecularProjectMembership';
import { ProjectAttachmentDialog } from './ProjectAttachmentDialog';
import { ProjectFormDialog, ProjectLifecycleDialog } from './ProjectFormDialog';

function formatShortDate(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value.slice(0, 10);
    return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function matchesQuery(item: ProjectHubItem, query: string): boolean {
    if (!query) return true;
    const needle = query.toLowerCase();
    return (
        item.title.toLowerCase().includes(needle)
        || item.subtitle.toLowerCase().includes(needle)
        || (item.status ?? '').toLowerCase().includes(needle)
        || item.badges.some((badge) => badge.toLowerCase().includes(needle))
    );
}

interface CollapsibleSectionProps {
    id: string;
    title: string;
    count: number;
    open: boolean;
    onToggle: () => void;
    action?: ReactNode;
    children: ReactNode;
}

function CollapsibleSection({
    id,
    title,
    count,
    open,
    onToggle,
    action,
    children,
}: CollapsibleSectionProps) {
    return (
        <section aria-labelledby={`${id}-heading`} className="overflow-hidden rounded-xl border border-slate-700 bg-slate-900/90">
            <div className="flex items-center justify-between gap-2 bg-slate-800/70 transition-colors hover:bg-slate-800">
                <button
                    id={`${id}-heading`}
                    type="button"
                    aria-expanded={open}
                    onClick={onToggle}
                    className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3.5 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-cyan-400"
                >
                    <span aria-hidden="true" className="text-xs font-bold text-slate-300">
                        {open ? '▾' : '▸'}
                    </span>
                    <span className="truncate text-sm font-semibold text-slate-100">{title}</span>
                    <span
                        className={count > 0
                            ? 'inline-flex min-w-[1.75rem] justify-center rounded-full border border-slate-500 bg-slate-700 px-2 py-0.5 text-xs font-semibold text-slate-100'
                            : 'inline-flex min-w-[1.75rem] justify-center rounded-full border border-slate-600 bg-slate-900 px-2 py-0.5 text-xs font-semibold text-slate-300'}
                    >
                        {count}
                    </span>
                </button>
                {action && <div className="shrink-0 pr-3">{action}</div>}
            </div>
            {open && <div className="border-t border-slate-700">{children}</div>}
        </section>
    );
}

interface HubItemRowProps {
    item: ProjectHubItem;
    kindLabel?: string;
    detachingId: string | null;
    onDetach?: (item: ProjectHubItem) => void;
}

function HubItemRow({ item, kindLabel, detachingId, onDetach }: HubItemRowProps) {
    const isDetaching = detachingId === item.id;
    return (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 last:border-b-0 hover:bg-slate-800/40">
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-100">{item.title}</span>
                    {kindLabel && (
                        <span className="rounded border border-slate-600 bg-slate-800 px-2 py-0.5 text-xs font-medium text-slate-200">
                            {kindLabel}
                        </span>
                    )}
                    {item.badges.map((badge, index) => (
                        <span
                            key={`${badge}-${index}`}
                            className="rounded border border-slate-600 bg-slate-800 px-2 py-0.5 text-xs font-medium text-slate-200"
                        >
                            {badge}
                        </span>
                    ))}
                    {item.status && (
                        <span className="rounded border border-slate-600 bg-slate-900 px-2 py-0.5 text-xs font-medium text-slate-200">
                            {item.status}
                        </span>
                    )}
                </div>
                {item.subtitle && (
                    <p className="mt-1 text-xs text-slate-300">{item.subtitle}</p>
                )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
                <span className="text-xs text-slate-300">{formatShortDate(item.updated_at)}</span>
                <Link
                    to={item.open_href}
                    className="rounded-lg border border-cyan-500/60 bg-cyan-950/60 px-3 py-1 text-xs font-semibold text-cyan-200 hover:bg-cyan-900/70"
                >
                    Open
                </Link>
                {item.detach && onDetach && (
                    <button
                        type="button"
                        disabled={isDetaching}
                        onClick={() => onDetach(item)}
                        className="rounded-lg border border-slate-600 px-3 py-1 text-xs font-semibold text-slate-200 hover:border-red-400 hover:text-red-200 disabled:opacity-50"
                    >
                        {isDetaching ? 'Removing…' : 'Remove'}
                    </button>
                )}
            </div>
        </div>
    );
}

export function ProjectHubPage({ projectId }: { projectId: string }) {
    const queryClient = useQueryClient();
    const [searchText, setSearchText] = useState('');
    const [groupBy, setGroupBy] = useState<'type' | 'experiment'>('type');
    const [openSections, setOpenSections] = useState<Record<string, boolean>>({});
    const [editOpen, setEditOpen] = useState(false);
    const [attachOpen, setAttachOpen] = useState(false);
    const [lifecycleAction, setLifecycleAction] = useState<'archive' | 'restore' | null>(null);
    const [detachError, setDetachError] = useState<string | null>(null);
    const [detachingId, setDetachingId] = useState<string | null>(null);

    const hubQuery = useQuery({
        queryKey: ['project-hub', projectId],
        queryFn: ({ signal }) => getProjectHub(projectId, { signal }),
    });

    const projectMetaQuery = useQuery({
        queryKey: ['project-manager', 'project', projectId],
        queryFn: ({ signal }) => getProject(projectId, signal),
        enabled: editOpen || lifecycleAction !== null || attachOpen,
    });

    const detachMutation = useMutation({
        mutationFn: async (item: ProjectHubItem) => {
            if (!item.detach) throw new Error('Detach is not supported for this record.');
            setDetachError(null);
            setDetachingId(item.id);
            const experimentId = item.experiment_id ?? hubQuery.data?.project.experiments[0]?.id ?? '';
            await removeMolecularProjectMember(
                projectId,
                experimentId,
                item.detach.domain_experiment_id,
                item.detach.target_id,
                item.detach.expected_state_revision_id,
            );
        },
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['project-hub', projectId] }),
                queryClient.invalidateQueries({ queryKey: ['projects-index'] }),
                queryClient.invalidateQueries({ queryKey: ['molbio-project-sequences', projectId] }),
            ]);
        },
        onError: (error) => {
            setDetachError(projectManagerErrorMessage(error));
        },
        onSettled: () => {
            setDetachingId(null);
        },
    });

    const toggleSection = (key: string) => {
        setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
    };

    const hub = hubQuery.data;
    const trimmedSearch = searchText.trim();

    const filteredInSilico = useMemo(
        () => (hub?.sections.in_silico ?? []).filter((item) => matchesQuery(item, trimmedSearch)),
        [hub?.sections.in_silico, trimmedSearch],
    );
    const filteredSequences = useMemo(
        () => (hub?.sections.molecular_sequences ?? []).filter((item) => matchesQuery(item, trimmedSearch)),
        [hub?.sections.molecular_sequences, trimmedSearch],
    );
    const filteredOperations = useMemo(
        () => (hub?.sections.molecular_operations ?? []).filter((item) => matchesQuery(item, trimmedSearch)),
        [hub?.sections.molecular_operations, trimmedSearch],
    );
    const filteredNgsAlign = useMemo(
        () => (hub?.sections.ngs_ont_and_alignments ?? []).filter((item) => matchesQuery(item, trimmedSearch)),
        [hub?.sections.ngs_ont_and_alignments, trimmedSearch],
    );

    const experimentGroups = useMemo(() => {
        if (!hub) return [];
        const allItems: Array<{ item: ProjectHubItem; kindLabel: string }> = [
            ...(hub.capabilities.has_in_silico ? filteredInSilico.map((item) => ({ item, kindLabel: 'In silico' })) : []),
            ...(hub.capabilities.has_molbio ? filteredSequences.map((item) => ({ item, kindLabel: 'Sequence' })) : []),
            ...(hub.capabilities.has_molbio ? filteredOperations.map((item) => ({ item, kindLabel: 'Operation' })) : []),
            ...(hub.capabilities.has_ngs_ont ? filteredNgsAlign.map((item) => ({ item, kindLabel: 'NGS / Alignment' })) : []),
        ];
        const byExp = new Map<string, Array<{ item: ProjectHubItem; kindLabel: string }>>();
        const unassigned: Array<{ item: ProjectHubItem; kindLabel: string }> = [];
        for (const entry of allItems) {
            if (entry.item.experiment_id) {
                const list = byExp.get(entry.item.experiment_id) ?? [];
                list.push(entry);
                byExp.set(entry.item.experiment_id, list);
            } else {
                unassigned.push(entry);
            }
        }
        const groups = hub.project.experiments.map((exp) => ({
            id: exp.id,
            name: exp.name,
            status: exp.status,
            entries: byExp.get(exp.id) ?? [],
        }));
        if (unassigned.length > 0) {
            groups.push({
                id: '__unassigned__',
                name: 'Project-level items',
                status: '',
                entries: unassigned,
            });
        }
        return groups;
    }, [filteredInSilico, filteredNgsAlign, filteredOperations, filteredSequences, hub]);

    if (hubQuery.isLoading) {
        return (
            <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
                <p role="status" className="text-sm text-slate-300">Loading project…</p>
            </main>
        );
    }

    if (hubQuery.isError || !hub) {
        return (
            <main className="mx-auto w-full max-w-6xl space-y-4 px-4 py-8 sm:px-6">
                <Link to="/projects" className="inline-flex items-center text-sm font-medium text-cyan-300 hover:underline">
                    ← All Projects
                </Link>
                <p role="alert" className="rounded-xl border border-red-500/50 bg-red-950/60 px-4 py-3 text-sm text-red-200">
                    {hubQuery.error ? projectManagerErrorMessage(hubQuery.error) : 'Project not found.'}
                </p>
            </main>
        );
    }

    const { capabilities, project } = hub;
    const isArchived = project.lifecycle_state === 'archived';
    const showScopeBadge = capabilities.has_in_silico && project.project_scope === 'ngs_molbio_local';
    const showExperimentToggle = project.experiments.length > 1;
    const showLinkedLocalProjects = capabilities.has_in_silico
        && project.project_scope === 'global'
        && project.linked_local_projects.length > 0;

    return (
        <main className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6 sm:px-6">
            <header className="space-y-3 border-b border-slate-800 pb-4">
                <Link to="/projects" className="inline-flex items-center text-xs font-semibold text-cyan-300 hover:underline">
                    ← All Projects
                </Link>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2.5">
                            <h1 className="text-2xl font-bold tracking-tight text-slate-100">{project.name}</h1>
                            {showScopeBadge && (
                                <span className="rounded-full border border-cyan-500/60 bg-cyan-950/60 px-2.5 py-0.5 text-xs font-semibold text-cyan-200">
                                    Local NGS/MolBio
                                </span>
                            )}
                            {isArchived && (
                                <span className="rounded-full border border-amber-500/60 bg-amber-950/60 px-2.5 py-0.5 text-xs font-semibold text-amber-200">
                                    Archived
                                </span>
                            )}
                        </div>
                        {project.description && (
                            <p className="mt-1.5 max-w-3xl text-sm text-slate-300">{project.description}</p>
                        )}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                        <button
                            type="button"
                            onClick={() => setEditOpen(true)}
                            className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800"
                        >
                            Edit
                        </button>
                        <button
                            type="button"
                            onClick={() => setAttachOpen(true)}
                            className="rounded-lg border border-cyan-500/60 bg-cyan-950/60 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-900/70"
                        >
                            Attach
                        </button>
                        <button
                            type="button"
                            onClick={() => setLifecycleAction(isArchived ? 'restore' : 'archive')}
                            className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800"
                        >
                            {isArchived ? 'Restore' : 'Archive'}
                        </button>
                    </div>
                </div>
            </header>

            <div className="flex flex-wrap items-center justify-between gap-3">
                <input
                    type="search"
                    aria-label="Filter project items"
                    value={searchText}
                    onChange={(event) => setSearchText(event.target.value)}
                    placeholder="Filter items in this project…"
                    className="w-full max-w-md rounded-lg border border-slate-600 bg-slate-900 px-3.5 py-2 text-sm text-slate-100 placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none"
                />
                {showExperimentToggle && (
                    <div
                        role="group"
                        aria-label="Project grouping"
                        className="inline-flex rounded-lg border border-slate-700 bg-slate-900 p-0.5 text-xs font-semibold"
                    >
                        <button
                            type="button"
                            aria-pressed={groupBy === 'type'}
                            onClick={() => setGroupBy('type')}
                            className={groupBy === 'type'
                                ? 'rounded-md bg-slate-700 px-3 py-1.5 text-slate-100'
                                : 'rounded-md px-3 py-1.5 text-slate-300 hover:text-slate-100'}
                        >
                            By type
                        </button>
                        <button
                            type="button"
                            aria-pressed={groupBy === 'experiment'}
                            onClick={() => setGroupBy('experiment')}
                            className={groupBy === 'experiment'
                                ? 'rounded-md bg-slate-700 px-3 py-1.5 text-slate-100'
                                : 'rounded-md px-3 py-1.5 text-slate-300 hover:text-slate-100'}
                        >
                            By experiment
                        </button>
                    </div>
                )}
            </div>

            {detachError && (
                <p role="alert" className="rounded-xl border border-red-500/50 bg-red-950/60 px-4 py-3 text-sm text-red-200">
                    {detachError}
                </p>
            )}

            {groupBy === 'type' ? (
                <div className="space-y-3">
                    {capabilities.has_in_silico && (
                        <CollapsibleSection
                            id="section-in-silico"
                            title="In silico"
                            count={filteredInSilico.length}
                            open={Boolean(openSections['in_silico'])}
                            onToggle={() => toggleSection('in_silico')}
                        >
                            {filteredInSilico.length === 0 ? (
                                <p className="px-4 py-4 text-sm text-slate-300">No in silico items in this project.</p>
                            ) : (
                                filteredInSilico.map((item) => (
                                    <HubItemRow key={item.id} item={item} detachingId={detachingId} />
                                ))
                            )}
                        </CollapsibleSection>
                    )}

                    {capabilities.has_molbio && (
                        <CollapsibleSection
                            id="section-molbio"
                            title="Molecular sequences & constructs"
                            count={filteredSequences.length}
                            open={Boolean(openSections['molbio'])}
                            onToggle={() => toggleSection('molbio')}
                            action={(
                                <Link
                                    to={`/designer?project_id=${encodeURIComponent(projectId)}`}
                                    className="rounded-lg border border-cyan-500/60 bg-cyan-950/60 px-3 py-1.5 text-xs font-semibold text-cyan-200 hover:bg-cyan-900/70"
                                >
                                    Open in Molecular Toolset
                                </Link>
                            )}
                        >
                            {filteredSequences.length === 0 ? (
                                <p className="px-4 py-4 text-sm text-slate-300">No molecular sequences in this project.</p>
                            ) : (
                                filteredSequences.map((item) => (
                                    <HubItemRow
                                        key={item.id}
                                        item={item}
                                        detachingId={detachingId}
                                        onDetach={(target) => detachMutation.mutate(target)}
                                    />
                                ))
                            )}
                            <div className="border-t border-slate-800 bg-slate-950/40">
                                <button
                                    type="button"
                                    aria-expanded={Boolean(openSections['molbio_ops'])}
                                    onClick={() => toggleSection('molbio_ops')}
                                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-xs font-semibold text-slate-200 hover:bg-slate-800/60"
                                >
                                    <span aria-hidden="true">{openSections['molbio_ops'] ? '▾' : '▸'}</span>
                                    <span>Operations</span>
                                    <span className="rounded-full border border-slate-600 bg-slate-800 px-2 py-0.5 text-xs text-slate-200">
                                        {filteredOperations.length}
                                    </span>
                                </button>
                                {openSections['molbio_ops'] && (
                                    <div className="border-t border-slate-800">
                                        {filteredOperations.length === 0 ? (
                                            <p className="px-4 py-3 text-xs text-slate-300">No molecular operations recorded.</p>
                                        ) : (
                                            filteredOperations.map((item) => (
                                                <HubItemRow key={item.id} item={item} detachingId={detachingId} />
                                            ))
                                        )}
                                    </div>
                                )}
                            </div>
                        </CollapsibleSection>
                    )}

                    {capabilities.has_ngs_ont && (
                        <CollapsibleSection
                            id="section-ngs-align"
                            title="NGS / ONT & alignments"
                            count={filteredNgsAlign.length}
                            open={Boolean(openSections['ngs_align'])}
                            onToggle={() => toggleSection('ngs_align')}
                            action={(
                                <Link
                                    to={`/alignments?project_id=${encodeURIComponent(projectId)}`}
                                    className="rounded-lg border border-slate-600 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800"
                                >
                                    Alignment library
                                </Link>
                            )}
                        >
                            {filteredNgsAlign.length === 0 ? (
                                <p className="px-4 py-4 text-sm text-slate-300">No NGS/ONT or alignment items in this project.</p>
                            ) : (
                                filteredNgsAlign.map((item) => (
                                    <HubItemRow key={item.id} item={item} detachingId={detachingId} />
                                ))
                            )}
                        </CollapsibleSection>
                    )}

                    {showLinkedLocalProjects && (
                        <CollapsibleSection
                            id="section-linked-local"
                            title="Linked local NGS/MolBio projects"
                            count={project.linked_local_projects.length}
                            open={Boolean(openSections['linked_local'])}
                            onToggle={() => toggleSection('linked_local')}
                        >
                            {project.linked_local_projects.map((linked) => (
                                <div
                                    key={linked.id}
                                    className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 last:border-b-0 hover:bg-slate-800/40"
                                >
                                    <div className="min-w-0 flex-1">
                                        <Link
                                            to={`/projects/${encodeURIComponent(linked.id)}`}
                                            className="font-medium text-slate-100 hover:text-cyan-300"
                                        >
                                            {linked.name}
                                        </Link>
                                        {linked.description && (
                                            <p className="mt-0.5 text-xs text-slate-300">{linked.description}</p>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-3 text-xs text-slate-300">
                                        <span>{linked.counts?.molecular_sequences ?? 0} sequences</span>
                                        <span>{linked.counts?.ngs_ont_and_alignments ?? 0} NGS/alignments</span>
                                        <Link
                                            to={`/projects/${encodeURIComponent(linked.id)}`}
                                            className="rounded-lg border border-cyan-500/60 bg-cyan-950/60 px-3 py-1 text-xs font-semibold text-cyan-200 hover:bg-cyan-900/70"
                                        >
                                            Open
                                        </Link>
                                    </div>
                                </div>
                            ))}
                        </CollapsibleSection>
                    )}
                </div>
            ) : (
                <div className="space-y-3" aria-label="Experiments">
                    {experimentGroups.map((group) => (
                        <CollapsibleSection
                            key={group.id}
                            id={`section-exp-${group.id}`}
                            title={group.status ? `${group.name} (${group.status})` : group.name}
                            count={group.entries.length}
                            open={Boolean(openSections[`exp:${group.id}`])}
                            onToggle={() => toggleSection(`exp:${group.id}`)}
                        >
                            {group.entries.length === 0 ? (
                                <p className="px-4 py-4 text-sm text-slate-300">No items in this experiment.</p>
                            ) : (
                                group.entries.map(({ item, kindLabel }) => (
                                    <HubItemRow
                                        key={`${kindLabel}:${item.id}`}
                                        item={item}
                                        kindLabel={kindLabel}
                                        detachingId={detachingId}
                                        onDetach={item.detach ? (target) => detachMutation.mutate(target) : undefined}
                                    />
                                ))
                            )}
                        </CollapsibleSection>
                    ))}
                </div>
            )}

            <ProjectFormDialog
                open={editOpen}
                mode="edit"
                project={{
                    id: project.id,
                    name: project.name,
                    description: project.description,
                    status: (projectMetaQuery.data?.status as ProjectStatus | undefined) ?? 'active',
                    head_generation: projectMetaQuery.data?.head_generation ?? 0,
                }}
                onClose={() => setEditOpen(false)}
                onSaved={() => setEditOpen(false)}
            />

            <ProjectLifecycleDialog
                open={lifecycleAction !== null}
                action={lifecycleAction ?? 'archive'}
                project={projectMetaQuery.data ? {
                    id: project.id,
                    name: project.name,
                    head_generation: projectMetaQuery.data.head_generation,
                } : null}
                onClose={() => setLifecycleAction(null)}
                onCompleted={() => setLifecycleAction(null)}
            />

            <ProjectAttachmentDialog
                open={attachOpen}
                projectId={project.id}
                projectName={project.name}
                onClose={() => setAttachOpen(false)}
                onAttached={() => {
                    setAttachOpen(false);
                    void queryClient.invalidateQueries({ queryKey: ['project-hub', projectId] });
                }}
            />
        </main>
    );
}
