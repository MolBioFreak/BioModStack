import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    attachExistingEntity,
    getProject,
    getProjectSummary,
    linkNgsMolBioProject,
    listDomainAdapters,
    listDomainExperiments,
    listGlobalExperiments,
    listProjects,
    listNgsMolBioShareableResults,
    projectManagerErrorMessage,
    searchProjects,
    searchAdapterEntities,
    type AdapterEntityProjection,
    type LineageRole,
    type ProjectManagerReadModel,
} from '../../lib/projectManager';
import { addMolecularProjectMember, exportMolecularProjectHistory, readMolecularProjectHistory, MOLECULAR_MEMBER_ADAPTER, type ProjectExportJournalEntry } from '../../lib/molecularProjectMembership';
import { fetchMolBioNgsDomainState } from '../../lib/api';
import { ManagerDialog, type ManagerDialogMode } from './ManagerDialog';
import { selectedDomainContext } from './projectManagerState';

export type AttachmentOperationMode = 'attach_reference' | 'bind_input' | 'link_output' | 'attach_evidence' | 'clone_import_revision';

export interface ProjectAttachmentSource {
    adapterId: string;
    entityId: string;
    label: string;
    revision?: string | null;
    digest?: string | null;
    availability?: string | null;
    historyJournal?: ProjectExportJournalEntry[];
}

interface ProjectAttachmentDialogProps {
    open: boolean;
    source?: ProjectAttachmentSource;
    projectId?: string;
    initialProjectId?: string;
    summary?: ProjectManagerReadModel;
    onClose: () => void;
    destination?: { globalExperimentId: string; domainExperimentId: string; stateRevisionId?: string | null };
    onAttached?: (receiptId: string | null, stateRevisionId?: string, destination?: { projectId: string; globalExperimentId: string; domainExperimentId: string }) => void;
}

const operationCopy: Record<Exclude<AttachmentOperationMode, 'clone_import_revision'>, { role: LineageRole; copy: string }> = {
    attach_reference: { role: 'references', copy: 'Attach a membership/reference receipt. Bytes remain in the canonical source store.' },
    bind_input: { role: 'uses_input', copy: 'Bind this immutable source as an input reference. No source bytes are copied.' },
    link_output: { role: 'produced', copy: 'Link a generated output receipt to this Domain Experiment. The producer remains authoritative.' },
    attach_evidence: { role: 'validated_by', copy: 'Attach verified evidence without copying or reinterpreting scientific content.' },
};

const roleCopy: Record<LineageRole, string> = {
    references: 'Writes a references lineage edge to the verified receipt.',
    uses_input: 'Writes an immutable uses_input lineage edge; the source store remains authoritative.',
    produced: 'Writes a produced lineage edge to an output verified by the selected adapter.',
    validated_by: 'Writes a validated_by evidence edge without altering the source record.',
};

function metadataString(value: unknown): string | null {
    return typeof value === 'string' && value.trim() ? value : null;
}

