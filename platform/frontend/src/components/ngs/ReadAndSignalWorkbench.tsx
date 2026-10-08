import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
    cancelOntSignalCalibration,
    cancelOntSignalMapping,
    cancelOntSignalView,
    createOntSignalCalibration,
    createOntSignalMapping,
    createOntSignalMappingProfile,
    createOntSignalView,
    createOntSignalViewerSession,
    fetchOntMoveSources,
    fetchOntSignalCalibration,
    fetchOntSignalMapping,
    fetchOntSignalMappingProfiles,
    fetchOntSignalView,
    fetchOntSignalViewArtifact,
    fetchOntSignalWorkbenchCapabilities,
    updateOntSignalViewerSession,
    type OntMoveTableSource,
    type OntSignalCalibrationJob,
    type OntSignalMappingJob,
    type OntSignalMappingMode,
    type OntSignalMappingProfile,
    type OntSignalRenderParams,
    type OntSignalViewerSession,
    type OntSignalViewJob,
    type OntSignalViewMode,
    type OntSignalWorkbenchCapabilities,
} from '../../lib/api';
import {
    fetchAlignmentRead,
    fetchAlignmentReads,
    type AlignmentRead,
    type AlignmentSession,
} from '../../lib/ngsAlignmentSession';
import type { AlignmentReadLocus } from '../../lib/ngsAlignmentViewer';

const TERMINAL_STATES = new Set(['ready', 'failed', 'cancelled']);
const DEFAULT_RENDER_PARAMS: OntSignalRenderParams = {
    strand: 'forward',
    signal_units: 'pA',
    scale: 'none',
    base_shift_source: 'profile',
    base_shift_value: 0,
    fixed_width: false,
    base_width: 10,
    point_size: 0.5,
    base_limit: 1000,
    signal_sample_limit: 100_000,
    pileup_read_limit: 20,
    loose_bound: false,
    show_samples: true,
    show_base_colours: true,
    remove_signal_outliers: false,
    managed_bed_artifact_id: null,
};

