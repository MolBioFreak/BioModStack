import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
    DEFAULT_ONT_SIGNAL_RENDER_PARAMS,
    cancelOntSignalCalibration,
    cancelOntSignalMapping,
    cancelOntSignalView,
    createOntFreshMoveSourceAttempt,
    createOntSignalCalibration,
    createOntSignalMapping,
    createOntSignalMappingProfile,
    createOntSignalView,
    createOntSignalViewerSession,
    fetchOntExternalMoveBamCandidates,
    fetchOntMoveSources,
    fetchOntSignalCalibration,
    fetchOntSignalMapping,
    fetchOntSignalMappingProfiles,
    fetchOntSignalView,
    fetchOntSignalViewArtifact,
    fetchOntSignalWorkbenchCapabilities,
    registerOntExternalMoveBamCandidate,
    updateOntSignalViewerSession,
    type OntExternalMoveBamCandidate,
    type OntMoveTableSource,
    type OntSignalCalibrationJob,
    type OntSignalMappingJob,
    type OntSignalMappingMode,
    type OntSignalMappingProfile,
    type OntSignalRenderParams,
    type OntSignalViewerIgvUpdateState,
    type OntSignalViewerSession,
    type OntSignalViewJob,
    type OntSignalViewMode,
    type OntSignalWorkbenchCapabilities,
} from '../../lib/api';
import {
    fetchAlignmentRead,
    fetchAlignmentReads,
    filterAlignmentReads,
    formatAlignmentReadSummary,
    type AlignmentRead,
    type AlignmentReadFilterPreset,
    type AlignmentSession,
} from '../../lib/ngsAlignmentSession';
import type { AlignmentReadLocus } from '../../lib/ngsAlignmentViewer';
import { GovernedRawSignalWaveform } from './RawReadInspector';
import { OntSignalIdealComparison } from './OntSignalIdealComparison';

const TERMINAL_STATES = new Set(['ready', 'failed', 'cancelled']);
type WorkbenchViewMode = OntSignalViewMode | 'raw_waveform' | 'ideal_comparison';

interface ReadAndSignalWorkbenchProps {
    datasetId: string;
    runId: string;
    observedGeneration: number;
    alignmentJobId: string;
    alignmentSession: AlignmentSession | null;
    referenceRevisionId: string | null;
    currentLocus: AlignmentReadLocus | null;
    viewerSession: OntSignalViewerSession | null;
    igvState: OntSignalViewerIgvUpdateState;
    onViewerSessionChange: (session: OntSignalViewerSession) => void;
    onNavigateIgv: (contig: string, start: number, end: number, source: string) => void;
}

function message(reason: unknown): string {
    return reason instanceof Error ? reason.message : String(reason);
}

function stateBadge(state: string): string {
    if (state === 'ready' || state === 'independent') return 'bg-emerald-500/20 text-emerald-300';
    if (state === 'preparable' || state === 'requested' || state === 'running') return 'bg-amber-500/20 text-amber-200';
    if (state === 'failed' || state === 'unavailable') return 'bg-rose-500/20 text-rose-200';
    return 'bg-slate-500/20 text-slate-200';
}

function integer(value: string): number | null {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
}

function isRenderParams(value: unknown): value is OntSignalRenderParams {
    if (!value || typeof value !== 'object') return false;
    const candidate = value as Partial<OntSignalRenderParams>;
    return (candidate.strand === 'forward' || candidate.strand === 'reverse')
        && (candidate.signal_units === 'pA' || candidate.signal_units === 'raw_adc')
        && ['none', 'medmad', 'znorm', 'scaledpA'].includes(String(candidate.scale));
}

const GOVERNED_HTML_CSP = [
    "default-src 'none'",
    "base-uri 'none'",
    "connect-src 'none'",
    "font-src data:",
    "form-action 'none'",
    "frame-src 'none'",
    "img-src data:",
    "media-src 'none'",
    "object-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "worker-src 'none'",
    "navigate-to 'none'",
].join('; ');

function secureGovernedHtml(source: string): Blob {
    const documentNode = new DOMParser().parseFromString(source, 'text/html');
    const policy = documentNode.createElement('meta');
    policy.httpEquiv = 'Content-Security-Policy';
    policy.content = GOVERNED_HTML_CSP;
    documentNode.head.insertBefore(policy, documentNode.head.firstChild);
    return new Blob([
        '<!doctype html>\n',
        documentNode.documentElement.outerHTML,
    ], { type: 'text/html;charset=utf-8' });
}

function readHtmlBlob(blob: Blob): Promise<string> {
    if (typeof blob.text === 'function') return blob.text();
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener('load', () => resolve(String(reader.result || '')));
        reader.addEventListener('error', () => reject(reader.error || new Error('Governed HTML could not be decoded.')));
        reader.readAsText(blob);
    });
}

