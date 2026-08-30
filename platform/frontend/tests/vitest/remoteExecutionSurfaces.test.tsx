import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it } from 'vitest';

import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { RemoteGpuTelemetry } from '../../src/components/RemoteGpuTelemetry';
import {
    EXECUTION_TARGET_STORAGE_KEY,
    submitBoltzApiJob,
    submitOntNgsJob,
    submitShapeBlueprint,
} from '../../src/lib/api';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });

const readyTarget = {
    id: 'vast:123',
    provider: 'vast' as const,
    provider_instance_id: '123',
    name: 'Remote 4090',
    state: 'ready' as const,
    active: true,
    host: '203.0.113.10',
    port: 22,
    username: 'root',
    remote_root: '/opt/biomodstack',
    host_key_sha256: 'a'.repeat(64),
    capabilities: { gpu_count: 1, gpu_name: 'RTX 4090' },
    pricing: { hourly_rate: 0.25, provider_started_at: '2026-08-30T12:00:00Z' },
    last_error: null,
    last_seen_at: '2026-08-30T12:00:00Z',
    activated_at: '2026-08-30T12:00:00Z',
};

afterEach(() => {
    document.body.replaceChildren();
    window.sessionStorage.clear();
});

describe('remote execution operator surfaces', () => {
    it('fails closed for Job Submission launchers that cannot execute on Vast', () => {
        window.history.replaceState({}, '', '/submit');
        window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:123');

        expect(() => submitShapeBlueprint({} as never)).toThrow(/Choose Local/);
        expect(() => submitBoltzApiJob({} as never)).toThrow(/Choose Local/);
        expect(() => submitOntNgsJob('wf-clone', {} as never)).toThrow(/Choose Local/);
    });

    it('keeps Local as the default and persists only an explicit Vast selection', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['execution-targets'], response([readyTarget]));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <ExecutionTargetPicker />
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
        const vastButton = [...container.querySelectorAll('button')]
            .find((button) => button.textContent?.includes('Vast · Remote 4090'));
        expect(vastButton).toBeTruthy();
        await act(async () => {
            vastButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBe('vast:123');

        const localButton = [...container.querySelectorAll('button')]
            .find((button) => button.textContent?.trim() === 'Local');
        await act(async () => {
            localButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();

        await act(async () => root.unmount());
        client.clear();
    });

    it('renders namespaced read-only Vast GPU telemetry', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['active-remote-gpu-telemetry'], response({
            source: 'active_vast',
            available: true,
            target: readyTarget,
            observed_at: '2026-08-30T12:05:00Z',
            gpus: [{
                id: 'vast:123:gpu:0', execution_target_id: 'vast:123', index: 0,
                uuid: 'GPU-remote', name: 'RTX 4090', utilization: 77,
                memory_used_mb: 12000, memory_total_mb: 24564,
                temperature: 63, power_draw_w: 310,
                controls: { fan: false, power: false },
            }],
        }));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <RemoteGpuTelemetry />
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Read-only remote telemetry');
        expect(container.textContent).toContain('Remote GPU 0');
        expect(container.textContent).toContain('12,000 MB used');
        expect(container.textContent).not.toContain('Fan speed');
        expect(container.textContent).not.toContain('Power limit');

        await act(async () => root.unmount());
        client.clear();
    });
});
