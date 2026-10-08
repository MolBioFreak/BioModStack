import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import DomainExperimentWorkspace from './DomainExperimentWorkspace';

const INPUT = 'w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary';
const BUTTON = 'rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs font-semibold text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';

type OwnershipMode = 'local-new' | 'local-existing' | 'global';

type NgsMolBioProjectHubProps = {
    presentation?: 'inline' | 'launcher-dialog';
};

interface LocalHierarchy {
    experiments: HierarchyMutationResult[];
    domains: Array<{ experiment: HierarchyMutationResult; domain: HierarchyMutationResult }>;
}

function ngsDomainPayload(objective: string): JsonObject {
    return {
        schema: 'bms.ngs-molbio-experiment.v2',
        experiment_mode: 'quality_control',
        scientific_objective: objective,
        planned_capability_ids: ['ngs.ont.fastq_qc'],
        grouping_intent: [],
        acceptance_criteria: [{
            criterion_id: 'ngs-result-manifest-present',
            schema_id: 'bms.scientific-criterion.artifact-presence.v1',
            schema_sha256: '3f03a62f9bc39f61c4bdfa938cca5453da91e68ef16b30effdd0f4195cc2bdc6',
            subject_role: 'result',
            payload: { artifact_role: 'ngs_result_manifest', minimum_count: 1 },
        }],
        evidence_plan: [{
            requirement_id: 'ngs-result-manifest-receipt',
            schema_id: 'bms.evidence-requirement.native-receipt.v1',
            schema_sha256: '4f1ea5545016d8d49739c2d1f1a94bc64f321667d2eb98205d0f727088da5d10',
            subject_role: 'result',
            required: true,
            payload: { receipt_kind: 'ngs_result_manifest', minimum_count: 1 },
        }],
    };
}

