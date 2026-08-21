import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
    archiveDomainExperiment,
    archiveGlobalExperiment,
    archiveProject,
    createDomainExperiment,
    createGlobalExperiment,
    createProject,
    createResearchRecord,
    getDomainExperiment,
    getGlobalExperiment,
    getProject,
    projectManagerErrorMessage,
    restoreDomainExperiment,
    restoreGlobalExperiment,
    restoreProject,
    updateDomainExperiment,
    updateGlobalExperiment,
    updateProject,
    type JsonObject,
    type ProjectManagerReadModel,
    type RecordKind,
} from '../../lib/projectManager';
import { globalExperimentForNode, selectedDomainContext } from './projectManagerState';

export type ManagerDialogMode = 'create_project' | 'create_global' | 'create_domain' | 'edit' | 'archive' | 'restore' | 'record';

function completeNgsDomainPayload(objective: string, experimentMode: string): JsonObject {
    return {
        schema: 'bms.ngs-molbio-experiment.v2',
        experiment_mode: experimentMode,
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

interface ManagerDialogProps {
    mode: ManagerDialogMode | null;
    projectId?: string;
    summary?: ProjectManagerReadModel;
    onClose: () => void;
    onComplete: (destination?: { projectId?: string; focusId?: string; selectedNodeKey?: string }) => void;
}

export function ManagerDialog({ mode, projectId, summary, onClose, onComplete }: ManagerDialogProps) {
    const selection = summary?.selection;
    const selectionId = typeof selection?.canonical_identity.entity_id === 'string' ? selection.canonical_identity.entity_id : null;
    const selectedGlobalId = summary && selection ? globalExperimentForNode(summary, selection.node_key) : null;
    const domainContext = summary ? selectedDomainContext(summary) : null;
    const [name, setName] = useState('');
    const [objective, setObjective] = useState('');
    const [question, setQuestion] = useState('');
    const [domainKind, setDomainKind] = useState<'protein_in_silico' | 'ngs_molbio'>('protein_in_silico');
    const [ngsExperimentMode, setNgsExperimentMode] = useState('analysis');
    const [recordKind, setRecordKind] = useState<RecordKind>('note');
    const [body, setBody] = useState('');

    const needsDetail = Boolean(mode && ['edit', 'archive', 'restore'].includes(mode));
    const detailQuery = useQuery({
        queryKey: ['project-manager', 'hierarchy-detail', projectId, selection?.node_type, selectionId],
        enabled: needsDetail && Boolean(projectId && selectionId),
        queryFn: ({ signal }) => {
            if (!projectId || !selectionId || !selection) throw new Error('The selected hierarchy identity is unavailable.');
            if (selection.node_type === 'project') return getProject(projectId, signal);
            if (selection.node_type === 'global_experiment') return getGlobalExperiment(projectId, selectionId, signal);
            if (selection.node_type === 'domain_experiment' && selectedGlobalId) return getDomainExperiment(projectId, selectedGlobalId, selectionId, signal);
            throw new Error('This selection does not support revision management.');
        },
    });

    useEffect(() => {
        if (!mode) return;
        setName('');
        setObjective('');
        setQuestion('');
        setDomainKind('protein_in_silico');
        setNgsExperimentMode('analysis');
        setRecordKind('note');
        setBody('');
    }, [mode]);

    useEffect(() => {
        if (mode !== 'edit' || !detailQuery.data) return;
        setName(detailQuery.data.name ?? '');
        const payload = detailQuery.data.payload ?? {};
        setObjective(typeof payload.research_objective === 'string' ? payload.research_objective : typeof payload.objective === 'string' ? payload.objective : '');
        setQuestion(typeof payload.scientific_question === 'string' ? payload.scientific_question : '');
    }, [detailQuery.data, mode]);

    const mutation = useMutation({
        mutationFn: async () => {
            if (mode === 'create_project') {
                return createProject({ schema: 'bms.project.v2', project_scope: 'global', name, research_objective: objective, status: 'active', change_summary: 'Created in Project Manager' });
            }
            if (!projectId || !summary) throw new Error('A Project context is required.');
            if (mode === 'create_global') {
                return createGlobalExperiment(projectId, { schema: 'bms.global-experiment.v2', name, objective, scientific_question: question, status: 'planned', change_summary: 'Created in Project Manager' });
            }
            if (mode === 'create_domain') {
                const globalId = selection?.node_type === 'global_experiment' ? selectionId : selectedGlobalId;
                if (!globalId) throw new Error('Select a Global Experiment before creating a Domain Experiment.');
                if (domainKind === 'protein_in_silico') {
                    throw new Error('Protein Domain creation requires exact producer-native receipt selection. This selector remains closed until accepted Protein capabilities are installed.');
                }
                const domainPayload = completeNgsDomainPayload(objective, ngsExperimentMode);
                return createDomainExperiment(projectId, globalId, {
                    schema: 'bms.domain-experiment.v4',
                    domain_kind: 'ngs_molbio',
                    domain_contract_version: '3',
                    name,
                    objective,
                    status: 'planned',
                    tags: [],
                    source_receipt_ids: [],
                    dataset_revision_ids: [],
                    change_summary: 'Created in Project Manager',
                    domain_payload: domainPayload,
                });
            }
            if (mode === 'record') {
                if (!selectionId || !selection) throw new Error('Select a hierarchy record before adding an ELN-lite record.');
                const subject = selection.node_type === 'project'
                    ? { projectId }
                    : selection.node_type === 'global_experiment'
                        ? { projectId, globalExperimentId: selectionId }
                        : selection.node_type === 'domain_experiment' && domainContext
                            ? { projectId, globalExperimentId: domainContext.globalExperimentId, domainExperimentId: selectionId }
                            : null;
                if (!subject) throw new Error('This selection does not accept notes or decisions.');
                return createResearchRecord(subject, { record_kind: recordKind, body });
            }
            const detail = detailQuery.data;
            if (!detail || !selectionId || !selection) throw new Error('Current generation is still loading.');
            if (mode === 'edit') {
                if (selection.node_type === 'project') {
                    return updateProject(projectId, { expected_head_generation: detail.head_generation, name, research_objective: objective, change_summary: 'Edited in Project Manager' });
                }
                if (selection.node_type === 'global_experiment') {
                    return updateGlobalExperiment(projectId, selectionId, { expected_head_generation: detail.head_generation, name, objective, scientific_question: question, change_summary: 'Edited in Project Manager' });
                }
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) {
                    return updateDomainExperiment(projectId, selectedGlobalId, selectionId, { expected_head_generation: detail.head_generation, name, objective, change_summary: 'Edited in Project Manager' });
                }
            }
            if (mode === 'archive') {
                if (selection.node_type === 'project') return archiveProject(projectId, detail.head_generation);
                if (selection.node_type === 'global_experiment') return archiveGlobalExperiment(projectId, selectionId, detail.head_generation);
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) return archiveDomainExperiment(projectId, selectedGlobalId, selectionId, detail.head_generation);
            }
            if (mode === 'restore') {
                if (selection.node_type === 'project') return restoreProject(projectId, detail.head_generation);
                if (selection.node_type === 'global_experiment') return restoreGlobalExperiment(projectId, selectionId, detail.head_generation);
                if (selection.node_type === 'domain_experiment' && selectedGlobalId) return restoreDomainExperiment(projectId, selectedGlobalId, selectionId, detail.head_generation);
            }
            throw new Error('This Project Manager operation is unavailable for the current selection.');
        },
        onSuccess: (result) => {
            if (mode === 'create_project' && 'id' in result && typeof result.id === 'string') {
                onComplete({ projectId: result.id });
            } else if (mode === 'create_global' && 'id' in result && typeof result.id === 'string') {
                onComplete({ focusId: result.id, selectedNodeKey: `global_experiment:${result.id}` });
            } else if (mode === 'create_domain' && 'id' in result && typeof result.id === 'string') {
                onComplete({ focusId: selectedGlobalId ?? selectionId ?? undefined, selectedNodeKey: `domain_experiment:${result.id}` });
            } else {
                onComplete();
            }
            onClose();
        },
    });

    const title = useMemo(() => {
        if (mode === 'create_project') return 'Create Project';
        if (mode === 'create_global') return 'Create Global Experiment';
        if (mode === 'create_domain') return 'Create Domain Experiment';
        if (mode === 'edit') return `Edit ${selection?.title ?? 'revision'}`;
        if (mode === 'archive') return `Archive ${selection?.title ?? 'selection'}`;
        if (mode === 'restore') return `Restore ${selection?.title ?? 'selection'}`;
        if (mode === 'record') return `Add record to ${selection?.title ?? 'selection'}`;
        return 'Project Manager action';
    }, [mode, selection?.title]);

    if (!mode) return null;
    const confirmation = mode === 'archive' || mode === 'restore';
    const domainCreationReady = domainKind === 'ngs_molbio';
    const canSubmit = confirmation
        ? Boolean(detailQuery.data)
        : mode === 'record'
            ? body.trim().length > 0
            : name.trim().length > 0 && (mode !== 'create_domain' || domainCreationReady);

    return (
        <div className="fixed inset-0 z-[95] grid place-items-center bg-black/65 p-3" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
            <section role="dialog" aria-modal="true" aria-labelledby="manager-action-title" className="w-full max-w-lg overflow-hidden rounded-2xl border border-border-primary bg-surface-secondary shadow-2xl">
                <header className="flex items-start justify-between gap-3 border-b border-border-primary px-5 py-4">
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Immutable Project record</p>
                        <h2 id="manager-action-title" className="mt-1 text-lg font-semibold text-content">{title}</h2>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-3 py-1.5 text-xs text-content-secondary">Close</button>
                </header>
                <form onSubmit={(event) => { event.preventDefault(); if (canSubmit) mutation.mutate(); }} className="space-y-4 p-5">
                    {detailQuery.isLoading && <p className="text-xs text-content-muted">Loading the current server-owned generation…</p>}
                    {confirmation ? (
                        <div className="rounded-xl border border-warning/50 bg-warning/10 p-4 text-sm text-content-secondary">
                            {mode === 'archive' ? (
                                <><p className="font-semibold text-warning">Archive removes this item from active navigation.</p><p className="mt-2">It retains identity, child records, receipts, notes, lineage, and audit history. It does not delete or cancel any canonical scientific run.</p></>
                            ) : (
                                <><p className="font-semibold text-success">Restore returns this item to active Project navigation.</p><p className="mt-2">The mutation uses the current server generation and creates an audited lifecycle transition.</p></>
                            )}
                        </div>
                    ) : mode === 'record' ? (
                        <>
                            <label className="block text-xs font-semibold text-content-secondary">Record type
                                <select value={recordKind} onChange={(event) => setRecordKind(event.target.value as RecordKind)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                    <option value="note">Note</option><option value="observation">Observation</option><option value="decision">Decision</option><option value="conclusion">Conclusion</option>
                                </select>
                            </label>
                            <label className="block text-xs font-semibold text-content-secondary">Body
                                <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={5} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            <p className="text-[10px] text-content-muted">Records are append-only. A later correction creates a replacement record; it does not rewrite this entry.</p>
                        </>
                    ) : (
                        <>
                            <label className="block text-xs font-semibold text-content-secondary">Name
                                <input autoFocus value={name} onChange={(event) => setName(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            {mode === 'create_domain' && <label className="block text-xs font-semibold text-content-secondary">Domain type
                                <select value={domainKind} onChange={(event) => setDomainKind(event.target.value as typeof domainKind)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                    <option value="protein_in_silico">Protein In Silico</option><option value="ngs_molbio">NGS / MolBio</option>
                                </select>
                            </label>}
                            {mode === 'create_domain' && domainKind === 'protein_in_silico' && (
                                <div className="rounded-xl border border-warning/50 bg-warning/10 p-3 text-xs text-content-secondary">
                                    Protein Domain creation is closed until the server advertises accepted Protein capabilities and exact producer-native receipt selectors. Project Manager will not invent target or validation contracts.
                                </div>
                            )}
                            {mode === 'create_domain' && domainKind === 'ngs_molbio' && (
                                <label className="block text-xs font-semibold text-content-secondary">Experiment mode
                                    <select value={ngsExperimentMode} onChange={(event) => setNgsExperimentMode(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                        <option value="molecular_design">Molecular design</option>
                                        <option value="assembly_validation">Assembly validation</option>
                                        <option value="pcr_validation">PCR validation</option>
                                        <option value="sequencing">Sequencing</option>
                                        <option value="quality_control">Quality control</option>
                                        <option value="alignment">Alignment</option>
                                        <option value="comparison">Comparison</option>
                                        <option value="analysis">Analysis</option>
                                    </select>
                                </label>
                            )}
                            <label className="block text-xs font-semibold text-content-secondary">Objective
                                <textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={3} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>
                            {(mode === 'create_global' || (mode === 'edit' && selection?.node_type === 'global_experiment')) && <label className="block text-xs font-semibold text-content-secondary">Scientific question
                                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>}
                        </>
                    )}
                    {(mutation.isError || detailQuery.isError) && <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">{projectManagerErrorMessage(mutation.error ?? detailQuery.error)}{mutation.error && projectManagerErrorMessage(mutation.error).toLowerCase().includes('generation') ? ' Reload the current Project read model before retrying.' : ''}</p>}
                    <div className="flex justify-end gap-2 border-t border-border-primary pt-4">
                        <button type="button" onClick={onClose} className="rounded-lg border border-border-primary px-4 py-2 text-xs font-semibold text-content-secondary">Cancel</button>
                        <button type="submit" disabled={!canSubmit || mutation.isPending} className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 focus:ring-2 focus:ring-accent">
                            {mutation.isPending ? 'Saving…' : mode === 'archive' ? 'Archive without cancelling runs' : mode === 'restore' ? 'Restore' : mode === 'record' ? `Append ${recordKind}` : 'Save immutable revision'}
                        </button>
                    </div>
                </form>
            </section>
        </div>
    );
}
