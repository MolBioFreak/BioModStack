import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import { api, submitJob, type Job, type Design, type PersistedAnalysisRun } from '../lib/api';
import { fetchBinderEvidence, evidenceText, predictionKey, type BinderPrediction } from '../lib/binderEvidence';
import { nativeCandidateRoute } from '../lib/nativeBinderResults';
import { parseScientificNativeMetric, parseScientificPae } from '../lib/scientificViewerIdentity';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';

const control = 'rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm';
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const jsonUrl = (v: unknown) => `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(v, null, 2))}`;

/** No representative/best-child selection. Inspection is independent of bulk selection. */
export function BinderPredictionEvidence(props: { jobId: string; sourceDesignId?: string; launchContextId?: string | null }) {
    return <EvidenceBrowser key={`${props.jobId}:${props.sourceDesignId ?? ''}`} {...props} />;
}
function EvidenceBrowser({ jobId, sourceDesignId, launchContextId }: { jobId: string; sourceDesignId?: string; launchContextId?: string | null }) {
    const [candidate, setCandidate] = useState(sourceDesignId ?? '');
    const [sequenceId, setSequenceId] = useState('');
    const [predictionId, setPredictionId] = useState('');
    const query = useInfiniteQuery({
        queryKey: ['binder-evidence', jobId], initialPageParam: 0,
        queryFn: ({ pageParam, signal }) => fetchBinderEvidence(jobId, pageParam, signal),
        getNextPageParam: page => page.records.length && page.offset + page.records.length < page.total ? page.offset + page.records.length : undefined,
        retry: false, refetchOnWindowFocus: false,
        // Primary completion does not imply child completion; discover late children too.
        refetchInterval: 5000,
    });
    const records = useMemo(() => query.data?.pages.flatMap(page => page.records) ?? [], [query.data]);
    const record = records.find(row => row.source_design_id === candidate);
    useEffect(() => {
        if (sourceDesignId && !record && query.hasNextPage && !query.isFetching && !query.isFetchNextPageError) void query.fetchNextPage();
    }, [sourceDesignId, record, query.hasNextPage, query.isFetching, query.isFetchNextPageError, query.fetchNextPage]);
    const sequence = record?.sequences.find(row => JSON.stringify([row.job_id, row.design_id]) === sequenceId);
    const prediction = sequence?.predictions.find(row => predictionKey(row) === predictionId);
    return <section aria-label="Candidate-linked prediction evidence" className="space-y-3 rounded-xl border border-[var(--border-color)] p-4 text-[var(--text-primary)]">
        <h4 className="font-semibold">Sequence and prediction evidence</h4>
        <RoundProgress jobId={jobId} />
        <p className="text-xs text-[var(--text-secondary)]">Each sequence, prediction sample and target state retains its own evidence. Missing measurements are not zero. Source viewing and selection remain independent.</p>
        {query.isLoading && <p role="status">Reading candidate-linked evidence…</p>}
        {query.isError && <p role="status">Evidence readback unavailable. <button type="button" onClick={() => void query.refetch()}>Retry evidence readback</button></p>}
        {!sourceDesignId && <label>Source candidate <select className={control} aria-label="Evidence candidate" value={candidate} onChange={event => { setCandidate(event.target.value); setSequenceId(''); setPredictionId(''); }}>
            <option value="">Choose a candidate</option>{records.map(row => <option key={row.source_design_id} value={row.source_design_id}>{row.candidate_key ?? row.source_design_id}</option>)}
        </select></label>}
        {query.hasNextPage && <button type="button" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>Load more evidence candidates ({records.length} of {query.data?.pages[0].total})</button>}
        {record && <>
            <p className="text-xs">Source Design: {record.source_design_id}</p>
            {!record.sequences.length ? <p>No explicitly linked sequence or prediction jobs are recorded. PAE, ipSAE and fold/pose evidence: Unmeasured.</p> : <label>Sequence <select className={control} aria-label="Evidence sequence" value={sequenceId} onChange={event => { setSequenceId(event.target.value); setPredictionId(''); }}>
                <option value="">Choose a sequence / sequence-design job</option>{record.sequences.map(row => { const key = JSON.stringify([row.job_id, row.design_id]); return <option key={key} value={key}>{row.name ?? row.design_id ?? 'No sequence published'} · {row.model_id} · {row.status} · {row.job_id}</option>; })}
            </select></label>}
            {sequence && <div className="space-y-2">
                <a className="underline" href={`/jobs/${encodeURIComponent(sequence.job_id)}`}>Open sequence producer Job</a>
                <details><summary>Native sequence identity</summary><BindCraft2SettingsReadback value={sequence.native_identity} /></details>
                {!sequence.predictions.length ? <p>Prediction evidence: Unmeasured. No explicitly linked prediction job is recorded.</p> : <label>Prediction sample and target <select className={control} aria-label="Evidence prediction" value={predictionId} onChange={event => setPredictionId(event.target.value)}>
                    <option value="">Choose a prediction sample and target state</option>{sequence.predictions.map(row => <option key={predictionKey(row)} value={predictionKey(row)}>{row.name ?? row.design_id ?? 'No structure published'} · {row.model_id} · target {evidenceText(row.target_state)} · {row.status} · {row.job_id}</option>)}
                </select></label>}
            </div>}
            {prediction && <PredictionDetail key={predictionKey(prediction)} prediction={prediction} launchContextId={launchContextId} />}
            <a className="block underline text-sm" href={jsonUrl(record)} download={`${record.source_design_id}-binder-evidence.json`}>Export candidate evidence JSON</a>
        </>}
    </section>;
}

