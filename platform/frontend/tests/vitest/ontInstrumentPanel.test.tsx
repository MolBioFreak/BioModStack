import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ont = vi.hoisted(() => ({
    fetchDevice: vi.fn(),
    fetchOptions: vi.fn(),
    createIntent: vi.fn(),
    startIntent: vi.fn(),
    uploadFile: vi.fn(),
    importRawSignal: vi.fn(),
    fetchRuns: vi.fn(),
    stopRun: vi.fn(),
    fetchLive: vi.fn(),
}));

const deviceResponse = {
    data: {
        implementation_status: 'configured',
        live_devices: [{
            position: 'MD-105428',
            device_type: 'mk1d',
            state: 'ready',
            running: false,
            available_for_run: true,
            flow_cell: { present: true },
            fake_or_demo_device: false,
        }],
        fake_or_demo_devices: false,
    },
};

const optionsResponse = {
    data: {
        position: 'MD-105428',
        can_start: true,
        blockers: [],
        flow_cell_present: true,
        available_kits: ['SQK-LSK114'],
        options: [{
            option_id: 'ont-option-safe',
            option_receipt_id: 'ont-preflight-safe',
            expires_at: '2026-08-01T00:00:00Z',
            protocol_label: 'Server-approved protocol',
            basecalling_enabled: true,
            output_policy_id: 'ont-output-policy-safe',
            output_policy_label: 'Server output policy',
        }],
        fake_or_demo_devices: false,
    },
};

vi.mock('../../src/lib/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../src/lib/api')>();
    return {
        ...actual,
        fetchOntDeviceStatus: ont.fetchDevice,
        fetchOntProtocolOptions: ont.fetchOptions,
        fetchMolBioNgsSummaries: vi.fn().mockResolvedValue({ items: [], next_cursor: null, total: 0 }),
        fetchOntInstrumentRuns: ont.fetchRuns,
        stopOntInstrumentRun: ont.stopRun,
        fetchOntInstrumentRunLive: ont.fetchLive,
        fetchOntInstrumentRunGeneration: vi.fn().mockResolvedValue(null),
        fetchOntRawSignalCapabilities: vi.fn().mockResolvedValue(null),
        fetchOntExternalPod5Candidates: vi.fn().mockResolvedValue({ candidates: [] }),
        createOntRunIntent: ont.createIntent,
        startOntRunIntent: ont.startIntent,
        uploadFile: ont.uploadFile,
        importOntRawSignalUpload: ont.importRawSignal,
    };
});

vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: 'workspace-test',
        globalExperimentId: 'global-experiment-test',
        selectedDomainExperiment: { domain_experiment_id: 'domain-experiment-test' },
        availability: { state: 'available', reason: null },
        contextHref: (href: string) => href,
    }),
}));

import { OntInstrumentPanel } from '../../src/components/ngs/OntInstrumentPanel';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

const intent = {
    id: 'ont-run-durable-001',
    position: 'MD-105428',
    status: 'armed',
    observed_generation: 1,
    handoff_ready: false,
    output_summary: { fastq: 0, pod5: 0, bam: 0 },
    events: [],
    fake_or_demo_devices: false as const,
};

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

function panel() {
    return (
        <MemoryRouter>
            <QueryClientProvider client={client}>
                <OntInstrumentPanel onAnalyzeExistingData={() => undefined} />
            </QueryClientProvider>
        </MemoryRouter>
    );
}

async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 10; attempt += 1) {
        try {
            assertion();
            return;
        } catch {
            await flush();
        }
    }
    assertion();
}

beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    ont.fetchDevice.mockReset();
    ont.fetchOptions.mockReset();
    ont.createIntent.mockReset();
    ont.startIntent.mockReset();
    ont.fetchRuns.mockReset().mockResolvedValue([]);
    ont.stopRun.mockReset();
    ont.fetchLive.mockReset().mockResolvedValue({ data: { run_id: 'bound-run', status: 'running', available: false } });
    ont.fetchDevice.mockResolvedValue(deviceResponse);
    ont.fetchOptions.mockResolvedValue(optionsResponse);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    client.clear();
    document.body.replaceChildren();
});

