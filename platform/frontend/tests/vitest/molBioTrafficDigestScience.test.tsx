import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { api } from '../../src/lib/api';
import { parseRestrictionDigestSimulation } from '../../src/lib/restrictionAnalysis';
import { retainedDigestFragment, digestFragmentFromImport } from '../../src/lib/retainedDigestFragment';
import { digestFragmentsForAssembly } from '../../src/lib/digestAssemblyTransfer';
import { ExportDropdown } from '../../src/components/MolBioToolkit/ExportDropdown';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';

const shell = vi.hoisted(() => ({ input: {} as any, viewer: {} as any, parsed: vi.fn() }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: (props: any) => { shell.viewer = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { shell.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: () => null }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: async () => [] }));
vi.mock('@teselagen/bio-parsers', async original => ({ ...await original<any>(), anyToJson: shell.parsed }));
vi.mock('../../src/components/MolBioToolkit/panels', () => Object.fromEntries(['AlignmentPanel','AssemblyPanel','DigestPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }) }));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';

// Python-engine receipt. EcoRI assertions below are independently specified geometry.
const fixture = JSON.parse(readFileSync('../api/tests/fixtures/restriction_digest/circular_single_ecori.json', 'utf8'));
const fragment = fixture.fragments[0];
const provenance = { schema: 'bms.molbio.restriction-digest-fragment-provenance.v1', geometry: fragment };
const sequence = { name: 'retained EcoRI fragment', sequence: 'AATTCAATTG', sequenceType: 'dna' as const, circular: false, moleculeStrandedness: 'double' as const, features: [], primers: [], digestFragment: fragment };
const revision = { document_id: 'digest-document', sequence_id: 'digest-document', revision_id: 'digest-revision', revision_number: 1, change_kind: 'digest_fragment', relation: 'output', document_name: sequence.name, content_sha256: 'a'.repeat(64), created_at: '2026-01-01T00:00:00Z', snapshot: { name: sequence.name, sequence: sequence.sequence, sequence_type: 'dna', is_circular: false, molecule_strandedness: 'double', features: [], primers: [] }, provenance, reopen_destination: { params: { sequence_id: 'digest-document', revision_id: 'digest-revision' } } };
let root: Root, host: HTMLDivElement, client: QueryClient;
let downloaded: Blob[], requests: string[];
const adapter = api.defaults.adapter;
async function render(node: React.ReactNode, route = '/') { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}>{node}</MemoryRouter></QueryClientProvider>)); }
async function click(label: string) { const button = [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(button, label).toBeTruthy(); await act(async () => button!.click()); }
async function blobText(blob: Blob): Promise<string> { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); }); }
beforeEach(() => {
    downloaded = []; requests = []; shell.viewer = {}; shell.parsed.mockClear(); localStorage.clear(); window.innerWidth = 1400;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.stubGlobal('alert', vi.fn()); vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn((blob: Blob) => { downloaded.push(blob); return 'blob:test'; }) });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    api.defaults.adapter = async config => {
        requests.push(config.url ?? ''); let data: unknown = [];
        if (config.url?.endsWith('/primer-tm/options')) data = { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} };
        if (config.url === '/api/sequences/digest-document/revisions/digest-revision') data = revision;
        if (config.url?.endsWith('/ligation/simulate')) data = { product: { sequence: sequence.sequence, length: 10, circular: false, mode: 'ligation', fragments: [], validation_notes: [], warnings: [], junctions: [] } };
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('accepts actual circular EcoRI receipt, ordered coordinates and one cut identity', () => {
    const receipt = parseRestrictionDigestSimulation(fixture);
    expect(receipt.fragments).toHaveLength(1);
    expect(receipt.fragments[0]).toMatchObject({ top_strand_sequence: 'AATTCAATTG', source_segments: [[3, 10], [0, 3]], wraps_origin: true });
    expect(receipt.fragments[0].lineage_cleavage_group_ids).toHaveLength(1);
    const legacy = structuredClone(fixture); legacy.digest_algorithm_version = '1.0.0'; legacy.fragments[0].lineage_cleavage_group_ids.push(legacy.fragments[0].lineage_cleavage_group_ids[0]);
    expect(() => parseRestrictionDigestSimulation(legacy)).not.toThrow();
    legacy.digest_algorithm_version = '1.1.0'; expect(() => parseRestrictionDigestSimulation(legacy)).toThrow(/duplicate/);
});
it('retains opposing sticky strands in direct and whole-fragment assembly handoffs', async () => {
    const expected = { left_end: { type: 'sticky_5', overhang: 'AATT', protruding_strand: 'top' }, right_end: { type: 'sticky_5', overhang: 'AATT', protruding_strand: 'bottom' } };
    expect(digestFragmentsForAssembly(parseRestrictionDigestSimulation(fixture), [0], null, 'fixture')[0]).toMatchObject(expected);
    expect(retainedDigestFragment(provenance, sequence.sequence)).toEqual(fragment);
    expect(() => retainedDigestFragment(provenance, 'TTTT')).toThrow(/does not match/);
    const transport = vi.fn(api.defaults.adapter as any); api.defaults.adapter = transport;
    await render(<AssemblyPanel sequenceData={sequence} selection={null} selectedSequenceId={null} onLoadProduct={vi.fn()} onLoadSavedWorkup={vi.fn()} />);
    await click('Add whole construct'); await click('Simulate');
    const call = transport.mock.calls.find(([config]) => config.url.endsWith('/ligation/simulate'));
    expect(call).toBeDefined(); expect(JSON.parse(call![0].data).fragments[0]).toMatchObject({ ...expected, metadata: { digest_fragment: fragment } });
});
it.each(['FASTA (.fasta)', 'GenBank (.gb)'])('A7c sticky fragment export proceeds with ends noted, top strand only: %s', async label => {
    await render(<ExportDropdown sequenceData={sequence} />); await click('Export');
    expect(document.body.textContent).toContain('encode only the top strand'); await click(label);
    await vi.waitFor(() => expect(downloaded).toHaveLength(1)); expect(host.textContent).not.toContain('Could not export'); const text = await blobText(downloaded[0]);
    expect(text).toMatch(/5' overhang AATT/); expect(text).toMatch(/top strand/);
});
it('retained immutable revision hydrates end geometry and exports it intact through real shell', async () => {
    await render(<MolBioToolkitV2 />, '/designer?molbio_sequence_id=digest-document&molbio_revision_id=digest-revision');
    expect(requests).toContain('/api/sequences/digest-document/revisions/digest-revision');
    expect(shell.viewer.sequenceData.digestFragment).toEqual(fragment);
    // Derive from this revision: Assembly/Digest/PCR are enabled on an exact revision (editing stays disabled).
    const tab = (name: string) => [...host.querySelectorAll('.panel-tabs button')].find(b => b.textContent?.trim() === name) as HTMLButtonElement | undefined;
    expect(tab('Assembly')?.disabled).toBe(false); expect(tab('Digest')?.disabled).toBe(false); expect(tab('PCR')?.disabled).toBe(false); expect(tab('Edit')?.disabled).toBe(true);
    await click('Export'); await click('Toolkit JSON (.molbio.json)'); expect(downloaded).toHaveLength(1);
    const saved = JSON.parse(await blobText(downloaded[0]));
    expect(saved.digestFragment).toEqual(fragment); expect(saved.sequence).toBe('AATTCAATTG');
    expect(digestFragmentFromImport(saved, saved.sequence)).toEqual(fragment);
});
it('A7c Toolkit JSON of a sticky fragment re-imports with its ends intact', async () => {
    await render(<MolBioToolkitV2 />);
    const file = new File([JSON.stringify(sequence)], 'fragment.molbio.json'); Object.defineProperty(file, 'text', { value: async () => JSON.stringify(sequence) });
    await act(async () => shell.input.onImportFile(file));
    expect(shell.parsed).not.toHaveBeenCalled(); expect(alert).not.toHaveBeenCalled();
    expect(shell.viewer.sequenceData.digestFragment).toEqual(fragment); expect(shell.viewer.sequenceData.sequence).toBe('AATTCAATTG');
});
it.each(['dna', 'rna'] as const)('opening a known %s primer preserves its single-stranded substrate identity', async sequenceType => {
    await render(<MolBioToolkitV2 />);
    await act(async () => shell.input.onOpenPrimerAsConstruct({
        name: 'ordered oligo', sequence: 'ACGACGACGACGACGACGAC', sequenceType,
    }));
    expect(shell.viewer.sequenceData).toMatchObject({
        sequenceType, moleculeStrandedness: 'single', moleculeOrientation: 'unknown', circular: false,
    });
    expect(shell.viewer.sequenceData.moleculeLabel).not.toContain('dsDNA');
});
it('GenBank export no longer refuses unplaced primers; JSON still preserves review status', async () => {
    const data = { ...sequence, digestFragment: undefined, primers: [{ name: 'moved', sequence: 'AATT', start: 0, end: 0, strand: 1 as const, binding_status: 'needs_review' as const, binding_warning: 'Sequence changed' }] };
    await render(<ExportDropdown sequenceData={data} />); await click('Export'); await click('GenBank (.gb)');
    await vi.waitFor(() => expect(downloaded).toHaveLength(1)); expect(host.textContent).not.toContain('Could not export'); expect(await blobText(downloaded[0])).not.toMatch(/moved|primer_bind/);
    await click('Export'); await click('Toolkit JSON (.molbio.json)'); expect(JSON.parse(await blobText(downloaded[1])).primers[0].binding_status).toBe('needs_review');
});
