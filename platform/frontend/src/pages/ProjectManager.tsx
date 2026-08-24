import { isAxiosError } from 'axios';
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
    createLaunchContext,
    getDomainRunGroup,
    getDomainWorkflowPlan,
    getProjectSummary,
    getResultSurface,
    internalRouteHref,
    isPermissionError,
    issuePreparedLaunchContext,
    listDomainWorkflowPlanRevisions,
    prepareDomainWorkflowPlanRevision,
    publishDomainWorkflowPlanRevision,
    replaceDomainWorkflowPlanDraft,
    retryDomainRunGroup,
    searchProjects,
    projectManagerErrorMessage,
    type JsonObject,
    type ProjectListItem,
    type ProjectManagerReadModel,
    type ProjectMapNode,
} from '../lib/projectManager';
import { ProjectAttachmentDialog } from '../components/project-manager/ProjectAttachmentDialog';
import { ManagerDialog, type ManagerDialogMode } from '../components/project-manager/ManagerDialog';
import { ProjectInspector } from '../components/project-manager/ProjectInspector';
import { ProjectTree } from '../components/project-manager/ProjectTree';
import { RelationshipMap } from '../components/project-manager/RelationshipMap';
import { RunPanel } from '../components/project-manager/RunPanel';
import { VirtualFolderPanel, type FolderKind } from '../components/project-manager/VirtualFolderPanel';
import { focusIdFromReadModel, globalExperimentForNode } from '../components/project-manager/projectManagerState';

const MAP_LIMIT = 50;
const RUN_LIMIT = 25;
const COLLECTION_LIMIT = 25;

function mergeJsonPages(current: JsonObject[], incoming: JsonObject[]): JsonObject[] {
    const merged = new Map<string, JsonObject>();
    const keyFor = (item: JsonObject, index: number) => {
        for (const key of ['id', 'resource_id', 'receipt_id', 'edge_key', 'entity_id']) {
            if (typeof item[key] === 'string') return `${key}:${item[key]}`;
        }
        return `index:${index}:${JSON.stringify(item)}`;
    };
    current.forEach((item, index) => merged.set(keyFor(item, index), item));
    incoming.forEach((item, index) => merged.set(keyFor(item, current.length + index), item));
    return Array.from(merged.values());
}

function folderKindFromNodeKey(nodeKey: string | undefined): FolderKind | null {
    if (!nodeKey?.startsWith('virtual_folder:')) return null;
    const candidate = nodeKey.split(':').at(-1);
    return candidate && ['plans', 'runs', 'results', 'datasets', 'notes', 'decisions', 'activity', 'lineage'].includes(candidate)
        ? candidate as FolderKind
        : null;
}

function domainExperimentForNode(summary: ProjectManagerReadModel, nodeKey: string): string | null {
    const nodes = [...summary.tree.nodes, ...summary.map.nodes];
    const byKey = new Map(nodes.map((node) => [node.node_key, node]));
    let current = byKey.get(nodeKey);
    if (!current && summary.selection.node_key === nodeKey) {
        const selectedParent = summary.selection.relationship.parent_node_key;
        if (typeof selectedParent === 'string' && selectedParent.startsWith('domain_experiment:')) {
            return selectedParent.slice('domain_experiment:'.length) || null;
        }
        if (typeof selectedParent === 'string') current = byKey.get(selectedParent);
    }
    const visited = new Set<string>();
    while (current && !visited.has(current.node_key)) {
        visited.add(current.node_key);
        if (current.node_type === 'domain_experiment') {
            const canonicalId = 'canonical_identity' in current
                ? current.canonical_identity.entity_id
                : null;
            const subjectId = 'subject_id' in current ? current.subject_id : null;
            return canonicalId ?? subjectId ?? null;
        }
        const structuralParent = 'parent_node_key' in current ? current.parent_node_key : undefined;
        const currentNodeKey = current.node_key;
        const parentNodeKey = structuralParent
            ?? summary.map.edges.find((edge) => edge.target_node_key === currentNodeKey)?.source_node_key;
        current = parentNodeKey ? byKey.get(parentNodeKey) : undefined;
    }
    const selectedParent = summary.selection.node_key === nodeKey
        ? summary.selection.relationship.parent_node_key
        : null;
    if (typeof selectedParent === 'string' && selectedParent.startsWith('domain_experiment:')) {
        return selectedParent.slice('domain_experiment:'.length) || null;
    }
    return null;
}

function ProjectManagerErrorState({ error, onRetry, permission = false }: { error: unknown; onRetry: () => void; permission?: boolean }) {
    return (
        <section role="alert" className="grid min-h-[32rem] place-items-center p-6">
            <div className="max-w-lg rounded-2xl border border-error/50 bg-surface-secondary p-6 text-center shadow-xl">
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-error">{permission ? 'Project access unavailable' : 'Project Manager unavailable'}</p>
                <h1 className="mt-2 text-xl font-semibold text-content">{permission ? 'You do not have permission to view this Project' : 'The Project work surface could not be loaded'}</h1>
                <p className="mt-3 text-sm text-content-secondary">{projectManagerErrorMessage(error)}</p>
                <button type="button" onClick={onRetry} className="mt-5 rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white focus:ring-2 focus:ring-accent">Retry</button>
            </div>
        </section>
    );
}

