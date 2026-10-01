import React, { act } from 'react';
import { writeFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { useBioXpOperatorReceiptV2, useBioXpOperatorReceiptDetailV2 } from '../../src/lib/bioxpClient';
import park from '../fixtures/bioxp_park_completed_receipt.json';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let root: Root; let host: HTMLDivElement; let client: QueryClient; let current: any;
let responses: Array<{ detail: boolean; bytes: number; at: number }> = [];
function Status() { const query = useBioXpOperatorReceiptV2(current.command_id, 7); return <output>{query.data?.status}</output>; }
function Detail() { const query = useBioXpOperatorReceiptDetailV2(current.command_id, 7); return <aside>{query.data?.deck_movement?.source_branch}</aside>; }
async function tick(ms = 10) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function render(detail = false) { await act(async () => root.render(<QueryClientProvider client={client}><Status />{detail && <Detail />}</QueryClientProvider>)); await tick(); }
const calls = (detail: boolean) => vi.mocked(api.get).mock.calls.filter(([, options]) => options?.params?.detail === detail).length;
beforeEach(() => {
    vi.useFakeTimers(); vi.mocked(api.get).mockReset(); responses = [];
    current = { ...park, status: 'dispatched', terminal: false, state_version: 1, terminal_receipt_id: null };
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (_url, options) => {
        const { source_receipt, deck_movement, raw_return_layers, ...compact } = current;
        const data = structuredClone(options?.params?.detail ? current : compact);
        responses.push({ detail: options?.params?.detail === true, bytes: new TextEncoder().encode(JSON.stringify(data)).length, at: Date.now() });
        return { data };
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.useRealTimers(); });
it('polls compact status only; typed detail loads once per native state revision', async () => {
    await render(); await tick(2500);
    expect(calls(false)).toBeGreaterThan(1); expect(calls(true)).toBe(0);
    await render(true); await tick();
    expect(calls(true)).toBe(1);
    await tick(2500); expect(calls(true)).toBe(1);
    current = { ...park, state_version: 2 };
    await tick(510); await tick();
    expect(calls(true)).toBe(2); expect(host.textContent).toContain('completed');
    const settled = calls(false); await tick(5000);
    expect(calls(false)).toBe(settled); expect(calls(true)).toBe(2);
    if (process.env.BMS_CONSUMER_FINISH_METRICS) writeFileSync(process.env.BMS_CONSUMER_FINISH_METRICS, JSON.stringify({
        scope: 'mounted mocked-transport response UTF-8 JSON bytes, no compression; not robot traffic or latency',
        compactRequests: calls(false), detailRequests: calls(true), settledWindowMs: 5000, settledWindowRequests: calls(false) - settled,
        responses,
    }, null, 2));
});
it('retains ambiguous reconciliation cadence without eager evidence or retries of the action', async () => {
    current = { ...current, terminal: true, status: 'ambiguous', completion_class: 'recovery_required' };
    await render(); const start = calls(false); await tick(6000);
    expect(calls(false)).toBeGreaterThan(start); expect(calls(true)).toBe(0); expect(api.post).not.toHaveBeenCalled();
});