interface RoundStep { state: string; job_id?: string; error?: string; superseded_by?: string; metadata?: Record<string, unknown>; request?: Partial<Job>; review?: unknown }
interface RoundReadback { job_id: string; state: string; steps: Record<string, RoundStep>; errors: Record<string, unknown> }
function RoundProgress({ jobId }: { jobId: string }) {
    const cache = useQueryClient();
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const url = `/api/binder-continuation/${encodeURIComponent(jobId)}/round`;
    const query = useQuery({ queryKey: ['binder-round-progress', jobId], retry: false, refetchInterval: 5000,
        queryFn: async ({ signal }) => {
            const { data } = await api.get<RoundReadback>(url, { signal });
            if (data.job_id !== jobId || typeof data.state !== 'string' || !data.steps) throw Error('Round progress readback unavailable');
            return data;
        } });
    const act = async (request?: Partial<Job>) => {
        setBusy(true); setError('');
        try {
            if (request) await submitJob(request, { launchContext: Boolean(request.launch_context_id) });
            else await api.post(`${url}/retry`, {});
            const readback = await query.refetch();
            if (readback.isError) throw Error('Action returned, but round readback failed. Refresh to inspect its persisted state.');
            await cache.invalidateQueries({ queryKey: ['binder-evidence', jobId] });
        } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
        finally { setBusy(false); }
    };
    if (query.isError) return <p role="status">Round progress unavailable. <button type="button" onClick={() => void query.refetch()}>Refresh round progress</button></p>;
    const progress = query.data;
    if (!progress) return <p role="status">Reading round progress…</p>;
    return <section aria-label="Binder round progress" className="space-y-2">
        <p>Round: {progress.state}</p>
        {Object.entries(progress.errors ?? {}).map(([source, reason]) => <p key={source} role="status">Source {source}: {evidenceText(reason)}</p>)}
        {Object.entries(progress.steps).map(([id, step]) => <div key={id} className="text-sm">
            <span>{evidenceText(step.metadata?.stage)} · source {evidenceText(step.metadata?.source_design_id)} · target {evidenceText(step.metadata?.target_state)} · {step.state}</span>
            {step.job_id && <a className="ml-2 underline" href={`/jobs/${encodeURIComponent(step.job_id)}`}>Open child Job</a>}
            {step.error && <p role="status">{step.error}</p>}
            {step.superseded_by && <p>Retained history; retry step {step.superseded_by}</p>}
            {step.state === 'review_required' && step.request && <><button className={control} type="button" disabled={busy} onClick={() => void act(step.request)}>Review prepared remote step {id}</button><details><summary>Retained preparation</summary><BindCraft2SettingsReadback value={{ request: step.request, review: step.review }} /></details></>}
        </div>)}
        {['needs_retry', 'completed_with_errors'].includes(progress.state) && <button className={control} type="button" disabled={busy} onClick={() => void act()}>Retry binder round</button>}
        {error && <p role="alert">{error}</p>}
    </section>;
}