interface ReadAndSignalWorkbenchProps {
    datasetId: string;
    runId: string;
    observedGeneration: number;
    alignmentJobId: string;
    alignmentSession: AlignmentSession | null;
    referenceRevisionId: string | null;
    currentLocus: AlignmentReadLocus | null;
    viewerSession: OntSignalViewerSession | null;
    igvState: Record<string, unknown>;
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
    const viewerCreateKeyRef = useRef('');
    const [capabilities, setCapabilities] = useState<OntSignalWorkbenchCapabilities | null>(null);
    const [moveSources, setMoveSources] = useState<OntMoveTableSource[]>([]);
    const [profiles, setProfiles] = useState<OntSignalMappingProfile[]>([]);
    const [calibration, setCalibration] = useState<OntSignalCalibrationJob | null>(null);
    const [preparationMode, setPreparationMode] = useState<OntSignalMappingMode>('signal_to_read');
    const [readMapping, setReadMapping] = useState<OntSignalMappingJob | null>(null);
    const [referenceMapping, setReferenceMapping] = useState<OntSignalMappingJob | null>(null);
    const [viewJob, setViewJob] = useState<OntSignalViewJob | null>(null);
    const [artifactUrl, setArtifactUrl] = useState<string | null>(null);
    const [selectedRead, setSelectedRead] = useState<AlignmentRead | null>(null);
    const [eligibleReads, setEligibleReads] = useState<AlignmentRead[]>([]);
    const [readId, setReadId] = useState('');
    const [contig, setContig] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [mode, setMode] = useState<OntSignalViewMode>('read');
    const [renderParams, setRenderParams] = useState<OntSignalRenderParams>(DEFAULT_RENDER_PARAMS);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [provenanceOpen, setProvenanceOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const identityKey = `${datasetId}:${runId}:${observedGeneration}:${alignmentJobId}:${alignmentSession?.session_id || ''}:${referenceRevisionId || ''}`;

    const refreshAuthorities = useCallback(async () => {
        const generation = identityRef.current;
        const [nextCapabilities, sourcePayload, profilePayload] = await Promise.all([
            fetchOntSignalWorkbenchCapabilities(runId, observedGeneration),
            fetchOntMoveSources(runId, observedGeneration),
            fetchOntSignalMappingProfiles(),
        ]);
        if (generation !== identityRef.current) return;
        setCapabilities(nextCapabilities);
        setMoveSources(sourcePayload.items);
        setProfiles(profilePayload.items);
        const readJobId = nextCapabilities.resolved.signal_to_read_mapping_job_id;
        const referenceJobId = nextCapabilities.resolved.signal_to_reference_mapping_job_id;
        const calibrationJobId = nextCapabilities.resolved.calibration_job_id;
        const [nextReadMapping, nextReferenceMapping, nextCalibration] = await Promise.all([
            readJobId ? fetchOntSignalMapping(readJobId) : Promise.resolve(null),
            referenceJobId ? fetchOntSignalMapping(referenceJobId) : Promise.resolve(null),
            calibrationJobId ? fetchOntSignalCalibration(calibrationJobId) : Promise.resolve(null),
        ]);
        if (generation !== identityRef.current) return;
        setReadMapping(nextReadMapping);
        setReferenceMapping(nextReferenceMapping);
        setCalibration(nextCalibration);
    }, [observedGeneration, runId]);

    useEffect(() => {
        identityRef.current += 1;
        setCapabilities(null);
        setMoveSources([]);
        setProfiles([]);
        setCalibration(null);
        setReadMapping(null);
        setReferenceMapping(null);
        setViewJob(null);
        setArtifactUrl((current) => {
            if (current) URL.revokeObjectURL(current);
            return null;
        });
        setSelectedRead(null);
        setEligibleReads([]);
        setError(null);
        void refreshAuthorities().catch((reason) => setError(message(reason)));
    }, [identityKey, refreshAuthorities]);

    useEffect(() => () => {
        identityRef.current += 1;
        if (artifactUrl) URL.revokeObjectURL(artifactUrl);
    }, [artifactUrl]);

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
    }, [currentLocus, viewerSession?.contig, viewerSession?.locus_end, viewerSession?.locus_start]);

    useEffect(() => {
        if (!viewerSession) return;
        setReadId(viewerSession.selected_read_id || '');
        const savedMode = viewerSession.signal_state.mode;
        if (savedMode === 'read' || savedMode === 'reference' || savedMode === 'pileup') setMode(savedMode);
        if (isRenderParams(viewerSession.signal_state.render_params)) {
            setRenderParams({ ...DEFAULT_RENDER_PARAMS, ...viewerSession.signal_state.render_params });
        }
        const savedViewJobId = viewerSession.signal_state.view_job_id;
        if (typeof savedViewJobId === 'string' && savedViewJobId) {
            const generation = identityRef.current;
            void fetchOntSignalView(savedViewJobId).then((job) => {
                if (generation === identityRef.current) setViewJob(job);
            }).catch((reason) => {
                if (generation === identityRef.current) setError(message(reason));
            });
        }
    }, [viewerSession?.viewer_session_id]);

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
            alignment_session_id: alignmentSession?.session_id || null,
            reference_revision_id: referenceRevisionId,
            contig: currentLocus?.contig || null,
            locus_start: currentLocus?.start || null,
            locus_end: currentLocus?.end || null,
            selected_read_id: readId.trim() || null,
        }).then((created) => {
            if (generation === identityRef.current) onViewerSessionChange(created);
        }).catch((reason) => {
            if (generation === identityRef.current) {
                viewerCreateKeyRef.current = '';
                setError(`Viewer session could not be persisted: ${message(reason)}`);
            }
        });
    }, [alignmentJobId, alignmentSession?.session_id, currentLocus?.contig, currentLocus?.end, currentLocus?.start, datasetId, identityKey, observedGeneration, onViewerSessionChange, readId, referenceRevisionId, runId, viewerSession]);

    const pollMapping = useCallback((mapping: OntSignalMappingJob | null, setter: (value: OntSignalMappingJob) => void) => {
        if (!mapping || TERMINAL_STATES.has(mapping.state)) return undefined;
        const generation = identityRef.current;
        const handle = window.setInterval(() => {
            void fetchOntSignalMapping(mapping.mapping_job_id).then((next) => {
                if (generation !== identityRef.current) return;
                setter(next);
                if (TERMINAL_STATES.has(next.state)) {
                    window.clearInterval(handle);
                    void refreshAuthorities().catch((reason) => setError(message(reason)));
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
                    void refreshAuthorities().catch((reason) => setError(message(reason)));
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
        const handle = window.setInterval(() => {
            void fetchOntSignalView(viewJob.view_job_id).then((next) => {
                if (generation !== identityRef.current) return;
                setViewJob(next);
                if (TERMINAL_STATES.has(next.state)) window.clearInterval(handle);
            }).catch((reason) => {
                if (generation === identityRef.current) setError(message(reason));
            });
        }, 1500);
        return () => window.clearInterval(handle);
    }, [viewJob?.state, viewJob?.view_job_id]);

    useEffect(() => {
        const readyViewJob = viewJob?.state === 'ready' ? viewJob : null;
        const html = readyViewJob?.output_manifest.artifacts.find((item) => item.media_type === 'text/html' && item.url);
        if (!readyViewJob || !html) {
            setArtifactUrl((current) => {
                if (current) URL.revokeObjectURL(current);
                return null;
            });
            return;
        }
        const generation = identityRef.current;
        const viewJobId = readyViewJob.view_job_id;
        void fetchOntSignalViewArtifact(viewJobId, html.artifact_id).then((blob) => {
            if (generation !== identityRef.current) return;
            const next = URL.createObjectURL(blob);
            setArtifactUrl((current) => {
                if (current) URL.revokeObjectURL(current);
                return next;
            });
        }).catch((reason) => {
            if (generation === identityRef.current) setError(`Bounded artifact could not be loaded: ${message(reason)}`);
        });
    }, [viewJob?.state, viewJob?.view_job_id]);

    const compatibleSource = useMemo(() => {
        const resolvedId = capabilities?.resolved.move_source_id;
        return moveSources.find((item) => item.move_source_id === resolvedId)
            || moveSources.find((item) => item.state === 'ready')
            || null;
    }, [capabilities?.resolved.move_source_id, moveSources]);
    const compatibleProfile = useMemo(() => {
        const resolvedId = capabilities?.resolved.mapping_profile_id;
        return profiles.find((item) => item.mapping_profile_id === resolvedId)
            || profiles.find((item) => compatibleSource
                && item.basecall_model_id === compatibleSource.basecall_model_id
                && item.molecule_type === compatibleSource.molecule_type
                && item.primary_alignment_policy === 'primary_only'
                && item.minimum_mapq === 0
                && item.include_supplementary === false
                && item.read_set_selection === 'immutable_full_set'
                && (item.parameter_source === 'exact_upstream_profile'
                    || (calibration?.artifact && item.calibration_artifact_id === calibration.artifact.calibration_artifact_id)))
            || null;
    }, [calibration?.artifact, capabilities?.resolved.mapping_profile_id, compatibleSource, profiles]);

    const prepareMapping = async (mappingMode: OntSignalMappingMode) => {
        const rawRepresentationId = capabilities?.resolved.raw_representation_id;
        if (!rawRepresentationId || !compatibleSource) {
            setError('No exact ready indexed BLOW5 and move-source authority is available.');
            return;
        }
        if (mappingMode === 'signal_to_reference' && (!referenceRevisionId || !alignmentSession?.ready || readMapping?.state !== 'ready')) {
            setError('Signal-to-reference preparation requires the selected managed reference, ready alignment session, and ready signal-to-read mapping.');
            return;
        }
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
                        policy: { primary_alignment_policy: 'primary_only', minimum_mapq: 0, include_supplementary: false, read_set_selection: 'immutable_full_set' },
                    },
                    approved_by: null,
                });
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
            if (mappingMode === 'signal_to_read') setReadMapping(created);
            else setReferenceMapping(created);
        } catch (reason) {
            setError(message(reason));
        } finally {
            setBusy(false);
        }
    };

    const mappingForMode = mode === 'read' ? readMapping : referenceMapping;
    const mappingArtifact = mappingForMode?.artifacts.find((item) => (
        mode === 'read' ? item.kind === 'reform_paf' : item.kind === 'realign_paf'
    )) || null;

    const inspectExactRead = async () => {
        const exact = readId.trim();
        if (!exact || !alignmentSession) return;
        setBusy(true);
        setError(null);
        try {
            const detail = await fetchAlignmentRead(alignmentJobId, alignmentSession.session_id, exact, {
                contig: contig.trim() || undefined,
                start: integer(start) || undefined,
                end: integer(end) || undefined,
            });
            setSelectedRead(detail);
            setReadId(detail.read_id);
        } catch (reason) {
            setSelectedRead(null);
            setError(message(reason));
        } finally {
            setBusy(false);
        }
    };

    const loadLocusReads = async () => {
        const locusStart = integer(start);
        const locusEnd = integer(end);
        if (!alignmentSession || !contig.trim() || !locusStart || !locusEnd || locusEnd < locusStart) {
            setError('A complete 1-based reference locus is required.');
            return;
        }
        setBusy(true);
        setError(null);
        try {
            const page = await fetchAlignmentReads(alignmentJobId, alignmentSession.session_id, {
                contig: contig.trim(), start: locusStart, end: locusEnd, limit: 50,
            });
            setEligibleReads(page.reads);
            if (page.reads.length > 0 && !page.reads.some((item) => item.read_id === readId.trim())) {
                setReadId(page.reads[0].read_id);
                setSelectedRead(page.reads[0]);
            }
        } catch (reason) {
            setEligibleReads([]);
            setError(message(reason));
        } finally {
            setBusy(false);
        }
    };

    const moveRead = (delta: number) => {
        if (eligibleReads.length === 0) return;
        const currentIndex = eligibleReads.findIndex((item) => item.read_id === readId.trim());
        const nextIndex = Math.min(eligibleReads.length - 1, Math.max(0, (currentIndex < 0 ? 0 : currentIndex) + delta));
        const next = eligibleReads[nextIndex];
        setReadId(next.read_id);
        setSelectedRead(next);
    };

    const render = async () => {
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
        setBusy(true);
        setError(null);
        setArtifactUrl((current) => {
            if (current) URL.revokeObjectURL(current);
            return null;
        });
        try {
            const created = await createOntSignalView({
                mapping_artifact_id: mappingArtifact.mapping_artifact_id,
                mode,
                read_id: mode === 'read' ? readId.trim() : null,
                reference_contig: mode === 'read' ? null : contig.trim(),
                reference_start: mode === 'read' ? null : locusStart,
                reference_end: mode === 'read' ? null : locusEnd,
                render_params: renderParams,
            });
            setViewJob(created);
        } catch (reason) {
            setError(message(reason));
        } finally {
            setBusy(false);
        }
    };

    const persistSession = async () => {
        if (!viewerSession) {
            setError('Viewer session creation is still pending.');
            return;
        }
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
            onViewerSessionChange(saved);
        } catch (reason) {
            setError(message(reason));
        } finally {
            setBusy(false);
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
        <aside className="absolute right-0 top-0 bottom-0 z-20 w-full lg:w-[48%] min-w-0 border-l border-[var(--border-primary)] bg-[var(--bg-secondary)]/98 shadow-2xl flex flex-col">
            <header className="border-b border-[var(--border-primary)] px-3 py-2 space-y-2">
                <div className="flex items-center justify-between gap-2">
                    <div>
                        <h2 className="text-sm font-semibold text-[var(--text-primary)]">Read and Signal Workbench</h2>
                        <p className="text-[10px] text-[var(--text-secondary)]">Run <code>{runId}</code> generation {observedGeneration} · IGV remains the alignment authority.</p>
                    </div>
                    <button type="button" onClick={() => void persistSession()} disabled={busy || !viewerSession} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[11px] disabled:opacity-40">
                        Save session
                    </button>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-1 text-[10px]">
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
                {error && <div role="alert" className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">{error}</div>}
            </header>

            <div className="flex-1 min-h-0 overflow-auto p-3 space-y-3">
                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <h3 className="text-xs font-semibold">Shared read and locus</h3>
                        <button type="button" onClick={() => void loadLocusReads()} disabled={busy || !alignmentSession?.ready} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Reads in locus</button>
                    </div>
                    <div className="grid grid-cols-[1fr_auto] gap-1">
                        <input value={readId} onChange={(event) => setReadId(event.target.value)} placeholder="Exact read ID" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs font-mono" />
                        <button type="button" onClick={() => void inspectExactRead()} disabled={busy || !alignmentSession?.ready || !readId.trim()} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Resolve</button>
                    </div>
                    <div className="grid grid-cols-[1fr_76px_76px] gap-1">
                        <input value={contig} onChange={(event) => setContig(event.target.value)} placeholder="Reference contig" className="min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                        <input value={start} onChange={(event) => setStart(event.target.value)} inputMode="numeric" placeholder="Start" className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                        <input value={end} onChange={(event) => setEnd(event.target.value)} inputMode="numeric" placeholder="End" className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1 text-xs" />
                    </div>
                    <div className="flex flex-wrap gap-1">
                        <button type="button" onClick={() => moveRead(-1)} disabled={eligibleReads.length === 0} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Previous eligible read</button>
                        <button type="button" onClick={() => moveRead(1)} disabled={eligibleReads.length === 0} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Next eligible read</button>
                        <button type="button" onClick={locateRead} disabled={!selectedRead} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px] disabled:opacity-40">Locate read in IGV</button>
                        <button type="button" onClick={openMappedLocus} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">Open mapped locus in IGV</button>
                    </div>
                    {selectedRead && (
                        <div className="rounded bg-[var(--bg-secondary)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                            <code className="text-[var(--text-primary)]">{selectedRead.read_id}</code> · {selectedRead.strand} · MAPQ {selectedRead.mapq ?? 'n/a'} · {selectedRead.cigar || 'no CIGAR'} · model {compatibleSource?.basecall_model_id || 'unresolved'} · raw signal {capabilities?.modes.raw_waveform.state || 'loading'}
                        </div>
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
                            {readMapping && !TERMINAL_STATES.has(readMapping.state) && <button type="button" onClick={() => void cancelOntSignalMapping(readMapping.mapping_job_id).then(setReadMapping)} className="mt-2 rounded border border-[var(--border-primary)] px-2 py-1">Cancel</button>}
                        </div>
                        <div className="rounded border border-[var(--border-primary)] p-2">
                            <div className="flex items-center justify-between"><span>Signal to reference</span><span className={`rounded px-1 ${stateBadge(referenceMapping?.state || capabilities?.modes.signal_to_reference.state || 'loading')}`}>{referenceMapping?.state || capabilities?.modes.signal_to_reference.state || 'loading'}</span></div>
                            <div className="mt-1 break-all text-[var(--text-secondary)]">{referenceMapping?.reason_code || capabilities?.modes.signal_to_reference.reason_code}</div>
                            {referenceMapping && !TERMINAL_STATES.has(referenceMapping.state) && <button type="button" onClick={() => void cancelOntSignalMapping(referenceMapping.mapping_job_id).then(setReferenceMapping)} className="mt-2 rounded border border-[var(--border-primary)] px-2 py-1">Cancel</button>}
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[10px]">
                        <select value={preparationMode} onChange={(event) => setPreparationMode(event.target.value as OntSignalMappingMode)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1">
                            <option value="signal_to_read">signal to read</option>
                            <option value="signal_to_reference">signal to reference</option>
                        </select>
                        <button type="button" onClick={() => void prepareMapping(preparationMode)} disabled={busy || (!!calibration && !TERMINAL_STATES.has(calibration.state)) || (preparationMode === 'signal_to_reference' && (!referenceRevisionId || !alignmentSession?.ready || readMapping?.state !== 'ready'))} className="rounded border border-[var(--border-primary)] px-2 py-1 disabled:opacity-40">Prepare aligned signal</button>
                        {calibration && !TERMINAL_STATES.has(calibration.state) && <button type="button" onClick={() => void cancelOntSignalCalibration(calibration.calibration_job_id).then(setCalibration)} className="rounded border border-[var(--border-primary)] px-2 py-1">Cancel calibration</button>}
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
                    <div className="text-[10px] text-[var(--text-secondary)] break-all">BLOW5 {capabilities?.resolved.raw_representation_id || 'unresolved'} · move source {compatibleSource?.move_source_id || 'unresolved'} · profile {compatibleProfile?.mapping_profile_id || 'unresolved'} ({compatibleProfile?.name || 'no exact approved profile'}) · reference {referenceRevisionId || 'not bound'} · alignment {alignmentSession?.session_id || 'not bound'}</div>
                </section>

                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 space-y-2">
                    <div className="flex flex-wrap items-center gap-1">
                        {(['read', 'reference', 'pileup'] as const).map((candidate) => (
                            <button key={candidate} type="button" onClick={() => setMode(candidate)} className={`rounded border px-2 py-1 text-[10px] ${mode === candidate ? 'border-[var(--accent-secondary)] text-[var(--accent-secondary)]' : 'border-[var(--border-primary)]'}`}>{candidate === 'read' ? 'Single read' : candidate}</button>
                        ))}
                        <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="ml-auto rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">{advancedOpen ? 'Hide' : 'Render settings'}</button>
                    </div>
                    {advancedOpen && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
                            <select value={renderParams.strand} onChange={(event) => setRenderParams((current) => ({ ...current, strand: event.target.value as OntSignalRenderParams['strand'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="forward">forward</option><option value="reverse">reverse</option></select>
                            <select value={renderParams.signal_units} onChange={(event) => setRenderParams((current) => ({ ...current, signal_units: event.target.value as OntSignalRenderParams['signal_units'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="pA">pA</option><option value="raw_adc">raw ADC</option></select>
                            <select value={renderParams.scale} onChange={(event) => setRenderParams((current) => ({ ...current, scale: event.target.value as OntSignalRenderParams['scale'] }))} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-1 py-1"><option value="none">no scaling</option><option value="medmad">medmad</option><option value="znorm">znorm</option><option value="scaledpA">scaledpA</option></select>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.fixed_width} onChange={(event) => setRenderParams((current) => ({ ...current, fixed_width: event.target.checked }))} /> fixed width</label>
                            <label>base width<input type="number" min={1} max={100} value={renderParams.base_width} onChange={(event) => setRenderParams((current) => ({ ...current, base_width: Number(event.target.value) }))} className="ml-1 w-16 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label>base limit<input type="number" min={1} max={100000} value={renderParams.base_limit} onChange={(event) => setRenderParams((current) => ({ ...current, base_limit: Number(event.target.value) }))} className="ml-1 w-20 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label>samples<input type="number" min={1} max={2000000} value={renderParams.signal_sample_limit} onChange={(event) => setRenderParams((current) => ({ ...current, signal_sample_limit: Number(event.target.value) }))} className="ml-1 w-24 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label>pileup reads<input type="number" min={1} max={100} value={renderParams.pileup_read_limit} onChange={(event) => setRenderParams((current) => ({ ...current, pileup_read_limit: Number(event.target.value) }))} className="ml-1 w-14 rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]" /></label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.show_samples} onChange={(event) => setRenderParams((current) => ({ ...current, show_samples: event.target.checked }))} /> samples</label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.show_base_colours} onChange={(event) => setRenderParams((current) => ({ ...current, show_base_colours: event.target.checked }))} /> base colours</label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.loose_bound} onChange={(event) => setRenderParams((current) => ({ ...current, loose_bound: event.target.checked }))} /> loose bound</label>
                            <label className="flex items-center gap-1"><input type="checkbox" checked={renderParams.remove_signal_outliers} onChange={(event) => setRenderParams((current) => ({ ...current, remove_signal_outliers: event.target.checked }))} /> remove outliers</label>
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <button type="button" onClick={() => void render()} disabled={busy || !mappingArtifact} className="rounded bg-[var(--accent-secondary)] px-3 py-1.5 text-xs text-white disabled:opacity-40">{busy ? 'Working…' : `Render ${mode}`}</button>
                        {viewJob && <span className={`rounded px-1.5 py-0.5 text-[10px] ${stateBadge(viewJob.state)}`}>{viewJob.state}: {viewJob.reason_code}</span>}
                        {viewJob && !TERMINAL_STATES.has(viewJob.state) && <button type="button" onClick={() => void cancelOntSignalView(viewJob.view_job_id).then(setViewJob)} className="rounded border border-[var(--border-primary)] px-2 py-1 text-[10px]">Cancel render</button>}
                    </div>
                    {viewJob?.failure_message && <div className="text-[10px] text-rose-200">{viewJob.failure_code}: {viewJob.failure_message}</div>}
                    {artifactUrl ? (
                        <iframe
                            title="Bounded Squigualiser artifact"
                            src={artifactUrl}
                            sandbox="allow-scripts"
                            referrerPolicy="no-referrer"
                            className="h-[360px] w-full rounded border border-[var(--border-primary)] bg-white"
                        />
                    ) : (
                        <div className="flex h-40 items-center justify-center rounded border border-dashed border-[var(--border-primary)] text-xs text-[var(--text-secondary)]">No ready bounded signal artifact.</div>
                    )}
                </section>

                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-2 text-[10px]">
                    <button type="button" onClick={() => setProvenanceOpen((value) => !value)} className="w-full text-left font-semibold">{provenanceOpen ? 'Hide' : 'Show'} provenance</button>
                    {provenanceOpen && (
                        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--bg-primary)] p-2 text-[9px] text-[var(--text-secondary)]">{JSON.stringify({
                            viewer_session_id: viewerSession?.viewer_session_id || null,
                            dataset_id: datasetId,
                            run_id: runId,
                            observed_generation: observedGeneration,
                            alignment_session_id: alignmentSession?.session_id || null,
                            reference_revision_id: referenceRevisionId,
                            raw_representation_id: capabilities?.resolved.raw_representation_id || null,
                            move_source: compatibleSource,
                            mapping_profile: compatibleProfile,
                            mapping: mappingForMode,
                            render: viewJob,
                        }, null, 2)}</pre>
                    )}
                </section>
            </div>
        </aside>
    );
}
