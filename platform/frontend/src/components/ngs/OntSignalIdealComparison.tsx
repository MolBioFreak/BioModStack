import { useEffect, useMemo, useRef, useState } from 'react';

import {
    cancelOntSignalIdealComparison,
    createFreshOntSignalIdealComparisonAttempt,
    createOntSignalComparisonReview,
    createOntSignalIdealComparison,
    fetchOntSignalComparisonArtifact,
    fetchOntSignalIdealComparison,
    previewOntSignalIdealComparison,
    updateOntSignalViewerSession,
    type OntSignalComparisonJob,
    type OntSignalComparisonPreview,
    type OntSignalComparisonProfileId,
    type OntSignalComparisonReview,
    type OntSignalComparisonSimulationSettings,
    type OntSignalRenderParams,
    type OntSignalViewerSession,
} from '../../lib/api';

export interface OntSignalIdealComparisonProps {
    datasetId: string;
    viewerSession: OntSignalViewerSession;
    selectedReadId: string;
    contig: string;
    start: number | null;
    end: number | null;
    mappingJobId: string | null;
    renderParams: OntSignalRenderParams;
    onViewerSessionChange: (session: OntSignalViewerSession) => void;
}

const TERMINAL = new Set(['ready', 'failed', 'cancelled']);
const PROFILES: Array<{ id: OntSignalComparisonProfileId; label: string; fixed: string; approximate: boolean }> = [
    { id: 'dna-r9-min', label: 'DNA R9 MinION', fixed: 'DNA · R9 · 4 kHz · BLOW5 · full contig', approximate: false },
    { id: 'dna-r9-prom', label: 'DNA R9 PromethION', fixed: 'DNA · R9 · 4 kHz · BLOW5 · full contig', approximate: false },
    { id: 'rna-r9-min', label: 'RNA R9 MinION', fixed: 'RNA · R9 · 4 kHz · BLOW5 · full contig', approximate: false },
    { id: 'rna-r9-prom', label: 'RNA R9 PromethION', fixed: 'RNA · R9 · 4 kHz · BLOW5 · full contig', approximate: false },
    { id: 'dna-r10-min', label: 'DNA R10 MinION', fixed: 'DNA · R10 approximation · 5 kHz · BLOW5 · full contig', approximate: true },
    { id: 'dna-r10-prom', label: 'DNA R10 PromethION', fixed: 'DNA · R10 approximation · 5 kHz · BLOW5 · full contig', approximate: true },
    { id: 'rna004-min', label: 'RNA004 MinION', fixed: 'RNA · RNA004 approximation · 5 kHz · BLOW5 · full contig', approximate: true },
];

const CSP = "default-src 'none'; base-uri 'none'; connect-src 'none'; font-src data:; form-action 'none'; frame-src 'none'; img-src data:; media-src 'none'; object-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; worker-src 'none'";
function securedHtml(source: string): Blob {
    const parsed = new DOMParser().parseFromString(source, 'text/html');
    const meta = parsed.createElement('meta'); meta.httpEquiv = 'Content-Security-Policy'; meta.content = CSP;
    parsed.head.insertBefore(meta, parsed.head.firstChild);
    return new Blob([`<!doctype html>\n${parsed.documentElement.outerHTML}`], { type: 'text/html;charset=utf-8' });
}
async function blobText(blob: Blob): Promise<string> {
    if (typeof blob.text === 'function') return blob.text();
    return new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(reader.error); reader.readAsText(blob);
    });
}
function errorText(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }

