import React from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const seen = vi.hoisted(() => ({ urls: [] as string[] }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({
    StructureWorkbench: ({ structureUrl }: { structureUrl: string }) => { seen.urls.push(structureUrl); return <div data-testid="wb">{structureUrl}</div>; },
}));
vi.mock('../../src/lib/api', async (orig) => ({
    ...(await orig<typeof import('../../src/lib/api')>()),
    fetchFullJob: vi.fn(async () => ({ id: 'job-1', name: 'RFD4 job', status: 'completed', model_id: 'rfd4', mode: 'unconditional' })),
    fetchJobs: vi.fn(async () => ({ data: { jobs: [] } })),
}));
import { QuickViewer, pickDefaultQuickViewerStructure } from '../../src/components/QuickViewer';

const file = (path: string, size = 1) => ({ name: path.split('/').pop()!.replace('.cif', ''), filename: path.split('/').pop()!, path, type: 'cif' as const, size_bytes: size });
const input = file('bms_results/j/collected/rfd4/prepared/native/r5_uncond.cif', 3020);
const design = file('bms_results/j/collected/rfd4/native/inference_samples/r5_uncond/r5_uncond_generated.cif', 120426);

let root: Root | undefined; let host: HTMLDivElement;
afterEach(async () => { await act(async () => root?.unmount()); host?.remove(); vi.unstubAllGlobals(); seen.urls = []; });

it('prefers a produced result over a staged prepared/ input but keeps every file', () => {
    expect(pickDefaultQuickViewerStructure([input, design])).toBe(design);
    expect(pickDefaultQuickViewerStructure([design, input])).toBe(design);
    expect(pickDefaultQuickViewerStructure([input])).toBe(input);
    expect(pickDefaultQuickViewerStructure([])).toBeNull();
    const a = file('x/a.cif'); const b = file('x/b.cif');
    expect(pickDefaultQuickViewerStructure([a, b])).toBe(a);
});

it('mounted Quick Viewer opens the generated design and still lists the input', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ structures: [input, design], count: 2 }) })));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { root!.render(<QueryClientProvider client={client}><QuickViewer selectedJobId="job-1" /></QueryClientProvider>); });
    for (let i = 0; i < 20 && !seen.urls.length; i++) await act(async () => { await new Promise(r => setTimeout(r, 25)); });
    expect(seen.urls[seen.urls.length - 1]).toBe(`/api/files/pdb/${design.path}`);
    const select = host.querySelectorAll('select')[1] as HTMLSelectElement;
    expect(select.value).toBe(design.path);
    expect([...select.options].map(o => o.value)).toEqual([input.path, design.path]);
});
