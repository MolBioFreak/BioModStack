import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { StatsToolkitLauncher } from '../../src/components/StatsToolkitLauncher';
import { MobilePreflightSettings } from '../../src/components/MobilePreflightSettings';
import { ThemeContext, THEMES } from '../../src/components/themeContext';
import { BMS_STATS_THEME_TOKEN_NAMES } from '../../src/runtime/statsToolkitThemeBridge';
import { MemoryRouter } from 'react-router-dom';
import { Layout } from '../../src/components/Layout';
import { api } from '../../src/lib/api';

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let requests: ReturnType<typeof vi.fn>;
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const status = { available: true, ready: null, entry_url: '/stats/embed/', detail: 'database readiness not assessed' };
beforeEach(() => {
    for (const token of BMS_STATS_THEME_TOKEN_NAMES) document.documentElement.style.setProperty(token, '#123456');
    container = document.createElement('div'); document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    requests = vi.fn(async () => new Response(JSON.stringify(status), { status: 200 }));
    vi.stubGlobal('fetch', requests);
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(cb, 0));
    vi.stubGlobal('cancelAnimationFrame', clearTimeout);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); document.documentElement.classList.remove('bms-cordova-compact'); vi.useRealTimers(); vi.unstubAllGlobals(); });
async function mountStats() {
    await act(async () => root.render(<QueryClientProvider client={client}><ThemeContext.Provider value={{ theme: 'midnight', setTheme: () => {}, themeConfig: THEMES[0] }}><StatsToolkitLauncher /></ThemeContext.Provider></QueryClientProvider>));
    await flush();
}
it.each([null, false])('opens workspace without a readiness gate (%s), with no recurring browsing reads', async (ready) => {
    requests.mockImplementation(async () => new Response(JSON.stringify({ ...status, ready }), { status: 200 }));
    await mountStats();
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe('http://127.0.0.1:18180/stats/');
    expect(requests.mock.calls.map(([url]) => url)).toEqual(['/api/system/stats-toolkit']);
    vi.useFakeTimers();
    await act(async () => { await vi.advanceTimersByTimeAsync(46_000); window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')); });
    expect(requests).toHaveBeenCalledTimes(1);
});
it('keeps the actual error and explicit retry, then opens workspace', async () => {
    requests.mockResolvedValueOnce(new Response('', { status: 503 }));
    await mountStats();
    expect(container.textContent).toContain('status probe failed (503)');
    expect(container.querySelector('iframe')).toBeNull();
    await act(async () => container.querySelector('button')!.click()); await flush();
    expect(requests).toHaveBeenCalledTimes(2);
    expect(container.querySelector('iframe')).not.toBeNull();
});
it('retains unavailable-service detail without fabricated readiness', async () => {
    requests.mockResolvedValueOnce(new Response(JSON.stringify({ ...status, available: false, detail: 'standalone probe failed: OSError' })));
    await mountStats();
    expect(container.textContent).toContain('standalone probe failed: OSError');
    expect(container.querySelector('iframe')).toBeNull();
});
it('has no issue overlay, collection requests or body observer; mobile Settings still reaches its preflight owner', async () => {
    const observe = vi.spyOn(MutationObserver.prototype, 'observe');
    await act(async () => root.render(<MobilePreflightSettings />));
    await flush();
    expect(container.textContent).toBe('');
    expect(requests).not.toHaveBeenCalled();
    expect(observe.mock.calls.some(([target]) => target === document.body)).toBe(false);
    const preflight = document.createElement('button'); preflight.id = 'bms-cordova-preflight-toggle';
    const click = vi.fn(); preflight.onclick = click; document.body.append(preflight);
    await act(async () => document.documentElement.classList.add('bms-cordova-compact')); await flush();
    expect(container.textContent).toContain('Settings');
    expect(container.textContent).not.toContain('Issues');
    await act(async () => container.querySelector('button')!.click());
    expect(click).toHaveBeenCalledTimes(1);
    expect(requests).not.toHaveBeenCalled();
    preflight.remove(); observe.mockRestore();
});
it('normal mounted Layout has no issue reads or subtree observation during unrelated updates', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    Element.prototype.scrollIntoView = vi.fn();
    const observe = vi.spyOn(MutationObserver.prototype, 'observe');
    const oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => ({ data: { features: {} }, status: 200, statusText: 'OK', headers: {}, config });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/stats']}><ThemeContext.Provider value={{ theme: 'midnight', setTheme: () => {}, themeConfig: THEMES[0] }}><Layout><span>workspace remains open</span></Layout></ThemeContext.Provider></MemoryRouter></QueryClientProvider>));
        await flush();
        const unrelated = document.createElement('span'); document.body.append(unrelated); unrelated.textContent = 'chart update'; await flush(); unrelated.remove();
        expect(container.textContent).toContain('workspace remains open');
        expect(container.querySelector('[data-bms-dev-issues-trigger]')).toBeNull();
        expect(requests.mock.calls.some(([url]) => String(url).includes('/api/dev/issues'))).toBe(false);
        expect(observe.mock.calls.some(([target, options]) => target === document.body && options?.subtree)).toBe(false);
    } finally { api.defaults.adapter = oldAdapter; observe.mockRestore(); }
});