export function ProjectAttachmentDialog({ open, source, projectId: fixedProjectId, initialProjectId, summary, destination, onClose, onAttached }: ProjectAttachmentDialogProps) {
    const queryClient = useQueryClient();
    const fixedContext = summary ? selectedDomainContext(summary) : null;
    const [projectId, setProjectId] = useState(fixedProjectId ?? initialProjectId ?? '');
    const [globalId, setGlobalId] = useState(fixedContext?.globalExperimentId ?? '');
    const [domainId, setDomainId] = useState(fixedContext?.domainExperimentId ?? '');
    const [adapterId, setAdapterId] = useState(source?.adapterId ?? '');
    const [query, setQuery] = useState('');
    const [selected, setSelected] = useState<AdapterEntityProjection | null>(null);
    const [operation, setOperation] = useState<AttachmentOperationMode>('attach_reference');
    const [role, setRole] = useState<LineageRole>('references');
    const [note, setNote] = useState('');
    const [attached, setAttached] = useState(false);
    const [includeHistory, setIncludeHistory] = useState(false);
    const [setupMode, setSetupMode] = useState<ManagerDialogMode | null>(null);
    const [mode, setMode] = useState<'item' | 'project-link'>('item');
    const [targetGlobalProjectId, setTargetGlobalProjectId] = useState('');
    const [selectedExperimentIds, setSelectedExperimentIds] = useState<string[]>([]);
    const [selectedResultIds, setSelectedResultIds] = useState<string[]>([]);
    const isLocalProject = summary?.project.project_scope === 'ngs_molbio_local';
    const isSequenceExport = source?.historyJournal !== undefined;

    useEffect(() => {
        if (!open) return;
        setProjectId(fixedProjectId ?? initialProjectId ?? '');
        setGlobalId(fixedContext?.globalExperimentId ?? destination?.globalExperimentId ?? '');
        setDomainId(fixedContext?.domainExperimentId ?? destination?.domainExperimentId ?? '');
        setAdapterId(source?.adapterId ?? '');
        setSelected(source ? {
            adapter_id: source.adapterId,
            entity_id: source.entityId,
            entity_kind: 'known_canonical_source',
            label: source.label,
            canonical_state: source.availability ?? 'availability_unavailable',
            attachable: source.availability === 'available',
            reason: source.availability === 'available' ? null : 'Source availability is not verified.',
            reopen_uri: '',
            metadata: {},
        } : null);
        setQuery('');
        setOperation('attach_reference');
        setRole('references');
        setNote('');
        setAttached(false);
        setIncludeHistory(false);
        setSetupMode(null);
        setMode('item');
        setTargetGlobalProjectId('');
        setSelectedExperimentIds([]);
        setSelectedResultIds([]);
    }, [fixedContext?.domainExperimentId, fixedContext?.globalExperimentId, fixedProjectId, initialProjectId, open, source, destination?.globalExperimentId, destination?.domainExperimentId]);

    const fixedProject = useQuery({ queryKey: ['project-manager', 'attachment-project', projectId], queryFn: () => getProject(projectId), enabled: open && Boolean(projectId) && !summary });
    const projects = useQuery({ queryKey: ['project-manager', 'attachment-projects'], queryFn: ({ signal }) => listProjects(signal), enabled: open && !fixedProjectId });
    const globals = useQuery({ queryKey: ['project-manager', 'attachment-globals', projectId], queryFn: ({ signal }) => listGlobalExperiments(projectId, signal), enabled: open && !fixedContext && Boolean(projectId) });
    const domains = useQuery({ queryKey: ['project-manager', 'attachment-domains', projectId, globalId], queryFn: ({ signal }) => listDomainExperiments(projectId, globalId, signal), enabled: open && !fixedContext && Boolean(projectId && globalId) });
    const adapters = useQuery({ queryKey: ['project-manager', 'attachment-adapters'], queryFn: ({ signal }) => listDomainAdapters(signal), enabled: open });
    const globalProjects = useQuery({ queryKey: ['project-manager', 'link-target-projects'], queryFn: ({ signal }) => searchProjects({ projectScope: 'global', archive: 'active', limit: 100, signal }), enabled: open && isLocalProject && mode === 'project-link' });
    const localExperiments = useQuery({ queryKey: ['project-manager', 'link-local-experiments', fixedProjectId], queryFn: ({ signal }) => listGlobalExperiments(fixedProjectId as string, signal), enabled: open && isLocalProject && mode === 'project-link' && Boolean(fixedProjectId) });
    const shareableResults = useQuery({ queryKey: ['project-manager', 'link-shareable-results', fixedProjectId], queryFn: ({ signal }) => listNgsMolBioShareableResults(fixedProjectId as string, signal), enabled: open && isLocalProject && mode === 'project-link' && Boolean(fixedProjectId) });
    const setupSummary = useQuery({
        queryKey: ['project-manager', 'attachment-setup-summary', projectId, globalId],
        queryFn: () => getProjectSummary(projectId, globalId ? { focusId: globalId, selectedNodeKey: `global_experiment:${globalId}` } : {}),
        enabled: open && setupMode !== null && Boolean(projectId),
    });
    const nativeState = useQuery({
        queryKey: ['molbio-ngs-domain-state', domainId], queryFn: () => fetchMolBioNgsDomainState(domainId),
        enabled: open && adapterId === MOLECULAR_MEMBER_ADAPTER && Boolean(domainId),
        retry: (count, error) => count < 5 && (error as { response?: { status?: number } }).response?.status === 404,
        retryDelay: 1000,
    });
    useEffect(() => {
        if (!fixedContext && !globalId && globals.data?.length === 1) setGlobalId(globals.data[0].id);
    }, [fixedContext, globalId, globals.data]);
    const selectedDomainKind = summary?.selection.node_type === 'domain_experiment' && typeof summary.selection.summary.domain_kind === 'string'
        ? summary.selection.summary.domain_kind
        : null;
    const domainKind = selectedDomainKind ?? (domains.data ?? []).find((item) => item.id === domainId)?.domain_kind ?? null;
    const compatibleAdapters = useMemo(
        () => (adapters.data?.adapters ?? []).filter((adapter) => !domainKind || adapter.domain_kind === domainKind),
        [adapters.data?.adapters, domainKind],
    );
    const selectedAdapterDomainKind = (adapters.data?.adapters ?? []).find((adapter) => adapter.adapter_id === adapterId)?.domain_kind ?? null;
    const compatibleDomains = useMemo(
        () => (domains.data ?? []).filter((domain) => !selectedAdapterDomainKind || domain.domain_kind === selectedAdapterDomainKind),
        [domains.data, selectedAdapterDomainKind],
    );
    useEffect(() => {
        if (!fixedContext && !domainId && compatibleDomains.length === 1) setDomainId(compatibleDomains[0].id);
    }, [compatibleDomains, domainId, fixedContext]);
    const expectedHeadGeneration = summary?.project.head_generation
        ?? fixedProject.data?.head_generation
        ?? projects.data?.items.find((project) => project.id === projectId)?.head_generation;

    useEffect(() => {
        if (source || adapterId || !compatibleAdapters.length) return;
        setAdapterId(compatibleAdapters.find((adapter) => adapter.adapter_id === MOLECULAR_MEMBER_ADAPTER)?.adapter_id ?? compatibleAdapters[0]?.adapter_id ?? '');
    }, [adapterId, compatibleAdapters, source]);

    useEffect(() => {
        if (fixedContext || domains.isPending || !domainId || compatibleDomains.some((domain) => domain.id === domainId)) return;
        setDomainId('');
    }, [compatibleDomains, domainId, fixedContext, domains.isPending]);

    const searchMutation = useMutation({
        mutationFn: () => searchAdapterEntities(adapterId, query, 25),
        onSuccess: () => setSelected(null),
    });
    const attachMutation = useMutation({
        mutationFn: async () => {
            if (!projectId || !globalId || !domainId || !adapterId || !selected) throw new Error('Choose a complete hierarchy destination and canonical source.');
            if (!Number.isInteger(expectedHeadGeneration)) throw new Error('The current Project generation is unavailable.');
            if (operation === 'clone_import_revision') throw new Error('Clone/import is not available through the receipt attachment contract.');
            const history = includeHistory && source?.historyJournal ? await readMolecularProjectHistory(selected.entity_id) : null;
            const nativeRevision = adapterId === MOLECULAR_MEMBER_ADAPTER
                ? await addMolecularProjectMember(projectId, globalId, domainId, selected.entity_id, destination?.globalExperimentId === globalId && destination.domainExperimentId === domainId ? destination.stateRevisionId : null) : null;
            // PM receipt attachment follows native membership acknowledgement.
            // Keep the supplied Project generation so concurrent edits fail closed.
            const molecularIdentity = new URLSearchParams(selected.entity_id);
            if (adapterId === MOLECULAR_MEMBER_ADAPTER) molecularIdentity.set('domain_experiment_id', domainId);
            const receipt = await attachExistingEntity(projectId, globalId, domainId, {
                adapter_id: adapterId,
                entity_id: adapterId === MOLECULAR_MEMBER_ADAPTER ? molecularIdentity.toString() : selected.entity_id,
                operation,
                role,
                note: note.trim() || null,
                expected_head_generation: expectedHeadGeneration as number,
            });
            if (history) {
                try { await exportMolecularProjectHistory(projectId, globalId, domainId, history, source?.historyJournal ?? [], receipt.source_receipt_id); }
                catch (error) { throw new Error(`The sequence is attached, but history export is incomplete. Retry to finish. ${projectManagerErrorMessage(error)}`); }
            }
            return { ...receipt, nativeStateRevisionId: nativeRevision?.id };
        },
        onSuccess: async (receipt) => {
            setAttached(true);
            await Promise.all([queryClient.invalidateQueries({ queryKey: ['project-manager'] }), queryClient.invalidateQueries({ queryKey: ['molbio-project-hub'] }), queryClient.invalidateQueries({ queryKey: ['molbio-ngs-domain-state'] })]);
            onAttached?.(receipt.source_receipt_id, receipt.nativeStateRevisionId, { projectId, globalExperimentId: globalId, domainExperimentId: domainId });
        },
        onError: async () => {
            // A native member may already be saved when the Project CAS loses.
            // Refresh generations; a retry reuses that exact member receipt.
            await queryClient.invalidateQueries({ queryKey: ['project-manager'] });
        },
    });
    const linkMutation = useMutation({
        mutationFn: () => {
            if (!fixedProjectId || !targetGlobalProjectId || selectedExperimentIds.length === 0) throw new Error('Choose a Global Project and at least one Experiment.');
            return linkNgsMolBioProject(targetGlobalProjectId, {
                local_project_id: fixedProjectId,
                experiment_ids: selectedExperimentIds,
                result_ids: selectedResultIds,
                change_summary: 'Link NGS/MolBio Project from Project Manager',
            });
        },
        onSuccess: async () => {
            await queryClient.invalidateQueries({ queryKey: ['project-manager'] });
            onAttached?.(null);
        },
    });

    if (!open) return null;
    const metadata = selected?.metadata ?? {};
    const revision = source?.revision ?? metadataString(metadata.entity_revision_id) ?? metadataString(metadata.revision_id);
    const digest = source?.digest ?? metadataString(metadata.content_digest) ?? metadataString(metadata.digest);
    const availability = source?.availability ?? (selected?.canonical_state === 'availability_unavailable' ? null : selected?.canonical_state);
    const alreadyAttached = attached || Boolean(selected && summary?.map.nodes.some((node) => node.canonical_identity.entity_id === selected.entity_id));
    const adapterCompatible = Boolean(selectedAdapterDomainKind && domainKind && selectedAdapterDomainKind === domainKind);
    const canAttach = Boolean(projectId && globalId && domainId && adapterId && adapterCompatible && Number.isInteger(expectedHeadGeneration) && (selected?.attachable || (adapterId === MOLECULAR_MEMBER_ADAPTER && selected?.entity_kind === 'molecular_revision')) && operation !== 'clone_import_revision' && !alreadyAttached && (adapterId !== MOLECULAR_MEMBER_ADAPTER || nativeState.isSuccess));
    const cloneOperatorHref = projectId && globalId && domainId
        ? `/ngs?${new URLSearchParams({
            workspace_id: projectId,
            ownership_scope: 'global',
            global_experiment_id: globalId,
            domain_experiment_id: domainId,
            section: 'workflow-plans',
        }).toString()}`
        : null;

    if (mode === 'project-link' && isLocalProject) {
        return (
            <div className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-3" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
                <section role="dialog" aria-modal="true" aria-labelledby="project-link-title" className="max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border-primary bg-surface-secondary shadow-2xl">
                    <header className="flex items-start justify-between gap-4 border-b border-border-primary px-5 py-4"><div><p className="text-xs font-semibold text-accent">Add current work to Project</p><h2 id="project-link-title" className="mt-1 text-lg font-semibold text-content">Link NGS/MolBio Project</h2><p className="mt-1 text-sm text-content-secondary">Choose what the Global Project can reference. Native data stays in this NGS/MolBio Project.</p></div><button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-3 py-1.5 text-xs text-content-secondary">Close</button></header>
                    <div className="space-y-4 p-5">
                        <button type="button" onClick={() => setMode('item')} className="rounded-lg border border-border-primary px-3 py-2 text-sm font-semibold text-content-secondary">Attach an item instead</button>
                        <label className="block text-sm font-semibold text-content-secondary">Global Project<select aria-label="Global Project link target" value={targetGlobalProjectId} onChange={(event) => setTargetGlobalProjectId(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content"><option value="">Choose Global Project…</option>{(globalProjects.data?.items ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                        <fieldset className="rounded-xl border border-border-primary bg-surface p-3"><legend className="px-1 text-sm font-semibold text-content">Experiments to expose</legend><div className="space-y-2">{(localExperiments.data ?? []).map((experiment) => <label key={experiment.id} className="flex items-center gap-2 text-sm text-content-secondary"><input type="checkbox" value={experiment.id} checked={selectedExperimentIds.includes(experiment.id)} onChange={(event) => setSelectedExperimentIds((current) => event.target.checked ? [...current, experiment.id] : current.filter((id) => id !== experiment.id))} />{experiment.name}</label>)}</div>{!localExperiments.isLoading && (localExperiments.data?.length ?? 0) === 0 && <p className="text-sm text-content-muted">No Experiments are available.</p>}</fieldset>
                        <fieldset className="rounded-xl border border-border-primary bg-surface p-3"><legend className="px-1 text-sm font-semibold text-content">Results to expose</legend><div className="space-y-2">{(shareableResults.data ?? []).map((result) => <label key={result.result_receipt_id} className="flex items-center gap-2 text-sm text-content-secondary"><input type="checkbox" value={result.result_receipt_id} checked={selectedResultIds.includes(result.result_receipt_id)} onChange={(event) => setSelectedResultIds((current) => event.target.checked ? [...current, result.result_receipt_id] : current.filter((id) => id !== result.result_receipt_id))} />{result.entity_kind} · {result.entity_id}</label>)}</div>{!shareableResults.isLoading && (shareableResults.data?.length ?? 0) === 0 && <p className="text-sm text-content-muted">No governed Results are available.</p>}</fieldset>
                        <p className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content-secondary">This creates a governed relationship. It does not copy sequence, read, signal, or result payloads.</p>
                        {linkMutation.isError && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-sm text-error">{projectManagerErrorMessage(linkMutation.error)}</p>}
                        <div className="flex justify-end gap-2 border-t border-border-primary pt-4"><button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-4 py-2 text-sm font-semibold text-content-secondary">Cancel</button><button type="button" disabled={!targetGlobalProjectId || selectedExperimentIds.length === 0 || linkMutation.isPending} onClick={() => linkMutation.mutate()} className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{linkMutation.isPending ? 'Linking…' : 'Create Project link'}</button></div>
                    </div>
                </section>
            </div>
        );
    }

    return (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-3" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
            <section role="dialog" aria-modal="true" aria-labelledby="project-attachment-title" className="max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border-primary bg-surface-secondary shadow-2xl">
                <header className="flex items-start justify-between gap-4 border-b border-border-primary px-5 py-4">
                    <div><p className="text-xs font-semibold text-accent">Project relationship</p><h2 id="project-attachment-title" className="mt-1 text-lg font-semibold text-content">{source?.historyJournal ? 'Export to Project' : source ? 'Add current work to Project' : 'Attach existing record'}</h2><p className="mt-1 text-sm text-content-secondary">{isSequenceExport ? `Add ${source?.label || 'this saved sequence'} to the selected Project, with optional history.` : 'Attach one verified reference. Canonical entities and bytes remain in their source store.'}</p></div>
                    <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-3 py-1.5 text-xs text-content-secondary">Close</button>
                </header>
                <div className="space-y-4 p-5">
                    {isLocalProject && <button type="button" onClick={() => setMode('project-link')} className="rounded-lg border border-accent px-3 py-2 text-sm font-semibold text-accent">Link this NGS/MolBio Project to a Global Project</button>}
                    <div className="grid gap-3 sm:grid-cols-3">
                        <label className="text-xs font-semibold text-content-secondary">Project
                            <select aria-label="Attachment Project" value={projectId} disabled={Boolean(fixedProjectId)} onChange={(event) => { setProjectId(event.target.value); setGlobalId(''); setDomainId(''); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedProjectId ? <option value={fixedProjectId}>{summary?.project.name ?? fixedProjectId}</option> : <><option value="">Select Project…</option>{(projects.data?.items ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</>}
                            </select>
                        </label>
                        <label className="text-xs font-semibold text-content-secondary">{isSequenceExport ? 'Experiment' : 'Global Experiment'}
                            <select aria-label="Attachment Global Experiment" value={globalId} disabled={Boolean(fixedContext) || !projectId} onChange={(event) => { setGlobalId(event.target.value); setDomainId(''); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedContext ? <option value={fixedContext.globalExperimentId}>{fixedContext.globalExperimentId}</option> : <><option value="">Select Global Experiment…</option>{(globals.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</>}
                            </select>
                        </label>
                        <label className="text-xs font-semibold text-content-secondary">{isSequenceExport ? 'NGS/MolBio workspace' : 'Domain Experiment'}
                            <select aria-label="Attachment Domain Experiment" value={domainId} disabled={Boolean(fixedContext) || !globalId} onChange={(event) => setDomainId(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedContext ? <option value={fixedContext.domainExperimentId}>{summary?.selection.title ?? fixedContext.domainExperimentId}</option> : <><option value="">Select compatible Domain Experiment…</option>{compatibleDomains.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</>}
                            </select>
                        </label>
                    </div>
                    {!fixedContext && projectId && <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={() => setSetupMode('create_global')} className="rounded border border-accent px-3 py-2 text-xs text-accent">New experiment</button>
                        {globalId && <button type="button" onClick={() => setSetupMode('create_domain')} className="rounded border border-accent px-3 py-2 text-xs text-accent">Add NGS/MolBio workspace</button>}
                        {setupMode && setupSummary.isPending && <span role="status" className="text-xs text-content-secondary">Loading Project setup…</span>}
                        {setupSummary.isError && <span role="alert" className="text-xs text-error">{projectManagerErrorMessage(setupSummary.error)}</span>}
                    </div>}
                    {adapterId === MOLECULAR_MEMBER_ADAPTER && domainId && !nativeState.isSuccess && <p role={nativeState.isError ? 'alert' : 'status'} className="text-xs text-content-secondary">{nativeState.isError ? 'Native workspace is not ready.' : 'Waiting for the managed connector to prepare this workspace…'}{nativeState.isError && <button type="button" className="ml-2 underline" onClick={() => void nativeState.refetch()}>Retry workspace connection</button>}</p>}
                    {!isSequenceExport && <>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <label className="text-xs font-semibold text-content-secondary">Operation mode
                            <select aria-label="Attachment operation mode" value={operation} onChange={(event) => { const next = event.target.value as AttachmentOperationMode; setOperation(next); if (next !== 'clone_import_revision') setRole(operationCopy[next].role); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                <option value="attach_reference">Attach membership / reference receipt</option><option value="bind_input">Bind immutable input</option><option value="link_output">Link generated output</option><option value="attach_evidence">Attach evidence</option><option value="clone_import_revision">Clone / import exact run intent into a new Plan draft</option>
                            </select>
                        </label>
                        <label className="text-xs font-semibold text-content-secondary">Lineage role
                            <select aria-label="Lineage role" value={role} disabled={operation === 'clone_import_revision'} onChange={(event) => setRole(event.target.value as LineageRole)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-50">
                                <option value="references">References</option><option value="uses_input">Uses immutable input</option><option value="produced">Produced output</option><option value="validated_by">Validated by evidence</option>
                            </select>
                        </label>
                    </div>
                    <p className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs text-content-secondary">{operation === 'clone_import_revision' ? 'Use the exact Run Group operator. It selects one immutable run and attempt, imports the complete source Plan payload and pinned capability contract into a fresh generation-0 draft, and writes derived_from lineage. It creates no preparation or Job.' : `${operationCopy[operation].copy} ${roleCopy[role]}`}</p>
                    </>}
                    {!source && <div className="rounded-xl border border-border-primary bg-surface p-3">
                        <label className="text-xs font-semibold text-content-secondary">Canonical source adapter
                            <select value={adapterId} onChange={(event) => { setAdapterId(event.target.value); setSelected(null); searchMutation.reset(); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2.5 text-content">
                                {!compatibleAdapters.length && <option value="">No compatible adapters</option>}{compatibleAdapters.map((adapter) => <option key={adapter.adapter_id} value={adapter.adapter_id}>{adapter.display_name ?? adapter.adapter_id}</option>)}
                            </select>
                        </label>
                        <div className="mt-3 flex gap-2"><input aria-label="Search canonical records" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Canonical ID or source-owned label" className="min-w-0 flex-1 rounded-lg border border-border-primary bg-surface-secondary px-3 py-2.5 text-sm text-content" /><button type="button" disabled={!adapterId || searchMutation.isPending} onClick={() => searchMutation.mutate()} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{searchMutation.isPending ? 'Searching…' : 'Search'}</button></div>
                        <div className="mt-3 max-h-48 space-y-2 overflow-y-auto">{(searchMutation.data?.items ?? []).map((item) => <label key={item.entity_id} className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2 ${selected?.entity_id === item.entity_id ? 'border-accent bg-accent/10' : 'border-border-primary bg-surface-secondary'}`}><input type="radio" name="canonical-source" value={item.entity_id} disabled={!item.attachable && !(adapterId === MOLECULAR_MEMBER_ADAPTER && item.entity_kind === 'molecular_revision')} checked={selected?.entity_id === item.entity_id} onChange={() => setSelected(item)} /><span className="min-w-0"><span className="block text-sm font-semibold text-content">{item.label}</span><span className="block break-all font-mono text-[10px] text-content-muted">{item.entity_kind} · {item.entity_id} · {item.canonical_state}</span></span></label>)}</div>
                    </div>}
                    <details className="text-xs text-content-secondary"><summary className="cursor-pointer">Saved source details</summary>
                    <section aria-label="Canonical source preview" className="rounded-xl border border-border-primary bg-surface p-3 text-xs">
                        <h3 className="font-semibold text-content">Canonical source preview</h3>
                        <dl className="mt-2 grid gap-2 sm:grid-cols-2"><div><dt className="text-content-muted">Source</dt><dd className="break-all text-content-secondary">{selected ? `${selected.label} · ${selected.entity_id}` : 'Not selected'}</dd></div><div><dt className="text-content-muted">Availability</dt><dd className="text-content-secondary">{availability ?? 'Unavailable from adapter search'}</dd></div><div><dt className="text-content-muted">Source revision</dt><dd className="break-all font-mono text-content-secondary">{revision ?? 'Unavailable from backend'}</dd></div><div><dt className="text-content-muted">Content digest</dt><dd className="break-all font-mono text-content-secondary">{digest ?? 'Unavailable from backend'}</dd></div><div><dt className="text-content-muted">Already attached</dt><dd className="text-content-secondary">{selected ? (alreadyAttached ? 'Yes' : summary ? 'No in loaded bounded map' : 'Unavailable from backend') : 'Unavailable until selected'}</dd></div><div><dt className="text-content-muted">Expected Project generation</dt><dd className="text-content-secondary">{expectedHeadGeneration ?? 'Unavailable from backend'}</dd></div></dl>
                    </section>
                    </details>
                    {adapterId === MOLECULAR_MEMBER_ADAPTER && <details className="text-xs text-content-secondary"><summary className="cursor-pointer">Workspace policy</summary><p className="rounded-lg border border-border-primary bg-surface p-3 text-xs text-content-secondary">Adds the exact saved sequence as an expected construct in this workspace, then attaches its verified Project receipt. Existing workspace settings are preserved. A new sequence inventory uses exact revision coordinates, no acquisition, no permitted analysis workflows, terminal manifests required, and server-owned assessment; completion alone never means a scientific pass.</p></details>}
                    {source?.historyJournal && <label className="flex items-start gap-2 rounded-lg border border-border-primary bg-surface p-3 text-sm text-content-secondary"><input type="checkbox" checked={includeHistory} onChange={(event) => setIncludeHistory(event.target.checked)} /><span>Include entire recorded plasmid history<span className="mt-1 block text-xs text-content-muted">All saved sequence revisions, related saved operations and PCR reviews, plus this workspace's recorded edit journal. History is captured at export; later changes are not automatically exported.</span></span></label>}
                    <label className="block text-xs font-semibold text-content-secondary">Optional note
                        <input aria-label="Optional attachment note" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} placeholder="Reason or scientific context for this relationship" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content" />
                    </label>
                    <p className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-content-secondary">The server re-verifies canonical identity and digest, persists the selected operation and note, and rejects stale Project generations.</p>
                    {(attachMutation.isError || searchMutation.isError || projects.isError || globals.isError || domains.isError || adapters.isError || fixedProject.isError) && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(attachMutation.error ?? searchMutation.error ?? projects.error ?? globals.error ?? domains.error ?? adapters.error ?? fixedProject.error)}</p>}
                    {attached && <p role="status" className="rounded-lg border border-success/50 bg-success/10 p-3 text-xs text-success">Verified receipt attached. The canonical source remains authoritative.</p>}
                    <div className="flex justify-end gap-2 border-t border-border-primary pt-4"><button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-4 py-2 text-xs font-semibold text-content-secondary">Cancel</button>{operation === 'clone_import_revision' ? (cloneOperatorHref ? <Link to={cloneOperatorHref} onClick={onClose} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white">Open exact run-clone operator</Link> : <button type="button" disabled className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white opacity-50">Select complete hierarchy context</button>) : <button type="button" disabled={!canAttach || attachMutation.isPending} onClick={() => attachMutation.mutate()} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{attachMutation.isPending ? 'Verifying…' : alreadyAttached ? 'Already attached' : source?.historyJournal ? 'Export to Project' : 'Verify receipt and attach'}</button>}</div>
                </div>
            </section>
            <ManagerDialog mode={setupSummary.data ? setupMode : null} projectId={projectId} summary={setupSummary.data} onClose={() => setSetupMode(null)} onComplete={(destination) => {
                if (destination?.selectedNodeKey?.startsWith('global_experiment:')) { setGlobalId(destination.selectedNodeKey.slice('global_experiment:'.length)); setDomainId(''); }
                if (destination?.selectedNodeKey?.startsWith('domain_experiment:')) setDomainId(destination.selectedNodeKey.slice('domain_experiment:'.length));
                setSetupMode(null); void queryClient.invalidateQueries({ queryKey: ['project-manager'] });
            }} />
        </div>
    );
}
