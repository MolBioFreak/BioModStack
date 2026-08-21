import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    attachExistingEntity,
    listDomainAdapters,
    listDomainExperiments,
    listGlobalExperiments,
    listProjects,
    projectManagerErrorMessage,
    searchAdapterEntities,
    type AdapterEntityProjection,
    type LineageRole,
    type ProjectManagerReadModel,
} from '../../lib/projectManager';
import { selectedDomainContext } from './projectManagerState';

export type AttachmentOperationMode = 'attach_reference' | 'bind_input' | 'link_output' | 'attach_evidence' | 'clone_import_revision';

export interface ProjectAttachmentSource {
    adapterId: string;
    entityId: string;
    label: string;
    revision?: string | null;
    digest?: string | null;
    availability?: string | null;
}

interface ProjectAttachmentDialogProps {
    open: boolean;
    source?: ProjectAttachmentSource;
    projectId?: string;
    summary?: ProjectManagerReadModel;
    onClose: () => void;
    onAttached?: (receiptId: string | null) => void;
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

export function ProjectAttachmentDialog({ open, source, projectId: fixedProjectId, summary, onClose, onAttached }: ProjectAttachmentDialogProps) {
    const queryClient = useQueryClient();
    const fixedContext = summary ? selectedDomainContext(summary) : null;
    const [projectId, setProjectId] = useState(fixedProjectId ?? '');
    const [globalId, setGlobalId] = useState(fixedContext?.globalExperimentId ?? '');
    const [domainId, setDomainId] = useState(fixedContext?.domainExperimentId ?? '');
    const [adapterId, setAdapterId] = useState(source?.adapterId ?? '');
    const [query, setQuery] = useState('');
    const [selected, setSelected] = useState<AdapterEntityProjection | null>(null);
    const [operation, setOperation] = useState<AttachmentOperationMode>('attach_reference');
    const [role, setRole] = useState<LineageRole>('references');
    const [note, setNote] = useState('');
    const [attached, setAttached] = useState(false);

    useEffect(() => {
        if (!open) return;
        setProjectId(fixedProjectId ?? '');
        setGlobalId(fixedContext?.globalExperimentId ?? '');
        setDomainId(fixedContext?.domainExperimentId ?? '');
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
    }, [fixedContext?.domainExperimentId, fixedContext?.globalExperimentId, fixedProjectId, open, source]);

    const projects = useQuery({ queryKey: ['project-manager', 'attachment-projects'], queryFn: ({ signal }) => listProjects(signal), enabled: open && !fixedProjectId });
    const globals = useQuery({ queryKey: ['project-manager', 'attachment-globals', projectId], queryFn: ({ signal }) => listGlobalExperiments(projectId, signal), enabled: open && !fixedContext && Boolean(projectId) });
    const domains = useQuery({ queryKey: ['project-manager', 'attachment-domains', projectId, globalId], queryFn: ({ signal }) => listDomainExperiments(projectId, globalId, signal), enabled: open && !fixedContext && Boolean(projectId && globalId) });
    const adapters = useQuery({ queryKey: ['project-manager', 'attachment-adapters'], queryFn: ({ signal }) => listDomainAdapters(signal), enabled: open });
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
    const expectedHeadGeneration = summary?.project.head_generation
        ?? projects.data?.items.find((project) => project.id === projectId)?.head_generation;

    useEffect(() => {
        if (source || adapterId || !compatibleAdapters.length) return;
        setAdapterId(compatibleAdapters[0]?.adapter_id ?? '');
    }, [adapterId, compatibleAdapters, source]);

    useEffect(() => {
        if (fixedContext || !domainId || compatibleDomains.some((domain) => domain.id === domainId)) return;
        setDomainId('');
    }, [compatibleDomains, domainId, fixedContext]);

    const searchMutation = useMutation({
        mutationFn: () => searchAdapterEntities(adapterId, query, 25),
        onSuccess: () => setSelected(null),
    });
    const attachMutation = useMutation({
        mutationFn: () => {
            if (!projectId || !globalId || !domainId || !adapterId || !selected) throw new Error('Choose a complete hierarchy destination and canonical source.');
            if (!Number.isInteger(expectedHeadGeneration)) throw new Error('The current Project generation is unavailable.');
            if (operation === 'clone_import_revision') throw new Error('Clone/import is not available through the receipt attachment contract.');
            return attachExistingEntity(projectId, globalId, domainId, {
                adapter_id: adapterId,
                entity_id: selected.entity_id,
                operation,
                role,
                note: note.trim() || null,
                expected_head_generation: expectedHeadGeneration as number,
            });
        },
        onSuccess: async (receipt) => {
            setAttached(true);
            await queryClient.invalidateQueries({ queryKey: ['project-manager'] });
            onAttached?.(receipt.source_receipt_id);
        },
    });

    if (!open) return null;
    const metadata = selected?.metadata ?? {};
    const revision = source?.revision ?? metadataString(metadata.entity_revision_id) ?? metadataString(metadata.revision_id);
    const digest = source?.digest ?? metadataString(metadata.content_digest) ?? metadataString(metadata.digest);
    const availability = source?.availability ?? (selected?.canonical_state === 'availability_unavailable' ? null : selected?.canonical_state);
    const alreadyAttached = attached || Boolean(selected && summary?.map.nodes.some((node) => node.canonical_identity.entity_id === selected.entity_id));
    const adapterCompatible = Boolean(selectedAdapterDomainKind && domainKind && selectedAdapterDomainKind === domainKind);
    const canAttach = Boolean(projectId && globalId && domainId && adapterId && adapterCompatible && Number.isInteger(expectedHeadGeneration) && selected?.attachable && operation !== 'clone_import_revision' && !alreadyAttached);
    const cloneOperatorHref = projectId && globalId && domainId
        ? `/ngs?${new URLSearchParams({
            workspace_id: projectId,
            ownership_scope: 'global',
            global_experiment_id: globalId,
            domain_experiment_id: domainId,
            section: 'workflow-plans',
        }).toString()}`
        : null;

    return (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-3" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
            <section role="dialog" aria-modal="true" aria-labelledby="project-attachment-title" className="max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border-primary bg-surface-secondary shadow-2xl">
                <header className="flex items-start justify-between gap-4 border-b border-border-primary px-5 py-4">
                    <div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Verified receipt reference</p><h2 id="project-attachment-title" className="mt-1 text-lg font-semibold text-content">Add existing / Add to Project</h2><p className="mt-1 text-xs text-content-secondary">One receipt-first interaction; canonical entities and bytes are never copied.</p></div>
                    <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-3 py-1.5 text-xs text-content-secondary">Close</button>
                </header>
                <div className="space-y-4 p-5">
                    <div className="grid gap-3 sm:grid-cols-3">
                        <label className="text-xs font-semibold text-content-secondary">Project
                            <select aria-label="Attachment Project" value={projectId} disabled={Boolean(fixedProjectId)} onChange={(event) => { setProjectId(event.target.value); setGlobalId(''); setDomainId(''); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedProjectId ? <option value={fixedProjectId}>{summary?.project.name ?? fixedProjectId}</option> : <><option value="">Select Project…</option>{(projects.data?.items ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</>}
                            </select>
                        </label>
                        <label className="text-xs font-semibold text-content-secondary">Global Experiment
                            <select aria-label="Attachment Global Experiment" value={globalId} disabled={Boolean(fixedContext) || !projectId} onChange={(event) => { setGlobalId(event.target.value); setDomainId(''); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedContext ? <option value={fixedContext.globalExperimentId}>{fixedContext.globalExperimentId}</option> : <><option value="">Select Global Experiment…</option>{(globals.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</>}
                            </select>
                        </label>
                        <label className="text-xs font-semibold text-content-secondary">Domain Experiment
                            <select aria-label="Attachment Domain Experiment" value={domainId} disabled={Boolean(fixedContext) || !globalId} onChange={(event) => setDomainId(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content disabled:opacity-70">
                                {fixedContext ? <option value={fixedContext.domainExperimentId}>{summary?.selection.title ?? fixedContext.domainExperimentId}</option> : <><option value="">Select compatible Domain Experiment…</option>{compatibleDomains.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</>}
                            </select>
                        </label>
                    </div>
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
                    {!source && <div className="rounded-xl border border-border-primary bg-surface p-3">
                        <label className="text-xs font-semibold text-content-secondary">Canonical source adapter
                            <select value={adapterId} onChange={(event) => { setAdapterId(event.target.value); setSelected(null); searchMutation.reset(); }} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2.5 text-content">
                                {!compatibleAdapters.length && <option value="">No compatible adapters</option>}{compatibleAdapters.map((adapter) => <option key={adapter.adapter_id} value={adapter.adapter_id}>{adapter.display_name ?? adapter.adapter_id}</option>)}
                            </select>
                        </label>
                        <div className="mt-3 flex gap-2"><input aria-label="Search canonical records" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Canonical ID or source-owned label" className="min-w-0 flex-1 rounded-lg border border-border-primary bg-surface-secondary px-3 py-2.5 text-sm text-content" /><button type="button" disabled={!adapterId || searchMutation.isPending} onClick={() => searchMutation.mutate()} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{searchMutation.isPending ? 'Searching…' : 'Search'}</button></div>
                        <div className="mt-3 max-h-48 space-y-2 overflow-y-auto">{(searchMutation.data?.items ?? []).map((item) => <label key={item.entity_id} className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2 ${selected?.entity_id === item.entity_id ? 'border-accent bg-accent/10' : 'border-border-primary bg-surface-secondary'}`}><input type="radio" name="canonical-source" value={item.entity_id} disabled={!item.attachable} checked={selected?.entity_id === item.entity_id} onChange={() => setSelected(item)} /><span className="min-w-0"><span className="block text-sm font-semibold text-content">{item.label}</span><span className="block break-all font-mono text-[10px] text-content-muted">{item.entity_kind} · {item.entity_id} · {item.canonical_state}</span></span></label>)}</div>
                    </div>}
                    <section aria-label="Canonical source preview" className="rounded-xl border border-border-primary bg-surface p-3 text-xs">
                        <h3 className="font-semibold text-content">Canonical source preview</h3>
                        <dl className="mt-2 grid gap-2 sm:grid-cols-2"><div><dt className="text-content-muted">Source</dt><dd className="break-all text-content-secondary">{selected ? `${selected.label} · ${selected.entity_id}` : 'Not selected'}</dd></div><div><dt className="text-content-muted">Availability</dt><dd className="text-content-secondary">{availability ?? 'Unavailable from adapter search'}</dd></div><div><dt className="text-content-muted">Source revision</dt><dd className="break-all font-mono text-content-secondary">{revision ?? 'Unavailable from backend'}</dd></div><div><dt className="text-content-muted">Content digest</dt><dd className="break-all font-mono text-content-secondary">{digest ?? 'Unavailable from backend'}</dd></div><div><dt className="text-content-muted">Already attached</dt><dd className="text-content-secondary">{selected ? (alreadyAttached ? 'Yes' : summary ? 'No in loaded bounded map' : 'Unavailable from backend') : 'Unavailable until selected'}</dd></div><div><dt className="text-content-muted">Expected Project generation</dt><dd className="text-content-secondary">{expectedHeadGeneration ?? 'Unavailable from backend'}</dd></div></dl>
                    </section>
                    <label className="block text-xs font-semibold text-content-secondary">Optional note
                        <input aria-label="Optional attachment note" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} placeholder="Reason or scientific context for this relationship" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content" />
                    </label>
                    <p className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-content-secondary">The server re-verifies canonical identity and digest, persists the selected operation and note, and rejects stale Project generations.</p>
                    {(attachMutation.isError || searchMutation.isError || projects.isError || globals.isError || domains.isError || adapters.isError) && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(attachMutation.error ?? searchMutation.error ?? projects.error ?? globals.error ?? domains.error ?? adapters.error)}</p>}
                    {attached && <p role="status" className="rounded-lg border border-success/50 bg-success/10 p-3 text-xs text-success">Verified receipt attached. The canonical source remains authoritative.</p>}
                    <div className="flex justify-end gap-2 border-t border-border-primary pt-4"><button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-4 py-2 text-xs font-semibold text-content-secondary">Cancel</button>{operation === 'clone_import_revision' ? (cloneOperatorHref ? <Link to={cloneOperatorHref} onClick={onClose} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white">Open exact run-clone operator</Link> : <button type="button" disabled className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white opacity-50">Select complete hierarchy context</button>) : <button type="button" disabled={!canAttach || attachMutation.isPending} onClick={() => attachMutation.mutate()} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{attachMutation.isPending ? 'Verifying…' : alreadyAttached ? 'Already attached' : 'Verify receipt and attach'}</button>}</div>
                </div>
            </section>
        </div>
    );
}
