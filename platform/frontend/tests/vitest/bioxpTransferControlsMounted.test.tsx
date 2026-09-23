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
const contract = { operator_id: 'offline-test-operator', physical_console_verified: true,
    deck_manifest: { fixture: 'explicit offline double' }, preflight: { reference_snapshot: { rows: {} }, artifact_refs: ['offline-fixture'] } };
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpTransferControls generation={9}
        connected controlsEnabled commandBusy={busy} onBusy={onBusy} /></QueryClientProvider>)); await tick();
}
function button(name = 'Pick up and move') { return [...host.querySelectorAll('button')].find(el => el.textContent === name)!; }
async function prepare() {
    const input = host.querySelector('input[type=file]')!;
    Object.defineProperty(input, 'files', { configurable: true, value: [{ text: async () => JSON.stringify(contract) }] });
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
    await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click()); await tick();
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset(); onBusy.mockReset();
    job = null; rows = []; busy = false;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.mocked(api.get).mockImplementation(async (path) => {
        if (String(path).endsWith('/jobs')) return { data: { rows } } as never;
        if (!job) throw new Error('job not yet accepted');
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
    expect(request.live_execution).toEqual({ ...contract, live_execution_ack: true });
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
it('retains the original job identity after a lost response and never retries submission', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('lost response'));
    await render(); await prepare(); await act(async () => button().click()); await tick();
    expect(host.textContent).toContain('protocol-live-'); expect(host.textContent).toContain('lost response');
    expect(button().disabled).toBe(true); expect(api.post).toHaveBeenCalledTimes(1);
});