export function OntSignalIdealComparison({
    datasetId, viewerSession, selectedReadId, contig, start, end, mappingJobId, renderParams, onViewerSessionChange,
}: OntSignalIdealComparisonProps) {
    const [profileId, setProfileId] = useState<OntSignalComparisonProfileId>('dna-r10-min');
    const [seed, setSeed] = useState(7);
    const [preview, setPreview] = useState<OntSignalComparisonPreview | null>(null);
    const [job, setJob] = useState<OntSignalComparisonJob | null>(null);
    const [artifactUrl, setArtifactUrl] = useState<string | null>(null);
    const [outcome, setOutcome] = useState<OntSignalComparisonReview['criterion_outcome']>('uncertain');
    const [note, setNote] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const generationRef = useRef(0);
    const urlRef = useRef<string | null>(null);
    const currentProfile = PROFILES.find((item) => item.id === profileId)!;
    const settings = useMemo<OntSignalComparisonSimulationSettings>(() => ({ profile_id: profileId, seed }), [profileId, seed]);
    const identity = `${datasetId}:${viewerSession.viewer_session_id}:${viewerSession.revision}:${selectedReadId}:${contig}:${start}:${end}:${mappingJobId}:${JSON.stringify(renderParams)}:${profileId}:${seed}`;

    const replaceUrl = (next: string | null) => {
        if (urlRef.current && urlRef.current !== next) URL.revokeObjectURL(urlRef.current);
        urlRef.current = next; setArtifactUrl(next);
    };
    useEffect(() => {
        generationRef.current += 1; setPreview(null); setJob(null); replaceUrl(null); setError(null);
    }, [identity]);
    useEffect(() => () => { generationRef.current += 1; if (urlRef.current) URL.revokeObjectURL(urlRef.current); urlRef.current = null; }, []);

    const request = () => {
        if (!selectedReadId || !contig || !start || !end || end < start) throw new Error('Select one exact mapped read and bounded reference interval.');
        if (!mappingJobId) throw new Error('A ready signal-to-reference mapping is required.');
        return {
            viewer_session_id: viewerSession.viewer_session_id,
            expected_viewer_session_revision: viewerSession.revision,
            selected_read_id: selectedReadId,
            reference_contig: contig,
            reference_start: start,
            reference_end: end,
            simulation_settings: settings,
            render_params: renderParams,
        };
    };
    const runPreview = async () => {
        const generation = generationRef.current; setBusy(true); setError(null); setPreview(null);
        try { const next = await previewOntSignalIdealComparison(request()); if (generation === generationRef.current) setPreview(next); }
        catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };
    const create = async () => {
        if (!preview) return;
        const generation = generationRef.current; setBusy(true); setError(null);
        try {
            const next = await createOntSignalIdealComparison({ ...request(), preview_digest: preview.preview_digest });
            if (generation !== generationRef.current) return;
            setJob(next);
            const saved = await updateOntSignalViewerSession(viewerSession.viewer_session_id, {
                expected_revision: viewerSession.revision, contig, locus_start: start, locus_end: end,
                selected_read_id: selectedReadId, igv_state: viewerSession.igv_state as never,
                signal_state: { mode: 'ideal_comparison', render_params: renderParams, view_job_id: null,
                    read_mapping_job_id: viewerSession.signal_state.read_mapping_job_id || null,
                    reference_mapping_job_id: mappingJobId, comparison_job_id: next.comparison_job_id,
                    comparison_review_id: null },
            });
            if (generation === generationRef.current) onViewerSessionChange(saved);
        } catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };

    useEffect(() => {
        if (!job || TERMINAL.has(job.state)) return undefined;
        const generation = generationRef.current; const controller = new AbortController();
        const handle = window.setInterval(() => void fetchOntSignalIdealComparison(job.comparison_job_id, controller.signal).then((next) => {
            if (generation === generationRef.current && !controller.signal.aborted) setJob(next);
        }).catch((reason) => { if (!controller.signal.aborted && generation === generationRef.current) setError(errorText(reason)); }), 1500);
        return () => { controller.abort(); window.clearInterval(handle); };
    }, [job?.comparison_job_id, job?.state]);

    useEffect(() => {
        const html = job?.state === 'ready' ? job.artifacts.find((item) => item.kind === 'comparison_html') : null;
        if (!job || !html) { replaceUrl(null); return; }
        const generation = generationRef.current;
        void fetchOntSignalComparisonArtifact(job.comparison_job_id, html.artifact_id).then(blobText).then((source) => {
            if (generation !== generationRef.current) return;
            if (!source.includes('Real acquired signal') || !source.includes('Ideal simulated reference')) throw new Error('Comparison artifact is missing exact track labels.');
            replaceUrl(URL.createObjectURL(securedHtml(source)));
        }).catch((reason) => { if (generation === generationRef.current) setError(errorText(reason)); });
    }, [job?.comparison_job_id, job?.state]);

    const saveReview = async () => {
        if (!job || job.state !== 'ready') return;
        const generation = generationRef.current; setBusy(true); setError(null);
        try {
            const prior = job.reviews[job.reviews.length - 1] || null;
            const review = await createOntSignalComparisonReview(job.comparison_job_id, {
                criterion_outcome: outcome, note: note.trim() || null, predecessor_review_id: prior?.review_id || null,
            });
            if (generation === generationRef.current) setJob({ ...job, reviews: [...job.reviews, review] });
        } catch (reason) { if (generation === generationRef.current) setError(errorText(reason)); }
        finally { if (generation === generationRef.current) setBusy(false); }
    };

    return <section className="space-y-2 rounded border border-[var(--border-primary)] p-2 text-[10px]">
        <div className="flex items-center justify-between"><h3 className="text-xs font-semibold">Ideal comparison</h3><span>{viewerSession.run_id} · generation {viewerSession.observed_generation}</span></div>
        <div>Real acquired signal: <code>{selectedReadId || 'no read'}</code> · reference <code>{viewerSession.reference_revision_id || 'unbound'}</code> · {contig}:{start ?? '?'}-{end ?? '?'}</div>
        <div className="grid grid-cols-2 gap-2">
            <label>Simulation profile<select aria-label="Simulation profile" value={profileId} onChange={(event) => setProfileId(event.target.value as OntSignalComparisonProfileId)} className="ml-1 rounded border bg-transparent p-1">{PROFILES.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}</select></label>
            <label>Seed<input aria-label="Simulation seed" type="number" min={1} max={2147483647} value={seed} onChange={(event) => setSeed(Number(event.target.value))} className="ml-1 w-24 rounded border bg-transparent p-1" /></label>
        </div>
        <details><summary>Profile-fixed values</summary><div>{currentProfile.fixed}</div><div>One full-contig record · one thread · deterministic seed · ideal reference only</div></details>
        {currentProfile.approximate && <div className="rounded border border-amber-500/40 bg-amber-500/10 p-1 text-amber-200">R10/RNA004 models are approximation profiles; simulated signal is model-derived and is not instrument-acquired evidence.</div>}
        {preview && <div className="space-y-1 rounded border p-1"><div>Derived context {preview.contig}:{preview.derived_start}-{preview.derived_end} · {preview.orientation}</div><div>Reference FASTA digest <code>{preview.reference_fasta_sha256}</code></div><div>Preview digest <code>{preview.preview_digest}</code></div>{preview.warnings.map((warning) => <div key={warning.code} className="text-amber-200">{warning.message}</div>)}{preview.blockers.map((blocker) => <div key={blocker.code} className="text-rose-200">{blocker.message}</div>)}</div>}
        {error && <div role="alert" className="text-rose-200">{error}</div>}
        <div className="flex gap-2"><button type="button" disabled={busy} onClick={() => void runPreview()} className="rounded border px-2 py-1">Preview</button><button type="button" disabled={busy || !preview || preview.blockers.length > 0} onClick={() => void create()} className="rounded border px-2 py-1">Generate and compare</button></div>
        {job && <div className="space-y-1"><div>Comparison {job.comparison_job_id} · attempt {job.attempt_number} · {job.state}: {job.reason_code}</div>{!TERMINAL.has(job.state) && <button type="button" onClick={() => void cancelOntSignalIdealComparison(job.comparison_job_id).then(setJob)} className="rounded border px-2 py-1">Cancel comparison</button>}{(job.state === 'failed' || job.state === 'cancelled') && job.attempt_number < 3 && <button type="button" onClick={() => void createFreshOntSignalIdealComparisonAttempt(job.comparison_job_id).then(setJob)} className="rounded border px-2 py-1">Create fresh attempt</button>}</div>}
        {artifactUrl ? <div><div className="flex justify-between font-semibold"><span>Real acquired signal</span><span>Ideal simulated reference</span></div><iframe title="Real acquired signal and ideal simulated reference" src={artifactUrl} sandbox="allow-scripts" referrerPolicy="no-referrer" className="h-[360px] w-full rounded border bg-white" /></div> : <div className="flex h-24 items-center justify-center rounded border border-dashed">No ready ideal comparison.</div>}
        {job?.state === 'ready' && <div className="space-y-1 rounded border p-1"><div className="font-semibold">Manual trace review</div><div>Does the acquired trace meet the ideal-reference review criterion?</div><select aria-label="Review criterion outcome" value={outcome} onChange={(event) => setOutcome(event.target.value as OntSignalComparisonReview['criterion_outcome'])}><option value="meets_criterion">Meets criterion</option><option value="does_not_meet_criterion">Does not meet criterion</option><option value="uncertain">Uncertain</option></select><textarea aria-label="Review note" value={note} onChange={(event) => setNote(event.target.value)} /><button type="button" onClick={() => void saveReview()} disabled={busy}>Record review revision</button>{job.reviews.map((review) => <div key={review.review_id}>Revision {review.revision}: {review.criterion_outcome} · {review.note || 'no note'}</div>)}</div>}
        <details><summary>Complete provenance</summary><pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({ preview, job }, null, 2)}</pre></details>
    </section>;
}
