import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import {
    createOntRunIntent,
    fetchMolBioNgsReferenceRevision,
    fetchMolBioNgsSampleRevision,
    fetchMolBioNgsSummaries,
    fetchOntDeviceStatus,
    fetchOntExternalPod5Candidates,
    fetchOntInstrumentRunGeneration,
    fetchOntInstrumentRuns,
    fetchOntRawSignalCapabilities,
    fetchOntRawSignalWaveform,
    requestOntBlow5Preparation,
    requestOntRawSignalWaveform,
    registerOntExternalPod5Candidate,
    importOntRawSignalUpload,
    uploadFile,
    fetchOntProtocolOptions,
    requestMk1dReconnect,
    startOntRunIntent,
    stopOntInstrumentRun,
    type OntInstrumentRun,
    type OntLiveDevice,
    type OntProtocolOption,
    type OntRawSignalPreference,
    type OntRawSignalWaveform,
    type OntRunSummary,
} from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

import { rawSignalWaveformPoints } from './RawReadInspector';

const RAW_WAVEFORM_POLL_ATTEMPTS = 130;
const RUN_HISTORY_PAGE_SIZE = 100;

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: () => void;
    rawSignalOnly?: boolean;
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

function runSelectionKey(run: { run_id: string; observed_generation: number }): string {
    return `${run.run_id}\u0000${run.observed_generation}`;
}

