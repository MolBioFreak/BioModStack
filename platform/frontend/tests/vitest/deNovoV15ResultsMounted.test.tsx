import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';

vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: object) => <div data-testid="viewer">{JSON.stringify(props)}</div> }));
vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
const adapter = api.defaults.adapter;
let unexpected: string[];
beforeEach(() => {
    unexpected = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No live network'); });
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No live network'); });
    api.defaults.adapter = async config => { unexpected.push(`${config.method} ${config.url}`); throw new Error('Missing inert HTTP fixture'); };
    window.history.replaceState({}, '', '/designs/fixture-job');
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); api.defaults.adapter = adapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); expect(unexpected).toEqual([]); });
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
// The Results workspace and its native header read arrive through lazy chunks; wait on the clock, not on a tick count.
const until = async (done: () => boolean, ms = 110000) => { const end = Date.now() + ms; while (!done() && Date.now() < end) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); }); };
const mount = async (element: React.ReactNode) => { await act(async () => root.render(<MemoryRouter initialEntries={['/designs/fixture-job']}><QueryClientProvider client={client}><ThemeProvider>{element}</ThemeProvider></QueryClientProvider></MemoryRouter>)); await flush(); };

it.each(['rfd4_proteina', 'complexa'] as const)('names the published %s record count in the real ResultsViewer header, not zero Design rows', async model => {
    const job = { id: 'fixture-job', name: 'TEST native publication', model_id: model, mode: 'generate', status: 'completed', params: {}, design_count: 0,
        provenance: { native_generation_publication: { contract: `${model}_v1` } }, created_at: '2026-01-01T00:00:00Z' };
    const totals: number[] = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith('/generation-results')) {
            totals.push(Number((config.params as { limit?: number } | undefined)?.limit));
            data = { records: [], total: 17, offset: 0, limit: 1, publication: { contract: `${model}_v1` }, receipt: {}, model_id: model, owner_model_id: model };
        } else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>);
    await until(() => !!container.textContent?.includes('17 generated candidates'));
    expect(container.textContent).toContain('17 generated candidates');
    expect(container.textContent).not.toContain('0 designs');
    expect(totals).toContain(1);   // the header reads only the total, one record
}, 120000);

it('counts generated molecules, not reward and timing rows, in the published native header', async () => {
    const job = { id: 'fixture-job', name: 'TEST native publication', model_id: 'complexa', mode: 'generate', status: 'completed', params: {}, design_count: 0,
        provenance: { native_generation_publication: { contract: 'complexa_v1' } }, created_at: '2026-01-01T00:00:00Z' };
    api.defaults.adapter = async config => {
        const url = String(config.url);
        let data: unknown = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/fixture-job') data = job;
        else if (url.endsWith('/generation-results')) data = { records: [], total: 5, offset: 0, limit: 1, publication: { contract: 'complexa_v1' }, receipt: {},
            record_counts: { total: 5, observations: 3, molecular_records: 2, populations: { generation: 2, observation: 3 } }, model_id: 'complexa', owner_model_id: 'complexa' };
        else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.includes('/backbones')) data = { backbones: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>);
    await until(() => !!container.textContent?.includes('2 generated candidates'));
    expect(container.textContent).toContain('2 generated candidates');
    expect(container.textContent).not.toContain('5 generated candidates');
}, 120000);
