import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
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
    useGlobalExperimentContext: () => {
        const [, setParams] = useSearchParams();
        const updateQueryParams = React.useCallback((changes: Record<string,string|null>) => {
            mocks.updateQueryParams(changes);
            setParams(current => { const next = new URLSearchParams(current); for (const [k,v] of Object.entries(changes)) {
                if (v) next.set(k,v); else next.delete(k);
            } return next; });
        }, [setParams]);
        return { updateQueryParams, workspaces: [{id:'proj-gg',name:'Golden Gate'},{id:'proj-bio',name:'Bionova'}], contextHref: (p: string) => p, ...mocks.context };
    },
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
    getProjectHub: vi.fn(),
    getProjectSequences: mocks.projectSeqs,
    searchProjects: mocks.searchProjects,
}));

import ProjectContext from '../../src/components/molbio-ngs/NgsMolBioProjectHub';
import { getProjectHub } from '../../src/lib/projectManager';
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


it('selects project work from the Molecular Toolset banner and keeps the shelf in sync', async () => {
    vi.mocked(getProjectHub).mockImplementation(async id => ({
        project: {id, name: id === 'proj-gg' ? 'Golden Gate' : 'Bionova', experiments: [], linked_local_projects: [], workflow_setups: []},
        capabilities: {has_in_silico:true, has_molbio:true, has_ngs_ont:true},
        sections:{in_silico:[], molecular_sequences:[], molecular_operations:[{id:id+'-op',title:id+' saved operation',subtitle:'',badges:[],open_href:'/designer?project_id='+id+'&molbio_operation_id='+id+'-op'}],ngs_ont_and_alignments:[]}, next_cursor:null,
    } as any));
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/designer']}>
        <ProjectContext presentation="viewer"><MolBioToolkitV2 /></ProjectContext>
    </MemoryRouter></QueryClientProvider>));
    const picker=host.querySelector<HTMLSelectElement>('select[aria-label="Select project"]');
    expect(picker).not.toBeNull();
    const choose = async (sel:HTMLSelectElement,value:string) => act(async()=>{sel.value=value;sel.dispatchEvent(new Event('change',{bubbles:true}));});
    await choose(picker!, 'proj-gg');
    await vi.waitFor(()=>expect(host.querySelector('h1')?.textContent).toBe('Golden Gate'));
    expect(host.textContent).toContain('Notes and research records');
    await act(async()=>host.querySelector<HTMLButtonElement>('#section-molbio-heading')!.click());
    const operations=Array.from(host.querySelectorAll('button')).find(b=>b.textContent?.includes('Operations'))!;
    await act(async()=>operations.click());
    expect(host.querySelector('a[href*="molbio_operation_id=proj-gg-op"]')).not.toBeNull();
    const editor=Array.from(host.querySelectorAll('button')).find(b=>b.textContent==='Sequence editor')!;
    await act(async()=>editor.click());
    const shelf=host.querySelector<HTMLSelectElement>('select[aria-label="Sequences from"]')!;
    expect(shelf.value).toBe('project:proj-gg');
    await choose(shelf,'project:proj-bio');
    await vi.waitFor(()=>expect(host.querySelector('h1')?.textContent).toBe('Bionova'));
    expect(picker!.value).toBe('proj-bio');
    expect(host.querySelector('a[href*="molbio_operation_id=proj-gg-op"]')).toBeNull();
});
