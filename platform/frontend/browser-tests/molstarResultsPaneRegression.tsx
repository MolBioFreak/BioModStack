/* eslint-disable react-refresh/only-export-components */
import React, { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import StructureViewerPane from '../src/components/StructureViewerPane';
import { ThemeProvider } from '../src/components/ThemeProvider';
import type { Design, Job } from '../src/lib/api';

const DESIGN_ID = 'results-viewer-regression-design';
const pdb = `HEADER    BMS RESULTS VIEWER REGRESSION
ATOM      1  N   ALA A   1      11.104  13.207  14.331  1.00 90.00           N
ATOM      2  CA  ALA A   1      12.560  13.207  14.331  1.00 90.00           C
ATOM      3  C   ALA A   1      13.000  14.630  14.331  1.00 90.00           C
ATOM      4  O   ALA A   1      12.300  15.600  14.331  1.00 90.00           O
ATOM      5  N   GLY A   2      14.250  14.760  14.331  1.00 80.00           N
ATOM      6  CA  GLY A   2      14.850  16.080  14.331  1.00 80.00           C
ATOM      7  C   GLY A   2      16.360  15.970  14.331  1.00 80.00           C
ATOM      8  O   GLY A   2      17.020  16.990  14.331  1.00 80.00           O
TER
END
`;

interface RegressionDiagnostics {
    mountAdds: number;
    mountRemoves: number;
    parentRenders: number;
    maximumDepthErrors: number;
    complete: boolean;
    pass: boolean;
    status: string | null;
    mounts: number;
    canvases: number;
    runtimeError: string | null;
}

declare global {
    interface Window {
        __bmsResultsViewerRegression?: RegressionDiagnostics;
    }
}

const diagnostics: RegressionDiagnostics = {
    mountAdds: 0,
    mountRemoves: 0,
    parentRenders: 0,
    maximumDepthErrors: 0,
    complete: false,
    pass: false,
    status: null,
    mounts: 0,
    canvases: 0,
    runtimeError: null,
};
window.__bmsResultsViewerRegression = diagnostics;
window.addEventListener('error', (event) => {
    diagnostics.runtimeError = event.error instanceof Error
        ? `${event.error.message}\n${event.error.stack ?? ''}`
        : String(event.message || event.error || 'unknown runtime error');
    document.body.dataset.bmsResultsViewerRegression = 'error';
});

const nodeContainsMount = (node: Node): boolean => (
    node instanceof Element
    && (node.matches('[data-bms-molstar-mount]') || Boolean(node.querySelector('[data-bms-molstar-mount]')))
);
const observer = new MutationObserver((records) => {
    for (const record of records) {
        for (const node of record.addedNodes) if (nodeContainsMount(node)) diagnostics.mountAdds += 1;
        for (const node of record.removedNodes) if (nodeContainsMount(node)) diagnostics.mountRemoves += 1;
    }
});
observer.observe(document.body, { childList: true, subtree: true });

const originalConsoleError = console.error;
console.error = (...args: unknown[]) => {
    if (args.some((arg) => String(arg).includes('Maximum update depth exceeded'))) {
        diagnostics.maximumDepthErrors += 1;
    }
    originalConsoleError(...args);
};

const originalFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const requestUrl = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, window.location.origin);
    if (requestUrl.pathname === `/api/designs/${DESIGN_ID}/pdb`) {
        return new Response(pdb, { status: 200, headers: { 'content-type': 'chemical/x-pdb' } });
    }
    if (requestUrl.pathname === `/api/designs/${DESIGN_ID}/residue-metrics`) {
        return Response.json({ plddt: [], residue_numbers: [] });
    }
    return originalFetch(input, init);
};

const design = {
    id: DESIGN_ID,
    name: 'Results Viewer pLDDT fallback regression',
    artifact_class: 'structure_model',
    artifact_schema_version: '1',
    viewer_capabilities: ['structure_viewer', 'structure_confidence_metrics'],
    plddt: 90,
    provenance: { analysis_lens: 'validation' },
} as unknown as Design;
const job = {
    id: 'results-viewer-regression-job',
    name: 'Results Viewer regression job',
    model_id: 'protenix',
    params: {},
} as unknown as Job;

function ResultsViewerFixture() {
    const [selectedDesignId, setSelectedDesignId] = useState(DESIGN_ID);
    const [colorMode, setColorMode] = useState<'default' | 'plddt' | 'cdr' | 'frustration' | 'fampnn_psce'>('plddt');

    useEffect(() => {
        diagnostics.parentRenders += 1;
    });

    // Mirrors ResultsViewer's validation/Protenix default preference. The pane
    // may derive a safe effective fallback, but must never write it back here.
    useEffect(() => {
        setColorMode('plddt');
    }, [selectedDesignId]);

    return (
        <div style={{ width: '100%', maxWidth: 1400, margin: '0 auto' }}>
            <div data-testid="requested-color-mode">requested:{colorMode}</div>
            <StructureViewerPane
                selectedDesignId={selectedDesignId}
                setSelectedDesignId={setSelectedDesignId}
                designs={[design]}
                selectedDesign={design}
                colorMode={colorMode}
                setColorMode={setColorMode}
                structureFormat="pdb"
                viewerAnalyses={{ chainMetrics: {} }}
                activeJob={job}
                getMetricColor={() => 'text-slate-200'}
            />
        </div>
    );
}

const element = document.querySelector<HTMLElement>('#root');
if (!element) throw new Error('results viewer regression root missing');
const root = createRoot(element);
root.render(<StrictMode><ThemeProvider><ResultsViewerFixture /></ThemeProvider></StrictMode>);

window.setTimeout(() => {
    const adapter = document.querySelector<HTMLElement>('[data-bms-molstar-adapter="direct-4.5.0"]');
    diagnostics.status = adapter?.dataset.bmsMolstarStatus ?? null;
    diagnostics.mounts = document.querySelectorAll('[data-bms-molstar-mount]').length;
    diagnostics.canvases = adapter?.querySelectorAll('canvas').length ?? 0;
    diagnostics.complete = true;
    diagnostics.pass = diagnostics.maximumDepthErrors === 0
        && diagnostics.mountAdds <= 3
        && diagnostics.mountRemoves <= 2
        && diagnostics.parentRenders <= 12
        && diagnostics.mounts === 1
        && diagnostics.canvases >= 1
        && diagnostics.status === 'ready';
    document.body.dataset.bmsResultsViewerRegression = diagnostics.pass ? 'pass' : 'fail';
}, 8000);

window.addEventListener('beforeunload', () => {
    observer.disconnect();
    console.error = originalConsoleError;
    window.fetch = originalFetch;
    root.unmount();
});
