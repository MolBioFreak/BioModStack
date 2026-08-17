import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    fetchMolBioNgsStateRevision,
    fetchMolBioSequenceRevisions,
    fetchNucleotideSequences,
    issueMolecularRevisionMemberReceipt,
    saveMolBioNgsStateRevision,
    type DomainStateMember,
    type DomainStateRevisionPayload,
    type MolBioSequenceRevision,
    type NucleotideSequenceListItem,
} from '../../lib/api';

interface ExperimentReferenceLibraryProps {
    domainExperimentId: string;
    globalDomainExperimentRevisionId: string;
    currentStateRevisionId: string | null;
    stateHeadGeneration: number;
    canMutate: boolean;
    mutationBlocker: string | null;
}

interface ReferenceDraft {
    sequenceId: string;
    revisionId: string;
    label: string;
    revisionNumber: number;
    digest: string;
    topology: string;
}

const INPUT = 'w-full rounded-md border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary disabled:cursor-not-allowed disabled:opacity-40';
const BUTTON = 'rounded-md border border-border-primary bg-surface px-3 py-2 text-xs font-semibold text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';
const PRIMARY = 'rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';

function defaultStatePayload(): DomainStateRevisionPayload {
    return {
        schema: 'bms.molbio-ngs.domain-state-revision.v1',
        design: {
            sample_revision_ids: [],
            conditions: [],
            replicates: [],
            expected_molecule_roles: ['molecular_expected_construct'],
        },
        reference_policy: {
            required_roles: ['molecular_expected_construct'],
            coordinate_policy: 'exact_revision',
        },
        acquisition_policy: {
            platform: 'ont',
            required_terminal_manifest: true,
        },
        analysis_policy: {
            allowed_workflow_ids: ['ont_plasmid_qc', 'ont_construct_screening', 'ont_fastq_qc'],
            required_manifest_schemas: ['biomodstack.construct_verification.v2'],
        },
        assessment_policy: {
            rule_id: 'server-owned-rule',
            completion_is_scientific_pass: false,
        },
        notes: 'Shared MolBio and NGS reference-library membership.',
    };
}

function receiptIdentity(member: DomainStateMember): string | null {
    if (member.entity_kind !== 'molecular_revision') return null;
    const destination = member.reopen_destination;
    if (!destination || typeof destination !== 'object' || Array.isArray(destination)) return null;
    const params = (destination as { params?: unknown }).params;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
    const sequenceId = (params as Record<string, unknown>).sequence_id;
    const revisionId = (params as Record<string, unknown>).revision_id;
    return typeof sequenceId === 'string' && typeof revisionId === 'string'
        ? `${sequenceId}:${revisionId}`
        : null;
}

