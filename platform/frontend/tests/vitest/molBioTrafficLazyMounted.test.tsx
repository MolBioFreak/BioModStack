import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
const seen = vi.hoisted(() => ({ plotlyImports: 0, parserImports: 0, plotProps: {} as any, input: {} as any, header: {} as any }));
vi.mock('react-plotly.js', () => { seen.plotlyImports++; return { default: (props: any) => { seen.plotProps = props; return <div data-plot />; } }; });
vi.mock('@teselagen/bio-parsers', async original => { seen.parserImports++; return await original(); });
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: () => <div data-viewer /> }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/SequenceHeader', () => ({ SequenceHeader: (props: any) => { seen.header = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { seen.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/AutoAnnotatePanel', () => ({ AutoAnnotatePanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])));
vi.mock('../../src/components/MolBioToolkit/RnaStructureViewer', () => ({ RnaStructureViewer: () => null }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: async () => [] }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: (p: string) => p }) }));
vi.mock('../../src/lib/restrictionAnalysis', async original => ({ ...await original<any>(), fetchRestrictionCatalogBrowse: async () => ({ catalog: { catalog_id: 'fixture' }, items: [] }), fetchRestrictionAnalysisBatch: async () => ({}) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<any>(), fetchNucleotideSequences: async () => ({ data: [] }), createNucleotideSequence: async (data: any) => ({ data: { ...data, id: 'imported', length: data.sequence.length, version: 1 } }), fetchPrimerTmOptions: async () => ({ data: { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} } }) }));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';
import { api } from '../../src/lib/api';
import { ExportDropdown } from '../../src/components/MolBioToolkit/ExportDropdown';

it('real toolkit defers chart/parser modules, retains GC science and real GenBank/FASTA actions', async () => {
    localStorage.clear(); window.innerWidth = 1400;
    const unexpectedRequests: string[] = [];
    const importRequests: unknown[] = [];
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const request = `${config.method?.toUpperCase()} ${config.url}`;
        if (request === 'POST /api/molbio/sequences/import/commit') {
            importRequests.push(JSON.parse(config.data));
            // This case exercises the real browser parser fallback, not server import.
            throw new Error('Import service unavailable in browser-parser fallback fixture');
        }
        unexpectedRequests.push(request);
        throw new Error(`Unexpected Axios request: ${request}`);
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/molbio/sequences/imported/ngs-workup?limit=50&offset=0') {
            return { ok: true, json: async () => ({ sequence_id: 'imported', workups: [], has_more: false }) };
        }
        unexpectedRequests.push(`fetch ${url}`);
        throw new Error(`Unexpected fetch: ${url}`);
    }));
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = async (node: React.ReactNode) => { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)); };
    const blobs: Blob[] = [];
    const createUrl = vi.fn((blob: Blob) => { blobs.push(blob); return 'blob:fixture'; });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const click = async (label: string) => { const b = [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(b).toBeDefined(); await act(async () => b!.click()); };
    const text = (blob: Blob) => new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); });
    try {
        await render(<MolBioToolkitV2 />);
        await act(async () => seen.input.onCreateSequence({ name: 'GC fixture', sequence: 'ACGT'.repeat(30), sequenceType: 'dna', circular: false }));
        expect(host.querySelector('[data-viewer]')).not.toBeNull();
        expect(seen.plotlyImports).toBe(0); expect(seen.parserImports).toBe(0);
        await act(async () => seen.header.onGCTrackToggle());
        await act(async () => { await vi.waitFor(() => expect(seen.plotlyImports).toBe(1)); });
        expect(host.querySelector('[data-plot]')).not.toBeNull();
        expect(seen.plotlyImports).toBe(1);
        expect(seen.plotProps.data.some((trace: any) => trace.y?.some((v: number) => v === 50))).toBe(true);
        await act(async () => seen.header.onGCTrackToggle()); await act(async () => seen.header.onGCTrackToggle());
        expect(seen.plotlyImports).toBe(1); expect(seen.parserImports).toBe(0);
        const importedFile = new File(['>Imported fixture\nACGTACGTACGT\n'], 'fixture.fasta', { type: 'text/plain' });
        // jsdom's File needs real text decoding instead of setup's String(blob) fallback.
        Object.defineProperty(importedFile, 'text', { value: () => text(importedFile) });
        await act(async () => seen.input.onImportFile(importedFile));
        expect(seen.parserImports).toBe(1); expect(seen.header.sequenceData.sequence).toBe('ACGTACGTACGT');
        await render(<ExportDropdown sequenceData={{ name: 'Export fixture', sequence: 'ACGTACGTACGT', circular: false, sequenceType: 'dna', features: [{ name: 'kept', type: 'misc_feature', start: 0, end: 4, strand: 1 }] }} />);
        await click('Export'); await click('GenBank (.gb)');
        expect(seen.parserImports).toBe(1); expect(blobs).toHaveLength(1);
        const gb = await text(blobs[0]); expect(gb).toContain('LOCUS'); expect(gb).toContain('kept'); expect(gb.toLowerCase()).toContain('acgtacgtac');
        await click('Export'); await click('FASTA (.fasta)'); expect(blobs).toHaveLength(2); expect(await text(blobs[1])).toContain('>Export fixture\nACGTACGTACGT');
        expect(importRequests).toHaveLength(1);
        expect(importRequests[0]).toMatchObject({ source_format: 'fasta', source_text: '>Imported fixture\nACGTACGTACGT\n' });
        expect(unexpectedRequests).toEqual([]);
    } finally {
        await act(async () => root.unmount()); client.clear(); host.remove(); anchorClick.mockRestore(); vi.unstubAllGlobals(); api.defaults.adapter = originalAdapter;
    }
});
