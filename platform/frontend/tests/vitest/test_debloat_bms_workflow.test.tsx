import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { setTimeout as realTimeout } from 'node:timers';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BioXpWorkflowControls } from '../../src/components/BioXpWorkflowControls';
import { api } from '../../src/lib/api';
import type { BioXpWorkflowJob, BioXpWorkflowState } from '../../src/lib/bioxpClient';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const workflow = (updates: Partial<BioXpWorkflowState> = {}): BioXpWorkflowState => ({
    command_id: 'job-one', phase: 'executing', gate: null, gate_id: null,
    source_occurrence_id: 'oem:7', requested_control: null, last_control_id: null,
    reached_control_id: null, held_reason: null, child_command_ids: ['native-child'], ...updates,
});
const jobFixture = (updates: Partial<BioXpWorkflowState> = {}): BioXpWorkflowJob => ({
    operator: { manual_review_required: updates.gate === 'review', pending_review: { stage_id: 'stage-1', action_id: 'review-action', reason: null } },
    job_id: 'job-one', status: 'dispatched', command: { command_id: 'job-one', idempotency_key: 'original',
        ownership_generation: 42, state_version: 12, status: 'dispatched', terminal: false,
        status_path: '/protocol/jobs/job-one' },
    execution: { dry_run: false, runtime_state: { completed: false, current_stage_id: 'stage-1',
        stage_states: { 'stage-1': { current_action_id: 'review-action', pause_marker_action_id: 'review-action' } },
        workflow: workflow(updates) } },
});
let root: Root;
let host: HTMLDivElement;
let client: QueryClient;
let job: BioXpWorkflowJob;
let rows: BioXpWorkflowJob[];
let failDetail: boolean;
let props: { generation: number; connected: boolean; controlsEnabled: boolean };
async function tick(ms = 20) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpWorkflowControls {...props} /></QueryClientProvider>));
    await tick();
}
function button(label: string) {
    const result = [...host.querySelectorAll('button')].find(node => node.textContent === label);
    if (!result) throw new Error(`Missing button: ${label}`);
    return result;
}
async function click(label: string) { await act(async () => button(label).click()); await tick(); }
async function check(index: number) { await act(async () => host.querySelectorAll<HTMLInputElement>('input[type=checkbox]')[index].click()); }
async function pick(value: unknown) {
    const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
    Object.defineProperty(input, 'files', { configurable: true, value: [{ name: 'prepared.json', text: async () => JSON.stringify(value) }] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
    await tick();
}
function inputText(label: string, value: string) {
    const node = [...host.querySelectorAll('label')].find(el => el.textContent?.startsWith(label))?.querySelector('input');
    if (!node) throw new Error(label);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    setter.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true }));
}
const prepared = { source_type: 'native', document: { protocol_id: 'prepared', version: 1,
    operations: [{ oem_opcode: 'la', arguments: ['unaltered'], source_occurrence_id: 'oem:0' }] },
    live_execution_ack: true, operator_id: 'operator', physical_console_verified: true,
    deck_manifest: { plates: ['source-plate'] }, preflight: { reviewed: true }, artifact_refs: ['reviewed-input'] };

beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('crypto', webcrypto);
    vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    job = jobFixture(); rows = [job]; failDetail = false;
    props = { generation: 9, connected: true, controlsEnabled: true };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (path) => {
        if (path === '/api/bioxp/protocols/jobs') return { data: { rows } } as never;
        if (String(path).startsWith('/api/bioxp/protocols/jobs/')) {
            if (failDetail) throw new Error('temporary readback loss');
            return { data: job } as never;
        }
        throw new Error(`Unexpected GET ${path}`);
    });
    vi.mocked(api.post).mockImplementation(async (path, request: any) => {
        if (String(path).endsWith('/control')) return { data: { control_command_id: 'control-one',
            idempotency_key: request.idempotency_key, command_id: request.command_id, job_id: 'job-one',
            ownership_generation: 42, state_version: 13, accepted: true, reached: false,
            phase: 'executing', gate: null, gate_id: null, status_path: '/protocol/jobs/job-one' } } as never;
        if (String(path).endsWith('/review')) return { data: job } as never;
        throw new Error(`Unexpected POST ${path}`);
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });


