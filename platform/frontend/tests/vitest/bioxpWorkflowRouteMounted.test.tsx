import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import App from '../../src/App';

// Keep the real router and workflow page; unrelated shell services are outside
// this route test. The live browser qualifies the actual top-bar link.
vi.mock('../../src/components/Layout', () => ({ Layout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ GlobalExperimentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/components/molbio-ngs/NgsMolBioProjectHub', () => ({ default: () => null }));
vi.mock('../../src/runtime/installFeatures', () => ({ useResolvedBmsFeatures: () => ({ features: { bioxp: true }, resolved: true, known: true }) }));
let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); vi.restoreAllMocks(); });
it('opens the standalone workflow route directly without mounting the robot cockpit', async () => {
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    cleanup = async () => { await act(async () => root.unmount()); client.clear(); host.remove(); };
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/bioxp/workflows']}><App /></MemoryRouter></QueryClientProvider>));
    for (let i = 0; i < 50 && !host.querySelector('[aria-label="Saved workflow"]'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(host.querySelector('h1')?.textContent).toBe('BioXP Workflows');
    expect(host.querySelector('[aria-label="Saved workflow"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Step to append"]')?.closest('details')?.open).toBe(true);
    expect(host.textContent).not.toContain('Connect robot');
    expect([...host.querySelectorAll('button')].some(button => button.textContent?.endsWith(' now'))).toBe(false);
    expect(host.querySelector('a[href="/bioxp"]')?.textContent).toBe('Robot controls');
});
