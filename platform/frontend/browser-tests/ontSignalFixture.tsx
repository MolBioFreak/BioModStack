import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ReadAndSignalWorkbench } from '../src/components/ngs/ReadAndSignalWorkbench';
import { fetchOntSignalViewerSession, type OntSignalViewerSession } from '../src/lib/api';
import '../src/index.css';

// Only mounting/navigation metadata is a fixture. Every data call goes to the
// real native router/SQL store; no fetch replacement, response replay or HTML shim.
function App() {
    const id = new URLSearchParams(location.search).get('viewer') || 'signal-viewer-raw';
    const [viewer, setViewer] = useState<OntSignalViewerSession | null>(null);
    const [error, setError] = useState('');
    useEffect(() => { void fetchOntSignalViewerSession(id).then(setViewer).catch(e => setError(String(e))); }, [id]);
    return <main style={{ width: 1200, padding: 16 }}>
        <h1>Retained native signal receiving — synthetic SQL identities, own-read controls</h1>
        <nav>{['raw', 'dna', 'rna', 'dna-comparison', 'rna-comparison'].map(name =>
            <a key={name} href={`?viewer=signal-viewer-${name}`} style={{ marginRight: 16 }}>{name}</a>)}</nav>
        {error && <div role="alert">{error}</div>}
        {viewer && <ReadAndSignalWorkbench key={id} datasetId={viewer.dataset_id} runId={viewer.run_id}
            observedGeneration={viewer.observed_generation} alignmentJobId={viewer.alignment_job_id || ''}
            alignmentSession={null} referenceRevisionId={viewer.reference_revision_id} currentLocus={null}
            viewerSession={viewer} igvState={viewer.igv_state as never} onViewerSessionChange={setViewer}
            onNavigateIgv={() => { throw Error('Genomic placement is outside this own-read control'); }} />}
    </main>;
}
createRoot(document.getElementById('root')!).render(<App />);
