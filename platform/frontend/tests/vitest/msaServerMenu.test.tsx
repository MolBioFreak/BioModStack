import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CanceledError, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MSAServerSettingsMenu } from '../../src/components/Layout';
import { api } from '../../src/lib/api';

const settings = { include_envdb_on_start: false, auto_stop_idle_enabled: true, auto_stop_idle_minutes: 10, pinned_gpu_id: null };
const oldAdapter = api.defaults.adapter;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let client: QueryClient;
let requests: InternalAxiosRequestConfig[];
let hang: boolean;
let failStatus: boolean;
let aborted: number;
let serverRunning: boolean;

const flush = async (milliseconds = 1) => { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); };
const button = (label: string) => [...container.querySelectorAll('button')].find(node => node.textContent?.trim() === label)!;
const click = async (label: string) => { await act(async () => button(label).click()); await flush(); };

beforeEach(async () => {
    vi.useFakeTimers();
    requests = []; hang = false; failStatus = false; aborted = 0; serverRunning = false;
    api.defaults.adapter = async config => {
        expect(config.method).toBe('get');
        expect(['/api/msa/server/status', '/api/msa/server/settings', '/api/gpu/gpus']).toContain(config.url);
        requests.push(config);
        // Deliberately omit adapter timeout delivery to test query single-flight even
        // when a transport fails to settle. Real transports receive the 10s timeout.
        if (hang) await new Promise((_resolve, reject) => {
            config.signal!.addEventListener!('abort', () => { aborted += 1; reject(new CanceledError()); }, { once: true });
        });
        if (config.url === '/api/msa/server/status' && failStatus) throw new Error('fixture status unavailable');
        const data = config.url === '/api/msa/server/status'
            ? { running: serverRunning, all_running: serverRunning, settings, servers: [] }
            : config.url === '/api/msa/server/settings' ? { settings } : { gpus: [{ index: 0, name: 'Fixture GPU' }] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Unexpected mutation'); }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    await act(async () => root.render(<QueryClientProvider client={client}><MSAServerSettingsMenu /></QueryClientProvider>));
    await flush();
});

afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); container.remove();
    api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); vi.useRealTimers();
});

it('keeps stalled polling single-flight, bounds transport time, and aborts on close/unmount', async () => {
    expect(requests).toHaveLength(0);
    hang = true;
    await click('MSA SERVER');
    expect(requests).toHaveLength(3);
    expect(requests.every(request => request.timeout === 10000 && request.signal)).toBe(true);
    await flush(60000);
    expect(requests).toHaveLength(3);
    await click('MSA SERVER');
    expect(aborted).toBe(3);
    await flush(30000);
    expect(requests).toHaveLength(3);
    await click('MSA SERVER');
    expect(requests).toHaveLength(6);
    await act(async () => root.render(<QueryClientProvider client={client}><div /></QueryClientProvider>));
    expect(aborted).toBe(6);
    expect(fetch).not.toHaveBeenCalled();
});

it('refreshes each section at ten seconds and recovers status independently of settings and GPUs', async () => {
    failStatus = true;
    await click('MSA SERVER');
    expect(requests).toHaveLength(3);
    expect(container.querySelector('select')?.textContent).toContain('Fixture GPU');
    expect(container.querySelector<HTMLInputElement>('input[type="number"]')?.value).toBe('10');
    failStatus = false; serverRunning = true;
    await flush(10001);
    expect(requests).toHaveLength(6);
    expect(container.querySelector('textarea')?.value).toContain('Running: yes');
    expect(fetch).not.toHaveBeenCalled();
});

it('manual refresh cancels preceding reads without accumulating requests', async () => {
    hang = true;
    await click('MSA SERVER');
    await click('Refresh');
    expect(aborted).toBe(3);
    expect(requests).toHaveLength(6);
    await flush(30000);
    expect(requests).toHaveLength(6);
    expect(fetch).not.toHaveBeenCalled();
});

it('reconciles optimistic settings against a fresh unchanged server response', async () => {
    await click('MSA SERVER');
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
        expect(url).toBe('/api/msa/server/settings'); expect(options.method).toBe('PUT');
        return { ok: false, status: 400 } as Response;
    }));
    await flush(5);
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(6);
    expect(container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(false);
});

it('does not restart polling when a settings save completes after the menu closes', async () => {
    await click('MSA SERVER');
    let finishSave!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
        expect(url).toBe('/api/msa/server/settings'); expect(options.method).toBe('PUT');
        return new Promise<Response>(resolve => { finishSave = resolve; });
    }));
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    await click('MSA SERVER');
    await act(async () => finishSave({ ok: true } as Response));
    await flush(30000);
    expect(requests).toHaveLength(3);
    await click('MSA SERVER');
    expect(requests).toHaveLength(6);
});
