import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import {
    createOntRunIntent,
    requestOntBarcodeBasecallHandoff,
    fetchMolBioNgsReferenceRevision,
    fetchMolBioNgsSampleRevision,
    fetchMolBioNgsSummaries,
    fetchOntDeviceStatus,
    fetchOntInstrumentRunGeneration,
    fetchOntInstrumentRuns,
    uploadFile,
    fetchOntProtocolOptions,
    requestMk1dReconnect,
    startOntRunIntent,
    stopOntInstrumentRun,
    type OntInstrumentRun,
    type OntLiveDevice,
    type OntProtocolOption,
    type OntRunSummary,
} from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

import OntRunLiveStats from './OntRunLiveStats';

const RUN_HISTORY_PAGE_SIZE = 100;

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: (values?: Record<string, unknown>) => void;
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

export function OntInstrumentPanel({ onAnalyzeExistingData }: OntInstrumentPanelProps) {
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
    const [barcodeSampleText, setBarcodeSampleText] = useState('');
    // Live basecalling: accuracy, modifications and read-quality cutoff, from what MinKNOW offers for the kit.
    const [basecallQuality, setBasecallQuality] = useState('');
    const [basecallModifications, setBasecallModifications] = useState<string[]>([]);
    const [basecallMinQscore, setBasecallMinQscore] = useState('');
    // Adaptive sampling is run by MinKNOW; BMS hands it the mode, a reference and optional target regions.
    const [adaptiveMode, setAdaptiveMode] = useState<'' | 'enrich' | 'deplete' | 'barcode_balance'>('');
    const [adaptiveReference, setAdaptiveReference] = useState<File | null>(null);
    const [adaptiveBed, setAdaptiveBed] = useState<File | null>(null);
    // The barcoding kits BMS demultiplexes; a run on one can carry a sample name per barcode.
    const barcodingKit = /^SQK-(RBK|NBD)114-(24|96)$/.test(selectedKit);
    const barcodeSamples = barcodingKit ? barcodeSampleText.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
        const [barcode = '', alias = ''] = line.split(/[\t,]/).map(value => value.trim());
        return { barcode, alias };
    }) : [];
    const barcodeSampleError = barcodeSamples.some(item => !/^barcode\d{2}$/.test(item.barcode) || !/^[A-Za-z0-9_-]{1,40}$/.test(item.alias))
        ? 'Use one line per barcode: barcode01, then a comma or tab, then a name of letters, numbers, _ or -.' : '';
    const [sampleSelection, setSampleSelection] = useState<{ domainId: string; sampleId: string } | null>(null);
    const [selectedRunGeneration, setSelectedRunGeneration] = useState<SelectedRunGeneration | null>(null);
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
        queryKey: ['ont-protocol-options', selectedPositionForQuery, selectedKit, runHours],
        queryFn: async () => (await fetchOntProtocolOptions(selectedPositionForQuery, { kit: selectedKit, run_hours: runHours })).data,
        enabled: Boolean(selectedPositionForQuery),
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const instrumentEvidenceError = deviceStatus.isError || protocolOptions.isError;
    const effectiveProtocolOptions = instrumentEvidenceError ? undefined : protocolOptions.data;
    const selectedOption: OntProtocolOption | undefined = effectiveProtocolOptions?.options[0];
    const basecallingChoices = selectedOption?.basecalling_enabled ? selectedOption.basecalling_choices ?? [] : [];
    const activeBasecalling = basecallingChoices.find(choice => choice.quality === basecallQuality)
        ?? basecallingChoices.find(choice => choice.default) ?? basecallingChoices[0];
    const activeModifications = basecallModifications.filter(id => activeBasecalling?.modified_bases.some(item => item.id === id));
    const minQscoreError = basecallMinQscore !== '' && !(Number(basecallMinQscore) >= 0 && Number(basecallMinQscore) <= 30)
        ? 'Minimum read Q-score must be from 0 to 30.' : '';
    const toggleBasecallModification = (id: string) => setBasecallModifications(() => {
        if (activeModifications.includes(id)) return activeModifications.filter(item => item !== id);
        const base = activeBasecalling?.modified_bases.find(item => item.id === id)?.canonical_base ?? id;
        // MinKNOW runs one modification model per base, so a second choice for the same base replaces the first.
        return [...activeModifications.filter(item => (activeBasecalling?.modified_bases.find(other => other.id === item)?.canonical_base ?? item) !== base), id];
    });
    const activeAdaptiveMode = activeBasecalling && (adaptiveMode !== 'barcode_balance' || barcodingKit) ? adaptiveMode : '';
    const adaptiveNeedsReference = activeAdaptiveMode === 'enrich' || activeAdaptiveMode === 'deplete';
    const adaptiveError = adaptiveNeedsReference && !adaptiveReference ? 'Adaptive sampling needs a reference FASTA.' : '';
    const adaptiveSummary = activeAdaptiveMode
        ? ` Adaptive sampling: ${activeAdaptiveMode === 'barcode_balance' ? 'balance barcodes' : activeAdaptiveMode}.` : '';
    const basecallingSummary = activeBasecalling
        ? ` Basecalling ${activeBasecalling.quality.toUpperCase()}${activeModifications.length ? `, calling ${activeModifications.join(', ')}` : ''}, minimum Q${basecallMinQscore !== '' ? basecallMinQscore : activeBasecalling.default_min_qscore ?? '?'}.`
        : '';

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
    const refreshDurableRuns = (run?: OntInstrumentRun) => {
        if (run) {
            setSelectedRunGeneration({ runId: run.id, observedGeneration: run.observed_generation });
        }
        void queryClient.invalidateQueries({ queryKey: ['ont-instrument-runs', exactDomainExperimentId] });
        void durableRunsQuery.refetch();
    };

    const reconnectMk1d = useMutation({
        mutationFn: async () => (await requestMk1dReconnect()).data,
        onSuccess: () => void deviceStatus.refetch(),
    });

    const startRun = useMutation({
        mutationFn: async () => {
            if (!exactDomainExperimentId) throw new Error('Choose a Project to start sequencing.');
            if (!selectedDevice?.position || !selectedOption) throw new Error('No server-issued protocol option is available');
            if (barcodeSampleError) throw new Error(barcodeSampleError);
            if (minQscoreError) throw new Error(minQscoreError);
            if (adaptiveError) throw new Error(adaptiveError);
            let adaptiveSampling: { mode: 'enrich' | 'deplete' | 'barcode_balance'; reference_path?: string; bed_path?: string } | undefined;
            if (activeAdaptiveMode) {
                adaptiveSampling = { mode: activeAdaptiveMode };
                if (adaptiveNeedsReference && adaptiveReference) {
                    const directory = `inputs/ont-adaptive-sampling/${crypto.randomUUID()}`;
                    adaptiveSampling.reference_path = (await uploadFile(directory, adaptiveReference)).data.path;
                    if (adaptiveBed) adaptiveSampling.bed_path = (await uploadFile(directory, adaptiveBed)).data.path;
                }
            }
            const intent = await createOntRunIntent(selectedDevice.position, {
                option_id: selectedOption.option_id,
                option_receipt_id: selectedOption.option_receipt_id,
                sample_id: selectedSample?.id,
                // The existing physical-intent contract calls this field experiment_group.
                // It is always the exact selected Domain Experiment ID, never the Global Experiment ID.
                experiment_group: exactDomainExperimentId,
                ...(barcodeSamples.length ? { barcode_samples: barcodeSamples } : {}),
                ...(activeBasecalling ? { basecalling: {
                    quality: activeBasecalling.quality,
                    modified_bases: activeModifications,
                    ...(basecallMinQscore !== '' ? { min_qscore: Number(basecallMinQscore) } : {}),
                } } : {}),
                ...(adaptiveSampling ? { adaptive_sampling: adaptiveSampling } : {}),
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

    // A finished run opens in the ONT form with its POD5 folder, barcoding kit and sample names filled in.
    const openBarcodeBasecall = useMutation({
        mutationFn: async (runId: string) => (await requestOntBarcodeBasecallHandoff(runId)).data,
        onSuccess: (handoff) => onAnalyzeExistingData({
            selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: handoff.pod5_dir, runModkit: false, runFastqQc: false, runAssembly: false,
            barcodeKit: handoff.barcode_kit ?? '', sampleSheet: handoff.sample_sheet ?? '',
        }),
        onError: (error) => setMessage(errorMessage(error, 'The run could not be opened for basecalling')),
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
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-[color-mix(in_srgb,var(--accent-primary)_40%,transparent)] bg-[color-mix(in_srgb,var(--accent-primary)_10%,transparent)] px-2 py-1 text-xs font-semibold uppercase tracking-wide text-[var(--accent-primary)]">Mk1D / MinKNOW</span>
                    </div>
                    <p className="max-w-2xl text-sm text-[var(--text-secondary)]">Pick a Mk1D position, choose the kit and run length, and start sequencing. BNS tracks the run and picks up its reads when it finishes. Reconnect only works on this computer, not over Tailnet.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => onAnalyzeExistingData()} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">Data Analysis</button>
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
                    <div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">New sequencing run</h3><p className="text-xs text-[var(--text-secondary)]">BNS picks the matching MinKNOW protocol for the kit and flow cell. Basecalling accuracy, modified bases and the read-quality cutoff appear once a kit is chosen.</p></div>
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
                    {barcodingKit ? (
                        <label className="block text-xs font-semibold text-[var(--text-secondary)]">Barcode sample names (optional)
                            <textarea aria-label="Barcode sample names" rows={4} value={barcodeSampleText} onChange={(event) => setBarcodeSampleText(event.target.value)}
                                placeholder={'barcode01\tplasmid_A\nbarcode02\tplasmid_B'}
                                className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 font-mono text-sm text-[var(--text-primary)]" />
                            <span className="mt-1 block font-normal">MinKNOW demultiplexes this kit as it basecalls. Names entered here label its output and are filled in when you basecall the run in BMS.</span>
                            {barcodeSampleError ? <span role="alert" className="mt-1 block font-normal text-[var(--warning)]">{barcodeSampleError}</span> : null}
                        </label>
                    ) : null}
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
                    {activeBasecalling ? (
                        <div className="space-y-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]" data-testid="ont-live-basecalling">
                            <label className="block font-semibold">Basecalling accuracy
                                <select
                                    aria-label="Basecalling accuracy"
                                    value={activeBasecalling.quality}
                                    onChange={(event) => { setBasecallQuality(event.target.value); setBasecallMinQscore(''); }}
                                    className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                >
                                    {basecallingChoices.map((choice) => <option key={choice.quality} value={choice.quality}>{choice.quality.toUpperCase()}{choice.default ? ' (MinKNOW default)' : ''}</option>)}
                                </select>
                            </label>
                            {activeBasecalling.modified_bases.length ? (
                                <div role="group" aria-label="Modified bases called during the run" className="space-y-1">
                                    <div className="font-semibold">Modified bases (optional)</div>
                                    {activeBasecalling.modified_bases.map((item) => (
                                        <label key={item.id} className="flex items-center gap-2">
                                            <input type="checkbox" aria-label={`Call ${item.id}`} checked={activeModifications.includes(item.id)} onChange={() => toggleBasecallModification(item.id)} />
                                            <span className="text-[var(--text-primary)]">{item.id.replace(/_/g, ' + ')}</span>
                                            <span>{item.context && item.context !== item.canonical_base ? `${item.context} context` : item.canonical_base ? `all ${item.canonical_base}` : ''}</span>
                                        </label>
                                    ))}
                                    <p>One model per base. A run that calls modifications also writes BAM, which carries the calls.</p>
                                </div>
                            ) : <p>MinKNOW offers no modification models for {activeBasecalling.quality.toUpperCase()} basecalling with this kit.</p>}
                            <label className="block font-semibold">Minimum read Q-score
                                <input
                                    aria-label="Minimum read Q-score"
                                    type="number"
                                    min={0}
                                    max={30}
                                    step={1}
                                    value={basecallMinQscore}
                                    placeholder={activeBasecalling.default_min_qscore == null ? '' : `${activeBasecalling.default_min_qscore} (MinKNOW default)`}
                                    onChange={(event) => setBasecallMinQscore(event.target.value)}
                                    className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                />
                            </label>
                            {minQscoreError ? <p role="alert" className="text-[var(--warning)]">{minQscoreError}</p> : <p>Reads below this go to MinKNOW's fail output. Raw signal is kept for every read, so the run can be basecalled again in Data Analysis.</p>}
                        </div>
                    ) : null}
                    {activeBasecalling ? (
                        <div className="space-y-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]" data-testid="ont-adaptive-sampling">
                            <label className="block font-semibold">Adaptive sampling
                                <select
                                    aria-label="Adaptive sampling"
                                    value={activeAdaptiveMode}
                                    onChange={(event) => setAdaptiveMode(event.target.value as typeof adaptiveMode)}
                                    className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                                >
                                    <option value="">Off</option>
                                    <option value="enrich">Enrich targets</option>
                                    <option value="deplete">Deplete targets</option>
                                    {barcodingKit ? <option value="barcode_balance">Balance barcodes</option> : null}
                                </select>
                            </label>
                            {adaptiveNeedsReference ? <>
                                <label className="block font-semibold">Reference FASTA
                                    <input aria-label="Adaptive sampling reference" type="file" accept=".fasta,.fa,.fna,.mmi"
                                        onChange={(event) => setAdaptiveReference(event.target.files?.[0] ?? null)} className="mt-1 block w-full font-normal" />
                                </label>
                                <label className="block font-semibold">Target regions, BED (optional)
                                    <input aria-label="Adaptive sampling target regions" type="file" accept=".bed"
                                        onChange={(event) => setAdaptiveBed(event.target.files?.[0] ?? null)} className="mt-1 block w-full font-normal" />
                                </label>
                                <p>MinKNOW basecalls the start of each strand and aligns it to this reference. Enrich keeps strands that match the targets and ejects the rest; deplete does the reverse. Without a BED file the whole reference is the target.</p>
                                {adaptiveError ? <p role="alert" className="text-[var(--warning)]">{adaptiveError}</p> : null}
                            </> : null}
                            {activeAdaptiveMode === 'barcode_balance' ? <p>MinKNOW ejects every strand whose barcode it cannot identify, from the start of the run. After 30 minutes it also ejects strands from barcodes that are ahead of the others. There is no coverage target. Leave this off to keep every strand.</p> : null}
                        </div>
                    ) : null}
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">{selectedOption ? `${selectedOption.protocol_label} · basecalling ${selectedOption.basecalling_enabled ? 'on' : 'off'}` : selectedKit ? 'MinKNOW has no sequencing protocol for this kit and flow cell.' : 'Choose a kit to prepare the run.'}{blockers.length ? <div className="mt-2 text-[var(--warning)]">Not ready: {blockers.join(', ')}</div> : null}</div>
                    <button
                        type="button"
                        disabled={!canStart}
                        title={domainIntentBlocker ?? undefined}
                        onClick={() => {
                            if (window.confirm(`Start sequencing on ${selectedDevice?.position} with ${selectedKit} for ${runHours} h?${basecallingSummary}${adaptiveSummary} This uses the flow cell.`)) startRun.mutate();
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
            {activeRuns.map((run) => <OntRunLiveStats key={run.run_id} runId={run.run_id} />)}

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

                        <Link
                            to={contextHref('/ngs', { section: 'raw-signal', run_id: selectedGeneration.run_id })}
                            className="mr-2 inline-flex rounded-lg border border-[var(--border-primary)] px-3 py-2 font-semibold text-[var(--text-action)] hover:bg-[var(--bg-tertiary)]"
                        >
                            Raw signal for this run
                        </Link>
                        {['completed', 'stopped'].includes(selectedGeneration.status) && selectedGeneration.output_counts.pod5 > 0 ? (
                            <button type="button" disabled={openBarcodeBasecall.isPending} onClick={() => openBarcodeBasecall.mutate(selectedGeneration.run_id)}
                                className="mr-2 inline-flex rounded-lg bg-[var(--accent-primary)] px-3 py-2 font-semibold text-[var(--text-on-accent)] disabled:opacity-50">
                                {openBarcodeBasecall.isPending ? 'Opening…' : 'Basecall and demultiplex this run'}
                            </button>
                        ) : null}
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
