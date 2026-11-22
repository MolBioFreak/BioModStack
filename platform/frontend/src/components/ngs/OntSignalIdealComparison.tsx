import { useEffect, useMemo, useRef, useState } from 'react';

import {
    cancelOntSignalIdealComparison,
    createFreshOntSignalIdealComparisonAttempt,
    createOntSignalComparisonReview,
    createOntSignalIdealComparison,
    fetchOntSignalComparisonArtifact,
    fetchOntSignalComparisonReviews,
    fetchOntSignalIdealComparison,
    previewOntSignalIdealComparison,
    updateOntSignalViewerSession,
    type OntSignalComparisonJob,
    type OntSignalComparisonPointSize,
    type OntSignalComparisonPreview,
    type OntSignalComparisonProfileId,
    type OntSignalComparisonRenderParams,
    type OntSignalComparisonReview,
    type OntSignalComparisonSimulationSettings,
    type OntSignalRenderParams,
    type OntSignalViewerSession,
} from '../../lib/api';
import { secureOntSignalHtml } from './ontSignalHtml';
import { isOwnedFullscreen, toggleOwnedFullscreen } from './ngsFullscreenOwner';

export interface OntSignalIdealComparisonProps {
    datasetId: string;
    viewerSession: OntSignalViewerSession;
    selectedReadId: string;
    contig: string;
    start: number | null;
    end: number | null;
    mappingJobId: string | null;
    mappingArtifactId: string | null;
    renderParams: OntSignalRenderParams;
    onViewerSessionChange: (session: OntSignalViewerSession) => void;
}

const TERMINAL = new Set(['ready', 'failed', 'cancelled']);
const JOB_WORDS: Record<string, string> = { requested: 'waiting to start', running: 'simulating and drawing…', ready: 'ready', failed: 'failed', cancelled: 'cancelled' };
const OUTCOME_WORDS: Record<string, string> = { approve: 'Approved', reject: 'Rejected', record_only: 'Recorded' };
const FIELD = 'flex items-center gap-2';
const INPUT = 'rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1';
const PROFILES: Array<{ id: OntSignalComparisonProfileId; label: string; approximate: boolean }> = [
    { id: 'dna-r9-min', label: 'DNA R9.4.1 MinION', approximate: false },
    { id: 'dna-r9-prom', label: 'DNA R9.4.1 PromethION', approximate: false },
    { id: 'rna-r9-min', label: 'RNA R9.4.1 MinION', approximate: false },
    { id: 'rna-r9-prom', label: 'RNA R9.4.1 PromethION', approximate: false },
    { id: 'dna-r10-min', label: 'DNA R10.4.1 MinION', approximate: true },
    { id: 'dna-r10-prom', label: 'DNA R10.4.1 PromethION', approximate: true },
    { id: 'rna004-min', label: 'RNA004 MinION', approximate: true },
    { id: 'rna004-prom', label: 'RNA004 PromethION', approximate: true },
];

const POINT_SIZES: readonly OntSignalComparisonPointSize[] = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

function comparisonPointSize(value: number): OntSignalComparisonPointSize {
    return POINT_SIZES.includes(value as OntSignalComparisonPointSize)
        ? value as OntSignalComparisonPointSize
        : 0.5;
}

function comparisonParams(value: OntSignalRenderParams): OntSignalComparisonRenderParams {
    return {
        scale: value.scale === 'scaledpA' ? 'none' : value.scale,
        point_size: comparisonPointSize(value.point_size),
        fixed_width: value.fixed_width,
        base_width: value.base_width,
        base_limit: Math.min(value.base_limit, 1000),
        signal_sample_limit: value.signal_sample_limit,
        show_samples: value.show_samples,
        show_base_colours: value.show_base_colours,
        remove_signal_outliers: value.remove_signal_outliers,
    };
}

// Bokeh CustomJS needs string compilation for native pan/zoom callbacks. This
// policy applies only inside the opaque-origin, network-denied sandbox below.
const CSP = "default-src 'none'; base-uri 'none'; connect-src 'none'; font-src data:; form-action 'none'; frame-src 'none'; img-src data:; media-src 'none'; object-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; worker-src 'none'";
async function blobText(blob: Blob): Promise<string> {
    if (typeof blob.text === 'function') return blob.text();
    return new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(reader.error); reader.readAsText(blob);
    });
}
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }

