import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
    commit: vi.fn(), createSequence: vi.fn(), preview: vi.fn(), domain: 'domain-1', state: 'state-1', summaries: vi.fn(), reference: vi.fn(), sample: vi.fn(), stateDetail: vi.fn(),
    fullReferences: vi.fn(), fullHistory: vi.fn(), fullSamples: vi.fn(), control: vi.fn(),
}));
vi.mock('../../src/lib/api', () => ({
    fetchMolBioNgsSummaries: mock.summaries,
    fetchMolBioNgsReferenceRevision: mock.reference,
    fetchMolBioNgsSampleRevision: mock.sample,
    fetchMolBioNgsStateRevision: mock.stateDetail,
    fetchMolBioNgsReferences: mock.fullReferences,
    fetchMolBioNgsReferenceRevisions: mock.fullHistory,
    fetchMolBioNgsSamples: mock.fullSamples,
    fetchNucleotideSequences: vi.fn().mockResolvedValue({ data: [] }),
    fetchMolBioSequenceRevisions: vi.fn().mockResolvedValue({ data: [] }),
    fetchOntDeviceStatus: vi.fn().mockResolvedValue({ data: { live_devices: [] } }),
    fetchOntProtocolOptions: vi.fn(),
    fetchOntInstrumentRuns: vi.fn().mockResolvedValue([]),
    fetchOntExternalPod5Candidates: vi.fn().mockResolvedValue({ candidates: [] }),
    fetchOntInstrumentRunGeneration: vi.fn(), fetchOntRawSignalCapabilities: vi.fn(), fetchOntRawSignalWaveform: vi.fn(),
    requestOntBlow5Preparation: mock.control, requestOntRawSignalWaveform: mock.control,
    registerOntExternalPod5Candidate: mock.control, requestMk1dReconnect: mock.control,
    createOntRunIntent: mock.control, startOntRunIntent: mock.control,
    commitMolBioSequenceImport: mock.commit, createNucleotideSequence: mock.createSequence, createMolBioNgsReference: mock.control,
    fetchFiles: vi.fn(), importMolBioNgsBrowserReference: mock.control,
    issueMolBioNgsReceipt: mock.control, previewMolBioSequenceImport: mock.preview,
    submitOntNgsJob: mock.control, submitPooledReferenceAssignment: mock.control,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({ workspaceId: 'workspace-1', globalExperimentId: 'global-1',
        selectedDomainExperiment: { domain_experiment_id: mock.domain }, stateRevisionId: mock.state,
        availability: { canMutateDomain: true, reason: '' }, contextHref: (path: string, params: Record<string, string> = {}) => `${path}?${new URLSearchParams(Object.entries(params).filter(([, value]) => Boolean(value))).toString()}` }),
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [] }) }));
import { NanoporeTemplate } from '../../src/components/NanoporeTemplate';
import { OntInstrumentPanel } from '../../src/components/ngs/OntInstrumentPanel';
let root: Root, container: HTMLDivElement, client: QueryClient;
const referenceRow = (id: string) => ({ id, reference_id: `resource-${id}`, name: `Name ${id}`, revision_number: 1, canonical_fasta_sha256: 'a'.repeat(64), created_at: '2026-01-01' });
const member = (id: string, domain = mock.domain) => ({ role: 'ngs_reference', entity_kind: 'ngs_reference_revision', entity_id: id,
    reopen_destination: { surface: 'molbio-ngs-reference-revision', params: { global_domain_experiment_id: domain, reference_id: `resource-${id}`, revision_id: id } } });
