import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ont = vi.hoisted(() => ({
    createIntent: vi.fn(),
    startIntent: vi.fn(),
}));

vi.mock('../../src/lib/api', () => ({
    fetchOntDeviceStatus: async () => ({
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
    }),
    fetchOntProtocolOptions: async () => ({
        data: {
            position: 'MD-105428',
            can_start: true,
            blockers: [],
            flow_cell_present: true,
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
    }),
    createOntRunIntent: ont.createIntent,
    startOntRunIntent: ont.startIntent,
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
    ont.createIntent.mockReset();
    ont.startIntent.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    document.body.replaceChildren();
});

describe('OntInstrumentPanel opaque intent lifecycle', () => {
    it('retains the created BMS intent and renders it revalidated/armed after the expected disabled-start response', async () => {
        let rejectStart: (reason: unknown) => void = () => undefined;
        ont.createIntent.mockResolvedValue({ data: intent });
        ont.startIntent.mockImplementation(() => new Promise((_, reject) => { rejectStart = reject; }));

        await act(async () => {
            root.render(<QueryClientProvider client={client}><OntInstrumentPanel onAnalyzeExistingData={() => undefined} /></QueryClientProvider>);
        });
        await flush();

        const validate = Array.from(container.querySelectorAll('button')).find((element) => element.textContent === 'Validate run intent') as HTMLButtonElement;
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
        expect(container.textContent).toContain('Intent ont-run-durable-001 · armed');

        await act(async () => {
            rejectStart({
                response: {
                    status: 501,
                    data: { detail: 'MinKNOW protocol start remains disabled pending separately authorized supervised commissioning' },
                },
            });
            await Promise.resolve();
        });

        expect(container.textContent).toContain('BMS run ont-run-durable-001 remains armed after fresh revalidation');
        expect(container.textContent).toContain('physical MinKNOW start remains disabled');
        expect(container.textContent).not.toContain('Request failed');
    });
});