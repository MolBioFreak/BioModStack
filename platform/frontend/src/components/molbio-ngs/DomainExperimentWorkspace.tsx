import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    fetchFullJob,
    fetchMolecularRevision,
    fetchMolBioNgsDomainState,
    fetchMolBioNgsEvidence,
    fetchMolBioNgsReferenceRevision,
    fetchMolBioNgsReferences,
    fetchMolBioNgsSampleRevision,
    fetchMolBioNgsSamples,
    fetchMolBioNgsStateRevision,
    fetchMolBioNgsStateRevisions,
    fetchOntInstrumentRunGeneration,
    fetchPcrExperimentRevision,
    type DomainStateMember,
} from '../../lib/api';
import { getProject } from '../../lib/projectManager';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';
import DomainDatasetOperator from './DomainDatasetOperator';
import DomainWorkflowOperator from './DomainWorkflowOperator';
import ExperimentReferenceLibrary from './ExperimentReferenceLibrary';

const SECTIONS = [
    ['overview', 'Overview'],
    ['samples', 'Samples'],
    ['molecular-inputs', 'Molecular Inputs'],
    ['references', 'References'],
    ['pcr', 'PCR'],
    ['instrument-runs', 'Instrument Runs'],
    ['datasets', 'Datasets'],
    ['workflow-plans', 'Plans & Runs'],
    ['analyses', 'Analyses'],
    ['evidence', 'Evidence'],
    ['history', 'History'],
] as const;

function selectedDatasetRevisionIdsFromQuery(): string[] {
    const encoded = new URLSearchParams(window.location.search).get('dataset_revision_ids') ?? '';
    return [...new Set(encoded.split(',').map((value) => value.trim()).filter(Boolean))].slice(0, 100);
}

type SectionKey = (typeof SECTIONS)[number][0];

type ExactReceiptReopenDestination = {
    aggregateId: string;
    revisionId: string;
    error: null;
} | {
    aggregateId: null;
    revisionId: null;
    error: string;
};

const RECEIPT_REOPEN_SPECS = {
    molecular_revision: {
        surface: 'molbio-sequence-revision',
        aggregateKey: 'sequence_id',
        error: 'Molecular receipt lacks an exact sequence/revision destination.',
    },
    pcr_experiment_revision: {
        surface: 'molbio-pcr-experiment-revision',
        aggregateKey: 'experiment_id',
        error: 'PCR receipt lacks an exact experiment/revision destination.',
    },
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
    return typeof value === 'string' && value.trim().length > 0;
}

function parseExactReceiptReopenDestination(member: DomainStateMember): ExactReceiptReopenDestination {
    const spec = RECEIPT_REOPEN_SPECS[member.entity_kind as keyof typeof RECEIPT_REOPEN_SPECS];
    if (!spec) {
        return { aggregateId: null, revisionId: null, error: 'Receipt kind has no exact MolBio reopen destination.' };
    }

    const destination = member.reopen_destination;
    if (!isRecord(destination) || destination.surface !== spec.surface || !isRecord(destination.params)) {
        return { aggregateId: null, revisionId: null, error: spec.error };
    }

    const aggregateId = destination.params[spec.aggregateKey];
    const revisionId = destination.params.revision_id;
    if (!isNonEmptyString(aggregateId) || !isNonEmptyString(revisionId)) {
        return { aggregateId: null, revisionId: null, error: spec.error };
    }
    return { aggregateId, revisionId, error: null };
}

function errorText(error: unknown): string | null {
    if (!error) return null;
    return error instanceof Error ? error.message : String(error);
}

function Identifier({ label, value }: { label: string; value: string | number | null | undefined }) {
    return (
        <div className="min-w-0 rounded-lg border border-border-primary bg-surface px-3 py-2">
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-content-muted">{label}</dt>
            <dd className="mt-1 break-all font-mono text-xs text-content">{value ?? 'Not selected'}</dd>
        </div>
    );
}

function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
    return (
        <section className="min-w-0 rounded-xl border border-border-primary bg-surface-secondary p-4 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-content">{title}</h3>
                {action}
            </div>
            {children}
        </section>
    );
}

function Empty({ children }: { children: ReactNode }) {
    return <p className="rounded-lg border border-dashed border-border-primary p-4 text-sm text-content-muted">{children}</p>;
}

function ErrorNotice({ error }: { error: unknown }) {
    const message = errorText(error);
    if (!message) return null;
    return (
        <div className="mb-3 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error" role="alert">
            {message}
        </div>
    );
}