describe('OntInstrumentPanel opaque intent lifecycle', () => {
    it('keeps internal project IDs and unused revision inspection out of the default view', async () => {
        await act(async () => root.render(panel()));
        await flush();
        expect(container.textContent).not.toMatch(/Global Experiment ID|Domain Experiment ID|URL-owned pinned|No exact pinned|Run intent disabled/);
        expect(container.querySelector('[aria-label="Sequencing kit"]')).not.toBeNull();
        expect(container.querySelector('[aria-label="Run length in hours"]')).not.toBeNull();
    });

    it('explains an inserted flow cell with no compatible MinKNOW kit', async () => {
        ont.fetchOptions.mockResolvedValue({ data: { ...optionsResponse.data, can_start: false, available_kits: [], options: [] } });
        await act(async () => root.render(panel()));
        await flush();
        expect(container.textContent).toContain('Test cells are used for hardware checks in MinKNOW');
        expect(container.textContent).not.toContain('Insert a flow cell to list kits');
        expect([...container.querySelectorAll('button')].find((b) => b.textContent === 'Start sequencing')?.disabled).toBe(true);
    });

    it('offers Stop only for a server-confirmed run binding and preserves the run on stop', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
            { run_id: 'uncertain-unbound', status: 'unknown', experiment_group: 'domain-experiment-test', can_stop: false },
        ]);
        ont.stopRun.mockResolvedValue({ data: { ...intent, id: 'bound-run', status: 'stopped' } });
        await act(async () => root.render(panel()));
        await flush();
        const stop = [...container.querySelectorAll('button')].find((b) => b.textContent?.startsWith('Stop run bound-run'))!;
        expect(stop).toBeDefined();
        expect([...container.querySelectorAll('button')].some((b) => b.textContent?.startsWith('Stop run uncertain-unbound'))).toBe(false);
        await act(async () => stop.click());
        await waitUntil(() => expect(ont.stopRun).toHaveBeenCalledWith('bound-run'));
        expect(container.textContent).toContain('Data collected so far is kept');
    });
    it('shows live reads, basecalled yield and per-barcode output for an active run only', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
            { run_id: 'done-run', status: 'completed', experiment_group: 'domain-experiment-test', can_stop: false },
        ]);
        const counts = { read_count: 0, basecalled_fail_bases: 0, estimated_selected_bases: 0 };
        ont.fetchLive.mockResolvedValue({ data: {
            run_id: 'bound-run', status: 'running', available: true, basecalling_enabled: true, fraction_basecalled: 0.8,
            totals: { read_count: 12345, basecalled_pass_read_count: 1000, basecalled_fail_read_count: 100,
                basecalled_pass_bases: 3_500_000, basecalled_fail_bases: 400_000, estimated_selected_bases: 5_000_000 },
            barcodes: [
                { ...counts, barcode: 'unclassified', alias: null, basecalled_pass_read_count: 250, basecalled_fail_read_count: 60, basecalled_pass_bases: 900_000 },
                { ...counts, barcode: 'barcode02', alias: null, basecalled_pass_read_count: 250, basecalled_fail_read_count: 10, basecalled_pass_bases: 1_100_000 },
                { ...counts, barcode: 'barcode01', alias: 'plasmid_A', basecalled_pass_read_count: 500, basecalled_fail_read_count: 30, basecalled_pass_bases: 2_500_000 },
            ],
            channels: { sequencing_fraction: 0.6, open_pore_fraction: 0.2, other_fraction: 0.2 },
        } });
        await act(async () => root.render(panel()));
        await flush();
        await waitUntil(() => expect(container.querySelector('[aria-label="Reads per barcode"]')).toBeTruthy());
        expect(ont.fetchLive).toHaveBeenCalledWith('bound-run');
        expect(ont.fetchLive).not.toHaveBeenCalledWith('done-run');
        const live = container.querySelector('[data-testid="ont-run-live"]')!;
        expect(live.textContent).toContain('12,345');
        expect(live.textContent).toContain('1,000 reads, 3.50 Mb');
        expect(live.textContent).toContain('100 reads, 400.0 kb');
        expect(live.textContent).toContain('Basecalling progress: 80% of reads finished, 20% still waiting.');
        expect(live.textContent).toContain('60% sequencing, 20% open pore, 20% other');
        const rows = [...live.querySelectorAll('tbody tr')].map((row) => [...row.querySelectorAll('td')].map((td) => td.textContent));
        expect(rows).toEqual([
            ['barcode01', 'plasmid_A', '500', '2.50 Mb', '30', '50%'],
            ['barcode02', '', '250', '1.10 Mb', '10', '25%'],
            ['unclassified', '', '250', '900.0 kb', '60', '25%'],
        ]);
        expect(live.textContent).toContain('Share of passed reads');
    });
    it('draws the run graphs MinKNOW has data for, with ejected strands marked on the read lengths', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
        ]);
        const y = { read_count: 100, basecalled_pass_read_count: 80, basecalled_fail_read_count: 5, basecalled_pass_bases: 400_000, basecalled_fail_bases: 20_000, estimated_selected_bases: 500_000 };
        ont.fetchLive.mockResolvedValue({ data: {
            run_id: 'bound-run', status: 'running', available: true, basecalling_enabled: true, totals: y,
            barcodes: [{ ...y, barcode: 'barcode01', alias: 'plasmid_A' }, { ...y, barcode: 'unclassified', alias: null }],
            charts: {
                pore_activity: [
                    { start_seconds: 0, sequencing: 0.5, pore: 0.3, recovering: 0.1, inactive: 0.05, unclassified: 0.05 },
                    { start_seconds: 60, sequencing: 0.7, pore: 0.2, recovering: 0.05, inactive: 0.03, unclassified: 0.02 },
                ],
                cumulative: [{ ...y, seconds: 60 }, { ...y, seconds: 120, estimated_selected_bases: 900_000 }],
                read_lengths: { n50: 5600, buckets: [{ start: 0, end: 500, value: 100, rejected: 90 }, { start: 500, end: 6000, value: 400, rejected: 0 }] },
            },
        } });
        await act(async () => root.render(panel()));
        await flush();
        await waitUntil(() => expect(container.querySelector('[aria-label="Pore activity over time"]')).toBeTruthy());
        const svg = (label: string) => container.querySelector(`svg[aria-label="${label}"]`)!;
        expect(svg('Pore activity over time').querySelectorAll('rect[data-state="sequencing"]')).toHaveLength(2);
        expect(svg('Pore activity over time').parentElement!.textContent).toContain('70% sequencing now');
        expect(svg('Cumulative output over time').querySelectorAll('path[data-series]')).toHaveLength(3);
        expect(svg('Read length histogram').querySelectorAll('rect[data-kind="kept"]')).toHaveLength(2);
        expect(svg('Read length histogram').querySelectorAll('rect[data-kind="rejected"]')).toHaveLength(1);
        expect(svg('Read length histogram').parentElement!.textContent).toContain('Estimated N50 5.6 kb');
        expect(svg('Read length histogram').parentElement!.textContent).toContain('Ejected by adaptive sampling');
        expect(svg('Reads per barcode chart').querySelectorAll('rect[data-barcode]')).toHaveLength(2);
    });
    it('shows no empty graphs when MinKNOW has no series yet', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
        ]);
        ont.fetchLive.mockResolvedValue({ data: { run_id: 'bound-run', status: 'running', available: true, basecalling_enabled: true,
            totals: { read_count: 0, basecalled_pass_read_count: 0, basecalled_fail_read_count: 0, basecalled_pass_bases: 0, basecalled_fail_bases: 0, estimated_selected_bases: 0 },
            barcodes: [], charts: { pore_activity: [], cumulative: [], read_lengths: null } } });
        await act(async () => root.render(panel()));
        await flush();
        await waitUntil(() => expect(container.querySelector('[data-testid="ont-run-live-charts"]')).toBeTruthy());
        expect(container.querySelectorAll('[data-testid="ont-run-live-charts"] svg')).toHaveLength(0);
    });
    it('uploads the adaptive sampling reference and targets and sends the mode with the run', async () => {
        ont.fetchOptions.mockResolvedValue({ data: { ...optionsResponse.data, available_kits: ['SQK-RBK114-24'],
            options: [{ ...optionsResponse.data.options[0], basecalling_choices: [{ quality: 'hac', default: true, default_min_qscore: 9, modified_bases: [] }] }] } });
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockResolvedValue({ data: intent });
        ont.uploadFile.mockImplementation(async (directory: string, file: File) => ({ data: { path: `${directory}/${file.name}` } }));
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
        await act(async () => { root.render(panel()); });
        await flush();
        const mode = () => container.querySelector<HTMLSelectElement>('[aria-label="Adaptive sampling"]')!;
        await waitUntil(() => expect(mode()).toBeTruthy());
        expect(mode().value).toBe('');
        expect(container.querySelector('[aria-label="Adaptive sampling reference"]')).toBeNull();
        await act(async () => { mode().value = 'enrich'; mode().dispatchEvent(new Event('change', { bubbles: true })); });
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('needs a reference FASTA');
        const pick = async (label: string, file: File) => {
            const input = container.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
            Object.defineProperty(input, 'files', { value: [file], configurable: true });
            await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); });
        };
        await pick('Adaptive sampling reference', new File(['>p\nACGT\n'], 'plasmids.fasta'));
        await pick('Adaptive sampling target regions', new File(['p\t0\t4\n'], 'targets.bed'));
        const start = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(start.disabled).toBe(false));
        await act(async () => start.click());
        await flush();
        await waitUntil(() => expect(ont.createIntent).toHaveBeenCalled());
        expect(confirm.mock.calls.at(-1)![0]).toContain('Adaptive sampling: enrich.');
        const sent = ont.createIntent.mock.calls[0][1].adaptive_sampling;
        expect(sent.mode).toBe('enrich');
        expect(sent.reference_path).toMatch(/^inputs\/ont-adaptive-sampling\/[0-9a-f-]+\/plasmids\.fasta$/);
        expect(sent.bed_path).toBe(sent.reference_path.replace('plasmids.fasta', 'targets.bed'));
    });
    it('separates skipped reads from waiting ones and says when barcode numbers were not read', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
        ]);
        const totals = { read_count: 100, basecalled_pass_read_count: 60, basecalled_fail_read_count: 5, basecalled_pass_bases: 300_000, basecalled_fail_bases: 20_000, estimated_selected_bases: 500_000 };
        const reading = { run_id: 'bound-run', status: 'running', available: true, basecalling_enabled: true, barcoding_enabled: true,
            fraction_basecalled: 0.7, fraction_skipped: 0.2, acquisition_count: 2, totals, barcodes: [] };
        ont.fetchLive.mockResolvedValue({ data: { ...reading, barcodes_available: false } });
        await act(async () => root.render(panel()));
        await flush();
        const live = () => container.querySelector('[data-testid="ont-run-live"]')!;
        await waitUntil(() => expect(live().textContent).toContain('Basecalling progress'));
        expect(live().textContent).toContain('Basecalling progress: 70% of reads finished, 20% skipped, 10% still waiting.');
        expect(live().textContent).not.toContain('catch up');
        expect(live().textContent).toContain('split this run into 2 acquisitions');
        expect(live().querySelector('[role="status"]')!.textContent).toContain('did not return per-barcode numbers');
        expect(live().textContent).not.toContain('No reads have been assigned');
    });
    it('gives no barcode message for a run without a barcoding kit', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'running', experiment_group: 'domain-experiment-test', can_stop: true },
        ]);
        ont.fetchLive.mockResolvedValue({ data: { run_id: 'bound-run', status: 'running', available: true, basecalling_enabled: true, barcoding_enabled: false,
            fraction_basecalled: 1, barcodes_available: true, barcodes: [],
            totals: { read_count: 10, basecalled_pass_read_count: 10, basecalled_fail_read_count: 0, basecalled_pass_bases: 1000, basecalled_fail_bases: 0, estimated_selected_bases: 1000 } } });
        await act(async () => root.render(panel()));
        await flush();
        const live = () => container.querySelector('[data-testid="ont-run-live"]')!;
        await waitUntil(() => expect(live().textContent).toContain('Basecalling progress'));
        expect(live().textContent).not.toContain('barcode');
    });
    it('says so when MinKNOW has no live numbers for the active run', async () => {
        ont.fetchRuns.mockResolvedValue([
            { run_id: 'bound-run', status: 'starting', experiment_group: 'domain-experiment-test', can_stop: true },
        ]);
        ont.fetchLive.mockResolvedValue({ data: { run_id: 'bound-run', status: 'starting', available: false } });
        await act(async () => root.render(panel()));
        await flush();
        await waitUntil(() => expect(container.textContent).toContain('MinKNOW has no live numbers for this run yet.'));
        expect(container.querySelector('[aria-label="Reads per barcode"]')).toBeNull();
    });
    it('sends a sample name per barcode when the run uses a barcoding kit', async () => {
        ont.fetchOptions.mockResolvedValue({ data: { ...optionsResponse.data, available_kits: ['SQK-LSK114', 'SQK-RBK114-24'] } });
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockResolvedValue({ data: intent });
        await act(async () => { root.render(panel()); });
        await flush();
        expect(container.querySelector('[aria-label="Barcode sample names"]')).toBeNull();
        const kit = container.querySelector<HTMLSelectElement>('[aria-label="Sequencing kit"]')!;
        await waitUntil(() => expect(Array.from(kit.options).map(option => option.value)).toContain('SQK-RBK114-24'));
        await act(async () => { kit.value = 'SQK-RBK114-24'; kit.dispatchEvent(new Event('change', { bubbles: true })); });
        await flush();
        const names = container.querySelector<HTMLTextAreaElement>('[aria-label="Barcode sample names"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(names, 'barcode01\tplasmid_A\nbarcode02,plasmid_B');
            names.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const start = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(start.disabled).toBe(false));
        await act(async () => start.click());
        await flush();
        expect(ont.createIntent).toHaveBeenCalledWith('MD-105428', expect.objectContaining({
            barcode_samples: [{ barcode: 'barcode01', alias: 'plasmid_A' }, { barcode: 'barcode02', alias: 'plasmid_B' }],
        }));
    });
    it('sends the chosen basecalling accuracy, modified bases and minimum Q-score with the run', async () => {
        const choices = [
            { quality: 'fast', default: false, default_min_qscore: 8, modified_bases: [] },
            { quality: 'hac', default: true, default_min_qscore: 9, modified_bases: [{ id: '6mA', canonical_base: 'A', context: 'A' }] },
            { quality: 'sup', default: false, default_min_qscore: 10, modified_bases: [
                { id: '6mA', canonical_base: 'A', context: 'A' },
                { id: '5mCG_5hmCG', canonical_base: 'C', context: 'CG' },
                { id: '5mC_5hmC', canonical_base: 'C', context: 'C' },
            ] },
        ];
        ont.fetchOptions.mockResolvedValue({ data: { ...optionsResponse.data, available_kits: ['SQK-RBK114-24'],
            options: [{ ...optionsResponse.data.options[0], basecalling_choices: choices }] } });
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockResolvedValue({ data: intent });
        const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
        await act(async () => { root.render(panel()); });
        await flush();
        const accuracy = () => container.querySelector<HTMLSelectElement>('[aria-label="Basecalling accuracy"]')!;
        await waitUntil(() => expect(accuracy()).toBeTruthy());
        expect(accuracy().value).toBe('hac');
        expect(Array.from(accuracy().options).map(option => option.textContent)).toEqual(['FAST', 'HAC (MinKNOW default)', 'SUP']);
        await act(async () => { accuracy().value = 'sup'; accuracy().dispatchEvent(new Event('change', { bubbles: true })); });
        const box = (id: string) => container.querySelector<HTMLInputElement>(`[aria-label="Call ${id}"]`)!;
        await act(async () => box('6mA').click());
        await act(async () => box('5mC_5hmC').click());
        // One model per base: the CG-context model replaces the all-C model.
        await act(async () => box('5mCG_5hmCG').click());
        expect(box('5mC_5hmC').checked).toBe(false);
        expect(box('5mCG_5hmCG').checked).toBe(true);
        const qscore = container.querySelector<HTMLInputElement>('[aria-label="Minimum read Q-score"]')!;
        expect(qscore.placeholder).toBe('10 (MinKNOW default)');
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(qscore, '12');
            qscore.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const start = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(start.disabled).toBe(false));
        await act(async () => start.click());
        await flush();
        expect(confirm.mock.calls.at(-1)![0]).toContain('Basecalling SUP, calling 6mA, 5mCG_5hmCG, minimum Q12.');
        expect(ont.createIntent).toHaveBeenCalledWith('MD-105428', expect.objectContaining({
            basecalling: { quality: 'sup', modified_bases: ['6mA', '5mCG_5hmCG'], min_qscore: 12 },
        }));
    });

    it('starts with the MinKNOW default basecalling when nothing is changed, and shows no controls when none are offered', async () => {
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockResolvedValue({ data: intent });
        await act(async () => { root.render(panel()); });
        await flush();
        const start = () => Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(start().disabled).toBe(false));
        expect(container.querySelector('[data-testid="ont-live-basecalling"]')).toBeNull();
        await act(async () => start().click());
        await flush();
        expect(ont.createIntent.mock.calls[0][1]).not.toHaveProperty('basecalling');
    });

    it('retains the durable run and reports an uncertain physical start without resubmitting it', async () => {
        let rejectStart: (reason: unknown) => void = () => undefined;
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockImplementation(() => new Promise((_, reject) => { rejectStart = reject; }));

        await act(async () => {
            root.render(panel());
        });
        await flush();

        const kit = container.querySelector<HTMLSelectElement>('[aria-label="Sequencing kit"]')!;
        await act(async () => { kit.value = 'SQK-LSK114'; kit.dispatchEvent(new Event('change', { bubbles: true })); });
        const validate = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(validate.disabled).toBe(false));
        await act(async () => validate.click());
        await flush();

        expect(ont.createIntent).toHaveBeenCalledWith('MD-105428', expect.objectContaining({
            option_id: 'ont-option-safe',
            option_receipt_id: 'ont-preflight-safe',
        }));
        expect(ont.startIntent).toHaveBeenCalledWith('ont-run-durable-001', {
            confirm_start: true,
            intent_generation: 1,
        });
        expect(container.textContent).toContain('Starting run ont-run-durable-001 in MinKNOW');

        await act(async () => {
            rejectStart({
                response: {
                    status: 502,
                    data: { detail: 'Lost contact with MinKNOW while starting. Check MinKNOW before retrying.' },
                },
            });
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Lost contact with MinKNOW while starting');
        expect(ont.startIntent).toHaveBeenCalledTimes(1);
        expect(container.textContent).not.toContain('Request failed');
    });

    it('fails closed and exposes a refresh error after cached protocol authorization becomes stale', async () => {
        await act(async () => {
            root.render(panel());
        });
        await flush();

        const validate = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Start sequencing') as HTMLButtonElement;
        await waitUntil(() => expect(validate.disabled).toBe(false));

        ont.fetchOptions.mockRejectedValueOnce(new Error('MinKNOW refresh failed'));
        await act(async () => {
            await client.refetchQueries({ queryKey: ['ont-protocol-options', 'MD-105428'] });
        });
        await flush();

        expect(validate.disabled).toBe(true);
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('could not be refreshed');
        expect(container.textContent).not.toContain('Server-approved protocol · Server output policy');
    });
    it('requests only the selected Project runs server-side and pages older runs instead of dropping them', async () => {
        const run = (index: number) => ({
            run_id: `project-run-${index}`,
            status: 'completed',
            experiment_group: 'domain-experiment-test',
            observed_generation: 1,
            can_stop: false,
        });
        ont.fetchRuns
            .mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => run(index)))
            .mockResolvedValueOnce([run(100), run(101)]);
        await act(async () => root.render(panel(true)));
        await flush();
        expect(ont.fetchRuns).toHaveBeenNthCalledWith(1, 100, { experimentGroup: 'domain-experiment-test', offset: 0 });
        await waitUntil(() => expect(container.textContent).toContain('100 runs loaded · older runs available'));
        const more = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Load older runs')!;
        expect(more).toBeDefined();
        await act(async () => more.click());
        await waitUntil(() => expect(container.textContent).toContain('102 runs loaded'));
        expect(ont.fetchRuns).toHaveBeenNthCalledWith(2, 100, { experimentGroup: 'domain-experiment-test', offset: 100 });
        expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Load older runs')).toBe(false);
        const options = [...container.querySelectorAll('option')].map((option) => option.textContent);
        expect(options).toContain('project-run-101 · completed · version 1');
    });
});
