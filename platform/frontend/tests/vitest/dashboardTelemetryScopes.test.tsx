import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DashboardTelemetry } from '../../src/components/dashboard/DashboardTelemetry';
import { api } from '../../src/lib/api';

vi.mock('../../src/components/InfraLiveTelemetry', () => ({
    InfraLiveTelemetry: () => <div data-testid="local-telemetry">Local telemetry panel</div>,
}));

vi.mock('../../src/components/RemoteGpuTelemetry', () => ({
    RemoteGpuTelemetry: () => <div data-testid="remote-telemetry">Remote telemetry panel</div>,
}));

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

afterEach(() => {
    document.body.replaceChildren();
    window.localStorage.clear();
    api.defaults.adapter = defaultApiAdapter;
});

describe('Dashboard telemetry source tabs', () => {
    it('switches between local, named Vast, and combined active telemetry', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
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

    it('hides cached remote selectors when current target refresh fails', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
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

        api.defaults.adapter = async () => Promise.reject(new Error('offline'));
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