export function ReadAndSignalWorkbench({
    datasetId,
    runId,
    observedGeneration,
    alignmentJobId,
    alignmentSession,
    referenceRevisionId,
    currentLocus,
    viewerSession,
    igvState,
    onViewerSessionChange,
    onNavigateIgv,
}: ReadAndSignalWorkbenchProps) {
    const identityRef = useRef(0);
    const artifactRequestGenerationRef = useRef(0);
    const viewJobGenerationRef = useRef(0);
    const viewJobIdRef = useRef<string | null>(null);
    const viewerCreateKeyRef = useRef('');
    const artifactUrlRef = useRef<string | null>(null);
    const [capabilities, setCapabilities] = useState<OntSignalWorkbenchCapabilities | null>(null);
    const [externalMoveBamCandidates, setExternalMoveBamCandidates] = useState<OntExternalMoveBamCandidate[]>([]);
    const [externalMoveBamCandidateId, setExternalMoveBamCandidateId] = useState('');
    const [externalMoveBamMoleculeType, setExternalMoveBamMoleculeType] = useState<'dna' | 'rna'>('dna');
    const [externalMoveBamAvailability, setExternalMoveBamAvailability] = useState<string | null>(null);
    const [moveSources, setMoveSources] = useState<OntMoveTableSource[]>([]);
    const [registeredExternalMoveSourceId, setRegisteredExternalMoveSourceId] = useState<string | null>(null);
    const [profiles, setProfiles] = useState<OntSignalMappingProfile[]>([]);
    const [calibration, setCalibration] = useState<OntSignalCalibrationJob | null>(null);
    const [preparationMode, setPreparationMode] = useState<OntSignalMappingMode>('signal_to_read');
    const [readMapping, setReadMapping] = useState<OntSignalMappingJob | null>(null);
    const [referenceMapping, setReferenceMapping] = useState<OntSignalMappingJob | null>(null);
    const [viewJob, setViewJob] = useState<OntSignalViewJob | null>(null);
    const [artifactUrl, setArtifactUrl] = useState<string | null>(null);
    const [selectedRead, setSelectedRead] = useState<AlignmentRead | null>(null);
    const [eligibleReads, setEligibleReads] = useState<AlignmentRead[]>([]);
    const [readFilterPreset, setReadFilterPreset] = useState<AlignmentReadFilterPreset>('all');
    const [readSearch, setReadSearch] = useState('');
    const [readId, setReadId] = useState('');
    const [contig, setContig] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [mode, setMode] = useState<WorkbenchViewMode>('read');
    const [renderParams, setRenderParams] = useState<OntSignalRenderParams>(DEFAULT_ONT_SIGNAL_RENDER_PARAMS);
    const [profileBaseShiftValue, setProfileBaseShiftValue] = useState(0);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const persistedReadMappingJobId = typeof viewerSession?.signal_state.read_mapping_job_id === 'string'
        ? viewerSession.signal_state.read_mapping_job_id
        : null;
    const persistedReferenceMappingJobId = typeof viewerSession?.signal_state.reference_mapping_job_id === 'string'
        ? viewerSession.signal_state.reference_mapping_job_id
        : null;
    const activeRawRepresentationId = viewerSession
        ? viewerSession.raw_representation_id
        : capabilities?.resolved.raw_representation_id || null;
    const externalMoveSources = useMemo(
        () => moveSources.filter((item) => item.external_registration_receipt_id),
        [moveSources],
    );
    const displayedActiveExternalMoveSource = useMemo(() => {
        const resolvedMoveSourceId = capabilities?.resolved.move_source_id || registeredExternalMoveSourceId;
        return externalMoveSources.find((item) => item.move_source_id === resolvedMoveSourceId && item.state === 'ready')
            || [...externalMoveSources]
                .filter((item) => item.state === 'ready')
                .sort((left, right) => right.attempt_number - left.attempt_number)[0]
            || null;
    }, [capabilities?.resolved.move_source_id, externalMoveSources, registeredExternalMoveSourceId]);
    const externalMoveSourceHistory = useMemo(
        () => externalMoveSources.filter((item) => item.move_source_id !== displayedActiveExternalMoveSource?.move_source_id),
        [displayedActiveExternalMoveSource?.move_source_id, externalMoveSources],
    );

    const identityKey = `${datasetId}:${runId}:${observedGeneration}:${alignmentJobId}:${alignmentSession?.session_id || ''}:${referenceRevisionId || ''}:${viewerSession?.viewer_session_id || ''}`;
    const viewerAlignmentSessionId = alignmentSession?.ready && alignmentSession.session_id && referenceRevisionId
        ? alignmentSession.session_id
        : null;
    const viewerReferenceRevisionId = viewerAlignmentSessionId ? referenceRevisionId : null;

    const replaceViewJob = useCallback((next: OntSignalViewJob | null) => {
        const nextId = next?.view_job_id || null;
        if (nextId !== viewJobIdRef.current) {
            viewJobIdRef.current = nextId;
            viewJobGenerationRef.current += 1;
        }
        setViewJob(next);
    }, []);

    const replaceArtifactUrl = useCallback((next: string | null) => {
        const current = artifactUrlRef.current;
        if (current === next) return;
        artifactUrlRef.current = next;
        if (current) URL.revokeObjectURL(current);
        setArtifactUrl(next);
    }, []);

    const refreshAuthorities = useCallback(async () => {
        const generation = identityRef.current;
        const [nextCapabilities, sourcePayload, profilePayload, externalCandidateResult] = await Promise.all([
            fetchOntSignalWorkbenchCapabilities(
                runId,
                observedGeneration,
                alignmentSession?.session_id && referenceRevisionId ? {
                    alignment_job_id: alignmentJobId,
                    alignment_session_id: alignmentSession.session_id,
                    reference_revision_id: referenceRevisionId,
                } : null,
            ),
            fetchOntMoveSources(runId, observedGeneration),
            fetchOntSignalMappingProfiles(),
            fetchOntExternalMoveBamCandidates().then(
                (payload) => ({ payload, unavailable: null }),
                () => ({ payload: { items: [] as OntExternalMoveBamCandidate[] }, unavailable: 'External move-BAM source is unavailable.' }),
            ),
        ]);
        if (generation !== identityRef.current) return;
        setCapabilities(nextCapabilities);
        setMoveSources(sourcePayload.items);
        const resumableExternalSource = [...sourcePayload.items]
            .filter((item) => (
                (item.state === 'requested' || item.state === 'running')
                && item.external_registration_receipt_id !== null
            ))
            .sort((left, right) => right.attempt_number - left.attempt_number)[0] ?? null;
        setRegisteredExternalMoveSourceId((current) => {
            const currentStillActive = current !== null && sourcePayload.items.some((item) => (
                item.move_source_id === current
                && (item.state === 'requested' || item.state === 'running')
            ));
            return currentStillActive ? current : resumableExternalSource?.move_source_id ?? null;
        });
        setProfiles(profilePayload.items);
        setExternalMoveBamCandidates(externalCandidateResult.payload.items);
        setExternalMoveBamAvailability(externalCandidateResult.unavailable);
        setExternalMoveBamCandidateId((current) => (
            externalCandidateResult.payload.items.some((item) => item.candidate_id === current)
                ? current
                : externalCandidateResult.payload.items[0]?.candidate_id || ''
        ));
        const resolvedMatchesPersistedAuthority = !viewerSession || (
            nextCapabilities.resolved.raw_representation_id === viewerSession.raw_representation_id
            && nextCapabilities.resolved.move_source_id === viewerSession.move_source_id
            && nextCapabilities.resolved.mapping_profile_id === viewerSession.mapping_profile_id
        );
        const resolvedMatchesPersistedRawMove = !viewerSession || (
            nextCapabilities.resolved.raw_representation_id === viewerSession.raw_representation_id
            && nextCapabilities.resolved.move_source_id === viewerSession.move_source_id
        );
        const readJobId = persistedReadMappingJobId
            || (resolvedMatchesPersistedAuthority ? nextCapabilities.resolved.signal_to_read_mapping_job_id : null);
        const referenceJobId = persistedReferenceMappingJobId
            || (resolvedMatchesPersistedAuthority ? nextCapabilities.resolved.signal_to_reference_mapping_job_id : null);
        const calibrationJobId = resolvedMatchesPersistedRawMove
            ? nextCapabilities.resolved.calibration_job_id
            : null;
        const [nextReadMapping, nextReferenceMapping, nextCalibration] = await Promise.all([
            readJobId ? fetchOntSignalMapping(readJobId) : Promise.resolve(null),
            referenceJobId ? fetchOntSignalMapping(referenceJobId) : Promise.resolve(null),
            calibrationJobId ? fetchOntSignalCalibration(calibrationJobId) : Promise.resolve(null),
        ]);
        if (generation !== identityRef.current) return;
        const expectedRawRepresentationId = viewerSession
            ? viewerSession.raw_representation_id
            : nextCapabilities.resolved.raw_representation_id;
        const expectedMoveSourceId = viewerSession
            ? viewerSession.move_source_id
            : nextCapabilities.resolved.move_source_id;
        const expectedMappingProfileId = viewerSession
            ? viewerSession.mapping_profile_id
            : nextCapabilities.resolved.mapping_profile_id;
        const mappingMatchesAuthority = (
            mapping: OntSignalMappingJob | null,
            expectedJobId: string | null,
            expectedMode: OntSignalMappingMode,
        ) => {
            if (!mapping) return expectedJobId === null;
            if (
                mapping.mapping_job_id !== expectedJobId
                || mapping.mode !== expectedMode
                || mapping.run_id !== runId
                || mapping.observed_generation !== observedGeneration
                || mapping.raw_representation_id !== expectedRawRepresentationId
                || mapping.move_source_id !== expectedMoveSourceId
                || mapping.mapping_profile_id !== expectedMappingProfileId
            ) return false;
            if (expectedMode === 'signal_to_read') {
                return mapping.reference_revision_id === null
                    && mapping.alignment_job_id === null
                    && mapping.alignment_session_id === null
                    && mapping.parent_mapping_job_id === null;
            }
            return mapping.alignment_job_id === alignmentJobId
                && mapping.alignment_session_id === alignmentSession?.session_id
                && mapping.reference_revision_id === referenceRevisionId;
        };
        if (
            !mappingMatchesAuthority(nextReadMapping, readJobId, 'signal_to_read')
            || !mappingMatchesAuthority(nextReferenceMapping, referenceJobId, 'signal_to_reference')
            || (nextReferenceMapping !== null && (
                nextReadMapping === null
                || nextReferenceMapping.parent_mapping_job_id !== nextReadMapping.mapping_job_id
            ))
        ) {
            throw new Error('Mapping job tuple does not match the viewer immutable authority.');
        }
        setReadMapping(nextReadMapping);
        setReferenceMapping(nextReferenceMapping);
        setCalibration(nextCalibration);
    }, [
        alignmentJobId,
        alignmentSession?.session_id,
        observedGeneration,
        persistedReadMappingJobId,
        persistedReferenceMappingJobId,
        referenceRevisionId,
        runId,
        viewerSession?.alignment_job_id,
        viewerSession?.alignment_session_id,
        viewerSession?.mapping_profile_id,
        viewerSession?.move_source_id,
        viewerSession?.observed_generation,
        viewerSession?.raw_representation_id,
        viewerSession?.reference_revision_id,
        viewerSession?.run_id,
        viewerSession?.viewer_session_id,
    ]);

    useEffect(() => {
        identityRef.current += 1;
        setCapabilities(null);
        setExternalMoveBamCandidates([]);
        setExternalMoveBamCandidateId('');
        setExternalMoveBamMoleculeType('dna');
        setExternalMoveBamAvailability(null);
        setMoveSources([]);
        setRegisteredExternalMoveSourceId(null);
        setProfiles([]);
        setCalibration(null);
        setReadMapping(null);
        setReferenceMapping(null);
        replaceViewJob(null);
        replaceArtifactUrl(null);
        setSelectedRead(null);
        setEligibleReads([]);
        setReadId('');
        setContig('');
        setStart('');
        setEnd('');
        setMode('read');
        setRenderParams(DEFAULT_ONT_SIGNAL_RENDER_PARAMS);
        setProfileBaseShiftValue(0);
        setBusy(false);
        setError(null);
        const generation = identityRef.current;
        void refreshAuthorities().catch((reason) => {
            if (generation === identityRef.current) setError(message(reason));
        });
    }, [identityKey, refreshAuthorities, replaceArtifactUrl, replaceViewJob]);

    useEffect(() => () => {
        identityRef.current += 1;
        artifactRequestGenerationRef.current += 1;
        const current = artifactUrlRef.current;
        artifactUrlRef.current = null;
        if (current) URL.revokeObjectURL(current);
    }, []);

    useEffect(() => {
        const locus = currentLocus || (viewerSession?.contig && viewerSession.locus_start && viewerSession.locus_end ? {
            contig: viewerSession.contig,
            start: viewerSession.locus_start,
            end: viewerSession.locus_end,
        } : null);
        if (locus) {
            setContig(locus.contig);
            setStart(String(locus.start));
            setEnd(String(locus.end));
        }
    }, [currentLocus, identityKey, viewerSession?.contig, viewerSession?.locus_end, viewerSession?.locus_start]);

    useEffect(() => {
        if (!viewerSession) return;
        setReadId(viewerSession.selected_read_id || '');
        const savedMode = viewerSession.signal_state.mode;
        if (savedMode === 'raw_waveform' || savedMode === 'read' || savedMode === 'reference' || savedMode === 'pileup' || savedMode === 'ideal_comparison') setMode(savedMode);
        if (isRenderParams(viewerSession.signal_state.render_params)) {
            setRenderParams({ ...DEFAULT_ONT_SIGNAL_RENDER_PARAMS, ...viewerSession.signal_state.render_params });
        }
        const savedViewJobId = viewerSession.signal_state.view_job_id;
        if (typeof savedViewJobId === 'string' && savedViewJobId) {
            const generation = identityRef.current;
            const viewGeneration = viewJobGenerationRef.current;
            void fetchOntSignalView(savedViewJobId).then((job) => {
                if (
                    generation === identityRef.current
                    && viewGeneration === viewJobGenerationRef.current
                ) replaceViewJob(job);
            }).catch((reason) => {
                if (
                    generation === identityRef.current
                    && viewGeneration === viewJobGenerationRef.current
                ) setError(message(reason));
            });
        }
    }, [replaceViewJob, viewerSession?.viewer_session_id]);

    useEffect(() => {
        if (viewerSession || !datasetId || !runId || !observedGeneration || !alignmentJobId) return;
        const createKey = identityKey;
        if (viewerCreateKeyRef.current === createKey) return;
        viewerCreateKeyRef.current = createKey;
        const generation = identityRef.current;
        void createOntSignalViewerSession({
            dataset_id: datasetId,
            run_id: runId,
            observed_generation: observedGeneration,
            alignment_job_id: alignmentJobId,
            alignment_session_id: viewerAlignmentSessionId,
            reference_revision_id: viewerReferenceRevisionId,
            contig: currentLocus?.contig || null,
            locus_start: currentLocus?.start || null,
            locus_end: currentLocus?.end || null,
            selected_read_id: readId.trim() || null,
            igv_state: igvState,
            signal_state: {
                mode,
                render_params: renderParams,
                view_job_id: viewJob?.view_job_id || null,
                read_mapping_job_id: readMapping?.mapping_job_id || null,
                reference_mapping_job_id: referenceMapping?.mapping_job_id || null,
            },
        }).then((created) => {
            if (generation === identityRef.current) onViewerSessionChange(created);
        }).catch((reason) => {
            if (generation === identityRef.current) {
                viewerCreateKeyRef.current = '';
                setError(`Viewer session could not be persisted: ${message(reason)}`);
            }
        });
    }, [alignmentJobId, alignmentSession?.ready, alignmentSession?.session_id, currentLocus?.contig, currentLocus?.end, currentLocus?.start, datasetId, identityKey, igvState, mode, observedGeneration, onViewerSessionChange, readId, readMapping?.mapping_job_id, referenceMapping?.mapping_job_id, referenceRevisionId, renderParams, runId, viewJob?.view_job_id, viewerAlignmentSessionId, viewerReferenceRevisionId, viewerSession]);

    const activeExternalMoveSource = useMemo(() => (
        registeredExternalMoveSourceId
            ? moveSources.find((item) => item.move_source_id === registeredExternalMoveSourceId
                && item.external_registration_receipt_id
                && (item.state === 'requested' || item.state === 'running'))
            : null
    ), [moveSources, registeredExternalMoveSourceId]);

    useEffect(() => {
        if (!activeExternalMoveSource) return undefined;
        const generation = identityRef.current;
        const moveSourceId = activeExternalMoveSource.move_source_id;
        const controller = new AbortController();
        let inFlight = false;
        let requestSequence = 0;
        const poll = async () => {
            if (inFlight || controller.signal.aborted) return;
            inFlight = true;
            const sequence = ++requestSequence;
            try {
                const payload = await fetchOntMoveSources(runId, observedGeneration, controller.signal);
                if (
                    controller.signal.aborted
                    || generation !== identityRef.current
                    || sequence !== requestSequence
                ) return;
                const next = payload.items.find((item) => item.move_source_id === moveSourceId);
                setMoveSources(payload.items);
                if (next && TERMINAL_STATES.has(next.state)) {
                    window.clearInterval(handle);
                    if (next.state === 'ready') {
                        void refreshAuthorities().catch((reason) => {
                            if (generation === identityRef.current && !controller.signal.aborted) setError(message(reason));
                        });
                    }
                }
            } catch (reason) {
                if (!controller.signal.aborted && generation === identityRef.current) setError(message(reason));
            } finally {
                if (sequence === requestSequence) inFlight = false;
            }
        };
        const handle = window.setInterval(() => {
            void poll();
        }, 1500);
        return () => {
            controller.abort();
            window.clearInterval(handle);
        };
    }, [
        activeExternalMoveSource?.move_source_id,
        activeExternalMoveSource?.state,
        observedGeneration,
        refreshAuthorities,
        runId,
    ]);

    const pollMapping = useCallback((mapping: OntSignalMappingJob | null, setter: (value: OntSignalMappingJob) => void) => {
        if (!mapping || TERMINAL_STATES.has(mapping.state)) return undefined;
        const generation = identityRef.current;
        const handle = window.setInterval(() => {
            void fetchOntSignalMapping(mapping.mapping_job_id).then((next) => {
                if (generation !== identityRef.current) return;
                setter(next);
                if (TERMINAL_STATES.has(next.state)) {
                    window.clearInterval(handle);
                    void refreshAuthorities().catch((reason) => {
                        if (generation === identityRef.current) setError(message(reason));
                    });
                }
            }).catch((reason) => {
                if (generation === identityRef.current) setError(message(reason));
            });
        }, 1500);
        return () => window.clearInterval(handle);
    }, [refreshAuthorities]);

    useEffect(() => pollMapping(readMapping, setReadMapping), [pollMapping, readMapping?.mapping_job_id, readMapping?.state]);
    useEffect(() => pollMapping(referenceMapping, setReferenceMapping), [pollMapping, referenceMapping?.mapping_job_id, referenceMapping?.state]);

    useEffect(() => {
        if (!calibration || TERMINAL_STATES.has(calibration.state)) return undefined;
        const generation = identityRef.current;
        const handle = window.setInterval(() => {
            void fetchOntSignalCalibration(calibration.calibration_job_id).then((next) => {
                if (generation !== identityRef.current) return;
                setCalibration(next);
                if (TERMINAL_STATES.has(next.state)) {
                    window.clearInterval(handle);
                    void refreshAuthorities().catch((reason) => {
                        if (generation === identityRef.current) setError(message(reason));
                    });
                }
            }).catch((reason) => {
                if (generation === identityRef.current) setError(message(reason));
            });
        }, 1500);
        return () => window.clearInterval(handle);
    }, [calibration?.calibration_job_id, calibration?.state, refreshAuthorities]);

    useEffect(() => {
        if (!viewJob || TERMINAL_STATES.has(viewJob.state)) return undefined;
        const generation = identityRef.current;
        const viewGeneration = viewJobGenerationRef.current;
        const viewJobId = viewJob.view_job_id;
        const handle = window.setInterval(() => {
            void fetchOntSignalView(viewJobId).then((next) => {
                if (
                    generation !== identityRef.current
                    || viewGeneration !== viewJobGenerationRef.current
                    || viewJobIdRef.current !== viewJobId
                ) return;
                replaceViewJob(next);
                if (TERMINAL_STATES.has(next.state)) window.clearInterval(handle);
            }).catch((reason) => {
                if (
                    generation === identityRef.current
                    && viewGeneration === viewJobGenerationRef.current
                    && viewJobIdRef.current === viewJobId
                ) setError(message(reason));
            });
        }, 1500);
        return () => window.clearInterval(handle);
    }, [replaceViewJob, viewJob?.state, viewJob?.view_job_id]);

    useEffect(() => {
        const requestGeneration = artifactRequestGenerationRef.current + 1;
        artifactRequestGenerationRef.current = requestGeneration;
        const readyViewJob = viewJob?.state === 'ready' ? viewJob : null;
        const html = readyViewJob?.output_manifest.artifacts.find((item) => item.media_type === 'text/html' && item.url);
        if (!readyViewJob || !html) {
            replaceArtifactUrl(null);
            return;
        }
        const identityGeneration = identityRef.current;
        const viewJobId = readyViewJob.view_job_id;
        void fetchOntSignalViewArtifact(viewJobId, html.artifact_id).then(async (blob) => {
            if (
                identityGeneration !== identityRef.current
                || requestGeneration !== artifactRequestGenerationRef.current
            ) return;
            const source = await readHtmlBlob(blob);
            if (
                identityGeneration !== identityRef.current
                || requestGeneration !== artifactRequestGenerationRef.current
            ) return;
            const next = URL.createObjectURL(secureGovernedHtml(source));
            replaceArtifactUrl(next);
        }).catch((reason) => {
            if (
                identityGeneration === identityRef.current
                && requestGeneration === artifactRequestGenerationRef.current
            ) setError(`Bounded artifact could not be loaded: ${message(reason)}`);
        });
    }, [replaceArtifactUrl, viewJob?.state, viewJob?.view_job_id]);

    const compatibleSource = useMemo(() => {
        const resolvedId = viewerSession
            ? viewerSession.move_source_id
            : capabilities?.resolved.move_source_id;
        return resolvedId ? moveSources.find((item) => item.move_source_id === resolvedId) || null : null;
    }, [capabilities?.resolved.move_source_id, moveSources, viewerSession?.move_source_id, viewerSession?.viewer_session_id]);
    const compatibleProfile = useMemo(() => {
        const resolvedId = viewerSession
            ? viewerSession.mapping_profile_id || readMapping?.mapping_profile_id || referenceMapping?.mapping_profile_id
            : capabilities?.resolved.mapping_profile_id;
        return profiles.find((item) => item.mapping_profile_id === resolvedId)
            || (!viewerSession && profiles.find((item) => compatibleSource
                && item.basecall_model_id === compatibleSource.basecall_model_id
                && item.molecule_type === compatibleSource.molecule_type
                && item.primary_alignment_policy === 'primary_only'
                && item.minimum_mapq === 0
                && item.include_supplementary === false
                && item.read_set_selection === 'immutable_full_set'
                && calibration?.artifact
                && item.calibration_artifact_id === calibration.artifact.calibration_artifact_id))
            || null;
    }, [calibration?.artifact, capabilities?.resolved.mapping_profile_id, compatibleSource, profiles, readMapping?.mapping_profile_id, referenceMapping?.mapping_profile_id, viewerSession?.mapping_profile_id, viewerSession?.viewer_session_id]);

    const registerExternalMoveBam = async () => {
        if (!activeRawRepresentationId || !externalMoveBamCandidateId) {
            setError('Select one path-opaque external move BAM and an exact ready indexed BLOW5 authority.');
            return;
        }
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            const created = await registerOntExternalMoveBamCandidate(runId, observedGeneration, {
                candidate_id: externalMoveBamCandidateId,
                raw_representation_id: activeRawRepresentationId,
                molecule_type: externalMoveBamMoleculeType,
            });
            if (generation !== identityRef.current) return;
            setRegisteredExternalMoveSourceId(created.move_source_id);
            setMoveSources((current) => [
                ...current.filter((item) => item.move_source_id !== created.move_source_id),
                created,
            ]);
            if (created.state === 'ready') {
                await refreshAuthorities();
            }
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const createFreshExternalMoveSourceAttempt = async (predecessorMoveSourceId: string) => {
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            const created = await createOntFreshMoveSourceAttempt(predecessorMoveSourceId);
            if (generation !== identityRef.current) return;
            setRegisteredExternalMoveSourceId(created.move_source_id);
            setMoveSources((current) => [
                ...current.filter((item) => item.move_source_id !== created.move_source_id),
                created,
            ]);
            if (created.state === 'ready') await refreshAuthorities();
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const prepareMapping = async (mappingMode: OntSignalMappingMode) => {
        const rawRepresentationId = activeRawRepresentationId;
        if (!rawRepresentationId || !compatibleSource) {
            setError('No exact ready indexed BLOW5 and move-source authority is available.');
            return;
        }
        if (mappingMode === 'signal_to_reference' && (!referenceRevisionId || !alignmentSession?.ready || readMapping?.state !== 'ready')) {
            setError('Signal-to-reference preparation requires the selected managed reference, ready alignment session, and ready signal-to-read mapping.');
            return;
        }
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            let profile = compatibleProfile;
            if (!profile) {
                if (!calibration?.artifact || calibration.state !== 'ready') {
                    if (calibration && (calibration.state === 'failed' || calibration.state === 'cancelled')) {
                        throw new Error(`Calibration cannot be approved: ${calibration.reason_code}${calibration.failure_message ? ` — ${calibration.failure_message}` : ''}`);
                    }
                    if (calibration && !TERMINAL_STATES.has(calibration.state)) {
                        throw new Error(`Calibration is ${calibration.state}: ${calibration.reason_code}`);
                    }
                    const createdCalibration = await createOntSignalCalibration(runId, observedGeneration, {
                        raw_representation_id: rawRepresentationId,
                        move_source_id: compatibleSource.move_source_id,
                        sample_count: 100,
                    });
                    if (generation !== identityRef.current) return;
                    setCalibration(createdCalibration);
                    return;
                }
                const evidence = calibration.artifact;
                if (evidence.raw_representation_id !== rawRepresentationId
                    || evidence.move_source_id !== compatibleSource.move_source_id
                    || evidence.basecall_model_id !== compatibleSource.basecall_model_id) {
                    throw new Error('Ready calibration evidence is not exact for the selected governed parents.');
                }
                profile = await createOntSignalMappingProfile({
                    name: `Calibrated ${evidence.basecall_model_id}`.slice(0, 255),
                    molecule_type: compatibleSource.molecule_type,
                    basecall_model_id: evidence.basecall_model_id,
                    kmer_length: evidence.recommended_kmer_length,
                    signal_move_offset: evidence.recommended_signal_move_offset,
                    base_shift_value: profileBaseShiftValue,
                    parameter_source: 'approved_calibration',
                    calibration_artifact_id: evidence.calibration_artifact_id,
                    primary_alignment_policy: 'primary_only',
                    minimum_mapq: 0,
                    include_supplementary: false,
                    read_set_selection: 'immutable_full_set',
                    approval_receipt: {
                        approved: true,
                        action: 'fresh_explicit_prepare_click',
                        calibration_artifact_id: evidence.calibration_artifact_id,
                        calibration_artifact_sha256: evidence.artifact_sha256,
                        base_shift_value: profileBaseShiftValue,
                        policy: { primary_alignment_policy: 'primary_only', minimum_mapq: 0, include_supplementary: false, read_set_selection: 'immutable_full_set' },
                    },
                    approved_by: null,
                });
                if (generation !== identityRef.current) return;
                setProfiles((current) => [...current.filter((item) => item.mapping_profile_id !== profile!.mapping_profile_id), profile!]);
            }
            if (!profile) throw new Error('Exact approved mapping profile was not created.');
            const created = await createOntSignalMapping(runId, observedGeneration, {
                mode: mappingMode,
                raw_representation_id: rawRepresentationId,
                move_source_id: compatibleSource.move_source_id,
                mapping_profile_id: profile.mapping_profile_id,
                reference_revision_id: mappingMode === 'signal_to_reference' ? referenceRevisionId : null,
                alignment_job_id: mappingMode === 'signal_to_reference' ? alignmentJobId : null,
                alignment_session_id: mappingMode === 'signal_to_reference' ? alignmentSession?.session_id || null : null,
            });
            if (generation !== identityRef.current) return;
            if (mappingMode === 'signal_to_read') setReadMapping(created);
            else setReferenceMapping(created);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const mappingForMode = mode === 'raw_waveform' ? null : mode === 'read' ? readMapping : referenceMapping;
    const mappingArtifact = mappingForMode?.artifacts.find((item) => (
        mode === 'read' ? item.kind === 'reform_paf' : item.kind === 'realign_paf'
    )) || null;
    const filteredEligibleReads = useMemo(
        () => filterAlignmentReads(eligibleReads, readFilterPreset, readSearch),
        [eligibleReads, readFilterPreset, readSearch],
    );

    const inspectExactRead = async () => {
        const exact = readId.trim();
        if (!exact || !alignmentSession) return;
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            const detail = await fetchAlignmentRead(alignmentJobId, alignmentSession.session_id, exact, {
                contig: contig.trim() || undefined,
                start: integer(start) || undefined,
                end: integer(end) || undefined,
            });
            if (generation !== identityRef.current) return;
            setSelectedRead(detail);
            setReadId(detail.read_id);
        } catch (reason) {
            if (generation === identityRef.current) {
                setSelectedRead(null);
                setError(message(reason));
            }
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const loadLocusReads = async () => {
        const locusStart = integer(start);
        const locusEnd = integer(end);
        if (!alignmentSession || !contig.trim() || !locusStart || !locusEnd || locusEnd < locusStart) {
            setError('A complete 1-based reference locus is required.');
            return;
        }
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            const page = await fetchAlignmentReads(alignmentJobId, alignmentSession.session_id, {
                contig: contig.trim(), start: locusStart, end: locusEnd, limit: 200,
            });
            if (generation !== identityRef.current) return;
            setEligibleReads(page.reads);
            if (page.reads.length > 0 && !page.reads.some((item) => item.read_id === readId.trim())) {
                setReadId(page.reads[0].read_id);
                setSelectedRead(page.reads[0]);
            }
        } catch (reason) {
            if (generation === identityRef.current) {
                setEligibleReads([]);
                setError(message(reason));
            }
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const moveRead = (delta: number) => {
        if (filteredEligibleReads.length === 0) return;
        const currentIndex = filteredEligibleReads.findIndex((item) => item.read_id === readId.trim());
        const nextIndex = Math.min(filteredEligibleReads.length - 1, Math.max(0, (currentIndex < 0 ? 0 : currentIndex) + delta));
        const next = filteredEligibleReads[nextIndex];
        setReadId(next.read_id);
        setSelectedRead(next);
    };

    const render = async () => {
        if (mode === 'raw_waveform' || mode === 'ideal_comparison') return;
        const locusStart = integer(start);
        const locusEnd = integer(end);
        if (!mappingArtifact) {
            setError(`A ready validated ${mode === 'read' ? 'signal-to-read' : 'signal-to-reference'} mapping artifact is required.`);
            return;
        }
        if (mode === 'read' && !readId.trim()) {
            setError('Enter one exact read ID.');
            return;
        }
        if (mode !== 'read' && (!contig.trim() || !locusStart || !locusEnd || locusEnd < locusStart)) {
            setError('Enter one complete bounded 1-based reference locus.');
            return;
        }
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        replaceArtifactUrl(null);
        try {
            const created = await createOntSignalView({
                mapping_artifact_id: mappingArtifact.mapping_artifact_id,
                mode,
                read_id: mode === 'read' ? readId.trim() : null,
                reference_contig: mode === 'read' ? null : contig.trim(),
                reference_start: mode === 'read' ? null : locusStart,
                reference_end: mode === 'read' ? null : locusEnd,
                render_params: mode === 'pileup' ? { ...renderParams, loose_bound: false } : renderParams,
            });
            if (generation !== identityRef.current) return;
            replaceViewJob(created);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const persistSession = async () => {
        if (!viewerSession) {
            setError('Viewer session creation is still pending.');
            return;
        }
        const generation = identityRef.current;
        setBusy(true);
        setError(null);
        try {
            const saved = await updateOntSignalViewerSession(viewerSession.viewer_session_id, {
                expected_revision: viewerSession.revision,
                contig: contig.trim() || null,
                locus_start: integer(start),
                locus_end: integer(end),
                selected_read_id: readId.trim() || null,
                igv_state: igvState,
                signal_state: {
                    mode,
                    render_params: renderParams,
                    view_job_id: viewJob?.view_job_id || null,
                    read_mapping_job_id: readMapping?.mapping_job_id || null,
                    reference_mapping_job_id: referenceMapping?.mapping_job_id || null,
                },
            });
            if (generation !== identityRef.current) return;
            onViewerSessionChange(saved);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        } finally {
            if (generation === identityRef.current) setBusy(false);
        }
    };

    const cancelMappingForIdentity = async (mapping: OntSignalMappingJob, target: 'read' | 'reference') => {
        const generation = identityRef.current;
        try {
            const cancelled = await cancelOntSignalMapping(mapping.mapping_job_id);
            if (generation !== identityRef.current) return;
            if (target === 'read') setReadMapping(cancelled);
            else setReferenceMapping(cancelled);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        }
    };

    const cancelCalibrationForIdentity = async (job: OntSignalCalibrationJob) => {
        const generation = identityRef.current;
        try {
            const cancelled = await cancelOntSignalCalibration(job.calibration_job_id);
            if (generation === identityRef.current) setCalibration(cancelled);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        }
    };

    const cancelViewForIdentity = async (job: OntSignalViewJob) => {
        const generation = identityRef.current;
        try {
            const cancelled = await cancelOntSignalView(job.view_job_id);
            if (generation === identityRef.current) replaceViewJob(cancelled);
        } catch (reason) {
            if (generation === identityRef.current) setError(message(reason));
        }
    };

    const locateRead = () => {
        if (!selectedRead?.contig || !selectedRead.start_1based) {
            setError('The selected read has no governed mapped locus.');
            return;
        }
        const readEnd = selectedRead.start_1based + Math.max(1, selectedRead.length || 1) - 1;
        onNavigateIgv(selectedRead.contig, selectedRead.start_1based, readEnd, 'selected raw-signal read');
    };

    const openMappedLocus = () => {
        const locusStart = viewJob?.reference_region?.start || integer(start);
        const locusEnd = viewJob?.reference_region?.end || integer(end);
        const locusContig = viewJob?.reference_region?.contig || contig.trim();
        if (!locusContig || !locusStart || !locusEnd) {
            setError('No mapped reference locus is available.');
            return;
        }
        onNavigateIgv(locusContig, locusStart, locusEnd, 'signal view');
    };

    return (
        <aside
            data-signal-workbench-panel
            className="absolute right-0 top-0 bottom-0 z-20 w-full lg:w-[560px] min-w-0 border-l border-[var(--border-primary)] bg-[var(--bg-secondary)]/98 shadow-2xl flex flex-col"
        >
            <header className="border-b border-[var(--border-primary)] px-3 py-2 space-y-2">
                <div className="flex items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold text-[var(--text-primary)]">Read and signal</h2>
                    <button type="button" onClick={() => void persistSession()} disabled={busy || !viewerSession} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[11px] disabled:opacity-40">
                        Save session
                    </button>
                </div>
                {error && <div role="alert" className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">{error}</div>}
            </header>

            <div data-signal-workbench-scroll onWheel={(event) => event.stopPropagation()} className="flex-1 min-h-0 overflow-y-scroll overscroll-contain [scrollbar-gutter:stable] p-3 space-y-3">
                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <h3 className="text-xs font-semibold">Read and locus</h3>
                        <button type="button" onClick={() => void loadLocusReads()} disabled={busy || !alignmentSession?.ready} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Reads in locus</button>
                    </div>
                    <div className="grid grid-cols-1 gap-1 sm:grid-cols-[150px_1fr]">
                        <select aria-label="Read filter preset" value={readFilterPreset} onChange={(event) => setReadFilterPreset(event.target.value as AlignmentReadFilterPreset)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs">
                            <option value="all">All eligible reads</option>
                            <option value="clean">Clean reads</option>
                            <option value="substitution_rich">Reference-substitution-rich reads</option>
                            <option value="indels_gaps">Indels and gaps</option>
                            <option value="clipped">Clipped reads</option>
                        </select>
                        <input aria-label="Search eligible reads" value={readSearch} onChange={(event) => setReadSearch(event.target.value)} placeholder="Search loaded reads" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                    </div>
                    <div className="grid grid-cols-[1fr_76px_76px] gap-1">
                        <input value={contig} onChange={(event) => setContig(event.target.value)} placeholder="Reference contig" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                        <input value={start} onChange={(event) => setStart(event.target.value)} inputMode="numeric" placeholder="Start" className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                        <input value={end} onChange={(event) => setEnd(event.target.value)} inputMode="numeric" placeholder="End" className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                    </div>
                    <div className="flex flex-wrap gap-1">
                        <button type="button" onClick={() => moveRead(-1)} disabled={filteredEligibleReads.length === 0} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Previous eligible read</button>
                        <button type="button" onClick={() => moveRead(1)} disabled={filteredEligibleReads.length === 0} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Next eligible read</button>
                        <button type="button" onClick={locateRead} disabled={!selectedRead} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Locate read in IGV</button>
                        <button type="button" onClick={openMappedLocus} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">Open mapped locus in IGV</button>
                    </div>
                    <div role="listbox" aria-label="Eligible reads" className="max-h-48 space-y-1 overflow-y-auto rounded border border-[var(--border-primary)] p-1">
                        {filteredEligibleReads.map((read) => (
                            <button key={read.read_id} type="button" role="option" aria-selected={read.read_id === readId.trim()} onClick={() => { setReadId(read.read_id); setSelectedRead(read); }} className={`block w-full rounded px-2 py-1 text-left text-[10px] ${read.read_id === readId.trim() ? 'bg-sky-500/20 text-sky-100' : 'hover:bg-[var(--bg-secondary)]'}`}>
                                <span className="block font-medium">{formatAlignmentReadSummary(read)}</span>
                                <span className="block truncate text-[var(--text-muted)]">{read.contig}:{read.start_1based} · {read.strand}</span>
                            </button>
                        ))}
                        {eligibleReads.length > 0 && filteredEligibleReads.length === 0 && <div className="px-2 py-3 text-center text-[10px] text-[var(--text-muted)]">No loaded reads match this filter.</div>}
                        {eligibleReads.length === 0 && <div className="px-2 py-3 text-center text-[10px] text-[var(--text-muted)]">Click a read in IGV or load reads in the current locus.</div>}
                    </div>
                    <details>
                        <summary className="cursor-pointer text-[10px] text-[var(--text-secondary)]">Exact read ID recovery</summary>
                        <div className="mt-1 grid grid-cols-[1fr_auto] gap-1">
                            <input value={readId} onChange={(event) => setReadId(event.target.value)} placeholder="Exact read ID" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs font-mono" />
                            <button type="button" onClick={() => void inspectExactRead()} disabled={busy || !alignmentSession?.ready || !readId.trim()} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Resolve</button>
                        </div>
                    </details>
                    {selectedRead && (
                        <div className="rounded bg-[var(--bg-secondary)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                            <code className="text-[var(--text-primary)]">{selectedRead.read_id}</code> · {selectedRead.contig || 'unmapped'}:{selectedRead.start_1based ?? 'n/a'} · {selectedRead.strand} · MAPQ {selectedRead.mapq ?? 'n/a'}
                        </div>
                    )}
                </section>

                <details data-signal-diagnostics className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 text-[10px]">
                    <summary className="cursor-pointer font-semibold text-[var(--text-secondary)]">Diagnostics</summary>
                    <div className="mt-2 space-y-2">
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-1">
                            {(['igv', 'raw_waveform', 'signal_to_read', 'signal_to_reference', 'signal_pileup'] as const).map((name) => {
                                const capability = capabilities?.modes[name];
                                return (
                                    <div key={name} title={capability?.reason_code || 'loading'} className="rounded border border-[var(--border-primary)] px-1.5 py-1">
                                        <div className="truncate text-[var(--text-secondary)]">{name.replaceAll('_', ' ')}</div>
                                        <span className={`inline-block rounded px-1 ${stateBadge(capability?.state || 'loading')}`}>{capability?.state || 'loading'}</span>
                                    </div>
                                );
                            })}
                        </div>

                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <h3 className="text-xs font-semibold">External move BAM</h3>
                        <span className="text-[10px] text-[var(--text-secondary)]">Server candidates</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_80px_auto] gap-1 text-[10px]">
                        <select
                            aria-label="External move BAM candidate"
                            value={externalMoveBamCandidateId}
                            onChange={(event) => setExternalMoveBamCandidateId(event.target.value)}
                            disabled={busy || externalMoveBamCandidates.length === 0}
                            className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 disabled:opacity-40"
                        >
                            {externalMoveBamCandidates.length === 0 && <option value="">No candidates</option>}
                            {externalMoveBamCandidates.map((candidate) => (
                                <option key={candidate.candidate_id} value={candidate.candidate_id}>
                                    {candidate.display_name} ({candidate.size_bytes} bytes)
                                </option>
                            ))}
                        </select>
                        <select
                            aria-label="External move BAM molecule type"
                            value={externalMoveBamMoleculeType}
                            onChange={(event) => setExternalMoveBamMoleculeType(event.target.value as 'dna' | 'rna')}
                            disabled={busy}
                            className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 disabled:opacity-40"
                        >
                            <option value="dna">DNA</option>
                            <option value="rna">RNA</option>
                        </select>
                        <button
                            type="button"
                            onClick={() => void registerExternalMoveBam()}
                            disabled={busy || !activeRawRepresentationId || !externalMoveBamCandidateId}
                            className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40"
                        >
                            Register external move BAM
                        </button>
                    </div>
                    <div className="text-[10px] text-[var(--text-secondary)]">
                        {externalMoveBamAvailability || 'Selection binds immutable bytes to this exact run, generation, and raw representation before independent move-tag validation.'}
                    </div>
                    {displayedActiveExternalMoveSource && (
                        <div data-active-external-move-source className="flex flex-wrap items-center gap-1 break-all text-[10px] text-[var(--text-secondary)]">
                            <span>Active external source <code>{displayedActiveExternalMoveSource.move_source_id}</code> · <span className={`rounded px-1 ${stateBadge(displayedActiveExternalMoveSource.state)}`}>{displayedActiveExternalMoveSource.state}</span> · {displayedActiveExternalMoveSource.reason_code}</span>
                        </div>
                    )}
                    {externalMoveSourceHistory.length > 0 && (
                        <details className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                            <summary className="cursor-pointer">Attempt history ({externalMoveSourceHistory.length})</summary>
                            <div className="mt-1 space-y-1">
                                {externalMoveSourceHistory.map((item) => (
                                    <div key={item.move_source_id} className="flex flex-wrap items-center gap-1 break-all">
                                        <span>External source <code>{item.move_source_id}</code> · <span className={`rounded px-1 ${stateBadge(item.state)}`}>{item.state}</span> · {item.reason_code}</span>
                                        {item.state === 'failed'
                                            && item.attempt_number < 3
                                            && !moveSources.some((candidate) => candidate.predecessor_move_source_id === item.move_source_id)
                                            && (
                                                <button
                                                    type="button"
                                                    onClick={() => void createFreshExternalMoveSourceAttempt(item.move_source_id)}
                                                    disabled={busy}
                                                    className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40"
                                                >
                                                    Create fresh attempt
                                                </button>
                                            )}
                                    </div>
                                ))}
                            </div>
                        </details>
                    )}
                </section>

                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <h3 className="text-xs font-semibold">Governed mappings</h3>
                        <span className="text-[10px] text-[var(--text-secondary)]">Reusable across bounded views</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px]">
                        <div className="rounded border border-[var(--border-primary)] p-2">
                            <div className="flex items-center justify-between"><span>Signal to read</span><span className={`rounded px-1 ${stateBadge(readMapping?.state || capabilities?.modes.signal_to_read.state || 'loading')}`}>{readMapping?.state || capabilities?.modes.signal_to_read.state || 'loading'}</span></div>
                            <div className="mt-1 break-all text-[var(--text-secondary)]">{readMapping?.reason_code || capabilities?.modes.signal_to_read.reason_code}</div>
                            {readMapping && !TERMINAL_STATES.has(readMapping.state) && <button type="button" onClick={() => void cancelMappingForIdentity(readMapping, 'read')} className="mt-2 rounded border border-[var(--border-primary)] px-2 py-1">Cancel</button>}
                        </div>
                        <div className="rounded border border-[var(--border-primary)] p-2">
                            <div className="flex items-center justify-between"><span>Signal to reference</span><span className={`rounded px-1 ${stateBadge(referenceMapping?.state || capabilities?.modes.signal_to_reference.state || 'loading')}`}>{referenceMapping?.state || capabilities?.modes.signal_to_reference.state || 'loading'}</span></div>
                            <div className="mt-1 break-all text-[var(--text-secondary)]">{referenceMapping?.reason_code || capabilities?.modes.signal_to_reference.reason_code}</div>
                            {referenceMapping && !TERMINAL_STATES.has(referenceMapping.state) && <button type="button" onClick={() => void cancelMappingForIdentity(referenceMapping, 'reference')} className="mt-2 rounded border border-[var(--border-primary)] px-2 py-1">Cancel</button>}
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[10px]">
                        <select value={preparationMode} onChange={(event) => setPreparationMode(event.target.value as OntSignalMappingMode)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1">
                            <option value="signal_to_read">signal to read</option>
                            <option value="signal_to_reference">signal to reference</option>
                        </select>
                        <label>profile base shift<input aria-label="Mapping profile base shift" type="number" min={-64} max={64} disabled={compatibleProfile !== null} value={compatibleProfile?.base_shift_value ?? profileBaseShiftValue} onChange={(event) => setProfileBaseShiftValue(Number(event.target.value))} className="ml-1 w-16 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] disabled:opacity-40" /></label>
                        <button type="button" onClick={() => void prepareMapping(preparationMode)} disabled={busy || (!!calibration && !TERMINAL_STATES.has(calibration.state)) || (preparationMode === 'signal_to_reference' && (!referenceRevisionId || !alignmentSession?.ready || readMapping?.state !== 'ready'))} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40">Prepare aligned signal</button>
                        {calibration && !TERMINAL_STATES.has(calibration.state) && <button type="button" onClick={() => void cancelCalibrationForIdentity(calibration)} className="rounded border border-[var(--border-primary)] px-2 py-1">Cancel calibration</button>}
                    </div>
                    <div className="rounded border border-[var(--border-primary)] p-2 text-[10px] space-y-1">
                        <div>Calibration <span className={`rounded px-1 ${stateBadge(calibration?.state || (compatibleProfile ? 'ready' : 'unavailable'))}`}>{calibration?.state || (compatibleProfile ? 'approved profile ready' : 'not requested')}</span></div>
                        <div className="break-all text-[var(--text-secondary)]">{calibration?.reason_code || (compatibleProfile ? 'exact approved profile available' : 'fresh click will request deterministic calibration')}</div>
                        {calibration?.failure_message && <div className="text-rose-200">{calibration.failure_code}: {calibration.failure_message}</div>}
                        {calibration?.artifact && <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-secondary)] p-1 text-[9px]">{JSON.stringify({
                            artifact_id: calibration.artifact.calibration_artifact_id,
                            artifact_sha256: calibration.artifact.artifact_sha256,
                            selection: calibration.artifact.sample_selection,
                            recommendation: { kmer_length: calibration.artifact.recommended_kmer_length, signal_move_offset: calibration.artifact.recommended_signal_move_offset },
                            score_evidence: calibration.artifact.score_evidence,
                            parent_sha256s: calibration.artifact.parent_sha256s,
                            runtime_identity: calibration.artifact.runtime_identity,
                        }, null, 2)}</pre>}
                    </div>
                    <div className="text-[10px] text-[var(--text-secondary)] break-all">BLOW5 {activeRawRepresentationId || 'unresolved'} · move source {compatibleSource?.move_source_id || 'unresolved'} · profile {compatibleProfile?.mapping_profile_id || 'unresolved'} ({compatibleProfile?.name || 'no exact approved profile'}) · profile base shift {compatibleProfile?.base_shift_value ?? profileBaseShiftValue} · reference {referenceRevisionId || 'not bound'} · alignment {alignmentSession?.session_id || 'not bound'}</div>
                </section>

                        <details className="rounded border border-[var(--border-primary)] px-2 py-1">
                            <summary className="cursor-pointer">Technical details</summary>
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-primary)] p-2 text-[9px] text-[var(--text-secondary)]">{JSON.stringify({
                                viewer_session_id: viewerSession?.viewer_session_id || null,
                                dataset_id: datasetId,
                                run_id: runId,
                                observed_generation: observedGeneration,
                                alignment_session_id: alignmentSession?.session_id || null,
                                reference_revision_id: referenceRevisionId,
                                raw_representation_id: activeRawRepresentationId,
                                move_source: compatibleSource,
                                mapping_profile: compatibleProfile,
                                mapping: mappingForMode,
                                render: viewJob,
                            }, null, 2)}</pre>
                        </details>
                    </div>
                </details>

                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex flex-wrap items-center gap-1">
                        {([
                            { value: 'raw_waveform', label: 'Raw waveform', disabled: capabilities?.modes.raw_waveform.state !== 'ready' },
                            { value: 'read', label: 'Single read', disabled: false },
                            { value: 'reference', label: 'Reference', disabled: capabilities?.modes.signal_to_reference.state !== 'ready' },
                            { value: 'pileup', label: 'Pileup', disabled: capabilities?.modes.signal_pileup.state !== 'ready' },
                            { value: 'ideal_comparison', label: 'Ideal comparison', disabled: !viewerSession || !readId.trim() || !referenceRevisionId },
                        ] as const).map((candidate) => (
                            <button key={candidate.value} type="button" onClick={() => {
                                setMode(candidate.value);
                                if (candidate.value === 'pileup') {
                                    setRenderParams((current) => ({ ...current, loose_bound: false }));
                                }
                            }} disabled={candidate.disabled} className={`rounded border px-2 py-1 text-[10px] disabled:opacity-40 ${mode === candidate.value ? 'border-[var(--accent-secondary)] text-[var(--accent-secondary)]' : 'border-[var(--border-primary)]'}`}>{candidate.label}</button>
                        ))}
                        {mode !== 'raw_waveform' && mode !== 'ideal_comparison' && <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="ml-auto rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">{advancedOpen ? 'Hide' : 'Render settings'}</button>}
                    </div>
                    {mode === 'raw_waveform' && activeRawRepresentationId ? (
                        <GovernedRawSignalWaveform
                            key={identityKey}
                            runId={runId}
                            observedGeneration={observedGeneration}
                            representationId={activeRawRepresentationId}
                            readId={readId}
                        />
                    ) : null}
                    {mode === 'ideal_comparison' && viewerSession ? (
                        <OntSignalIdealComparison
                            datasetId={datasetId}
                            viewerSession={viewerSession}
                            selectedReadId={readId.trim()}
                            contig={contig.trim()}
                            start={integer(start)}
                            end={integer(end)}
                            mappingJobId={referenceMapping?.mapping_job_id || persistedReferenceMappingJobId}
                            mappingArtifactId={referenceMapping?.artifacts.find((item) => item.kind === 'realign_paf')?.mapping_artifact_id || null}
                            renderParams={renderParams}
                            onViewerSessionChange={onViewerSessionChange}
                        />
                    ) : null}
                    {mode !== 'raw_waveform' && mode !== 'ideal_comparison' && advancedOpen && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
                            <select value={renderParams.strand} onChange={(event) => setRenderParams((current) => ({ ...current, strand: event.target.value as OntSignalRenderParams['strand'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="forward">forward</option><option value="reverse">reverse</option></select>
                            <select value={renderParams.signal_units} onChange={(event) => setRenderParams((current) => ({ ...current, signal_units: event.target.value as OntSignalRenderParams['signal_units'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="pA">pA</option><option value="raw_adc">raw ADC</option></select>
                            <select value={renderParams.scale} onChange={(event) => setRenderParams((current) => ({ ...current, scale: event.target.value as OntSignalRenderParams['scale'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="none">no scaling</option><option value="medmad">medmad</option><option value="znorm">znorm</option><option value="scaledpA">scaledpA</option></select>
                            <label>base shift source<select aria-label="Base shift source" value={renderParams.base_shift_source} onChange={(event) => setRenderParams((current) => ({ ...current, base_shift_source: event.target.value as OntSignalRenderParams['base_shift_source'] }))} className="ml-1 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="profile">profile</option><option value="explicit">explicit</option></select></label>
                            <label>base shift<input aria-label="Base shift value" type="number" min={-64} max={64} disabled={renderParams.base_shift_source !== 'explicit'} value={renderParams.base_shift_source === 'profile' ? compatibleProfile?.base_shift_value ?? profileBaseShiftValue : renderParams.base_shift_value} onChange={(event) => setRenderParams((current) => ({ ...current, base_shift_value: Number(event.target.value) }))} className="ml-1 w-16 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] disabled:opacity-40" /></label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.fixed_width} onChange={(event) => setRenderParams((current) => ({ ...current, fixed_width: event.target.checked }))} /> fixed width</label>
                            <label>base width<input type="number" min={1} max={100} disabled={!renderParams.fixed_width} value={renderParams.base_width} onChange={(event) => setRenderParams((current) => ({ ...current, base_width: Number(event.target.value) }))} className="ml-1 w-16 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] disabled:opacity-40" /></label>
                            <label>point size<input aria-label="Point size" type="number" min={0.5} max={10} step={0.5} value={renderParams.point_size} onChange={(event) => {
                                const value = Number(event.target.value);
                                if (value === 0.5 || (Number.isInteger(value) && value >= 1 && value <= 10)) {
                                    setRenderParams((current) => ({ ...current, point_size: value }));
                                }
                            }} className="ml-1 w-16 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label>base limit<input type="number" min={1} max={100000} value={renderParams.base_limit} onChange={(event) => setRenderParams((current) => ({ ...current, base_limit: Number(event.target.value) }))} className="ml-1 w-20 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label>samples<input type="number" min={1} max={2000000} value={renderParams.signal_sample_limit} onChange={(event) => setRenderParams((current) => ({ ...current, signal_sample_limit: Number(event.target.value) }))} className="ml-1 w-24 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            {mode === 'pileup' && <label>pileup reads<input type="number" min={1} max={100} value={renderParams.pileup_read_limit} onChange={(event) => setRenderParams((current) => ({ ...current, pileup_read_limit: Number(event.target.value) }))} className="ml-1 w-14 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>}
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.show_samples} onChange={(event) => setRenderParams((current) => ({ ...current, show_samples: event.target.checked }))} /> samples</label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.show_base_colours} onChange={(event) => setRenderParams((current) => ({ ...current, show_base_colours: event.target.checked }))} /> base colours</label>
                            {mode !== 'pileup' && <label className="flex items-center gap-1"><input aria-label="Loose bound" type="checkbox" checked={renderParams.loose_bound} onChange={(event) => setRenderParams((current) => ({ ...current, loose_bound: event.target.checked }))} /> loose bound</label>}
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.remove_signal_outliers} onChange={(event) => setRenderParams((current) => ({ ...current, remove_signal_outliers: event.target.checked }))} /> remove outliers</label>
                            {(mode === 'reference' || mode === 'pileup') && <label className="col-span-2">managed BED artifact<input aria-label="Managed BED artifact ID" type="text" value={renderParams.managed_bed_artifact_id || ''} onChange={(event) => setRenderParams((current) => ({ ...current, managed_bed_artifact_id: event.target.value.trim() || null }))} placeholder="Opaque managed artifact ID" className="ml-1 min-w-56 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1 font-mono" /></label>}
                        </div>
                    )}
                    {mode !== 'raw_waveform' && mode !== 'ideal_comparison' && <div className="flex items-center gap-2">
                        <button type="button" onClick={() => void render()} disabled={busy || !mappingArtifact} className="rounded bg-[var(--accent-secondary)] px-3 py-1.5 text-xs text-white disabled:opacity-40">{busy ? 'Working…' : `Render ${mode}`}</button>
                        {viewJob && <span className={`rounded px-1.5 py-0.5 text-[10px] ${stateBadge(viewJob.state)}`}>{viewJob.state}: {viewJob.reason_code}</span>}
                        {viewJob && !TERMINAL_STATES.has(viewJob.state) && <button type="button" onClick={() => void cancelViewForIdentity(viewJob)} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">Cancel render</button>}
                    </div>}
                    {mode !== 'raw_waveform' && mode !== 'ideal_comparison' && viewJob?.failure_message && <div className="text-[10px] text-rose-200">{viewJob.failure_code}: {viewJob.failure_message}</div>}
                    {mode !== 'raw_waveform' && mode !== 'ideal_comparison' && (artifactUrl ? (
                        <iframe
                            title="Bounded Squigualiser artifact"
                            src={artifactUrl}
                            sandbox="allow-scripts"
                            referrerPolicy="no-referrer"
                            className="h-[360px] w-full rounded border border-[var(--border-primary)] bg-white"
                        />
                    ) : (
                        <div className="flex h-40 items-center justify-center rounded border border-dashed border-[var(--border-primary)] text-xs text-[var(--text-secondary)]">Open Diagnostics to prepare an aligned signal view.</div>
                    ))}
                </section>

            </div>
        </aside>
    );
}