describe('debloat prepared intent boundary', () => {
    it('stops settled selected polling while retaining bounded cross-client discovery', async () => {
        job = jobFixture({ phase: 'terminal' });
        job.command!.terminal = true; job.command!.status = 'completed'; rows = [job];
        await render();
        await act(async () => { const select = host.querySelector('select')!; select.value = job.job_id; select.dispatchEvent(new Event('change', { bubbles: true })); });
        await tick();
        const details = () => api.get.mock.calls.filter(([url]) => String(url).endsWith('/job-one')).length;
        const lists = () => api.get.mock.calls.filter(([url]) => url === '/api/bioxp/protocols/jobs').length;
        const before = details(); const initialLists = lists();
        rows = [job, { ...job, job_id: 'another-client-job' }];
        await tick(30_000);
        expect(details()).toBe(before);
        expect(lists() - initialLists).toBe(3);
        expect(host.textContent).toContain('another-client-job');
        expect(api.post).not.toHaveBeenCalled();
    });
    it('keeps ambiguous terminal reconciliation on its existing cadence', async () => {
        job = jobFixture({ phase: 'reconciling' }); job.command!.terminal = true; job.command!.status = 'ambiguous'; rows = [job];
        await render(); const before = api.get.mock.calls.length; await tick(6100);
        expect(api.get.mock.calls.length - before).toBe(3);
        expect(api.post).not.toHaveBeenCalled();
    });
    it.each(['loading', 'error', 'active', 'ambiguous', 'missing'])('allows a new selection despite %s observations', async mode => {
        if (mode === 'loading') vi.mocked(api.get).mockImplementation(() => new Promise(() => {}));
        if (mode === 'error') vi.mocked(api.get).mockRejectedValue(new Error('offline readback'));
        if (mode === 'ambiguous') { job.command!.terminal = true; job.command!.status = 'ambiguous'; }
        if (mode === 'missing') failDetail = true;
        props.controlsEnabled = false;
        await render(); await pick(prepared);
        expect(host.querySelector<HTMLInputElement>('input[type=file]')!.disabled).toBe(false);
        expect(button('Submit prepared workflow').disabled).toBe(false);
        expect(host.textContent).not.toContain('I intend to submit');
    });
    it('reserves same-event clicks through digest and permits new intent after failed POST', async () => {
        let resolve!: (value: ArrayBuffer) => void;
        vi.stubGlobal('crypto', { randomUUID: () => 'reserved-key', subtle: { digest: () => new Promise<ArrayBuffer>(r => { resolve = r; }) } });
        vi.mocked(api.post).mockRejectedValue(new Error('robot refusal'));
        await render(); await pick(prepared);
        await act(async () => { button('Submit prepared workflow').click(); button('Submit prepared workflow').click(); });
        expect(api.post).not.toHaveBeenCalled();
        await act(async () => resolve(new Uint8Array(32).buffer)); await tick();
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(host.textContent).toContain('reserved-key');
        expect(host.textContent).toContain('robot refusal');
        expect(button('Submit prepared workflow').disabled).toBe(false);
        await pick({ ...prepared, operator_id: 'next' });
        expect(host.querySelector<HTMLInputElement>('input[type=file]')!.disabled).toBe(false);
        await tick(6100); expect(api.post).toHaveBeenCalledTimes(1);
    });
    it.each(['digest', 'post'])('does not publish old generation after replacement during %s', async boundary => {
        let releaseDigest!: (value: ArrayBuffer) => void;
        let releasePost!: (value: unknown) => void;
        vi.stubGlobal('crypto', { randomUUID: () => 'old-key', subtle: { digest: () => new Promise<ArrayBuffer>(r => { releaseDigest = r; }) } });
        vi.mocked(api.post).mockImplementation(() => new Promise(r => { releasePost = r; }));
        rows = []; await render(); await pick(prepared); await click('Submit prepared workflow');
        if (boundary === 'post') { await act(async () => releaseDigest(new Uint8Array(32).buffer)); await tick(); }
        props.generation = 10; await render();
        if (boundary === 'digest') await act(async () => releaseDigest(new Uint8Array(32).buffer));
        else await act(async () => releasePost({ data: jobFixture() }));
        await tick();
        expect(api.post).toHaveBeenCalledTimes(boundary === 'digest' ? 0 : 1);
        expect(host.textContent).not.toContain('Original submission key: old-key');
        expect(host.textContent).not.toContain('Canonical job: job-one');
        expect(button('Submit prepared workflow').disabled).toBe(false);
    });
});
