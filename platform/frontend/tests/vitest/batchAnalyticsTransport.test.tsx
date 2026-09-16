import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it } from 'vitest';
import { BatchComparePane } from '../../src/components/BatchComparePane';
import { api } from '../../src/lib/api';

it('serializes the selected IDs as the router-declared list and renders the returned scalar comparison', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const oldAdapter = api.defaults.adapter;
    const requests: string[][] = [];
    const unexpected: string[] = [];
    api.defaults.adapter = async config => {
        let data;
        if (config.method === 'get' && config.url === '/api/jobs') {
            data = { jobs: [{ id: 'fixture', name: 'Fixture comparison', model_id: 'rfdiffusion3', mode: 'design', design_count: 2, created_at: '2026-01-01T00:00:00Z' }], total: 1 };
        } else if (config.method === 'post' && config.url === '/api/analytics/batch') {
            const ids = JSON.parse(config.data);
            expect(ids).toEqual(['fixture']);
            requests.push(ids);
            data = { job_ids: ids, metrics_summary: { plddt_overall: { fixture: 87 } }, common_metrics: ['plddt_overall'], scientific_cohorts: [] };
        } else { unexpected.push(`${config.method} ${config.url}`); throw new Error('Unexpected fixture transport'); }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const container = document.createElement('div'); document.body.append(container); const root = createRoot(container);
    const flush = () => new Promise(resolve => setTimeout(resolve, 15));
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><BatchComparePane /></QueryClientProvider>));
        await act(flush);
        await act(async () => {
            [...container.querySelectorAll('span')].find(node => node.textContent === 'Fixture comparison')!.click();
        });
        await act(flush);
        expect(requests).toEqual([['fixture']]);
        expect(container.querySelector('tbody')?.textContent).toContain('87.0');
        expect(unexpected).toEqual([]);
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = oldAdapter; }
});
