import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import {
    createOntRunIntent,
    fetchMolBioNgsReferenceRevision,
    fetchMolBioNgsSampleRevision,
    fetchMolBioNgsSamples,
    fetchOntDeviceStatus,
    fetchOntExternalPod5Candidates,
    fetchOntInstrumentRunGeneration,
    fetchOntInstrumentRuns,
    fetchOntRawSignalCapabilities,
    fetchOntRawSignalWaveform,
    requestOntBlow5Preparation,
    requestOntRawSignalWaveform,
    registerOntExternalPod5Candidate,
    fetchOntProtocolOptions,
    requestMk1dReconnect,
    startOntRunIntent,
    type OntInstrumentRun,
    type OntLiveDevice,
    type OntProtocolOption,
    type OntRawSignalPreference,
    type OntRawSignalWaveform,
    type OntRunSummary,
} from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

const RAW_WAVEFORM_POLL_ATTEMPTS = 130;

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: () => void;
}

interface SelectedRunGeneration {
    runId: string;
    observedGeneration: number;
}

type ExactUrlRevisionPair = {
    resourceId: string;
    revisionId: string;
};

type ParsedExactUrlRevisionPair = {
    value: ExactUrlRevisionPair | null;
    error: string | null;
};

function parseExactUrlRevisionPair(
    resourceId: string | null,
    revisionId: string | null,
    label: string,
): ParsedExactUrlRevisionPair {
    if (resourceId === null && revisionId === null) return { value: null, error: null };
    if (
        resourceId === null
        || revisionId === null
        || resourceId.trim().length === 0
        || revisionId.trim().length === 0
    ) {
        return {
            value: null,
            error: `${label} pinned revision URL requires both the resource ID and revision ID.`,
        };
    }
    return { value: { resourceId, revisionId }, error: null };
}

function statusLabel(status?: string): string {
    switch (status) {
        case 'configured': return 'Mk1D discovery available';
        case 'not_configured': return 'MinKNOW not configured';
        case 'client_missing': return 'MinKNOW client missing';
        case 'unreachable': return 'MinKNOW unreachable';
        default: return status || 'unknown';
    }
}

function deviceStateLabel(device: OntLiveDevice): string {
    if (device.running) return 'position running';
    if (!device.flow_cell.present) return 'flow cell absent';
    return device.available_for_run ? 'available for run' : (device.state || 'not available');
}

function responseDetail(error: unknown): string | undefined {
    if (!error || typeof error !== 'object') return undefined;
    const response = (error as { response?: unknown }).response;
    if (!response || typeof response !== 'object') return undefined;
    const data = (response as { data?: unknown }).data;
    if (!data || typeof data !== 'object') return undefined;
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
        const message = (detail as { message?: unknown }).message;
        if (typeof message === 'string') return message;
    }
    return undefined;
}

function errorMessage(error: unknown, fallback: string): string {
    return responseDetail(error) ?? (error instanceof Error ? error.message : fallback);
}

function isPhysicalStartDisabledAfterRevalidation(error: unknown): boolean {
    if (!error || typeof error !== 'object') return false;
    const response = (error as { response?: unknown }).response;
    if (!response || typeof response !== 'object') return false;
    const status = (response as { status?: unknown }).status;
    const detail = responseDetail(error)?.replace(/[.]+$/, '');
    return status === 501
        && detail === 'MinKNOW protocol start remains disabled pending separately authorized supervised commissioning';
}

function runSelectionKey(run: { run_id: string; observed_generation: number }): string {
    return `${run.run_id}\u0000${run.observed_generation}`;
}

