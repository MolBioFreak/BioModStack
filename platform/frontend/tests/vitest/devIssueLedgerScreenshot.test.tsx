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
import { buildBioXpCameraMjpegUrl } from '../../src/lib/bioxpClient';
import { resolveApiResourceUrl } from '../../src/runtime/tailnetEnvironment';
import { NgsDataRecords } from '../../src/components/project-manager/NgsDataRecords';

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
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); document.documentElement.classList.remove('bms-cordova-compact'); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); localStorage.removeItem('show_dev_features'); });
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
it('packaged Stats uses the configured server origin and keeps exact theme-origin targeting', async () => {
    vi.stubGlobal('__BMS_API_BASE_URL__', 'https://node.example.ts.net');
    await mountStats();
    const frame = container.querySelector('iframe')!;
    expect(frame.getAttribute('src')).toBe('https://node.example.ts.net/stats/embed/?ui=66882da');
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    await act(async () => frame.dispatchEvent(new Event('load'))); await flush();
    expect(post).toHaveBeenCalledWith(expect.any(Object), 'https://node.example.ts.net');
    expect(requests.mock.calls.map(([url]) => url)).toEqual(['/api/system/stats-toolkit']);
});

it.each([
    ['development', null, true, true],
    ['production', null, true, false],
    ['development', 'false', true, false],
    ['production', 'true', true, true],
    ['unavailable', null, true, false],
    ['unavailable', 'true', true, true],
    ['development', 'true', false, false],
] as const)('packaged Layout uses selected %s, preference %s, installed %s for visible BioXP %s', async (environment, preference, installed, visible) => {
    vi.stubEnv('MODE', 'production');
    vi.stubGlobal('__BMS_API_BASE_URL__', 'https://node.example.ts.net');
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    Element.prototype.scrollIntoView = vi.fn();
    if (preference !== null) localStorage.setItem('show_dev_features', preference);
    requests.mockImplementation(async (url: string) => {
        if (url === '/api/system/features') return new Response(JSON.stringify({ features: { bioxp: installed }, dev_features: { bioxp: true } }));
        if (url === '/api/tailnet-environment/status') return environment === 'unavailable'
            ? new Response('', { status: 503 }) : new Response(JSON.stringify({ selected_environment: environment }));
        throw new Error('Unexpected request: ' + url);
    });
    const oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><ThemeContext.Provider value={{ theme: 'midnight', setTheme: () => {}, themeConfig: THEMES[0] }}><Layout><span>workspace remains open</span></Layout></ThemeContext.Provider></MemoryRouter></QueryClientProvider>));
        await flush();
        await vi.waitFor(() => expect(container.querySelector('a[href="/bioxp"]') !== null).toBe(visible));
        expect(container.textContent).toContain('workspace remains open');
        expect(localStorage.getItem('show_dev_features')).toBe(preference);
        expect(requests.mock.calls.filter(([url]) => url === '/api/tailnet-environment/status')).toHaveLength(1);
        vi.useFakeTimers();
        await act(async () => { await vi.advanceTimersByTimeAsync(61_000); window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')); });
        expect(requests.mock.calls.filter(([url]) => url === '/api/tailnet-environment/status')).toHaveLength(1);
        expect(container.querySelector('a[href="/bioxp"]') !== null).toBe(visible);
        expect(requests.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
    } finally { api.defaults.adapter = oldAdapter; }
});

it('React raw image and file-link resources use the server without any camera mutation', async () => {
    vi.stubGlobal('__BMS_API_BASE_URL__', 'https://node.example.ts.net');
    await act(async () => root.render(<>
        <img alt="camera transport fixture" src={buildBioXpCameraMjpegUrl(77, 'stream/one')} />
        <a href={resolveApiResourceUrl('/api/files/download/report%20one.csv')} download>Download fixture</a>
    </>));
    expect(container.querySelector('img')?.getAttribute('src')).toBe('https://node.example.ts.net/api/bioxp/camera/mjpeg?expected_generation=77&stream_id=stream%2Fone');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://node.example.ts.net/api/files/download/report%20one.csv');
    expect(requests).not.toHaveBeenCalled();
});

it('packaged runtime switching still uses the remote selector when status observation fails', async () => {
    vi.stubGlobal('__BMS_API_BASE_URL__', 'https://node.example.ts.net');
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    Element.prototype.scrollIntoView = vi.fn();
    requests.mockImplementation(async (url: string, options?: RequestInit) => {
        if (url === '/api/system/features') return new Response(JSON.stringify({ features: { bioxp: true }, dev_features: { bioxp: true } }));
        if (url === '/api/health') return new Response(JSON.stringify({ status: 'healthy', build: { revision: 'a'.repeat(40) } }));
        if (url === '/api/tailnet-environment/status') return new Response('', { status: 503 });
        // Stop at the actual mutation boundary; do not navigate or change a live target.
        if (url === '/api/tailnet-environment/select' && options?.method === 'POST') return new Response(JSON.stringify({ detail: 'inert selector stop' }), { status: 409 });
        throw new Error('Unexpected request: ' + url);
    });
    const oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><ThemeContext.Provider value={{ theme: 'midnight', setTheme: () => {}, themeConfig: THEMES[0] }}><Layout><span>workspace remains open</span></Layout></ThemeContext.Provider></MemoryRouter></QueryClientProvider>));
        await flush();
        await act(async () => container.querySelector<HTMLButtonElement>('button[title="Diagnostics"]')!.click()); await flush();
        const button = Array.from(container.querySelectorAll('button')).find(b => b.textContent === 'Switch to Development')!;
        expect(button.disabled).toBe(false);
        await act(async () => button.click()); await flush();
        const calls = requests.mock.calls.filter(([url]) => url === '/api/tailnet-environment/select');
        expect(calls).toHaveLength(1);
        expect(JSON.parse(calls[0][1].body)).toEqual({ environment: 'development' });
        expect(container.textContent).toContain('inert selector stop');
        expect(requests.mock.calls.some(([url]) => url === '/api/system/runtime-ports')).toBe(false);
    } finally { api.defaults.adapter = oldAdapter; }
});

it('Project NGS file links retain encoded server paths in the downloaded phone UI', async () => {
    vi.stubGlobal('__BMS_API_BASE_URL__', 'https://node.example.ts.net');
    const oldAdapter = api.defaults.adapter;
    const seen: string[] = [];
    api.defaults.adapter = async config => {
        seen.push(`${config.method} ${config.url}`);
        return { data: { schema: 'bns.ngs-data-record-list.v1', next_cursor: null, items: [{ id: 'transport-fixture', created_at: '2025-01-01T00:00:00Z', author: null, payload: {
            schema: 'bns.ngs-data-record.v1', record_key: 'fixture', title: 'Transport fixture', sample_name: '', data_format: 'fastq', use_case: 'sequence_identity', workflow_stage: 'acquired', data_location: '', notes: '', files: [{ name: 'read one.fastq', path: 'ngs folder/read one.fastq', size: 10 }],
        } }] }, status: 200, statusText: 'OK', headers: {}, config };
    };
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><NgsDataRecords projectId="project fixture" onAdd={vi.fn()} /></QueryClientProvider>));
        await flush();
        expect(container.querySelector('a')?.getAttribute('href')).toBe('https://node.example.ts.net/api/files/download/ngs%20folder/read%20one.fastq');
        expect(seen).toEqual(['get /api/projects/project%20fixture/ngs-data-records']);
    } finally { api.defaults.adapter = oldAdapter; }
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