export default function ExperimentReferenceLibrary({
    domainExperimentId,
    globalDomainExperimentRevisionId,
    currentStateRevisionId,
    stateHeadGeneration,
    canMutate,
    mutationBlocker,
}: ExperimentReferenceLibraryProps) {
    const queryClient = useQueryClient();
    const [selectedSequenceId, setSelectedSequenceId] = useState('');
    const [selectedRevisionId, setSelectedRevisionId] = useState('');
    const [librarySearch, setLibrarySearch] = useState('');
    const [drafts, setDrafts] = useState<ReferenceDraft[]>([]);
    const [notice, setNotice] = useState<string | null>(null);

    const sequencesQuery = useQuery<NucleotideSequenceListItem[]>({
        queryKey: ['shared-molecular-reference-library', librarySearch],
        queryFn: async () => (await fetchNucleotideSequences({
            limit: 100,
            search: librarySearch.trim() || undefined,
            sort_by: 'name',
            sort_desc: false,
        })).data,
        retry: false,
    });
    const revisionsQuery = useQuery<MolBioSequenceRevision[]>({
        queryKey: ['shared-molecular-reference-revisions', selectedSequenceId],
        queryFn: async () => (await fetchMolBioSequenceRevisions(selectedSequenceId)).data,
        enabled: Boolean(selectedSequenceId),
        retry: false,
    });
    const currentRevisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', domainExperimentId, currentStateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(domainExperimentId, currentStateRevisionId as string),
        enabled: Boolean(currentStateRevisionId),
        retry: false,
    });

    const sequences = sequencesQuery.data ?? [];
    const revisions = revisionsQuery.data ?? [];
    const selectedSequence = sequences.find((item) => item.id === selectedSequenceId) ?? null;
    const selectedRevision = revisions.find((item) => item.id === selectedRevisionId) ?? null;
    const existingIdentities = useMemo(() => new Set(
        (currentRevisionQuery.data?.members ?? []).map(receiptIdentity).filter((value): value is string => Boolean(value)),
    ), [currentRevisionQuery.data?.members]);

    const addDraft = () => {
        if (!selectedSequence || !selectedRevision) {
            setNotice('Select a reference sequence and one exact immutable revision.');
            return;
        }
        const identity = `${selectedSequence.id}:${selectedRevision.id}`;
        if (existingIdentities.has(identity) || drafts.some((draft) => `${draft.sequenceId}:${draft.revisionId}` === identity)) {
            setNotice('That exact reference revision is already attached or queued.');
            return;
        }
        setDrafts((current) => [...current, {
            sequenceId: selectedSequence.id,
            revisionId: selectedRevision.id,
            label: selectedSequence.name,
            revisionNumber: selectedRevision.revision_number,
            digest: selectedRevision.content_sha256,
            topology: selectedRevision.topology,
        }]);
        setSelectedSequenceId('');
        setSelectedRevisionId('');
        setNotice(null);
    };

    const attachMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker || 'This Experiment is read-only.');
            if (!drafts.length) throw new Error('Add one or more reference revisions first.');
            const currentRevision = currentRevisionQuery.data ?? null;
            if (currentStateRevisionId && !currentRevision) {
                throw new Error('The current scientific-state revision could not be loaded.');
            }
            const issued = [];
            for (const draft of drafts) {
                issued.push(await issueMolecularRevisionMemberReceipt({
                    sequence_id: draft.sequenceId,
                    revision_id: draft.revisionId,
                }));
            }
            const existingMembers = currentRevision?.members ?? [];
            const members = [
                ...existingMembers.map((member, ordinal) => ({
                    receipt_id: member.receipt_id,
                    role: member.role,
                    ordinal,
                    sample_revision_id: member.sample_revision_id,
                })),
                ...issued.map((receipt, index) => ({
                    receipt_id: receipt.receipt_id,
                    role: 'molecular_expected_construct' as const,
                    ordinal: existingMembers.length + index,
                    sample_revision_id: null,
                })),
            ];
            const payload = currentRevision?.payload ?? defaultStatePayload();
            const expectedRoles = new Set(payload.design.expected_molecule_roles);
            expectedRoles.add('molecular_expected_construct');
            const requiredRoles = new Set(payload.reference_policy.required_roles);
            requiredRoles.add('molecular_expected_construct');
            return saveMolBioNgsStateRevision(domainExperimentId, {
                global_domain_experiment_revision_id: globalDomainExperimentRevisionId,
                expected_head_generation: stateHeadGeneration,
                parent_revision_id: currentStateRevisionId,
                idempotency_key: crypto.randomUUID(),
                payload: {
                    ...payload,
                    design: { ...payload.design, expected_molecule_roles: [...expectedRoles] },
                    reference_policy: { ...payload.reference_policy, required_roles: [...requiredRoles] },
                },
                members,
            });
        },
        onSuccess: async (revision) => {
            setDrafts([]);
            setNotice(`Attached ${drafts.length} exact reference revision(s). Published scientific-state revision ${revision.revision_number}.`);
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['molbio-ngs-domain-state', domainExperimentId] }),
                queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revisions', domainExperimentId] }),
                queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revision', domainExperimentId] }),
                queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-hierarchy'] }),
            ]);
        },
        onError: (error: unknown) => setNotice(error instanceof Error ? error.message : 'Reference attachment failed.'),
    });

    const loadError = sequencesQuery.error ?? revisionsQuery.error ?? currentRevisionQuery.error;

    return (
        <section className="mb-4 rounded-lg border border-border-primary bg-surface-secondary p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-content-primary">Shared MolBio and NGS reference library</h3>
                    <p className="mt-1 max-w-4xl text-xs text-content-muted">
                        The molecular sequence viewer owns the shared catalogue. Attach several exact immutable revisions to this Experiment. MolBio reopens those revisions directly, while NGS receives runtime FASTA only through server-issued receipts.
                    </p>
                </div>
                <a className={BUTTON} href="/designer?section=molecular-inputs">Open molecular viewer and import references</a>
            </div>
            {!canMutate && <p className="mt-3 text-xs text-warning">{mutationBlocker}</p>}
            {loadError && <p role="alert" className="mt-3 text-xs text-error">Reference library data could not be loaded.</p>}
            <label className="mt-4 block text-xs text-content-secondary">Search the shared reference library
                <input className={`${INPUT} mt-1`} value={librarySearch} onChange={(event) => setLibrarySearch(event.target.value)} placeholder="Name, accession, organism, or description" />
            </label>
            <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(12rem,1fr)_minmax(14rem,1fr)_auto]">
                <label className="text-xs text-content-secondary">Available reference sequence
                    <select className={`${INPUT} mt-1`} value={selectedSequenceId} onChange={(event) => {
                        setSelectedSequenceId(event.target.value);
                        setSelectedRevisionId('');
                    }}>
                        <option value="">Select from shared library</option>
                        {sequences.map((sequence) => <option key={sequence.id} value={sequence.id}>{sequence.name}</option>)}
                    </select>
                </label>
                <label className="text-xs text-content-secondary">Exact immutable revision
                    <select className={`${INPUT} mt-1`} value={selectedRevisionId} onChange={(event) => setSelectedRevisionId(event.target.value)} disabled={!selectedSequenceId || revisionsQuery.isLoading}>
                        <option value="">Select revision</option>
                        {revisions.map((revision) => (
                            <option key={revision.id} value={revision.id}>r{revision.revision_number} · {revision.topology} · {revision.content_sha256.slice(0, 12)}</option>
                        ))}
                    </select>
                </label>
                <div className="flex items-end"><button type="button" className={BUTTON} onClick={addDraft} disabled={!selectedRevision}>Add reference</button></div>
            </div>
            {drafts.length > 0 && (
                <div className="mt-3 space-y-2">
                    {drafts.map((draft) => (
                        <div key={`${draft.sequenceId}:${draft.revisionId}`} className="grid gap-2 rounded-md border border-border-primary bg-surface p-3 md:grid-cols-[1fr_auto]">
                            <div>
                                <p className="text-sm font-semibold text-content-primary">{draft.label} · revision {draft.revisionNumber}</p>
                                <p className="mt-1 break-all font-mono text-xs text-content-muted">{draft.revisionId} · {draft.topology} · {draft.digest}</p>
                            </div>
                            <button type="button" className={BUTTON} onClick={() => setDrafts((current) => current.filter((item) => item.revisionId !== draft.revisionId))}>Remove</button>
                        </div>
                    ))}
                </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" className={PRIMARY} disabled={!canMutate || !drafts.length || attachMutation.isPending} onClick={() => attachMutation.mutate()}>
                    {attachMutation.isPending ? 'Attaching references…' : `Attach ${drafts.length || ''} reference${drafts.length === 1 ? '' : 's'} to Experiment`}
                </button>
                {notice && <p role="status" className="text-xs text-content-secondary">{notice}</p>}
            </div>
        </section>
    );
}