function ProjectsIndex() {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [dialogMode, setDialogMode] = useState<ManagerDialogMode | null>(null);
    const [query, setQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [archiveFilter, setArchiveFilter] = useState('active');
    const projectsQuery = useInfiniteQuery({
        queryKey: ['project-manager', 'projects', query.trim(), statusFilter, archiveFilter],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam, signal }) => searchProjects({
            query,
            status: statusFilter,
            archive: archiveFilter as 'active' | 'archived' | 'all',
            cursor: pageParam,
            limit: 50,
            projectScope: 'global',
            signal,
        }),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
    });
    const projectPages = projectsQuery.data?.pages;
    const projects = useMemo(() => {
        const byId = new Map<string, ProjectListItem>();
        for (const page of projectPages ?? []) {
            for (const project of page.items) byId.set(project.id, project);
        }
        return Array.from(byId.values());
    }, [projectPages]);

    if (projectsQuery.isError) {
        return <ProjectManagerErrorState error={projectsQuery.error} permission={isPermissionError(projectsQuery.error)} onRetry={() => void projectsQuery.refetch()} />;
    }

    return (
        <div className="min-h-full bg-surface p-4 sm:p-6 lg:p-8">
            <div className="mx-auto max-w-6xl">
                <header className="flex flex-wrap items-end justify-between gap-4">
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-accent">Global research organization</p>
                        <h1 className="mt-2 text-2xl font-semibold text-content sm:text-3xl">Project Manager</h1>
                        <p className="mt-2 max-w-2xl text-sm text-content-secondary">Open a durable Project relationship map or create a new research container. Scientific records remain authoritative in their owning BMS stores.</p>
                    </div>
                    <button type="button" onClick={() => setDialogMode('create_project')} className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white focus:ring-2 focus:ring-accent">Create Project</button>
                </header>

                <section aria-label="Project discovery controls" className="mt-6 grid gap-3 rounded-xl border border-border-primary bg-surface-secondary p-3 md:grid-cols-[minmax(12rem,1fr)_auto_auto_auto]">
                    <input aria-label="Search Projects" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, objective, owner, or tag" className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content" />
                    <select aria-label="Project status filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs text-content"><option value="all">All statuses</option><option value="draft">Draft</option><option value="active">Active</option><option value="on_hold">On hold</option><option value="completed">Completed</option></select>
                    <select aria-label="Archive filter" value={archiveFilter} onChange={(event) => setArchiveFilter(event.target.value)} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs text-content"><option value="active">Current only</option><option value="archived">Archived only</option><option value="all">Current and archived</option></select>
                    <span aria-label="Project order" className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs text-content">Recent activity</span>
                </section>

                {projectsQuery.isLoading ? (
                    <div aria-busy="true" className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {[0, 1, 2].map((item) => <div key={item} className="h-40 animate-pulse rounded-2xl border border-border-primary bg-surface-secondary" />)}
                    </div>
                ) : projects.length ? (
                    <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {projects.map((project) => {
                            const payload = project.payload ?? {};
                            const activeCount = typeof project.active_experiment_count === 'number' ? project.active_experiment_count : typeof payload.active_experiment_count === 'number' ? payload.active_experiment_count : null;
                            const failureCount = typeof project.unresolved_failure_count === 'number' ? project.unresolved_failure_count : typeof payload.unresolved_failure_count === 'number' ? payload.unresolved_failure_count : null;
                            return (
                                <Link data-project-card key={project.id} to={`/projects/${encodeURIComponent(project.id)}`} className="group rounded-2xl border border-border-primary bg-surface-secondary p-5 shadow-sm outline-none transition hover:-translate-y-0.5 hover:border-accent hover:shadow-xl focus:ring-2 focus:ring-accent">
                                    <div className="flex items-start justify-between gap-3"><span className="rounded-full border border-border-primary px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-content-secondary">{project.status}</span><span className="text-[10px] text-content-muted">Revision {project.head_generation}</span></div>
                                    <h2 className="mt-4 text-lg font-semibold text-content group-hover:text-accent">{project.name}</h2>
                                    <p className="mt-2 line-clamp-3 text-xs leading-5 text-content-secondary">{typeof payload.research_objective === 'string' ? payload.research_objective : project.description || 'No research objective recorded.'}</p>
                                    <div className="mt-3 space-y-1 text-[10px] text-content-muted"><p>{activeCount === null ? 'Active experiments unavailable' : `${activeCount} active experiments`}</p><p>{failureCount === null ? 'Unresolved failures unavailable' : `${failureCount} unresolved failures`}</p></div>
                                    <p className="mt-4 text-[10px] text-content-muted">Updated {new Date(project.updated_at).toLocaleString()}</p>
                                </Link>
                            );
                        })}
                        {projectsQuery.hasNextPage ? (
                            <button
                                type="button"
                                onClick={() => void projectsQuery.fetchNextPage()}
                                disabled={projectsQuery.isFetchingNextPage}
                                className="rounded-2xl border border-dashed border-accent p-5 text-sm font-semibold text-accent disabled:opacity-50"
                            >
                                {projectsQuery.isFetchingNextPage ? 'Loading Projects…' : 'Load more Projects'}
                            </button>
                        ) : null}
                    </div>
                ) : (
                    <section className="mt-8 rounded-2xl border border-dashed border-border-secondary bg-surface-secondary p-10 text-center"><h2 className="text-lg font-semibold text-content">No Projects match</h2><p className="mt-2 text-sm text-content-secondary">Change the discovery filters or create a durable Project.</p><button type="button" onClick={() => setDialogMode('create_project')} className="mt-5 rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white">Create Project</button></section>
                )}
            </div>
            <ManagerDialog mode={dialogMode} onClose={() => setDialogMode(null)} onComplete={(destination) => { void queryClient.invalidateQueries({ queryKey: ['project-manager', 'projects'] }); if (destination?.projectId) navigate(`/projects/${encodeURIComponent(destination.projectId)}`); }} />
        </div>
    );
}

