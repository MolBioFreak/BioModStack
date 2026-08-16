import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    createDomainExperiment,
    createGlobalExperiment,
    createProject,
    linkNgsMolBioProject,
    listDomainExperiments,
    listGlobalExperiments,
    listNgsMolBioProjectLinks,
    listNgsMolBioShareableResults,
    projectManagerErrorMessage,
    searchProjects,
    type HierarchyMutationResult,
    type JsonObject,
} from '../../lib/projectManager';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

const INPUT = 'w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary';
const BUTTON = 'rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs font-semibold text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';

type OwnershipMode = 'local-new' | 'local-existing' | 'global';

interface LocalHierarchy {
    experiments: HierarchyMutationResult[];
    domains: Array<{ experiment: HierarchyMutationResult; domain: HierarchyMutationResult }>;
}

function ngsDomainPayload(objective: string): JsonObject {
    return {
        schema: 'bms.ngs-molbio-experiment.v2',
        experiment_mode: 'analysis',
        scientific_objective: objective,
        planned_capability_ids: [],
        grouping_intent: [],
        acceptance_criteria: [],
        evidence_plan: [],
    };
}

export default function NgsMolBioProjectHub() {
    const queryClient = useQueryClient();
    const { workspaceId, updateQueryParams } = useGlobalExperimentContext();
    const [mode, setMode] = useState<OwnershipMode>('local-new');
    const [projectName, setProjectName] = useState('');
    const [experimentName, setExperimentName] = useState('');
    const [objective, setObjective] = useState('');
    const [selectedLocalProjectId, setSelectedLocalProjectId] = useState('');
    const [targetGlobalProjectId, setTargetGlobalProjectId] = useState('');
    const [selectedExperimentIds, setSelectedExperimentIds] = useState<string[]>([]);
    const [selectedResultIds, setSelectedResultIds] = useState<string[]>([]);

    const localProjectsQuery = useQuery({
        queryKey: ['ngs-molbio-projects', 'local'],
        queryFn: ({ signal }) => searchProjects({ projectScope: 'ngs_molbio_local', archive: 'active', limit: 100, signal }),
        retry: false,
    });
    const globalProjectsQuery = useQuery({
        queryKey: ['ngs-molbio-projects', 'global'],
        queryFn: ({ signal }) => searchProjects({ projectScope: 'global', archive: 'active', limit: 100, signal }),
        retry: false,
    });
    const localProjects = localProjectsQuery.data?.items ?? [];
    const globalProjects = globalProjectsQuery.data?.items ?? [];

    useEffect(() => {
        if (workspaceId && localProjects.some((project) => project.id === workspaceId)) {
            setSelectedLocalProjectId(workspaceId);
            return;
        }
        if (!selectedLocalProjectId && localProjects[0]) setSelectedLocalProjectId(localProjects[0].id);
    }, [localProjects, selectedLocalProjectId, workspaceId]);

    const localHierarchyQuery = useQuery({
        queryKey: ['ngs-molbio-project-hierarchy', selectedLocalProjectId],
        enabled: Boolean(selectedLocalProjectId),
        retry: false,
        queryFn: async ({ signal }): Promise<LocalHierarchy> => {
            const experiments = await listGlobalExperiments(selectedLocalProjectId, signal);
            const nested = await Promise.all(experiments.map(async (experiment) => ({
                experiment,
                domains: await listDomainExperiments(selectedLocalProjectId, experiment.id, signal),
            })));
            return {
                experiments,
                domains: nested.flatMap(({ experiment, domains }) => domains
                    .filter((domain) => domain.domain_kind === 'ngs_molbio' || domain.payload?.domain_kind === 'ngs_molbio')
                    .map((domain) => ({ experiment, domain }))),
            };
        },
    });
    const linksQuery = useQuery({
        queryKey: ['ngs-molbio-project-links', selectedLocalProjectId],
        enabled: Boolean(selectedLocalProjectId),
        queryFn: ({ signal }) => listNgsMolBioProjectLinks(selectedLocalProjectId, signal),
        retry: false,
    });
    const shareableResultsQuery = useQuery({
        queryKey: ['ngs-molbio-shareable-results', selectedLocalProjectId],
        enabled: Boolean(selectedLocalProjectId),
        queryFn: ({ signal }) => listNgsMolBioShareableResults(selectedLocalProjectId, signal),
        retry: false,
    });

    useEffect(() => {
        const available = new Set((localHierarchyQuery.data?.experiments ?? []).map((item) => item.id));
        setSelectedExperimentIds((current) => current.filter((id) => available.has(id)));
    }, [localHierarchyQuery.data]);

    useEffect(() => {
        const available = new Set((shareableResultsQuery.data ?? []).map((item) => item.result_receipt_id));
        setSelectedResultIds((current) => current.filter((id) => available.has(id)));
    }, [shareableResultsQuery.data]);

    const createMutation = useMutation({
        mutationFn: async () => {
            let projectId = mode === 'local-existing' ? selectedLocalProjectId : targetGlobalProjectId;
            if (mode === 'local-new') {
                const project = await createProject({
                    schema: 'bms.project.v1',
                    name: projectName,
                    research_objective: objective,
                    status: 'active',
                    project_scope: 'ngs_molbio_local',
                    change_summary: 'Created in the NGS/MolBio Project layer',
                });
                projectId = project.id;
            }
            if (!projectId) throw new Error('Select a broader Project for a global NGS/MolBio Experiment.');
            const experiment = await createGlobalExperiment(projectId, {
                schema: 'bms.global-experiment.v1',
                name: experimentName,
                objective,
                scientific_question: objective,
                status: 'planned',
                change_summary: mode === 'global' ? 'Created inside broader BMS Project' : 'Created inside local NGS/MolBio Project',
            });
            const domain = await createDomainExperiment(projectId, experiment.id, {
                schema: 'bms.domain-experiment.v2',
                domain_kind: 'ngs_molbio',
                domain_contract_version: '2',
                name: experimentName,
                objective,
                status: 'planned',
                tags: [],
                source_receipt_ids: [],
                dataset_revision_ids: [],
                change_summary: 'Created from two-tier NGS/MolBio Project authoring',
                domain_payload: ngsDomainPayload(objective),
            });
            return { projectId, experiment, domain };
        },
        onSuccess: ({ projectId, experiment, domain }) => {
            void queryClient.invalidateQueries({ queryKey: ['ngs-molbio-projects'] });
            setProjectName('');
            setExperimentName('');
            setObjective('');
            updateQueryParams({
                workspace_id: projectId,
                global_experiment_id: experiment.id,
                domain_experiment_id: domain.id,
                state_revision_id: null,
                ownership_scope: mode === 'global' ? 'global' : 'ngs_molbio_local',
            });
        },
    });

    const linkMutation = useMutation({
        mutationFn: () => {
            if (!selectedLocalProjectId || !targetGlobalProjectId || selectedExperimentIds.length === 0) {
                throw new Error('Select a local Project, one or more contained Experiments, and a broader Project.');
            }
            return linkNgsMolBioProject(targetGlobalProjectId, {
                local_project_id: selectedLocalProjectId,
                experiment_ids: selectedExperimentIds,
                result_ids: selectedResultIds,
                change_summary: 'Exposed selected local NGS/MolBio Experiments and Results to broader Project',
            });
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-links', selectedLocalProjectId] });
            setSelectedExperimentIds([]);
            setSelectedResultIds([]);
        },
    });

    const selectedLocalProject = useMemo(
        () => localProjects.find((project) => project.id === selectedLocalProjectId) ?? null,
        [localProjects, selectedLocalProjectId],
    );
    const error = createMutation.error ?? linkMutation.error ?? localProjectsQuery.error ?? globalProjectsQuery.error ?? localHierarchyQuery.error ?? shareableResultsQuery.error;

    return (
        <section className="mb-4 rounded-2xl border border-border-primary bg-surface-secondary/80 p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Two-tier ownership</p>
                    <h2 className="mt-1 text-lg font-semibold text-content-primary">NGS/MolBio Projects</h2>
                    <p className="mt-1 max-w-4xl text-xs leading-5 text-content-secondary">
                        Projects contain Experiments. Experiments reference real or in-silico Data, record Workflow Receipts about execution, and expose data-bearing Results. Local Projects are complete standalone authorities for Mol Bio and NGS. They can optionally share selected Experiments and Results with several broader Projects.
                    </p>
                </div>
                <a href="/projects" className={BUTTON}>Open broader Project Manager</a>
            </div>

            <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
                <div className="rounded-xl border border-border-primary bg-surface p-4">
                    <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => setMode('local-new')} className={`${BUTTON} ${mode === 'local-new' ? 'border-primary bg-primary/10' : ''}`}>New local NGS/MolBio Project</button>
                        <button type="button" onClick={() => setMode('local-existing')} disabled={!selectedLocalProjectId} className={`${BUTTON} ${mode === 'local-existing' ? 'border-primary bg-primary/10' : ''}`}>Add Experiment to selected local Project</button>
                        <button type="button" onClick={() => setMode('global')} className={`${BUTTON} ${mode === 'global' ? 'border-primary bg-primary/10' : ''}`}>Experiment in broader Project</button>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        {mode === 'local-new' && (
                            <label className="text-xs text-content-secondary">Project name<input className={`${INPUT} mt-1`} value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Focused sequencing Project" /></label>
                        )}
                        {mode === 'local-existing' && (
                            <label className="text-xs text-content-secondary">Owning local Project<input className={`${INPUT} mt-1`} value={selectedLocalProject?.name ?? ''} readOnly /></label>
                        )}
                        {mode === 'global' && (
                            <label className="text-xs text-content-secondary">Owning broader Project<select className={`${INPUT} mt-1`} value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)}><option value="">Select Project</option>{globalProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                        )}
                        <label className="text-xs text-content-secondary">Contained Experiment name<input className={`${INPUT} mt-1`} value={experimentName} onChange={(event) => setExperimentName(event.target.value)} placeholder="Experiment" /></label>
                        <label className="text-xs text-content-secondary sm:col-span-2">Scientific objective<textarea className={`${INPUT} mt-1 min-h-20`} value={objective} onChange={(event) => setObjective(event.target.value)} /></label>
                    </div>
                    <button type="button" className={`${BUTTON} mt-3`} disabled={createMutation.isPending || !experimentName.trim() || !objective.trim() || (mode === 'local-new' ? !projectName.trim() : mode === 'local-existing' ? !selectedLocalProjectId : !targetGlobalProjectId)} onClick={() => createMutation.mutate()}>
                        {createMutation.isPending ? 'Creating…' : mode === 'local-new' ? 'Create local Project and first Experiment' : mode === 'local-existing' ? 'Add contained Experiment' : 'Create global NGS/MolBio Experiment'}
                    </button>
                </div>

                <div className="rounded-xl border border-border-primary bg-surface p-4">
                    <label className="text-xs text-content-secondary">Local NGS/MolBio Project<select className={`${INPUT} mt-1`} value={selectedLocalProjectId} onChange={(event) => setSelectedLocalProjectId(event.target.value)}><option value="">Select local Project</option>{localProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                    {selectedLocalProject && <p className="mt-2 text-xs text-content-muted">{selectedLocalProject.description || selectedLocalProject.payload?.research_objective || 'No objective recorded.'}</p>}
                    <div className="mt-3 space-y-2">
                        {(localHierarchyQuery.data?.domains ?? []).map(({ experiment, domain }) => (
                            <button key={domain.id} type="button" className="flex w-full items-center justify-between gap-3 rounded-lg border border-border-primary px-3 py-2 text-left hover:border-primary/60" onClick={() => updateQueryParams({ workspace_id: selectedLocalProjectId, global_experiment_id: experiment.id, domain_experiment_id: domain.id, state_revision_id: null, ownership_scope: 'ngs_molbio_local' })}>
                                <span><span className="block text-xs font-semibold text-content-primary">{experiment.name}</span><span className="block text-[11px] text-content-muted">NGS/MolBio Domain Experiment</span></span>
                                <span className="text-xs text-accent">Open</span>
                            </button>
                        ))}
                        {selectedLocalProjectId && !localHierarchyQuery.isLoading && (localHierarchyQuery.data?.domains.length ?? 0) === 0 && <p className="text-xs text-content-muted">This local Project has no NGS/MolBio Experiment.</p>}
                    </div>
                </div>
            </div>

            {selectedLocalProjectId && (
                <div className="mt-4 rounded-xl border border-border-primary bg-surface p-4">
                    <h3 className="text-sm font-semibold text-content-primary">Optional: expose local Experiments and Results to a broader Project</h3>
                    <p className="mt-1 text-xs text-content-secondary">This local Project needs no broader association. When useful, each explicit link adds governed membership and lineage. Native Data and Result payloads remain single-copy in their owning stores.</p>
                    <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)_auto]">
                        <div className="space-y-2">
                            <p className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">Contained Experiments</p>
                            {(localHierarchyQuery.data?.experiments ?? []).map((experiment) => (
                                <label key={experiment.id} className="flex items-center gap-2 text-xs text-content-secondary"><input type="checkbox" checked={selectedExperimentIds.includes(experiment.id)} onChange={(event) => setSelectedExperimentIds((current) => event.target.checked ? [...current, experiment.id] : current.filter((id) => id !== experiment.id))} />{experiment.name}</label>
                            ))}
                        </div>
                        <div className="space-y-2">
                            <p className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">Data-bearing Results</p>
                            {(shareableResultsQuery.data ?? []).map((result) => (
                                <label key={result.result_receipt_id} className="flex items-start gap-2 text-xs text-content-secondary"><input className="mt-0.5" type="checkbox" checked={selectedResultIds.includes(result.result_receipt_id)} onChange={(event) => setSelectedResultIds((current) => event.target.checked ? [...current, result.result_receipt_id] : current.filter((id) => id !== result.result_receipt_id))} /><span>{result.entity_kind} · {result.entity_id}<span className="block font-mono text-[10px] text-content-muted">{result.content_digest.slice(0, 12)} · {result.store_id}</span></span></label>
                            ))}
                            {!shareableResultsQuery.isLoading && (shareableResultsQuery.data?.length ?? 0) === 0 && <p className="text-xs text-content-muted">No governed native Result receipts are available yet.</p>}
                        </div>
                        <select className={INPUT} value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)}><option value="">Select broader Project</option>{globalProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>
                        <button type="button" className={BUTTON} disabled={linkMutation.isPending || !targetGlobalProjectId || selectedExperimentIds.length === 0} onClick={() => linkMutation.mutate()}>{linkMutation.isPending ? 'Linking…' : 'Create governed link'}</button>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">{(linksQuery.data ?? []).map((link) => <span key={link.link_id} className="rounded-full border border-border-primary px-2 py-1 text-[11px] text-content-muted">Global Project {link.global_project_id.slice(0, 8)} · {link.experiment_ids.length} Experiment{link.experiment_ids.length === 1 ? '' : 's'} · {link.result_ids.length} Result{link.result_ids.length === 1 ? '' : 's'}</span>)}</div>
                </div>
            )}

            {error && <p className="mt-3 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-xs text-error">{projectManagerErrorMessage(error)}</p>}
        </section>
    );
}
