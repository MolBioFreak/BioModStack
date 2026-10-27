import { Link, useLocation, useParams } from 'react-router-dom';
import AlignmentWorkspace from './AlignmentWorkspace';
import JbrowseViewer from './JbrowseViewer';
import Av2Viewer from './Av2Viewer';
import BiojsViewer from './BiojsViewer';
import AlignmentEditor from './AlignmentEditor';
import AlignmentLibrary from './AlignmentLibrary';
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { jobPollingInterval } from '../../lib/queryPolling';
import { api } from '../../lib/api';
import type { AlignmentDocument } from '../../lib/alignmentCoordinates';
import { ProjectWorkflowSetupBanner, useProjectWorkflowSetup } from '../project-manager/ProjectWorkflowSetup';
import { projectManagerErrorMessage } from '../../lib/projectManager';

type SupportSample = {artifact_id: string; logical_path: string | null; numerator: number; denominator: number; fraction: number | null; matched_rows: number; rows_with_unknown_mapping: number};
type Support = {receipt_id: string; reference_artifact_id: string; report: {method: string; interpretation: string; denominator_policy: string; sample_count: number; numerator: number; denominator: number; fraction: number | null; samples: SupportSample[]}};

export function EnsembleSupport({projectId, receiptId, reference, artifacts}: {projectId: string; receiptId: string; reference: string; artifacts: {id: string; logical_path?: string | null}[]}) {
    const [selected, setSelected] = useState<string[]>([]);
    const [result, setResult] = useState<Support>();
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const fraction = (x: number | null) => x === null ? 'undefined' : x.toFixed(3);
    return <section aria-label="Ensemble support"><h2>Ensemble support</h2>
        <p>Reference: {reference}. Select retained samples explicitly; including the reference counts it as a sample. Observed agreement is not probability of alignment truth.</p>
        {artifacts.map(a => <label key={a.id}><input type="checkbox" checked={selected.includes(a.id)} onChange={e => setSelected(old => e.target.checked ? [...old, a.id] : old.filter(id => id !== a.id))}/>{a.logical_path || a.id} ({a.id}) </label>)}
        <button disabled={busy || !selected.length} onClick={async () => { setBusy(true); setError(''); setResult(undefined); try {
            setResult((await api.post<Support>(`/api/family-msa/projects/${encodeURIComponent(projectId)}/ensemble-support`, {receipt_id: receiptId, reference_artifact_id: reference, member_artifact_ids: selected})).data);
        } catch (e) { setError(String(e)); } finally { setBusy(false); } }}>Compute ensemble support</button>
        {error && <p role="alert">Ensemble support failed: {error}</p>}
        {result && <div><p>Method: {result.report.method}; samples: {result.report.sample_count}; pooled retention: {result.report.numerator} / {result.report.denominator} = {fraction(result.report.fraction)}. {result.report.denominator_policy}. {result.report.interpretation}.</p>
            <ul>{result.report.samples.map((s, i) => <li key={`${s.artifact_id}:${i}`}>{s.logical_path || s.artifact_id} ({s.artifact_id}): {s.numerator} / {s.denominator} = {fraction(s.fraction)}; matched rows {s.matched_rows}; unknown mapping rows {s.rows_with_unknown_mapping}</li>)}</ul></div>}
    </section>;
}

