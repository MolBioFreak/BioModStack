import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ context: {} as Record<string, unknown>, header: {} as any, input: {} as any, viewer: {} as any, digest: {} as any, visibility: {} as any, recent: vi.fn(), hub: vi.fn(), get: vi.fn(), create: vi.fn(), demos: vi.fn() }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: (props: any) => { mocks.viewer = props; return <div data-viewer />; } }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: (props: any) => { mocks.visibility = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({ SequenceHeader: (props: any) => { mocks.header = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { mocks.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({ AutoAnnotatePanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/panels', () => ({
    ...Object.fromEntries(['AlignmentPanel','AssemblyPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])),
    DigestPanel: (props: any) => { mocks.digest = props; return null; },
}));
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({ RnaStructureViewer: () => null }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: mocks.demos }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: (p: string) => p, ...mocks.context }) }));
vi.mock('../../src/lib/restrictionAnalysis', async original => ({
    ...await original<any>(),
    fetchRestrictionCatalogBrowse: async () => ({ catalog: { catalog_id: 'fixture' }, items: [] }),
    fetchRestrictionAnalysisBatch: async () => ({ analysis: { enzyme_summaries: [], occurrences: [] } }),
}));
vi.mock('../../src/lib/api', async original => ({ ...await original<any>(),
    fetchNucleotideSequences: mocks.recent, fetchProjectHub: mocks.hub, fetchNucleotideSequence: mocks.get, createNucleotideSequence: mocks.create,
    fetchPrimerTmOptions: async () => ({ data: { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} } }),
}));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
import { DEFAULT_MAPPED_ENZYMES } from '../../src/components/MolBioToolkit/utils/restrictionLabels';
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
async function clickButton(name: string, within: ParentNode = host) {
    const button = [...within.querySelectorAll('button')].find(element => element.textContent?.trim() === name);
    expect(button).toBeDefined();
    await act(async () => button!.click());
}
beforeEach(() => {
    localStorage.clear(); window.innerWidth = 1400;
    mocks.recent.mockReset().mockResolvedValue({ data: [item('orphan-sequence')] });
    mocks.hub.mockReset().mockImplementation(async (id: string) => project(`sequence-${id}`));
    mocks.get.mockReset().mockImplementation(async (id: string) => ({ data: { ...item(id), sequence: 'ACGT', features: [], primers: [], analysis_tracks: [], version: 1 } }));
    mocks.create.mockReset();
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

it('keeps each saved sequence’s restriction display across switching, closing, and reloading', async () => {
    async function selectSequence(id: string) {
        await act(async () => mocks.input.onSelectSequence(id));
        expect(mocks.viewer.sequenceData.name).toBe(id);
    }
    function expectDisplay(enzymes: string[], visible: boolean, grouped: boolean) {
        expect(mocks.viewer.selectedEnzymes).toEqual(enzymes);
        expect(mocks.viewer.visibility.cutsites).toBe(visible);
        expect(mocks.viewer.groupSameSiteLabels).toBe(grouped);
    }

    await mount(null);
    await selectSequence('plasmid-a');
    await clickButton('Digest');
    const firstPanel = mocks.digest;
    await act(async () => {
        firstPanel.onEnzymesChange([]);
        firstPanel.onMapVisibilityRequest();
        firstPanel.onGroupSameSiteLabelsChange(false);
    });
    expectDisplay([], true, false);

    await selectSequence('plasmid-b');
    expectDisplay(DEFAULT_MAPPED_ENZYMES, false, true);
    await act(async () => firstPanel.onEnzymesChange(['EcoRI']));
    expectDisplay(DEFAULT_MAPPED_ENZYMES, false, true);
    await act(async () => {
        mocks.digest.onEnzymesChange(['BamHI']);
        mocks.digest.onMapVisibilityRequest();
    });
    expectDisplay(['BamHI'], true, true);

    const firstTab = host.querySelector('[data-workspace-id="molecular_sequence_plasmid-a"]')!;
    await clickButton('plasmid-a', firstTab);
    expectDisplay([], true, false);
    await clickButton('View');
    await act(async () => mocks.visibility.onChange('cutsites'));
    expectDisplay([], false, false);
    await clickButton('×', firstTab);
    expectDisplay(['BamHI'], true, true);
    await selectSequence('plasmid-a');
    expectDisplay([], false, false);

    await act(async () => root.unmount());
    root = createRoot(host);
    await mount(null);
    expect(mocks.viewer.sequenceData.name).toBe('plasmid-a');
    expectDisplay([], false, false);
    await selectSequence('plasmid-b');
    expectDisplay(['BamHI'], true, true);
});

it('keeps the draft restriction display when the sequence is first saved', async () => {
    mocks.create.mockImplementation(async () => mocks.get('new-plasmid'));
    await mount(null);
    await act(async () => mocks.input.onCreateSequence({ name: 'new-plasmid', sequence: 'ACGT', sequenceType: 'dna', circular: false }));
    await clickButton('Digest');
    await act(async () => {
        mocks.digest.onEnzymesChange(['BamHI']);
        mocks.digest.onMapVisibilityRequest();
    });
    await act(async () => mocks.header.onSave());
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(mocks.viewer.selectedEnzymes).toEqual(['BamHI']);
    expect(mocks.viewer.visibility.cutsites).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(host);
    await mount(null);
    expect(mocks.viewer.sequenceData.name).toBe('new-plasmid');
    expect(mocks.viewer.selectedEnzymes).toEqual(['BamHI']);
    expect(mocks.viewer.visibility.cutsites).toBe(true);
});
