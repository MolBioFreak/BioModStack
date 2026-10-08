import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';

const viewer = vi.hoisted(() => ({ props: [] as Array<Record<string, unknown>> }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: Record<string, unknown>) => {
    viewer.props.push(props);
    return <div data-format={String(props.format)} data-source={String(props.structureUrl)} />;
} }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingViewer', () => ({ ConformationalMappingViewer: () => null }));
import { JobDetailPage } from '../../src/components/JobDetailPage';

let root: Root | undefined;
let client: QueryClient | undefined;
afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    client?.clear();
    document.body.replaceChildren();
    vi.unstubAllGlobals();
    viewer.props = [];
});

async function mount(failListing = false) {
    const rows = [
        { engine: 'diffdock', complex_name: 'complex-a', name: 'rank1.sdf', artifact_path: 'run/diffdock/results/complex-a/rank1.sdf', format: 'sdf', confidence: -1.92, affinity: null },
        { engine: 'diffdock', complex_name: 'complex b', name: 'rank1.sdf', artifact_path: 'run/diffdock/results/complex b/rank1.sdf', format: 'sdf', confidence: -1.1, affinity: null },
        { engine: 'unidock', name: 'pose.pdb', artifact_path: 'run/unidock/filtered/pose.pdb', format: 'pdb', confidence: null, affinity: -8.2 },
    ];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
        if (url.endsWith('/docking-results')) return new Response(JSON.stringify({ sdfs: rows }), { status: failListing ? 500 : 200 });
        if (url === '/api/jobs/dock') return new Response(JSON.stringify({ id: 'dock', name: 'Docking test', model_id: 'docking', mode: 'dual_docking', status: 'completed', params: {}, created_at: '2026-09-01T00:00:00Z' }));
        throw new Error(`Unexpected request: ${url}`);
    }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root!.render(<QueryClientProvider client={client!}><MemoryRouter initialEntries={['/jobs/dock']}><Routes><Route path="/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter></QueryClientProvider>));
    return container;
}

it('selects exact complex/engine artifact and forwards the matching native parser', async () => {
    const container = await mount();
    await vi.waitFor(() => expect(container.querySelector('select')).not.toBeNull());
    const select = container.querySelector('select')!;
    expect(select.textContent).toContain('complex-a');
    expect(select.textContent).toContain('complex b');
    await act(async () => { select.value = '1'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(viewer.props.at(-1)).toMatchObject({ structureUrl: '/api/jobs/dock/docking-results/run/diffdock/results/complex%20b/rank1.sdf', format: 'sdf' });
    await act(async () => { select.value = '2'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(viewer.props.at(-1)).toMatchObject({ structureUrl: '/api/jobs/dock/docking-results/run/unidock/filtered/pose.pdb', format: 'pdb' });
});

it('does not render an unsuccessful listing as confirmed absence', async () => {
    const container = await mount(true);
    await vi.waitFor(() => expect(container.querySelector('[role=alert]')?.textContent).toContain('could not be loaded'));
    expect(container.textContent).not.toContain('No docking results found');
});
