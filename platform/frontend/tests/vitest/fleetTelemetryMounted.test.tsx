import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { DashboardTelemetry } from '../../src/components/dashboard/DashboardTelemetry';
import { api } from '../../src/lib/api';

vi.mock('../../src/components/InfraLiveTelemetry', () => ({ InfraLiveTelemetry: () => <div>Local fixture</div> }));

it('requires fleet selection and isolates telemetry, cursors, inventory and late replies by worker', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const workers = ['1', '2'].map(id => ({ id: `vast:${id}`, provider: 'vast', provider_instance_id: id,
        name: `Worker ${id}`, active: true, state: 'ready', pricing: {}, capabilities: {} }));
    client.setQueryData(['execution-targets'], { data: workers });
    const oldAdapter = api.defaults.adapter;
    const unexpected: string[] = [];
    const reads: Array<{ id: string; since?: string }> = [];
    const inventory: string[] = [];
    const pending = new Map<string, (value: unknown) => void>();
    const payload = (id: string) => ({ source: 'active_vast', available: true,
        target: workers.find(w => w.id === id), gpus: [], history: [], cursor: `cursor-${id}` });
    api.defaults.adapter = async config => {
        let data: unknown;
        if (config.method === 'get' && config.url === '/api/execution-targets/provision/catalog') data = [];
        else if (config.method === 'get' && /\/vast%3A[12]\/runtime-inventory$/.test(config.url ?? '')) {
            inventory.push(config.url!); data = null;
        } else if (config.method === 'get' && config.url === '/api/execution-targets/active/telemetry') {
            const id = config.params.execution_target_id;
            reads.push({ id, since: config.params.since });
            data = await new Promise(resolve => pending.set(id, resolve));
        } else { unexpected.push(`${config.method} ${config.url}`); throw new Error('Unexpected request'); }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container);
    const flush = () => new Promise(resolve => setTimeout(resolve, 15));
    const select = async (id: string) => { await act(async () => {
        const select = container.querySelector<HTMLSelectElement>('[aria-label="Telemetry worker"]')!;
        select.value = id; select.dispatchEvent(new Event('change', { bubbles: true })); await flush();
    }); };
    const tab = async (label: string) => { await act(async () => {
        [...container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(b => b.textContent?.startsWith(label))!.click(); await flush();
    }); };
    try {
        await act(async () => { root.render(<QueryClientProvider client={client}><DashboardTelemetry /></QueryClientProvider>); await flush(); });
        expect(container.querySelector<HTMLSelectElement>('[aria-label="Telemetry worker"]')!.value).toBe('');
        expect(container.querySelectorAll('[role="tab"]')).toHaveLength(1);
        expect(reads).toEqual([]); expect(inventory).toEqual([]);
        await select('vast:1'); await tab('Vast');
        expect(reads).toEqual([{ id: 'vast:1', since: undefined }]);
        await select('vast:2');
        expect(reads.at(-1)).toEqual({ id: 'vast:2', since: undefined });
        await act(async () => { pending.get('vast:1')!(payload('vast:1')); await flush(); });
        expect(container.textContent).not.toContain('Vast instance 1');
        await act(async () => { pending.get('vast:2')!(payload('vast:2')); await flush(); });
        expect(container.textContent).toContain('Vast instance 2');
        expect(container.textContent).not.toContain('Vast instance 1');
        expect(inventory).toContain('/api/execution-targets/vast%3A1/runtime-inventory');
        expect(inventory).toContain('/api/execution-targets/vast%3A2/runtime-inventory');
        await tab('Combined');
        expect(container.textContent).toContain('Local fixture');
        expect(container.textContent).toContain('Vast instance 2');
        await act(async () => { void client.invalidateQueries({ queryKey: ['active-remote-gpu-telemetry', 'vast:2'] }); await flush(); });
        expect(reads.at(-1)).toEqual({ id: 'vast:2', since: 'cursor-vast:2' });
        await act(async () => { pending.get('vast:2')!(payload('vast:2')); await flush(); });
        await select('vast:1');
        expect(container.textContent).toContain('Vast instance 1');
        expect(container.textContent).not.toContain('Vast instance 2');
        await act(async () => { client.setQueryData(['execution-targets'], { data: [workers[1]] }); await flush(); });
        expect(container.textContent).not.toContain('Vast instance');
        expect(container.querySelector<HTMLSelectElement>('[aria-label="Telemetry worker"]')!.value).toBe('');
        expect(unexpected).toEqual([]);
    } finally {
        await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = oldAdapter;
    }
});