function isValidatedSelectionFailure(error: unknown): boolean {
    return isAxiosError(error) && error.response?.status === 422;
}

function ProjectWorkspace({ projectId, routeFocusId, routeDomainId }: { projectId: string; routeFocusId?: string; routeDomainId?: string }) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [searchParams, setSearchParams] = useSearchParams();
    const focusId = searchParams.get('focus') ?? routeFocusId;
    const selectedNodeKey = searchParams.get('selected')
        ?? (routeDomainId ? `domain_experiment:${routeDomainId}` : routeFocusId ? `global_experiment:${routeFocusId}` : undefined);
    const [mapCursor, setMapCursor] = useState<string | undefined>();
    const [runCursor, setRunCursor] = useState<string | undefined>();
    const [resultCursor, setResultCursor] = useState<string | undefined>();
    const [lineageCursor, setLineageCursor] = useState<string | undefined>();
    const [noteCursor, setNoteCursor] = useState<string | undefined>();
    const [decisionCursor, setDecisionCursor] = useState<string | undefined>();
    const [datasetCursor, setDatasetCursor] = useState<string | undefined>();
    const [activityCursor, setActivityCursor] = useState<string | undefined>();
    const [treeOpen, setTreeOpen] = useState(true);
    const [inspectorOpen, setInspectorOpen] = useState(true);
    const [treeWidth, setTreeWidth] = useState(272);
    const [inspectorWidth, setInspectorWidth] = useState(368);
    const [attachOpen, setAttachOpen] = useState(false);
    const [dialogMode, setDialogMode] = useState<ManagerDialogMode | null>(null);

    const [accumulatedMap, setAccumulatedMap] = useState<{
        contextKey: string;
        nodes: ProjectManagerReadModel['map']['nodes'];
        edges: ProjectManagerReadModel['map']['edges'];
    } | null>(null);
    const [accumulatedRuns, setAccumulatedRuns] = useState<{
        contextKey: string;
        items: ProjectManagerReadModel['runs']['items'];
    } | null>(null);
    const [accumulatedCollections, setAccumulatedCollections] = useState<{
        contextKey: string;
        results: JsonObject[];
        lineage: JsonObject[];
        notes: JsonObject[];
        decisions: JsonObject[];
        datasets: JsonObject[];
        activity: JsonObject[];
    } | null>(null);

    const summaryQuery = useQuery({
        queryKey: ['project-manager', 'summary', projectId, focusId ?? null, selectedNodeKey ?? null, mapCursor ?? null, runCursor ?? null, resultCursor ?? null, lineageCursor ?? null, noteCursor ?? null, decisionCursor ?? null, datasetCursor ?? null, activityCursor ?? null],
        queryFn: ({ signal }) => getProjectSummary(projectId, {
            focusId,
            selectedNodeKey,
            mapCursor,
            runCursor,
            resultCursor,
            lineageCursor,
            noteCursor,
            decisionCursor,
            datasetCursor,
            activityCursor,
            mapLimit: MAP_LIMIT,
            runLimit: RUN_LIMIT,
            resultLimit: COLLECTION_LIMIT,
            lineageLimit: COLLECTION_LIMIT,
            noteLimit: COLLECTION_LIMIT,
            decisionLimit: COLLECTION_LIMIT,
            datasetLimit: COLLECTION_LIMIT,
            activityLimit: COLLECTION_LIMIT,
            signal,
        }),
        placeholderData: keepPreviousData,
    });
    const invalidSelection = summaryQuery.isError && isValidatedSelectionFailure(summaryQuery.error) && Boolean(focusId || selectedNodeKey);
    const fallbackQuery = useQuery({
        queryKey: ['project-manager', 'summary', projectId, 'validated-fallback'],
        queryFn: ({ signal }) => getProjectSummary(projectId, { mapLimit: MAP_LIMIT, runLimit: RUN_LIMIT, signal }),
        enabled: invalidSelection,
    });
    const rawSummary = summaryQuery.isPlaceholderData
        ? fallbackQuery.data
        : summaryQuery.data ?? fallbackQuery.data;
    const projectLevelError = summaryQuery.isError && !invalidSelection;
    const mapContextKey = `${projectId}:${focusId ?? 'default'}`;
    const selectedDomainScope = selectedNodeKey?.startsWith('virtual_folder:')
        ? selectedNodeKey.split(':')[1]
        : selectedNodeKey?.startsWith('domain_experiment:')
            ? selectedNodeKey.split(':')[1]
            : 'all-domains';
    const collectionContextKey = `${mapContextKey}:${selectedDomainScope}`;

    useEffect(() => {
        if (!rawSummary) return;
        setAccumulatedMap((current) => {
            if (!current || current.contextKey !== mapContextKey) {
                return { contextKey: mapContextKey, nodes: rawSummary.map.nodes, edges: rawSummary.map.edges };
            }
            const nodes = new Map(current.nodes.map((item) => [item.node_key, item]));
            for (const item of rawSummary.map.nodes) nodes.set(item.node_key, item);
            const edges = new Map(current.edges.map((item) => [item.edge_key, item]));
            for (const item of rawSummary.map.edges) edges.set(item.edge_key, item);
            return {
                contextKey: mapContextKey,
                nodes: Array.from(nodes.values()),
                edges: Array.from(edges.values()),
            };
        });
    }, [mapContextKey, rawSummary]);

    useEffect(() => {
        if (!rawSummary) return;
        setAccumulatedRuns((current) => {
            if (!current || current.contextKey !== mapContextKey) return { contextKey: mapContextKey, items: rawSummary.runs.items };
            const items = new Map(current.items.map((item) => [item.run_id, item]));
            for (const item of rawSummary.runs.items) items.set(item.run_id, item);
            return { contextKey: mapContextKey, items: Array.from(items.values()) };
        });
    }, [mapContextKey, rawSummary]);

    useEffect(() => {
        if (!rawSummary) return;
        const pages = rawSummary.pagination;
        const incoming = {
            results: pages.results.items,
            lineage: pages.lineage.items,
            notes: pages.notes.items,
            decisions: pages.decisions.items,
            datasets: pages.datasets.items,
            activity: pages.activity.items as unknown as JsonObject[],
        };
        setAccumulatedCollections((current) => {
            if (!current || current.contextKey !== collectionContextKey) return { contextKey: collectionContextKey, ...incoming };
            return {
                contextKey: collectionContextKey,
                results: mergeJsonPages(current.results, incoming.results),
                lineage: mergeJsonPages(current.lineage, incoming.lineage),
                notes: mergeJsonPages(current.notes, incoming.notes),
                decisions: mergeJsonPages(current.decisions, incoming.decisions),
                datasets: mergeJsonPages(current.datasets, incoming.datasets),
                activity: mergeJsonPages(current.activity, incoming.activity),
            };
        });
    }, [collectionContextKey, rawSummary]);

    const summary = useMemo(() => {
        if (!rawSummary) return undefined;
        const map = accumulatedMap?.contextKey === mapContextKey
            ? { ...rawSummary.map, nodes: accumulatedMap.nodes, edges: accumulatedMap.edges }
            : rawSummary.map;
        const runs = accumulatedRuns?.contextKey === mapContextKey
            ? { ...rawSummary.runs, items: accumulatedRuns.items }
            : rawSummary.runs;
        const pagination = accumulatedCollections?.contextKey === collectionContextKey
            ? {
                ...rawSummary.pagination,
                results: { ...rawSummary.pagination.results, items: accumulatedCollections.results },
                lineage: { ...rawSummary.pagination.lineage, items: accumulatedCollections.lineage },
                notes: { ...rawSummary.pagination.notes, items: accumulatedCollections.notes },
                decisions: { ...rawSummary.pagination.decisions, items: accumulatedCollections.decisions },
                datasets: { ...rawSummary.pagination.datasets, items: accumulatedCollections.datasets },
                activity: { ...rawSummary.pagination.activity, items: accumulatedCollections.activity as unknown as ProjectManagerReadModel['pagination']['activity']['items'] },
            }
            : rawSummary.pagination;
        return { ...rawSummary, map, runs, pagination };
    }, [accumulatedCollections, accumulatedMap, accumulatedRuns, collectionContextKey, mapContextKey, rawSummary]);

    useEffect(() => {
        setMapCursor(undefined);
        setRunCursor(undefined);
        setResultCursor(undefined);
        setLineageCursor(undefined);
        setNoteCursor(undefined);
        setDecisionCursor(undefined);
        setDatasetCursor(undefined);
        setActivityCursor(undefined);
    }, [focusId]);

    useEffect(() => {
        if (!summary || invalidSelection) return;
        const next = new URLSearchParams(searchParams);
        let changed = false;
        if (!next.get('focus')) { next.set('focus', focusId ?? focusIdFromReadModel(summary)); changed = true; }
        if (!next.get('selected')) { next.set('selected', selectedNodeKey ?? summary.selection.node_key); changed = true; }
        next.delete('focus_id');
        next.delete('selected_node_key');
        if (changed || searchParams.has('focus_id') || searchParams.has('selected_node_key')) setSearchParams(next, { replace: true });
    }, [focusId, invalidSelection, searchParams, selectedNodeKey, setSearchParams, summary]);

    const setSelection = useCallback((nodeKey: string, nodeType?: string, subjectId?: string | null) => {
        if (!summary) return;
        const next = new URLSearchParams(searchParams);
        next.set('selected', nodeKey);
        if (nodeType === 'global_experiment' && subjectId) next.set('focus', subjectId);
        else {
            const globalId = globalExperimentForNode(summary, nodeKey);
            if (globalId) next.set('focus', globalId);
        }
        next.delete('map_cursor');
        setSearchParams(next);

        setMapCursor(undefined);
        setRunCursor(undefined);
        setResultCursor(undefined);
        setLineageCursor(undefined);
        setNoteCursor(undefined);
        setDecisionCursor(undefined);
        setDatasetCursor(undefined);
        setActivityCursor(undefined);
        setInspectorOpen(true);
    }, [searchParams, setSearchParams, summary]);

    const surfaceMutation = useMutation({
        mutationFn: async () => {
            if (!summary) throw new Error('No validated selection is available.');
            const surface = summary.selection.canonical_surface ?? await (async () => {
                const receiptId = summary.selection.canonical_identity.receipt_id;
                if (typeof receiptId !== 'string') throw new Error('The server did not issue a receipt-backed canonical surface.');
                return getResultSurface(projectId, receiptId);
            })();
            if (!surface.route) {
                throw new Error('The server did not issue a same-origin canonical route.');
            }
            const globalExperimentId = globalExperimentForNode(summary, summary.selection.node_key)
                ?? focusId
                ?? focusIdFromReadModel(summary);
            const domainExperimentId = domainExperimentForNode(summary, summary.selection.node_key);
            if (!globalExperimentId || !domainExperimentId) {
                throw new Error('The selected surface has no validated Domain Experiment launch context.');
            }
            const selectedNode = [...summary.map.nodes, ...summary.tree.nodes]
                .find((node) => node.node_key === summary.selection.node_key);
            const workflowId = selectedNode?.node_type === 'workflow'
                ? selectedNode.canonical_identity.entity_id
                : null;
            const workflowRevisionId = workflowId && typeof summary.selection.summary.current_revision_id === 'string'
                ? summary.selection.summary.current_revision_id
                : null;
            const returnQuery = new URLSearchParams();
            returnQuery.set('focus', globalExperimentId);
            returnQuery.set('selected', summary.selection.node_key);
            const returnUri = `/projects/${encodeURIComponent(projectId)}?${returnQuery.toString()}`;
            const launchContext = await createLaunchContext(
                projectId,
                globalExperimentId,
                domainExperimentId,
                {
                    workflow_id: workflowId,
                    workflow_revision_id: workflowRevisionId,
                    return_uri: returnUri,
                },
            );
            const route = new URL(internalRouteHref(surface.route), window.location.origin);
            route.searchParams.set('launch_context_id', launchContext.launch_context_id);
            return `${route.pathname}${route.search}`;
        },
        onSuccess: (route) => navigate(route),
    });

    const openNgsMolBioWorkspace = () => {
        if (!summary) return;
        const selectedGlobalExperimentId = globalExperimentForNode(summary, summary.selection.node_key)
            ?? focusId
            ?? focusIdFromReadModel(summary);
        const selectedDomainExperimentId = domainExperimentForNode(summary, summary.selection.node_key);
        if (!selectedGlobalExperimentId || !selectedDomainExperimentId) return;
        const query = new URLSearchParams({
            workspace_id: projectId,
            global_experiment_id: selectedGlobalExperimentId,
            domain_experiment_id: selectedDomainExperimentId,
            section: 'workflow-plans',
            ownership_scope: 'global',
        });
        navigate(`/ngs?${query.toString()}`);
    };

    const runActionMutation = useMutation({
        mutationFn: async ({ action, run }: { action: string; run: ProjectManagerReadModel['runs']['items'][number] }) => {
            if (!summary) throw new Error('No validated Project context is available.');
            const workflowNodeKey = `workflow:${run.workflow_id}`;
            const domainExperimentId = domainExperimentForNode(summary, workflowNodeKey);
            if (action === 'retry' || action === 'resubmit' || action === 'clone') {
                if (!run.batch_or_run_group_id) throw new Error('The server did not issue a run-group identity.');
                const globalExperimentId = globalExperimentForNode(summary, workflowNodeKey)
                    ?? focusId
                    ?? focusIdFromReadModel(summary);
                if (!globalExperimentId || !domainExperimentId) {
                    throw new Error('The run has no validated Domain Experiment context.');
                }
                if (action === 'retry' && run.adapter_id === 'bms.core-job.esmfold2.adapter.v1') {
                    const sourceAttempt = run.attempts.at(-1);
                    const binding = sourceAttempt?.binding_receipt;
                    const workflowRevisionId = binding && typeof binding.workflow_revision_id === 'string'
                        ? binding.workflow_revision_id
                        : null;
                    if (!workflowRevisionId) {
                        throw new Error('The failed ESMFold2 attempt has no immutable workflow revision binding.');
                    }
                    const [group, plan, revisions] = await Promise.all([
                        getDomainRunGroup(
                            projectId,
                            globalExperimentId,
                            domainExperimentId,
                            run.batch_or_run_group_id,
                        ),
                        getDomainWorkflowPlan(
                            projectId,
                            globalExperimentId,
                            domainExperimentId,
                            run.workflow_id,
                        ),
                        listDomainWorkflowPlanRevisions(
                            projectId,
                            globalExperimentId,
                            domainExperimentId,
                            run.workflow_id,
                        ),
                    ]);
                    const sourceRevision = revisions.items.find((item) => item.revision_id === workflowRevisionId);
                    if (!sourceRevision || plan.draft_generation === null) {
                        throw new Error('The failed ESMFold2 revision cannot be copied into a fresh immutable retry revision.');
                    }
                    const draft = await replaceDomainWorkflowPlanDraft(
                        projectId,
                        globalExperimentId,
                        domainExperimentId,
                        run.workflow_id,
                        plan.draft_generation,
                        sourceRevision.payload,
                    );
                    const retryRevision = await publishDomainWorkflowPlanRevision(
                        projectId,
                        globalExperimentId,
                        domainExperimentId,
                        run.workflow_id,
                        {
                            expected_head_generation: plan.head_generation,
                            expected_draft_generation: draft.generation,
                            change_summary: `Retry failed ESMFold2 attempt ${sourceAttempt?.attempt_id ?? run.run_id}`,
                        },
                    );
                    const preparation = await prepareDomainWorkflowPlanRevision(
                        projectId,
                        globalExperimentId,
                        domainExperimentId,
                        run.workflow_id,
                        retryRevision.revision_id,
                        [],
                    );
                    const returnQuery = new URLSearchParams({
                        focus: globalExperimentId,
                        selected: `workflow_run:${run.run_id}`,
                    });
                    const launchContext = await issuePreparedLaunchContext(
                        projectId,
                        globalExperimentId,
                        domainExperimentId,
                        preparation.preparation_id,
                        `/projects/${encodeURIComponent(projectId)}?${returnQuery.toString()}`,
                    );
                    await retryDomainRunGroup(
                        projectId,
                        globalExperimentId,
                        domainExperimentId,
                        run.batch_or_run_group_id,
                        group.generation,
                        [{
                            run_id: run.run_id,
                            preparation_id: preparation.preparation_id,
                            launch_context_id: launchContext.launch_context_id,
                        }],
                    );
                    return { kind: 'refresh' as const };
                }
                const query = new URLSearchParams({
                    workspace_id: projectId,
                    global_experiment_id: globalExperimentId,
                    domain_experiment_id: domainExperimentId,
                    section: 'workflow-plans',
                    run_group_id: run.batch_or_run_group_id,
                    ownership_scope: 'global',
                    run_group_action: action,
                });
                if (action === 'clone') {
                    const sourceAttempt = run.attempts.at(-1);
                    if (!sourceAttempt) throw new Error('The server did not issue an exact source attempt for clone.');
                    query.set('source_run_id', run.run_id);
                    query.set('source_attempt_id', sourceAttempt.attempt_id);
                }
                return { kind: 'route' as const, route: `/ngs?${query.toString()}` };
            }
            if (action === 'view_lineage') {
                if (!domainExperimentId) throw new Error('The run has no validated Domain Experiment context.');
                return { kind: 'selection' as const, nodeKey: `virtual_folder:${domainExperimentId}:lineage` };
            }
            if (action === 'open_results') {
                const surface = run.canonical_surface;
                if (!surface?.route) {
                    throw new Error('The server did not issue a same-origin canonical result route.');
                }
                const globalExperimentId = globalExperimentForNode(summary, workflowNodeKey)
                    ?? focusId
                    ?? focusIdFromReadModel(summary);
                if (!globalExperimentId || !domainExperimentId) throw new Error('The run has no validated Project launch context.');
                const returnQuery = new URLSearchParams({
                    focus: globalExperimentId,
                    selected: `workflow_run:${run.run_id}`,
                });
                const launchContext = await createLaunchContext(projectId, globalExperimentId, domainExperimentId, {
                    workflow_id: run.workflow_id,
                    workflow_revision_id: null,
                    return_uri: `/projects/${encodeURIComponent(projectId)}?${returnQuery.toString()}`,
                });
                const route = new URL(internalRouteHref(surface.route), window.location.origin);
                route.searchParams.set('launch_context_id', launchContext.launch_context_id);
                return { kind: 'route' as const, route: `${route.pathname}${route.search}` };
            }
            throw new Error(`Unsupported server-issued run action: ${action}`);
        },
        onSuccess: (result) => {
            if (result.kind === 'route') navigate(result.route);
            else if (result.kind === 'selection') setSelection(result.nodeKey, 'virtual_folder', null);
            else void queryClient.invalidateQueries({ queryKey: ['project-manager', 'summary', projectId] });
        },
    });

    const selectFolderRecord = (folder: FolderKind, item: JsonObject) => {
        const id = typeof item.id === 'string' ? item.id : typeof item.resource_id === 'string' ? item.resource_id : null;
        const receiptId = typeof item.receipt_id === 'string' ? item.receipt_id : null;
        if (folder === 'results' || folder === 'lineage') {
            if (receiptId) setSelection(`external_entity_receipt:${receiptId}`, 'external_entity_receipt', receiptId);
        } else if (folder === 'notes' || folder === 'decisions') {
            if (id) setSelection(`research_record:${id}`, 'research_record', id);
        } else if (folder === 'plans') {
            if (id) setSelection(id, 'workflow', id.split(':').at(-1) ?? null);
        } else if (folder === 'datasets') {
            if (id) setSelection(id, 'dataset', typeof item.dataset_id === 'string' ? item.dataset_id : null);
        } else if (folder === 'runs' && id) {
            setSelection(`workflow_run:${id}`, 'workflow_run', id);
        }
    };

    const refreshSummary = useCallback(async (destination?: { focusId?: string; selectedNodeKey?: string }) => {
        await queryClient.invalidateQueries({ queryKey: ['project-manager', 'summary', projectId] });
        if (destination?.focusId || destination?.selectedNodeKey) {
            const next = new URLSearchParams(searchParams);
            if (destination.focusId) next.set('focus', destination.focusId);
            if (destination.selectedNodeKey) next.set('selected', destination.selectedNodeKey);
            setSearchParams(next);
        }
    }, [projectId, queryClient, searchParams, setSearchParams]);

    if (projectLevelError) {
        return <ProjectManagerErrorState error={summaryQuery.error} permission={isPermissionError(summaryQuery.error)} onRetry={() => void summaryQuery.refetch()} />;
    }
    if (invalidSelection && fallbackQuery.isError) {
        return <ProjectManagerErrorState error={fallbackQuery.error} permission={isPermissionError(fallbackQuery.error)} onRetry={() => void fallbackQuery.refetch()} />;
    }
    if (!summary) {
        return (
            <div aria-busy="true" className="grid min-h-[32rem] place-items-center p-6 text-sm text-content-secondary">
                <div className="text-center"><div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-border-primary border-t-accent" /><p className="mt-3">Loading bounded Project map…</p></div>
            </div>
        );
    }

    const selectionUnavailable = invalidSelection ? projectManagerErrorMessage(summaryQuery.error) : null;
    const busy = summaryQuery.isFetching || fallbackQuery.isFetching;
    const folderKind = folderKindFromNodeKey(selectedNodeKey);
    const loadMoreFolder = (folder: FolderKind) => {
        if (folder === 'plans') setMapCursor(summary.map.next_cursor ?? undefined);
        else if (folder === 'datasets') setDatasetCursor(summary.pagination.datasets.next_cursor ?? undefined);
        else if (folder === 'runs') setRunCursor(summary.runs.next_cursor ?? undefined);
        else if (folder === 'results') setResultCursor(summary.pagination.results.next_cursor ?? undefined);
        else if (folder === 'lineage') setLineageCursor(summary.pagination.lineage.next_cursor ?? undefined);
        else if (folder === 'notes') setNoteCursor(summary.pagination.notes.next_cursor ?? undefined);
        else if (folder === 'decisions') setDecisionCursor(summary.pagination.decisions.next_cursor ?? undefined);
        else if (folder === 'activity') setActivityCursor(summary.pagination.activity.next_cursor ?? undefined);
    };
    const inspectExecution = (kind: 'workflow' | 'workflow_run', id: string, _run: ProjectManagerReadModel['runs']['items'][number]) => {
        const nodeKey = `${kind}:${id}`;
        setSelection(nodeKey, kind, null);
    };
    const startRailResize = (rail: 'tree' | 'inspector', event: ReactPointerEvent<HTMLDivElement>) => {
        event.preventDefault();
        const start = event.clientX;
        const initial = rail === 'tree' ? treeWidth : inspectorWidth;
        const onMove = (move: PointerEvent) => {
            const delta = move.clientX - start;
            const value = rail === 'tree' ? initial + delta : initial - delta;
            if (rail === 'tree') setTreeWidth(Math.min(400, Math.max(208, value)));
            else setInspectorWidth(Math.min(520, Math.max(288, value)));
        };
        const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
    };
    const gridClass = treeOpen
        ? inspectorOpen
            ? 'md:grid-cols-[var(--tree-width)_0.5rem_minmax(0,1fr)] xl:grid-cols-[var(--tree-width)_0.5rem_minmax(0,1fr)_0.5rem_var(--inspector-width)]'
            : 'md:grid-cols-[var(--tree-width)_0.5rem_minmax(0,1fr)]'
        : inspectorOpen
            ? 'xl:grid-cols-[minmax(0,1fr)_0.5rem_var(--inspector-width)]'
            : 'grid-cols-1';

    return (
        <div className="relative flex h-full min-h-[calc(100vh-4rem)] flex-col overflow-hidden bg-surface" data-project-manager="true">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border-primary bg-surface-secondary px-3 py-2 sm:px-4">
                <div className="flex min-w-0 items-center gap-3">
                    <Link to="/projects" className="rounded-lg border border-border-primary px-2.5 py-2 text-xs text-content-secondary outline-none hover:text-content focus:ring-2 focus:ring-accent">All Projects</Link>
                    <div className="min-w-0">
                        <h1 className="truncate text-sm font-semibold text-content">{summary.project.name}</h1>
                        <p className="truncate text-[10px] text-content-muted">{summary.project.objective || 'No objective recorded'} · revision {summary.project.head_generation}</p>
                    </div>
                    {busy && <span role="status" className="text-[10px] font-medium text-accent">Refreshing…</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" onClick={() => setTreeOpen((value) => !value)} className="rounded-lg border border-border-primary px-3 py-2 text-xs text-content-secondary focus:ring-2 focus:ring-accent">{treeOpen ? 'Hide tree' : 'Show tree'}</button>
                    <button type="button" onClick={() => setInspectorOpen((value) => !value)} className="rounded-lg border border-border-primary px-3 py-2 text-xs text-content-secondary focus:ring-2 focus:ring-accent">{inspectorOpen ? 'Hide inspector' : 'Show inspector'}</button>
                    {summary.allowed_actions.includes('create_global_experiment') && <button type="button" onClick={() => setDialogMode('create_global')} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white focus:ring-2 focus:ring-accent">New Global Experiment</button>}
                </div>
            </header>

            {summary.warnings.length > 0 && (
                <div role="status" className="border-b border-warning/40 bg-warning/10 px-4 py-2 text-xs text-warning">{summary.warnings.join(' · ')}</div>
            )}
            {surfaceMutation.isError && (
                <div role="alert" className="border-b border-error/40 bg-error/10 px-4 py-2 text-xs text-error">Canonical source unavailable: {projectManagerErrorMessage(surfaceMutation.error)}</div>
            )}
            {runActionMutation.isError && (
                <div role="alert" className="border-b border-error/40 bg-error/10 px-4 py-2 text-xs text-error">Run action unavailable: {projectManagerErrorMessage(runActionMutation.error)}</div>
            )}

            <div
                className={`relative grid min-h-0 flex-1 grid-cols-1 ${gridClass}`}
                style={{ '--tree-width': `${treeWidth}px`, '--inspector-width': `${inspectorWidth}px` } as CSSProperties}
            >
                {treeOpen && (
                    <div className="fixed inset-y-0 left-0 z-[70] w-[min(88vw,25rem)] shadow-2xl md:static md:z-auto md:w-auto md:shadow-none">
                        <ProjectTree
                            nodes={summary.tree.nodes}
                            selectedNodeKey={summary.selection.node_key}
                            onSelect={(nodeKey) => {
                                const node = summary.tree.nodes.find((item) => item.node_key === nodeKey);
                                if (node) setSelection(node.node_key, node.node_type, node.subject_id);
                            }}
                            onClose={() => setTreeOpen(false)}
                        />
                    </div>
                )}
                {treeOpen && <div role="separator" aria-orientation="vertical" aria-label="Resize Project tree" tabIndex={0} onPointerDown={(event) => startRailResize('tree', event)} onKeyDown={(event) => { if (event.key === 'ArrowLeft') setTreeWidth((value) => Math.max(208, value - 16)); if (event.key === 'ArrowRight') setTreeWidth((value) => Math.min(400, value + 16)); }} className="hidden cursor-col-resize bg-border-primary outline-none focus:bg-accent md:block" />}
                <main className="flex min-h-0 min-w-0 flex-col">
                    <RelationshipMap
                        summary={summary}
                        selectedNodeKey={summary.selection.node_key}
                        onSelect={(node: ProjectMapNode) => setSelection(node.node_key, node.node_type, typeof node.canonical_identity.entity_id === 'string' ? node.canonical_identity.entity_id : null)}
                        onLoadMore={summary.map.next_cursor ? () => setMapCursor(summary.map.next_cursor ?? undefined) : undefined}
                    />
                    <div className="p-3">
                        <VirtualFolderPanel folder={folderKind} summary={summary} onLoadMore={loadMoreFolder} onSelectRecord={selectFolderRecord} loading={busy} />
                    </div>
                    <RunPanel runs={summary.runs.items} selectedNodeKey={summary.selection.node_key} onSelect={inspectExecution} onAction={(action, run) => runActionMutation.mutate({ action, run })} onLoadMore={summary.runs.next_cursor ? () => setRunCursor(summary.runs.next_cursor ?? undefined) : undefined} />
                </main>
                {inspectorOpen && <div role="separator" aria-orientation="vertical" aria-label="Resize Project inspector" tabIndex={0} onPointerDown={(event) => startRailResize('inspector', event)} onKeyDown={(event) => { if (event.key === 'ArrowLeft') setInspectorWidth((value) => Math.min(520, value + 16)); if (event.key === 'ArrowRight') setInspectorWidth((value) => Math.max(288, value - 16)); }} className="hidden cursor-col-resize bg-border-primary outline-none focus:bg-accent xl:block" />}
                {inspectorOpen && (
                    <div className="fixed inset-y-0 right-0 z-[70] w-[min(100vw,32rem)] shadow-2xl xl:static xl:z-auto xl:w-auto xl:shadow-none">
                        <ProjectInspector
                            summary={summary}
                            busy={busy}
                            selectionUnavailable={selectionUnavailable}
                            onClose={() => setInspectorOpen(false)}
                            onOpenCanonical={() => surfaceMutation.mutate()}
                            onOpenNgsMolBio={openNgsMolBioWorkspace}
                            onAddExisting={() => setAttachOpen(true)}
                            onCreateDomain={() => setDialogMode('create_domain')}
                            onEdit={() => setDialogMode('edit')}
                            onArchive={() => setDialogMode('archive')}
                            onRestore={() => setDialogMode('restore')}
                            onRecord={() => setDialogMode('record')}
                        />
                    </div>
                )}
            </div>

            <ProjectAttachmentDialog
                open={attachOpen}
                projectId={projectId}
                summary={summary}
                onClose={() => setAttachOpen(false)}
                onAttached={(receiptId) => {
                    setAttachOpen(false);
                    if (receiptId) setSelection(`external_entity_receipt:${receiptId}`, 'external_entity_receipt', receiptId);
                    void refreshSummary();
                }}
            />
            <ManagerDialog
                mode={dialogMode}
                projectId={projectId}
                summary={summary}
                onClose={() => setDialogMode(null)}
                onComplete={(destination) => void refreshSummary(destination)}
            />
        </div>
    );
}

export function ProjectManager() {
    const { projectId, experimentId, domainId } = useParams<{ projectId?: string; experimentId?: string; domainId?: string }>();
    return projectId ? <ProjectWorkspace projectId={projectId} routeFocusId={experimentId} routeDomainId={domainId} /> : <ProjectsIndex />;
}

export default ProjectManager;
