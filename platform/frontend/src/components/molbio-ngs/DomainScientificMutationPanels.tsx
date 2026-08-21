import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    archiveMolBioNgsReference,
    assessMolBioNgsEvidence,
    attachMolBioNgsInstrumentRunEvidence,
    attachMolBioNgsJobEvidence,
    createMolBioNgsReference,
    createMolBioNgsReferenceRevision,
    createMolBioNgsSample,
    createMolBioNgsSampleRevision,
    fetchMolBioNgsSampleRevision,
    importMolBioNgsBrowserReference,
    type DomainReference,
    type DomainReferenceRevision,
    type DomainSample,
    type DomainSampleRevision,
    type DomainStateMember,
    type DomainStateRevision,
    type EvidenceAssessmentRequest,
    type SampleRevisionPayload,
} from '../../lib/api';

const INPUT = 'w-full rounded-md border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary disabled:cursor-not-allowed disabled:opacity-40';
const BUTTON = 'rounded-md border border-border-primary bg-surface px-3 py-2 text-xs font-semibold text-content-primary hover:border-primary/60 disabled:cursor-not-allowed disabled:opacity-40';
const PRIMARY = 'rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';
const DANGER = 'rounded-md border border-error/50 bg-error/10 px-3 py-2 text-xs font-semibold text-error hover:bg-error/20 disabled:cursor-not-allowed disabled:opacity-40';

type SampleRow = { sample: DomainSample; revision?: DomainSampleRevision };
type ReferenceRow = { reference: DomainReference; revision?: DomainReferenceRevision };

type SampleForm = {
    name: string;
    description: string;
    sample_kind: string;
    organism: string;
    strain: string;
    external_ids: string;
    method: string;
    batch_id: string;
    prepared_at: string;
    container_label: string;
    barcode: string;
    minknow_sample_id: string;
    notes: string;
};

const EMPTY_SAMPLE: SampleForm = {
    name: '',
    description: '',
    sample_kind: 'biological_sample',
    organism: '',
    strain: '',
    external_ids: '',
    method: 'operator-entered',
    batch_id: '',
    prepared_at: '',
    container_label: '',
    barcode: '',
    minknow_sample_id: '',
    notes: '',
};

