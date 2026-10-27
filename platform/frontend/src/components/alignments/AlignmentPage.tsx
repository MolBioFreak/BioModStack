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
    const selected = selection.base === base ? selection.id : '';
    const member = selected || (alignments.length === 1 ? alignments[0].id : '');
    const documentQuery = useQuery({
        queryKey: ['alignment-result-document', base, member],
        queryFn: async ({ signal }) => {
            const { data } = await api.get<{result_id: string; rows: {row_id: string; label: string; aligned: string}[]}>(`${base}/documents/${encodeURIComponent(member)}`, { signal });
            return {resultId: data.result_id, rows: data.rows.map(row => ({rowId: row.row_id, label: row.label, aligned: row.aligned}))} satisfies AlignmentDocument;
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
        {base && alignments.length > 1 && <label>Alignment member <select value={selected} onChange={e => setSelection({base, id: e.target.value})}><option value="">Choose a member</option>{alignments.map((x, index) => <option key={x.id} value={x.id}>{x.logical_path || `Alignment ${index + 1}`}</option>)}</select></label>}
        {!base && !resultId && section === 'New' && <>{!queryModel && <label>Alignment family <select aria-label="Alignment family" value={model} onChange={e => setSelectedModel(e.target.value)}><option value="family_msa_protein">Protein</option><option value="family_msa_nucleic">Nucleic acid</option></select></label>}<AlignmentEditor key={model} model={model}/></>}
        {!base && !resultId && section === 'Library' && <AlignmentLibrary initialProject={projectId}/>}
        {!base && !resultId && section === 'Compare' && <p role="status">Alignment comparison is not connected yet.</p>}
        <AlignmentWorkspace importedStateIds={(manifest.data?.import_provenance?.shared_states ?? []).flatMap(item => item.dataset_id ? [item.dataset_id] : [])} viewers={{ jbrowse: JbrowseViewer, 'alignment-viewer-2': Av2Viewer, biojs: BiojsViewer }} projectLabel={setup.setup?.project_label || projectId || undefined} projectId={projectId || undefined} receiptId={projectId ? receiptId || undefined : undefined} document={document} downloads={base ? artifacts.map(x => ({label: x.role === 'source' ? `Original file (${x.logical_path || 'source'})` : `${x.role}${x.logical_path ? ` (${x.logical_path})` : ''}`, href: `${base}/artifacts/${encodeURIComponent(x.id)}`})) : []} />
    </div>;
}
