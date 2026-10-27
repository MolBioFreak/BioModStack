import { Link, useLocation, useParams } from 'react-router-dom';
import AlignmentWorkspace from './AlignmentWorkspace';
import AlignmentEditor from './AlignmentEditor';
import { useEffect, useState } from 'react';
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
    const projectId = query.get('project_id');
    const receiptId = query.get('receipt_id') || resultId;
    const base = projectId && receiptId ? `/api/family-msa/projects/${encodeURIComponent(projectId)}/receipts/${encodeURIComponent(receiptId)}` : jobId ? `/api/family-msa/jobs/${encodeURIComponent(jobId)}` : null;
    const [artifacts, setArtifacts] = useState<{id: string; role: string}[]>([]);
    const [selected, setSelected] = useState('');
    const [document, setDocument] = useState<AlignmentDocument>();
    const [error, setError] = useState('');
    useEffect(() => {
        setArtifacts([]); setSelected(''); setDocument(undefined); setError('');
        if (!base) return;
        const controller = new AbortController();
        api.get<{artifacts: {id: string; role: string}[]}>(base, {signal: controller.signal}).then(({data}) => {
            setArtifacts(data.artifacts);
            const alignments = data.artifacts.filter(x => x.role === 'alignment');
            if (alignments.length === 1) setSelected(alignments[0].id);
        }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
        return () => controller.abort();
    }, [base]);
    useEffect(() => {
        setDocument(undefined);
        if (!base || !selected) return;
        const controller = new AbortController();
        api.get<{result_id: string; rows: {row_id: string; label: string; aligned: string}[]}>(`${base}/documents/${encodeURIComponent(selected)}`, {signal: controller.signal}).then(({data}) => {
            setDocument({resultId: data.result_id, rows: data.rows.map(row => ({rowId: row.row_id, label: row.label, aligned: row.aligned}))});
        }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
        return () => controller.abort();
    }, [base, selected]);
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
        {error && <p role="alert">Alignment result unavailable: {error}</p>}
        {base && artifacts.filter(x => x.role === 'alignment').length > 1 && <label>Alignment member <select value={selected} onChange={e => {setError(''); setSelected(e.target.value)}}><option value="">Choose a member</option>{artifacts.filter(x => x.role === 'alignment').map(x => <option key={x.id} value={x.id}>{x.id}</option>)}</select></label>}
        {!base && !resultId && section === 'New' && <>{!queryModel && <label>Alignment family <select aria-label="Alignment family" value={model} onChange={e => setSelectedModel(e.target.value)}><option value="family_msa_protein">Protein</option><option value="family_msa_nucleic">Nucleic acid</option></select></label>}<AlignmentEditor key={model} model={model}/></>}
        {!base && !resultId && section !== 'New' && <p role="status">{section === 'Compare' ? 'Alignment comparison is not connected yet.' : 'Alignment library is not connected yet.'}</p>}
        <AlignmentWorkspace projectLabel={setup.setup?.project_label} document={document} downloads={base ? artifacts.map(x => ({label: `${x.role} (${x.id})`, href: `${base}/artifacts/${encodeURIComponent(x.id)}`})) : []} />
    </div>;
}
