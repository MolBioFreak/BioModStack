import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BioXpWorkflowJobClone, type BioXpWorkflowJobCloneProps } from '../../src/components/BioXpWorkflowJobClone';
import { api } from '../../src/lib/api';
import type { WorkflowJobClone } from '../../src/lib/bioxpWorkflowPlan';

// Transport-only fixtures: real mounted leaf + shared helper; no native/hardware execution.
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let root: Root, host: HTMLDivElement, props: BioXpWorkflowJobCloneProps;
const documentFixture = { protocol_id: 'not-the-authoritative-job-id', metadata: { bms_saved_workflow: {
    id: 'deleted-template', name: 'Original', draft: { schema: 'bms.bioxp-workflow-draft.v1', steps: [],
        editor_state: { raw: '01.20', blank: '', zero: 0, flag: false, nullable: null, unknown: { kept: true } } },
} }, stages: [] };
const cloneFixture: WorkflowJobClone = { name: 'Original copy', draft: documentFixture.metadata.bms_saved_workflow.draft as WorkflowJobClone['draft'], issues: [] };
function deferred() { let resolve!: (value: unknown) => void; let reject!: (reason: Error) => void;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function render() { await act(async () => root.render(<BioXpWorkflowJobClone {...props} />)); }
const button = (text: string) => [...host.querySelectorAll('button')].find(node => node.textContent === text)!;
async function click(text: string) { await act(async () => button(text).click()); }
async function select(index: number, value: string) { await act(async () => {
    const node = host.querySelectorAll('select')[index]; node.value = value; node.dispatchEvent(new Event('change', { bubbles: true }));
}); }
async function exact(id: string) { await act(async () => {
    const node = host.querySelector('input')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, id);
    node.dispatchEvent(new Event('input', { bubbles: true }));
}); }
async function open() { await render(); await click('Clone previous job'); }
const clonePosts = () => vi.mocked(api.post).mock.calls;
beforeEach(() => {
    vi.useFakeTimers(); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    props = { generation: 9, connected: true, retained: [{ version: 1, key: 'never-replayed', jobId: 'original-job', generation: 3,
        document: structuredClone(documentFixture), saved: { ...structuredClone(documentFixture.metadata.bms_saved_workflow),
            draft: structuredClone(documentFixture.metadata.bms_saved_workflow.draft) as NonNullable<WorkflowJobClone['draft']> } }], onClone: vi.fn() };
    vi.mocked(api.get).mockImplementation(async url => {
        if (url === '/api/bioxp/protocols/jobs') return { data: { rows: [{ job_id: 'history-job', status: 'failed', protocol: { document: { staleSummary: true } } }] } } as never;
        return { data: { job_id: decodeURIComponent(String(url).split('/').at(-1)!), status: 'ambiguous', protocol: { document: documentFixture } } } as never;
    });
    vi.mocked(api.post).mockImplementation(async url => {
        if (url !== '/api/bioxp/workflows/clone') throw new Error(`Unexpected write: ${url}`);
        return { data: structuredClone(cloneFixture) } as never;
    });
});
afterEach(async () => {
    // Every test checks the only permitted POST is the pure clone endpoint.
    expect(clonePosts().every(([url]) => url === '/api/bioxp/workflows/clone')).toBe(true);
    await act(async () => root.unmount()); host.remove(); vi.useRealTimers();
});
describe('demand-only original-job cloning (transport fixtures)', () => {
    it('does no reads on mount, disclosure, selection or elapsed time', async () => {
        await open(); await exact('older-job');
        await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
        expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled(); expect(props.onClone).not.toHaveBeenCalled();
    });
    it('clones retained original document offline without reading a changed/deleted template or replaying run state', async () => {
        props.connected = false;
        props.retained[0].saved.name = 'Mutable association label';
        const original = structuredClone(props.retained);
        await open(); await select(0, 'original-job'); await click('Clone selected original job');
        expect(api.get).not.toHaveBeenCalled();
        expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/bioxp/workflows/clone', { job_id: 'original-job', document: documentFixture });
        expect(props.onClone).toHaveBeenCalledExactlyOnceWith(cloneFixture);
        expect(props.retained).toEqual(original);
        expect(button('Load recent jobs').disabled).toBe(true);
    });
    it('loads recent jobs only explicitly and fetches selected detail afresh instead of its summary', async () => {
        await open(); await click('Load recent jobs');
        expect(api.get).toHaveBeenCalledExactlyOnceWith('/api/bioxp/protocols/jobs', { params: { expected_connection_generation: 9 } });
        await select(1, 'history-job'); expect(api.get).toHaveBeenCalledTimes(1);
        await click('Clone selected original job');
        expect(api.get).toHaveBeenLastCalledWith('/api/bioxp/protocols/jobs/history-job', { params: { expected_connection_generation: 9 } });
        expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/bioxp/workflows/clone', { job_id: 'history-job', document: documentFixture });
        expect(props.onClone).toHaveBeenCalledTimes(1);
        await act(async () => { await vi.advanceTimersByTimeAsync(60_000); }); expect(api.get).toHaveBeenCalledTimes(2);
    });
    it.each(['failed', 'ambiguous', 'queued', 'running'])('fresh-reads exact older %s job without a control-authority/state prerequisite', async status => {
        vi.mocked(api.get).mockResolvedValue({ data: { job_id: 'older/job', status, protocol: { document: documentFixture } } } as never);
        await open(); await exact('older/job'); await click('Clone selected original job');
        expect(api.get).toHaveBeenCalledExactlyOnceWith('/api/bioxp/protocols/jobs/older%2Fjob', { params: { expected_connection_generation: 9 } });
        expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/bioxp/workflows/clone', { job_id: 'older/job', document: documentFixture });
        expect(props.onClone).toHaveBeenCalledTimes(1);
    });
    it('rejects a mismatched returned job ID without cloning its document', async () => {
        vi.mocked(api.get).mockResolvedValue({ data: { job_id: 'other', protocol: { document: documentFixture } } } as never);
        await open(); await exact('original'); await click('Clone selected original job');
        expect(host.textContent).toContain('identity mismatch'); expect(api.post).not.toHaveBeenCalled(); expect(props.onClone).not.toHaveBeenCalled();
    });
    it('reports unsupported native documents and absent original documents without overwriting editor', async () => {
        vi.mocked(api.post).mockResolvedValue({ data: { name: null, draft: null, issues: [{ step_id: 's1', message: 'Unsupported native action; no lossless clone.' }] } } as never);
        await open(); await exact('unsupported'); await click('Clone selected original job');
        expect(host.textContent).toContain('s1: Unsupported native action; no lossless clone.'); expect(props.onClone).not.toHaveBeenCalled();
        vi.mocked(api.get).mockResolvedValue({ data: { job_id: 'missing' } } as never);
        await exact('missing'); await click('Clone selected original job');
        expect(host.textContent).toContain('Original job document unavailable'); expect(api.post).toHaveBeenCalledTimes(1);
    });
    it('reports read errors and leaves remote browsing disconnected unavailable without gating retained clones', async () => {
        vi.mocked(api.get).mockRejectedValue(new Error('fixture read timeout'));
        await open(); await click('Load recent jobs'); expect(host.textContent).toContain('fixture read timeout');
        await exact('older'); await click('Clone selected original job'); expect(host.textContent).toContain('fixture read timeout');
        expect(props.onClone).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
        props.connected = false; await render(); expect(button('Clone selected original job').disabled).toBe(true);
        await select(0, 'original-job'); expect(button('Clone selected original job').disabled).toBe(false);
    });
    it.each(['selection', 'generation', 'disconnect', 'close', 'unmount'])('fences a late detail after %s', async change => {
        const read = deferred(); vi.mocked(api.get).mockReturnValue(read.promise as never);
        await open(); await exact('old'); await click('Clone selected original job');
        if (change === 'selection') await exact('new');
        if (change === 'generation') { props.generation++; await render(); }
        if (change === 'disconnect') { props.connected = false; await render(); }
        if (change === 'close') await click('Clone previous job');
        if (change === 'unmount') { await act(async () => root.unmount()); root = createRoot(host); }
        await act(async () => read.resolve({ data: { job_id: 'old', protocol: { document: documentFixture } } }));
        expect(api.post).not.toHaveBeenCalled(); expect(props.onClone).not.toHaveBeenCalled();
    });
    it.each(['selection', 'generation'])('fences late pure clone response after %s', async change => {
        const clone = deferred(); vi.mocked(api.post).mockReturnValue(clone.promise as never);
        await open(); await select(0, 'original-job'); await click('Clone selected original job');
        if (change === 'selection') await exact('new'); else { props.generation++; await render(); }
        await act(async () => clone.resolve({ data: cloneFixture })); expect(props.onClone).not.toHaveBeenCalled();
    });
    it('ignores stale list/error responses while allowing a newly selected clone to finish', async () => {
        const read = deferred(); vi.mocked(api.get).mockReturnValue(read.promise as never);
        await open(); await click('Load recent jobs'); await select(0, 'original-job'); await click('Clone selected original job');
        await act(async () => read.reject(new Error('obsolete list error')));
        expect(host.textContent).not.toContain('obsolete list error'); expect(props.onClone).toHaveBeenCalledTimes(1);
    });
    it('retains the initiating owner callback and displays its stale-editor refusal without calling a newer owner', async () => {
        const clone = deferred(); vi.mocked(api.post).mockReturnValue(clone.promise as never);
        const originalOwner = vi.fn(() => { throw new Error('Editor changed during clone; newer edits retained.'); });
        props.onClone = originalOwner;
        await open(); await select(0, 'original-job'); await click('Clone selected original job');
        const newerOwner = vi.fn(); props.onClone = newerOwner; await render();
        await act(async () => clone.resolve({ data: cloneFixture }));
        expect(originalOwner).toHaveBeenCalledExactlyOnceWith(cloneFixture);
        expect(newerOwner).not.toHaveBeenCalled();
        expect(host.textContent).toContain('Editor changed during clone; newer edits retained.');
        expect(host.textContent).not.toContain('Original job cloned into an unsaved draft.');
    });
    it('fences stale recent history across generation changes and lists fresh on another explicit request', async () => {
        const list = deferred(); vi.mocked(api.get).mockReturnValueOnce(list.promise as never);
        await open(); await click('Load recent jobs'); props.generation = 10; await render();
        await act(async () => list.resolve({ data: { rows: [{ job_id: 'stale', status: 'failed' }] } }));
        expect(host.textContent).not.toContain('stale'); expect(host.querySelectorAll('select')).toHaveLength(1);
        await click('Load recent jobs');
        expect(api.get).toHaveBeenLastCalledWith('/api/bioxp/protocols/jobs', { params: { expected_connection_generation: 10 } });
        expect(host.textContent).toContain('history-job');
    });
    it('prevents same-turn duplicate clone calls and respects the explicit disabled prop', async () => {
        const clone = deferred(); vi.mocked(api.post).mockReturnValue(clone.promise as never);
        await open(); await select(0, 'original-job');
        await act(async () => { button('Clone selected original job').click(); button('Clone selected original job').click(); });
        expect(api.post).toHaveBeenCalledTimes(1);
        props.disabled = true; await render(); await act(async () => clone.resolve({ data: cloneFixture }));
        expect(props.onClone).not.toHaveBeenCalled(); expect(button('Clone selected original job').disabled).toBe(true);
    });
});
