import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    context: {} as Record<string, unknown>,
    updateQueryParams: vi.fn(),
    recent: vi.fn(),
    projectSeqs: vi.fn(),
    searchProjects: vi.fn(),
    getSeq: vi.fn(),
}));

vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: () => <div data-viewer /> }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({ SequenceHeader: () => null }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: () => null }));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({ AutoAnnotatePanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/panels', () =>
    Object.fromEntries(['AlignmentPanel', 'AssemblyPanel', 'DigestPanel', 'HistoryPanel', 'PCRPanel', 'PrimerPanel', 'RnaStructurePanel', 'FeaturePanel', 'EditPanel', 'SearchPanel'].map((name) => [name, () => null]))
);
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({ RnaStructureViewer: () => null }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: vi.fn().mockResolvedValue([]) }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        updateQueryParams: mocks.updateQueryParams,
        contextHref: (p: string) => p,
        ...mocks.context,
    }),
}));
vi.mock('../../src/lib/restrictionAnalysis', async (original) => ({
    ...(await original<any>()),
    fetchRestrictionCatalogBrowse: async () => ({ catalog: { catalog_id: 'fixture' }, items: [] }),
    fetchRestrictionAnalysisBatch: async () => ({}),
}));
vi.mock('../../src/lib/api', async (original) => ({
    ...(await original<any>()),
    fetchNucleotideSequences: mocks.recent,
    fetchNucleotideSequence: mocks.getSeq,
    fetchPrimerTmOptions: async () => ({
        data: { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} },
    }),
}));
vi.mock('../../src/lib/projectManager', async (original) => ({
    ...(await original<any>()),
    getProjectSequences: mocks.projectSeqs,
    searchProjects: mocks.searchProjects,
}));

import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';

let host: HTMLDivElement;
let root: Root;
let client: QueryClient;

const seqItem = (id: string, name = id) => ({
    id,
    name,
    sequence_type: 'dna',
    is_circular: true,
    length: 1200,
    gc_content: 50,
    feature_count: 2,
    primer_count: 0,
    version: 1,
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    reopen_href: `/designer?project_id=proj-gg&molbio_sequence_id=${id}`,
    is_head_editable: true,
    pinned_revision_id: null,
});

beforeEach(() => {
    localStorage.clear();
    window.innerWidth = 1400;
    mocks.context = {};
    mocks.updateQueryParams.mockReset();
    mocks.recent.mockReset().mockImplementation(async (params?: Record<string, unknown>) => ({
        data: params?.project_membership === 'unassigned'
            ? [seqItem('seq-unassigned', 'pUnassigned')]
            : [seqItem('seq-unassigned', 'pUnassigned'), seqItem('seq-bionova', 'pBionova')],
    }));
    mocks.projectSeqs.mockReset().mockImplementation(async (projectId: string) => ({
        project_id: projectId,
        project_name: projectId === 'proj-gg' ? 'Golden Gate' : 'Bionova',
        project_scope: 'global',
        domains: [{ domain_experiment_id: 'dom-1', experiment_id: 'exp-1', name: 'Dom 1', current_state_revision_id: 'srev-1' }],
        items: projectId === 'proj-gg' ? [seqItem('seq-gg', 'pGoldenGate')] : [seqItem('seq-bionova', 'pBionova')],
    }));
    mocks.searchProjects.mockReset().mockResolvedValue({
        items: [
            { id: 'proj-gg', name: 'Golden Gate', lifecycle_state: 'active', project_scope: 'global' },
            { id: 'proj-bio', name: 'Bionova', lifecycle_state: 'active', project_scope: 'global' },
        ],
    });
    mocks.getSeq.mockReset().mockImplementation(async (id: string) => ({
        data: { ...seqItem(id), sequence: 'ATGCGTAC', features: [], primers: [], analysis_tracks: [] },
    }));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ workups: [] }) }));
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    host.remove();
    vi.unstubAllGlobals();
});

it('loads project sequences from ?project_id= alone and supports Sequences from dropdown switching', async () => {
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={['/designer?project_id=proj-gg']}>
                    <MolBioToolkitV2 />
                </MemoryRouter>
            </QueryClientProvider>
        );
    });
    await vi.waitFor(() => {
        expect(host.textContent).toContain('pGoldenGate');
    });
    expect(mocks.projectSeqs).toHaveBeenCalledWith('proj-gg', expect.any(Object));
    const backLink = host.querySelector('a[href="/projects/proj-gg"]');
    expect(backLink).not.toBeNull();
    expect(backLink?.textContent).toContain('Back to project');

    const select = host.querySelector('select[aria-label="Sequences from"]') as HTMLSelectElement;
    expect(select).not.toBeNull();
    expect(select.value).toBe('project:proj-gg');

    // Switch to another project via dropdown
    await act(async () => {
        select.value = 'project:proj-bio';
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await vi.waitFor(() => {
        expect(host.textContent).toContain('pBionova');
    });
    expect(mocks.projectSeqs).toHaveBeenCalledWith('proj-bio', expect.any(Object));

    // Switch to All sequences
    await act(async () => {
        select.value = 'all';
        select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await vi.waitFor(() => {
        expect(host.textContent).toContain('pUnassigned');
        expect(host.textContent).toContain('pBionova');
    });
});
