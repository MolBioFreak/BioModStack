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

function panel(rawSignalOnly = false) {
    return (
        <MemoryRouter>
            <QueryClientProvider client={client}>
                <OntInstrumentPanel rawSignalOnly={rawSignalOnly} onAnalyzeExistingData={() => undefined} />
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
    it('opens raw-signal tools without polling MinKNOW and registers an indexed BLOW5 pair', async () => {
        ont.uploadFile.mockImplementation(async (directory: string, file: File) => ({ data: { path: `${directory}/${file.name}`, size: file.size } }));
        ont.importRawSignal.mockResolvedValue({ run_id: 'external-native', observed_generation: 1, already_registered: false });
        await act(async () => root.render(panel(true)));
        await flush();
        expect(container.textContent).toContain('Raw signal tools');
        expect(container.textContent).toContain('Squigualiser');
        expect(container.textContent).toContain('Squigulator');
        expect(container.textContent).not.toContain('ONT instrument control');
        expect(ont.fetchDevice).not.toHaveBeenCalled();
        expect(ont.fetchOptions).not.toHaveBeenCalled();
        const source = container.querySelector<HTMLInputElement>('input[aria-label="Raw-signal file"]')!;
        Object.defineProperty(source, 'files', { value: [new File(['blow5 bytes'], 'reads.blow5')], configurable: true });
        await act(async () => source.dispatchEvent(new Event('change', { bubbles: true })));
        const index = container.querySelector<HTMLInputElement>('input[aria-label="BLOW5 index"]')!;
        Object.defineProperty(index, 'files', { value: [new File(['index bytes'], 'reads.blow5.idx')], configurable: true });
        await act(async () => index.dispatchEvent(new Event('change', { bubbles: true })));
        const button = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Import raw signal')!;
        expect(button.disabled).toBe(false);
        await act(async () => button.click());
        await waitUntil(() => expect(ont.importRawSignal).toHaveBeenCalledTimes(1));
        const request = ont.importRawSignal.mock.calls[0][0];
        expect(request).toMatchObject({ format: 'blow5', experiment_group: 'domain-experiment-test', sample_id: null });
        expect(request.file_path).toMatch(/^inputs\/ont-raw-signal\/[0-9a-f-]+\/source.blow5$/);
        expect(request.index_file_path).toBe(request.file_path + '.idx');
        expect(request).not.toHaveProperty('source_fidelity');
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
    it('offers upload and existing-POD5 registration as two choices in one Add raw signal card', async () => {
        const fetchCandidates = (await import('../../src/lib/api')).fetchOntExternalPod5Candidates as unknown as ReturnType<typeof vi.fn>;
        fetchCandidates.mockResolvedValueOnce({ candidates: [{ candidate_id: 'pod5-candidate-1', display_name: 'run.pod5', size_bytes: 1024 ** 3 }] });
        await act(async () => root.render(panel(true)));
        await flush();
        const headings = [...container.querySelectorAll('h3')].map((h) => h.textContent);
        expect(headings).toContain('Add raw signal');
        expect(headings).not.toContain('Upload raw signal');
        expect(headings).not.toContain('Register existing POD5');
        const choices = [...container.querySelectorAll('[role="radiogroup"][aria-label="Raw-signal source"] [role="radio"]')];
        expect(choices.map((choice) => choice.getAttribute('aria-checked'))).toEqual(['true', 'false']);
        expect(container.querySelector('input[aria-label="Raw-signal file"]')).not.toBeNull();
        expect(container.querySelector('select[aria-label="Existing POD5 candidate"]')).toBeNull();
        await act(async () => (choices[1] as HTMLButtonElement).click());
        expect(container.querySelector('input[aria-label="Raw-signal file"]')).toBeNull();
        const select = container.querySelector<HTMLSelectElement>('select[aria-label="Existing POD5 candidate"]')!;
        expect(select).not.toBeNull();
        expect([...select.options].map((option) => option.value)).toContain('pod5-candidate-1');
        expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Register selected POD5')).toBe(true);
    });
});
