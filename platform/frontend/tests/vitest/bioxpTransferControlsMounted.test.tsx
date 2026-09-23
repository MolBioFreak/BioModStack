import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpTransferControls, transferDocument } from '../../src/components/BioXpTransferControls';
import { api } from '../../src/lib/api';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let host: HTMLDivElement, root: Root, client: QueryClient;
let job: any, rows: any[], busy: boolean;
const onBusy = vi.fn();
const preflight = { connection_generation: 9, ownership_generation: 2, observed_deck: { current_location: null },
    preflight: { reference_snapshot: { rows: {} }, artifact_refs: ['offline-fixture'] } };
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function render(generation = 9) {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpTransferControls generation={generation}
        connected controlsEnabled commandBusy={busy} onBusy={onBusy} /></QueryClientProvider>)); await tick();
}
function button(name = 'Pick up and move') { return [...host.querySelectorAll('button')].find(el => el.textContent === name)!; }
async function prepare() {
    const input = host.querySelector<HTMLInputElement>('input:not([type=checkbox])')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'offline-test-operator'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click()); await tick();
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset(); onBusy.mockReset();
    job = null; rows = []; busy = false;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (path) => {
        if (String(path).endsWith('/transfer-preflight')) return { data: preflight } as never;
        if (String(path).endsWith('/jobs')) return { data: { rows } } as never;
        if (!job) throw new Error('job not yet accepted');
        if (!String(path).includes('/jobs/')) throw new Error(`Unexpected GET ${path}`);
        return { data: job } as never;
    });
    vi.mocked(api.post).mockImplementation(async (_path, request: any) => {
        const digest = await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(request.idempotency_key));
        const id = 'protocol-live-' + Buffer.from(digest).toString('hex');
        job = { job_id: id, status: 'dispatched', command: { command_id: id, idempotency_key: request.idempotency_key,
            status: 'dispatched', terminal: false }, execution: { dry_run: false, runtime_state: {
                workflow: { command_id: id, phase: 'executing', child_command_ids: [] }, action_results: [] } } };
        return { data: job } as never;
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.unstubAllGlobals(); });
it('submits a compound cover transfer through the existing API, with no source override or travel request', async () => {
    await render(); expect(button().disabled).toBe(true); await prepare();
    expect(button().disabled).toBe(false);
    await act(async () => { button().click(); button().click(); }); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    const [path, request] = vi.mocked(api.post).mock.calls[0] as [string, any];
    expect(path).toBe('/api/bioxp/protocols/submit');
    expect(request.document).toEqual(transferDocument('CV_OUTPUT', 'LOC_OCS', false));
    expect(request.document.stages[0].actions[0]).toMatchObject({ kind: 'move_cover', params: { cover_id: 'CV_OUTPUT', target_location: 'LOC_OCS' } });
    expect(request.live_execution).toMatchObject({ operator_id: 'offline-test-operator', physical_console_verified: true, live_execution_ack: true, preflight: preflight.preflight, deck_manifest: { observed_deck: preflight.observed_deck, selected_operation: { object: 'CV_OUTPUT', target: 'LOC_OCS' } } });
    expect(host.querySelector('input[type=file]')).toBeNull();
    expect(request.dry_run).toBe(false); expect(request.expected_connection_generation).toBe(9);
    expect(JSON.stringify(request)).not.toContain('source_location');
    expect(button().disabled).toBe(true); expect(onBusy).toHaveBeenLastCalledWith(true);
});
it('selects plate and destination and sends the robot plate_move kind', async () => {
    await render(); await prepare();
    await act(async () => {
        const [object, destination] = host.querySelectorAll('select');
        object.value = 'PL_POOL'; object.dispatchEvent(new Event('change', { bubbles: true }));
        destination.value = 'LOC_TC'; destination.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click());
    await act(async () => button().click()); await tick();
    expect((vi.mocked(api.post).mock.calls[0][1] as any).document.stages[0].actions[0]).toMatchObject({
        kind: 'plate_move', params: { plate_id: 'PL_POOL', target_location: 'LOC_TC' } });
});
it('inspection dispatches the existing inspect handler, not a camera-offset travel', async () => {
    await render(); await prepare(); await act(async () => button('Inspect covers (may move covers)').click()); await tick();
    expect((vi.mocked(api.post).mock.calls[0][1] as any).document.stages[0].actions[0]).toMatchObject({ kind: 'inspect', params: {} });
});
it.each(['completed', 'failed'])('renders terminal %s and custody/failure evidence without freezing new work', async status => {
    await render(); await prepare(); await act(async () => button().click()); await tick();
    job = { ...job, status, command: { ...job.command, status, terminal: true }, execution: { dry_run: false, runtime_state: {
        workflow: { command_id: job.job_id, phase: 'terminal', child_command_ids: [] },
        action_results: [{ custody: status === 'completed' ? 'released at destination' : 'pickup failed', ok: status === 'completed' }],
    } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain(`Robot compound status: ${status} · terminal`);
    expect(host.textContent).toContain(status === 'completed' ? 'released at destination' : 'pickup failed');
    expect(host.textContent).toContain('not independent physical verification');
    expect(onBusy).toHaveBeenLastCalledWith(false);
    await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click()); await tick();
    expect(button().disabled).toBe(false);
});
it('blocks only current live work, not a historical terminal custody error', async () => {
    rows = [{ job_id: 'old', command: { status: 'ambiguous', terminal: true } }];
    await render(); await prepare(); expect(button().disabled).toBe(false);
    busy = true; await render(); expect(button().disabled).toBe(true);
    busy = false; await render(); expect(button().disabled).toBe(false);
});
it('rejects a mismatched terminal readback identity after an uncertain reply', async () => {
    const post = vi.mocked(api.post).getMockImplementation()!;
    vi.mocked(api.post).mockImplementation(async (...args) => {
        await post(...args);
        job = { ...job, command: { ...job.command, terminal: true, status: 'completed', idempotency_key: 'wrong-key' } };
        throw new Error('lost response');
    });
    await render(); await prepare(); await act(async () => button().click()); await tick();
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('Robot readback identity does not match');
    expect(button().disabled).toBe(true); expect(api.post).toHaveBeenCalledTimes(1);
});
it('shows absent reference refusal without posting or retaining a phantom submission', async () => {
    const get = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path, ...args) => {
        if (String(path).endsWith('/transfer-preflight')) throw new Error('Robot reference missing or unverified: g');
        return get(path, ...args);
    });
    await render(); await prepare(); await act(async () => button().click()); await tick();
    expect(host.querySelector('[role=alert]')?.textContent).toContain('Robot reference missing');
    expect(api.post).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('Submission key:');
    expect(onBusy).toHaveBeenLastCalledWith(false);
});
it('does not submit when connection changes while preflight is in flight', async () => {
    const get = vi.mocked(api.get).getMockImplementation()!;
    let resolve!: (value: any) => void;
    vi.mocked(api.get).mockImplementation(async (path, ...args) => {
        if (String(path).endsWith('/transfer-preflight')) return await new Promise(r => { resolve = r; });
        return get(path, ...args);
    });
    await render(); await prepare(); await act(async () => button().click());
    await render(10); await act(async () => resolve({ data: preflight })); await tick();
    expect(api.post).not.toHaveBeenCalled(); expect(host.textContent).toContain('Connection changed during preflight');
});
it('renders a failed child error outside collapsed JSON details', async () => {
    const get = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path, ...args) => {
        if (String(path).includes('/receipts/child-1')) return { data: { command_id: 'child-1', status: 'failed', error: { message: 'Gripper pickup refused' } } } as never;
        return get(path, ...args);
    });
    await render(); await prepare(); await act(async () => button().click()); await tick();
    job.execution.runtime_state.workflow.child_command_ids = ['child-1'];
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect([...host.querySelectorAll('[role=alert]')].some(el => !el.closest('details') && el.textContent?.includes('Gripper pickup refused'))).toBe(true);
});
it.each([undefined, 409, 502])('retains the original job identity after an uncertain reply (%s) and never retries submission', async status => {
    vi.mocked(api.post).mockRejectedValue(Object.assign(new Error('lost response'), { response: status ? { status } : undefined }));
    await render(); await prepare(); await act(async () => button().click()); await tick();
    expect(host.textContent).toContain('protocol-live-'); expect(host.textContent).toContain('lost response');
    expect(button().disabled).toBe(true); expect(api.post).toHaveBeenCalledTimes(1);
    const priorReads = vi.mocked(api.get).mock.calls.filter(([path]) => String(path).includes('/jobs/')).length;
    await render(10); await tick();
    expect(vi.mocked(api.get).mock.calls.filter(([path]) => String(path).includes('/jobs/'))).toHaveLength(priorReads);
    expect(host.textContent).toContain('Original submission identity retained');
    expect(button().disabled).toBe(true);
});
