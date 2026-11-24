import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpRecoveryDecision } from '../../src/components/BioXpRecoveryDecision';

// Inert transport; no robot, workflow control or acquisition.
const host = document.createElement('div');
let root = createRoot(host);
const adapter = api.defaults.adapter, send = vi.fn();
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const requests: string[] = [];
const row = { action_id: 'asp', attempt: 2, source_occurrence_id: 'occ', hold_id: 'gate', kind: 'pipette_aspirate', ok: false, unresolved_attempt: true };
const chunk = { channel: 2, stream_session_id: 'trace', chunk_id: 'c', scientific_result: { scientific_result: { lost_frame_count: 0, malformed_frame_count: 0 }, samples: [0,1,4].map((s,i) => ({ sample_sequence: s, raw_pressure: [-9,0,3][i] })) } };
const capture = { stream: { stream_session_id: 'trace', terminal_state: 'completed', loss_count: 0 }, chunks: [chunk] };
async function render(extra: object, read = async (): Promise<unknown> => capture, generation = 7, attempt = 2) {
    const report = { job_id: 'job', workflow: { gate_id: 'gate' }, action_results: [{ ...row, attempt }] };
    api.defaults.adapter = async config => {
        const wire = `${config.method} ${config.url} ${config.params?.expected_connection_generation}`;
        requests.push(wire);
        let data;
        if (wire === `get /api/bioxp/methods/runs/job/report ${generation}`) data = report;
        else if (wire === `post /api/bioxp/methods/runs/job/recovery-preview ${generation}`) data = { ...JSON.parse(config.data), job_id: 'job', submitted: false, editable_settings: ['speed'], attempt_results: [row], sibling_results: [], ...extra };
        else if (wire === `get /api/bioxp/pipettes/pressure-captures/trace ${generation}`) data = await read();
        else throw new Error(wire);
        return { config, data, headers: {}, status: 200, statusText: 'OK' };
    };
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpRecoveryDecision key={`${generation}:${attempt}`} jobId="job" generation={generation} gateId="gate" report={report} held={{ actionId: 'asp', occurrence: 'occ', attempt }} disabled={false} send={send} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve,20)); });
}
afterEach(async () => { await act(async () => root.unmount()); root = createRoot(host); client.clear(); api.defaults.adapter = adapter; requests.length = 0; send.mockClear(); });
const traces = { pressure_traces: [{ stream_session_id: 'trace' }] };
it('automatically reads only the addressed capture and charts raw counts without bridging a sequence gap', async () => {
    await render({ ...traces, pressure_trace_warning: 'Another reference is inconsistent.' });
    expect(host.textContent).toContain('Another reference is inconsistent.');
    expect(host.textContent).toContain('Channel 2');
    expect(host.textContent).toContain('Raw counts: -9 to 3');
    const chart = host.querySelector('svg[role="img"]');
    expect(chart?.getAttribute('aria-label')).toContain('sample sequence');
    expect(chart?.querySelectorAll('polyline')).toHaveLength(1);
    expect(chart?.querySelectorAll('circle')).toHaveLength(1);
    await render(traces);
    expect(requests.toSorted()).toEqual(['get /api/bioxp/methods/runs/job/report 7','post /api/bioxp/methods/runs/job/recovery-preview 7','get /api/bioxp/pipettes/pressure-captures/trace 7'].sort());
    expect(send).not.toHaveBeenCalled();
});

const reads = () => requests.filter(w => w.includes('/pressure-captures/'));
const submit = () => Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Submit recovery decision')!;
it.each([{}, { pressure_traces: [] }])('leaves recovery available when no capture was reported', async extra => {
    await render(extra); expect(host.textContent).toContain('No pressure capture reported');
    expect(reads()).toEqual([]); expect(submit().disabled).toBe(false); expect(send).not.toHaveBeenCalled();
});
it('distinguishes a reference warning from no capture', async () => {
    await render({ pressure_trace_warning: 'Capture identity unavailable.' });
    expect(host.textContent).toContain('Capture identity unavailable.'); expect(host.textContent).not.toContain('No pressure capture');
    expect(reads()).toEqual([]); expect(submit().disabled).toBe(false);
});
it.each(['interrupted','loss'])('does not draw through an unlocated %s and shows each channel separately', async reason => {
    await render({ pressure_traces: [...traces.pressure_traces, ...traces.pressure_traces] }, async () => ({
        stream: { ...capture.stream, terminal_state: reason === 'interrupted' ? reason : 'completed', loss_count: reason === 'loss' ? 1 : 0 }, chunks: [chunk, { ...chunk, channel: 0, chunk_id: 'd' }],
    }));
    expect(host.querySelectorAll('figure')).toHaveLength(2); expect(host.querySelector('polyline')).toBeNull();
    expect(host.querySelectorAll('circle')).toHaveLength(6); expect(host.textContent).toContain('gap locations are not inferred');
    expect(reads()).toHaveLength(1); expect(submit().disabled).toBe(false);
});
it.each(['read failure','wrong stream'])('keeps %s separate from no capture and does not block recovery', async reason => {
    await render(traces, async () => { if (reason === 'read failure') throw new Error('Capture not found'); return { ...capture, stream: { stream_session_id: 'other' } }; });
    expect(host.textContent).toContain('Pressure trace trace unavailable'); expect(host.textContent).not.toContain('No pressure capture');
    expect(host.querySelector('svg')).toBeNull(); expect(submit().disabled).toBe(false); expect(send).not.toHaveBeenCalled();
});
it.each([[8,2],[7,3]])('ignores a late capture after changing to generation %i attempt %i', async (generation, attempt) => {
    let finish!: (data: unknown) => void;
    await render(traces, () => new Promise(resolve => { finish = resolve; }));
    expect(host.textContent).toContain('Reading pressure trace'); expect(submit().disabled).toBe(false);
    await render({}, undefined, generation, attempt);
    await act(async () => { finish(capture); await new Promise(resolve => setTimeout(resolve,20)); });
    expect(host.textContent).toContain('No pressure capture reported'); expect(host.querySelector('svg')).toBeNull(); expect(send).not.toHaveBeenCalled();
});
