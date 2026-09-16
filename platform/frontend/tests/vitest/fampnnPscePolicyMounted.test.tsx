import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import StructureViewerPane from '../../src/components/StructureViewerPane';
import { ThemeProvider } from '../../src/components/ThemeProvider';

vi.mock('react-plotly.js', () => ({ default: () => null }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: () => <div>TEST structure renderer</div> }));
vi.mock('../../src/components/ReferenceSelector', () => ({ default: () => null }));
vi.mock('../../src/components/ChainDetailsPanel', () => ({ default: () => null }));
vi.mock('../../src/lib/api', async (original) => ({ ...(await original<any>()), fetchViewerVolumes: async () => ({ volumes: [] }) }));
let root: Root;
let container: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); container?.remove(); });

it.each(['A', 'all_chains', null])('shows retained pSCE scope %s without inventing historical scope', (scope) => {
    const run = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const design = { id: 'test', name: 'TEST FAMPNN', job_id: 'test-job', stage_family: 'fampnn', fampnn_psce: 4, pdb_path: 'test.pdb' } as any;
    const profile = { design_id: 'test', design_name: 'TEST', metric_kind: 'fampnn_psce', direction: 'lower_is_better',
        scope, ignore_cbeta: scope ? true : null, chains: {}, status: scope ? 'ok' : 'unavailable',
        policy: scope ? { version: 1, chain_id: scope, ignore_cbeta: true } : null } as any;
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    act(() => root.render(<ThemeProvider><QueryClientProvider client={client}><StructureViewerPane selectedDesignId="test" setSelectedDesignId={() => {}}
        designs={[design]} selectedDesign={design} colorMode="default" setColorMode={() => {}} structureFormat="pdb"
        activeJob={null} getMetricColor={() => ''} viewerAnalyses={{ fampnnPsceProfile: profile, onRunFampnnPsceProfile: run,
            structureAnalysis: { residue_count: 3, chain_ids: ['A', 'B'] } as any }} /></QueryClientProvider></ThemeProvider>));
    expect(container.textContent).toMatch(scope ? new RegExp(`pSCE policy v1.*${scope === 'A' ? 'Chain A' : 'All chains'}.*Cβ excluded`) : /Historical pSCE policy unknown/);
    if (scope === null) {
        const chain = container.querySelector<HTMLInputElement>('input[aria-label="pSCE chain scope"]')!;
        const cbeta = container.querySelector<HTMLSelectElement>('select[aria-label="pSCE C-beta policy"]')!;
        const button = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Analyze pSCE with selected policy')!;
        expect(button.disabled).toBe(true);
        act(() => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(chain, 'B');
            chain.dispatchEvent(new Event('input', { bubbles: true }));
        });
        expect(button.disabled).toBe(true);
        act(() => { cbeta.value = 'include'; cbeta.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(button.disabled).toBe(false);
        act(() => button.click());
        expect(run).toHaveBeenLastCalledWith({ chain_id: 'B', ignore_cbeta: false });
        act(() => { cbeta.value = 'exclude'; cbeta.dispatchEvent(new Event('change', { bubbles: true })); });
        act(() => button.click());
        expect(run).toHaveBeenLastCalledWith({ chain_id: 'B', ignore_cbeta: true });
        act(() => [...container.querySelectorAll('button')].find((button) => button.textContent === 'Use recorded pSCE policy')!.click());
        expect(run).toHaveBeenLastCalledWith({});
    }
    client.clear();
});