function AnalysisEvidence({ run }: { run: PersistedAnalysisRun<unknown> }) {
    const [sort, setSort] = useState('');
    const result = object(run.result);
    const pairs = Array.isArray(result.pair_scores) ? result.pair_scores.map(object) : [];
    const keys = Array.from(new Set(pairs.flatMap(pair => Object.keys(pair).filter(key => pair[key] == null || (!Array.isArray(pair[key]) && typeof pair[key] !== 'object')))));
    const sorted = [...pairs].sort((a, b) => {
        if (!sort) return 0;
        const x = a[sort], y = b[sort];
        if (x == null || y == null) return x == null ? y == null ? 0 : 1 : -1;
        return typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    });
    return <section className="space-y-2" aria-label={`Persisted ${run.analysis_type}`}>
        <h5 className="font-medium">{run.analysis_type} · {run.status} · {run.run_id}</h5>
        {run.error_message && <p>{run.error_message}</p>}
        {result.reason != null && <p>{evidenceText(result.reason)}</p>}
        {run.analysis_type === 'binder_pose_comparison' && <>
            <dl className="grid grid-cols-2 gap-2 text-sm">
                <dt>Binder-fitted fold CA RMSD (Å)</dt><dd>{evidenceText(result.binder_fitted_ca_rmsd)}</dd>
                <dt>Target-fitted binder pose CA RMSD (Å)</dt><dd>{evidenceText(result.target_fitted_binder_ca_rmsd)}</dd>
                <dt>Target fit CA RMSD (Å)</dt><dd>{evidenceText(result.target_fit_ca_rmsd)}</dd>
                <dt>Reference Design</dt><dd>{evidenceText(result.reference_design_id)}</dd>
            </dl>
            <details><summary>Exact comparison documents and mapping counts</summary><BindCraft2SettingsReadback value={{ reference_document: result.reference_document, prediction_document: result.prediction_document, counts: result.counts }} /></details>
        </>}
        {run.analysis_type === 'ipsae_interface' && <>
            <p>Persisted output roles: binder {evidenceText(result.binder_chains)}; target {evidenceText(result.target_chains)}</p>
            {result.role_assignment != null && <details><summary>Source component to native output chain mapping</summary><BindCraft2SettingsReadback value={result.role_assignment} /></details>}
            <p>PAE cutoff: {evidenceText(result.pae_cutoff)} · distance cutoff: {evidenceText(result.dist_cutoff)}</p>
            {!pairs.length ? <p>Directional pair evidence: Unmeasured</p> : <div className="max-h-80 overflow-auto"><table className="text-xs"><thead><tr>{keys.map(key => <th className="p-2" key={key}><button type="button" onClick={() => setSort(key)} aria-label={`Sort directional pairs by ${key}`}>{key}{sort === key ? ' ↑' : ''}</button></th>)}</tr></thead><tbody>{sorted.map((pair, index) => <tr key={index}>{keys.map(key => <td className="p-2" key={key}>{evidenceText(pair[key])}</td>)}</tr>)}</tbody></table></div>}
        </>}
        <details><summary>Persisted result, mapping and native parameters</summary><BindCraft2SettingsReadback value={{ params: run.params, result: run.result, summary: run.summary, artifacts: run.artifacts }} /></details>
    </section>;
}
function PredictionDetail({ prediction: p, launchContextId }: { prediction: BinderPrediction; launchContextId?: string | null }) {
    const design = useQuery({ queryKey: ['binder-evidence-design', p.design_id], enabled: !!p.design_url, retry: false,
        queryFn: ({ signal }) => api.get<Design>(p.design_url!, { signal }).then(response => response.data) });
    const pae = useQuery({ queryKey: ['binder-evidence-pae', p.design_id], enabled: !!p.pae_url, retry: false,
        queryFn: ({ signal }) => api.get(p.pae_url!, { params: { max_size: 200 }, signal }).then(response => response.data) });
    const chains = useQuery({ queryKey: ['binder-evidence-chains', p.design_id], enabled: !!p.chain_metrics_url, retry: false,
        queryFn: ({ signal }) => api.get(p.chain_metrics_url!, { signal }).then(response => response.data) });
    const doc = design.data?.id === p.design_id ? design.data.scientific_structure_document : null;
    const boundPae = parseScientificPae(pae.data, doc, p.design_id ?? undefined);
    const boundChains = parseScientificNativeMetric(chains.data, doc, 'chain_metrics', p.design_id ?? undefined);
    return <div className="space-y-3 border-t border-[var(--border-color)] pt-3">
        <p>Prediction {p.design_id ?? 'not published'} · {p.status} · target {evidenceText(p.target_state)}</p>
        <p className="text-xs">Requested source roles: binder {evidenceText(p.binder_chains)}; target {evidenceText(p.target_chains)}. Native pair labels below come from the prediction mapping, not source chain letters.</p>
        {p.error_message && <p role="status">{p.error_message}</p>}
        <div className="flex gap-3"><a className="underline" href={`/jobs/${encodeURIComponent(p.job_id)}`}>Open prediction Job</a>{p.design_id && <a className="underline" href={nativeCandidateRoute(p.job_id, p.design_id, undefined, launchContextId)}>Open prediction Mol* workbench</a>}</div>
        <h5 className="font-medium">PAE matrix</h5>
        {boundPae.status === 'ok' ? <Plot data={[{ type: 'heatmap', z: boundPae.matrix, x: boundPae.columns.map(r => `${r.authAsymId ?? r.labelAsymId}:${r.authSeqId ?? r.labelSeqId}${r.insertionCode ?? ''}`), y: boundPae.rows.map(r => `${r.authAsymId ?? r.labelAsymId}:${r.authSeqId ?? r.labelSeqId}${r.insertionCode ?? ''}`), colorscale: 'Viridis', colorbar: { title: { text: 'PAE (Å)' } } }]} layout={{ autosize: true, height: 360, margin: { t: 20, l: 70, b: 70 }, xaxis: { title: { text: 'Scored residue' } }, yaxis: { title: { text: 'Aligned residue' } } }} style={{ width: '100%' }} useResizeHandler /> : <p>PAE: Unmeasured / unavailable. {pae.isError ? 'Native readback failed.' : boundPae.reason}</p>}
        {p.pae_url && <a className="underline" href={p.pae_url}>Open native PAE readback</a>}
        <h5 className="font-medium">Native chain-pair iPTM (separate from ipSAE)</h5>
        {boundChains.status === 'ok' ? <table className="text-sm"><thead><tr><th>Native pair</th><th>iPTM</th></tr></thead><tbody>{boundChains.chains.flatMap(a => boundChains.chains.map(b => <tr key={`${a.providerIndex}:${b.providerIndex}`}><td className="p-2">{a.chainId} → {b.chainId}</td><td className="p-2">{evidenceText(boundChains.pairChainsIptm[a.providerIndex]?.[b.providerIndex])}</td></tr>))}</tbody></table> : <p>Native pair iPTM: Unmeasured / unavailable. {boundChains.reason}</p>}
        {!p.ipsae.length && <p>ipSAE directional pairs and cutoffs: Unmeasured.</p>}
        <h5 className="font-medium">Persisted ipSAE and fold / pose evidence</h5>
        <p className="text-xs">Fold comparison fits the binder; pose comparison fits the target before evaluating the binder without refit. Only persisted analysis results are shown; no comparison is computed here.</p>
        {!p.analyses.some(run => run.analysis_type === 'binder_pose_comparison') && <p>Fold / pose comparison: Unmeasured.</p>}
        {p.analyses.map(run => <AnalysisEvidence key={run.run_id ?? run.analysis_type} run={run} />)}
    </div>;
}
