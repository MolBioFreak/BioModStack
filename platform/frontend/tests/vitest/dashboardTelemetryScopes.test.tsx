import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardTelemetry } from '../../src/components/dashboard/DashboardTelemetry';
import { api, type ExecutionTarget } from '../../src/lib/api';

vi.mock('../../src/components/InfraLiveTelemetry', () => ({
    InfraLiveTelemetry: () => <div data-testid="local-telemetry">Local telemetry panel</div>,
}));

vi.mock('../../src/components/RemoteGpuTelemetry', () => ({
    RemoteGpuTelemetry: () => <div data-testid="remote-telemetry">Remote telemetry panel</div>,
}));

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const defaultApiAdapter = api.defaults.adapter;
let unexpectedRequests: string[] = [];
function unexpected(config: { method?: string; url?: string }): never {
    unexpectedRequests.push(`${config.method} ${config.url}`);
    throw new Error('Unexpected request');
}
beforeEach(() => {
    unexpectedRequests = [];
    api.defaults.adapter = async config => {
        if (config.method === 'get' && config.url === '/api/execution-targets/vast%3A123/runtime-inventory') return response(null);
        return unexpected(config);
    };
});

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
    window.localStorage.clear();
    api.defaults.adapter = defaultApiAdapter;
    expect(unexpectedRequests).toEqual([]);
});

