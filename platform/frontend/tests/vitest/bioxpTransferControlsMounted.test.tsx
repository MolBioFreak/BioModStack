import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpTransferControls, transferDocument } from '../../src/components/BioXpTransferControls';
import { api } from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let host: HTMLDivElement, root: Root, client: QueryClient;
let job: any, rows: any[];
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function render(generation = 9) {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpTransferControls generation={generation}
        connected /></QueryClientProvider>));
    await tick();
}
function button(name = 'Pick up and move') { return [...host.querySelectorAll('button')].find(el => el.textContent === name)!; }

beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    job = null; rows = [];
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (path) => {
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
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['pending', 'failed'])('submits without waiting for %s observations', async state => {
    vi.mocked(api.get).mockImplementation(() => state === 'pending'
        ? new Promise(() => {}) : Promise.reject(new Error('observations unavailable')));
    await render();
    await act(async () => button().click()); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect((vi.mocked(api.post).mock.calls[0][1] as any).live_execution).toEqual({ live_execution_ack: true });
    expect(vi.mocked(api.get).mock.calls.every(([path]) => String(path).includes('/protocols/jobs'))).toBe(true);
    expect(button().disabled).toBe(false);
});

it('fences a connection change while preparing the submission identity', async () => {
    const digest = new Uint8Array(32).buffer;
    let release!: (value: ArrayBuffer) => void;
    vi.spyOn(webcrypto.subtle, 'digest').mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
    await render();
    await act(async () => button().click());
    expect(button().disabled).toBe(true);
    await render(10);
    await act(async () => release(digest)); await tick();
    expect(api.post).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Connection changed before submission.');
    expect(button().disabled).toBe(false);
});

it('surfaces an OEM denial without retrying or fabricating completion', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 409, data: { detail: 'OEM door interlock denied' } } });
    await render(); await act(async () => button().click()); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('OEM door interlock denied');
    expect(host.textContent).not.toContain('completed');
    expect(button().disabled).toBe(false);
});

it('submits a compound cover transfer on one click with no checkbox, name or fabricated evidence', async () => {
    await render();
    expect(button().disabled).toBe(false);
    expect(host.querySelector('input')).toBeNull();
    await act(async () => { button().click(); button().click(); }); await tick();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.get).mock.calls.some(([path]) => String(path).includes('transfer-preflight'))).toBe(false);
    const [path, request] = vi.mocked(api.post).mock.calls[0] as [string, any];
    expect(path).toBe('/api/bioxp/protocols/submit');
    expect(request.document).toEqual(transferDocument('CV_OUTPUT', 'LOC_OCS', false));
    expect(request.document.stages[0].actions[0]).toMatchObject({ kind: 'move_cover', params: { cover_id: 'CV_OUTPUT', target_location: 'LOC_OCS' } });
    expect(request.live_execution).toEqual({ live_execution_ack: true });
    expect(request.dry_run).toBe(false); expect(request.expected_connection_generation).toBe(9);
    expect(JSON.stringify(request)).not.toContain('source_location');
    expect(button().disabled).toBe(false);
});
it('reserves a single click only while its HTTP submission is in flight', async () => {
    const original = vi.mocked(api.post).getMockImplementation()!;
    let release!: () => void;
    vi.mocked(api.post).mockImplementation(async (...args) => {
        await new Promise<void>(resolve => { release = resolve; });
        return original(...args);
    });
    await render();
    await act(async () => { button().click(); button().click(); await Promise.resolve(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(button().disabled).toBe(true);
    await act(async () => { release(); await Promise.resolve(); }); await tick();
    expect(button().disabled).toBe(false);
});
it('selects plate and destination and sends the robot plate_move kind', async () => {
    await render();
    await act(async () => {
        const [object, destination] = host.querySelectorAll('select');
        object.value = 'PL_POOL'; object.dispatchEvent(new Event('change', { bubbles: true }));
        destination.value = 'LOC_TC'; destination.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => button().click()); await tick();
    expect((vi.mocked(api.post).mock.calls[0][1] as any).document.stages[0].actions[0]).toMatchObject({
        kind: 'plate_move', params: { plate_id: 'PL_POOL', target_location: 'LOC_TC' } });
});
it('inspection dispatches the existing inspect handler, not a camera-offset travel', async () => {
    await render(); await act(async () => button('Inspect covers (may move covers)').click()); await tick();
    expect((vi.mocked(api.post).mock.calls[0][1] as any).document.stages[0].actions[0]).toMatchObject({ kind: 'inspect', params: {} });
});
it.each(['completed', 'failed'])('shows terminal %s and custody/failure evidence without freezing new work', async status => {
    await render(); await act(async () => button().click()); await tick();
    job = { ...job, status, command: { ...job.command, status, terminal: true }, execution: { dry_run: false, runtime_state: {
        workflow: { command_id: job.job_id, phase: 'terminal', child_command_ids: [] },
        action_results: [{ custody: status === 'completed' ? 'released at destination' : 'pickup failed', ok: status === 'completed' }],
    } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain(status);
    expect(host.textContent).toContain(status === 'completed' ? 'released at destination' : 'pickup failed');
    expect(button().disabled).toBe(false);
});
it('shows listed live work without treating the jobs projection as admission', async () => {
    rows = [{ job_id: 'old', command: { status: 'ambiguous', terminal: true } }];
    await render(); expect(button().disabled).toBe(false);

    rows = [{ job_id: 'live', command: { status: 'executing', terminal: false } }];
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('Listed job live · executing');
    expect(button().disabled).toBe(false);
});
it('reports a mismatched terminal readback without turning the record into an admission gate', async () => {
    const post = vi.mocked(api.post).getMockImplementation()!;
    vi.mocked(api.post).mockImplementation(async (...args) => {
        await post(...args);
        job = { ...job, command: { ...job.command, terminal: true, status: 'completed', idempotency_key: 'wrong-key' } };
        throw new Error('lost response');
    });
    await render(); await act(async () => button().click()); await tick();
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('Job identity mismatch');
    expect(button().disabled).toBe(false); expect(api.post).toHaveBeenCalledTimes(1);
});
it('shows a failed child error outside collapsed details', async () => {
    const get = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path, ...args) => {
        if (String(path).includes('/receipts/child-1')) return { data: { command_id: 'child-1', status: 'failed', error: { message: 'Gripper pickup refused' } } } as never;
        return get(path, ...args);
    });
    await render(); await act(async () => button().click()); await tick();
    job.execution.runtime_state.workflow.child_command_ids = ['child-1'];
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect([...host.querySelectorAll('[role=alert]')].some(el => !el.closest('details') && el.textContent?.includes('Gripper pickup refused'))).toBe(true);
});
it.each([undefined, 409, 502])('keeps the original identity after a failed response (%s) without disabling new actions', async status => {
    vi.mocked(api.post).mockRejectedValue(Object.assign(new Error('lost response'), { response: status ? { status } : undefined }));
    await render(); await act(async () => button().click()); await tick();
    expect(host.textContent).toContain('protocol-live-'); expect(host.textContent).toContain('lost response');
    expect(button().disabled).toBe(false); expect(api.post).toHaveBeenCalledTimes(1);
    const priorReads = vi.mocked(api.get).mock.calls.filter(([path]) => String(path).includes('/jobs/')).length;
    await render(10); await tick();
    expect(vi.mocked(api.get).mock.calls.filter(([path]) => String(path).includes('/jobs/'))).toHaveLength(priorReads);
    expect(host.textContent).toContain('Connection changed. Check the earlier job.');
});
