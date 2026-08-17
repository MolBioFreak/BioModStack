import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    attachExistingEntity,
    archiveDomainDataset,
    createDomainDataset,
    getDomainDataset,
    getDomainDatasetRevision,
    getProject,
    listDomainDatasetKinds,
    listDomainDatasetRevisionMembers,
    listDomainDatasetRevisions,
    listDomainDatasets,
    projectManagerErrorMessage,
    restoreDomainDataset,
    reviseDomainDataset,
    type DomainDatasetKindDescriptor,
    type DomainDatasetMember,
    type DomainDatasetMemberDraft,
} from '../../lib/projectManager';
import { fetchMolBioNgsStateRevision, type DomainStateMember } from '../../lib/api';

interface DomainDatasetOperatorProps {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
    canMutate: boolean;
    mutationBlocker: string | null;
    currentStateRevisionId: string | null;
    selectedRevisionIds: string[];
    onSelectedRevisionIdsChange: (revisionIds: string[]) => void;
}

const INPUT_CLASS = 'w-full rounded-md border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary disabled:cursor-not-allowed disabled:opacity-40';
const BUTTON_CLASS = 'rounded-md border border-border-primary bg-surface-secondary px-3 py-2 text-xs font-semibold text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';
const PRIMARY_BUTTON_CLASS = 'rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';

function ErrorBanner({ error }: { error: unknown }) {
    if (!error) return null;
    return (
        <div role="alert" className="rounded-md border border-error/40 bg-error/10 px-3 py-2 text-sm text-error">
            {projectManagerErrorMessage(error)}
        </div>
    );
}

function KeyValue({ label, value }: { label: string; value: string | number | null | undefined }) {
    return (
        <div className="min-w-0">
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-content-muted">{label}</dt>
            <dd className="mt-0.5 break-all font-mono text-xs text-content-secondary">{value ?? 'Unavailable'}</dd>
        </div>
    );
}

function emptyMember(role = ''): DomainDatasetMemberDraft {
    return {
        receipt_id: '',
        role,
        media_type: null,
        metadata: { display_label: null, group_label: null, condition_label: null, tags: [] },
    };
}

function editableMember(member: DomainDatasetMember): DomainDatasetMemberDraft {
    return {
        receipt_id: member.receipt_id,
        role: member.role,
        media_type: member.media_type,
        metadata: {
            display_label: member.metadata.display_label ?? null,
            group_label: member.metadata.group_label ?? null,
            condition_label: member.metadata.condition_label ?? null,
            tags: member.metadata.tags ?? [],
        },
    };
}

function normalizedTags(value: string): string[] {
    return Array.from(new Set(value.split(',').map((tag) => tag.trim()).filter(Boolean)));
}