describe('Dashboard telemetry source tabs', () => {
    it('switches between local, named Vast, and combined active telemetry', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['remote-provision-catalog'], []);
        client.setQueryData(['execution-targets'], response([readyTarget]));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <DashboardTelemetry />
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        const tabs = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
        expect(tabs.map((tab) => tab.textContent?.trim())).toEqual([
            'Local',
            'Vast · Remote 4090',
            'Combined',
        ]);
        expect(container.querySelectorAll('[data-testid="local-telemetry"]')).toHaveLength(1);
        expect(container.querySelectorAll('[data-testid="remote-telemetry"]')).toHaveLength(0);

        await act(async () => {
            tabs[1]?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(container.querySelectorAll('[data-testid="local-telemetry"]')).toHaveLength(0);
        expect(container.querySelectorAll('[data-testid="remote-telemetry"]')).toHaveLength(1);

        await act(async () => {
            tabs[2]?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(container.querySelector('[data-bms-telemetry-combined="true"]')).toBeTruthy();
        expect(container.querySelectorAll('[data-testid="local-telemetry"]')).toHaveLength(1);
        expect(container.querySelectorAll('[data-testid="remote-telemetry"]')).toHaveLength(1);

        await act(async () => root.unmount());
        client.clear();
    });

    it('mounts saved recipes under the selected worker; preload is click-only and persisted progress survives refresh', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        let target: ExecutionTarget = readyTarget;
        const requests: string[] = [];
        api.defaults.adapter = async config => {
            if (config.method === 'post' && config.url === '/api/execution-targets/vast%3A123/preload') {
                requests.push(String(config.url));
                expect(JSON.parse(String(config.data))).toEqual({ job_id: 'saved-job' });
                target = { ...target, preload: { operation_id: 'op', job_id: 'saved-job', source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64), phase: 'transferring', artifact: 'esmfold2.sif', message: 'Transferring runtime', started_at: '2026-09-06', updated_at: '2026-09-06' } };
                return response(target);
            }
            if (config.method === 'get' && config.url === '/api/execution-targets/vast%3A123/runtime-inventory') return response(null);
            if (config.method === 'get' && config.url === '/api/execution-targets') return response([target]);
            return unexpected(config);
        };
        client.setQueryData(['remote-provision-catalog'], []);
        client.setQueryData(['execution-targets'], response([target]));
        const container = document.createElement('div');
        document.body.append(container);
        const root = createRoot(container);
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><DashboardTelemetry jobs={[{ id: 'saved-job', name: 'Saved ESM', model_id: 'esmfold2' }]} /></QueryClientProvider>));
            expect(container.querySelector('[aria-label="Remote preload and activity"]')).toBeNull();
            await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent?.includes('Vast'))!.click());
            const selector = container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')!;
            expect(selector.textContent).toContain('Saved ESM');
            await act(async () => { selector.value = 'saved-job'; selector.dispatchEvent(new Event('change', { bubbles: true })); });
            expect(requests).toEqual([]);
            await act(async () => {
                const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === 'Preload selected worker')!;
                button.click();
                button.click(); // Same-tick duplicate input must not enqueue a second operation.
                await new Promise(resolve => setTimeout(resolve, 20));
            });
            expect(requests).toEqual(['/api/execution-targets/vast%3A123/preload']);
            expect(container.textContent).toContain('Transferring runtime');
            expect(container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')?.disabled).toBe(true);
            target = { ...target, preload: { ...target.preload!, phase: 'source_download_ready' }, progress: { operation_id: 'run', job_id: 'saved-job', phase: 'running', artifact: null, message: 'Scientific workflow running', updated_at: '2026-09-06', activity: { stage: 'protenix', state: 'started', updated_at: '2026-09-06' } } };
            await act(async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(container.textContent).toContain('Source/download ready — not scientific Ready');
            expect(container.textContent).toContain('protenix: started');
            expect([...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === 'Preload selected worker')?.disabled).toBe(true);
            // Leaving/re-entering the mounted panel restores actual server activity, not a guessed stage.
            await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent === 'Local')!.click());
            await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent?.includes('Vast'))!.click());
            expect(container.querySelector('[aria-label="Worker activity"]')?.textContent).toContain('protenix: started');
            expect(container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')?.value).toBe('');
            expect(requests).toHaveLength(1);
        } finally { await act(async () => root.unmount()); client.clear(); }
    });

    it('requires explicit retry after server rejection and preserves failed progress on remount', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        const target: ExecutionTarget = { ...readyTarget, preload: {
            operation_id: 'failed-op', job_id: 'saved-job', source_revision: 'a'.repeat(40), source_tree: 'b'.repeat(40), request_sha256: 'c'.repeat(64),
            phase: 'failed', artifact: 'esmfold2.sif', message: 'Artifact verification failed', started_at: '2026-09-06', updated_at: '2026-09-06',
        } };
        const post = vi.spyOn(api, 'post').mockRejectedValue({ isAxiosError: true, message: 'Request failed with status code 409', response: { data: { detail: 'Worker has active execution' } } });
        api.defaults.adapter = async config => {
            if (config.method === 'get' && config.url === '/api/execution-targets/vast%3A123/runtime-inventory') return response(null);
            if (config.method === 'get' && config.url === '/api/execution-targets') return response([target]);
            return unexpected(config);
        };
        client.setQueryData(['remote-provision-catalog'], []);
        client.setQueryData(['execution-targets'], response([target]));
        const container = document.createElement('div');
        document.body.append(container);
        const root = createRoot(container);
        const render = () => root.render(<QueryClientProvider client={client}><DashboardTelemetry jobs={[{ id: 'saved-job', name: 'Saved ESM', model_id: 'esmfold2' }]} /></QueryClientProvider>);
        const openWorker = () => [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent?.includes('Vast'))!.click();
        try {
            await act(async () => render());
            await act(async () => openWorker());
            expect(container.textContent).toContain('Artifact verification failed');
            const retryButton = () => [...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === 'Retry preload')!;
            expect(retryButton().disabled).toBe(true);
            await act(async () => {
                const selector = container.querySelector<HTMLSelectElement>('[aria-label="Saved Job recipe"]')!;
                selector.value = 'saved-job'; selector.dispatchEvent(new Event('change', { bubbles: true }));
            });
            expect(post).not.toHaveBeenCalled();
            await act(async () => { retryButton().click(); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(container.querySelector('[role="alert"]')?.textContent).toBe('Worker has active execution');
            expect(post).toHaveBeenCalledTimes(1);
            await act(async () => { await client.invalidateQueries({ queryKey: ['execution-targets'] }); });
            expect(post).toHaveBeenCalledTimes(1);
            await act(async () => { retryButton().click(); await new Promise(resolve => setTimeout(resolve, 20)); });
            expect(post).toHaveBeenCalledTimes(2);
            await act(async () => root.render(null));
            await act(async () => render());
            await act(async () => openWorker());
            expect(container.textContent).toContain('Artifact verification failed');
            expect(retryButton().disabled).toBe(true);
            expect(post).toHaveBeenCalledTimes(2);
        } finally { await act(async () => root.unmount()); client.clear(); post.mockRestore(); }
    });

    it.each(['error', 'empty'])('hides cached remote selectors when inventory is %s', async (outcome) => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        client.setQueryData(['remote-provision-catalog'], []);
        client.setQueryData(['execution-targets'], response([readyTarget]));
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <QueryClientProvider client={client}>
                    <DashboardTelemetry />
                </QueryClientProvider>,
            );
            await Promise.resolve();
        });

        const vastTab = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
            .find((tab) => tab.textContent?.includes('Vast ·'));
        await act(async () => {
            vastTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(container.querySelectorAll('[data-testid="remote-telemetry"]')).toHaveLength(1);

        api.defaults.adapter = async config => {
            if (config.method === 'get' && config.url === '/api/execution-targets/vast%3A123/runtime-inventory') return response(null);
            if (config.method === 'get' && config.url === '/api/execution-targets') return outcome === 'empty' ? response([]) : Promise.reject(new Error('offline'));
            return unexpected(config);
        };
        await act(async () => {
            await client.invalidateQueries({ queryKey: ['execution-targets'] });
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        const remainingTabs = [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
        expect(remainingTabs.map((tab) => tab.textContent?.trim())).toEqual(['Local']);
        expect(container.querySelectorAll('[data-testid="local-telemetry"]')).toHaveLength(1);
        expect(container.querySelectorAll('[data-testid="remote-telemetry"]')).toHaveLength(0);

        await act(async () => root.unmount());
        client.clear();
    });
});