export function OntInstrumentPanel({ onAnalyzeExistingData }: OntInstrumentPanelProps) {
    const {
        workspaceId,
        globalExperimentId,
        selectedDomainExperiment,
        availability,
        contextHref,
    } = useGlobalExperimentContext();
    const location = useLocation();
    const queryClient = useQueryClient();
    const exactDomainExperimentId = selectedDomainExperiment?.domain_experiment_id ?? null;
    const requestedRunGeneration = useMemo<SelectedRunGeneration | null>(() => {
        const params = new URLSearchParams(location.search);
        const runId = params.get('run_id')?.trim() ?? '';
        const observedGeneration = Number(params.get('observed_generation'));
        return runId && Number.isInteger(observedGeneration) && observedGeneration > 0
            ? { runId, observedGeneration }
            : null;
    }, [location.search]);
    const requestedPinnedRevisions = useMemo(() => {
        const params = new URLSearchParams(location.search);
        return {
            sample: parseExactUrlRevisionPair(
                params.get('sample_id'),
                params.get('sample_revision_id'),
                'Sample',
            ),
            reference: parseExactUrlRevisionPair(
                params.get('reference_id'),
                params.get('reference_revision_id'),
                'Reference',
            ),
        };
    }, [location.search]);
    const requestedSampleRevision = requestedPinnedRevisions.sample.value;
    const requestedReferenceRevision = requestedPinnedRevisions.reference.value;

    const [selectedPosition, setSelectedPosition] = useState('');
    const [sampleSelection, setSampleSelection] = useState<{ domainId: string; sampleId: string } | null>(null);
    const [selectedRunGeneration, setSelectedRunGeneration] = useState<SelectedRunGeneration | null>(null);
    const [selectedExternalPod5CandidateId, setSelectedExternalPod5CandidateId] = useState('');
    const [rawSignalPreference, setRawSignalPreference] = useState<OntRawSignalPreference>('auto');
    const [waveformReadId, setWaveformReadId] = useState('');
    const [waveform, setWaveform] = useState<OntRawSignalWaveform | null>(null);
    const [waveformError, setWaveformError] = useState<string | null>(null);
    const [waveformLoading, setWaveformLoading] = useState(false);
    const [message, setMessage] = useState('');

    useEffect(() => {
        setSelectedRunGeneration(requestedRunGeneration);
    }, [exactDomainExperimentId, requestedRunGeneration]);

    const deviceStatus = useQuery({
        queryKey: ['ont-device-status'],
        queryFn: async () => (await fetchOntDeviceStatus()).data,
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const data = deviceStatus.isError ? undefined : deviceStatus.data;
    // The API/host already enforce Mk1D-only discovery; the UI never filters eligibility.
    const devices = useMemo(() => data?.live_devices ?? [], [data?.live_devices]);
    const selectedDevice = devices.find((device) => device.position === selectedPosition) ?? devices[0];
    const selectedPositionForQuery = selectedDevice?.position ?? '';
    const protocolOptions = useQuery({
        queryKey: ['ont-protocol-options', selectedPositionForQuery],
        queryFn: async () => (await fetchOntProtocolOptions(selectedPositionForQuery)).data,
        enabled: Boolean(selectedPositionForQuery),
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const instrumentEvidenceError = deviceStatus.isError || protocolOptions.isError;
    const effectiveProtocolOptions = instrumentEvidenceError ? undefined : protocolOptions.data;
    const selectedOption: OntProtocolOption | undefined = effectiveProtocolOptions?.options[0];

    const samplesQuery = useQuery({
        queryKey: ['molbio-ngs-samples', exactDomainExperimentId],
        queryFn: () => fetchMolBioNgsSamples(exactDomainExperimentId as string),
        enabled: exactDomainExperimentId !== null,
        retry: false,
    });
    const currentSamples = useMemo(
        () => (samplesQuery.data ?? []).filter((sample) => sample.current_revision_id !== null),
        [samplesQuery.data],
    );
    const selectedSample = exactDomainExperimentId && sampleSelection?.domainId === exactDomainExperimentId
        ? currentSamples.find((sample) => sample.id === sampleSelection.sampleId) ?? null
        : null;

    const pinnedSampleRevisionQuery = useQuery({
        queryKey: [
            'molbio-ngs-pinned-sample-revision',
            exactDomainExperimentId,
            requestedSampleRevision?.resourceId,
            requestedSampleRevision?.revisionId,
        ],
        queryFn: async () => {
            if (!exactDomainExperimentId || !requestedSampleRevision) {
                throw new Error('Exact Domain Experiment, sample ID, and sample revision ID are required.');
            }
            const revision = await fetchMolBioNgsSampleRevision(
                exactDomainExperimentId,
                requestedSampleRevision.resourceId,
                requestedSampleRevision.revisionId,
            );
            if (
                revision.sample_id !== requestedSampleRevision.resourceId
                || revision.id !== requestedSampleRevision.revisionId
                || revision.global_domain_experiment_id !== exactDomainExperimentId
            ) {
                throw new Error('Pinned sample revision response does not match the exact URL-owned identity.');
            }
            return revision;
        },
        enabled: exactDomainExperimentId !== null && requestedSampleRevision !== null,
        retry: false,
    });
    const pinnedReferenceRevisionQuery = useQuery({
        queryKey: [
            'molbio-ngs-pinned-reference-revision',
            requestedReferenceRevision?.resourceId,
            requestedReferenceRevision?.revisionId,
        ],
        queryFn: async () => {
            if (!requestedReferenceRevision) {
                throw new Error('Reference ID and reference revision ID are required.');
            }
            const revision = await fetchMolBioNgsReferenceRevision(
                requestedReferenceRevision.resourceId,
                requestedReferenceRevision.revisionId,
            );
            if (
                revision.reference_id !== requestedReferenceRevision.resourceId
                || revision.id !== requestedReferenceRevision.revisionId
                || (exactDomainExperimentId !== null && revision.global_domain_experiment_id !== exactDomainExperimentId)
            ) {
                throw new Error('Pinned reference revision response does not match the exact URL-owned identity.');
            }
            return revision;
        },
        enabled: requestedReferenceRevision !== null,
        retry: false,
    });

    const durableRunsQuery = useQuery({
        queryKey: ['ont-instrument-runs', exactDomainExperimentId],
        queryFn: () => fetchOntInstrumentRuns(100),
        enabled: exactDomainExperimentId !== null,
        retry: false,
    });
    const externalPod5CandidatesQuery = useQuery({
        queryKey: ['ont-external-pod5-candidates'],
        queryFn: fetchOntExternalPod5Candidates,
        retry: false,
    });
    const durableRuns = useMemo(
        () => exactDomainExperimentId
            ? (durableRunsQuery.data ?? []).filter((run) => run.experiment_group === exactDomainExperimentId)
            : [],
        [durableRunsQuery.data, exactDomainExperimentId],
    );
    const effectiveSelectedRunGeneration = selectedRunGeneration ?? requestedRunGeneration;
    const selectedGenerationQuery = useQuery({
        queryKey: [
            'ont-instrument-run-generation',
            exactDomainExperimentId,
            effectiveSelectedRunGeneration?.runId,
            effectiveSelectedRunGeneration?.observedGeneration,
        ],
        queryFn: async () => {
            const generation = await fetchOntInstrumentRunGeneration(
                effectiveSelectedRunGeneration?.runId as string,
                effectiveSelectedRunGeneration?.observedGeneration as number,
            );
            if (generation.experiment_group !== exactDomainExperimentId) {
                throw new Error('Persisted run generation is not bound to the exact selected Domain Experiment.');
            }
            return generation;
        },
        enabled: exactDomainExperimentId !== null && effectiveSelectedRunGeneration !== null,
        retry: false,
    });
    const rawSignalQuery = useQuery({
        queryKey: [
            'ont-raw-signal-capabilities',
            effectiveSelectedRunGeneration?.runId,
            effectiveSelectedRunGeneration?.observedGeneration,
            rawSignalPreference,
        ],
        queryFn: () => fetchOntRawSignalCapabilities(
            effectiveSelectedRunGeneration?.runId as string,
            effectiveSelectedRunGeneration?.observedGeneration as number,
            rawSignalPreference,
        ),
        enabled: effectiveSelectedRunGeneration !== null,
        retry: false,
    });

    const refreshDurableRuns = (run?: OntInstrumentRun) => {
        if (run) {
            setSelectedRunGeneration({ runId: run.id, observedGeneration: run.observed_generation });
        }
        void queryClient.invalidateQueries({ queryKey: ['ont-instrument-runs', exactDomainExperimentId] });
        void durableRunsQuery.refetch();
    };

    const registerExternalPod5 = useMutation({
        mutationFn: async () => {
            if (!exactDomainExperimentId) throw new Error('Select an exact Domain Experiment.');
            if (!selectedExternalPod5CandidateId) throw new Error('Select one existing POD5 candidate.');
            return registerOntExternalPod5Candidate(
                selectedExternalPod5CandidateId,
                exactDomainExperimentId,
                selectedSample?.id,
            );
        },
        onSuccess: (registration) => {
            setSelectedRunGeneration({
                runId: registration.run_id,
                observedGeneration: registration.observed_generation,
            });
            setMessage(registration.already_registered
                ? 'Existing POD5 registration reopened.'
                : 'Existing POD5 registered as one sealed run generation.');
            void queryClient.invalidateQueries({ queryKey: ['ont-instrument-runs', exactDomainExperimentId] });
        },
        onError: (error) => setMessage(errorMessage(error, 'Existing POD5 registration failed.')),
    });

    const prepareBlow5 = useMutation({
        mutationFn: async (sourceRepresentationId: string) => {
            if (!effectiveSelectedRunGeneration) throw new Error('Select an exact ONT generation.');
            return requestOntBlow5Preparation(
                effectiveSelectedRunGeneration.runId,
                effectiveSelectedRunGeneration.observedGeneration,
                sourceRepresentationId,
                rawSignalPreference === 'blow5' ? 'blow5' : 'auto',
            );
        },
        onSuccess: (result) => {
            setMessage(`Raw-signal request ${result.job_id}: ${result.state} (${result.reason_code})`);
            void rawSignalQuery.refetch();
        },
    });

    const waveformPoints = useMemo(() => {
        const samples = waveform?.state === 'ready' ? waveform.samples : null;
        if (!samples?.length) return '';
        const min = Math.min(...samples);
        const max = Math.max(...samples);
        return samples.map((value, index) => {
            const x = samples.length > 1 ? (index * 600) / (samples.length - 1) : 0;
            const y = max > min ? 118 - ((value - min) * 116) / (max - min) : 60;
            return `${x},${y}`;
        }).join(' ');
    }, [waveform]);

    const inspectWaveform = async (representationId: string) => {
        if (!effectiveSelectedRunGeneration || !waveformReadId.trim()) return;
        setWaveformLoading(true);
        setWaveform(null);
        setWaveformError(null);
        try {
            let current = await requestOntRawSignalWaveform(
                effectiveSelectedRunGeneration.runId,
                effectiveSelectedRunGeneration.observedGeneration,
                representationId,
                waveformReadId.trim(),
            );
            for (let attempt = 0; attempt < RAW_WAVEFORM_POLL_ATTEMPTS && (current.state === 'requested' || current.state === 'running'); attempt += 1) {
                await new Promise((resolve) => window.setTimeout(resolve, 1000));
                current = await fetchOntRawSignalWaveform(current.lookup_id);
            }
            setWaveform(current);
            if (current.state !== 'ready') setWaveformError(current.reason_code);
        } catch (error) {
            setWaveformError(errorMessage(error, 'Raw waveform lookup failed.'));
        } finally {
            setWaveformLoading(false);
        }
    };

    const reconnectMk1d = useMutation({
        mutationFn: async () => (await requestMk1dReconnect()).data,
        onSuccess: () => void deviceStatus.refetch(),
    });

    const startRun = useMutation({
        mutationFn: async () => {
            if (!exactDomainExperimentId) throw new Error(availability.reason || 'Select an exact NGS/MolBio Domain Experiment.');
            if (!selectedDevice?.position || !selectedOption) throw new Error('No server-issued protocol option is available');
            const intent = await createOntRunIntent(selectedDevice.position, {
                option_id: selectedOption.option_id,
                option_receipt_id: selectedOption.option_receipt_id,
                sample_id: selectedSample?.id,
                // The existing physical-intent contract calls this field experiment_group.
                // It is always the exact selected Domain Experiment ID, never the Global Experiment ID.
                experiment_group: exactDomainExperimentId,
            });
            // Select and refresh the durable BMS ledger immediately. The subsequent
            // physical-start revalidation never becomes the sole run-history authority.
            refreshDurableRuns(intent.data);
            setMessage(`BMS run ${intent.data.id} is armed; revalidating before physical start.`);
            try {
                const started = await startOntRunIntent(intent.data.id, {
                    confirm_start: true,
                    intent_generation: intent.data.observed_generation,
                });
                return { run: started.data, revalidated: false };
            } catch (error) {
                if (isPhysicalStartDisabledAfterRevalidation(error)) {
                    return { run: intent.data, revalidated: true };
                }
                throw error;
            }
        },
        onSuccess: ({ run, revalidated }) => {
            refreshDurableRuns(run);
            setMessage(revalidated
                ? `BMS run ${run.id} remains armed after fresh revalidation; physical MinKNOW start remains disabled.`
                : 'Intent was freshly checked; physical MinKNOW start remains disabled.');
            void deviceStatus.refetch();
        },
        onError: (error) => setMessage(errorMessage(error, 'ONT intent validation failed')),
    });

    const blockers = effectiveProtocolOptions?.blockers ?? [];
    const domainIntentBlocker = exactDomainExperimentId
        ? null
        : (availability.reason || 'Select an exact NGS/MolBio Domain Experiment.');
    const canStart = Boolean(
        !instrumentEvidenceError
        && !domainIntentBlocker
        && selectedDevice?.available_for_run
        && selectedOption
        && effectiveProtocolOptions?.can_start
        && !startRun.isPending
    );
    const availableCount = useMemo(() => devices.filter((device) => device.available_for_run).length, [devices]);
    const selectedGeneration = selectedGenerationQuery.data;

    return (
        <section className="space-y-5 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-100">Mk1D / MinKNOW</span>
                    </div>
                    <p className="max-w-2xl text-sm text-[var(--text-secondary)]">Select a server-discovered Mk1D position and submit its opaque protocol intent. Reconnect is a trusted local BMS-host recovery action; it is not available through Tailnet.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={onAnalyzeExistingData} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">Data Analysis</button>
                    <button
                        type="button"
                        disabled={reconnectMk1d.isPending}
                        onClick={() => {
                            if (window.confirm('Reconnect Mk1D is a fixed recovery action for this local BMS host only. It starts MinKNOW only when inactive and recreates bms-host-agent. It does not start sequencing, a hardware check, alter a flow cell, or restart active MinKNOW. Continue?')) {
                                reconnectMk1d.mutate();
                            }
                        }}
                        className="rounded-lg border border-cyan-500/40 px-3 py-2 text-sm font-semibold text-cyan-100 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {reconnectMk1d.isPending ? 'Reconnecting Mk1D…' : 'Reconnect Mk1D (local host)'}
                    </button>
                </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Project / workspace ID</div><div className="mt-1 break-all font-mono text-sm font-semibold text-[var(--text-primary)]">{workspaceId ?? 'not selected'}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Global Experiment ID</div><div className="mt-1 break-all font-mono text-sm font-semibold text-[var(--text-primary)]">{globalExperimentId ?? 'not selected'}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Domain Experiment ID</div><div className="mt-1 break-all font-mono text-sm font-semibold text-[var(--text-primary)]">{exactDomainExperimentId ?? 'not selected'}</div></div>
            </div>

            <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                <div>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">URL-owned pinned revision inspection</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        Current-head browsing remains separate. Exact pinned sample and reference views are read-only and never advance to current heads.
                    </p>
                </div>
                {requestedPinnedRevisions.sample.error ? (
                    <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">{requestedPinnedRevisions.sample.error} No exact sample revision was fetched.</p>
                ) : null}
                {requestedPinnedRevisions.reference.error ? (
                    <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">{requestedPinnedRevisions.reference.error} No exact reference revision was fetched.</p>
                ) : null}
                {requestedSampleRevision && !exactDomainExperimentId ? (
                    <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Exact pinned sample revision requires the URL-owned Domain Experiment context. No exact sample revision was fetched.</p>
                ) : null}
                {pinnedSampleRevisionQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading exact pinned sample revision…</p> : null}
                {pinnedSampleRevisionQuery.isError ? (
                    <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Exact pinned sample revision could not be loaded: {errorMessage(pinnedSampleRevisionQuery.error, 'unknown pinned sample revision failure')}</p>
                ) : null}
                {pinnedSampleRevisionQuery.data ? (
                    <div className="space-y-2 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3 text-xs text-[var(--text-secondary)]">
                        <div className="font-semibold text-[var(--text-primary)]">Exact pinned sample revision</div>
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                            <div>Sample ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.sample_id}</span></div>
                            <div>Revision ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.id}</span></div>
                            <div>Revision number: <span className="text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.revision_number}</span></div>
                            <div>Domain Experiment ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.global_domain_experiment_id}</span></div>
                            <div>Name: <span className="text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.payload.name}</span></div>
                            <div>Sample kind: <span className="text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.payload.sample_kind}</span></div>
                            <div>Created at: <span className="text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.created_at}</span></div>
                            <div>Payload SHA-256: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedSampleRevisionQuery.data.payload_sha256}</span></div>
                        </div>
                    </div>
                ) : null}
                {pinnedReferenceRevisionQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading exact pinned reference revision…</p> : null}
                {pinnedReferenceRevisionQuery.isError ? (
                    <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Exact pinned reference revision could not be loaded: {errorMessage(pinnedReferenceRevisionQuery.error, 'unknown pinned reference revision failure')}</p>
                ) : null}
                {pinnedReferenceRevisionQuery.data ? (
                    <div className="space-y-2 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3 text-xs text-[var(--text-secondary)]">
                        <div className="font-semibold text-[var(--text-primary)]">Exact pinned reference revision</div>
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                            <div>Reference ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.reference_id}</span></div>
                            <div>Revision ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.id}</span></div>
                            <div>Revision number: <span className="text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.revision_number}</span></div>
                            <div>Domain Experiment ID: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.global_domain_experiment_id}</span></div>
                            <div>Molecule type: <span className="text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.molecule_type}</span></div>
                            <div>Topology: <span className="text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.topology}</span></div>
                            <div>Canonical FASTA SHA-256: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.canonical_fasta_sha256}</span></div>
                            <div>Payload SHA-256: <span className="break-all font-mono text-[var(--text-primary)]">{pinnedReferenceRevisionQuery.data.payload_sha256}</span></div>
                        </div>
                    </div>
                ) : null}
                {!requestedSampleRevision && !requestedReferenceRevision && !requestedPinnedRevisions.sample.error && !requestedPinnedRevisions.reference.error ? (
                    <p className="text-xs text-[var(--text-secondary)]">No exact pinned sample or reference revision was requested in the URL.</p>
                ) : null}
            </div>

            <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Mk1D link</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{deviceStatus.isLoading ? 'Checking…' : statusLabel(data?.implementation_status)}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Positions</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{devices.length}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Available</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{availableCount}</div></div>
            </div>

            {domainIntentBlocker ? <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Run intent disabled: {domainIntentBlocker}</p> : null}
            {instrumentEvidenceError ? <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Current instrument evidence is unavailable after a refresh failure. Intent validation is disabled until a fresh device and protocol response succeeds.</p> : null}

            {reconnectMk1d.isPending || reconnectMk1d.data || reconnectMk1d.isError ? (
                <div className="rounded-xl border border-cyan-500/40 bg-cyan-500/10 p-4 text-sm text-cyan-50">
                    <div className="font-semibold">Recovery receipt</div>
                    {reconnectMk1d.isPending ? <p className="mt-1">Recovery request is in progress. Waiting for the bounded MinKNOW and host-agent stages.</p> : null}
                    {reconnectMk1d.data ? (
                        <div className="mt-2 space-y-1 text-xs">
                            <div>Receipt: {reconnectMk1d.data.receipt.receipt_id} · {reconnectMk1d.data.receipt.status}</div>
                            <div>MinKNOW stage: {reconnectMk1d.data.receipt.minknow}</div>
                            <div>Host-agent recreate: {reconnectMk1d.data.receipt.host_agent_recreate} · read-only health/status verification: {reconnectMk1d.data.receipt.host_agent_health}</div>
                            <div>Post-action device status observed: {reconnectMk1d.data.device_status_observed ? 'yes' : 'no'}</div>
                            {reconnectMk1d.data.connected
                                ? <div>Connection is confirmed by a post-recovery Mk1D observation with no connection error.</div>
                                : <div>Mk1D is not confirmed connected until a post-recovery device status is observed with no connection error.</div>}
                        </div>
                    ) : null}
                    {reconnectMk1d.isError ? <p className="mt-1 text-amber-100">Reconnect request did not produce a safe receipt. Review the local API status and helper installation.</p> : null}
                </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Instrument positions</h3><p className="text-xs text-[var(--text-secondary)]">Only host-approved Mk1D cards are returned.</p></div><button type="button" onClick={() => void deviceStatus.refetch()} className="text-sm text-[var(--accent-secondary)]">Refresh</button></div>
                    {data?.message ? <p className="rounded-lg bg-slate-900/40 p-2 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
                    <div className="grid gap-3 md:grid-cols-2">
                        {devices.length === 0 ? <div className="rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-sm text-[var(--text-secondary)]">No live Mk1D positions reported by MinKNOW.</div> : devices.map((device) => <button key={device.position} type="button" onClick={() => setSelectedPosition(device.position)} className={`rounded-xl border p-4 text-left transition ${selectedDevice?.position === device.position ? 'border-cyan-400 bg-cyan-500/10' : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-cyan-500/50'}`}><div className="text-base font-semibold text-[var(--text-primary)]">{device.position}</div><div className="text-xs text-[var(--text-secondary)]">Mk1D · {deviceStateLabel(device)}</div><div className="mt-3 text-xs text-[var(--text-secondary)]">Flow cell: {device.flow_cell.present ? 'present' : 'absent'} · Running: {device.running ? 'yes' : 'no'}</div></button>)}
                    </div>
                </div>

                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Run intent</h3><p className="text-xs text-[var(--text-secondary)]">Protocol and output policy are server-issued opaque handles.</p></div>
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Current domain sample revision
                        <select
                            value={selectedSample?.id ?? ''}
                            onChange={(event) => setSampleSelection(exactDomainExperimentId && event.target.value ? { domainId: exactDomainExperimentId, sampleId: event.target.value } : null)}
                            disabled={!exactDomainExperimentId || samplesQuery.isLoading}
                            className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
                        >
                            <option value="">No sample selected</option>
                            {currentSamples.map((sample) => <option key={sample.id} value={sample.id}>{sample.id} · revision {sample.current_revision_id}</option>)}
                        </select>
                    </label>
                    {samplesQuery.isError ? <p role="alert" className="text-xs text-amber-100">Exact domain samples could not be loaded: {errorMessage(samplesQuery.error, 'unknown sample query failure')}</p> : null}
                    {selectedSample ? (
                        <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">
                            <div>Stable sample ID sent as existing <code>sample_id</code>: <span className="break-all font-mono text-[var(--text-primary)]">{selectedSample.id}</span></div>
                            <div className="mt-1">Immutable current sample revision ID (context evidence only; not an invented request field): <span className="break-all font-mono text-[var(--text-primary)]">{selectedSample.current_revision_id}</span></div>
                        </div>
                    ) : null}
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Experiment group (exact Domain Experiment ID)
                        <input readOnly value={exactDomainExperimentId ?? ''} placeholder="Select an exact Domain Experiment" className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)]" />
                    </label>
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">{selectedOption ? `${selectedOption.protocol_label} · ${selectedOption.output_policy_label}` : 'No protocol option is currently available.'}{blockers.length ? <div className="mt-2 text-amber-100">Preflight blockers: {blockers.join(', ')}</div> : null}</div>
                    <button type="button" disabled={!canStart} title={domainIntentBlocker ?? undefined} onClick={() => startRun.mutate()} className="rounded-lg bg-[var(--accent-secondary)] px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Validate run intent</button>
                    {message ? <p className="text-xs text-[var(--text-secondary)]">{message}</p> : null}
                </div>
            </div>

            <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                <div>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Register existing POD5</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Select one server-governed POD5. BMS records its exact bytes and keeps the source unchanged.</p>
                </div>
                {externalPod5CandidatesQuery.isError ? <p role="alert" className="text-sm text-amber-100">Existing POD5 candidates are unavailable: {errorMessage(externalPod5CandidatesQuery.error, 'unknown candidate-list failure')}</p> : null}
                <select
                    aria-label="Existing POD5 candidate"
                    value={selectedExternalPod5CandidateId}
                    onChange={(event) => setSelectedExternalPod5CandidateId(event.target.value)}
                    disabled={externalPod5CandidatesQuery.isLoading || registerExternalPod5.isPending}
                    className="w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
                >
                    <option value="">Select one existing POD5…</option>
                    {(externalPod5CandidatesQuery.data?.candidates ?? []).map((candidate) => (
                        <option key={candidate.candidate_id} value={candidate.candidate_id}>
                            {candidate.display_name} · {(candidate.size_bytes / (1024 ** 3)).toFixed(2)} GiB
                        </option>
                    ))}
                </select>
                <button
                    type="button"
                    disabled={!exactDomainExperimentId || !selectedExternalPod5CandidateId || registerExternalPod5.isPending}
                    onClick={() => registerExternalPod5.mutate()}
                    className="rounded-lg border border-cyan-500/50 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                    {registerExternalPod5.isPending ? 'Registering immutable POD5…' : 'Register selected POD5'}
                </button>
            </div>

            <div className="space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Durable BMS ONT run ledger</h3>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">This persisted-only list reads the BMS ledger. Loading or reopening it does not contact MinKNOW.</p>
                    </div>
                    <button type="button" disabled={!exactDomainExperimentId} onClick={() => void durableRunsQuery.refetch()} className="text-sm text-[var(--accent-secondary)] disabled:cursor-not-allowed disabled:opacity-50">Refresh ledger</button>
                </div>
                {durableRunsQuery.isError ? <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Durable ONT runs could not be loaded: {errorMessage(durableRunsQuery.error, 'unknown run-ledger failure')}</p> : null}
                <label className="block text-xs font-semibold text-[var(--text-secondary)]">Persisted run and observed generation
                    <select
                        value={effectiveSelectedRunGeneration ? runSelectionKey({ run_id: effectiveSelectedRunGeneration.runId, observed_generation: effectiveSelectedRunGeneration.observedGeneration }) : ''}
                        onChange={(event) => {
                            const run = durableRuns.find((candidate) => runSelectionKey(candidate) === event.target.value);
                            setSelectedRunGeneration(run ? { runId: run.run_id, observedGeneration: run.observed_generation } : null);
                        }}
                        disabled={!exactDomainExperimentId || durableRunsQuery.isLoading}
                        className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
                    >
                        <option value="">Select a persisted run generation…</option>
                        {durableRuns.map((run: OntRunSummary) => <option key={runSelectionKey(run)} value={runSelectionKey(run)}>{run.run_id} · {run.status} · generation {run.observed_generation}</option>)}
                    </select>
                </label>
                {requestedRunGeneration ? <p className="text-xs text-[var(--text-secondary)]">Reopening URL-owned exact run generation {requestedRunGeneration.runId} · {requestedRunGeneration.observedGeneration}.</p> : null}
                {selectedGenerationQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading exact persisted run generation…</p> : null}
                {selectedGenerationQuery.isError ? <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Exact run generation could not be reopened: {errorMessage(selectedGenerationQuery.error, 'unknown generation query failure')}</p> : null}
                {selectedGeneration ? (
                    <div className="space-y-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-xs text-[var(--text-secondary)]">
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <div><div className="uppercase tracking-wide">Run ID</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedGeneration.run_id}</div></div>
                            <div><div className="uppercase tracking-wide">Status</div><div className="mt-1 text-[var(--text-primary)]">{selectedGeneration.status}</div></div>
                            <div><div className="uppercase tracking-wide">Observed generation</div><div className="mt-1 text-[var(--text-primary)]">{selectedGeneration.observed_generation}</div></div>
                            <div><div className="uppercase tracking-wide">Position ID</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedGeneration.position_id}</div></div>
                            <div><div className="uppercase tracking-wide">Created at</div><div className="mt-1 text-[var(--text-primary)]">{selectedGeneration.created_at}</div></div>
                            <div><div className="uppercase tracking-wide">Observed at</div><div className="mt-1 text-[var(--text-primary)]">{selectedGeneration.observed_at}</div></div>
                            <div><div className="uppercase tracking-wide">Stable sample ID</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedGeneration.sample_id ?? 'not bound'}</div></div>
                            <div><div className="uppercase tracking-wide">Experiment group / Domain ID</div><div className="mt-1 break-all font-mono text-[var(--text-primary)]">{selectedGeneration.experiment_group ?? 'not bound'}</div></div>
                        </div>
                        <div>Terminal manifest SHA-256: <span className="break-all font-mono text-[var(--text-primary)]">{selectedGeneration.terminal_manifest_sha256 ?? 'not published'}</span></div>

                        <div className="space-y-3 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3">
                            <div className="flex flex-wrap items-end justify-between gap-3">
                                <div>
                                    <div className="font-semibold text-[var(--text-primary)]">Raw-signal representations</div>
                                    <div>Readiness is exact-generation scoped. IGV stays independent.</div>
                                </div>
                                <label className="text-xs font-semibold text-[var(--text-secondary)]">Representation preference
                                    <select
                                        value={rawSignalPreference}
                                        onChange={(event) => setRawSignalPreference(event.target.value as OntRawSignalPreference)}
                                        className="ml-2 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-[var(--text-primary)]"
                                    >
                                        <option value="auto">Auto</option>
                                        <option value="pod5">POD5</option>
                                        <option value="blow5">Indexed BLOW5</option>
                                    </select>
                                </label>
                            </div>
                            {rawSignalQuery.isLoading ? <div>Loading raw-signal capability state…</div> : null}
                            {rawSignalQuery.isError ? <div role="alert" className="text-amber-100">Raw-signal state unavailable: {errorMessage(rawSignalQuery.error, 'unknown capability failure')}</div> : null}
                            {rawSignalQuery.data ? (
                                <>
                                    <div>Selected: <span className="font-mono text-[var(--text-primary)]">{rawSignalQuery.data.selected_format ?? 'none'}</span> · {rawSignalQuery.data.selection_reason_code}</div>
                                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                                        {Object.entries(rawSignalQuery.data.modes).map(([mode, capability]) => (
                                            <div key={mode} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2">
                                                <div className="font-mono text-[var(--text-primary)]">{mode}</div>
                                                <div className={capability.state === 'ready' ? 'text-emerald-300' : capability.state === 'preparable' ? 'text-amber-200' : 'text-[var(--text-secondary)]'}>{capability.state}</div>
                                                <div className="break-words">{capability.reason_code}</div>
                                            </div>
                                        ))}
                                    </div>
                                    {rawSignalQuery.data.representations.map((representation) => {
                                        const semantic = representation.validation_receipts.semantic;
                                        const containerDigest = typeof representation.runtime_identity.container_digest === 'string'
                                            ? representation.runtime_identity.container_digest
                                            : null;
                                        return (
                                            <div key={representation.representation_id} className="space-y-2 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2">
                                                <div>
                                                    <span className="font-semibold text-[var(--text-primary)]">{representation.format.toUpperCase()}</span>
                                                    {' '}· {representation.role} · {representation.state} · {representation.reason_code}
                                                </div>
                                                <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)]">
                                                    <summary className="cursor-pointer select-none px-3 py-2 font-semibold text-[var(--text-secondary)]">
                                                        Technical details
                                                    </summary>
                                                    <div className="space-y-2 border-t border-[var(--border-primary)] p-3" aria-label="Raw-signal technical details">
                                                        <div className="break-all font-mono">{representation.representation_id} · manifest {representation.manifest_sha256}</div>
                                                        {representation.role === 'derived' ? (
                                                            <div className="space-y-2 rounded border border-emerald-500/30 bg-emerald-500/5 p-2" aria-label="Raw-signal publication receipt">
                                                                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Publication</span><div>{representation.published_at ?? 'not published'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Reads</span><div>{representation.read_count ?? 'unknown'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Partitions</span><div>{semantic?.partition_count ?? 'unknown'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Indexed lookups</span><div>{semantic?.indexed_lookup_count ?? 'unknown'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Semantic receipt</span><div>{semantic?.status ?? 'missing'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Signal contract</span><div>{semantic?.signal_samples ?? 'unknown'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Duplicate reads</span><div>{semantic?.duplicate_read_ids ?? 'unknown'}</div></div>
                                                                    <div><span className="font-semibold text-[var(--text-primary)]">Compared samples</span><div>{semantic?.total_signal_samples_compared ?? 'unknown'}</div></div>
                                                                </div>
                                                                <div>Adjacent indexes: <span className="font-semibold text-[var(--text-primary)]">{representation.validation_receipts.adjacent_index ? 'validated' : 'not validated'}</span></div>
                                                                <div>Mapping contract: <span className="break-all font-mono text-[var(--text-primary)]">{semantic?.mapping_contract ?? 'unknown'}</span></div>
                                                                <div>Parent representation: <span className="break-all font-mono text-[var(--text-primary)]">{representation.parent_representation_ids.join(', ') || 'none'}</span></div>
                                                                <div>Parent manifest: <span className="break-all font-mono text-[var(--text-primary)]">{representation.parent_manifest_sha256s.join(', ') || 'none'}</span></div>
                                                                <div>Runtime: <span className="break-all font-mono text-[var(--text-primary)]">{representation.profile_id ?? 'unknown'}{containerDigest ? ` · ${containerDigest}` : ''}</span></div>
                                                                <div className="space-y-1">
                                                                    <div className="font-semibold text-[var(--text-primary)]">Published artifacts ({representation.artifact_count})</div>
                                                                    {representation.artifacts.map((artifact) => (
                                                                        <div key={artifact.artifact_id ?? `${artifact.kind}-${artifact.sha256}`} className="rounded border border-[var(--border-primary)] p-2">
                                                                            <div><span className="font-semibold text-[var(--text-primary)]">{artifact.kind ?? 'artifact'}</span>{artifact.read_count !== undefined ? ` · ${artifact.read_count} reads` : ''}{artifact.bytes !== undefined ? ` · ${artifact.bytes} bytes` : ''}</div>
                                                                            {artifact.partition_fingerprint ? <div>Partition: <span className="break-all font-mono text-[var(--text-primary)]">{artifact.partition_fingerprint}</span></div> : null}
                                                                            <div>SHA-256: <span className="break-all font-mono text-[var(--text-primary)]">{artifact.sha256 ?? 'missing'}</span></div>
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                </details>
                                                {representation.state !== 'ready' && (representation.format === 'pod5' || representation.format === 'blow5') ? (
                                                    <button
                                                        type="button"
                                                        onClick={() => prepareBlow5.mutate(representation.representation_id)}
                                                        disabled={prepareBlow5.isPending}
                                                        className="mt-2 rounded border border-cyan-500/50 px-2 py-1 font-semibold text-cyan-200 disabled:opacity-40"
                                                    >
                                                        {representation.format === 'blow5' ? 'Validate indexed BLOW5' : 'Prepare indexed BLOW5'}
                                                    </button>
                                                ) : null}
                                            </div>
                                        );
                                    })}
                                    {(() => {
                                        const readyBlow5 = rawSignalQuery.data.representations.find(
                                            (representation) => representation.format === 'blow5' && representation.state === 'ready',
                                        );
                                        if (!readyBlow5) return null;
                                        return (
                                            <div className="space-y-2 rounded border border-emerald-500/40 bg-emerald-500/5 p-3">
                                                <div className="font-semibold text-[var(--text-primary)]">Indexed BLOW5 waveform inspection</div>
                                                <div className="flex flex-wrap gap-2">
                                                    <input
                                                        aria-label="Raw waveform read ID"
                                                        value={waveformReadId}
                                                        onChange={(event) => setWaveformReadId(event.target.value)}
                                                        placeholder="Exact read ID"
                                                        className="min-w-[20rem] flex-1 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 font-mono text-xs text-[var(--text-primary)]"
                                                    />
                                                    <button
                                                        type="button"
                                                        onClick={() => void inspectWaveform(readyBlow5.representation_id)}
                                                        disabled={!waveformReadId.trim() || waveformLoading}
                                                        className="rounded border border-emerald-500/50 px-2 py-1 font-semibold text-emerald-200 disabled:opacity-40"
                                                    >
                                                        {waveformLoading ? 'Inspecting…' : 'Inspect raw waveform'}
                                                    </button>
                                                </div>
                                                {waveformError ? <div role="alert" className="text-amber-100">Waveform unavailable: {waveformError}</div> : null}
                                                {waveform?.state === 'ready' && waveformPoints ? (
                                                    <div className="space-y-1">
                                                        <div>Read <span className="font-mono text-[var(--text-primary)]">{waveform.read_id}</span> · {waveform.sample_count} source samples · {waveform.samples?.length ?? 0} displayed</div>
                                                        <svg aria-label="Raw electrical signal waveform" viewBox="0 0 600 120" className="h-36 w-full rounded bg-slate-950" preserveAspectRatio="none">
                                                            <polyline points={waveformPoints} fill="none" stroke="rgb(52 211 153)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                                                        </svg>
                                                    </div>
                                                ) : null}
                                            </div>
                                        );
                                    })()}
                                </>
                            ) : null}
                        </div>

                        <Link
                            to={contextHref('/ngs', {
                                section: 'instrument-runs',
                                run_id: selectedGeneration.run_id,
                                observed_generation: String(selectedGeneration.observed_generation),
                            })}
                            className="inline-flex rounded-lg border border-[var(--border-primary)] px-3 py-2 font-semibold text-[var(--accent-secondary)] hover:bg-[var(--bg-tertiary)]"
                        >
                            Reopen exact persisted generation
                        </Link>
                    </div>
                ) : null}
            </div>
        </section>
    );
}
