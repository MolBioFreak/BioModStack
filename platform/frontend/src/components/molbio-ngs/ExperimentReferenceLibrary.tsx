import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    commitMolBioSequenceImport,
    fetchMolBioNgsStateRevision,
    fetchMolBioSequenceRevisions,
    fetchNcbiSequenceArtifact,
    fetchNucleotideSequences,
    issueMolecularRevisionMemberReceipt,
    previewMolBioSequenceImport,
    saveMolBioNgsStateRevision,
    type DomainStateMember,
    type DomainStateRevisionPayload,
    type MolBioSequenceImportPayload,
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

type EntryMode = 'library' | 'upload' | 'paste' | 'ncbi';

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
        acquisition_policy: { platform: 'ont', required_terminal_manifest: true },
        analysis_policy: {
            allowed_workflow_ids: ['ont_plasmid_qc', 'ont_construct_screening', 'ont_fastq_qc'],
            required_manifest_schemas: ['biomodstack.construct_verification.v2'],
        },
        assessment_policy: { rule_id: 'server-owned-rule', completion_is_scientific_pass: false },
        notes: 'Shared MolBio and NGS reference-library membership.',
    };
}

function receiptIdentity(member: DomainStateMember): string | null {
    if (member.entity_kind !== 'molecular_revision') return null;
    const params = member.reopen_destination && typeof member.reopen_destination === 'object' && !Array.isArray(member.reopen_destination)
        ? (member.reopen_destination as { params?: unknown }).params
        : null;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
    const sequenceId = (params as Record<string, unknown>).sequence_id;
    const revisionId = (params as Record<string, unknown>).revision_id;
    return typeof sequenceId === 'string' && typeof revisionId === 'string' ? `${sequenceId}:${revisionId}` : null;
}