function Field({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
    return <label className={wide ? 'text-xs text-content-secondary md:col-span-2' : 'text-xs text-content-secondary'}>{label}{children}</label>;
}

function Notice({ message, error = false }: { message: string | null; error?: boolean }) {
    if (!message) return null;
    return <p role={error ? 'alert' : undefined} className={`mt-3 rounded-md border px-3 py-2 text-xs ${error ? 'border-error/40 bg-error/10 text-error' : 'border-info/40 bg-info/10 text-info'}`}>{message}</p>;
}

function MutationBox({ title, blocker, children }: { title: string; blocker: string | null; children: React.ReactNode }) {
    return (
        <div className="mb-4 rounded-lg border border-border-primary bg-surface p-3">
            <h4 className="text-sm font-semibold text-content">{title}</h4>
            {blocker && <p className="mt-2 text-xs text-warning">{blocker}</p>}
            {children}
        </div>
    );
}

function samplePayload(form: SampleForm): SampleRevisionPayload {
    return {
        schema: 'bms.molbio-ngs.sample-revision.v1',
        name: form.name.trim(),
        description: form.description.trim(),
        sample_kind: form.sample_kind.trim(),
        source: {
            organism: form.organism.trim() || null,
            strain: form.strain.trim() || null,
            external_ids: form.external_ids.split(',').map((value) => value.trim()).filter(Boolean),
        },
        preparation: {
            method: form.method.trim(),
            batch_id: form.batch_id.trim() || null,
            prepared_at: form.prepared_at.trim() || null,
        },
        labels: {
            container_label: form.container_label.trim() || null,
            barcode: form.barcode.trim() || null,
            minknow_sample_id: form.minknow_sample_id.trim() || null,
        },
        notes: form.notes.trim(),
    };
}

function formFromSampleRevision(revision: DomainSampleRevision): SampleForm {
    const payload = revision.payload;
    return {
        name: payload.name,
        description: payload.description,
        sample_kind: payload.sample_kind,
        organism: payload.source.organism ?? '',
        strain: payload.source.strain ?? '',
        external_ids: payload.source.external_ids.join(', '),
        method: payload.preparation.method,
        batch_id: payload.preparation.batch_id ?? '',
        prepared_at: payload.preparation.prepared_at ?? '',
        container_label: payload.labels.container_label ?? '',
        barcode: payload.labels.barcode ?? '',
        minknow_sample_id: payload.labels.minknow_sample_id ?? '',
        notes: payload.notes,
    };
}

export function DomainSampleMutationPanel({
    domainExperimentId,
    canMutate,
    mutationBlocker,
    rows,
}: {
    domainExperimentId: string;
    canMutate: boolean;
    mutationBlocker: string | null;
    rows: SampleRow[];
}) {
    const queryClient = useQueryClient();
    const [mode, setMode] = useState<'create' | 'revise'>('create');
    const [selectedSampleId, setSelectedSampleId] = useState('');
    const [form, setForm] = useState<SampleForm>(EMPTY_SAMPLE);
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const selectedSample = rows.find(({ sample }) => sample.id === selectedSampleId)?.sample;
    const selectedRevisionQuery = useQuery({
        queryKey: ['molbio-ngs-sample-revision-editor', domainExperimentId, selectedSampleId, selectedSample?.current_revision_id],
        queryFn: () => fetchMolBioNgsSampleRevision(domainExperimentId, selectedSampleId, selectedSample?.current_revision_id as string),
        enabled: mode === 'revise' && Boolean(selectedSampleId && selectedSample?.current_revision_id),
        retry: false,
    });

    useEffect(() => {
        if (mode === 'revise' && selectedRevisionQuery.data) setForm(formFromSampleRevision(selectedRevisionQuery.data));
    }, [mode, selectedRevisionQuery.data]);

    const update = <K extends keyof SampleForm>(key: K, value: SampleForm[K]) => setForm((current) => ({ ...current, [key]: value }));
    const saveMutation = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker || 'This Domain is read-only.');
            const payload = samplePayload(form);
            if (mode === 'create') {
                return createMolBioNgsSample(domainExperimentId, { payload, idempotency_key: crypto.randomUUID() });
            }
            if (!selectedSample || !selectedRevisionQuery.data) throw new Error('Select a sample with an exact current revision.');
            return createMolBioNgsSampleRevision(domainExperimentId, selectedSample.id, {
                payload,
                expected_head_generation: selectedSample.head_generation,
                parent_revision_id: selectedRevisionQuery.data.id,
                idempotency_key: crypto.randomUUID(),
            });
        },
        onSuccess: (revision) => {
            setNotice(`${mode === 'create' ? 'Created sample' : 'Published sample revision'} ${revision.id}.`);
            setError(null);
            setForm(EMPTY_SAMPLE);
            setSelectedSampleId('');
            setMode('create');
            void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-samples', domainExperimentId] });
            void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revisions', domainExperimentId] });
            void queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-hierarchy'] });
        },
        onError: (value: unknown) => {
            setError(value instanceof Error ? value.message : 'Sample mutation failed.');
            setNotice(null);
        },
    });

    return (
        <MutationBox title="Create or revise a managed sample" blocker={!canMutate ? mutationBlocker : null}>
            <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Sample mutation mode">
                <button type="button" className={`${BUTTON} ${mode === 'create' ? 'border-primary bg-primary/10' : ''}`} onClick={() => { setMode('create'); setSelectedSampleId(''); setForm(EMPTY_SAMPLE); setError(null); }}>Create sample</button>
                <button type="button" className={`${BUTTON} ${mode === 'revise' ? 'border-primary bg-primary/10' : ''}`} onClick={() => { setMode('revise'); setError(null); }}>Publish revision</button>
            </div>
            {mode === 'revise' && <Field label="Sample to revise"><select className={`${INPUT} mt-1`} value={selectedSampleId} onChange={(event) => setSelectedSampleId(event.target.value)} disabled={!canMutate}><option value="">Select sample</option>{rows.map(({ sample, revision }) => <option key={sample.id} value={sample.id}>{revision?.payload.name ?? sample.id} · generation {sample.head_generation}</option>)}</select></Field>}
            <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Name"><input className={`${INPUT} mt-1`} value={form.name} onChange={(event) => update('name', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Sample kind"><input className={`${INPUT} mt-1`} value={form.sample_kind} onChange={(event) => update('sample_kind', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Description" wide><textarea className={`${INPUT} mt-1 min-h-16`} value={form.description} onChange={(event) => update('description', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Organism"><input className={`${INPUT} mt-1`} value={form.organism} onChange={(event) => update('organism', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Strain"><input className={`${INPUT} mt-1`} value={form.strain} onChange={(event) => update('strain', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="External IDs, comma separated"><input className={`${INPUT} mt-1`} value={form.external_ids} onChange={(event) => update('external_ids', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Preparation method"><input className={`${INPUT} mt-1`} value={form.method} onChange={(event) => update('method', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Preparation batch"><input className={`${INPUT} mt-1`} value={form.batch_id} onChange={(event) => update('batch_id', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Prepared at"><input className={`${INPUT} mt-1`} value={form.prepared_at} onChange={(event) => update('prepared_at', event.target.value)} placeholder="ISO timestamp or lab label" disabled={!canMutate} /></Field>
                <Field label="Container label"><input className={`${INPUT} mt-1`} value={form.container_label} onChange={(event) => update('container_label', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Barcode"><input className={`${INPUT} mt-1`} value={form.barcode} onChange={(event) => update('barcode', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="MinKNOW sample ID"><input className={`${INPUT} mt-1`} value={form.minknow_sample_id} onChange={(event) => update('minknow_sample_id', event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Notes" wide><textarea className={`${INPUT} mt-1 min-h-16`} value={form.notes} onChange={(event) => update('notes', event.target.value)} disabled={!canMutate} /></Field>
            </div>
            <button type="button" className={`${PRIMARY} mt-3`} onClick={() => saveMutation.mutate()} disabled={!canMutate || saveMutation.isPending || (mode === 'revise' && !selectedSampleId)}>{saveMutation.isPending ? 'Saving…' : mode === 'create' ? 'Create immutable sample revision' : 'Publish immutable sample revision'}</button>
            <Notice message={notice} />
            <Notice message={error} error />
        </MutationBox>
    );
}

export function DomainReferenceMutationPanel({
    domainExperimentId,
    canMutate,
    mutationBlocker,
    rows,
}: {
    domainExperimentId: string;
    canMutate: boolean;
    mutationBlocker: string | null;
    rows: ReferenceRow[];
}) {
    const queryClient = useQueryClient();
    const [mode, setMode] = useState<'create' | 'import' | 'revise' | 'archive'>('create');
    const [selectedReferenceId, setSelectedReferenceId] = useState('');
    const [name, setName] = useState('');
    const [fasta, setFasta] = useState('');
    const [moleculeType, setMoleculeType] = useState<'dna' | 'rna'>('dna');
    const [topology, setTopology] = useState<'linear' | 'circular' | 'mixed' | 'unknown'>('circular');
    const [coordinateContract, setCoordinateContract] = useState('zero_based_half_open');
    const [sourceNote, setSourceNote] = useState('Project Manager operator entry');
    const [entryId, setEntryId] = useState('');
    const [entrySource, setEntrySource] = useState<'fasta' | 'path'>('fasta');
    const [entryPath, setEntryPath] = useState('');
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const selectedReference = rows.find(({ reference }) => reference.id === selectedReferenceId)?.reference;

    const mutateReference = useMutation({
        mutationFn: async () => {
            if (!canMutate) throw new Error(mutationBlocker || 'This Domain is read-only.');
            const sourceProvenance = { surface: 'project-manager', note: sourceNote.trim() || null };
            if (mode === 'create') {
                return createMolBioNgsReference({
                    global_domain_experiment_id: domainExperimentId,
                    name: name.trim(),
                    fasta,
                    molecule_type: moleculeType,
                    topology,
                    coordinate_contract: coordinateContract.trim(),
                    source_provenance: sourceProvenance,
                    idempotency_key: crypto.randomUUID(),
                });
            }
            if (mode === 'import') {
                if (!entryId.trim() || !name.trim()) throw new Error('Entry ID and reference name are required for import.');
                return importMolBioNgsBrowserReference({
                    global_domain_experiment_id: domainExperimentId,
                    entry: {
                        id: entryId.trim(),
                        name: name.trim(),
                        source: entrySource,
                        fasta: entrySource === 'fasta' ? fasta : null,
                        path: entrySource === 'path' ? entryPath.trim() : null,
                        createdAt: new Date().toISOString(),
                        updatedAt: new Date().toISOString(),
                    },
                    name: name.trim(),
                    molecule_type: moleculeType,
                    topology,
                    coordinate_contract: coordinateContract.trim(),
                    idempotency_key: crypto.randomUUID(),
                });
            }
            if (!selectedReference) throw new Error('Select a managed reference.');
            if (mode === 'archive') {
                return archiveMolBioNgsReference(selectedReference.id, {
                    expected_head_generation: selectedReference.head_generation,
                    idempotency_key: crypto.randomUUID(),
                });
            }
            const current = rows.find(({ reference }) => reference.id === selectedReference.id)?.revision;
            if (!current) throw new Error('The exact current reference revision is still loading.');
            return createMolBioNgsReferenceRevision(selectedReference.id, {
                fasta,
                molecule_type: moleculeType,
                topology,
                coordinate_contract: coordinateContract.trim(),
                source_provenance: sourceProvenance,
                expected_head_generation: selectedReference.head_generation,
                parent_revision_id: current.id,
                idempotency_key: crypto.randomUUID(),
            });
        },
        onSuccess: (value) => {
            setNotice(`Reference operation completed: ${'id' in value ? value.id : 'updated'}.`);
            setError(null);
            setFasta('');
            setSelectedReferenceId('');
            setMode('create');
            void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-references', domainExperimentId] });
            void queryClient.invalidateQueries({ queryKey: ['ngs-molbio-project-hierarchy'] });
        },
        onError: (value: unknown) => {
            setError(value instanceof Error ? value.message : 'Reference mutation failed.');
            setNotice(null);
        },
    });

    return (
        <MutationBox title="Create, import, revise, or archive a managed reference" blocker={!canMutate ? mutationBlocker : null}>
            <div className="mt-3 flex flex-wrap gap-2" role="tablist" aria-label="Reference mutation mode">
                {(['create', 'import', 'revise', 'archive'] as const).map((value) => <button key={value} type="button" className={`${BUTTON} ${mode === value ? 'border-primary bg-primary/10' : ''}`} onClick={() => { setMode(value); setError(null); }}>{value === 'create' ? 'Create' : value === 'import' ? 'Import browser entry' : value === 'revise' ? 'Publish revision' : 'Archive'}</button>)}
            </div>
            {(mode === 'revise' || mode === 'archive') && <Field label="Managed reference"><select className={`${INPUT} mt-1`} value={selectedReferenceId} onChange={(event) => setSelectedReferenceId(event.target.value)} disabled={!canMutate}><option value="">Select reference</option>{rows.map(({ reference }) => <option key={reference.id} value={reference.id}>{reference.name} · generation {reference.head_generation}{reference.archived_at ? ' · archived' : ''}</option>)}</select></Field>}
            {mode !== 'archive' && <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Reference name"><input className={`${INPUT} mt-1`} value={name} onChange={(event) => setName(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Molecule type"><select className={`${INPUT} mt-1`} value={moleculeType} onChange={(event) => setMoleculeType(event.target.value as typeof moleculeType)} disabled={!canMutate}><option value="dna">DNA</option><option value="rna">RNA</option></select></Field>
                <Field label="Topology"><select className={`${INPUT} mt-1`} value={topology} onChange={(event) => setTopology(event.target.value as typeof topology)} disabled={!canMutate}><option value="circular">Circular</option><option value="linear">Linear</option><option value="mixed">Mixed</option><option value="unknown">Unknown</option></select></Field>
                <Field label="Coordinate contract"><input className={`${INPUT} mt-1`} value={coordinateContract} onChange={(event) => setCoordinateContract(event.target.value)} disabled={!canMutate} /></Field>
                {mode === 'import' && <>
                    <Field label="Browser entry ID"><input className={`${INPUT} mt-1`} value={entryId} onChange={(event) => setEntryId(event.target.value)} disabled={!canMutate} /></Field>
                    <Field label="Entry source"><select className={`${INPUT} mt-1`} value={entrySource} onChange={(event) => setEntrySource(event.target.value as typeof entrySource)} disabled={!canMutate}><option value="fasta">FASTA bytes</option><option value="path">Managed path</option></select></Field>
                    {entrySource === 'path' && <Field label="Managed path" wide><input className={`${INPUT} mt-1`} value={entryPath} onChange={(event) => setEntryPath(event.target.value)} disabled={!canMutate} /></Field>}
                </>}
                {mode !== 'import' || entrySource === 'fasta' ? <Field label="FASTA" wide><textarea className={`${INPUT} mt-1 min-h-32 font-mono`} value={fasta} onChange={(event) => setFasta(event.target.value)} disabled={!canMutate} placeholder=">reference\nATGC…" /></Field> : null}
                <Field label="Source note" wide><input className={`${INPUT} mt-1`} value={sourceNote} onChange={(event) => setSourceNote(event.target.value)} disabled={!canMutate} /></Field>
            </div>}
            <button type="button" className={mode === 'archive' ? `${DANGER} mt-3` : `${PRIMARY} mt-3`} onClick={() => mutateReference.mutate()} disabled={!canMutate || mutateReference.isPending || ((mode === 'revise' || mode === 'archive') && !selectedReferenceId)}>{mutateReference.isPending ? 'Saving…' : mode === 'create' ? 'Create immutable reference revision' : mode === 'import' ? 'Import immutable reference revision' : mode === 'revise' ? 'Publish immutable reference revision' : 'Archive reference'}</button>
            <Notice message={notice} />
            <Notice message={error} error />
        </MutationBox>
    );
}

function memberReceipt(members: DomainStateMember[], role: DomainStateMember['role']): string {
    return members.find((member) => member.role === role)?.receipt_id ?? '';
}

export function DomainEvidenceMutationPanel({
    domainExperimentId,
    canMutate,
    mutationBlocker,
    stateRevisionId,
    stateRevisions,
    members,
    sampleRows,
}: {
    domainExperimentId: string;
    canMutate: boolean;
    mutationBlocker: string | null;
    stateRevisionId: string | null;
    stateRevisions: DomainStateRevision[];
    members: DomainStateMember[];
    sampleRows: SampleRow[];
}) {
    const queryClient = useQueryClient();
    const [jobId, setJobId] = useState('');
    const [runId, setRunId] = useState('');
    const [observedGeneration, setObservedGeneration] = useState('1');
    const [stateId, setStateId] = useState(stateRevisionId ?? '');
    const [sampleRevisionId, setSampleRevisionId] = useState('');
    const [jobReceiptId, setJobReceiptId] = useState(memberReceipt(members, 'ngs_analysis_job'));
    const [manifestReceiptId, setManifestReceiptId] = useState(memberReceipt(members, 'ngs_analysis_result_manifest'));
    const [referenceReceiptId, setReferenceReceiptId] = useState(memberReceipt(members, 'ngs_reference'));
    const [instrumentReceiptId, setInstrumentReceiptId] = useState(memberReceipt(members, 'ngs_instrument_run'));
    const [molecularReceiptId, setMolecularReceiptId] = useState(memberReceipt(members, 'molecular_expected_construct'));
    const [comparisonReceiptId, setComparisonReceiptId] = useState(memberReceipt(members, 'ngs_comparison_panel'));
    const [assessmentRuleId, setAssessmentRuleId] = useState('server-owned-rule');
    const [notes, setNotes] = useState('');
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setStateId((current) => current || stateRevisionId || stateRevisions[0]?.id || '');
        setJobReceiptId((current) => current || memberReceipt(members, 'ngs_analysis_job'));
        setManifestReceiptId((current) => current || memberReceipt(members, 'ngs_analysis_result_manifest'));
        setReferenceReceiptId((current) => current || memberReceipt(members, 'ngs_reference'));
        setInstrumentReceiptId((current) => current || memberReceipt(members, 'ngs_instrument_run'));
        setMolecularReceiptId((current) => current || memberReceipt(members, 'molecular_expected_construct'));
        setComparisonReceiptId((current) => current || memberReceipt(members, 'ngs_comparison_panel'));
    }, [members, stateRevisionId, stateRevisions]);

    const jobAttachMutation = useMutation({
        mutationFn: () => {
            if (!jobId.trim()) throw new Error('Enter a job ID.');
            return attachMolBioNgsJobEvidence(domainExperimentId, { job_id: jobId.trim(), idempotency_key: crypto.randomUUID() });
        },
        onSuccess: (result) => { setJobReceiptId(result.ngs_job.receipt_id); setManifestReceiptId(result.ngs_result_manifest.receipt_id); setNotice('Job and result-manifest receipts attached.'); setError(null); void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-evidence', domainExperimentId] }); },
        onError: (value: unknown) => { setError(value instanceof Error ? value.message : 'Job evidence attachment failed.'); setNotice(null); },
    });
    const runAttachMutation = useMutation({
        mutationFn: () => {
            if (!runId.trim()) throw new Error('Enter an instrument run ID.');
            const generation = Number(observedGeneration);
            if (!Number.isInteger(generation) || generation < 1) throw new Error('Observed generation must be a positive integer.');
            if (!stateId) throw new Error('Select a state revision first.');
            return attachMolBioNgsInstrumentRunEvidence(domainExperimentId, { state_revision_id: stateId, run_id: runId.trim(), observed_generation: generation, idempotency_key: crypto.randomUUID() });
        },
        onSuccess: (receipt) => { setInstrumentReceiptId(receipt.receipt_id); setNotice('Instrument-run receipt attached.'); setError(null); },
        onError: (value: unknown) => { setError(value instanceof Error ? value.message : 'Instrument evidence attachment failed.'); setNotice(null); },
    });
    const assessmentMutation = useMutation({
        mutationFn: () => {
            if (!stateId || !jobReceiptId || !manifestReceiptId || !referenceReceiptId) throw new Error('State, job, result-manifest, and reference receipts are required.');
            const payload: EvidenceAssessmentRequest = {
                state_revision_id: stateId,
                sample_revision_id: sampleRevisionId || null,
                ngs_job_receipt_id: jobReceiptId,
                ngs_result_manifest_receipt_id: manifestReceiptId,
                ngs_reference_revision_receipt_id: referenceReceiptId,
                ont_instrument_run_receipt_id: instrumentReceiptId || null,
                molecular_revision_receipt_id: molecularReceiptId || null,
                ngs_comparison_panel_receipt_id: comparisonReceiptId || null,
                assessment_rule_id: assessmentRuleId.trim(),
                notes: notes.trim() || null,
                idempotency_key: crypto.randomUUID(),
            };
            return assessMolBioNgsEvidence(domainExperimentId, payload);
        },
        onSuccess: (assessment) => { setNotice(`Evidence assessment ${assessment.evidence_id} persisted with result ${assessment.scientific_assessment}.`); setError(null); setNotes(''); void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-evidence', domainExperimentId] }); void queryClient.invalidateQueries({ queryKey: ['molbio-ngs-state-revisions', domainExperimentId] }); },
        onError: (value: unknown) => { setError(value instanceof Error ? value.message : 'Evidence assessment failed.'); setNotice(null); },
    });

    const memberOptions = useMemo(() => members.map((member) => `${member.role} · ${member.receipt_id}`), [members]);
    return (
        <MutationBox title="Attach receipts and create an evidence assessment" blocker={!canMutate ? mutationBlocker : null}>
            <div className="grid gap-3 md:grid-cols-2">
                <Field label="Exact state revision"><select className={`${INPUT} mt-1`} value={stateId} onChange={(event) => setStateId(event.target.value)} disabled={!canMutate}><option value="">Select state revision</option>{stateRevisions.map((revision) => <option key={revision.id} value={revision.id}>Revision {revision.revision_number} · {revision.id}</option>)}</select></Field>
                <Field label="Assessment rule"><input className={`${INPUT} mt-1`} value={assessmentRuleId} onChange={(event) => setAssessmentRuleId(event.target.value)} disabled={!canMutate} /></Field>
            </div>
            <div className="mt-3 grid gap-3 rounded-md border border-border-primary bg-surface-secondary p-3 md:grid-cols-[1fr_auto]">
                <Field label="NGS job ID"><input className={`${INPUT} mt-1`} value={jobId} onChange={(event) => setJobId(event.target.value)} disabled={!canMutate} placeholder="Attach job receipts before assessment" /></Field>
                <div className="flex items-end"><button type="button" className={BUTTON} onClick={() => jobAttachMutation.mutate()} disabled={!canMutate || jobAttachMutation.isPending}>{jobAttachMutation.isPending ? 'Attaching…' : 'Attach job receipts'}</button></div>
                <Field label="ONT run ID"><input className={`${INPUT} mt-1`} value={runId} onChange={(event) => setRunId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Observed generation"><input className={`${INPUT} mt-1`} type="number" min="1" step="1" value={observedGeneration} onChange={(event) => setObservedGeneration(event.target.value)} disabled={!canMutate} /></Field>
                <div className="flex items-end"><button type="button" className={BUTTON} onClick={() => runAttachMutation.mutate()} disabled={!canMutate || runAttachMutation.isPending}>{runAttachMutation.isPending ? 'Attaching…' : 'Attach run receipt'}</button></div>
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Job receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={jobReceiptId} onChange={(event) => setJobReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Result manifest receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={manifestReceiptId} onChange={(event) => setManifestReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Reference revision receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={referenceReceiptId} onChange={(event) => setReferenceReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Sample revision"><select className={`${INPUT} mt-1`} value={sampleRevisionId} onChange={(event) => setSampleRevisionId(event.target.value)} disabled={!canMutate}><option value="">None</option>{sampleRows.map(({ sample, revision }) => revision ? <option key={revision.id} value={revision.id}>{revision.payload.name} · r{revision.revision_number}</option> : <option key={sample.id} value="" disabled>{sample.id} loading</option>)}</select></Field>
                <Field label="Instrument receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={instrumentReceiptId} onChange={(event) => setInstrumentReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Molecular receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={molecularReceiptId} onChange={(event) => setMolecularReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Comparison-panel receipt ID"><input className={`${INPUT} mt-1 font-mono`} value={comparisonReceiptId} onChange={(event) => setComparisonReceiptId(event.target.value)} disabled={!canMutate} /></Field>
                <Field label="Notes" wide><textarea className={`${INPUT} mt-1 min-h-16`} value={notes} onChange={(event) => setNotes(event.target.value)} disabled={!canMutate} /></Field>
            </div>
            {memberOptions.length > 0 && <p className="mt-3 text-[11px] text-content-muted">Current state receipts: {memberOptions.join(' · ')}</p>}
            <button type="button" className={`${PRIMARY} mt-3`} onClick={() => assessmentMutation.mutate()} disabled={!canMutate || assessmentMutation.isPending}>{assessmentMutation.isPending ? 'Persisting…' : 'Create immutable evidence assessment'}</button>
            <Notice message={notice} />
            <Notice message={error} error />
        </MutationBox>
    );
}