async function flush() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); }
async function until(assertion: () => void) { for (let i = 0; i < 30; i++) { try { assertion(); return; } catch { await flush(); } } assertion(); }
const button = (text: string) => Array.from(container.querySelectorAll('button')).find((item) => item.textContent === text)!;
const referenceSelect = () => Array.from(container.querySelectorAll('select')).find((item) => item.textContent?.includes('Select immutable managed revision'))!;
async function mount(kind: 'reference' | 'sample', initialValues: Record<string, unknown> = {}, url = '/') {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}>
        {kind === 'reference' ? <NanoporeTemplate onBack={() => undefined} initialValues={{ selectedWorkflow: 'constructScreening', inputSource: 'fastq', jobName: 'test', fastqPath: '/fixture.fastq', ngsReferenceRevisionId: 'r1', ...initialValues }} /> : <OntInstrumentPanel onAnalyzeExistingData={() => undefined} />}
    </MemoryRouter></QueryClientProvider>));
    await flush(); await flush();
}
beforeEach(() => {
    vi.clearAllMocks(); mock.commit.mockReset(); mock.createSequence.mockReset(); mock.preview.mockReset(); mock.domain = 'domain-1'; mock.state = 'state-1';
    mock.summaries.mockReset(); mock.reference.mockReset(); mock.sample.mockReset(); mock.stateDetail.mockReset();
    mock.stateDetail.mockResolvedValue({ members: [] });
    mock.reference.mockImplementation(async (resourceId, revisionId) => ({ ...referenceRow(revisionId), reference_id: resourceId, global_domain_experiment_id: mock.domain }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Live transport forbidden in Lane I tests')));
});
afterEach(async () => {
    expect(mock.fullReferences).not.toHaveBeenCalled(); expect(mock.fullHistory).not.toHaveBeenCalled(); expect(mock.fullSamples).not.toHaveBeenCalled();
    expect(mock.control).not.toHaveBeenCalled();
    await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/');
});
it('loads 50 reference summaries once, explicitly continues, and never fans out native history reads', async () => {
    mock.summaries.mockImplementation(async (_domain, collection, params) => {
        expect(collection).toBe('reference'); expect(params.limit).toBe(50);
        return params.cursor ? { items: [referenceRow('r51')], next_cursor: null, total: 51 }
            : { items: Array.from({ length: 50 }, (_, i) => referenceRow(`r${i + 1}`)), next_cursor: 'r50', total: 51 };
    });
    await mount('reference');
    await until(() => expect(container.textContent).toContain('Loaded 50 of 51'));
    expect(mock.summaries).toHaveBeenCalledTimes(1); expect(mock.reference).not.toHaveBeenCalled();
    await act(async () => button('Load more reference revisions').click());
    await until(() => expect(container.textContent).toContain('Loaded 51 of 51'));
    await act(async () => { referenceSelect().value = 'r51'; referenceSelect().dispatchEvent(new Event('change', { bubbles: true })); });
    expect(referenceSelect().value).toBe('r51'); expect(mock.summaries).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('not a member of the exact selected local state');
    expect(mock.reference).not.toHaveBeenCalled();
});
it('restores a saved state-member revision outside page one using only its exact receipt-bound GET, even if continuation fails', async () => {
    mock.stateDetail.mockResolvedValue({ members: [member('historical')] });
    mock.summaries.mockImplementation(async (_domain, _collection, params) => {
        if (params.cursor) throw new Error('page unavailable');
        return { items: [referenceRow('current')], next_cursor: 'current', total: 51 };
    });
    await mount('reference', { ngsReferenceRevisionId: 'historical' });
    await until(() => expect(referenceSelect().value).toBe('historical'));
    await until(() => expect(container.textContent).toContain('resource-historical'));
    expect(mock.reference).toHaveBeenCalledWith('resource-historical', 'historical'); expect(mock.reference).toHaveBeenCalledTimes(1);
    await act(async () => button('Load more reference revisions').click());
    await until(() => expect(container.textContent).toContain('Reference summary page could not be loaded'));
    expect(referenceSelect().value).toBe('historical');
    expect(button('Review and submit').disabled).toBe(false);
});
it('auto-selects exact state membership beyond the first page without scanning to it', async () => {
    mock.summaries.mockResolvedValue({ items: [referenceRow('current')], next_cursor: 'current', total: 999 });
    mock.stateDetail.mockResolvedValue({ members: [member('old')] });
    await mount('reference', { ngsReferenceRevisionId: 'old' });
    await act(async () => { referenceSelect().value = ''; referenceSelect().dispatchEvent(new Event('change', { bubbles: true })); });
    await until(() => expect(referenceSelect().value).toBe('old'));
    await until(() => expect(mock.reference).toHaveBeenCalledWith('resource-old', 'old'));
    expect(mock.summaries).toHaveBeenCalledTimes(1);
});
it('isolates late reference detail from a switched Domain context', async () => {
    let resolveOld!: (value: unknown) => void;
    mock.summaries.mockImplementation(async (domain) => ({ items: [referenceRow(`${domain}-current`)], next_cursor: null, total: 1 }));
    mock.stateDetail.mockImplementation(async (domain) => ({ members: domain === 'domain-1' ? [member('old', domain)] : [] }));
    mock.reference.mockImplementation(() => new Promise((resolve) => { resolveOld = resolve; }));
    await mount('reference', { ngsReferenceRevisionId: 'old' });
    await until(() => expect(mock.reference).toHaveBeenCalledTimes(1));
    mock.domain = 'domain-2'; mock.state = 'state-2'; await mount('reference');
    await act(async () => resolveOld({ ...referenceRow('old'), global_domain_experiment_id: 'domain-1' }));
    await flush(); expect(referenceSelect().value).toBe(''); expect(container.textContent).not.toContain('resource-old');
});
it('defers all managed-reference collection/detail reads in the MolBio receipt lane', async () => {
    window.history.replaceState({}, '', '/?molbio_sequence_id=molecular');
    await mount('reference'); await flush();
    expect(mock.summaries).not.toHaveBeenCalled(); expect(mock.reference).not.toHaveBeenCalled();
});
it('loads sample summaries on demand and preserves URL-pinned exact sample/reference reads independently', async () => {
    mock.summaries.mockImplementation(async (_domain, collection, params) => {
        expect(collection).toBe('samples'); expect(params.limit).toBe(50);
        return params.cursor ? { items: [{ id: 'sample-51', current_revision_id: 'head-51' }], next_cursor: null, total: 51 }
            : { items: Array.from({ length: 50 }, (_, i) => ({ id: `sample-${i}`, current_revision_id: `head-${i}` })), next_cursor: 'sample-49', total: 51 };
    });
    mock.sample.mockResolvedValue({ id: 'pinned-old', sample_id: 'pinned', global_domain_experiment_id: 'domain-1', payload: { name: 'Pinned sample', sample_kind: 'dna' } });
    await mount('sample', {}, '/?sample_id=pinned&sample_revision_id=pinned-old&reference_id=resource-old&reference_revision_id=old');
    await until(() => expect(container.textContent).toContain('Loaded 50 of 51'));
    expect(mock.sample).toHaveBeenCalledWith('domain-1', 'pinned', 'pinned-old'); expect(mock.reference).toHaveBeenCalledWith('resource-old', 'old');
    expect(mock.summaries).toHaveBeenCalledTimes(1);
    await act(async () => button('Load more samples').click());
    await until(() => expect(container.textContent).toContain('Loaded 51 of 51'));
    const select = Array.from(container.querySelectorAll('select')).find((item) => item.querySelector('option[value="sample-51"]'))!;
    await act(async () => { select.value = 'sample-51'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(select.value).toBe('sample-51'); expect(mock.sample).toHaveBeenCalledTimes(1); expect(mock.reference).toHaveBeenCalledTimes(1);
});

async function inputValue(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
    await act(async () => {
        const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
        element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
}
const committedRows = [
    { sequence_id: 'shared-a', revision_id: 'revision-a', name: 'A' },
    { sequence_id: 'shared-b', revision_id: 'revision-b', name: 'B' },
];
async function importSetup() {
    mock.summaries.mockResolvedValue({ items: [referenceRow('r1')], next_cursor: null, total: 1 });
    mock.commit.mockResolvedValue({ data: { records: committedRows } });
    await mount('reference');
}
it('migrates historical FASTA authoring to shared multi-record import without changing historical selection or autoattachment', async () => {
    await importSetup();
    await act(async () => button('Import FASTA into shared MolBio').click());
    const fasta = '>A\nACGT\n>B\nTGCA\n';
    await inputValue(container.querySelector('textarea[placeholder^=">my_reference"]')!, fasta);
    await inputValue(container.querySelector('[aria-label="Shared reference topology"]')!, 'linear');
    await act(async () => button('Save to shared MolBio').click());
    await until(() => expect(container.textContent).toContain('Saved all 2 record(s)'));
    expect(mock.commit).toHaveBeenCalledWith(expect.objectContaining({ origin_surface: 'ngs', source_format: 'fasta', source_text: fasta, topology_default: 'linear', idempotency_key: expect.any(String) }));
    const links = Array.from(container.querySelectorAll('[data-testid="molbio-import-committed-records"] a'));
    expect(links).toHaveLength(2); expect(links[1].getAttribute('href')).toContain('molbio_revision_id=revision-b');
    await act(async () => button('Historical selection').click()); expect(referenceSelect().value).toBe('r1');
    expect(mock.createSequence).not.toHaveBeenCalled();
});
it('preserves RNA records and circular topology through the existing typed shared RNA writer', async () => {
    await importSetup();
    mock.createSequence.mockImplementation(async (payload) => ({ data: { id: `rna-${payload.name}`, name: payload.name } }));
    await act(async () => button('Import FASTA into shared MolBio').click());
    await inputValue(container.querySelector('[aria-label="Shared reference molecule type"]')!, 'rna');
    await inputValue(container.querySelector('textarea[placeholder^=">my_reference"]')!, '>A\nACGU\n>B\nUGCA\n');
    await act(async () => button('Save to shared MolBio').click());
    await until(() => expect(mock.createSequence).toHaveBeenCalledTimes(2));
    expect(mock.createSequence).toHaveBeenNthCalledWith(1, { name: 'A', sequence: 'ACGU\n', sequence_type: 'rna', is_circular: true });
    expect(mock.createSequence).toHaveBeenNthCalledWith(2, { name: 'B', sequence: 'UGCA', sequence_type: 'rna', is_circular: true });
    await until(() => expect(container.querySelectorAll('[data-testid="molbio-import-committed-records"] a')).toHaveLength(2));
    expect(mock.commit).not.toHaveBeenCalled();
});
it('rejects an empty RNA FASTA record before any shared write instead of silently dropping it', async () => {
    await importSetup();
    await act(async () => button('Import FASTA into shared MolBio').click());
    await inputValue(container.querySelector('[aria-label="Shared reference molecule type"]')!, 'rna');
    await inputValue(container.querySelector('textarea[placeholder^=">my_reference"]')!, '>A\nACGU\n>');
    await act(async () => button('Save to shared MolBio').click());
    await until(() => expect(container.textContent).toContain('Every RNA FASTA record requires a name and sequence'));
    expect(mock.createSequence).not.toHaveBeenCalled(); expect(mock.commit).not.toHaveBeenCalled();
});
it('keeps partial RNA save receipts visible and reports the precise remaining record after a failed shared writer', async () => {
    await importSetup();
    mock.createSequence.mockResolvedValueOnce({ data: { id: 'rna-a', name: 'A' } }).mockRejectedValueOnce(new Error('record rejected'));
    await act(async () => button('Import FASTA into shared MolBio').click());
    await inputValue(container.querySelector('[aria-label="Shared reference molecule type"]')!, 'rna');
    await inputValue(container.querySelector('textarea[placeholder^=">my_reference"]')!, '>A\nACGU\n>B\nINVALID\n');
    await act(async () => button('Save to shared MolBio').click());
    await until(() => expect(container.textContent).toContain('RNA import stopped at B'));
    expect(container.textContent).toContain('1 earlier record(s) remain saved');
    expect(container.querySelectorAll('[data-testid="molbio-import-committed-records"] a')).toHaveLength(1);
    expect(mock.commit).not.toHaveBeenCalled();
});
it('migrates every browser FASTA record without rewriting stored browser history', async () => {
    await importSetup();
    const stored = JSON.stringify([{ id: 'hint', name: 'Two records', source: 'fasta', fasta: '>A\nACGT\n>B\nTGCA\n' }]);
    localStorage.setItem('bms.nanopore.referenceLibrary.v1', stored);
    await act(async () => button('Import browser hint into shared MolBio').click());
    await act(async () => button('Read legacy browser hints').click());
    await act(async () => button('Save browser hint to shared MolBio').click());
    await until(() => expect(mock.commit).toHaveBeenCalledTimes(1));
    expect(mock.commit.mock.calls[0][0].source_text).toContain('>B\nTGCA');
    expect(localStorage.getItem('bms.nanopore.referenceLibrary.v1')).toBe(stored);
    await until(() => expect(container.querySelectorAll('[data-testid="molbio-import-committed-records"] a')).toHaveLength(2));
    localStorage.removeItem('bms.nanopore.referenceLibrary.v1');
});
it('the visible shared preview/commit panel retains all returned exact revisions and sends NGS provenance', async () => {
    await importSetup();
    mock.preview.mockResolvedValue({ data: { valid: true, errors: [], records: [{ record_ordinal: 0, name: 'A', topology: 'linear' }, { record_ordinal: 1, name: 'B', topology: 'linear' }] } });
    const panel = container.querySelector('[data-testid="molbio-sequence-import-panel"]')!;
    await inputValue(panel.querySelector('textarea')!, '>A\nACGT\n>B\nTGCA\n');
    await inputValue(panel.querySelector('[aria-label="Import topology default"]')!, 'linear');
    await act(async () => button('Preview import').click());
    await until(() => expect(button('Commit preview').disabled).toBe(false));
    await act(async () => button('Commit preview').click());
    await until(() => expect(panel.querySelectorAll('[data-testid="molbio-import-committed-records"] a')).toHaveLength(2));
    expect(mock.commit).toHaveBeenCalledWith(expect.objectContaining({ origin_surface: 'ngs', topology_default: 'linear' }));
    expect(referenceSelect().value).toBe('r1');
});
