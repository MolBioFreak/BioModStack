import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync, writeFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpWorkflowJobMonitor } from '../../src/components/BioXpWorkflowJobMonitor';

// Native SQLite projection -> real BMS HTTP receiving -> actual Axios adapter.
// This is capture replay, not measured live bandwidth or physical execution.
const path = process.env.BIOXP_OBSERVATION_RECEIVING;
const evidence = path ? JSON.parse(readFileSync(path, 'utf8')) : null;
const originalAdapter = api.defaults.adapter;
let root: Root, host: HTMLDivElement, client: QueryClient;
let index: number, rows: any[], requests: { at: number; bytes: number; params: any; url: string }[];
let visible: boolean, connected: boolean, generation: number;
let fail: boolean;
const busyRef = { current: false };
async function tick(ms = 20) { await act(async () => vi.advanceTimersByTimeAsync(ms)); }
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}>
        <BioXpWorkflowJobMonitor jobId={evidence.job_id} generation={generation} connected={connected}
            controlsEnabled visible={visible} busyRef={busyRef} onBusyChange={() => {}} />
    </QueryClientProvider>));
    await tick();
}
beforeEach(() => {
    vi.useFakeTimers();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    index = 0; requests = []; visible = true; connected = true; generation = 7; fail = false;
    api.defaults.adapter = async config => {
        expect(config.method).toBe('get');
        expect(config.url).toBe(`/api/bioxp/protocols/jobs/${evidence.job_id}`);
        expect(config.params.observation).toBe(true);
        const data = rows[Math.min(index, rows.length - 1)]; index++;
        const body = JSON.stringify(data);
        requests.push({ at: Date.now(), bytes: Buffer.byteLength(body), params: config.params, url: config.url! });
        if (fail) throw new Error('inert read failure');
        return { data: body, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); host.remove();
    api.defaults.adapter = originalAdapter; vi.useRealTimers();
});
it.skipIf(!evidence).each(['idle', 'changing'])('retains two-second %s observation for a full minute at >=95%% reduction', async scenario => {
    rows = evidence.windows[scenario].rows;
    await render();
    expect(requests).toHaveLength(1);
    const cold = requests[0].bytes;
    const recurringStart = requests.length;
    for (let poll = 0; poll < 30; poll++) {
        await tick(2000);
        const row = rows[Math.min(index - 1, rows.length - 1)];
        const workflow = row.execution.runtime_state.workflow;
        expect(host.textContent).toContain(`Robot status: ${row.command.status} · Phase: ${workflow.phase}`);
        expect(host.textContent).toContain(`Source occurrence: ${workflow.source_occurrence_id ?? 'none'}`);
        expect(host.textContent).toContain(`State version: ${row.command.state_version}`);
        if (workflow.held_reason) expect(host.textContent).toContain(`Held reason: ${workflow.held_reason}`);
        if (workflow.gate === 'review') expect(host.textContent).toContain('Acknowledge protocol review');
    }
    const recurring = requests.slice(recurringStart);
    expect(recurring).toHaveLength(30);
    for (let n = 1; n < requests.length; n++) expect(requests[n].at - requests[n-1].at).toBeLessThanOrEqual(2020);
    const bytes = recurring.reduce((sum, item) => sum + item.bytes, 0);
    const baseline = evidence.baseline_bytes * recurring.length;
    expect(bytes / baseline).toBeLessThanOrEqual(.05);
    expect(new Set(requests.map(item => item.url))).toEqual(new Set([`/api/bioxp/protocols/jobs/${evidence.job_id}`]));
    if (process.env.BIOXP_OBSERVATION_MOUNTED_OUTPUT) writeFileSync(`${process.env.BIOXP_OBSERVATION_MOUNTED_OUTPUT}-${scenario}.json`, JSON.stringify({
        scenario, provenance: 'native SQLite capture replay, BMS HTTP, mounted Axios adapter', cold,
        recurring_count: recurring.length, bytes, baseline, reduction_percent: 100 * (1 - bytes / baseline), requests,
    }, null, 2));
});
it.skipIf(!evidence)('keeps read errors visible, recovers on cadence, and fences demand/generation', async () => {
    rows = evidence.windows.idle.rows; await render();
    fail = true; await tick(2020); expect(host.textContent).toContain('Workflow readback unavailable');
    fail = false; await tick(2020); expect(host.textContent).not.toContain('Workflow readback unavailable');
    visible = false; await render(); const hidden = requests.length; await tick(60000); expect(requests).toHaveLength(hidden);
    visible = true; generation = 8; await render(); expect(requests.at(-1)?.params.expected_connection_generation).toBe(8);
    connected = false; await render(); const disconnected = requests.length; await tick(60000); expect(requests).toHaveLength(disconnected);
});
it.skipIf(!evidence)('does not repeatedly hydrate a full old-peer response or change its control authority', async () => {
    const retained = structuredClone(evidence.windows.changing.rows[0]);
    retained.schema_version = 'bioxp.protocol_operator_bundle.v1';
    rows = [retained]; await render(); await tick(60000);
    expect(requests).toHaveLength(1);
    expect(host.textContent).toContain('Compact workflow observation unavailable');
    const button = [...host.querySelectorAll('button')].find(node => node.textContent === 'Request safe-state stop')!;
    expect(button.disabled).toBe(false);
});
