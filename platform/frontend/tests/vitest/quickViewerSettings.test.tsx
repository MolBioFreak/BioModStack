import React, { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import QuickViewer from '../../src/components/QuickViewer';
import { StandardStructureWorkbench } from '../../src/structureViewer/workbench/StandardStructureWorkbench';

const mocks = vi.hoisted(() => ({ mounts: vi.fn(), disposals: vi.fn(), volumes: vi.fn(), snapshots: vi.fn() }));
// Keep QuickViewer -> compact workbench -> host -> custom panel real; only the WebGL engine is replaced.
vi.mock('../../src/components/MolstarViewerImpl', () => ({
    default: () => {
        useEffect(() => { mocks.mounts(); return () => { mocks.disposals(); }; }, []);
        return <canvas data-test-engine />;
    },
}));
vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...await importOriginal<typeof import('../../src/lib/api')>(),
    fetchJobs: vi.fn(async () => ({ data: { jobs: [], total: 0 } })),
    fetchFullJob: vi.fn(async () => ({ id: 'job-a', model_id: 'boltz2', mode: 'structure_prediction' })),
    fetchViewerVolumes: mocks.volumes,
    fetchViewerSnapshots: mocks.snapshots,
}));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let fullscreenElement: Element | null;
const settings = () => container.querySelector<HTMLButtonElement>('button[aria-label="Quick Viewer settings"]')!;
const panel = () => container.querySelector<HTMLElement>('aside[aria-label="Structure reproducibility workbench"]')!;
const click = async (element: HTMLElement) => { await act(async () => element.click()); };
async function renderQuick(jobId: string | null = 'job-a') {
    await act(async () => root.render(<QueryClientProvider client={client}><QuickViewer selectedJobId={jobId}/></QueryClientProvider>));
    if (jobId) await vi.waitFor(() => expect(panel()).not.toBeNull());
}

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    fullscreenElement = null;
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullscreenElement });
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: vi.fn(async function(this: HTMLElement) {
        fullscreenElement = this; document.dispatchEvent(new Event('fullscreenchange'));
    }) });
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: vi.fn(async () => {
        fullscreenElement = null; document.dispatchEvent(new Event('fullscreenchange'));
    }) });
    mocks.volumes.mockResolvedValue({ data: { volumes: [], segmentations: [], registrations: [] } });
    mocks.snapshots.mockResolvedValue({ data: { snapshots: [] } });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({
        structures: [{ path: 'fixture.cif', type: 'cif', name: 'fixture', size_bytes: 20 }], count: 1,
    }) })));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount());
    client.clear(); container.remove(); localStorage.clear();
    vi.unstubAllGlobals(); vi.restoreAllMocks();
    delete (HTMLElement.prototype as Partial<HTMLElement>).requestFullscreen;
    delete (document as Partial<Document>).exitFullscreen;
    Reflect.deleteProperty(document, 'fullscreenElement');
});

it.each(['micro', 'compact', 'standard', 'large', 'xlarge'])('custom settings are collapsed by default at %s size and toggle without resetting the panel or engine', async (size) => {
    localStorage.setItem('bms_dashboard_quick_viewer_compact_v1', size);
    await renderQuick();
    const originalPanel = panel();
    const canvas = container.querySelector('canvas');
    expect(originalPanel.hidden).toBe(true);
    expect(settings().getAttribute('aria-expanded')).toBe('false');
    expect(mocks.mounts).toHaveBeenCalledTimes(1);
    await click(settings());
    expect(panel().hidden).toBe(false);
    expect(settings().getAttribute('aria-expanded')).toBe('true');
    const input = panel().querySelector('input')!;
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Keep this draft');
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(settings());
    expect(panel().hidden).toBe(true);
    await click(settings());
    expect(panel()).toBe(originalPanel);
    expect(panel().querySelector('input')!.value).toBe('Keep this draft');
    expect(container.querySelector('canvas')).toBe(canvas);
    expect(mocks.mounts).toHaveBeenCalledTimes(1);
    expect(mocks.disposals).not.toHaveBeenCalled();
    expect(mocks.volumes).toHaveBeenCalledTimes(1);
    expect(mocks.snapshots).toHaveBeenCalledTimes(1);
});

it('size and fullscreen transitions retain the explicit settings choice; a new Quick Viewer starts collapsed', async () => {
    await renderQuick();
    for (const title of ['Very compact viewer', 'Compact viewer', 'Standard viewer', 'Large viewer', 'Maximum viewer size', 'Open fullscreen', 'Exit fullscreen']) {
        await click(container.querySelector<HTMLButtonElement>(`button[title="${title}"]`)!);
        expect(panel().hidden).toBe(true);
    }
    await click(settings());
    await click(container.querySelector<HTMLButtonElement>('button[title="Open fullscreen"]')!);
    expect(panel().hidden).toBe(false);
    await click(container.querySelector<HTMLButtonElement>('button[title="Exit fullscreen"]')!);
    expect(panel().hidden).toBe(false);
    await act(async () => root.render(null));
    await renderQuick();
    expect(panel().hidden).toBe(true);
});

it('settings are unavailable before structure selection, and full results workbench defaults are unchanged', async () => {
    await renderQuick(null);
    expect(settings().disabled).toBe(true);
    expect(container.querySelector('aside')).toBeNull();
    await act(async () => root.render(<StandardStructureWorkbench structureUrl="/fixture.cif" jobId="job-a"/>));
    const standardPanel = container.querySelector<HTMLElement>('aside[aria-label="Structure metric workbench"]')!;
    expect(standardPanel).not.toBeNull();
    expect(standardPanel.hidden).toBe(false);
    expect(standardPanel.textContent).toContain('Reproducibility, volumes, and exports');
});
