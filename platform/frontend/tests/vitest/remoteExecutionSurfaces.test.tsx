import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ExecutionTargetPicker } from '../../src/components/ExecutionTargetPicker';
import { InfraLiveTelemetry } from '../../src/components/InfraLiveTelemetry';
import { RemoteGpuTelemetry } from '../../src/components/RemoteGpuTelemetry';
import {
    api,
    EXECUTION_TARGET_STORAGE_KEY,
    submitBoltzApiJob,
    submitOntBarcodeBatch,
    submitOntNgsJob,
    submitPooledReferenceAssignment,
    submitShapeBlueprint,
} from '../../src/lib/api';
import { submitCmRequest } from '../../src/components/conformationalMapping/conformationalMappingApi';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const defaultApiAdapter = api.defaults.adapter;

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

const discoveredTarget = {
    provider: 'vast' as const,
    provider_instance_id: '456',
    name: 'Remote A6000',
    provider_state: 'running',
    host: '203.0.113.11',
    port: 22,
    username: 'root',
    gpu_name: 'RTX A6000',
    gpu_count: 1,
    gpu_vram_mb: 49140,
    hourly_rate_usd: 0.35,
    started_at: '2026-08-31T12:00:00Z',
};

const persistedDiscoveredTarget = {
    ...readyTarget,
    id: 'vast:456',
    provider_instance_id: '456',
    name: 'Remote A6000',
    state: 'discovered' as const,
    active: false,
    host: '203.0.113.11',
    capabilities: { gpu_count: 1, gpu_name: 'RTX A6000', gpu_vram_mb: 49140 },
    pricing: { hourly_rate: 0.35, provider_started_at: '2026-08-31T12:00:00Z' },
    host_key_sha256: null,
    activated_at: null,
};

const discoveredSystem = {
    gpus: [],
    gpu_error: null,
    cpu: {
        name: 'Test CPU',
        cores_physical: 4,
        cores_logical: 8,
        utilization: 10,
        per_core_utilization: [],
        frequency_current_mhz: 3000,
        frequency_max_mhz: 4000,
        temperature: 40,
        power_watts: 35,
    },
    ram: {
        total_gb: 32,
        used_gb: 8,
        available_gb: 24,
        utilization: 25,
        swap_total_gb: 0,
        swap_used_gb: 0,
        swap_percent: 0,
    },
    timestamp: '2026-09-01T12:00:00Z',
    cpu_history: [],
    ram_history: [],
};

const hardwareDiscovery = {
    success: true,
    message: '3 GPUs discovered',
    gpu_count: 3,
    gpu_error: null,
    cpu_power_telemetry: {
        source: 'rapl',
        available: true,
        status: 'ok',
        message: 'available',
        discovered_sources: 1,
        readable_sources: 1,
    },
    power_control: {
        limits: {},
        eco_mode: false,
        power_percentage: 100,
        total_current_watts: 0,
        total_max_watts: 0,
        hardware_limits: {},
    },
    fan_control: {
        supported: false,
        message: 'unavailable',
        backend: 'none',
        available_modes: [],
        gpus: {},
    },
    timestamp: '2026-09-01T12:00:00Z',
};

beforeEach(() => {
    api.defaults.adapter = async () => { throw new Error('offline test dependency'); };
});

afterEach(() => {
    document.body.replaceChildren();
    window.sessionStorage.clear();
    api.defaults.adapter = defaultApiAdapter;
});