function Digest({ label, value }: { label: string; value: string | null | undefined }) {
    return (
        <div className="mt-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-content-muted">{label}</span>
            <p className="break-all font-mono text-xs text-content-secondary">{value ?? 'None'}</p>
        </div>
    );
}

function MemberAuthority({ member }: { member: DomainStateMember }) {
    return (
        <div className="grid gap-2 rounded-lg border border-border-primary bg-surface p-3 sm:grid-cols-2 xl:grid-cols-4">
            <Identifier label="Role" value={member.role} />
            <Identifier label="Entity ID" value={member.entity_id} />
            <Identifier label="Exact revision / generation" value={member.source_generation_or_revision} />
            <Identifier label="Source store" value={member.source_store_id} />
            <div className="sm:col-span-2 xl:col-span-4">
                <Digest label="Content digest" value={member.content_digest} />
                <Digest label="Receipt digest" value={member.receipt_sha256} />
            </div>
        </div>
    );
}

export default function DomainExperimentWorkspace() {
    const context = useGlobalExperimentContext();
    const {
        workspaceId,
        globalExperimentId,
        domainExperimentId,
        stateRevisionId,
        selectedDomainExperiment,
        availability,
        setStateRevisionId,
        updateQueryParams,
        contextHref,
    } = context;
    const [selectedDatasetRevisionIds, setSelectedDatasetRevisionIds] = useState<string[]>(
        selectedDatasetRevisionIdsFromQuery,
    );

    useEffect(() => {
        setSelectedDatasetRevisionIds(selectedDatasetRevisionIdsFromQuery());
    }, [workspaceId, globalExperimentId, domainExperimentId]);

    const updateSelectedDatasetRevisionIds = useCallback((revisionIds: string[]) => {
        const exactRevisionIds = [...new Set(revisionIds.map((value) => value.trim()).filter(Boolean))].slice(0, 100);
        setSelectedDatasetRevisionIds(exactRevisionIds);
        updateQueryParams({ dataset_revision_ids: exactRevisionIds.length ? exactRevisionIds.join(',') : null });
    }, [updateQueryParams]);

    const projectAuthorityQuery = useQuery({
        queryKey: ['ngs-molbio-project-authority', workspaceId],
        enabled: Boolean(workspaceId),
        queryFn: ({ signal }) => getProject(workspaceId, signal),
        retry: false,
    });
    const requestedSection = new URLSearchParams(window.location.search).get('section');
    const isLocalProject = projectAuthorityQuery.data?.payload?.project_scope === 'ngs_molbio_local';
    const activeSection: SectionKey = SECTIONS.some(([key]) => key === requestedSection)
        ? requestedSection as SectionKey
        : 'overview';
    const exactDomainId = selectedDomainExperiment?.domain_experiment_id ?? null;

    const stateQuery = useQuery({
        queryKey: ['molbio-ngs-domain-state', exactDomainId],
        queryFn: () => fetchMolBioNgsDomainState(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });
    const historyQuery = useQuery({
        queryKey: ['molbio-ngs-state-revisions', exactDomainId],
        queryFn: () => fetchMolBioNgsStateRevisions(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });
    const samplesQuery = useQuery({
        queryKey: ['molbio-ngs-samples', exactDomainId],
        queryFn: () => fetchMolBioNgsSamples(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });
    const referencesQuery = useQuery({
        queryKey: ['molbio-ngs-references', exactDomainId],
        queryFn: () => fetchMolBioNgsReferences(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });
    const evidenceQuery = useQuery({
        queryKey: ['molbio-ngs-evidence', exactDomainId],
        queryFn: () => fetchMolBioNgsEvidence(exactDomainId as string),
        enabled: exactDomainId !== null,
        retry: false,
    });

    const selectedStateRevisionId = stateRevisionId
        ?? selectedDomainExperiment?.local_state_revision_id
        ?? stateQuery.data?.current_state_revision_id
        ?? null;

    useEffect(() => {
        if (stateRevisionId === null && selectedStateRevisionId !== null) {
            updateQueryParams({ state_revision_id: selectedStateRevisionId }, { replace: true });
        }
    }, [selectedStateRevisionId, stateRevisionId, updateQueryParams]);

    const selectedRevisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', exactDomainId, selectedStateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(exactDomainId as string, selectedStateRevisionId as string),
        enabled: exactDomainId !== null && selectedStateRevisionId !== null,
        retry: false,
    });

    const sampleRevisionQueries = useQueries({
        queries: (samplesQuery.data ?? []).map((sample) => ({
            queryKey: ['molbio-ngs-sample-revision', exactDomainId, sample.id, sample.current_revision_id],
            queryFn: () => fetchMolBioNgsSampleRevision(
                exactDomainId as string,
                sample.id,
                sample.current_revision_id as string,
            ),
            enabled: exactDomainId !== null && sample.current_revision_id !== null,
            retry: false,
        })),
    });
    const referenceRevisionQueries = useQueries({
        queries: (referencesQuery.data ?? []).map((reference) => ({
            queryKey: ['molbio-ngs-reference-revision', reference.id, reference.current_revision_id],
            queryFn: () => fetchMolBioNgsReferenceRevision(reference.id, reference.current_revision_id as string),
            enabled: reference.current_revision_id !== null,
            retry: false,
        })),
    });

    const members = selectedRevisionQuery.data?.members ?? [];
    const molecularMembers = members.filter((member) => member.entity_kind === 'molecular_revision');
    const pcrMembers = members.filter((member) => member.entity_kind === 'pcr_experiment_revision');
    const runMembers = members.filter((member) => member.entity_kind === 'ont_instrument_run');
    const jobMembers = members.filter((member) => member.entity_kind === 'ngs_job');
    const molecularDestinations = molecularMembers.map(parseExactReceiptReopenDestination);
    const pcrDestinations = pcrMembers.map(parseExactReceiptReopenDestination);

    const molecularQueries = useQueries({
        queries: molecularDestinations.map((destination) => {
            return {
                queryKey: ['molecular-revision', destination.aggregateId, destination.revisionId],
                queryFn: () => destination.error === null
                    ? fetchMolecularRevision(destination.aggregateId, destination.revisionId)
                    : Promise.reject(new Error(destination.error)),
                enabled: destination.error === null,
                retry: false,
            };
        }),
    });
    const pcrQueries = useQueries({
        queries: pcrDestinations.map((destination) => {
            return {
                queryKey: ['pcr-experiment-revision', destination.aggregateId, destination.revisionId],
                queryFn: () => destination.error === null
                    ? fetchPcrExperimentRevision(destination.aggregateId, destination.revisionId)
                    : Promise.reject(new Error(destination.error)),
                enabled: destination.error === null,
                retry: false,
            };
        }),
    });
    const runQueries = useQueries({
        queries: runMembers.map((member) => ({
            queryKey: ['ont-run-generation', member.entity_id, member.source_generation_or_revision],
            queryFn: () => fetchOntInstrumentRunGeneration(
                member.entity_id,
                Number(member.source_generation_or_revision),
            ),
            enabled: Number.isInteger(Number(member.source_generation_or_revision)),
            retry: false,
        })),
    });
    const jobQueries = useQueries({
        queries: jobMembers.map((member) => ({
            queryKey: ['full-job', member.entity_id],
            queryFn: () => fetchFullJob(member.entity_id),
            retry: false,
        })),
    });

    const queryErrors = useMemo(() => [
        stateQuery.error,
        historyQuery.error,
        samplesQuery.error,
        referencesQuery.error,
        evidenceQuery.error,
        selectedRevisionQuery.error,
    ].filter(Boolean), [
        evidenceQuery.error,
        historyQuery.error,
        referencesQuery.error,
        samplesQuery.error,
        selectedRevisionQuery.error,
        stateQuery.error,
    ]);

    const disabledMutationReason = availability.canMutateDomain
        ? 'Reference mutation forms are intentionally not exposed in this read/reopen foundation.'
        : availability.reason;

    const renderOverview = () => (
        <div className="grid gap-4 xl:grid-cols-3">
            <Panel title="Exact authority context">
                <div className="grid gap-2 sm:grid-cols-2">
                    <Identifier label="Project (workspace) ID" value={workspaceId} />
                    <Identifier label="Global Experiment ID" value={globalExperimentId} />
                    <Identifier label="NGS/MolBio Domain Experiment ID" value={domainExperimentId} />
                    <Identifier
                        label="Global Domain Experiment revision ID"
                        value={selectedDomainExperiment?.global_domain_experiment_revision_id}
                    />
                    <Identifier label="Local state revision ID" value={selectedStateRevisionId} />
                    <Identifier
                        label="Local state head generation"
                        value={selectedDomainExperiment?.local_state_head_generation ?? stateQuery.data?.head_generation}
                    />
                </div>
            </Panel>
            <Panel title="Local scientific-state counts">
                <div className="grid grid-cols-3 gap-2">
                    <Identifier label="Samples" value={selectedDomainExperiment?.local_counts.samples ?? 0} />
                    <Identifier label="References" value={selectedDomainExperiment?.local_counts.references ?? 0} />
                    <Identifier label="Evidence" value={selectedDomainExperiment?.local_counts.evidence_assessments ?? 0} />
                </div>
                <Digest label="State payload digest" value={selectedRevisionQuery.data?.payload_sha256} />
                <Digest label="Membership graph digest" value={selectedRevisionQuery.data?.membership_graph_sha256} />
            </Panel>
            <Panel title="Availability">
                <dl className="grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
                    <Identifier label="Workspace surface" value={availability.status} />
                    <Identifier label="Persisted local binding" value={availability.localBinding} />
                    <Identifier label="Global adapter" value={availability.globalAdapter} />
                </dl>
                <p className="mt-3 text-sm text-content-secondary">{availability.reason}</p>
            </Panel>
            <div className="xl:col-span-3">
                <Panel title="Selected immutable state policy">
                    {selectedRevisionQuery.data ? (
                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                            <Identifier label="Schema" value={selectedRevisionQuery.data.schema_name} />
                            <Identifier label="Revision number" value={selectedRevisionQuery.data.revision_number} />
                            <Identifier label="Acquisition platform" value={selectedRevisionQuery.data.payload.acquisition_policy.platform} />
                            <Identifier label="Assessment rule" value={selectedRevisionQuery.data.payload.assessment_policy.rule_id} />
                        </div>
                    ) : <Empty>No local state revision is selected or available.</Empty>}
                </Panel>
            </div>
        </div>
    );

    const renderSamples = () => (
        <Panel title="Domain samples">
            <ErrorNotice error={samplesQuery.error} />
            {(samplesQuery.data ?? []).length === 0 ? <Empty>No samples belong to this exact Domain Experiment.</Empty> : (
                <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                    {(samplesQuery.data ?? []).map((sample, index) => {
                        const revisionQuery = sampleRevisionQueries[index];
                        const revision = revisionQuery?.data;
                        return (
                            <div key={sample.id} className="rounded-lg border border-border-primary bg-surface p-3">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h4 className="font-semibold text-content">{revision?.payload.name ?? 'Unnamed sample'}</h4>
                                        <p className="font-mono text-xs text-content-muted">{sample.id}</p>
                                    </div>
                                    <span className="rounded-full bg-surface-tertiary px-2 py-1 text-xs text-content-secondary">
                                        generation {sample.head_generation}
                                    </span>
                                </div>
                                <ErrorNotice error={revisionQuery?.error} />
                                <Identifier label="Current immutable revision ID" value={sample.current_revision_id} />
                                <Digest label="Revision payload digest" value={revision?.payload_sha256} />
                                {revision && (
                                    <Link
                                        className="mt-3 inline-flex text-xs font-semibold text-info hover:underline"
                                        to={contextHref(window.location.pathname, {
                                            section: 'samples',
                                            sample_id: sample.id,
                                            sample_revision_id: revision.id,
                                        })}
                                    >
                                        Reopen exact sample revision
                                    </Link>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </Panel>
    );

    const renderMolecularInputs = () => (
        <>
            {exactDomainId && selectedDomainExperiment && (
                <ExperimentReferenceLibrary
                    domainExperimentId={exactDomainId}
                    globalDomainExperimentRevisionId={selectedDomainExperiment.global_domain_experiment_revision_id}
                    currentStateRevisionId={stateQuery.data?.current_state_revision_id ?? null}
                    stateHeadGeneration={stateQuery.data?.head_generation ?? 0}
                    canMutate={availability.canMutateDomain}
                    mutationBlocker={availability.canMutateDomain ? null : availability.reason}
                />
            )}
            <Panel title="Experiment reference sequences">
                {molecularMembers.length === 0 ? <Empty>No shared reference revision is attached to the selected scientific-state revision.</Empty> : (
                    <div className="grid gap-3 xl:grid-cols-2">
                        {molecularMembers.map((member, index) => {
                            const destination = molecularDestinations[index];
                            const revisionQuery = molecularQueries[index];
                            const revision = revisionQuery?.data;
                            return (
                                <div key={member.receipt_id} className="space-y-3 rounded-lg border border-border-primary bg-surface p-3">
                                    <ErrorNotice error={destination.error} />
                                    <ErrorNotice error={revisionQuery?.error} />
                                    <MemberAuthority member={member} />
                                    {revision && destination.error === null && (
                                        <>
                                            <div className="grid gap-2 sm:grid-cols-3">
                                                <Identifier label="Reference sequence" value={revision.document_name} />
                                                <Identifier label="Exact revision ID" value={revision.revision_id} />
                                                <Identifier label="Experiment role" value={member.role} />
                                            </div>
                                            <Digest label="Molecular content digest" value={revision.content_sha256} />
                                            <Link
                                                className="inline-flex text-xs font-semibold text-info hover:underline"
                                                to={contextHref('/designer', {
                                                    section: 'molecular-inputs',
                                                    molbio_sequence_id: destination.aggregateId,
                                                    molbio_revision_id: destination.revisionId,
                                                })}
                                            >
                                                Reopen exact reference in molecular viewer
                                            </Link>
                                        </>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </Panel>
        </>
    );

    const renderReferences = () => (
        <Panel
            title="Managed reference revisions"
            action={(
                <div className="flex flex-wrap gap-2" title={disabledMutationReason}>
                    {['Create', 'Import', 'Archive'].map((label) => (
                        <button
                            key={label}
                            type="button"
                            disabled
                            className="cursor-not-allowed rounded-md border border-border-primary bg-surface px-2.5 py-1.5 text-xs text-content-muted opacity-60"
                        >
                            {label}
                        </button>
                    ))}
                </div>
            )}
        >
            <p className="mb-3 text-xs text-content-muted">Reference mutations disabled: {disabledMutationReason}</p>
            <ErrorNotice error={referencesQuery.error} />
            {(referencesQuery.data ?? []).length === 0 ? <Empty>No managed references belong to this exact Domain Experiment.</Empty> : (
                <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                    {(referencesQuery.data ?? []).map((reference, index) => {
                        const revisionQuery = referenceRevisionQueries[index];
                        const revision = revisionQuery?.data;
                        return (
                            <div key={reference.id} className="rounded-lg border border-border-primary bg-surface p-3">
                                <div className="flex items-center justify-between gap-2">
                                    <h4 className="font-semibold text-content">{reference.name}</h4>
                                    {reference.archived_at && <span className="text-xs text-warning">Archived</span>}
                                </div>
                                <Identifier label="Reference ID" value={reference.id} />
                                <Identifier label="Current revision ID" value={reference.current_revision_id} />
                                <ErrorNotice error={revisionQuery?.error} />
                                <Digest label="Revision payload digest" value={revision?.payload_sha256} />
                                <Digest label="Canonical FASTA byte digest" value={revision?.canonical_fasta_sha256} />
                                <Digest label="Contig manifest digest" value={revision?.contig_manifest_sha256} />
                                {revision && (
                                    <Link
                                        className="mt-3 inline-flex text-xs font-semibold text-info hover:underline"
                                        to={contextHref('/ngs', {
                                            section: 'references',
                                            reference_id: reference.id,
                                            reference_revision_id: revision.id,
                                        })}
                                    >
                                        Reopen exact reference revision
                                    </Link>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </Panel>
    );

    const renderPcr = () => (
        <Panel title="Exact PCR experiment revisions">
            {pcrMembers.length === 0 ? <Empty>No PCR experiment revision receipts are members of the selected state revision.</Empty> : (
                <div className="grid gap-3 xl:grid-cols-2">
                    {pcrMembers.map((member, index) => {
                        const destination = pcrDestinations[index];
                        const revisionQuery = pcrQueries[index];
                        const revision = revisionQuery?.data;
                        return (
                            <div key={member.receipt_id} className="space-y-3 rounded-lg border border-border-primary bg-surface p-3">
                                <ErrorNotice error={destination.error} />
                                <ErrorNotice error={revisionQuery?.error} />
                                <MemberAuthority member={member} />
                                {revision && destination.error === null && (
                                    <>
                                        <div className="grid gap-2 sm:grid-cols-3">
                                            <Identifier label="PCR revision ID" value={revision.id} />
                                            <Identifier label="Revision number" value={revision.revision_number} />
                                            <Identifier label="Review state" value={revision.review_state} />
                                        </div>
                                        <Digest label="Template sequence digest" value={revision.template_sha256} />
                                        <Link
                                            className="inline-flex text-xs font-semibold text-info hover:underline"
                                            to={contextHref('/designer', {
                                                section: 'pcr',
                                                pcr_experiment_id: destination.aggregateId,
                                                pcr_revision_id: destination.revisionId,
                                            })}
                                        >
                                            Reopen exact PCR revision in MolBio
                                        </Link>
                                    </>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </Panel>
    );

    const renderRuns = () => (
        <Panel title="Exact ONT instrument generations">
            {runMembers.length === 0 ? <Empty>No instrument-run generation receipts are members of the selected state revision.</Empty> : (
                <div className="grid gap-3 xl:grid-cols-2">
                    {runMembers.map((member, index) => {
                        const runQuery = runQueries[index];
                        const run = runQuery?.data;
                        return (
                            <div key={member.receipt_id} className="space-y-3 rounded-lg border border-border-primary bg-surface p-3">
                                <ErrorNotice error={runQuery?.error} />
                                <MemberAuthority member={member} />
                                {run && (
                                    <>
                                        <div className="grid gap-2 sm:grid-cols-3">
                                            <Identifier label="Run ID" value={run.run_id} />
                                            <Identifier label="Observed generation" value={run.observed_generation} />
                                            <Identifier label="Status" value={run.status} />
                                        </div>
                                        <Digest label="Terminal manifest digest" value={run.terminal_manifest_sha256} />
                                        <Link
                                            className="inline-flex text-xs font-semibold text-info hover:underline"
                                            to={contextHref('/ngs', {
                                                section: 'instrument-runs',
                                                run_id: run.run_id,
                                                observed_generation: String(run.observed_generation),
                                            })}
                                        >
                                            Reopen exact instrument generation in NGS
                                        </Link>
                                    </>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </Panel>
    );

    const renderAnalyses = () => (
        <Panel title="Exact analysis jobs">
            {jobMembers.length === 0 ? <Empty>No NGS job receipts are members of the selected state revision.</Empty> : (
                <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
                    {jobMembers.map((member, index) => {
                        const jobQuery = jobQueries[index];
                        const job = jobQuery?.data;
                        return (
                            <div key={member.receipt_id} className="space-y-3 rounded-lg border border-border-primary bg-surface p-3">
                                <ErrorNotice error={jobQuery?.error} />
                                <MemberAuthority member={member} />
                                {job && (
                                    <>
                                        <div className="grid gap-2 sm:grid-cols-2">
                                            <Identifier label="Job ID" value={job.id} />
                                            <Identifier label="Lifecycle" value={job.status} />
                                            <Identifier label="Model" value={job.model_id} />
                                            <Identifier label="Mode" value={job.mode} />
                                        </div>
                                        <Link
                                            className="inline-flex text-xs font-semibold text-info hover:underline"
                                            to={contextHref('/ngs', { section: 'analyses', job_id: job.id })}
                                        >
                                            Reopen full job detail in NGS
                                        </Link>
                                    </>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </Panel>
    );

    const renderEvidence = () => (
        <Panel title="Immutable scientific evidence assessments">
            <ErrorNotice error={evidenceQuery.error} />
            {(evidenceQuery.data ?? []).length === 0 ? <Empty>No evidence assessments belong to this exact Domain Experiment.</Empty> : (
                <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
                    {(evidenceQuery.data ?? []).map((assessment) => (
                        <div key={assessment.evidence_id} className="rounded-lg border border-border-primary bg-surface p-3">
                            <h4 className="font-semibold text-content">{assessment.scientific_assessment}</h4>
                            <Identifier label="Evidence assessment ID" value={assessment.evidence_id} />
                            <Identifier label="Bound state revision ID" value={assessment.state_revision_id} />
                            <Identifier label="Job lifecycle" value={assessment.job_lifecycle_state} />
                            <Identifier label="Manifest integrity" value={assessment.manifest_integrity} />
                            <Digest label="Raw manifest digest" value={assessment.raw_manifest_sha256} />
                            <Digest label="Assessment wrapper digest" value={assessment.wrapper_sha256} />
                            <Link
                                className="mt-3 inline-flex text-xs font-semibold text-info hover:underline"
                                to={contextHref('/ngs', {
                                    section: 'evidence',
                                    evidence_id: assessment.evidence_id,
                                    state_revision_id: assessment.state_revision_id,
                                })}
                            >
                                Reopen exact evidence assessment
                            </Link>
                        </div>
                    ))}
                </div>
            )}
        </Panel>
    );

    const renderHistory = () => (
        <Panel title="Immutable local state history">
            <ErrorNotice error={historyQuery.error} />
            {(historyQuery.data ?? []).length === 0 ? <Empty>No local state revisions are available.</Empty> : (
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[900px] border-separate border-spacing-y-2 text-left text-xs">
                        <thead className="text-content-muted">
                            <tr>
                                <th className="px-3 py-1">Revision</th>
                                <th className="px-3 py-1">Local state revision ID</th>
                                <th className="px-3 py-1">Global domain revision ID</th>
                                <th className="px-3 py-1">Payload digest</th>
                                <th className="px-3 py-1">Membership digest</th>
                                <th className="px-3 py-1">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(historyQuery.data ?? []).map((revision) => (
                                <tr key={revision.id} className="bg-surface">
                                    <td className="rounded-l-lg px-3 py-2 font-semibold text-content">{revision.revision_number}</td>
                                    <td className="break-all px-3 py-2 font-mono text-content-secondary">{revision.id}</td>
                                    <td className="break-all px-3 py-2 font-mono text-content-secondary">{revision.global_domain_experiment_revision_id}</td>
                                    <td className="break-all px-3 py-2 font-mono text-content-secondary">{revision.payload_sha256}</td>
                                    <td className="break-all px-3 py-2 font-mono text-content-secondary">{revision.membership_graph_sha256}</td>
                                    <td className="rounded-r-lg px-3 py-2">
                                        <button
                                            type="button"
                                            className="font-semibold text-info hover:underline"
                                            onClick={() => setStateRevisionId(revision.id)}
                                        >
                                            Reopen exact revision
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </Panel>
    );

    const projectReturnUri = workspaceId && globalExperimentId && domainExperimentId
        ? `/projects/${encodeURIComponent(workspaceId)}?${new URLSearchParams({
            focus: globalExperimentId,
            selected: `domain_experiment:${domainExperimentId}`,
        }).toString()}`
        : '/projects';

    const hasProjectOwnedContext = Boolean(workspaceId && globalExperimentId && domainExperimentId);

    const renderDatasets = () => {
        if (!workspaceId || !globalExperimentId || !domainExperimentId) {
            return <Empty>Select an exact Project, Global Experiment, and NGS/MolBio Domain Experiment.</Empty>;
        }
        const currentStateRevisionId = stateQuery.data?.current_state_revision_id ?? null;
        const datasetMutationBlocker = !availability.canMutateDomain
            ? availability.reason
            : !selectedStateRevisionId
                ? 'Select an immutable local state revision.'
                : selectedStateRevisionId !== currentStateRevisionId
                    ? 'Historical local state revisions are read-only. Select the current immutable state revision to mutate Datasets.'
                    : null;
        return (
            <DomainDatasetOperator
                projectId={workspaceId}
                globalExperimentId={globalExperimentId}
                domainExperimentId={domainExperimentId}
                canMutate={datasetMutationBlocker === null}
                mutationBlocker={datasetMutationBlocker}
                selectedRevisionIds={selectedDatasetRevisionIds}
                onSelectedRevisionIdsChange={updateSelectedDatasetRevisionIds}
            />
        );
    };

    const renderWorkflowPlans = () => {
        if (!workspaceId || !globalExperimentId || !domainExperimentId) {
            return <Empty>Select an exact Project, Global Experiment, and NGS/MolBio Domain Experiment.</Empty>;
        }
        return (
            <DomainWorkflowOperator
                projectId={workspaceId}
                globalExperimentId={globalExperimentId}
                domainExperimentId={domainExperimentId}
                initialRunGroupId={new URLSearchParams(window.location.search).get('run_group_id')}
                domainRevisionId={selectedDomainExperiment?.global_domain_experiment_revision_id ?? null}
                selectedStateRevisionId={selectedStateRevisionId}
                currentStateRevisionId={stateQuery.data?.current_state_revision_id ?? null}
                projectReturnUri={projectReturnUri}
                contextHref={contextHref}
                inputDatasetRevisionIds={selectedDatasetRevisionIds}
            />
        );
    };

    const sectionContent: Record<SectionKey, () => ReactNode> = {
        overview: renderOverview,
        samples: renderSamples,
        'molecular-inputs': renderMolecularInputs,
        references: renderReferences,
        pcr: renderPcr,
        'instrument-runs': renderRuns,
        datasets: renderDatasets,
        'workflow-plans': renderWorkflowPlans,
        analyses: renderAnalyses,
        evidence: renderEvidence,
        history: renderHistory,
    };

    return (
        <div className="w-full max-w-none border-b border-border-primary bg-surface px-3 py-4 sm:px-5 lg:px-6">
            <div className="mx-0 w-full max-w-none space-y-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">{isLocalProject ? 'Local NGS/MolBio-owned context' : 'Broader Project-owned NGS/MolBio context'}</p>
                        <h2 className="text-xl font-bold text-content">Domain Experiment workspace</h2>
                        <p className="mt-1 max-w-4xl text-sm text-content-secondary">
                            Inspect immutable scientific state, prepare and launch governed Workflow Plans, reopen Workflow Receipts, and inspect data-bearing Results.
                            {isLocalProject
                                ? ' The NGS/MolBio layer owns this complete standalone Project and its contained Experiments. Optional governed links can expose selected Experiments and Results to several broader Projects.'
                                : ' The broader Project Manager owns this Project and contains this NGS/MolBio Experiment. The domain store owns native scientific Data and Results.'}
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                        {selectedDomainExperiment && (
                            <>
                                <Link
                                    className="rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content"
                                    to={contextHref('/molbio', { state_revision_id: selectedStateRevisionId })}
                                >
                                    MolBio Toolkit
                                </Link>
                                <Link
                                    className="rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content"
                                    to={contextHref('/ngs', { state_revision_id: selectedStateRevisionId })}
                                >
                                    NGS Toolkit
                                </Link>
                                {!isLocalProject && (
                                    <Link
                                        className="rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content"
                                        to={projectReturnUri}
                                    >
                                        Return to broader Project
                                    </Link>
                                )}
                            </>
                        )}
                        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                            availability.status === 'available'
                                ? 'bg-success/15 text-success'
                                : availability.status === 'read-only'
                                    ? 'bg-warning/15 text-warning'
                                    : 'bg-error/10 text-error'
                        }`}>
                            {availability.status}
                        </span>
                    </div>
                </div>

                {hasProjectOwnedContext ? (
                    <div className="grid gap-3 lg:grid-cols-4">
                        <Identifier label={isLocalProject ? 'Local NGS/MolBio Project' : 'Broader BMS Project'} value={workspaceId} />
                        <Identifier label="Global Experiment" value={globalExperimentId} />
                        <Identifier label="NGS/MolBio Domain Experiment" value={domainExperimentId} />
                        <label className="text-xs font-semibold text-content-secondary">
                            Immutable local state revision
                            <select
                                value={selectedStateRevisionId ?? ''}
                                disabled={!selectedDomainExperiment || historyQuery.isLoading}
                                onChange={(event) => setStateRevisionId(event.target.value || null)}
                                className="mt-1 w-full rounded-lg border border-border-primary bg-surface-secondary px-3 py-2 text-sm text-content disabled:opacity-50"
                            >
                                <option value="">Select immutable state revision</option>
                                {(historyQuery.data ?? []).map((revision) => (
                                    <option key={revision.id} value={revision.id}>
                                        Revision {revision.revision_number} — {revision.id}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>
                ) : (
                    <div className="rounded-xl border border-warning/40 bg-warning/10 p-4" role="status">
                        <p className="text-sm font-semibold text-content">Choose an NGS/MolBio Project and Experiment</p>
                        <p className="mt-1 text-sm text-content-secondary">
                            Create or open a local NGS/MolBio Project above. For cross-domain work, create an NGS/MolBio Experiment inside a broader Project or open one from Project Manager.
                        </p>
                        <Link
                            to="/projects"
                            className="mt-3 inline-flex rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white focus:ring-2 focus:ring-accent"
                        >
                            Open Project Manager
                        </Link>
                    </div>
                )}

                {hasProjectOwnedContext && availability.status !== 'available' && availability.status !== 'read-only' && (
                    <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-content" role="status">
                        <p className="font-semibold">Domain context is not ready</p>
                        <p className="mt-1 text-content-secondary">{availability.reason}</p>
                    </div>
                )}
                {availability.status === 'read-only' && (
                    <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-content" role="status">
                        <p className="font-semibold">Read/reopen mode</p>
                        <p className="mt-1 text-content-secondary">{availability.reason}</p>
                    </div>
                )}
                {queryErrors.map((error, index) => <ErrorNotice key={`${errorText(error)}-${index}`} error={error} />)}

                {selectedDomainExperiment && (
                    <>
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
                            <Identifier label="Project / workspace ID" value={workspaceId} />
                            <Identifier label="Global Experiment ID" value={globalExperimentId} />
                            <Identifier label="Domain Experiment ID" value={domainExperimentId} />
                            <Identifier label="Global domain revision ID" value={selectedDomainExperiment.global_domain_experiment_revision_id} />
                            <Identifier label="Local state revision ID" value={selectedStateRevisionId} />
                            <Identifier label="Local head generation" value={selectedDomainExperiment.local_state_head_generation} />
                        </div>
                        <nav className="flex w-full gap-1 overflow-x-auto rounded-xl border border-border-primary bg-surface-secondary p-1" aria-label="Domain Experiment sections">
                            {SECTIONS.map(([key, label]) => (
                                <button
                                    key={key}
                                    type="button"
                                    onClick={() => updateQueryParams({ section: key })}
                                    className={`shrink-0 rounded-lg px-3 py-2 text-xs font-semibold transition-colors ${
                                        activeSection === key
                                            ? 'bg-accent text-white'
                                            : 'text-content-secondary hover:bg-surface-tertiary hover:text-content'
                                    }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </nav>
                        <div className="min-w-0">{sectionContent[activeSection]()}</div>
                    </>
                )}
            </div>
        </div>
    );
}
