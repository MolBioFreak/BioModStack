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
    issueAdapterReceipt,
    listDomainAdapters,
    projectManagerErrorMessage,
    restoreDomainExperiment,
    restoreGlobalExperiment,
    restoreProject,
    searchAdapterEntities,
    updateDomainExperiment,
    updateGlobalExperiment,
    updateProject,
    upgradeGlobalExperiment,
    upgradeProject,
    type AdapterEntityProjection,
    type JsonObject,
    type JsonValue,
    type ProjectManagerReadModel,
    type ProteinExperimentMode,
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

function completeProteinDomainPayload(
    objective: string,
    targetId: string,
    targetLabel: string,
    targetRole: string,
    sourceReceiptIdsText: string,
    datasetMemberRefsText: string,
    entityMapReferenceText: string,
    expectedContentSha256: string,
    experimentMode: ProteinExperimentMode,
): JsonObject {
    const sourceReceiptIds = sourceReceiptIdsText.split(',').map((value) => value.trim()).filter(Boolean);
    let datasetMemberRefs: JsonValue[] = [];
    let entityMapReference: JsonValue;
    try {
        const parsed = JSON.parse(datasetMemberRefsText || '[]');
        if (!Array.isArray(parsed)) throw new Error('Dataset member references must be a JSON array.');
        datasetMemberRefs = parsed;
        entityMapReference = JSON.parse(entityMapReferenceText);
    } catch (error) {
        throw new Error(error instanceof Error ? error.message : 'Protein authority JSON is invalid.');
    }
    if (!targetId.trim() || !targetLabel.trim() || !sourceReceiptIds.length || !expectedContentSha256.trim()) {
        throw new Error('Protein Domains require target identity, at least one source receipt, and an expected content SHA-256.');
    }
    return {
        schema: 'bms.protein-in-silico-experiment.v3',
        experiment_mode: experimentMode,
        scientific_objective: objective,
        targets: [{
            target_id: targetId.trim(),
            label: targetLabel.trim(),
            role: targetRole,
            source_receipt_ids: sourceReceiptIds,
            dataset_member_refs: datasetMemberRefs,
            entity_map_reference: entityMapReference,
            expected_content_sha256: expectedContentSha256.trim().toLowerCase(),
        }],
        design_constraints: [],
        planned_capability_ids: [],
        comparison_groups: [],
        validation_capability_ids: [],
        acceptance_criteria: [],
        evidence_plan: [],
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
    const [description, setDescription] = useState('');
    const [objective, setObjective] = useState('');
    const [question, setQuestion] = useState('');
    const [hypothesis, setHypothesis] = useState('');
    const [priority, setPriority] = useState<'low' | 'normal' | 'high' | 'critical'>('normal');
    const [contributors, setContributors] = useState('');
    const [tags, setTags] = useState('');
    const [startDate, setStartDate] = useState('');
    const [targetEndDate, setTargetEndDate] = useState('');
    const [successCriteria, setSuccessCriteria] = useState('');
    const [reviewSummary, setReviewSummary] = useState('');
    const [conclusion, setConclusion] = useState('');
    const [domainKind, setDomainKind] = useState<'protein_in_silico' | 'ngs_molbio'>('protein_in_silico');
    const [ngsExperimentMode, setNgsExperimentMode] = useState('analysis');
    const [proteinExperimentMode, setProteinExperimentMode] = useState<ProteinExperimentMode>('design');
    const [proteinTargetId, setProteinTargetId] = useState('');
    const [proteinTargetLabel, setProteinTargetLabel] = useState('');
    const [proteinTargetRole, setProteinTargetRole] = useState('target');
    const [proteinSourceReceiptIds, setProteinSourceReceiptIds] = useState('');
    const [proteinDatasetMemberRefs, setProteinDatasetMemberRefs] = useState('[]');
    const [proteinEntityMapReference, setProteinEntityMapReference] = useState('{}');
    const [proteinExpectedContentSha256, setProteinExpectedContentSha256] = useState('');
    const [proteinSourceAdapterId, setProteinSourceAdapterId] = useState('');
    const [proteinSourceQuery, setProteinSourceQuery] = useState('');
    const [proteinSourceSelection, setProteinSourceSelection] = useState<AdapterEntityProjection | null>(null);
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
    const proteinAdaptersQuery = useQuery({
        queryKey: ['project-manager', 'protein-source-adapters'],
        queryFn: ({ signal }) => listDomainAdapters(signal),
        enabled: mode === 'create_domain' && domainKind === 'protein_in_silico',
    });
    const proteinAdapters = (proteinAdaptersQuery.data?.adapters ?? []).filter((adapter) => adapter.domain_kind === 'protein_in_silico');
    const proteinSourceSearch = useMutation({
        mutationFn: () => searchAdapterEntities(proteinSourceAdapterId, proteinSourceQuery, 25),
        onSuccess: () => setProteinSourceSelection(null),
    });
    const proteinReceiptIssue = useMutation({
        mutationFn: async () => {
            if (!projectId || !proteinSourceAdapterId || !proteinSourceSelection) throw new Error('Select one verified Protein source.');
            const result = await issueAdapterReceipt(proteinSourceAdapterId, proteinSourceSelection.entity_id, projectId);
            const digest = result.receipt.content_digest;
            if (typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest)) throw new Error('The verified source receipt has no exact content digest.');
            return { result, digest };
        },
        onSuccess: ({ result, digest }) => {
            setProteinSourceReceiptIds(result.receipt_id);
            setProteinExpectedContentSha256(digest);
        },
    });

    useEffect(() => {
        if (!mode) return;
        setName('');
        setDescription('');
        setObjective('');
        setQuestion('');
        setHypothesis('');
        setPriority('normal');
        setContributors('');
        setTags('');
        setStartDate('');
        setTargetEndDate('');
        setSuccessCriteria('');
        setReviewSummary('');
        setConclusion('');
        setDomainKind('protein_in_silico');
        setNgsExperimentMode('analysis');
        setProteinExperimentMode('design');
        setProteinTargetId('');
        setProteinTargetLabel('');
        setProteinTargetRole('target');
        setProteinSourceReceiptIds('');
        setProteinDatasetMemberRefs('[]');
        setProteinEntityMapReference('{}');
        setProteinExpectedContentSha256('');
        setProteinSourceAdapterId('');
        setProteinSourceQuery('');
        setProteinSourceSelection(null);
        setRecordKind('note');
        setBody('');
    }, [mode]);

    useEffect(() => {
        if (proteinSourceAdapterId || !proteinAdapters.length) return;
        setProteinSourceAdapterId(proteinAdapters[0]?.adapter_id ?? '');
    }, [proteinAdapters, proteinSourceAdapterId]);

    useEffect(() => {
        if (mode !== 'edit' || !detailQuery.data) return;
        setName(detailQuery.data.name ?? '');
        const payload = detailQuery.data.payload ?? {};
        const list = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
        setDescription(detailQuery.data.description ?? '');
        setObjective(typeof payload.research_objective === 'string' ? payload.research_objective : typeof payload.objective === 'string' ? payload.objective : '');
        setQuestion(typeof payload.scientific_question === 'string' ? payload.scientific_question : '');
        setHypothesis(typeof payload.hypothesis === 'string' ? payload.hypothesis : '');
        setPriority(payload.priority === 'low' || payload.priority === 'high' || payload.priority === 'critical' ? payload.priority : 'normal');
        setContributors(list(payload.contributors).join(', '));
        setTags(list(payload.tags).join(', '));
        setStartDate(typeof payload.start_date === 'string' ? payload.start_date : '');
        setTargetEndDate(typeof payload.target_end_date === 'string' ? payload.target_end_date : '');
        setSuccessCriteria(list(payload.success_criteria).join('\n'));
        setReviewSummary(typeof payload.review_summary === 'string' ? payload.review_summary : '');
        setConclusion(typeof payload.conclusion === 'string' ? payload.conclusion : '');
    }, [detailQuery.data, mode]);

    const mutation = useMutation({
        mutationFn: async () => {
            if (mode === 'create_project') {
                return createProject({
                    schema: 'bms.project.v2',
                    project_scope: 'global',
                    name,
                    description,
                    research_objective: objective,
                    contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                    tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                    status: 'active',
                    start_date: startDate || null,
                    target_end_date: targetEndDate || null,
                    change_summary: 'Created in Project Manager',
                });
            }
            if (!projectId || !summary) throw new Error('A Project context is required.');
            if (mode === 'create_global') {
                return createGlobalExperiment(projectId, {
                    schema: 'bms.global-experiment.v2',
                    name,
                    objective,
                    scientific_question: question,
                    hypothesis: hypothesis || null,
                    description,
                    status: 'planned',
                    priority,
                    tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                    shared_source_receipt_ids: [],
                    shared_dataset_ids: [],
                    comparison_plan: null,
                    success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                    review_summary: reviewSummary || null,
                    conclusion: conclusion || null,
                    change_summary: 'Created in Project Manager',
                });
            }
            if (mode === 'create_domain') {
                const globalId = selection?.node_type === 'global_experiment' ? selectionId : selectedGlobalId;
                if (!globalId) throw new Error('Select a Global Experiment before creating a Domain Experiment.');
                if (domainKind === 'protein_in_silico') {
                    const domainPayload = completeProteinDomainPayload(
                        objective,
                        proteinTargetId,
                        proteinTargetLabel,
                        proteinTargetRole,
                        proteinSourceReceiptIds,
                        proteinDatasetMemberRefs,
                        proteinEntityMapReference,
                        proteinExpectedContentSha256,
                        proteinExperimentMode,
                    );
                    const datasetRevisionIds = (JSON.parse(proteinDatasetMemberRefs || '[]') as Array<{ dataset_revision_id?: unknown }>)
                        .map((reference) => typeof reference.dataset_revision_id === 'string' ? reference.dataset_revision_id : '')
                        .filter(Boolean);
                    return createDomainExperiment(projectId, globalId, {
                        schema: 'bms.domain-experiment.v4',
                        domain_kind: 'protein_in_silico',
                        domain_contract_version: '3',
                        name,
                        objective,
                        status: 'draft',
                        tags: [],
                        source_receipt_ids: proteinSourceReceiptIds.split(',').map((value) => value.trim()).filter(Boolean),
                        dataset_revision_ids: datasetRevisionIds,
                        change_summary: 'Created in Project Manager',
                        domain_payload: domainPayload,
                    });
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
                    if (detail.payload?.schema === 'bms.project.v1') {
                        return upgradeProject(projectId, {
                            expected_head_generation: detail.head_generation,
                            schema: 'bms.project.v2',
                            project_scope: detail.payload.project_scope === 'ngs_molbio_local' ? 'ngs_molbio_local' : 'global',
                            name,
                            description,
                            research_objective: objective,
                            contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                            tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                            status: detail.status as 'draft' | 'active' | 'on_hold' | 'completed' | 'archived',
                            start_date: startDate || null,
                            target_end_date: targetEndDate || null,
                            change_summary: 'Upgraded Project to v2 from Project Manager',
                        });
                    }
                    return updateProject(projectId, {
                        expected_head_generation: detail.head_generation,
                        name,
                        description,
                        research_objective: objective,
                        contributors: contributors.split(',').map((value) => value.trim()).filter(Boolean),
                        tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                        start_date: startDate || null,
                        target_end_date: targetEndDate || null,
                        change_summary: 'Edited in Project Manager',
                    });
                }
                if (selection.node_type === 'global_experiment') {
                    if (detail.payload?.schema === 'bms.global-experiment.v1') {
                        return upgradeGlobalExperiment(projectId, selectionId, {
                            expected_head_generation: detail.head_generation,
                            schema: 'bms.global-experiment.v2',
                            name,
                            objective,
                            scientific_question: question,
                            hypothesis: hypothesis || null,
                            description,
                            status: detail.status as 'draft' | 'planned' | 'active' | 'analysis' | 'review' | 'completed' | 'blocked' | 'archived',
                            priority,
                            tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                            shared_source_receipt_ids: [],
                            shared_dataset_ids: [],
                            comparison_plan: null,
                            success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                            review_summary: reviewSummary || null,
                            conclusion: conclusion || null,
                            change_summary: 'Upgraded Global Experiment to v2 from Project Manager',
                        });
                    }
                    return updateGlobalExperiment(projectId, selectionId, {
                        expected_head_generation: detail.head_generation,
                        name,
                        objective,
                        scientific_question: question,
                        description,
                        hypothesis: hypothesis || null,
                        priority,
                        tags: tags.split(',').map((value) => value.trim()).filter(Boolean),
                        success_criteria: successCriteria.split('\n').map((value) => value.trim()).filter(Boolean),
                        review_summary: reviewSummary || null,
                        conclusion: conclusion || null,
                        change_summary: 'Edited in Project Manager',
                    });
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
    const domainCreationReady = domainKind === 'ngs_molbio'
        || (proteinTargetId.trim().length > 0
            && proteinTargetLabel.trim().length > 0
            && proteinSourceReceiptIds.trim().length > 0
            && proteinExpectedContentSha256.trim().length === 64
            && proteinEntityMapReference.trim().length > 0);
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
                            {(mode === 'create_project' || mode === 'create_global' || (mode === 'edit' && (selection?.node_type === 'project' || selection?.node_type === 'global_experiment'))) && <label className="block text-xs font-semibold text-content-secondary">Description
                                <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-sm text-content outline-none focus:border-accent focus:ring-2 focus:ring-accent/30" />
                            </label>}
                            {(mode === 'create_project' || (mode === 'edit' && selection?.node_type === 'project')) && (
                                <div className="grid gap-3 rounded-xl border border-border-primary bg-surface p-3 sm:grid-cols-2">
                                    <label className="text-xs font-semibold text-content-secondary">Contributors
                                        <input value={contributors} onChange={(event) => setContributors(event.target.value)} placeholder="comma-separated names" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Tags
                                        <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="comma-separated tags" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Start date
                                        <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="text-xs font-semibold text-content-secondary">Target end date
                                        <input type="date" value={targetEndDate} onChange={(event) => setTargetEndDate(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                </div>
                            )}
                            {(mode === 'create_global' || (mode === 'edit' && selection?.node_type === 'global_experiment')) && (
                                <div className="space-y-3 rounded-xl border border-border-primary bg-surface p-3">
                                    <label className="block text-xs font-semibold text-content-secondary">Hypothesis
                                        <textarea value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Priority
                                        <select value={priority} onChange={(event) => setPriority(event.target.value as typeof priority)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select>
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Success criteria
                                        <textarea value={successCriteria} onChange={(event) => setSuccessCriteria(event.target.value)} rows={3} placeholder="one criterion per line" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Review summary
                                        <textarea value={reviewSummary} onChange={(event) => setReviewSummary(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Conclusion
                                        <textarea value={conclusion} onChange={(event) => setConclusion(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                </div>
                            )}
                            {mode === 'create_domain' && <label className="block text-xs font-semibold text-content-secondary">Domain type
                                <select value={domainKind} onChange={(event) => setDomainKind(event.target.value as typeof domainKind)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface px-3 py-2.5 text-content">
                                    <option value="protein_in_silico">Protein In Silico</option><option value="ngs_molbio">NGS / MolBio</option>
                                </select>
                            </label>}
                            {mode === 'create_domain' && domainKind === 'protein_in_silico' && (
                                <div className="space-y-3 rounded-xl border border-border-primary bg-surface p-3">
                                    <p className="text-xs text-content-secondary">Protein v3 uses producer-native target authority. Enter IDs from existing verified receipts. The server resolves each receipt and checks every digest.</p>
                                    <label className="block text-xs font-semibold text-content-secondary">Protein experiment mode
                                        <select aria-label="Protein experiment mode" value={proteinExperimentMode} onChange={(event) => setProteinExperimentMode(event.target.value as ProteinExperimentMode)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-content">
                                            {['exploration', 'design', 'redesign', 'prediction', 'validation', 'comparison', 'simulation', 'analysis'].map((value) => <option key={value} value={value}>{value}</option>)}
                                        </select>
                                    </label>
                                    <div className="space-y-2 rounded-lg border border-border-primary p-3">
                                        <p className="text-xs font-semibold text-content-secondary">Verify a producer-native source receipt</p>
                                        <select aria-label="Protein source adapter" value={proteinSourceAdapterId} onChange={(event) => { setProteinSourceAdapterId(event.target.value); setProteinSourceSelection(null); }} className="w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content">
                                            {proteinAdapters.map((adapter) => <option key={adapter.adapter_id} value={adapter.adapter_id}>{adapter.display_name}</option>)}
                                        </select>
                                        <div className="flex gap-2">
                                            <input aria-label="Search Protein source records" value={proteinSourceQuery} onChange={(event) => setProteinSourceQuery(event.target.value)} placeholder="1UBQ or exact Job ID" className="min-w-0 flex-1 rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content" />
                                            <button type="button" disabled={!proteinSourceAdapterId || proteinSourceSearch.isPending} onClick={() => proteinSourceSearch.mutate()} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary disabled:opacity-50">Search Protein sources</button>
                                        </div>
                                        {(proteinSourceSearch.data?.items ?? []).map((item) => (
                                            <label key={item.entity_id} className="flex gap-2 rounded-lg border border-border-primary p-2 text-xs text-content-secondary">
                                                <input type="radio" name="protein-source-record" value={item.entity_id} checked={proteinSourceSelection?.entity_id === item.entity_id} disabled={!item.attachable} onChange={() => setProteinSourceSelection(item)} />
                                                <span><strong className="text-content">{item.label}</strong><br />{item.canonical_state}{item.reason ? ` · ${item.reason}` : ''}</span>
                                            </label>
                                        ))}
                                        <button type="button" disabled={!proteinSourceSelection?.attachable || proteinReceiptIssue.isPending} onClick={() => proteinReceiptIssue.mutate()} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Verify and use receipt</button>
                                        {(proteinAdaptersQuery.isError || proteinSourceSearch.isError || proteinReceiptIssue.isError) && <p role="alert" className="text-xs text-error">{projectManagerErrorMessage(proteinAdaptersQuery.error ?? proteinSourceSearch.error ?? proteinReceiptIssue.error)}</p>}
                                    </div>
                                    <label className="block text-xs font-semibold text-content-secondary">Target ID
                                        <input value={proteinTargetId} onChange={(event) => setProteinTargetId(event.target.value)} placeholder="target identifier" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Target label
                                        <input value={proteinTargetLabel} onChange={(event) => setProteinTargetLabel(event.target.value)} placeholder="target label" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Target role
                                        <select value={proteinTargetRole} onChange={(event) => setProteinTargetRole(event.target.value)} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-content">
                                            {['target', 'binder', 'partner', 'template', 'reference', 'control', 'motif', 'ligand_context', 'other'].map((role) => <option key={role} value={role}>{role}</option>)}
                                        </select>
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Source receipt IDs
                                        <input aria-label="Protein source receipt IDs" value={proteinSourceReceiptIds} onChange={(event) => setProteinSourceReceiptIds(event.target.value)} placeholder="comma-separated receipt IDs" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Dataset member references JSON
                                        <textarea value={proteinDatasetMemberRefs} onChange={(event) => setProteinDatasetMemberRefs(event.target.value)} rows={2} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Entity-map reference JSON
                                        <textarea value={proteinEntityMapReference} onChange={(event) => setProteinEntityMapReference(event.target.value)} rows={4} className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs text-content" />
                                    </label>
                                    <label className="block text-xs font-semibold text-content-secondary">Expected content SHA-256
                                        <input aria-label="Protein expected content SHA-256" value={proteinExpectedContentSha256} onChange={(event) => setProteinExpectedContentSha256(event.target.value)} placeholder="64 lowercase hexadecimal characters" className="mt-1.5 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 font-mono text-xs text-content" />
                                    </label>
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