describe('remote execution operator surfaces', () => {
    it.each([true, false])('shows current empty or unknown inventory and clears saved placement on Dashboard (available=%s)', async (available) => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, readyTarget.id);
        api.defaults.adapter = async (config) => {
            if (config.url === '/api/execution-targets') {
                if (!available) throw new Error('Inventory unknown');
                return response([]);
            }
            if (config.url === '/api/execution-targets/providers/vast/refresh') {
                return response({ provider: 'vast', available, credential_configured: available, message: 'fixture inventory', instances: [] });
            }
            throw new Error('offline dependency');
        };
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(<QueryClientProvider client={client}><InfraLiveTelemetry variant="dashboard" /></QueryClientProvider>);
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
        const button = [...container.querySelectorAll('button')].find((node) => node.textContent === 'Discover running Vast');
        await act(async () => {
            button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
        expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
        expect(container.textContent).not.toContain('Remote 4090');
        expect(container.textContent).toContain(available ? 'No owned Vast instances' : 'Vast inventory unavailable');
        if (!available) {
            expect(container.textContent).not.toContain('Vast discovery complete');
            const receipt = [...container.querySelectorAll('[role="status"]')].find((node) => node.textContent?.includes('Vast inventory unavailable'));
            expect(receipt?.className).toContain('amber');
            expect(receipt?.className).not.toContain('emerald');
        }
        await act(async () => root.unmount());
        client.clear();
    });
    it('gives both compact Dashboard discovery actions visible success receipts', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        const requests: string[] = [];
        let targets = [] as typeof persistedDiscoveredTarget[];
        api.defaults.adapter = async (config) => {
            requests.push(`${config.method?.toUpperCase()} ${config.url}`);
            if (config.url === '/api/gpu/hardware/discover') return response(hardwareDiscovery);
            if (config.url === '/api/gpu/status') return response(discoveredSystem);
            if (config.url === '/api/execution-targets') return response(targets);
            if (config.url === '/api/execution-targets/providers/vast/refresh') {
                targets = [persistedDiscoveredTarget];
                return response({
                    provider: 'vast',
                    available: true,
                    credential_configured: true,
                    message: '1 running instance found',
                    instances: [discoveredTarget],
                });
            }
            throw new Error('offline test dependency');
        };
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<QueryClientProvider client={client}><InfraLiveTelemetry variant="dashboard" /></QueryClientProvider>);
            await Promise.resolve();
        });
        const buttons = [...container.querySelectorAll('button')];
        const hardwareButton = buttons.find((button) => button.textContent?.trim() === 'Discover hardware');
        const vastButton = buttons.find((button) => button.textContent?.trim() === 'Discover running Vast');
        expect(hardwareButton).toBeTruthy();
        expect(vastButton).toBeTruthy();
        expect(hardwareButton?.parentElement).toBe(vastButton?.parentElement);
        expect(hardwareButton?.className).toContain('px-2.5 py-1.5 text-[10px]');
        expect(vastButton?.className).toContain('px-2.5 py-1.5 text-[10px]');

        await act(async () => {
            hardwareButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        expect(requests).toContain('POST /api/gpu/hardware/discover');
        expect(container.textContent).toContain('Hardware discovery complete: 3 GPUs discovered');

        await act(async () => {
            vastButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        expect(requests).toContain('POST /api/execution-targets/providers/vast/refresh');
        expect(container.textContent).toContain('Vast discovery complete: 1 running instance found');
        expect(container.textContent).toContain('Remote A6000');
        expect(container.textContent).toContain('Discovered');
        expect(container.textContent).toContain('Attach worker');

        await act(async () => root.unmount());
        client.clear();
    });

    it('restores a persisted discovered target and attaches it from Dashboard without launching a workflow', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['execution-targets'], response([persistedDiscoveredTarget]));
        const requests: string[] = [];
        let targets = [persistedDiscoveredTarget];
        api.defaults.adapter = async (config) => {
            requests.push(`${config.method?.toUpperCase()} ${config.url}`);
            if (config.url === '/api/execution-targets') return response(targets);
            if (config.url === '/api/execution-targets/activate') {
                targets = [readyTarget];
                return response(readyTarget);
            }
            throw new Error('offline test dependency');
        };
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<QueryClientProvider client={client}><InfraLiveTelemetry variant="dashboard" /></QueryClientProvider>);
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Remote A6000');
        expect(container.textContent).toContain('Attach worker');
        const attachButton = [...container.querySelectorAll('button')]
            .find((button) => button.textContent?.trim() === 'Attach worker');
        await act(async () => {
            attachButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        expect(requests).toContain('POST /api/execution-targets/activate');
        expect(container.textContent).toContain('Ready');
        expect(container.textContent).toContain('Remote analytics available');
        expect(container.textContent).toContain('Detach');

        await act(async () => root.unmount());
        client.clear();
    });

    it('keeps lifecycle controls on Dashboard and leaves Job Launcher for placement only', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['execution-targets'], response([readyTarget]));
        window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, readyTarget.id);
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<QueryClientProvider client={client}><ExecutionTargetPicker /></QueryClientProvider>);
            await Promise.resolve();
        });

        expect(container.textContent).toContain('Local');
        expect(container.textContent).toContain('Vast · Remote 4090');
        expect(container.textContent).not.toContain('Discover running Vast');
        expect(container.textContent).not.toContain('Attach worker');
        expect(container.textContent).not.toContain('Detach');

        await act(async () => root.unmount());
        client.clear();
    });

    it('fails closed for Job Submission launchers that cannot execute on Vast', async () => {
        window.history.replaceState({}, '', '/submit');
        window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:123');

        expect(() => submitShapeBlueprint({} as never)).toThrow(/Choose Local/);
        expect(() => submitBoltzApiJob({} as never)).toThrow(/Choose Local/);
        expect(() => submitOntNgsJob('wf-clone', {} as never)).toThrow(/Choose Local/);
        expect(() => submitOntBarcodeBatch('source', {} as never)).toThrow(/Choose Local/);
        expect(() => submitPooledReferenceAssignment({} as never)).toThrow(/Choose Local/);
        await expect(submitCmRequest({} as never)).rejects.toThrow(/Choose Local/);
    });

    it('clears retained Vast selection when target refresh fails', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['execution-targets'], response([readyTarget]));
        window.sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, readyTarget.id);
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
            root.render(<QueryClientProvider client={client}><ExecutionTargetPicker /></QueryClientProvider>);
            await Promise.resolve();
        });
        api.defaults.adapter = async () => Promise.reject(new Error('offline'));
        await act(async () => {
            await client.invalidateQueries({ queryKey: ['execution-targets'] });
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        expect(window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
        expect(container.textContent).not.toContain('Vast · Remote 4090');
        await act(async () => root.unmount());
        client.clear();
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
