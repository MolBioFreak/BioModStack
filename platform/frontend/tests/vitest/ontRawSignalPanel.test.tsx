import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ont = vi.hoisted(() => ({
    sources: vi.fn(), reads: vi.fn(), opened: vi.fn(), requestWaveform: vi.fn(), fetchWaveform: vi.fn(), restoreStatus: vi.fn(),
    restore: vi.fn(), convert: vi.fn(), candidates: vi.fn(), retention: vi.fn(), saveRetention: vi.fn(), uploadFile: vi.fn(),
    importUpload: vi.fn(), registerCandidate: vi.fn(), handoff: vi.fn(),
    bamCandidates: vi.fn(), registerBam: vi.fn(), references: vi.fn(), domainState: vi.fn(), align: vi.fn(), job: vi.fn(),
}));

vi.mock('../../src/lib/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../src/lib/api')>();
    return {
        ...actual,
        fetchOntRawSignalSources: ont.sources, fetchOntRawSignalReads: ont.reads, fetchOntRawSignalOpenedReads: ont.opened,
        requestOntRawSignalWaveform: ont.requestWaveform, fetchOntRawSignalWaveform: ont.fetchWaveform,
        fetchOntPod5Restore: ont.restoreStatus, requestOntPod5Restore: ont.restore, requestOntBlow5Preparation: ont.convert,
        fetchOntExternalPod5Candidates: ont.candidates, fetchOntRawSignalRetention: ont.retention, saveOntRawSignalRetention: ont.saveRetention,
        uploadFile: ont.uploadFile, importOntRawSignalUpload: ont.importUpload, registerOntExternalPod5Candidate: ont.registerCandidate,
        requestOntBarcodeBasecallHandoff: ont.handoff,
        fetchOntExternalMoveBamCandidates: ont.bamCandidates, registerOntExternalMoveBamCandidate: ont.registerBam,
        fetchMolBioNgsReferences: ont.references, fetchMolBioNgsDomainState: ont.domainState,
        createOntExternalAlignmentJob: ont.align, fetchJobById: ont.job,
    };
});

vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        selectedDomainExperiment: { domain_experiment_id: 'project-1' },
        contextHref: (href: string, updates: Record<string, string | null> = {}) =>
            `${href}?${Object.entries(updates).filter(([, value]) => value).map(([key, value]) => `${key}=${value}`).join('&')}`,
    }),
}));

import { OntRawSignalPanel } from '../../src/components/ngs/OntRawSignalPanel';

const imported = {
    run_id: 'run-imported', observed_generation: 1, origin: 'imported', name: 'lib_A.pod5', status: 'completed', created_at: '2026-08-21T16:16:06',
    source: { representation_id: 'src-a', format: 'pod5', bytes: 3_627_362_144, removed: false },
    blow5: { representation_id: 'b5-a', read_count: 60784, readable: true }, conversion: { job_id: 'job-a', state: 'ready', reason_code: 'indexed_blow5_ready' },
    basecalls: null,
    results: [{ job_id: 'result-1', name: 'lib A basecall', created_at: '2026-08-29T00:00:00', signal_workbench: true }],
};
const sequenced = {
    run_id: 'ont-run-1', observed_generation: 2, origin: 'sequenced', name: 'ont-run-1', status: 'completed', created_at: '2026-11-22T10:00:00',
    source: { representation_id: 'src-s', format: 'pod5', bytes: 900_000_000, removed: false }, blow5: null, conversion: null, basecalls: null, results: [],
};

let container: HTMLDivElement;
let root: Root;
let analyze: ReturnType<typeof vi.fn>;

async function flush() {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); await Promise.resolve(); });
}
async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 12; attempt += 1) {
        try { assertion(); return; } catch { await flush(); }
    }
    assertion();
}
async function mount(search = '') {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(
        <MemoryRouter initialEntries={[`/ngs${search}`]}>
            <QueryClientProvider client={client}><OntRawSignalPanel onAnalyzeExistingData={analyze} /></QueryClientProvider>
        </MemoryRouter>,
    ));
    await flush();
}
const button = (text: string) => [...container.querySelectorAll('button')].find((item) => item.textContent?.trim().startsWith(text)) as HTMLButtonElement;
const detail = () => container.querySelector('[data-testid="ont-raw-signal-detail"]')!;

