import { Link, useLocation, useParams } from 'react-router-dom';
import AlignmentWorkspace from './AlignmentWorkspace';
import AlignmentEditor from './AlignmentEditor';
import { useState } from 'react';
import { ProjectWorkflowSetupBanner, useProjectWorkflowSetup } from '../project-manager/ProjectWorkflowSetup';
import { projectManagerErrorMessage } from '../../lib/projectManager';

/** Route receiver only: native artifact and editor owners are not yet connected. */
export default function AlignmentPage() {
    const { jobId: routeJobId, resultId } = useParams<{ jobId?: string; resultId?: string }>();
    const location = useLocation();
    const setup = useProjectWorkflowSetup();
    const query = new URLSearchParams(location.search);
    // Existing Project receipts use the query form; do not silently show the
    // library for those links while the result reader is still unavailable.
    const jobId = routeJobId || (location.pathname === '/alignments' ? query.get('job_id') : null);
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
        {(jobId || resultId) && <p role="status">{jobId ? `Job ${jobId}` : `Result ${resultId}`}: alignment artifact reopening is unavailable until the native result API is connected.</p>}
        {!jobId && !resultId && section === 'New' && <>{!queryModel && <label>Alignment family <select aria-label="Alignment family" value={model} onChange={e => setSelectedModel(e.target.value)}><option value="family_msa_protein">Protein</option><option value="family_msa_nucleic">Nucleic acid</option></select></label>}<AlignmentEditor key={model} model={model}/></>}
        {!jobId && !resultId && section !== 'New' && <p role="status">{section === 'Compare' ? 'Alignment comparison is not connected yet.' : 'Alignment library is not connected yet.'}</p>}
        <AlignmentWorkspace projectLabel={setup.setup?.project_label} />
    </div>;
}
