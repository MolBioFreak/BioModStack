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

describe('canonical prepared workflow controls', () => {
    it.skipIf(!process.env.BIOXP_WORKFLOW_EXPORT_SOURCE).each(['accepted', 'running', 'completed', 'fresh_process'])(
        'renders the real connected robot producer %s without mutation', async phase => {
            const producer = JSON.parse(readFileSync(process.env.BIOXP_WORKFLOW_EXPORT_SOURCE!, 'utf8'));
            expect(producer.fixture_only).toBe(true);
            expect(producer.physical_acceptance).toBe(false);
            job = producer[phase] as BioXpWorkflowJob;
            rows = [job];
            await render();
            // Completed history is deliberately not auto-selected as active.
            await act(async () => {
                const picker = host.querySelector('select')!;
                picker.value = job.job_id;
                picker.dispatchEvent(new Event('change', { bubbles: true }));
            });
            await tick();
            expect(host.textContent).toContain(`Canonical job: ${job.job_id}`);
            expect(host.textContent).toContain(`Phase: ${job.execution.runtime_state.workflow!.phase}`);
            expect(api.post).not.toHaveBeenCalled();
            await tick(4100);
            expect(host.textContent).toContain(`Phase: ${job.execution.runtime_state.workflow!.phase}`);
            expect(api.post).not.toHaveBeenCalled();
        },
    );
    it('renders robot phase/source/held truth and sends a generation-bound finite pause without version admission', async () => {
        await render();
        expect(host.textContent).toContain('Phase: executing'); expect(host.textContent).toContain('oem:7');
        await click('Pause workflow');
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/control', {
            action: 'pause', mode: 'ordinary', command_id: 'job-one', expected_connection_generation: 9,
            expected_ownership_generation: 42, idempotency_key: expect.any(String),
        });
        expect(host.textContent).toContain('accepted · not yet reached');
        expect(host.textContent).not.toContain('Phase: terminal');
    });
    it('retains GET polling through transient status failure, then fences disconnect and new generations', async () => {
        await render(); const initial = vi.mocked(api.get).mock.calls.length;
        props.controlsEnabled = false; await render(); await tick(4100);
        expect(api.get.mock.calls.length).toBeGreaterThan(initial);
        expect(button('Pause workflow').disabled).toBe(true);
        expect(host.textContent).toContain('passive readback continues');
        failDetail = true; await tick(2100);
        expect(host.textContent).toContain('Workflow readback unavailable');
        failDetail = false; job = jobFixture({ phase: 'waiting', gate: 'error_hold', gate_id: 'held', held_reason: 'source_error' });
        await tick(2100); expect(host.textContent).toContain('Held reason: source_error');
        props.connected = false; await render(); const count = api.get.mock.calls.length;
        await tick(4100); expect(api.get.mock.calls.length).toBe(count);
        props = { generation: 10, connected: true, controlsEnabled: true }; rows = []; await render();
        expect(host.textContent).not.toContain('Canonical job: job-one'); expect(api.post).not.toHaveBeenCalled();
    });
    it('keeps wake and explicit Continue separate and binds the exact reached gate', async () => {
        job = jobFixture({ phase: 'waiting', gate: 'deferred_pause', gate_id: 'pause-occurrence' }); rows = [job]; await render();
        expect(button('Continue workflow').disabled).toBe(true);
        await click('Wake workflow');
        expect(api.post).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ action: 'wake', gate_id: 'pause-occurrence' }));
        expect(api.post).toHaveBeenCalledTimes(1);
        job = jobFixture({ phase: 'waiting', gate: 'deferred_pause', gate_id: 'pause-occurrence', last_control_id: 'wake-control', reached_control_id: 'wake-control' });
        await tick(2100);
        expect(button('Wake workflow').disabled).toBe(true);
        await click('Continue workflow');
        expect(api.post).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ action: 'continue', gate: 'deferred_pause', gate_id: 'pause-occurrence' }));
    });
    it('distinguishes requested from reached ordinary pause using canonical control identities', async () => {
        job = jobFixture({ phase: 'executing', requested_control: { action: 'pause', mode: 'ordinary' }, last_control_id: 'pause-one' }); rows = [job]; await render();
        expect(button('Pause workflow').disabled).toBe(true);
        job = jobFixture({ phase: 'waiting', gate: 'ordinary_pause', gate_id: 'pause-one',
            requested_control: { action: 'pause', mode: 'ordinary' }, last_control_id: 'pause-one', reached_control_id: 'pause-one' });
        await tick(2100); expect(button('Continue workflow').disabled).toBe(false);
        await click('Continue workflow');
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/control', expect.objectContaining({ action: 'continue', gate: 'ordinary_pause', gate_id: 'pause-one' }));
    });
    it('releases only the delaypoint using Start now, never submits another job', async () => {
        job = jobFixture({ phase: 'waiting', gate: 'delaypoint', gate_id: 'oem:4' }); rows = [job]; await render();
        await click('Start now');
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/control', expect.objectContaining({ action: 'continue', gate: 'delaypoint', gate_id: 'oem:4' }));
        expect(api.post).toHaveBeenCalledTimes(1);
    });
    it('review targets its stage/action occurrence and does not expose a Continue release', async () => {
        job = jobFixture({ phase: 'waiting', gate: 'review', gate_id: 'review-action' }); rows = [job]; await render();
        expect(host.textContent).not.toContain('Continue workflow');
        await act(async () => { inputText('Reviewer', 'Alice'); inputText('Review note', 'Checked'); });
        await click('Acknowledge protocol review');
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/review', expect.objectContaining({
            stage_id: 'stage-1', action_id: 'review-action', reviewer: 'Alice', note: 'Checked',
            command_id: 'job-one', expected_connection_generation: 9, expected_ownership_generation: 42,
        }));
    });
    it('requires confirmation for cooperative Abort and leaves error holds without Continue', async () => {
        job = jobFixture({ phase: 'waiting', gate: 'error_hold', gate_id: 'error-1', held_reason: 'source_error' }); rows = [job]; await render();
        expect(button('Abort workflow').disabled).toBe(true); expect(host.textContent).not.toContain('Continue workflow');
        await check(1); await click('Abort workflow');
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/control', expect.objectContaining({ action: 'abort' }));
        expect(button('Abort workflow').disabled).toBe(true);
    });
    it.each(['epilogue', 'cleanup'] as const)('retains cooperative Abort admission during %s without exposing ordinary pause', async phase => {
        job = jobFixture({ phase }); rows = [job]; await render();
        expect(button('Pause workflow').disabled).toBe(true);
        await check(1); expect(button('Abort workflow').disabled).toBe(false);
        await click('Abort workflow');
        expect(api.post).toHaveBeenCalledWith('/api/bioxp/protocols/jobs/job-one/control', expect.objectContaining({ action: 'abort' }));
    });
    it('never infers successful completion or release authority from ambiguous terminal receipts', async () => {
        job = jobFixture({ phase: 'reconciling', held_reason: 'native_outcome_unknown' });
        job.command!.terminal = true; job.command!.status = 'ambiguous'; rows = [job]; await render();
        expect(host.textContent).toContain('Robot status: ambiguous');
        expect(button('Pause workflow').disabled).toBe(true); expect(button('Request safe-state stop').disabled).toBe(true);
        await pick(prepared); await check(0); expect(button('Submit prepared workflow').disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
    });
    it('submits captured selected input once, retaining identity and GET reconciliation after response loss', async () => {
        rows = []; await render(); await pick(prepared); await check(0);
        vi.mocked(api.post).mockRejectedValue(new Error('response lost'));
        expect(button('Submit prepared workflow').disabled, host.textContent ?? '').toBe(false);
        await click('Submit prepared workflow');
        // SHA-256 is asynchronous native crypto, not driven by fake timers.
        for (let i = 0; i < 30 && api.post.mock.calls.length === 0; i++) await act(async () => { await new Promise(resolve => realTimeout(resolve, 5)); });
        await tick();
        expect(api.post.mock.calls.length, host.textContent ?? '').toBe(1);
        const [path, request] = api.post.mock.calls[0] as [string, any];
        expect(path).toBe('/api/bioxp/protocols/submit');
        expect(request).toEqual({ ...prepared, dry_run: false, expected_connection_generation: 9, idempotency_key: expect.any(String) });
        expect(host.textContent).toContain('Original submission key:');
        expect(host.textContent).toContain('no automatic retry');
        expect(button('Submit prepared workflow').disabled).toBe(true);
        await tick(6100); expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.get.mock.calls.some(([url]) => String(url).startsWith('/api/bioxp/protocols/jobs/protocol-live-'))).toBe(true);
    });
    it('rejects authoring/delivery escape fields without a submit or compile request', async () => {
        rows = []; await render(); await pick({ ...prepared, parent_command_id: 'forged', idempotency_key: 'forged' });
        expect(host.textContent).toContain('without delivery identity fields');
        expect(button('Submit prepared workflow').disabled).toBe(true); expect(api.post).not.toHaveBeenCalled();
    });
    it('historical nonphysical records are read-only and never relabeled as a live workflow', async () => {
        job = { job_id: 'job-one', status: 'blocked' }; rows = [job]; await render();
        const select = host.querySelector('select')!;
        await act(async () => { select.value = 'job-one'; select.dispatchEvent(new Event('change', { bubbles: true })); }); await tick();
        expect(host.textContent).toContain('Historical or nonphysical job');
        expect(host.textContent).not.toContain('Pause workflow'); expect(api.post).not.toHaveBeenCalled();
    });
});