describe('Raw signal tab', () => {
    beforeEach(() => {
        Object.values(ont).forEach((mock) => mock.mockReset());
        ont.sources.mockResolvedValue({ sources: [sequenced, imported] });
        ont.reads.mockResolvedValue({ representation_id: 'b5-a', total: 60784, matched: 60784, offset: 0, read_ids: ['0001-read', '0002-read'] });
        ont.opened.mockResolvedValue({ lookups: [
            { lookup_id: 'l1', representation_id: 'b5-a', read_id: 'b0aa-read', state: 'ready', sample_count: 84356 },
            { lookup_id: 'l2', representation_id: 'b5-a', read_id: 'bad-read', state: 'failed', sample_count: null },
        ] });
        ont.restoreStatus.mockResolvedValue({ representation_id: 'b5-a', state: 'not_requested' });
        ont.retention.mockResolvedValue({ policy: 'pod5_and_blow5', remove_pod5_after_verified_blow5: false, installation_default: 'pod5_and_blow5' });
        ont.candidates.mockResolvedValue({ candidates: [{ candidate_id: 'cand-1', display_name: 'run.pod5', size_bytes: 2e9, modified_at_ns: 1 }] });
        ont.bamCandidates.mockResolvedValue({ items: [{ candidate_id: 'bam-1', display_name: 'lib_A.bam', size_bytes: 6e8, modified_at_ns: 1 }] });
        ont.references.mockResolvedValue([{ id: 'ref-1', name: 'eGFP plasmid', current_revision_id: 'ref-rev-1', archived_at: null }]);
        ont.domainState.mockResolvedValue({ current_state_revision_id: 'state-9' });
        analyze = vi.fn();
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
    });
    afterEach(() => { act(() => root.unmount()); container.remove(); });

    it('lists runs sequenced here apart from imported files, by name, with their signal state', async () => {
        await mount();
        await waitUntil(() => expect(container.textContent).toContain('lib_A.pod5'));
        expect(ont.sources).toHaveBeenCalledWith('project-1');
        const headings = [...container.querySelectorAll('h3')].map((item) => item.textContent);
        expect(headings.slice(0, 2)).toEqual(['Sequenced on this system', 'Imported files']);
        const rows = [...container.querySelectorAll('button[aria-pressed]')].filter((item) => item.textContent?.includes('POD5'));
        expect(rows.map((item) => item.textContent)).toEqual([
            expect.stringContaining('ont-run-1Not convertedSequenced 22 Nov 2026 · POD5 900.0 MB'),
            expect.stringContaining('lib_A.pod5BLOW5 readyImported 21 Aug 2026 · POD5 3.63 GB'),
        ]);
        expect(container.textContent).not.toContain('pod5_identity_validation_required');
    });

    it('offers the lossless conversion for a file without BLOW5 and no read list until it is done', async () => {
        ont.convert.mockResolvedValue({ job_id: 'job-s', state: 'requested', reason_code: 'conversion_requested', profile_id: 'p' });
        await mount();
        await waitUntil(() => expect(detail().textContent).toContain('ont-run-1'));
        expect(detail().textContent).toContain('once the file is converted to indexed BLOW5');
        expect(detail().querySelector('[aria-label="Reads in this file"]')).toBeNull();
        expect(ont.reads).not.toHaveBeenCalled();
        expect(button('Basecall and demultiplex this run')).toBeTruthy();
        await act(async () => button('Convert to indexed BLOW5').click());
        await waitUntil(() => expect(ont.convert).toHaveBeenCalledWith('ont-run-1', 2, 'src-s'));
    });

    it('browses the reads of a converted file and draws the one that is picked', async () => {
        ont.requestWaveform.mockResolvedValue({ lookup_id: 'l9', run_id: 'run-imported', observed_generation: 1, representation_id: 'b5-a',
            read_id: '0002-read', state: 'ready', reason_code: 'ok', sample_count: 9000, samples: [80, 95, 60, 120, 90] });
        await mount('?run_id=run-imported');
        await waitUntil(() => expect(detail().querySelector('[aria-label="Reads in this file"]')?.textContent).toContain('0002-read'));
        expect(detail().textContent).toContain('ready, 60,784 reads');
        expect(detail().textContent).toContain('1–2 of 60,784');
        // Only reads that opened successfully are offered again.
        expect(detail().querySelector('[aria-label="Reads opened before"]')!.textContent).toBe('b0aa-read84,356');
        expect(detail().textContent).toContain('Pick a read to see its signal.');
        await act(async () => button('0002-read').click());
        await waitUntil(() => expect(detail().querySelector('svg[aria-label="Raw electrical signal waveform"] polyline')).toBeTruthy());
        expect(ont.requestWaveform).toHaveBeenCalledWith('run-imported', 1, 'b5-a', '0002-read');
        expect(detail().textContent).toContain('9,000 samples · 5 drawn');
        expect(detail().querySelector('svg')!.textContent).toContain('120 pA');
        const find = detail().querySelector<HTMLInputElement>('[aria-label="Find a read"]')!;
        const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        await act(async () => { setValue.call(find, 'b0 aa'); find.dispatchEvent(new Event('input', { bubbles: true })); });
        await waitUntil(() => expect(ont.reads).toHaveBeenLastCalledWith('run-imported', 1, 'b5-a', { offset: 0, limit: 50, query: 'b0aa' }));
    });

    it('links a file to its results and their squiggle views, and hands restored POD5 to basecalling', async () => {
        ont.restoreStatus.mockResolvedValue({ representation_id: 'b5-a', state: 'ready', directory: '/data/restored', restored_read_count: 60784 });
        await mount('?run_id=run-imported');
        await waitUntil(() => expect(detail().querySelector('[aria-label="Results from this signal"]')).toBeTruthy());
        const links = [...detail().querySelectorAll('[aria-label="Results from this signal"] a')].map((item) => [item.textContent, item.getAttribute('href')]);
        expect(links).toEqual([
            ['Squiggle views', '/ngs?section=analyses&job_id=result-1&view=workbench'],
            ['Open result', '/ngs?section=analyses&job_id=result-1'],
        ]);
        await waitUntil(() => expect(button('Basecall restored POD5')).toBeTruthy());
        await act(async () => button('Basecall restored POD5').click());
        expect(analyze).toHaveBeenCalledWith({ selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/data/restored' });
        expect(button('Convert to indexed BLOW5')).toBeUndefined();
    });

    it('adds an uploaded BLOW5 pair or a POD5 already on this computer from one dialog', async () => {
        ont.uploadFile.mockImplementation(async (directory: string, file: File) => ({ data: { path: `${directory}/${file.name}` } }));
        ont.importUpload.mockResolvedValue({ run_id: 'run-new', observed_generation: 1, already_registered: false });
        ont.registerCandidate.mockResolvedValue({ run_id: 'run-existing', observed_generation: 1, already_registered: false });
        await mount();
        await waitUntil(() => expect(button('+ Add raw signal').disabled).toBe(false));
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        await act(async () => button('+ Add raw signal').click());
        const dialog = () => container.querySelector('[role="dialog"][aria-label="Add raw signal"]')!;
        expect(button('Import raw signal').disabled).toBe(true);
        const choose = async (label: string, file: File) => {
            const input = dialog().querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
            Object.defineProperty(input, 'files', { value: [file], configurable: true });
            await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
        };
        await choose('Raw-signal file', new File(['blow5'], 'reads.blow5'));
        expect(button('Import raw signal').disabled).toBe(true);
        await choose('BLOW5 index', new File(['idx'], 'reads.blow5.idx'));
        await act(async () => button('Import raw signal').click());
        await waitUntil(() => expect(ont.importUpload).toHaveBeenCalledTimes(1));
        const request = ont.importUpload.mock.calls[0][0];
        expect(request).toMatchObject({ format: 'blow5', experiment_group: 'project-1', sample_id: null });
        expect(request.file_path).toMatch(/^inputs\/ont-raw-signal\/[0-9a-f-]+\/source\.blow5$/);
        expect(request.index_file_path).toBe(`${request.file_path}.idx`);
        await waitUntil(() => expect(container.querySelector('[role="dialog"]')).toBeNull());

        await act(async () => button('+ Add raw signal').click());
        await act(async () => (dialog().querySelectorAll('[role="radio"]')[1] as HTMLButtonElement).click());
        await waitUntil(() => expect(dialog().querySelector('select[aria-label="Existing POD5 candidate"] option[value="cand-1"]')).toBeTruthy());
        const select = dialog().querySelector<HTMLSelectElement>('select[aria-label="Existing POD5 candidate"]')!;
        await act(async () => { select.value = 'cand-1'; select.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => button('Register selected POD5').click());
        await waitUntil(() => expect(ont.registerCandidate).toHaveBeenCalledWith('cand-1', 'project-1'));
    });

    it('shows saved traces but no read list for a file whose BLOW5 has moved', async () => {
        ont.sources.mockResolvedValue({ sources: [{ ...imported, blow5: { ...imported.blow5, readable: false } }] });
        ont.fetchWaveform.mockResolvedValue({ lookup_id: 'l1', run_id: 'run-imported', observed_generation: 1, representation_id: 'b5-a',
            read_id: 'b0aa-read', state: 'ready', reason_code: 'ok', sample_count: 84356, samples: [70, 90, 110] });
        await mount();
        await waitUntil(() => expect(detail().querySelector('[aria-label="Reads opened before"]')).toBeTruthy());
        expect(container.textContent).toContain('Re-import needed');
        expect(detail().querySelector('[role="status"]')!.textContent).toContain('add its POD5 once more');
        expect(ont.reads).not.toHaveBeenCalled();
        expect(detail().querySelector('[aria-label="Reads in this file"]')).toBeNull();
        await act(async () => button('b0aa-read').click());
        await waitUntil(() => expect(detail().querySelector('svg[aria-label="Raw electrical signal waveform"] polyline')).toBeTruthy());
        expect(ont.fetchWaveform).toHaveBeenCalledWith('l1');
        expect(ont.requestWaveform).not.toHaveBeenCalled();
    });

    it('adds move-tagged basecalls to a converted file, then aligns them to a Project reference', async () => {
        ont.registerBam.mockResolvedValue({ move_source_id: 'moves-1', state: 'requested' });
        await mount('?run_id=run-imported');
        await waitUntil(() => expect(container.querySelector('select[aria-label="Move-tagged BAM"] option[value="bam-1"]')).toBeTruthy());
        expect(container.querySelector('[data-testid="ont-raw-signal-basecalls"]')!.textContent).toContain('--emit-moves');
        const bam = container.querySelector<HTMLSelectElement>('select[aria-label="Move-tagged BAM"]')!;
        await act(async () => { bam.value = 'bam-1'; bam.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => button('Add basecalls').click());
        await waitUntil(() => expect(ont.registerBam).toHaveBeenCalledWith('run-imported', 1, { candidate_id: 'bam-1', raw_representation_id: 'b5-a', molecule_type: 'dna' }));

        await act(async () => root.unmount());
        root = createRoot(container);
        ont.sources.mockResolvedValue({ sources: [{ ...imported, basecalls: { move_source_id: 'moves-1', state: 'ready', reason_code: 'ok', basecall_model_id: 'sup@v4.3.0', read_count: 61708, external: true } }] });
        ont.align.mockResolvedValue({ job_id: 'align-1', status: 'queued' });
        ont.job.mockResolvedValue({ data: { id: 'align-1', status: 'completed' } });
        await mount('?run_id=run-imported');
        await waitUntil(() => expect(container.querySelector('[data-testid="ont-raw-signal-basecalls"]')!.textContent).toContain('Ready, 61,708 reads with move tables (sup@v4.3.0).'));
        // The Project's only reference is chosen already.
        await waitUntil(() => expect(container.querySelector<HTMLSelectElement>('select[aria-label="Reference for alignment"]')!.value).toBe('ref-rev-1'));
        await act(async () => button('Align to reference').click());
        await waitUntil(() => expect(ont.align).toHaveBeenCalledWith({
            move_source_id: 'moves-1', reference_revision_id: 'ref-rev-1', global_domain_experiment_id: 'project-1',
            molbio_ngs_state_revision_id: 'state-9', name: 'lib_A aligned to eGFP plasmid',
        }));
        await waitUntil(() => expect(container.textContent).toContain('Aligned. Open its squiggle views below.'));
    });

    it('says what to do in a Project that has no raw signal', async () => {
        ont.sources.mockResolvedValue({ sources: [] });
        await mount();
        await waitUntil(() => expect(container.textContent).toContain('This Project has no raw signal yet.'));
        expect(container.textContent).toContain('No runs sequenced here in this Project yet.');
        expect(container.querySelector('[data-testid="ont-raw-signal-detail"]')).toBeNull();
    });
});