/** Receives exact Job or Project receipt identities. */
export default function AlignmentPage() {
    const { jobId: routeJobId, resultId } = useParams<{ jobId?: string; resultId?: string }>();
    const location = useLocation();
    const setup = useProjectWorkflowSetup();
    const query = new URLSearchParams(location.search);
    // Historical receipts can still carry the Job query form.
    const jobId = routeJobId || (location.pathname === '/alignments' ? query.get('job_id') : null);
    // Project Results continuations use workspace_id; direct setup routes use project_id.
    const projectId = query.get('project_id') || query.get('workspace_id');
    const receiptId = query.get('receipt_id') || resultId;
    const base = projectId && receiptId ? `/api/family-msa/projects/${encodeURIComponent(projectId)}/receipts/${encodeURIComponent(receiptId)}` : jobId ? `/api/family-msa/jobs/${encodeURIComponent(jobId)}` : null;
    const [selection, setSelection] = useState<{base: string | null; id: string}>({base: null, id: ''});
    const job = useQuery({
        queryKey: ['alignment-result-job', jobId],
        queryFn: async ({ signal }) => (await api.get<{status: string}>(`/api/jobs/${encodeURIComponent(jobId!)}`, { signal })).data,
        enabled: Boolean(jobId && !(projectId && receiptId)), retry: false,
        refetchInterval: q => !q.state.data || ['queued', 'running'].includes(q.state.data.status) ? jobPollingInterval(3000, q) : false,
        refetchIntervalInBackground: false,
    });
    const manifest = useQuery({
        queryKey: ['alignment-result-manifest', base],
        queryFn: async ({ signal }) => (await api.get<{artifacts: {id: string; role: string; logical_path?: string | null}[]; import_provenance?: {shared_states?: {dataset_id: string | null}[]}}>(base!, { signal })).data,
        enabled: Boolean(base), retry: false,
        refetchInterval: q => jobId && !(projectId && receiptId) && (!job.data || ['queued', 'running'].includes(job.data.status)) && !q.state.data ? jobPollingInterval(3000, q) : false,
        refetchIntervalInBackground: false,
    });
    useEffect(() => {
        if (job.data && !['queued', 'running'].includes(job.data.status) && !manifest.data) void manifest.refetch();
        // Only the status transition triggers the final read.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [job.data?.status, base]);
    const artifacts = manifest.data?.artifacts ?? [];
    const alignments = artifacts.filter(x => x.role === 'alignment');
    const sourceMetadata = artifacts.find(x => x.role === 'source_metadata');
    const observations = useQuery({
        queryKey: ['alignment-source-observations', base, sourceMetadata?.id],
        queryFn: async ({ signal }) => (await api.get<{format_observations?: string[]}>(`${base}/artifacts/${encodeURIComponent(sourceMetadata!.id)}`, { signal })).data.format_observations ?? [],
        enabled: Boolean(base && sourceMetadata), retry: false,
    });
    const selected = selection.base === base ? selection.id : '';
    const member = selected || (alignments.length === 1 ? alignments[0].id : '');
    const documentQuery = useQuery({
        queryKey: ['alignment-result-document', base, member],
        queryFn: async ({ signal }) => {
            const { data } = await api.get<{result_id: string; rows: {row_id: string; label: string; aligned: string; source_member_id?: string | null; occurrence_id?: string | null; source_positions?: (number | null)[] | null}[]}>(`${base}/documents/${encodeURIComponent(member)}`, { signal });
            return {resultId: data.result_id, rows: data.rows.map(row => ({rowId: row.row_id, label: row.label, aligned: row.aligned, sourceMemberId: row.source_member_id ?? undefined, occurrenceId: row.occurrence_id ?? undefined, sourcePositions: row.source_positions ?? undefined}))} satisfies AlignmentDocument;
        },
        enabled: Boolean(base && member), retry: false,
    });
    const document = documentQuery.data;
    const error = manifest.error || documentQuery.error || job.error;
    const [selectedModel, setSelectedModel] = useState('family_msa_protein');
    const queryModel = query.get('model');
    const model = queryModel === 'family_msa_protein' || queryModel === 'family_msa_nucleic' ? queryModel : selectedModel;
    const section = location.pathname.endsWith('/compare') ? 'Compare' : location.pathname.endsWith('/new') || (location.pathname === '/alignments' && query.has('model')) ? 'New' : 'Library';
    return <div className="mx-auto max-w-7xl p-4">
        {setup.active && setup.isLoading && <p role="status">Loading Project workflow setup…</p>}
        {setup.active && setup.error && <p role="alert">Project workflow setup unavailable: {projectManagerErrorMessage(setup.error)}</p>}
        {setup.setup && <ProjectWorkflowSetupBanner setup={setup.setup} />}
        <nav aria-label="Alignment sections" className="flex flex-wrap gap-4 py-3">
            {(['Library', 'New', 'Compare'] as const).map(name => <Link key={name} aria-current={section === name ? 'page' : undefined} to={{ pathname: `/alignments/${name.toLowerCase()}`, search: setup.active ? location.search : '' }}>{name}</Link>)}
        </nav>
        {base && !manifest.data && !error && <p role="status">Waiting for alignment result{job.data?.status ? ` (Job ${job.data.status})` : ''}…</p>}
        {base && manifest.data && !alignments.length && <p role="status">No alignment artifact is available in this result. <button onClick={() => void manifest.refetch()}>Retry</button></p>}
        {error && <div role="alert">Alignment result unavailable: {String(error)} <button onClick={() => { if (job.error) void job.refetch(); if (manifest.error) void manifest.refetch(); if (documentQuery.error) void documentQuery.refetch(); }}>Retry</button></div>}
        {observations.error && <p role="status">Source format observations unavailable; the original file remains downloadable.</p>}
        {Boolean(observations.data?.length) && <section aria-label="Source format observations"><h2>Source format observations</h2><ul>{observations.data?.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
        {base && alignments.length > 1 && <label>Alignment member <select value={selected} onChange={e => setSelection({base, id: e.target.value})}><option value="">Choose a member</option>{alignments.map((x, index) => <option key={x.id} value={x.id}>{x.logical_path || `Alignment ${index + 1}`}</option>)}</select></label>}
        {projectId && receiptId && member && alignments.length > 1 && <EnsembleSupport key={`${projectId}:${receiptId}:${member}`} projectId={projectId} receiptId={receiptId} reference={member} artifacts={alignments}/>}
        {!base && !resultId && section === 'New' && <>{!queryModel && <label>Alignment family <select aria-label="Alignment family" value={model} onChange={e => setSelectedModel(e.target.value)}><option value="family_msa_protein">Protein</option><option value="family_msa_nucleic">Nucleic acid</option></select></label>}<AlignmentEditor key={model} model={model}/></>}
        {!base && !resultId && section === 'Library' && <AlignmentLibrary initialProject={projectId}/>}
        {!base && !resultId && section === 'Compare' && <p role="status">Alignment comparison is not connected yet.</p>}
        <AlignmentWorkspace importedStateIds={(manifest.data?.import_provenance?.shared_states ?? []).flatMap(item => item.dataset_id ? [item.dataset_id] : [])} viewers={{ jbrowse: JbrowseViewer, 'alignment-viewer-2': Av2Viewer, biojs: BiojsViewer }} projectLabel={setup.setup?.project_label || projectId || undefined} projectId={projectId || undefined} receiptId={projectId ? receiptId || undefined : undefined} document={document} downloads={base ? artifacts.map(x => ({label: x.role === 'source' ? `Original file (${x.logical_path || 'source'})` : `${x.role}${x.logical_path ? ` (${x.logical_path})` : ''}`, href: `${base}/artifacts/${encodeURIComponent(x.id)}`})) : []} />
    </div>;
}
