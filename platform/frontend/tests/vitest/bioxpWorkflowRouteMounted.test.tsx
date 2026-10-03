import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../../src/App';
import { api } from '../../src/lib/api';

// Keep the real router, robot workspace and workflow editor. The live browser
// also qualifies the actual BMS navigation, which has only one robot entry.
vi.mock('../../src/components/Layout', () => ({ Layout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ GlobalExperimentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/components/molbio-ngs/NgsMolBioProjectHub', () => ({ default: () => null }));
vi.mock('../../src/runtime/installFeatures', () => ({ useResolvedBmsFeatures: () => ({ features: { bioxp: true }, resolved: true, known: true }) }));
let cleanup: (() => Promise<void>) | undefined;
const adapter = api.defaults.adapter;
afterEach(async () => { await cleanup?.(); api.defaults.adapter = adapter; vi.restoreAllMocks(); });
it.each(['/bioxp', '/bioxp/workflows'])('opens saved authoring inside the robot workspace from %s', async path => {
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const requests: Array<{ method: string | undefined; url: string | undefined }> = [];
    const authoringReads = ['/api/bioxp/methods/catalog', '/api/bioxp/methods/schema', '/api/bioxp/methods/examples', '/api/bioxp/methods/library', '/api/bioxp/methods/presets'];
    api.defaults.adapter = async config => {
        requests.push({ method: config.method, url: config.url });
        if (config.method === 'get' && authoringReads.includes(config.url ?? '')) return { data: {}, status: 200, statusText: 'OK', config, headers: {} };
        if (config.method !== 'get' || config.url !== '/api/bioxp/status') throw new Error('Unexpected robot request');
        return { data: { connection: { active: false, configured: false, generation: 0 } }, status: 200, statusText: 'OK', config, headers: {} };
    };
    cleanup = async () => { await act(async () => root.unmount()); client.clear(); host.remove(); };
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>));
    for (let i = 0; i < 50 && !host.querySelector('#control-tab-workflows'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(host.querySelector('h1')?.textContent).toBe('BioXP 3200');
    const tab = host.querySelector('#control-tab-workflows') as HTMLButtonElement;
    expect(tab.textContent).toBe('Workflows');
    expect(tab.getAttribute('aria-selected')).toBe(String(path.endsWith('/workflows')));
    if (path === '/bioxp') await act(async () => tab.click());
    const panel = host.querySelector('#control-panel-workflows') as HTMLElement;
    expect(panel.hidden).toBe(false);
    expect(panel.querySelector('[aria-label="Saved workflow"]')).not.toBeNull();
    expect(panel.querySelector('[aria-label="Step to append"]')?.closest('[aria-label="Step settings"]')).not.toBeNull();
    expect([...panel.querySelectorAll('button')].some(button => button.textContent?.endsWith(' now'))).toBe(false);
    expect(panel.textContent).toContain('Editing does not send robot commands.');
    expect(host.querySelector('#control-panel-pipettes [aria-label="Saved workflow"]')).toBeNull();
    expect(requests.every(request => request.method === 'get' && (request.url === '/api/bioxp/status' || authoringReads.includes(request.url ?? '')))).toBe(true);
});
