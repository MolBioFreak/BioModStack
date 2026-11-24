import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ context: {} as Record<string, unknown>, header: {} as any, input: {} as any, recent: vi.fn(), hub: vi.fn(), get: vi.fn(), demos: vi.fn() }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: () => <div data-viewer /> }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({ SequenceHeader: (props: any) => { mocks.header = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { mocks.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({ AutoAnnotatePanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])));
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({ RnaStructureViewer: () => null }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: mocks.demos }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: (p: string) => p, ...mocks.context }) }));
vi.mock('../../src/lib/restrictionAnalysis', async original => ({ ...await original<any>(), fetchRestrictionCatalogBrowse: async () => ({ catalog: { catalog_id: 'fixture' }, items: [] }), fetchRestrictionAnalysisBatch: async () => ({}) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<any>(),
    fetchNucleotideSequences: mocks.recent, fetchProjectHub: mocks.hub, fetchNucleotideSequence: mocks.get,
    fetchPrimerTmOptions: async () => ({ data: { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} } }),
}));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
let host: HTMLDivElement, root: Root, client: QueryClient;
const item = (id: string) => ({ id, name: id, sequence_type: 'dna', length: 4, feature_count: 0, is_circular: false, created_at: '', updated_at: null });
const project = (name: string) => ({ plasmids: [{ ...item(name), sequence_id: name, length_bp: 4, topology: 'linear', reopen_href: `/designer?molbio_sequence_id=${name}` }] });
const context = (id: string | null) => id ? { workspaceId: id, globalExperimentId: `experiment-${id}`, domainExperimentId: `domain-${id}`, stateRevisionId: `state-${id}` } : {};
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
const shelf = () => host.querySelector('.sequence-library')!;
async function mount(id: string | null) {
    mocks.context = context(id);
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><MolBioToolkitV2 /></MemoryRouter></QueryClientProvider>));
}
async function click(text: string) {
    const button = [...shelf().querySelectorAll('button')].find(button => button.textContent?.trim() === text)!;
    expect(button).toBeDefined();
    await act(async () => button.click());
}
beforeEach(() => {
    localStorage.clear(); window.innerWidth = 1400;
    mocks.recent.mockReset().mockResolvedValue({ data: [item('orphan-sequence')] });
    mocks.hub.mockReset().mockImplementation(async (id: string) => project(`sequence-${id}`));
    mocks.get.mockReset().mockImplementation(async (id: string) => ({ data: { ...item(id), sequence: 'ACGT', features: [], primers: [], analysis_tracks: [], version: 1 } }));
    mocks.demos.mockReset().mockResolvedValue([{ name: 'Demo example', sequence: 'ACGT', circular: false, sequenceType: 'dna', features: [] }]);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ workups: [] }) }));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals(); });

it('clears project records on deselection and ignores a late response from that project', async () => {
    const late = deferred<any>(); mocks.hub.mockReturnValue(late.promise);
    await mount('project-a');
    await mount(null);
    expect(shelf().textContent).toContain('orphan-sequence');
    expect(mocks.recent).toHaveBeenCalledWith(expect.objectContaining({ project_membership: 'unassigned' }));
    await act(async () => late.resolve(project('column-reuse-plasmid')));
    expect(shelf().textContent).not.toContain('column-reuse-plasmid');
    expect(shelf().textContent).toContain('orphan-sequence');
    const demoButton = [...shelf().querySelectorAll('button')].find(button => button.textContent?.includes('Demo Plasmids'))!;
    await act(async () => demoButton.click());
    expect(shelf().textContent).toContain('Demo example');
});

it('shows only the selected project and drops the unassigned override when switching projects', async () => {
    await mount('project-a');
    expect(shelf().textContent).toContain('sequence-project-a');
    expect(mocks.recent).not.toHaveBeenCalled();
    await click('Unassigned DNA sequences');
    expect(shelf().textContent).toContain('orphan-sequence');
    expect(shelf().textContent).not.toContain('sequence-project-a');
    const next = deferred<any>(); mocks.hub.mockReturnValueOnce(next.promise);
    await mount('project-b');
    expect(shelf().textContent).not.toContain('sequence-project-a');
    expect(shelf().textContent).not.toContain('orphan-sequence');
    await act(async () => next.resolve(project('sequence-project-b')));
    expect(shelf().textContent).toContain('sequence-project-b');
    expect(shelf().textContent).not.toContain('sequence-project-a');
    await mount(null);
    expect(shelf().textContent).not.toContain('sequence-project-b');
    expect(shelf().textContent).toContain('orphan-sequence');
});

it('keeps an independently open saved sequence visible outside the recent page', async () => {
    mocks.recent.mockImplementation(async params => ({ data: params.sequence_ids ? [item('older-open-sequence')] : [item('orphan-sequence')] }));
    await mount(null);
    await act(async () => mocks.input.onSelectSequence('older-open-sequence'));
    expect(shelf().textContent).toContain('older-open-sequence');
    expect(shelf().textContent).toContain('orphan-sequence');
    expect(mocks.recent).toHaveBeenCalledWith(expect.objectContaining({ project_membership: 'unassigned', sequence_ids: 'older-open-sequence' }));
    await mount('project-a');
    expect(shelf().textContent).not.toContain('older-open-sequence');
    await mount(null);
    expect(shelf().textContent).toContain('older-open-sequence');
    expect(mocks.header.sequenceData.name).toBe('older-open-sequence');
});

it('does not fall back to a global list when membership lookup fails', async () => {
    await mount('project-a');
    mocks.recent.mockRejectedValue(new Error('Membership lookup unavailable'));
    await mount(null);
    expect(shelf().textContent).not.toContain('sequence-project-a');
    expect(shelf().textContent).not.toContain('orphan-sequence');
    expect(mocks.recent.mock.calls.every(([params]) => params.project_membership === 'unassigned')).toBe(true);
});