export function OntSignalIdealComparison({
    datasetId, viewerSession, selectedReadId, contig, start, end, mappingJobId, mappingArtifactId,
    renderParams, onViewerSessionChange,
}: OntSignalIdealComparisonProps) {
    const [profileId, setProfileId] = useState<OntSignalComparisonProfileId>('dna-r10-min');
    const [seed, setSeed] = useState(7);
    const [preview, setPreview] = useState<OntSignalComparisonPreview | null>(null);
    const [job, setJob] = useState<OntSignalComparisonJob | null>(null);
    const [artifactUrl, setArtifactUrl] = useState<string | null>(null);
    const [outcome, setOutcome] = useState<OntSignalComparisonReview['required_outcome']>('record_only');
    const [note, setNote] = useState('');
    const [reviews, setReviews] = useState<OntSignalComparisonReview[]>([]);
    const [comparisonRenderParams, setComparisonRenderParams] = useState<OntSignalComparisonRenderParams>(() => comparisonParams(renderParams));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [comparisonIsFullscreen, setComparisonIsFullscreen] = useState(false);
    const generationRef = useRef(0);
    const pollSequenceRef = useRef(0);
    const viewerMutationRef = useRef<AbortController | null>(null);
    const urlRef = useRef<string | null>(null);
    const restoringPersistedSettingsRef = useRef<string | null>(null);
    const comparisonFullscreenRef = useRef<HTMLDivElement | null>(null);
    const currentProfile = PROFILES.find((item) => item.id === profileId)!;
    const effectiveProfile = preview?.effective_request?.effective_settings?.profile || job?.simulation_settings?.profile;
    const settings = useMemo<OntSignalComparisonSimulationSettings>(() => ({ profile_id: profileId, seed }), [profileId, seed]);
    // The saved revision is not part of this: Preview saves the session itself, and Generate
    // checks that the preview was made against the session as it is now.
    const identity = `${datasetId}:${viewerSession.viewer_session_id}:${selectedReadId}:${contig}:${start}:${end}:${mappingJobId}:${mappingArtifactId}`;
    const settingsIdentity = `${JSON.stringify(comparisonRenderParams)}:${profileId}:${seed}`;

    const replaceUrl = (next: string | null) => {
        if (urlRef.current && urlRef.current !== next) URL.revokeObjectURL(urlRef.current);
        urlRef.current = next; setArtifactUrl(next);
    };
    const abortViewerMutation = () => {
        viewerMutationRef.current?.abort();
        viewerMutationRef.current = null;
    };
    const beginViewerMutation = () => {
        abortViewerMutation();
        const controller = new AbortController();
        viewerMutationRef.current = controller;
        return controller;
    };
    const immutableComparisonSettings = (sourceJob: OntSignalComparisonJob) => ({
        simulation_settings: {
            profile_id: sourceJob.simulation_settings.profile_id,
            seed: sourceJob.simulation_settings.operator_owned.seed,
        },
        render_params: sourceJob.render_params,
    });
    useEffect(() => {
        generationRef.current += 1; abortViewerMutation(); setBusy(false);
        setPreview(null); setJob(null); setReviews([]); replaceUrl(null); setError(null);
    }, [identity]);
    useEffect(() => {
        const restored = restoringPersistedSettingsRef.current;
        restoringPersistedSettingsRef.current = null;
        if (restored === settingsIdentity) return;
        generationRef.current += 1; abortViewerMutation(); setBusy(false);
        setPreview(null); setJob(null); setReviews([]); replaceUrl(null); setError(null);
    }, [settingsIdentity]);
    useEffect(() => () => {
        generationRef.current += 1; abortViewerMutation();
        if (urlRef.current) URL.revokeObjectURL(urlRef.current); urlRef.current = null;
    }, []);
    useEffect(() => {
        const handleFullscreenChange = () => setComparisonIsFullscreen(isOwnedFullscreen(comparisonFullscreenRef.current));
        const handleFullscreenError = () => setError('Fullscreen request failed: the browser rejected the comparison request.');
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        document.addEventListener('fullscreenerror', handleFullscreenError);
        handleFullscreenChange();
        return () => {
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
            document.removeEventListener('fullscreenerror', handleFullscreenError);
        };
    }, []);

    const toggleComparisonFullscreen = async () => {
        try {
            await toggleOwnedFullscreen(comparisonFullscreenRef.current);
        } catch (reason) {
            setError(`Fullscreen request failed: ${errorText(reason)}`);
        }
    };

    const request = (session: OntSignalViewerSession = viewerSession) => {
        if (!selectedReadId || !contig || !start || !end || end < start) throw new Error('Pick a read and a reference region (start and end) first.');
        if (!mappingJobId || !mappingArtifactId) throw new Error('Prepare reference views first.');
        return {
            viewer_session_id: session.viewer_session_id,
            expected_viewer_revision: session.revision,
            mapping_artifact_id: mappingArtifactId,
            selected_read_id: selectedReadId,
            reference_contig: contig,
            reference_start: start,
            reference_end: end,
            simulation_settings: settings,
            render_params: comparisonRenderParams,
        };
    };
    const runPreview = async () => {
        const generation = generationRef.current; setBusy(true); setError(null); setPreview(null);
        try {
            const draft = request();
            let session = viewerSession;
            if (session.selected_read_id !== selectedReadId || session.contig !== contig
                || session.locus_start !== start || session.locus_end !== end) {
                // The comparison is checked against the saved read and region, so save what is on screen first.
                const mutation = beginViewerMutation();
                session = await updateOntSignalViewerSession(session.viewer_session_id, {
                    expected_revision: session.revision, contig, locus_start: start, locus_end: end,
                    selected_read_id: selectedReadId, igv_state: session.igv_state as never,
                    signal_state: { mode: 'ideal_comparison', render_params: renderParams, view_job_id: null,
                        read_mapping_job_id: session.signal_state.read_mapping_job_id || null,
                        reference_mapping_job_id: mappingJobId },
                }, mutation.signal);
                if (generation !== generationRef.current || mutation.signal.aborted) return;
                if (viewerMutationRef.current === mutation) viewerMutationRef.current = null;
                onViewerSessionChange(session);
            }
            const next = await previewOntSignalIdealComparison({ ...draft, viewer_session_id: session.viewer_session_id, expected_viewer_revision: session.revision });
            if (generation === generationRef.current) setPreview(next);
        }
        catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };
    const previewCurrent = Boolean(preview && preview.viewer_session_revision === viewerSession.revision);
    const create = async () => {
        if (!preview) return;
        const generation = generationRef.current; setBusy(true); setError(null);
        try {
            const next = await createOntSignalIdealComparison({ ...request(), preview_digest: preview.preview_digest });
            if (generation !== generationRef.current) return;
            setJob(next);
            const mutation = beginViewerMutation();
            const saved = await updateOntSignalViewerSession(viewerSession.viewer_session_id, {
                expected_revision: viewerSession.revision, contig, locus_start: start, locus_end: end,
                selected_read_id: selectedReadId, igv_state: viewerSession.igv_state as never,
                signal_state: { mode: 'ideal_comparison', render_params: renderParams, view_job_id: null,
                    read_mapping_job_id: viewerSession.signal_state.read_mapping_job_id || null,
                    reference_mapping_job_id: mappingJobId, comparison_job_id: next.comparison_job_id,
                    comparison_preview_digest: preview.preview_digest,
                    comparison_settings: immutableComparisonSettings(next),
                    comparison_review_id: null },
            }, mutation.signal);
            if (generation === generationRef.current && !mutation.signal.aborted) onViewerSessionChange(saved);
            if (viewerMutationRef.current === mutation) viewerMutationRef.current = null;
        } catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };

    useEffect(() => {
        if (!job || TERMINAL.has(job.state)) return undefined;
        const generation = generationRef.current; const controller = new AbortController();
        const handle = window.setInterval(() => {
            const sequence = ++pollSequenceRef.current;
            void fetchOntSignalIdealComparison(job.comparison_job_id, controller.signal).then((next) => {
                if (generation === generationRef.current && sequence === pollSequenceRef.current && !controller.signal.aborted) {
                    setError(null);
                    setJob(next);
                }
            }).catch((reason) => {
                if (!controller.signal.aborted && generation === generationRef.current && sequence === pollSequenceRef.current) setError(errorText(reason));
            });
        }, 1500);
        return () => { controller.abort(); window.clearInterval(handle); };
    }, [job?.comparison_job_id, job?.state]);

    useEffect(() => {
        const persisted = viewerSession.signal_state.comparison_job_id;
        // The identity reset effect above clears the job on this same commit.
        // Do not skip restoration based on the previous render's job: a mapping
        // artifact can arrive after the persisted comparison has already loaded.
        if (!persisted) return;
        const generation = generationRef.current;
        void fetchOntSignalIdealComparison(persisted).then((next) => {
            if (generation !== generationRef.current) return;
            const operator = next.simulation_settings.operator_owned;
            const restoredParams: OntSignalComparisonRenderParams = {
                scale: operator.scale, point_size: operator.point_size,
                fixed_width: operator.fixed_width, base_width: operator.base_width,
                base_limit: operator.base_limit, signal_sample_limit: operator.signal_sample_limit,
                show_samples: operator.show_samples, show_base_colours: operator.show_base_colours,
                remove_signal_outliers: operator.remove_signal_outliers,
            };
            restoringPersistedSettingsRef.current = `${JSON.stringify(restoredParams)}:${next.simulation_settings.profile_id}:${operator.seed}`;
            setProfileId(next.simulation_settings.profile_id);
            setSeed(operator.seed);
            setComparisonRenderParams(restoredParams);
            setJob(next);
        }).catch((reason) => { if (generation === generationRef.current) setError(errorText(reason)); });
        // Restore only when the receiving session changes. Clearing a result after
        // an operator edit must not reload the saved job and overwrite that edit.
    }, [identity, viewerSession.signal_state.comparison_job_id]);

    useEffect(() => {
        if (!job || job.state !== 'ready') { setReviews([]); return; }
        const generation = generationRef.current;
        void fetchOntSignalComparisonReviews(job.comparison_job_id).then((items) => {
            if (generation === generationRef.current) setReviews(items);
        }).catch((reason) => { if (generation === generationRef.current) setError(errorText(reason)); });
    }, [job?.comparison_job_id, job?.state]);

    useEffect(() => {
        const html = job?.state === 'ready' ? job.artifacts.find((item) => item.kind === 'comparison_html') : null;
        if (!job || !html) { replaceUrl(null); return; }
        const generation = generationRef.current;
        void fetchOntSignalComparisonArtifact(job.comparison_job_id, html.artifact_id).then(blobText).then((source) => {
            if (generation !== generationRef.current) return;
            if (!source.includes('REAL · INSTRUMENT ACQUIRED ·') || !source.includes('SIMULATED IDEAL · SQUIGULATOR 0.5.0 ·')) throw new Error('Comparison artifact is missing exact track labels.');
            replaceUrl(URL.createObjectURL(secureOntSignalHtml(source, CSP)));
        }).catch((reason) => { if (generation === generationRef.current) setError(errorText(reason)); });
    }, [job?.comparison_job_id, job?.state]);

    const saveReview = async () => {
        if (!job || job.state !== 'ready') return;
        const generation = generationRef.current; setBusy(true); setError(null);
        try {
            const prior = reviews[reviews.length - 1] || null;
            const review = await createOntSignalComparisonReview(job.comparison_job_id, {
                predecessor_review_id: prior?.review_id || null,
                review_question: 'Does the real trace visually agree with the ideal expectation?',
                required_outcome: outcome, note: note.trim(), reviewed_start: job.reference_start,
                reviewed_end: job.reference_end,
            });
            if (generation === generationRef.current) {
                setReviews([...reviews, review]);
                const mutation = beginViewerMutation();
                const updated = await updateOntSignalViewerSession(viewerSession.viewer_session_id, {
                    expected_revision: viewerSession.revision,
                    selected_read_id: selectedReadId, igv_state: viewerSession.igv_state as never,
                    contig, locus_start: start, locus_end: end,
                    signal_state: { mode: 'ideal_comparison', render_params: renderParams, view_job_id: null,
                        read_mapping_job_id: viewerSession.signal_state.read_mapping_job_id || null,
                        reference_mapping_job_id: mappingJobId,
                        comparison_job_id: job.comparison_job_id,
                        comparison_preview_digest: job.preview_digest,
                        comparison_settings: immutableComparisonSettings(job),
                        comparison_review_id: review.review_id },
                }, mutation.signal);
                if (generation === generationRef.current && !mutation.signal.aborted) onViewerSessionChange(updated);
                if (viewerMutationRef.current === mutation) viewerMutationRef.current = null;
            }
        } catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };

    const applyLifecycle = async (operation: Promise<OntSignalComparisonJob>) => {
        const generation = generationRef.current; setBusy(true); setError(null);
        try {
            const next = await operation;
            if (generation === generationRef.current) setJob(next);
        } catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };

    return <section data-testid="ont-ideal-comparison" className="space-y-3 rounded border border-[var(--border-primary)] p-3 text-xs">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold">Ideal comparison</h3>
            <span className="text-[var(--text-secondary)]">
                {selectedReadId ? <>Read <code title={selectedReadId} className="text-[var(--text-primary)]">{selectedReadId.slice(0, 8)}</code></> : 'No read picked'} · {contig || 'no contig'} {start ?? '?'}–{end ?? '?'}
            </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <label className={FIELD}>Simulation profile<select aria-label="Simulation profile" disabled={busy} value={profileId} onChange={(event) => setProfileId(event.target.value as OntSignalComparisonProfileId)} className={INPUT}>{PROFILES.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select></label>
            <label className={FIELD}>Seed<input aria-label="Simulation seed" type="number" min={1} max={2147483647} disabled={busy} value={seed} onChange={(event) => setSeed(Number(event.target.value))} className={`${INPUT} w-24`} /></label>
        </div>
        {currentProfile.approximate && <p className="text-[var(--warning)]">R10 and RNA004 models are approximate: the simulated trace is a model, not measured signal.</p>}
        <div className="flex flex-wrap items-center gap-2">
            <button type="button" disabled={busy} onClick={() => void runPreview()} className="rounded border border-[var(--border-primary)] px-3 py-1">Preview</button>
            <button type="button" disabled={busy || !previewCurrent} onClick={() => void create()} className="rounded bg-[var(--accent-primary)] px-3 py-1 text-[var(--text-on-accent)] disabled:opacity-40">Generate and compare</button>
            {preview ? <span className="text-[var(--text-secondary)]">Simulates {contig} {preview.derived_window.start}–{preview.derived_window.end}, {preview.simulation_orientation} strand.</span> : null}
        </div>
        {preview?.warnings.map((warning) => <p key={warning} className="text-[var(--warning)]">{warning}</p>)}
        {error && <div role="alert" className="text-[var(--error)]">{error}</div>}
        {job && <div data-comparison-job={job.comparison_job_id} data-state={job.state} data-reason={job.reason_code} title={job.reason_code} className="flex flex-wrap items-center gap-2">
            <span>Comparison: {JOB_WORDS[job.state] ?? job.state}{job.attempt_number > 1 ? ` (attempt ${job.attempt_number})` : ''}{job.state === 'failed' && job.failure_message ? `. ${job.failure_message}` : ''}</span>
            {!TERMINAL.has(job.state) && <button type="button" disabled={busy} onClick={() => void applyLifecycle(cancelOntSignalIdealComparison(job.comparison_job_id))} className="rounded border border-[var(--border-primary)] px-2 py-1">Cancel comparison</button>}
            {(job.state === 'failed' || job.state === 'cancelled') && job.attempt_number < 3 && <button type="button" disabled={busy} onClick={() => void applyLifecycle(createFreshOntSignalIdealComparisonAttempt(job.comparison_job_id))} className="rounded border border-[var(--border-primary)] px-2 py-1">Create fresh attempt</button>}
        </div>}
        {artifactUrl ? <div
            ref={comparisonFullscreenRef}
            data-comparison-fullscreen-owner
            className={comparisonIsFullscreen ? 'flex h-screen w-screen flex-col bg-[var(--bg-secondary)] p-3' : ''}
        >
            <div className="flex items-center justify-between gap-2 pb-1 font-semibold">
                <div className="flex min-w-0 flex-1 justify-between gap-4"><span>Real acquired signal</span><span>Ideal simulated reference</span></div>
                <button type="button" onClick={() => void toggleComparisonFullscreen()} className="shrink-0 rounded border px-2 py-1">
                    {comparisonIsFullscreen ? 'Exit comparison fullscreen' : 'View comparison fullscreen'}
                </button>
            </div>
            <iframe title="Real acquired signal and ideal simulated reference" src={artifactUrl} sandbox="allow-scripts" referrerPolicy="no-referrer" className={`${comparisonIsFullscreen ? 'h-full min-h-0 flex-1' : 'h-[360px]'} w-full rounded border bg-white`} />
        </div> : <div className="flex h-24 items-center justify-center rounded border border-dashed border-[var(--border-primary)] px-3 text-center text-[var(--text-secondary)]">{job && !TERMINAL.has(job.state) ? 'Drawing the comparison…' : 'Preview, then Generate and compare, to draw this read above its ideal trace.'}</div>}
        {job?.state === 'ready' && <div className="space-y-2 rounded border border-[var(--border-primary)] p-2">
            <div className="font-semibold">Manual trace review</div>
            <label className="flex flex-col gap-1">Does the real trace match the ideal one?
                <select aria-label="Review required outcome" disabled={busy} value={outcome} onChange={(event) => setOutcome(event.target.value as OntSignalComparisonReview['required_outcome'])} className={INPUT}><option value="approve">Approve</option><option value="reject">Reject</option><option value="record_only">Record only</option></select></label>
            <textarea aria-label="Review note" placeholder="Note (required)" rows={2} disabled={busy} value={note} onChange={(event) => setNote(event.target.value)} className={`${INPUT} w-full`} />
            <button type="button" onClick={() => void saveReview()} disabled={busy || !note.trim()} className="rounded border border-[var(--border-primary)] px-3 py-1 disabled:opacity-40">Record review revision</button>
            {reviews.length ? <ul className="space-y-0.5 text-[var(--text-secondary)]">{reviews.map((review) => <li key={review.review_id}>{new Date(review.created_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}: {OUTCOME_WORDS[review.required_outcome] ?? review.required_outcome} · {review.note}</li>)}</ul> : null}
        </div>}
        <details className="rounded border border-[var(--border-primary)] px-2 py-1"><summary className="cursor-pointer text-[var(--text-secondary)]">Display settings</summary>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
            <label className={FIELD}>Scale<select aria-label="Comparison scale" className={INPUT} disabled={busy} value={comparisonRenderParams.scale} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, scale: event.target.value as OntSignalComparisonRenderParams['scale'] }))}><option value="none">none</option><option value="medmad">medmad</option><option value="znorm">znorm</option></select></label>
            <label className={FIELD}>Point size<select aria-label="Comparison point size" className={INPUT} disabled={busy} value={comparisonRenderParams.point_size} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, point_size: Number(event.target.value) as OntSignalComparisonPointSize }))}>{POINT_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}</select></label>
            <label className={FIELD}><input aria-label="Comparison fixed width" type="checkbox" disabled={busy} checked={comparisonRenderParams.fixed_width} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, fixed_width: event.target.checked }))} /> fixed width</label>
            <label className={FIELD}>Base width<input aria-label="Comparison base width" className={INPUT} type="number" min={1} max={100} disabled={busy || !comparisonRenderParams.fixed_width} value={comparisonRenderParams.base_width} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, base_width: Number(event.target.value) }))} /></label>
            <label className={FIELD}>Base limit<input aria-label="Comparison base limit" className={INPUT} type="number" min={1} max={1000} disabled={busy} value={comparisonRenderParams.base_limit} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, base_limit: Number(event.target.value) }))} /></label>
            <label className={FIELD}>Signal samples<input aria-label="Comparison signal sample limit" className={INPUT} type="number" min={1} max={2000000} disabled={busy} value={comparisonRenderParams.signal_sample_limit} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, signal_sample_limit: Number(event.target.value) }))} /></label>
            <label className={FIELD}><input aria-label="Comparison show samples" type="checkbox" disabled={busy} checked={comparisonRenderParams.show_samples} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, show_samples: event.target.checked }))} /> samples</label>
            <label className={FIELD}><input aria-label="Comparison show base colours" type="checkbox" disabled={busy} checked={comparisonRenderParams.show_base_colours} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, show_base_colours: event.target.checked }))} /> base colours</label>
            <label className={FIELD}><input aria-label="Comparison remove outliers" type="checkbox" disabled={busy} checked={comparisonRenderParams.remove_signal_outliers} onChange={(event) => setComparisonRenderParams((current) => ({ ...current, remove_signal_outliers: event.target.checked }))} /> remove outliers</label>
        </div>
        </details>
        <details className="rounded border border-[var(--border-primary)] px-2 py-1"><summary className="cursor-pointer text-[var(--text-secondary)]">Model constants</summary>{effectiveProfile ? <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
            <dt>Molecule</dt><dd>{effectiveProfile.molecule_type}</dd><dt>Flow-cell generation</dt><dd>{effectiveProfile.flow_cell_generation}</dd>
            <dt>Device class</dt><dd>{effectiveProfile.device_class}</dd><dt>Pore model</dt><dd>{effectiveProfile.pore_model_identity}</dd>
            <dt>K-mer length</dt><dd>{effectiveProfile.kmer_length}</dd><dt>Digitisation</dt><dd>{effectiveProfile.digitisation}</dd>
            <dt>Sample rate</dt><dd>{effectiveProfile.sample_rate} Hz</dd><dt>Translocation speed</dt><dd>{effectiveProfile.translocation_speed} bases/s</dd>
            <dt>Range</dt><dd>{effectiveProfile.range}</dd><dt>Offset mean ± SD</dt><dd>{effectiveProfile.offset_mean} ± {effectiveProfile.offset_standard_deviation}</dd>
            <dt>Median-before mean ± SD</dt><dd>{effectiveProfile.median_before_mean} ± {effectiveProfile.median_before_standard_deviation}</dd>
            <dt>Dwell mean ± SD</dt><dd>{effectiveProfile.dwell_mean} ± {effectiveProfile.dwell_standard_deviation}</dd>
            <dt>Compatibility floor</dt><dd>{effectiveProfile.compatibility_floor}</dd><dt>Model warning</dt><dd>{effectiveProfile.model_quality_warning || 'None'}</dd>
        </dl> : <p className="mt-1 text-[var(--text-secondary)]">Shown after Preview.</p>}</details>
        <details className="rounded border border-[var(--border-primary)] px-2 py-1"><summary className="cursor-pointer text-[var(--text-secondary)]">Technical details</summary>
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
                <dt>Run</dt><dd className="break-all font-mono">{viewerSession.run_id} · generation {viewerSession.observed_generation}</dd>
                <dt>Read</dt><dd className="break-all font-mono">{selectedReadId || 'none'}</dd>
                <dt>Reference revision</dt><dd className="break-all font-mono">{viewerSession.reference_revision_id || 'none'}</dd>
                {preview ? <><dt>Reference FASTA digest</dt><dd className="break-all font-mono">{preview.reference_fasta_sha256}</dd><dt>Preview digest</dt><dd className="break-all font-mono">{preview.preview_digest}</dd></> : null}
                {job ? <><dt>Comparison job</dt><dd className="break-all font-mono">{job.comparison_job_id} · {job.state}: {job.reason_code}</dd></> : null}
            </dl>
            <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all text-[11px]">{JSON.stringify({ preview, job }, null, 2)}</pre>
        </details>
    </section>;
}