function referenceFromMember(member: DomainStateMember) {
    const identity = receiptIdentity(member);
    if (!identity) return null;
    const [sequenceId, revisionId] = identity.split(':');
    return { sequenceId, revisionId, receiptId: member.receipt_id };
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
    const [entryMode, setEntryMode] = useState<EntryMode>('library');
    const [selectedSequenceId, setSelectedSequenceId] = useState('');
    const [selectedRevisionId, setSelectedRevisionId] = useState('');
    const [librarySearch, setLibrarySearch] = useState('');
    const [drafts, setDrafts] = useState<ReferenceDraft[]>([]);
    const [sourceText, setSourceText] = useState('');
    const [rawName, setRawName] = useState('');
    const [ncbiAccession, setNcbiAccession] = useState('');
    const [topology, setTopology] = useState<'circular' | 'linear'>('circular');
    const [uploadName, setUploadName] = useState('');
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
    const currentMembers = currentRevisionQuery.data?.members ?? [];
    const attachedReferences = currentMembers.map(referenceFromMember).filter((value): value is NonNullable<typeof value> => Boolean(value));
    const existingIdentities = useMemo(() => new Set(
        currentMembers.map(receiptIdentity).filter((value): value is string => Boolean(value)),
    ), [currentMembers]);

    const publishReferences = async (references: ReferenceDraft[]) => {
        if (!canMutate) throw new Error(mutationBlocker || 'This Experiment is read-only.');
        if (!references.length) throw new Error('Add one or more exact reference revisions first.');
        const currentRevision = currentRevisionQuery.data ?? null;
        if (currentStateRevisionId && !currentRevision) throw new Error('The current scientific-state revision could not be loaded.');
        const unique = references.filter((reference, index, all) => {
            const identity = `${reference.sequenceId}:${reference.revisionId}`;
            return !existingIdentities.has(identity) && all.findIndex((item) => `${item.sequenceId}:${item.revisionId}` === identity) === index;
        });
        if (!unique.length) throw new Error('Every selected reference revision is already attached.');
        const issued = [];
        for (const reference of unique) {
            issued.push(await issueMolecularRevisionMemberReceipt({ sequence_id: reference.sequenceId, revision_id: reference.revisionId }));
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
        const revision = await saveMolBioNgsStateRevision(domainExperimentId, {
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
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-domain-state', domainExperimentId] }),
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revisions', domainExperimentId] }),
            queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revision', domainExperimentId] }),
            queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-hierarchy'] }),
            queryClient.invalidateQueries({ queryKey: ['shared-molecular-reference-library'] }),
        ]);
        return { revision, count: unique.length };
    };

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
        mutationFn: () => publishReferences(drafts),
        onSuccess: ({ revision, count }) => {
            setDrafts([]);
            setNotice(`Attached ${count} exact reference revision(s). Published scientific-state revision ${revision.revision_number}.`);
        },
        onError: (error: unknown) => setNotice(error instanceof Error ? error.message : 'Reference attachment failed.'),
    });

    const importMutation = useMutation({
        mutationFn: async () => {
            let payload: MolBioSequenceImportPayload;
            if (entryMode === 'ncbi') {
                const accession = ncbiAccession.trim().toUpperCase();
                if (!accession) throw new Error('Enter an NCBI accession.');
                const artifact = (await fetchNcbiSequenceArtifact(accession)).data;
                payload = {
                    source_format: 'genbank',
                    source_text: artifact.content,
                    topology_default: topology,
                    idempotency_key: crypto.randomUUID(),
                    origin_surface: 'ngs',
                    source_provider: 'ncbi',
                    source_id: artifact.source.source_id,
                };
            } else if (entryMode === 'paste') {
                if (!sourceText.trim()) throw new Error('Paste FASTA or a raw nucleotide sequence.');
                const isFasta = sourceText.trimStart().startsWith('>');
                payload = isFasta ? {
                    source_format: 'fasta', source_text: sourceText, topology_default: topology,
                    idempotency_key: crypto.randomUUID(), origin_surface: 'ngs', source_provider: 'paste', source_id: rawName.trim() || 'pasted-reference',
                } : {
                    source_format: 'raw_dna', raw_rows: [{ name: rawName.trim() || 'NGS reference', sequence: sourceText, topology }], topology_default: topology,
                    idempotency_key: crypto.randomUUID(), origin_surface: 'ngs', source_provider: 'paste', source_id: rawName.trim() || 'pasted-reference',
                };
            } else if (entryMode === 'upload') {
                if (!sourceText.trim() || !uploadName) throw new Error('Choose a FASTA or GenBank file.');
                const isGenBank = /\.(gb|gbk|genbank)$/i.test(uploadName);
                payload = {
                    source_format: isGenBank ? 'genbank' : 'fasta', source_text: sourceText, topology_default: topology,
                    idempotency_key: crypto.randomUUID(), origin_surface: 'ngs', source_provider: 'upload', source_id: uploadName,
                };
            } else {
                throw new Error('Select, upload, paste, or retrieve a reference first.');
            }
            const preview = (await previewMolBioSequenceImport(payload)).data;
            if (!preview.valid) throw new Error(preview.errors.map((error) => error.message).join(' · ') || 'Reference import preview failed.');
            const committed = (await commitMolBioSequenceImport(payload)).data;
            const references: ReferenceDraft[] = committed.records.map((record) => {
                if (!record.revision_id) throw new Error(`Imported record ${record.name} has no immutable revision.`);
                const previewRecord = preview.records.find((item) => item.name === record.name) ?? preview.records[0];
                return {
                    sequenceId: record.sequence_id,
                    revisionId: record.revision_id,
                    label: record.name,
                    revisionNumber: 1,
                    digest: previewRecord?.content_sha256 ?? '',
                    topology: previewRecord?.topology ?? topology,
                };
            });
            const attached = await publishReferences(references);
            return { ...attached, records: committed.records };
        },
        onSuccess: ({ revision, count, records }) => {
            const reused = records.filter((record) => record.reused_existing_revision).length;
            setSourceText('');
            setUploadName('');
            setRawName('');
            setNcbiAccession('');
            setNotice(`Saved and attached ${count} reference revision(s) in state revision ${revision.revision_number}. ${reused ? `${reused} existing exact revision(s) were reused with new NGS import provenance.` : 'New immutable molecular revisions were created.'}`);
        },
        onError: (error: unknown) => setNotice(error instanceof Error ? error.message : 'Reference import failed.'),
    });

    const loadError = sequencesQuery.error ?? revisionsQuery.error ?? currentRevisionQuery.error;
    const importing = importMutation.isPending;

    return (
        <section className="mb-4 rounded-lg border border-border-primary bg-surface-secondary p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-content-primary">Reference sequence entry</h3>
                    <p className="mt-1 max-w-4xl text-xs text-content-muted">
                        One shared molecular library owns every reference. Add an existing exact revision, upload FASTA or GenBank, paste sequence text, or retrieve an NCBI accession. Imports reuse matching normalized sequence bytes and preserve the new NGS source as operation provenance.
                    </p>
                </div>
                <a className={BUTTON} href="/designer?section=molecular-inputs">Open full molecular viewer</a>
            </div>
            {!canMutate && <p className="mt-3 text-xs text-warning">{mutationBlocker}</p>}
            {loadError && <p role="alert" className="mt-3 text-xs text-error">Reference library data could not be loaded.</p>}

            <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="Reference entry method">
                {([
                    ['library', 'Existing library'], ['upload', 'Upload file'], ['paste', 'Paste sequence'], ['ncbi', 'NCBI accession'],
                ] as Array<[EntryMode, string]>).map(([value, label]) => (
                    <button key={value} type="button" className={`${BUTTON} ${entryMode === value ? 'border-primary bg-primary/10' : ''}`} onClick={() => { setEntryMode(value); setNotice(null); }}>{label}</button>
                ))}
            </div>

            {entryMode === 'library' ? (
                <div className="mt-3 rounded-md border border-border-primary bg-surface p-3">
                    <label className="block text-xs text-content-secondary">Search the shared reference library
                        <input className={`${INPUT} mt-1`} value={librarySearch} onChange={(event) => setLibrarySearch(event.target.value)} placeholder="Name, accession, organism, or description" />
                    </label>
                    <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(12rem,1fr)_minmax(14rem,1fr)_auto]">
                        <label className="text-xs text-content-secondary">Reference sequence
                            <select className={`${INPUT} mt-1`} value={selectedSequenceId} onChange={(event) => { setSelectedSequenceId(event.target.value); setSelectedRevisionId(''); }}>
                                <option value="">Select from shared library</option>
                                {sequences.map((sequence) => <option key={sequence.id} value={sequence.id}>{sequence.name}</option>)}
                            </select>
                        </label>
                        <label className="text-xs text-content-secondary">Exact immutable revision
                            <select className={`${INPUT} mt-1`} value={selectedRevisionId} onChange={(event) => setSelectedRevisionId(event.target.value)} disabled={!selectedSequenceId || revisionsQuery.isLoading}>
                                <option value="">Select revision</option>
                                {revisions.map((revision) => <option key={revision.id} value={revision.id}>r{revision.revision_number} · {revision.topology} · {revision.content_sha256.slice(0, 12)}</option>)}
                            </select>
                        </label>
                        <div className="flex items-end"><button type="button" className={BUTTON} onClick={addDraft} disabled={!selectedRevision}>Queue exact revision</button></div>
                    </div>
                </div>
            ) : (
                <div className="mt-3 rounded-md border border-border-primary bg-surface p-3">
                    <div className="grid gap-3 md:grid-cols-[1fr_10rem]">
                        <div>
                            {entryMode === 'upload' && <label className="text-xs text-content-secondary">FASTA or GenBank file
                                <input className={`${INPUT} mt-1`} type="file" accept=".fasta,.fa,.fna,.gb,.gbk,.genbank" onChange={async (event) => {
                                    const file = event.target.files?.[0];
                                    setUploadName(file?.name ?? '');
                                    setSourceText(file ? await file.text() : '');
                                }} />
                            </label>}
                            {entryMode === 'paste' && <>
                                <label className="text-xs text-content-secondary">Reference name<input className={`${INPUT} mt-1`} value={rawName} onChange={(event) => setRawName(event.target.value)} placeholder="Reference name" /></label>
                                <label className="mt-3 block text-xs text-content-secondary">FASTA or raw nucleotide sequence<textarea className={`${INPUT} mt-1 min-h-32 font-mono`} value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder=">reference\nATGC… or ATGC…" /></label>
                            </>}
                            {entryMode === 'ncbi' && <label className="text-xs text-content-secondary">Versioned NCBI accession<input className={`${INPUT} mt-1 font-mono`} value={ncbiAccession} onChange={(event) => setNcbiAccession(event.target.value)} placeholder="J01749.1" /></label>}
                        </div>
                        <label className="text-xs text-content-secondary">Topology<select className={`${INPUT} mt-1`} value={topology} onChange={(event) => setTopology(event.target.value as typeof topology)}><option value="circular">Circular</option><option value="linear">Linear</option></select></label>
                    </div>
                    <button type="button" className={`${PRIMARY} mt-3`} disabled={!canMutate || importing} onClick={() => importMutation.mutate()}>{importing ? 'Saving and attaching…' : 'Save to shared library and attach'}</button>
                </div>
            )}

            {drafts.length > 0 && <div className="mt-3 space-y-2">
                {drafts.map((draft) => <div key={`${draft.sequenceId}:${draft.revisionId}`} className="grid gap-2 rounded-md border border-border-primary bg-surface p-3 md:grid-cols-[1fr_auto]">
                    <div><p className="text-sm font-semibold text-content-primary">{draft.label} · revision {draft.revisionNumber}</p><p className="mt-1 break-all font-mono text-xs text-content-muted">{draft.revisionId} · {draft.topology} · {draft.digest}</p></div>
                    <button type="button" className={BUTTON} onClick={() => setDrafts((current) => current.filter((item) => item.revisionId !== draft.revisionId))}>Remove</button>
                </div>)}
                <button type="button" className={PRIMARY} disabled={!canMutate || attachMutation.isPending} onClick={() => attachMutation.mutate()}>{attachMutation.isPending ? 'Attaching references…' : `Attach ${drafts.length} queued reference${drafts.length === 1 ? '' : 's'}`}</button>
            </div>}

            <div className="mt-4 rounded-md border border-border-primary bg-surface p-3">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-content-muted">Attached exact references</h4>
                {attachedReferences.length === 0 ? <p className="mt-2 text-xs text-content-muted">No reference revision is attached to this Experiment.</p> : <div className="mt-2 flex flex-wrap gap-2">
                    {attachedReferences.map((reference) => <a key={reference.receiptId} className={BUTTON} href={`/designer?molbio_sequence_id=${encodeURIComponent(reference.sequenceId)}&molbio_revision_id=${encodeURIComponent(reference.revisionId)}`}>Open {reference.sequenceId.slice(0, 8)} · {reference.revisionId.slice(0, 8)}</a>)}
                </div>}
            </div>
            {notice && <p role="status" className="mt-3 text-xs text-content-secondary">{notice}</p>}
        </section>
    );
}