export default function NgsMolBioProjectHub({ presentation = 'inline' }: NgsMolBioProjectHubProps) {
    const queryClient = useQueryClient();
    const { workspaceId, globalExperimentId, domainExperimentId, selectedDomainExperiment, updateQueryParams } = useGlobalExperimentContext();
    const showRoutineWorkspace = presentation === 'inline'
        && Boolean(workspaceId && globalExperimentId && domainExperimentId && selectedDomainExperiment?.domain_experiment_id === domainExperimentId);
    const [mode, setMode] = useState<OwnershipMode>('local-new');
    const [projectName, setProjectName] = useState('');
    const [projectDescription, setProjectDescription] = useState('');
    const [projectOwner, setProjectOwner] = useState('');
    const [contributors, setContributors] = useState('');
    const [tags, setTags] = useState('');
    const [startDate, setStartDate] = useState('');
    const [targetEndDate, setTargetEndDate] = useState('');
    const [experimentName, setExperimentName] = useState('');
    const [objective, setObjective] = useState('');
    const [scientificQuestion, setScientificQuestion] = useState('');
    const [hypothesis, setHypothesis] = useState('');
    const [successCriteria, setSuccessCriteria] = useState('');
    const [priority, setPriority] = useState<'low' | 'normal' | 'high' | 'critical'>('normal');
    const [showDetails, setShowDetails] = useState(false);
    const [showExpose, setShowExpose] = useState(false);
    const [projectPanelTab, setProjectPanelTab] = useState<'local' | 'broader'>('local');
    const [selectedLocalProjectId, setSelectedLocalProjectId] = useState('');
    const [selectedLocalDomainId, setSelectedLocalDomainId] = useState('');
    const [targetGlobalProjectId, setTargetGlobalProjectId] = useState('');
    const [selectedExperimentIds, setSelectedExperimentIds] = useState<string[]>([]);
    const [selectedResultIds, setSelectedResultIds] = useState<string[]>([]);

    const localProjectsQuery = useQuery({
        queryKey: ['ngs-molbio-projects', 'local'],
        enabled: !showRoutineWorkspace,
        queryFn: ({ signal }) => searchProjects({ projectScope: 'ngs_molbio_local', archive: 'active', limit: 100, signal }),
        retry: false,
    });
    const globalProjectsQuery = useQuery({
        queryKey: ['ngs-molbio-projects', 'global'],
        enabled: !showRoutineWorkspace,
        queryFn: ({ signal }) => searchProjects({ projectScope: 'global', archive: 'active', limit: 100, signal }),
        retry: false,
    });
    const localProjects = useMemo(() => localProjectsQuery.data?.items ?? [], [localProjectsQuery.data?.items]);
    const globalProjects = useMemo(() => globalProjectsQuery.data?.items ?? [], [globalProjectsQuery.data?.items]);

    useEffect(() => {
        if (workspaceId && localProjects.some((project) => project.id === workspaceId)) {
            setSelectedLocalProjectId(workspaceId);
            return;
        }
        if (!selectedLocalProjectId && localProjects[0]) setSelectedLocalProjectId(localProjects[0].id);
    }, [localProjects, selectedLocalProjectId, workspaceId]);

    const localHierarchyQuery = useQuery({
        queryKey: ['ngs-molbio-project-hierarchy', selectedLocalProjectId],
        enabled: !showRoutineWorkspace && Boolean(selectedLocalProjectId),
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
    useEffect(() => {
        const domains = localHierarchyQuery.data?.domains ?? [];
        if (domains.some(({ domain }) => domain.id === selectedLocalDomainId)) return;
        setSelectedLocalDomainId(domains[0]?.domain.id ?? '');
    }, [localHierarchyQuery.data, selectedLocalDomainId]);
    const selectedLocalDomain = useMemo(
        () => (localHierarchyQuery.data?.domains ?? []).find(({ domain }) => domain.id === selectedLocalDomainId) ?? null,
        [localHierarchyQuery.data, selectedLocalDomainId],
    );
    const openSelectedProject = () => {
        if (!selectedLocalProjectId) return;
        updateQueryParams({
            workspace_id: selectedLocalProjectId,
            global_experiment_id: selectedLocalDomain?.experiment.id ?? null,
            domain_experiment_id: selectedLocalDomain?.domain.id ?? null,
            state_revision_id: null,
            ownership_scope: 'ngs_molbio_local',
        });
    };

    const linksQuery = useQuery({
        queryKey: ['ngs-molbio-project-links', selectedLocalProjectId],
        enabled: !showRoutineWorkspace && Boolean(selectedLocalProjectId),
        queryFn: ({ signal }) => listNgsMolBioProjectLinks(selectedLocalProjectId, signal),
        retry: false,
    });
    const shareableResultsQuery = useQuery({
        queryKey: ['ngs-molbio-shareable-results', selectedLocalProjectId],
        enabled: !showRoutineWorkspace && Boolean(selectedLocalProjectId),
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

    const resetProjectDrafts = useCallback(() => {
        setMode('local-new');
        setProjectName('');
        setProjectDescription('');
        setProjectOwner('');
        setContributors('');
        setTags('');
        setStartDate('');
        setTargetEndDate('');
        setExperimentName('');
        setObjective('');
        setScientificQuestion('');
        setHypothesis('');
        setSuccessCriteria('');
        setPriority('normal');
        setShowDetails(false);
        setShowExpose(false);
        setSelectedExperimentIds([]);
        setSelectedResultIds([]);
        setTargetGlobalProjectId('');
    }, []);

    const createMutation = useMutation({
        mutationFn: async (requestedMode: OwnershipMode = mode) => {
            if (!experimentName.trim()) throw new Error('Enter the required contained Experiment name.');
            if (!objective.trim()) throw new Error('Enter the required scientific objective.');
            if (requestedMode === 'local-new' && !projectName.trim()) throw new Error('Enter the required local Project name.');
            if (requestedMode === 'local-existing' && !selectedLocalProjectId) throw new Error('Select the owning local Project.');
            if (requestedMode === 'global' && !targetGlobalProjectId) throw new Error('Select the owning broader Project.');
            let projectId = requestedMode === 'local-existing' ? selectedLocalProjectId : targetGlobalProjectId;
            if (requestedMode === 'local-new') {
                const project = await createProject({
                    schema: 'bms.project.v2',
                    name: projectName,
                    description: projectDescription.trim() || undefined,
                    research_objective: objective,
                    owner: projectOwner.trim() || null,
                    contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                    tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                    status: 'active',
                    start_date: startDate || null,
                    target_end_date: targetEndDate || null,
                    project_scope: 'ngs_molbio_local',
                    change_summary: 'Created in the NGS/MolBio Project layer',
                });
                projectId = project.id;
            }
            if (!projectId) throw new Error('Select a broader Project for a global NGS/MolBio Experiment.');
            const experiment = await createGlobalExperiment(projectId, {
                schema: 'bms.global-experiment.v2',
                name: experimentName,
                objective,
                scientific_question: scientificQuestion.trim() || objective,
                hypothesis: hypothesis.trim() || null,
                description: projectDescription.trim() || undefined,
                status: 'planned',
                priority,
                tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                change_summary: requestedMode === 'global' ? 'Created inside broader BMS Project' : 'Created inside local NGS/MolBio Project',
            });
            const domain = await createDomainExperiment(projectId, experiment.id, {
                schema: 'bms.domain-experiment.v4',
                domain_kind: 'ngs_molbio',
                domain_contract_version: '3',
                name: experimentName,
                objective,
                status: 'planned',
                tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                source_receipt_ids: [],
                dataset_revision_ids: [],
                change_summary: 'Created from two-tier NGS/MolBio Project authoring',
                domain_payload: ngsDomainPayload(objective),
            });
            return { projectId, experiment, domain, ownershipMode: requestedMode };
        },
        onSuccess: ({ projectId, experiment, domain, ownershipMode }) => {
            void Promise.all([
                queryClient.invalidateQueries({ queryKey: ['ngs-molbio-projects'] }),
                queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-authority', projectId] }),
                queryClient.invalidateQueries({ queryKey: ['global-workspaces'] }),
                queryClient.invalidateQueries({ queryKey: ['global-experiments', projectId] }),
                queryClient.invalidateQueries({ queryKey: ['molbio-ngs-project-domain-experiments', projectId] }),
                queryClient.invalidateQueries({ queryKey: ['ngs-molbio-binding', projectId, experiment.id, domain.id] }),
            ]);
            resetProjectDrafts();
            updateQueryParams({
                workspace_id: projectId,
                global_experiment_id: experiment.id,
                domain_experiment_id: domain.id,
                state_revision_id: null,
                ownership_scope: ownershipMode === 'global' ? 'global' : 'ngs_molbio_local',
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
    const selectedResearchObjective = selectedLocalProject?.payload?.research_objective;
    const selectedProjectDescription = selectedLocalProject?.description
        || (typeof selectedResearchObjective === 'string' || typeof selectedResearchObjective === 'number'
            ? String(selectedResearchObjective)
            : 'No objective recorded.');
    const error = createMutation.error ?? linkMutation.error ?? localProjectsQuery.error ?? globalProjectsQuery.error ?? localHierarchyQuery.error ?? shareableResultsQuery.error;
    const [isOpen, setIsOpen] = useState(false);
    const launcherRef = useRef<HTMLButtonElement>(null);
    const dialogRef = useRef<HTMLElement>(null);
    const wasDialogOpenRef = useRef(false);
    const closeProjectPanel = useCallback(() => {
        resetProjectDrafts();
        setProjectPanelTab('local');
        setIsOpen(false);
    }, [resetProjectDrafts]);

    useEffect(() => {
        if (presentation !== 'launcher-dialog' || !isOpen) return undefined;
        const focusableSelector = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])';
        const focusFirst = () => dialogRef.current?.querySelector<HTMLElement>(focusableSelector)?.focus();
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeProjectPanel();
                return;
            }
            if (event.key !== 'Tab' || !dialogRef.current) return;
            const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector));
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', handleKeyDown);
        queueMicrotask(focusFirst);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [closeProjectPanel, isOpen, presentation]);

    useEffect(() => {
        if (presentation !== 'launcher-dialog') {
            wasDialogOpenRef.current = false;
            return;
        }
        if (wasDialogOpenRef.current && !isOpen) launcherRef.current?.focus();
        wasDialogOpenRef.current = isOpen;
    }, [isOpen, presentation]);

    const advancedMetadataFields = (
        <div className="mt-3 grid gap-3 rounded-lg border border-border-primary bg-surface-secondary p-3 sm:grid-cols-2">
            <label className="text-xs text-content-secondary sm:col-span-2">Description<textarea className={`${INPUT} mt-1 min-h-16`} value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} /></label>
            {mode === 'local-new' && <>
                <label className="text-xs text-content-secondary">Owner <span className="text-content-muted">(leave blank to use authenticated principal)</span><input className={`${INPUT} mt-1`} value={projectOwner} onChange={(event) => setProjectOwner(event.target.value)} placeholder="Exact authenticated principal only" /></label>
                <label className="text-xs text-content-secondary">Contributors, comma separated<input className={`${INPUT} mt-1`} value={contributors} onChange={(event) => setContributors(event.target.value)} /></label>
                <label className="text-xs text-content-secondary">Start date<input type="date" className={`${INPUT} mt-1`} value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
                <label className="text-xs text-content-secondary">Target end date<input type="date" className={`${INPUT} mt-1`} value={targetEndDate} onChange={(event) => setTargetEndDate(event.target.value)} /></label>
            </>}
            <label className="text-xs text-content-secondary sm:col-span-2">Tags, comma separated<input className={`${INPUT} mt-1`} value={tags} onChange={(event) => setTags(event.target.value)} /></label>
            <label className="text-xs text-content-secondary sm:col-span-2">Scientific question<textarea className={`${INPUT} mt-1 min-h-16`} value={scientificQuestion} onChange={(event) => setScientificQuestion(event.target.value)} placeholder="Defaults to the objective" /></label>
            <label className="text-xs text-content-secondary sm:col-span-2">Hypothesis<textarea className={`${INPUT} mt-1 min-h-16`} value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} /></label>
            <label className="text-xs text-content-secondary">Priority<select className={`${INPUT} mt-1`} value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select></label>
            <label className="text-xs text-content-secondary sm:col-span-2">Success criteria, one per line<textarea className={`${INPUT} mt-1 min-h-20`} value={successCriteria} onChange={(event) => setSuccessCriteria(event.target.value)} /></label>
        </div>
    );

    const governedExposureControls = selectedLocalProjectId ? (
        <div className="mt-3 rounded-xl border border-border-primary bg-surface p-3">
            <button type="button" className="flex w-full items-center justify-between gap-2 text-left" onClick={() => setShowExpose((value) => !value)}>
                <span className="text-sm font-semibold text-content-primary">Optional: expose local Experiments and Results to a broader Project</span>
                <span className="text-xs text-content-muted">{showExpose ? 'Hide' : 'Show'}</span>
            </button>
            {showExpose && (<>
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
                <div className="mt-2 flex flex-wrap gap-2">{(linksQuery.data ?? []).map((link) => <span key={link.link_id} className="rounded-full border border-border-primary px-2 py-1 text-[11px] text-content-muted">Global Project {link.global_project_id.slice(0, 8)} · {link.experiment_ids.length} Experiment{link.experiment_ids.length === 1 ? '' : 's'} · {link.result_ids.length} Result{link.result_ids.length === 1 ? '' : 's'}</span>)}</div>
            </>)}
        </div>
    ) : null;

    const advancedProjectWorkspace = <DomainExperimentWorkspace />;

    const projectSurface = (
        <div className="w-full max-w-none rounded-2xl border border-border-primary bg-surface-secondary/80 shadow-sm">
            <section className="p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Two-tier ownership</p>
                    <h2 className="mt-1 text-lg font-semibold text-content-primary" title="Projects contain Experiments. Local Projects are complete standalone authorities for Mol Bio and NGS and can optionally share selected Experiments and Results with broader Projects.">NGS/MolBio Projects</h2>
                </div>
                        {presentation === 'inline' && (
                            <a href="/projects" className={BUTTON}>Open broader Project Manager</a>
                        )}
            </div>

            <div className="mt-2 grid gap-3 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
                <div className="rounded-xl border border-border-primary bg-surface p-3">
                    <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => setMode('local-new')} className={`${BUTTON} ${mode === 'local-new' ? 'border-primary bg-primary/10' : ''}`}>New local NGS/MolBio Project</button>
                        <button type="button" onClick={() => setMode('local-existing')} disabled={!selectedLocalProjectId} className={`${BUTTON} ${mode === 'local-existing' ? 'border-primary bg-primary/10' : ''}`}>Add Experiment to selected local Project</button>
                        <button type="button" onClick={() => setMode('global')} className={`${BUTTON} ${mode === 'global' ? 'border-primary bg-primary/10' : ''}`}>Experiment in broader Project</button>
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        {mode === 'local-new' && (
                            <label className="text-xs text-content-secondary">Project name <span className="text-error">(required)</span><input required className={`${INPUT} mt-1`} value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Focused sequencing Project" /></label>
                        )}
                        {mode === 'local-existing' && (
                            <label className="text-xs text-content-secondary">Owning local Project <span className="text-error">(required)</span><input className={`${INPUT} mt-1`} value={selectedLocalProject?.name ?? ''} readOnly /></label>
                        )}
                        {mode === 'global' && (
                            <label className="text-xs text-content-secondary">Owning broader Project <span className="text-error">(required)</span><select required className={`${INPUT} mt-1`} value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)}><option value="">Select Project</option>{globalProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                        )}
                        <label className="text-xs text-content-secondary">Contained Experiment name <span className="text-error">(required)</span><input required className={`${INPUT} mt-1`} value={experimentName} onChange={(event) => setExperimentName(event.target.value)} placeholder="Experiment" /></label>
                        <label className="text-xs text-content-secondary sm:col-span-2">Scientific objective <span className="text-error">(required)</span><textarea required className={`${INPUT} mt-1 min-h-16`} value={objective} onChange={(event) => setObjective(event.target.value)} /></label>
                    </div>
                    <button type="button" className={`${BUTTON} mt-2`} onClick={() => setShowDetails((value) => !value)}>{showDetails ? 'Hide Project and Experiment details' : 'Add Project and Experiment details'}</button>
                    {showDetails && advancedMetadataFields}
                    <button type="button" className={`${BUTTON} mt-2`} disabled={createMutation.isPending} onClick={() => createMutation.mutate(mode)}>
                        {createMutation.isPending ? 'Creating…' : mode === 'local-new' ? 'Create local Project and first Experiment' : mode === 'local-existing' ? 'Add contained Experiment' : 'Create global NGS/MolBio Experiment'}
                    </button>
                </div>

                <div className="rounded-xl border border-border-primary bg-surface p-3">
                    <label className="text-xs text-content-secondary">Local NGS/MolBio Project<select className={`${INPUT} mt-1`} value={selectedLocalProjectId} onChange={(event) => setSelectedLocalProjectId(event.target.value)}><option value="">Select local Project</option>{localProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                    {selectedLocalProject && <p className="mt-2 text-xs text-content-muted">{selectedProjectDescription}</p>}
                    <div className="mt-2 space-y-2">
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

            {governedExposureControls}

            {error && <p className="mt-3 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-xs text-error">{projectManagerErrorMessage(error)}</p>}
            </section>

            {advancedProjectWorkspace}
        </div>
    );

    const compactProjectPanel = (
        <div className="p-4 sm:p-5">
            <div role="tablist" aria-label="Project scope" className="flex gap-2 border-b border-border-primary pb-3">
                <button
                    type="button"
                    role="tab"
                    id="ngs-project-tab-local"
                    aria-selected={projectPanelTab === 'local'}
                    aria-controls="ngs-project-panel-local"
                    className={`${BUTTON} ${projectPanelTab === 'local' ? 'border-primary bg-primary/15' : ''}`}
                    onClick={() => {
                        setProjectPanelTab('local');
                        setMode('local-new');
                    }}
                >
                    Local Projects
                </button>
                <button
                    type="button"
                    role="tab"
                    id="ngs-project-tab-broader"
                    aria-selected={projectPanelTab === 'broader'}
                    aria-controls="ngs-project-panel-broader"
                    className={`${BUTTON} ${projectPanelTab === 'broader' ? 'border-primary bg-primary/15' : ''}`}
                    onClick={() => {
                        setProjectPanelTab('broader');
                        setMode('global');
                    }}
                >
                    Broader Projects
                </button>
            </div>

            {projectPanelTab === 'local' ? (
                <div id="ngs-project-panel-local" role="tabpanel" aria-labelledby="ngs-project-tab-local" className="grid gap-3 pt-4 md:grid-cols-2">
                    <section className="rounded-xl border border-border-primary bg-surface p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Open a local Project</p>
                        <label className="mt-3 block text-xs text-content-secondary">
                            Existing Project
                            <select className={`${INPUT} mt-1`} value={selectedLocalProjectId} onChange={(event) => setSelectedLocalProjectId(event.target.value)}>
                                <option value="">Select local NGS/MolBio Project…</option>
                                {localProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                            </select>
                        </label>
                        <label className="mt-3 block text-xs text-content-secondary">
                            Contained Experiment
                            <select className={`${INPUT} mt-1`} value={selectedLocalDomainId} onChange={(event) => setSelectedLocalDomainId(event.target.value)} disabled={!selectedLocalProjectId || localHierarchyQuery.isLoading}>
                                <option value="">Select Experiment…</option>
                                {(localHierarchyQuery.data?.domains ?? []).map(({ experiment, domain }) => <option key={domain.id} value={domain.id}>{experiment.name}</option>)}
                            </select>
                        </label>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" className={`${BUTTON} border-primary bg-primary/90 text-white`} disabled={!selectedLocalProjectId} onClick={openSelectedProject}>Open selected Project</button>
                            <button type="button" className={BUTTON} disabled={!selectedLocalProjectId} onClick={() => setMode('local-existing')}>Add Experiment to selected local Project</button>
                        </div>
                    </section>
                    <section className="rounded-xl border border-border-primary bg-surface p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">{mode === 'local-existing' ? 'Add an Experiment to the selected local Project' : 'Create a local Project'}</p>
                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                            {mode !== 'local-existing' && <label className="text-xs text-content-secondary">Project name<input className={`${INPUT} mt-1`} value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Focused sequencing Project" /></label>}
                            <label className="text-xs text-content-secondary">First Experiment<input className={`${INPUT} mt-1`} value={experimentName} onChange={(event) => setExperimentName(event.target.value)} placeholder="Validation run" /></label>
                            <label className="text-xs text-content-secondary sm:col-span-2">Scientific objective<input className={`${INPUT} mt-1`} value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="Define the sequencing objective…" /></label>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="button" className={`${BUTTON} border-primary bg-primary/90 text-white`} disabled={createMutation.isPending} onClick={() => createMutation.mutate(mode === 'local-existing' ? 'local-existing' : 'local-new')}>{createMutation.isPending ? 'Creating…' : mode === 'local-existing' ? 'Add contained Experiment' : 'Create Project and Experiment'}</button>
                            {mode === 'local-existing' && <button type="button" className={BUTTON} onClick={() => setMode('local-new')}>Create a new local Project instead</button>}
                        </div>
                    </section>
                </div>
            ) : (
                <div id="ngs-project-panel-broader" role="tabpanel" aria-labelledby="ngs-project-tab-broader" className="grid gap-3 pt-4 md:grid-cols-2">
                    <section className="rounded-xl border border-border-primary bg-surface p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Open a broader Project</p>
                        <label className="mt-3 block text-xs text-content-secondary">Existing Project<select className={`${INPUT} mt-1`} value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)}><option value="">Select broader Project…</option>{globalProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                        <button type="button" className={`${BUTTON} mt-3 border-primary bg-primary/90 text-white`} disabled={!targetGlobalProjectId} onClick={() => updateQueryParams({ workspace_id: targetGlobalProjectId, global_experiment_id: null, domain_experiment_id: null, state_revision_id: null, ownership_scope: 'global' })}>Open selected Project</button>
                    </section>
                    <section className="rounded-xl border border-border-primary bg-surface p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Create a broader Experiment</p>
                        <label className="mt-3 block text-xs text-content-secondary">Owning broader Project<select className={`${INPUT} mt-1`} value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)}><option value="">Select broader Project…</option>{globalProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                            <label className="text-xs text-content-secondary">First Experiment<input className={`${INPUT} mt-1`} value={experimentName} onChange={(event) => setExperimentName(event.target.value)} placeholder="Validation run" /></label>
                            <label className="text-xs text-content-secondary">Scientific objective<input className={`${INPUT} mt-1`} value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="Define the sequencing objective…" /></label>
                        </div>
                        <button type="button" className={`${BUTTON} mt-3 border-primary bg-primary/90 text-white`} disabled={createMutation.isPending} onClick={() => createMutation.mutate('global')}>{createMutation.isPending ? 'Creating…' : 'Create Experiment'}</button>
                    </section>
                </div>
            )}

            <details data-testid="ngs-project-advanced-disclosure" className="mt-3 rounded-xl border border-border-primary bg-surface p-3">
                <summary className="cursor-pointer text-sm font-semibold text-content-primary">Advanced project and experiment metadata</summary>
                <div className="mt-3 rounded-lg border border-border-primary bg-surface-secondary p-3 text-xs text-content-secondary">
                    <p>These controls reuse the compact panel's Project and Experiment state and existing governed mutations.</p>
                    {advancedMetadataFields}
                    {governedExposureControls}
                    <div className="mt-3">
                        <p className="mb-3">Use the existing governed workspace for immutable state, evidence, and exact revision controls.</p>
                        {advancedProjectWorkspace}
                    </div>
                </div>
            </details>

            <div className="mt-3 flex flex-col gap-3 rounded-xl border border-border-primary bg-surface p-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-content-secondary">Need cross-domain ownership or global Project administration?</p>
                <a href="/projects" className={BUTTON}>Open global Project Manager</a>
            </div>
            {error && <p role="alert" className="mt-3 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-xs text-error">{projectManagerErrorMessage(error)}</p>}
        </div>
    );

    if (showRoutineWorkspace) return advancedProjectWorkspace;
    if (presentation === 'inline') return projectSurface;

    return (
        <>
            <button
                ref={launcherRef}
                type="button"
                className={`${BUTTON} border-primary bg-primary/10`}
                onClick={() => setIsOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={isOpen}
            >
                Projects
            </button>
            {isOpen && (
                <div
                    className="fixed inset-0 z-[100] grid items-end justify-center bg-black/70 p-0 sm:items-center sm:p-3"
                    onMouseDown={(event) => {
                        if (event.currentTarget === event.target) closeProjectPanel();
                    }}
                >
                    <section
                        ref={dialogRef}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="ngs-project-panel-title"
                        className="h-[100dvh] max-h-[100dvh] w-full overflow-y-auto rounded-t-2xl border border-border-primary bg-surface-secondary shadow-2xl sm:h-auto sm:max-h-[90vh] sm:max-w-[1040px] sm:rounded-2xl"
                    >
                        <header className="flex items-start justify-between gap-4 border-b border-border-primary px-4 py-4 sm:px-5">
                            <div>
                                <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">NGS / MolBio Projects</p>
                                <h2 id="ngs-project-panel-title" className="mt-1 text-lg font-semibold text-content-primary">Project workspace</h2>
                            </div>
                            <button type="button" className={BUTTON} onClick={closeProjectPanel}>Close ×</button>
                        </header>
                        {compactProjectPanel}
                    </section>
                </div>
            )}
        </>
    );
}