export function OntInstrumentPanel({ onAnalyzeExistingData, rawSignalOnly = false }: OntInstrumentPanelProps) {
    const {
        selectedDomainExperiment,
        contextHref,
    } = useGlobalExperimentContext();
    const location = useLocation();
    const queryClient = useQueryClient();
    const exactDomainExperimentId = selectedDomainExperiment?.domain_experiment_id ?? null;
    const currentDomainId = useRef(exactDomainExperimentId);
    currentDomainId.current = exactDomainExperimentId;
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
    const [selectedKit, setSelectedKit] = useState('');
    const [runHours, setRunHours] = useState(24);
    const [sampleSelection, setSampleSelection] = useState<{ domainId: string; sampleId: string } | null>(null);
    const [selectedRunGeneration, setSelectedRunGeneration] = useState<SelectedRunGeneration | null>(null);
    const [selectedExternalPod5CandidateId, setSelectedExternalPod5CandidateId] = useState('');
    const [rawSourceMode, setRawSourceMode] = useState<'upload' | 'existing'>('upload');
    const [rawUpload, setRawUpload] = useState<File | null>(null);
    const [rawIndexUpload, setRawIndexUpload] = useState<File | null>(null);
    const uploadedRawFiles = useRef(new Map<File, string>());
    const rawUploadDirectory = useRef('');
    if (!rawUploadDirectory.current) rawUploadDirectory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`;
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
        enabled: !rawSignalOnly,
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const data = deviceStatus.isError ? undefined : deviceStatus.data;
    // The API/host already enforce Mk1D-only discovery; the UI never filters eligibility.
    const devices = useMemo(() => data?.live_devices ?? [], [data?.live_devices]);
    const selectedDevice = devices.find((device) => device.position === selectedPosition) ?? devices[0];
    const selectedPositionForQuery = selectedDevice?.position ?? '';
    const protocolOptions = useQuery({
        queryKey: ['ont-protocol-options', selectedPositionForQuery, selectedKit, runHours],
        queryFn: async () => (await fetchOntProtocolOptions(selectedPositionForQuery, { kit: selectedKit, run_hours: runHours })).data,
        enabled: !rawSignalOnly && Boolean(selectedPositionForQuery),
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const instrumentEvidenceError = deviceStatus.isError || protocolOptions.isError;
    const effectiveProtocolOptions = instrumentEvidenceError ? undefined : protocolOptions.data;
    const selectedOption: OntProtocolOption | undefined = effectiveProtocolOptions?.options[0];

    const samplesQuery = useInfiniteQuery({
        queryKey: ['molbio-ngs-summaries', exactDomainExperimentId, 'samples'],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam, signal }) => fetchMolBioNgsSummaries(exactDomainExperimentId as string, 'samples', { limit: 50, cursor: pageParam }, signal),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
        enabled: exactDomainExperimentId !== null,
        retry: false,
    });
    const currentSamples = useMemo(
        () => (samplesQuery.data?.pages.flatMap((page) => page.items) ?? []).filter((sample) => Boolean(sample.current_revision_id)),
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

    const durableRunsQuery = useInfiniteQuery({
        queryKey: ['ont-instrument-runs', exactDomainExperimentId],
        initialPageParam: 0,
        queryFn: ({ pageParam }) => fetchOntInstrumentRuns(RUN_HISTORY_PAGE_SIZE, {
            experimentGroup: exactDomainExperimentId as string,
            offset: pageParam,
        }),
        getNextPageParam: (lastPage, pages) => lastPage.length < RUN_HISTORY_PAGE_SIZE
            ? undefined
            : pages.reduce((count, page) => count + page.length, 0),
        enabled: exactDomainExperimentId !== null,
        retry: false,
        refetchInterval: (query) => query.state.data?.pages.some((page) => page.some((run) =>
            run.experiment_group === exactDomainExperimentId && ['starting', 'running', 'stopping', 'unknown'].includes(run.status)))
            ? jobPollingInterval(3000, query) : false,
    });
    const externalPod5CandidatesQuery = useQuery({
        queryKey: ['ont-external-pod5-candidates'],
        queryFn: fetchOntExternalPod5Candidates,
        retry: false,
    });
    const durableRuns = useMemo(
        () => exactDomainExperimentId
            ? (durableRunsQuery.data?.pages.flat() ?? []).filter((run) => run.experiment_group === exactDomainExperimentId)
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
        refetchInterval: (query) => query.state.data?.representations.some((item) => item.format === 'blow5' && item.state === 'ready')
            ? false
            : query.state.data?.representations.some((item) => item.format === 'pod5' || item.format === 'blow5')
                ? jobPollingInterval(2000, query) : false,
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

    const importRawSignal = useMutation({
        onMutate: () => ({ domainId: exactDomainExperimentId }),
        mutationFn: async () => {
            if (!exactDomainExperimentId || !rawUpload) throw new Error('Choose a Project and a raw-signal file.');
            const format = rawUpload.name.split('.').pop()?.toLowerCase();
            if (format !== 'pod5' && format !== 'slow5' && format !== 'blow5') throw new Error('Select POD5, SLOW5 or BLOW5.');
            if (format === 'blow5' && !rawIndexUpload) throw new Error('Select the matching BLOW5 index.');
            const directory = rawUploadDirectory.current;
            const uploaded = uploadedRawFiles.current;
            const upload = async (file: File, name: string) => {
                let path = uploaded.get(file);
                if (!path) {
                    path = (await uploadFile(directory, new File([file], name))).data.path;
                    uploaded.set(file, path);
                }
                return path;
            };
            const name = `source.${format}`;
            return importOntRawSignalUpload({
                file_path: await upload(rawUpload, name), format,
                index_file_path: format === 'blow5' && rawIndexUpload ? await upload(rawIndexUpload, name + '.idx') : null,
                experiment_group: exactDomainExperimentId, sample_id: selectedSample?.id ?? null,
            });
        },
        onSuccess: (registration, _variables, context) => {
            void queryClient.invalidateQueries({ queryKey: ['ont-instrument-runs', context?.domainId] });
            if (currentDomainId.current !== context?.domainId) return;
            setSelectedRunGeneration({ runId: registration.run_id, observedGeneration: registration.observed_generation });
            setMessage('Raw-signal source registered. Its validation and readiness are shown below.');
        },
    });

    useEffect(() => {
        setRawUpload(null);
        setRawIndexUpload(null);
        setMessage('');
        importRawSignal.reset();
        uploadedRawFiles.current = new Map();
        rawUploadDirectory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`;
    }, [exactDomainExperimentId, importRawSignal.reset]);

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

    const waveformPoints = useMemo(() => rawSignalWaveformPoints(waveform), [waveform]);

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
            if (!exactDomainExperimentId) throw new Error('Choose a Project to start sequencing.');
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
            setMessage(`Starting run ${intent.data.id} in MinKNOW…`);
            const started = await startOntRunIntent(intent.data.id, {
                confirm_start: true,
                intent_generation: intent.data.observed_generation,
            });
            return started.data;
        },
        onSuccess: (run) => {
            refreshDurableRuns(run);
            setMessage(`Run ${run.id} started in MinKNOW (${run.status}).`);
            void deviceStatus.refetch();
        },
        onError: (error) => {
            setMessage(errorMessage(error, 'The run could not be started'));
            void durableRunsQuery.refetch();
        },
    });

    const stopRun = useMutation({
        mutationFn: async (runId: string) => (await stopOntInstrumentRun(runId)).data,
        onSuccess: (run) => {
            refreshDurableRuns(run);
            setMessage(`Stop sent to MinKNOW for run ${run.id} (${run.status}). Data collected so far is kept.`);
            void deviceStatus.refetch();
        },
        onError: (error) => setMessage(errorMessage(error, 'The run could not be stopped')),
    });

    const blockers = effectiveProtocolOptions?.blockers ?? [];
    const domainIntentBlocker = exactDomainExperimentId
        ? null
        : 'Choose a Project (Projects button above) to start sequencing.';
    const canStart = Boolean(
        !instrumentEvidenceError
        && !domainIntentBlocker
        && selectedDevice?.available_for_run
        && selectedOption
        && effectiveProtocolOptions?.can_start
        && !startRun.isPending
    );
    const availableKits = effectiveProtocolOptions?.available_kits ?? [];
    const activeRuns = durableRuns.filter((run) => run.can_stop || run.status === 'stopping');
    const minknowReachable = !deviceStatus.isLoading && data?.implementation_status === 'configured';
    const availableCount = useMemo(() => devices.filter((device) => device.available_for_run).length, [devices]);
    const selectedGeneration = selectedGenerationQuery.data;

    return (
        <section className="space-y-5 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            {rawSignalOnly ? <div className="space-y-2 text-[var(--text-primary)]"><h2 className="text-xl font-semibold">Raw signal tools</h2><p className="text-sm text-[var(--text-secondary)]">POD5 and indexed BLOW5: source registration, SLOW5tools conversion, read lookup and waveform inspection. Open an alignment result’s Read &amp; Signal Workbench for Squigualiser views, mapping, calibration and Squigulator ideal trace comparison.</p><button type="button" onClick={onAnalyzeExistingData} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--accent-primary)]">Open data analysis</button></div> : null}
            {!rawSignalOnly ? <>
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-[color-mix(in_srgb,var(--accent-primary)_40%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_10%,transparent)] px-2 py-1 text-xs font-semibold uppercase tracking-wide text-[var(--accent-primary)]">Mk1D / MinKNOW</span>
                    </div>
                    <p className="max-w-2xl text-sm text-[var(--text-secondary)]">Pick a Mk1D position, choose the kit and run length, and start sequencing. BNS tracks the run and picks up its reads when it finishes. Reconnect only works on this computer, not over Tailnet.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={onAnalyzeExistingData} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">Data Analysis</button>
                    <button
                        type="button"
                        disabled={reconnectMk1d.isPending}
                        onClick={() => {
                            if (window.confirm('Reconnect Mk1D is a fixed recovery action for this local BNS host only. It starts MinKNOW only when inactive and recreates bms-host-agent. It does not start sequencing, a hardware check, alter a flow cell, or restart active MinKNOW. Continue?')) {
                                reconnectMk1d.mutate();
                            }
                        }}
                        className="rounded-lg border border-[color-mix(in_srgb,var(--accent-primary)_40%,transparent)] px-3 py-2 text-sm font-semibold text-[var(--accent-primary)] hover:bg-[color-mix(in_srgb,var(--accent-primary)_10%,transparent)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {reconnectMk1d.isPending ? 'Reconnecting Mk1D…' : 'Reconnect Mk1D (local host)'}
                    </button>
                </div>
            </div>

            {requestedSampleRevision || requestedReferenceRevision || requestedPinnedRevisions.sample.error || requestedPinnedRevisions.reference.error ? (
            <details className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4" open>
                <summary className="cursor-pointer text-sm font-semibold text-[var(--text-primary)]">Saved sample and reference versions</summary>
                <div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        These saved versions are read-only. Opening this link keeps the recorded versions selected.
                    </p>
                </div>
                {requestedPinnedRevisions.sample.error ? (
                    <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">{requestedPinnedRevisions.sample.error} No exact sample revision was fetched.</p>
                ) : null}
                {requestedPinnedRevisions.reference.error ? (
                    <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">{requestedPinnedRevisions.reference.error} No exact reference revision was fetched.</p>
                ) : null}
                {requestedSampleRevision && !exactDomainExperimentId ? (
                    <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Exact pinned sample revision requires the URL-owned Domain Experiment context. No exact sample revision was fetched.</p>
                ) : null}
                {pinnedSampleRevisionQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading exact pinned sample revision…</p> : null}
                {pinnedSampleRevisionQuery.isError ? (
                    <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Exact pinned sample revision could not be loaded: {errorMessage(pinnedSampleRevisionQuery.error, 'unknown pinned sample revision failure')}</p>
                ) : null}
                {pinnedSampleRevisionQuery.data ? (
                    <div className="space-y-2 rounded-lg border border-[color-mix(in_srgb,var(--accent-primary)_30%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_5%,transparent)] p-3 text-xs text-[var(--text-secondary)]">
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
                    <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Exact pinned reference revision could not be loaded: {errorMessage(pinnedReferenceRevisionQuery.error, 'unknown pinned reference revision failure')}</p>
                ) : null}
                {pinnedReferenceRevisionQuery.data ? (
                    <div className="space-y-2 rounded-lg border border-[color-mix(in_srgb,var(--accent-primary)_30%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_5%,transparent)] p-3 text-xs text-[var(--text-secondary)]">
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
            </details>
            ) : null}

            <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Mk1D link</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{deviceStatus.isLoading ? 'Checking…' : statusLabel(data?.implementation_status)}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Positions</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{minknowReachable ? devices.length : '—'}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Available</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{minknowReachable ? availableCount : '—'}</div></div>
            </div>

            {domainIntentBlocker ? <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">{domainIntentBlocker}</p> : null}
            {instrumentEvidenceError ? <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Device or protocol status could not be refreshed. Refresh the connection before starting sequencing.</p> : null}

            {reconnectMk1d.isPending || reconnectMk1d.data || reconnectMk1d.isError ? (
                <div className="rounded-xl border border-[color-mix(in_srgb,var(--accent-primary)_40%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_10%,transparent)] p-4 text-sm text-[var(--accent-primary)]">
                    <div className="font-semibold">Reconnect result</div>
                    {reconnectMk1d.isPending ? <p className="mt-1">Reconnecting MinKNOW and the host agent…</p> : null}
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
                    {reconnectMk1d.isError ? <p className="mt-1 text-[var(--warning)]">Reconnect failed. Check the local API and reconnect helper.</p> : null}
                </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Instrument positions</h3><p className="text-xs text-[var(--text-secondary)]">Mk1D positions reported by MinKNOW.</p></div><button type="button" onClick={() => void deviceStatus.refetch()} className="text-sm text-[var(--text-action)]">Refresh</button></div>
                    {data?.message ? <p className="rounded-lg bg-[var(--bg-secondary)] p-2 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
                    <div className="grid gap-3 md:grid-cols-2">
                        {devices.length === 0 && !data?.message ? <div className="rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-sm text-[var(--text-secondary)] md:col-span-2">{minknowReachable ? 'No Mk1D positions found. Check the device is plugged in, then Refresh.' : 'MinKNOW is not reachable. Use Reconnect Mk1D, then Refresh.'}</div> : devices.map((device) => <button key={device.position} type="button" onClick={() => setSelectedPosition(device.position)} className={`rounded-xl border p-4 text-left transition ${selectedDevice?.position === device.position ? 'border-[var(--accent-primary)] bg-[color-mix(in_srgb,var(--accent-primary)_10%,transparent)]' : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-[color-mix(in_srgb,var(--accent-primary)_50%,transparent)]'}`}><div className="text-base font-semibold text-[var(--text-primary)]">{device.position}</div><div className="text-xs text-[var(--text-secondary)]">Mk1D · {deviceStateLabel(device)}</div><div className="mt-3 text-xs text-[var(--text-secondary)]">Flow cell: {device.flow_cell.present ? 'present' : 'absent'} · Running: {device.running ? 'yes' : 'no'}</div></button>)}
                    </div>
                </div>

                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">New sequencing run</h3><p className="text-xs text-[var(--text-secondary)]">BNS picks the matching MinKNOW protocol and default basecalling model for the kit and flow cell.</p></div>
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Sample
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
                    {samplesQuery.data && <p className="text-xs">Loaded {samplesQuery.data.pages.reduce((count, page) => count + page.items.length, 0)} of {samplesQuery.data.pages[0].total} samples (only revision-bearing samples are selectable).</p>}
                    {samplesQuery.hasNextPage && <button type="button" disabled={samplesQuery.isFetchingNextPage} onClick={() => void samplesQuery.fetchNextPage()}>Load more samples</button>}
                    {samplesQuery.isError ? <p role="alert" className="text-xs text-[var(--warning)]">Exact domain samples could not be loaded: {errorMessage(samplesQuery.error, 'unknown sample query failure')}</p> : null}
                    {selectedSample ? (
                        <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">
                            <div>Stable sample ID sent as existing <code>sample_id</code>: <span className="break-all font-mono text-[var(--text-primary)]">{selectedSample.id}</span></div>
                            <div className="mt-1">Sample revision (for reference): <span className="break-all font-mono text-[var(--text-primary)]">{selectedSample.current_revision_id}</span></div>
                        </div>
                    ) : null}
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Sequencing kit
                        <select
                            aria-label="Sequencing kit"
                            value={selectedKit}
                            onChange={(event) => setSelectedKit(event.target.value)}
                            className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                        >
                            <option value="">{availableKits.length ? 'Select the kit used for this library…' : effectiveProtocolOptions?.flow_cell_present ? 'No compatible sequencing kits reported' : 'Insert a flow cell to list kits'}</option>
                            {availableKits.map((kit) => <option key={kit} value={kit}>{kit}</option>)}
                        </select>
                    </label>
                    {effectiveProtocolOptions?.flow_cell_present && !availableKits.length ? (
                        <p role="status" className="text-xs text-[var(--text-secondary)]">MinKNOW detects this cell but offers no sequencing kits for it. Test cells are used for hardware checks in MinKNOW. DNA and RNA sequencing kits appear with their matching sequencing cell.</p>
                    ) : null}
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Run length (hours)
                        <input
                            aria-label="Run length in hours"
                            type="number"
                            min={0.5}
                            max={96}
                            step={0.5}
                            value={runHours}
                            onChange={(event) => setRunHours(Math.min(96, Math.max(0.5, Number(event.target.value) || 24)))}
                            className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                        />
                    </label>
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">{selectedOption ? `${selectedOption.protocol_label} · basecalling ${selectedOption.basecalling_enabled ? 'on' : 'off'}` : selectedKit ? 'MinKNOW has no sequencing protocol for this kit and flow cell.' : 'Choose a kit to prepare the run.'}{blockers.length ? <div className="mt-2 text-[var(--warning)]">Not ready: {blockers.join(', ')}</div> : null}</div>
                    <button
                        type="button"
                        disabled={!canStart}
                        title={domainIntentBlocker ?? undefined}
                        onClick={() => {
                            if (window.confirm(`Start sequencing on ${selectedDevice?.position} with ${selectedKit} for ${runHours} h? This uses the flow cell.`)) startRun.mutate();
                        }}
                        className="rounded-lg bg-[var(--accent-primary)] px-4 py-2 text-sm font-semibold text-[var(--text-on-accent)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {startRun.isPending ? 'Starting…' : 'Start sequencing'}
                    </button>
                    {activeRuns.map((run) => (
                        <button
                            key={run.run_id}
                            type="button"
                            disabled={stopRun.isPending || run.status === 'stopping'}
                            onClick={() => {
                                if (window.confirm(`Stop run ${run.run_id}? Data collected so far is kept.`)) stopRun.mutate(run.run_id);
                            }}
                            className="w-full rounded-lg border border-[color-mix(in_srgb,var(--error)_50%,transparent)] px-4 py-2 text-sm font-semibold text-[var(--error)] hover:bg-[color-mix(in_srgb,var(--error)_10%,transparent)] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {run.status === 'stopping' ? `Stopping ${run.run_id}…` : `Stop run ${run.run_id} (${run.status})`}
                        </button>
                    ))}
                    {message ? <p className="text-xs text-[var(--text-secondary)]">{message}</p> : null}
                </div>
            </div>

            </> : null}
            <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4 text-[var(--text-primary)]">
                <div>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Add raw signal</h3>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Register POD5, SLOW5 or indexed BLOW5 in the selected Project. File provenance and validation stay in the ONT ledger. SLOW5 and POD5 are kept as uploaded and can be converted to indexed BLOW5, which the waveform and Squigualiser views read.</p>
                </div>
                {!exactDomainExperimentId && rawSignalOnly ? <p role="status" className="text-sm text-[var(--text-secondary)]">Choose a Project (Projects button above) to import raw signal.</p> : null}
                <div role="radiogroup" aria-label="Raw-signal source" className="flex flex-wrap gap-2">
                    {([
                        ['upload', 'Upload a file', 'POD5, SLOW5 or BLOW5 (with its .idx index)'],
                        ['existing', 'Use a POD5 on this computer', 'Registered in place instead of uploading; the file is not moved or changed'],
                    ] as const).map(([value, label, detail]) => (
                        <button
                            key={value}
                            type="button"
                            role="radio"
                            aria-checked={rawSourceMode === value}
                            onClick={() => setRawSourceMode(value)}
                            className={`rounded-lg border px-3 py-2 text-left text-sm ${rawSourceMode === value
                                ? 'border-[var(--accent-primary)] bg-[var(--bg-secondary)] font-semibold text-[var(--text-primary)]'
                                : 'border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]'}`}
                        >
                            <span className="block">{label}</span>
                            <span className="block text-xs font-normal text-[var(--text-secondary)]">{detail}</span>
                        </button>
                    ))}
                </div>
                {rawSourceMode === 'upload' ? (
                    <div className="space-y-3">
                        <fieldset disabled={importRawSignal.isPending} className="grid gap-3 sm:grid-cols-2">
                            <label className="text-xs">Raw-signal file<input key={exactDomainExperimentId ?? 'no-project'} aria-label="Raw-signal file" type="file" accept=".pod5,.slow5,.blow5" onChange={(e) => { setRawUpload(e.target.files?.[0] ?? null); setRawIndexUpload(null); uploadedRawFiles.current.clear(); rawUploadDirectory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`; }} className="mt-1 block w-full text-sm" /></label>
                            {rawUpload?.name.toLowerCase().endsWith('.blow5') ? <label className="text-xs">Matching BLOW5 index<input aria-label="BLOW5 index" type="file" accept=".idx" onChange={(e) => { setRawIndexUpload(e.target.files?.[0] ?? null); uploadedRawFiles.current = new Map(); rawUploadDirectory.current = `inputs/ont-raw-signal/${crypto.randomUUID()}`; }} className="mt-1 block w-full text-sm" /></label> : null}
                        </fieldset>
                        <button type="button" disabled={!exactDomainExperimentId || !rawUpload || importRawSignal.isPending || (rawUpload.name.toLowerCase().endsWith('.blow5') && !rawIndexUpload)} onClick={() => importRawSignal.mutate()} className="rounded-lg border border-[var(--accent-primary)] px-3 py-2 text-sm font-semibold text-[var(--accent-primary)] disabled:opacity-50">{importRawSignal.isPending ? 'Importing raw signal…' : 'Import raw signal'}</button>
                        {importRawSignal.isError ? <p role="alert" className="text-sm text-[var(--text-primary)]">{errorMessage(importRawSignal.error, 'Raw-signal import failed.')}</p> : null}
                        {importRawSignal.isSuccess ? <p role="status" className="text-sm text-[var(--text-secondary)]">{message}</p> : null}
                    </div>
                ) : (
                    <div className="space-y-3">
                        {externalPod5CandidatesQuery.isError ? <p role="alert" className="text-sm text-[var(--warning)]">Existing POD5 candidates are unavailable: {errorMessage(externalPod5CandidatesQuery.error, 'unknown candidate-list failure')}</p> : null}
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
                            className="rounded-lg border border-[color-mix(in_srgb,var(--accent-primary)_50%,transparent)] px-3 py-2 text-sm font-semibold text-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {registerExternalPod5.isPending ? 'Registering POD5…' : 'Register selected POD5'}
                        </button>
                    </div>
                )}
            </div>

            <div className="space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Run history</h3>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">Sequencing runs saved to this Project.</p>
                    </div>
                    <button type="button" disabled={!exactDomainExperimentId} onClick={() => void durableRunsQuery.refetch()} className="text-sm text-[var(--text-action)] disabled:cursor-not-allowed disabled:opacity-50">Refresh history</button>
                </div>
                {durableRunsQuery.isError ? <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Durable ONT runs could not be loaded: {errorMessage(durableRunsQuery.error, 'unknown run-ledger failure')}</p> : null}
                <label className="block text-xs font-semibold text-[var(--text-secondary)]">Saved run
                    <select
                        value={effectiveSelectedRunGeneration ? runSelectionKey({ run_id: effectiveSelectedRunGeneration.runId, observed_generation: effectiveSelectedRunGeneration.observedGeneration }) : ''}
                        onChange={(event) => {
                            const run = durableRuns.find((candidate) => runSelectionKey(candidate) === event.target.value);
                            setSelectedRunGeneration(run ? { runId: run.run_id, observedGeneration: run.observed_generation } : null);
                        }}
                        disabled={!exactDomainExperimentId || durableRunsQuery.isLoading}
                        className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
                    >
                        <option value="">Choose a run…</option>
                        {durableRuns.map((run: OntRunSummary) => <option key={runSelectionKey(run)} value={runSelectionKey(run)}>{run.sample_id || run.run_id} · {run.status} · version {run.observed_generation}</option>)}
                    </select>
                </label>
                {exactDomainExperimentId && durableRunsQuery.isSuccess ? (
                    <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--text-secondary)]">
                        <span>{durableRuns.length} {durableRuns.length === 1 ? 'run' : 'runs'} loaded{durableRunsQuery.hasNextPage ? ' · older runs available' : ''}</span>
                        {durableRunsQuery.hasNextPage ? (
                            <button
                                type="button"
                                disabled={durableRunsQuery.isFetchingNextPage}
                                onClick={() => void durableRunsQuery.fetchNextPage()}
                                className="text-sm text-[var(--text-action)] disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {durableRunsQuery.isFetchingNextPage ? 'Loading older runs…' : 'Load older runs'}
                            </button>
                        ) : null}
                    </div>
                ) : null}
                {requestedRunGeneration ? <p className="text-xs text-[var(--text-secondary)]">Reopening URL-owned exact run generation {requestedRunGeneration.runId} · {requestedRunGeneration.observedGeneration}.</p> : null}
                {selectedGenerationQuery.isLoading ? <p className="text-sm text-[var(--text-secondary)]">Loading exact persisted run generation…</p> : null}
                {selectedGenerationQuery.isError ? <p role="alert" className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--warning)_10%,transparent)] p-3 text-sm text-[var(--warning)]">Exact run generation could not be reopened: {errorMessage(selectedGenerationQuery.error, 'unknown generation query failure')}</p> : null}
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

                        <div className="space-y-3 rounded-lg border border-[color-mix(in_srgb,var(--accent-primary)_30%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_5%,transparent)] p-3">
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
                                        <option value="blow5">Indexed BLOW5</option>
                                    </select>
                                </label>
                            </div>
                            {rawSignalQuery.isLoading ? <div>Loading raw-signal capability state…</div> : null}
                            {rawSignalQuery.isError ? <div role="alert" className="text-[var(--warning)]">Raw-signal state unavailable: {errorMessage(rawSignalQuery.error, 'unknown capability failure')}</div> : null}
                            {rawSignalQuery.data ? (
                                <>
                                    <div>Selected: <span className="font-mono text-[var(--text-primary)]">{rawSignalQuery.data.selected_format ?? 'none'}</span> · {rawSignalQuery.data.selection_reason_code}</div>
                                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                                        {Object.entries(rawSignalQuery.data.modes).map(([mode, capability]) => (
                                            <div key={mode} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2">
                                                <div className="font-mono text-[var(--text-primary)]">{mode}</div>
                                                <div className={capability.state === 'ready' ? 'text-[var(--success)]' : capability.state === 'preparable' ? 'text-[var(--warning)]' : 'text-[var(--text-secondary)]'}>{capability.state}</div>
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
                                                            <div className="space-y-2 rounded border border-[color-mix(in_srgb,var(--success)_30%,transparent)] bg-[color-mix(in_srgb,var(--success)_5%,transparent)] p-2" aria-label="Raw-signal publication receipt">
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
                                                {representation.state !== 'ready' && (representation.format === 'pod5' || representation.format === 'slow5' || representation.format === 'blow5') ? (
                                                    <button
                                                        type="button"
                                                        onClick={() => prepareBlow5.mutate(representation.representation_id)}
                                                        disabled={prepareBlow5.isPending}
                                                        className="mt-2 rounded border border-[color-mix(in_srgb,var(--accent-primary)_50%,transparent)] px-2 py-1 font-semibold text-[var(--accent-primary)] disabled:opacity-40"
                                                    >
                                                        {representation.format === 'blow5' ? 'Validate indexed BLOW5' : representation.format === 'slow5' ? 'Convert SLOW5 to indexed BLOW5' : 'Prepare indexed BLOW5'}
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
                                            <div className="space-y-2 rounded border border-[color-mix(in_srgb,var(--success)_40%,transparent)] bg-[color-mix(in_srgb,var(--success)_5%,transparent)] p-3">
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
                                                        className="rounded border border-[color-mix(in_srgb,var(--success)_50%,transparent)] px-2 py-1 font-semibold text-[var(--success)] disabled:opacity-40"
                                                    >
                                                        {waveformLoading ? 'Inspecting…' : 'Inspect raw waveform'}
                                                    </button>
                                                </div>
                                                {waveformError ? <div role="alert" className="text-[var(--warning)]">Waveform unavailable: {waveformError}</div> : null}
                                                {waveform?.state === 'ready' && waveformPoints ? (
                                                    <div className="space-y-1">
                                                        <div>Read <span className="font-mono text-[var(--text-primary)]">{waveform.read_id}</span> · {waveform.sample_count} source samples · {waveform.samples?.length ?? 0} displayed</div>
                                                        <svg aria-label="Raw electrical signal waveform" viewBox="0 0 600 120" className="h-36 w-full rounded bg-[var(--bg-tertiary)]" preserveAspectRatio="none">
                                                            <polyline points={waveformPoints} fill="none" stroke="var(--success)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
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
                            className="inline-flex rounded-lg border border-[var(--border-primary)] px-3 py-2 font-semibold text-[var(--text-action)] hover:bg-[var(--bg-tertiary)]"
                        >
                            Reopen exact persisted generation
                        </Link>
                    </div>
                ) : null}
            </div>
        </section>
    );
}