export default function DomainDatasetOperator({
    projectId,
    globalExperimentId,
    domainExperimentId,
    canMutate,
    mutationBlocker,
    currentStateRevisionId,
    selectedRevisionIds,
    onSelectedRevisionIdsChange,
}: DomainDatasetOperatorProps) {
    const queryClient = useQueryClient();
    const scope = [projectId, globalExperimentId, domainExperimentId] as const;
    const [selectedDatasetId, setSelectedDatasetId] = useState('');
    const [selectedRevisionId, setSelectedRevisionId] = useState('');
    const [datasetName, setDatasetName] = useState('');
    const [datasetKind, setDatasetKind] = useState('');
    const [createSummary, setCreateSummary] = useState('Create typed Dataset');
    const [revisionSummary, setRevisionSummary] = useState('Publish operator-reviewed Dataset revision');
    const [lifecycleSummary, setLifecycleSummary] = useState('Operator-reviewed Dataset lifecycle change');
    const [members, setMembers] = useState<DomainDatasetMemberDraft[]>([]);

    const kindsQuery = useQuery({
        queryKey: ['domain-dataset-kinds', ...scope],
        queryFn: ({ signal }) => listDomainDatasetKinds(...scope, signal),
        retry: false,
    });
    const datasetsQuery = useQuery({
        queryKey: ['domain-datasets', ...scope],
        queryFn: ({ signal }) => listDomainDatasets(...scope, signal),
        retry: false,
    });
    const datasetQuery = useQuery({
        queryKey: ['domain-dataset', ...scope, selectedDatasetId],
        queryFn: ({ signal }) => getDomainDataset(...scope, selectedDatasetId, signal),
        enabled: Boolean(selectedDatasetId),
        retry: false,
    });
    const revisionsQuery = useQuery({
        queryKey: ['domain-dataset-revisions', ...scope, selectedDatasetId],
        queryFn: ({ signal }) => listDomainDatasetRevisions(...scope, selectedDatasetId, signal),
        enabled: Boolean(selectedDatasetId),
        retry: false,
    });
    const revisionQuery = useQuery({
        queryKey: ['domain-dataset-revision', ...scope, selectedDatasetId, selectedRevisionId],
        queryFn: ({ signal }) => getDomainDatasetRevision(...scope, selectedDatasetId, selectedRevisionId, signal),
        enabled: Boolean(selectedDatasetId && selectedRevisionId),
        retry: false,
    });
    const pagedMembersQuery = useQuery({
        queryKey: ['domain-dataset-revision-members', ...scope, selectedDatasetId, selectedRevisionId],
        queryFn: ({ signal }) => listDomainDatasetRevisionMembers(...scope, selectedDatasetId, selectedRevisionId, signal),
        enabled: Boolean(selectedDatasetId && selectedRevisionId && revisionQuery.data?.members_uri),
        retry: false,
    });
    const stateRevisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', domainExperimentId, currentStateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(domainExperimentId, currentStateRevisionId as string),
        enabled: Boolean(currentStateRevisionId),
        retry: false,
    });

    useEffect(() => {
        const datasets = datasetsQuery.data?.items ?? [];
        if (!datasets.length) {
            setSelectedDatasetId('');
            return;
        }
        if (!datasets.some((dataset) => dataset.dataset_id === selectedDatasetId)) {
            setSelectedDatasetId(datasets[0].dataset_id);
        }
    }, [datasetsQuery.data?.items, selectedDatasetId]);

    useEffect(() => {
        const kinds = kindsQuery.data?.items ?? [];
        if (datasetKind && !kinds.some((kind) => kind.dataset_kind === datasetKind)) setDatasetKind('');
    }, [datasetKind, kindsQuery.data?.items]);

    useEffect(() => {
        const revisions = revisionsQuery.data?.items ?? [];
        if (!revisions.length) {
            setSelectedRevisionId('');
            return;
        }
        if (!revisions.some((revision) => revision.revision_id === selectedRevisionId)) {
            const current = datasetQuery.data?.current_revision_id;
            setSelectedRevisionId(current && revisions.some((revision) => revision.revision_id === current)
                ? current
                : revisions[0].revision_id);
        }
    }, [datasetQuery.data?.current_revision_id, revisionsQuery.data?.items, selectedRevisionId]);

    useEffect(() => {
        setSelectedDatasetId('');
        setSelectedRevisionId('');
        setMembers([]);
    }, [projectId, globalExperimentId, domainExperimentId]);

    const selectedKind = useMemo<DomainDatasetKindDescriptor | null>(() => {
        const kindId = datasetQuery.data?.dataset_kind ?? datasetKind;
        return kindsQuery.data?.items.find((kind) => kind.dataset_kind === kindId) ?? null;
    }, [datasetKind, datasetQuery.data?.dataset_kind, kindsQuery.data?.items]);
    const roleOptions = useMemo(() => {
        const options = (selectedKind?.allowed_members ?? []).flatMap((contract) =>
            contract.allowed_roles.map((role) => ({ role, receiptKind: contract.receipt_kind })));
        return Array.from(new Map(options.map((option) => [option.role, option])).values());
    }, [selectedKind]);
    const exactMembers = revisionQuery.data?.members ?? pagedMembersQuery.data?.items ?? [];
    const attachedReferenceMembers = (stateRevisionQuery.data?.members ?? []).filter(
        (member: DomainStateMember) => member.entity_kind === 'molecular_revision',
    );

    const invalidateDatasets = async () => {
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['domain-datasets', ...scope] }),
            queryClient.invalidateQueries({ queryKey: ['domain-dataset', ...scope, selectedDatasetId] }),
            queryClient.invalidateQueries({ queryKey: ['domain-dataset-revisions', ...scope, selectedDatasetId] }),
        ]);
    };

    const createMutation = useMutation({
        mutationFn: () => createDomainDataset(...scope, {
            name: datasetName.trim(),
            dataset_kind: datasetKind,
            change_summary: createSummary.trim(),
        }),
        onSuccess: async (created) => {
            setDatasetName('');
            await queryClient.invalidateQueries({ queryKey: ['domain-datasets', ...scope] });
            setSelectedDatasetId(created.dataset_id);
        },
    });
    const attachReferenceMutation = useMutation({
        mutationFn: async (member: DomainStateMember) => {
            const params = member.reopen_destination?.params;
            const sequenceId = params && typeof params === 'object' && !Array.isArray(params)
                ? (params as Record<string, unknown>).sequence_id
                : null;
            if (typeof sequenceId !== 'string' || !sequenceId) {
                throw new Error('Exact molecular sequence identity is unavailable for Dataset attachment.');
            }
            const project = await getProject(projectId);
            const attachment = await attachExistingEntity(projectId, globalExperimentId, domainExperimentId, {
                adapter_id: 'bms.molbio.member-molecular-revision.adapter.v1',
                entity_id: new URLSearchParams({
                    sequence_id: sequenceId,
                    revision_id: member.entity_id,
                    domain_experiment_id: domainExperimentId,
                }).toString(),
                operation: 'attach_reference',
                role: 'references',
                note: 'Dataset membership authority for an exact Experiment-linked molecular revision.',
                expected_head_generation: project.head_generation,
            });
            return { member, receiptId: attachment.source_receipt_id };
        },
        onSuccess: ({ member, receiptId }) => setMembers((current) => [...current, {
            receipt_id: receiptId,
            role: 'molecular_expected_construct',
            media_type: null,
            metadata: { display_label: member.entity_id, group_label: null, condition_label: null, tags: ['experiment-reference'] },
        }]),
    });
    const reviseMutation = useMutation({
        mutationFn: () => reviseDomainDataset(
            ...scope,
            selectedDatasetId,
            datasetQuery.data?.head_generation ?? 0,
            revisionSummary.trim(),
            members,
        ),
        onSuccess: async (created) => {
            await invalidateDatasets();
            setSelectedRevisionId(created.revision_id);
        },
    });
    const lifecycleMutation = useMutation({
        mutationFn: (operation: 'archive' | 'restore') => operation === 'archive'
            ? archiveDomainDataset(...scope, selectedDatasetId, datasetQuery.data?.head_generation ?? 0, lifecycleSummary.trim())
            : restoreDomainDataset(...scope, selectedDatasetId, datasetQuery.data?.head_generation ?? 0, lifecycleSummary.trim()),
        onSuccess: invalidateDatasets,
    });

    const activeError = kindsQuery.error
        ?? datasetsQuery.error
        ?? datasetQuery.error
        ?? revisionsQuery.error
        ?? revisionQuery.error
        ?? pagedMembersQuery.error
        ?? stateRevisionQuery.error
        ?? createMutation.error
        ?? attachReferenceMutation.error
        ?? reviseMutation.error
        ?? lifecycleMutation.error;
    const revisionSelectedForPreparation = selectedRevisionId && selectedRevisionIds.includes(selectedRevisionId);
    const memberDraftValid = members.every((member) => member.receipt_id.trim() && member.role);

    const updateMember = (index: number, update: (member: DomainDatasetMemberDraft) => DomainDatasetMemberDraft) => {
        setMembers((current) => current.map((member, memberIndex) => memberIndex === index ? update(member) : member));
    };
    const moveMember = (index: number, direction: -1 | 1) => {
        setMembers((current) => {
            const target = index + direction;
            if (target < 0 || target >= current.length) return current;
            const next = [...current];
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });
    };

    return (
        <div className="space-y-4">
            <ErrorBanner error={activeError} />
            <section className="rounded-lg border border-border-primary bg-surface-secondary p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-sm font-semibold text-content-primary">Typed Dataset authority</h3>
                        <p className="mt-1 text-xs text-content-muted">
                            Dataset kinds and member roles come from the server registry. The browser supplies receipt IDs and display-only metadata; the server resolves immutable native authority.
                        </p>
                    </div>
                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${canMutate ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning'}`}>
                        {canMutate ? 'mutation enabled' : 'read-only'}
                    </span>
                </div>
                {!canMutate && <p className="mt-2 text-xs text-warning">{mutationBlocker}</p>}
                <div className="mt-4 grid gap-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
                    <input className={INPUT_CLASS} value={datasetName} onChange={(event) => setDatasetName(event.target.value)} placeholder="Dataset name" />
                    <select className={INPUT_CLASS} value={datasetKind} onChange={(event) => setDatasetKind(event.target.value)}>
                        <option value="">Select an enabled server Dataset kind</option>
                        {(kindsQuery.data?.items ?? []).map((kind) => (
                            <option key={kind.dataset_kind} value={kind.dataset_kind}>{kind.label} · {kind.dataset_kind}</option>
                        ))}
                    </select>
                    <input className={INPUT_CLASS} value={createSummary} onChange={(event) => setCreateSummary(event.target.value)} placeholder="Creation change summary" />
                    <button
                        type="button"
                        className={PRIMARY_BUTTON_CLASS}
                        disabled={!canMutate || !datasetName.trim() || !datasetKind || !createSummary.trim() || createMutation.isPending}
                        onClick={() => createMutation.mutate()}
                    >Create Dataset</button>
                </div>
                {!kindsQuery.isLoading && (kindsQuery.data?.items ?? []).length === 0 && (
                    <p className="mt-3 rounded-md border border-dashed border-border-primary p-3 text-xs text-content-muted">
                        The server currently advertises no enabled Dataset kind for this Domain. Arbitrary kind IDs are not accepted.
                    </p>
                )}
            </section>

            <section className="rounded-lg border border-border-primary bg-surface-secondary p-4">
                <div className="grid gap-3 lg:grid-cols-[1fr_minmax(16rem,0.75fr)_auto]">
                    <label className="text-xs text-content-secondary">Dataset
                        <select className={`${INPUT_CLASS} mt-1`} value={selectedDatasetId} onChange={(event) => {
                            setSelectedDatasetId(event.target.value);
                            setSelectedRevisionId('');
                            setMembers([]);
                        }}>
                            <option value="">Select a Dataset</option>
                            {(datasetsQuery.data?.items ?? []).map((dataset) => (
                                <option key={dataset.dataset_id} value={dataset.dataset_id}>{dataset.name} · {dataset.dataset_kind} · {dataset.lifecycle_state}</option>
                            ))}
                        </select>
                    </label>
                    <label className="text-xs text-content-secondary">Lifecycle change summary
                        <input
                            className={`${INPUT_CLASS} mt-1`}
                            value={lifecycleSummary}
                            onChange={(event) => setLifecycleSummary(event.target.value)}
                            placeholder="Required archive / restore change summary"
                        />
                    </label>
                    <div className="flex items-end gap-2">
                        <button type="button" className={BUTTON_CLASS} onClick={() => datasetsQuery.refetch()}>Refresh</button>
                        {datasetQuery.data?.lifecycle_state === 'active' ? (
                            <button type="button" className={BUTTON_CLASS} disabled={!canMutate || !lifecycleSummary.trim() || lifecycleMutation.isPending} onClick={() => lifecycleMutation.mutate('archive')}>Archive</button>
                        ) : datasetQuery.data?.lifecycle_state === 'archived' ? (
                            <button type="button" className={BUTTON_CLASS} disabled={!canMutate || !lifecycleSummary.trim() || lifecycleMutation.isPending} onClick={() => lifecycleMutation.mutate('restore')}>Restore</button>
                        ) : null}
                    </div>
                </div>
                {datasetsQuery.data?.has_more && <p className="mt-2 text-xs text-warning">The bounded Dataset page has additional server rows not shown in this selector.</p>}
                {datasetQuery.data && (
                    <dl className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                        <KeyValue label="Dataset ID" value={datasetQuery.data.dataset_id} />
                        <KeyValue label="Kind" value={datasetQuery.data.dataset_kind} />
                        <KeyValue label="Lifecycle" value={datasetQuery.data.lifecycle_state} />
                        <KeyValue label="Head generation" value={datasetQuery.data.head_generation} />
                        <KeyValue label="Current revision" value={datasetQuery.data.current_revision_id} />
                    </dl>
                )}
            </section>

            {datasetQuery.data && (
                <section className="rounded-lg border border-border-primary bg-surface-secondary p-4">
                    <h3 className="text-sm font-semibold text-content-primary">Publish an immutable Dataset revision</h3>
                    <p className="mt-1 text-xs text-content-muted">Order is scientific authority: ordinal is derived from row position and cannot be typed independently.</p>
                    {selectedKind?.dataset_kind === 'ngs_molbio.molecular_construct_cohort.v1' && <div className="mt-3 rounded-md border border-border-primary bg-surface p-3">
                        <p className="text-xs font-semibold text-content-primary">Experiment reference memberships</p>
                        <p className="mt-1 text-xs text-content-muted">A Dataset can include zero or several exact Experiment-linked references. A workflow that needs one reference requires a primary-reference choice at launch.</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                            {attachedReferenceMembers.map((member: DomainStateMember) => <button
                                key={member.receipt_id}
                                type="button"
                                className={BUTTON_CLASS}
                                disabled={attachReferenceMutation.isPending || members.some((draft) => draft.metadata.display_label === member.entity_id)}
                                onClick={() => attachReferenceMutation.mutate(member)}
                            >Add {member.entity_id.slice(0, 8)} · {member.source_generation_or_revision.slice(0, 8)}</button>)}
                            {attachedReferenceMembers.length === 0 && <span className="text-xs text-content-muted">Attach references in Molecular Inputs first.</span>}
                        </div>
                    </div>}
                    <div className="mt-3 space-y-3">
                        {members.map((member, index) => (
                            <div key={`${index}-${member.receipt_id}`} className="rounded-md border border-border-primary bg-surface p-3">
                                <div className="grid gap-2 lg:grid-cols-[4rem_1fr_1fr_1fr_auto]">
                                    <div className="rounded-md border border-border-primary px-3 py-2 text-center font-mono text-xs text-content-secondary">#{index}</div>
                                    <input className={INPUT_CLASS} value={member.receipt_id} onChange={(event) => updateMember(index, (current) => ({ ...current, receipt_id: event.target.value }))} placeholder="Verified receipt ID" />
                                    <select className={INPUT_CLASS} value={member.role} onChange={(event) => updateMember(index, (current) => ({ ...current, role: event.target.value }))}>
                                        <option value="">Select registry role</option>
                                        {member.role && !roleOptions.some((option) => option.role === member.role) && <option value={member.role}>{member.role} · unavailable</option>}
                                        {roleOptions.map((option) => <option key={option.role} value={option.role}>{option.role} · {option.receiptKind}</option>)}
                                    </select>
                                    <input className={INPUT_CLASS} value={member.media_type ?? ''} onChange={(event) => updateMember(index, (current) => ({ ...current, media_type: event.target.value.trim() || null }))} placeholder="Media type (optional)" />
                                    <div className="flex gap-1">
                                        <button type="button" className={BUTTON_CLASS} disabled={index === 0} onClick={() => moveMember(index, -1)} aria-label={`Move member ${index + 1} up`}>↑</button>
                                        <button type="button" className={BUTTON_CLASS} disabled={index === members.length - 1} onClick={() => moveMember(index, 1)} aria-label={`Move member ${index + 1} down`}>↓</button>
                                        <button type="button" className={BUTTON_CLASS} onClick={() => setMembers((current) => current.filter((_, memberIndex) => memberIndex !== index))}>Remove</button>
                                    </div>
                                </div>
                                <div className="mt-2 grid gap-2 md:grid-cols-4">
                                    <input className={INPUT_CLASS} value={member.metadata.display_label ?? ''} onChange={(event) => updateMember(index, (current) => ({ ...current, metadata: { ...current.metadata, display_label: event.target.value || null } }))} placeholder="Display label" />
                                    <input className={INPUT_CLASS} value={member.metadata.group_label ?? ''} onChange={(event) => updateMember(index, (current) => ({ ...current, metadata: { ...current.metadata, group_label: event.target.value || null } }))} placeholder="Group label" />
                                    <input className={INPUT_CLASS} value={member.metadata.condition_label ?? ''} onChange={(event) => updateMember(index, (current) => ({ ...current, metadata: { ...current.metadata, condition_label: event.target.value || null } }))} placeholder="Condition label" />
                                    <input className={INPUT_CLASS} value={member.metadata.tags.join(', ')} onChange={(event) => updateMember(index, (current) => ({ ...current, metadata: { ...current.metadata, tags: normalizedTags(event.target.value) } }))} placeholder="Tags, comma separated" />
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="mt-3 grid gap-2 md:grid-cols-[auto_1fr_auto]">
                        <button type="button" className={BUTTON_CLASS} disabled={!roleOptions.length || members.length >= (selectedKind?.maximum_members ?? 0)} onClick={() => setMembers((current) => [...current, emptyMember(roleOptions[0]?.role)])}>Add typed member</button>
                        <input className={INPUT_CLASS} value={revisionSummary} onChange={(event) => setRevisionSummary(event.target.value)} placeholder="Revision change summary" />
                        <button
                            type="button"
                            className={PRIMARY_BUTTON_CLASS}
                            disabled={!canMutate || datasetQuery.data.lifecycle_state !== 'active' || !revisionSummary.trim() || !memberDraftValid || members.length < (selectedKind?.minimum_members ?? 0) || reviseMutation.isPending}
                            onClick={() => reviseMutation.mutate()}
                        >Publish immutable revision</button>
                    </div>
                </section>
            )}

            {selectedDatasetId && (
                <section className="rounded-lg border border-border-primary bg-surface-secondary p-4">
                    <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                        <label className="text-xs text-content-secondary">Exact immutable revision
                            <select className={`${INPUT_CLASS} mt-1`} value={selectedRevisionId} onChange={(event) => setSelectedRevisionId(event.target.value)}>
                                <option value="">Select an immutable revision</option>
                                {(revisionsQuery.data?.items ?? []).map((revision) => (
                                    <option key={revision.revision_id} value={revision.revision_id}>Revision {revision.revision_number} · {revision.revision_id}</option>
                                ))}
                            </select>
                        </label>
                        <div className="flex items-end gap-2">
                            <button
                                type="button"
                                className={revisionSelectedForPreparation ? PRIMARY_BUTTON_CLASS : BUTTON_CLASS}
                                disabled={!selectedRevisionId}
                                onClick={() => onSelectedRevisionIdsChange(revisionSelectedForPreparation
                                    ? selectedRevisionIds.filter((revisionId) => revisionId !== selectedRevisionId)
                                    : [...selectedRevisionIds, selectedRevisionId])}
                            >{revisionSelectedForPreparation ? 'Selected for preparation' : 'Use for preparation'}</button>
                            <button type="button" className={BUTTON_CLASS} disabled={!exactMembers.length} onClick={() => setMembers(exactMembers.map(editableMember))}>Copy members into new draft</button>
                        </div>
                    </div>
                    {revisionQuery.data && (
                        <>
                            <dl className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                                <KeyValue label="Revision ID" value={revisionQuery.data.revision_id} />
                                <KeyValue label="Revision number" value={revisionQuery.data.revision_number} />
                                <KeyValue label="Parent revision" value={revisionQuery.data.parent_revision_id} />
                                <KeyValue label="Revision digest" value={revisionQuery.data.revision_sha256} />
                                <KeyValue label="Member count" value={revisionQuery.data.member_count} />
                            </dl>
                            <div className="mt-3 space-y-2">
                                {exactMembers.map((member) => (
                                    <div key={`${member.ordinal}:${member.receipt_id}:${member.role}`} className="grid gap-2 rounded-md border border-border-primary bg-surface p-3 md:grid-cols-4">
                                        <KeyValue label="Ordinal" value={member.ordinal} />
                                        <KeyValue label="Receipt" value={member.receipt_id} />
                                        <KeyValue label="Role" value={member.role} />
                                        <KeyValue label="Native revision / generation" value={member.native_revision_or_generation} />
                                    </div>
                                ))}
                            </div>
                            {(revisionQuery.data.members_uri && pagedMembersQuery.data?.has_more) && (
                                <p className="mt-2 text-xs text-warning">Only the first bounded member page is displayed; the immutable revision contains additional members.</p>
                            )}
                        </>
                    )}
                </section>
            )}
        </div>
    );
}
