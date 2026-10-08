import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import canonicalProducer from '../fixtures/bioxp_deck_canonical_queue.json';
import deckCatalog from '../fixtures/bioxp_deck_admission_catalog.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';
const state = vi.hoisted(() => ({ generation: 7, error: false, active: true }));
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../../src/lib/bioxpClient', async importOriginal => ({
    ...await importOriginal<typeof import('../../src/lib/bioxpClient')>(),
    useBioXpStatus: () => ({ data: { connection: { active: state.active, configured: true, generation: state.generation, reachable: true, runtime_ready: true }, mutation_access: { enabled: true } }, isError: state.error }),
    useBioXpOperatorActionHistory: () => ({ data: { items: [], next_cursor: null }, error: null }),
}));
vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
let root: Root; let container: HTMLDivElement; let client: QueryClient;
let catalog: any;
let admissions: Array<{ body: any; resolve: (value: any) => void; reject: (error: unknown) => void }>;
let lookup: any;
let completed: Set<number>;
let updateSequence: number, updateReply: ((value: any) => void) | undefined;
const updateWire = (ids: string[] = []) => ({ schema_version: 'bioxp.operator_updates.v1', source_instance_id: 'queue-fixture', ownership_generation: 7, next_after_sequence: updateSequence, pose_sequence: 0, changed_command_ids: ids, active_command_ids: [], has_more: false, reset: false, pose: null });
const notify = async (ids: string[] = ['queue-0']) => { updateSequence++; await act(async () => updateReply?.({ data: updateWire(ids) })); await advance(); };
const receipt = (index: number) => ({ ...park, command_id: `queue-${index}`, terminal_receipt_id: null, status: 'dispatched', terminal: false, completion_class: null });
const advance = async (ms = 1) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const render = async () => { await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit initialTab="live-deck" /></QueryClientProvider>)); await advance(); };
const panel = () => container.querySelector('[data-testid="oem-deck-movement"]')!;
const move = () => [...panel().querySelectorAll('button')].find(b => b.textContent === 'Move to destination')!;
const submit = async (target: string, cameraOffset = false) => {
    await act(async () => { const select = [...panel().querySelectorAll('label')].find(label => label.textContent?.startsWith('Robot destination'))!.querySelector('select')!; select.value = target; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => { const checkbox = panel().querySelector('input[type=checkbox]') as HTMLInputElement; if (!checkbox.disabled && checkbox.checked !== cameraOffset) checkbox.click(); });
    await act(async () => move().click()); await advance();
};
const accept = async (index: number) => { await act(async () => admissions[index].resolve({ data: receipt(index) })); await advance(); };
beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks(); Object.assign(state, { generation: 7, active: true, error: false });
    catalog = structuredClone(metadata.catalog);
    catalog.dashboard.generated_at = Date.now() / 1000;
    catalog.dashboard.deck = structuredClone(deckCatalog.deck);
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.dashboard.command_queue = { schema_version: 'bioxp.oem_command_queue.v1', generated_at: Date.now() / 1000, items: [] };
    catalog.actions = catalog.actions.filter((a: any) => a.action_id !== 'oem.deck.move_to_location').concat(structuredClone(deckCatalog.action));
    admissions = []; lookup = null; completed = new Set(); updateSequence = 0; updateReply = undefined;
    vi.mocked(api.get).mockImplementation(async (url, config) => {
        if (url.endsWith('/updates')) {
            if (config?.params?.after_sequence === undefined) return { data: updateWire() };
            return new Promise((resolve, reject) => {
                const abort = () => reject(new Error('cancelled'));
                updateReply = value => { config?.signal?.removeEventListener?.('abort', abort); resolve(value); };
                config?.signal?.addEventListener?.('abort', abort);
            });
        }
        if (url === "/api/bioxp/calibration-settings") return { data: { active_motion_positions: [] } };
        if (url.endsWith('/catalog')) { catalog.dashboard.generated_at = Date.now() / 1000; return { data: catalogWireFixture({ actions: [], dashboard: {}, canonical: structuredClone(catalog) }, config?.params?.view) }; }
        if (url.includes('/requests/')) { if (lookup == null) throw { response: { status: 404 } }; return { data: lookup }; }
        if (url.includes('/receipts/')) { const id = Number(url.split('queue-')[1]); return { data: completed.has(id) ? { ...receipt(id), terminal: true, status: 'completed', completion_class: 'completed' } : receipt(id) }; }
        throw new Error(`Unexpected GET ${url}`);
    });
    vi.mocked(api.post).mockImplementation((url, body) => {
        if (!url.endsWith('/oem.deck.move_to_location')) return Promise.resolve({ data: { ...park, action_id: 'oem.x.stop' } });
        return new Promise((resolve, reject) => admissions.push({ body, resolve, reject }));
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); vi.useRealTimers(); });
it.each([2, 7])('allows %s successive intents after HTTP admission, with no receipt lock', async count => {
    await render(); const targets = deckCatalog.action.destination_options.slice(0, count).map(x => x.target);
    for (const [index, target] of targets.entries()) {
        await submit(target, index % 2 === 0);
        await act(async () => { move().click(); move().click(); });
        expect(admissions).toHaveLength(index + 1); // in-flight duplicates dropped
        expect(move().disabled).toBe(true);
        expect(admissions[index].body.inputs).toEqual({ target, camera_offset: index % 2 === 0 && deckCatalog.action.destination_options[index].camera_offset_option });
        await accept(index); // dispatched/nonterminal receipt is not a host lock
    }
    expect(new Set(admissions.map(x => x.body.idempotency_key)).size).toBe(count);
    for (let i = 0; i < count; i++) expect(panel().textContent).toContain(`queue-${i}`);
});
it.each(['timeout', 'validation502', 'malformed'])('keeps %s visible without replay or a retained-receipt lock', async mode => {
    await render(); await submit('LOC_TC'); await submit('LOC_OC');
    expect(admissions).toHaveLength(1);
    await act(async () => mode === 'malformed' ? admissions[0].resolve({ data: null }) : admissions[0].reject(mode === 'timeout' ? new Error('timeout') : { response: { status: 502, data: { detail: { error: 'post_dispatch_receipt_validation_failed', command_id: 'queue-0', status_path: '/operator/v2/actions/receipts/queue-0', retry_guidance: 'do_not_resubmit_reconcile_by_command_id' } } } }));
    await advance(2001); expect(admissions).toHaveLength(1); expect(panel().textContent).toContain('admission uncertain');
    state.error = true; await render(); lookup = { ...receipt(0), status: 'completed', terminal: true };
    await notify(); expect(admissions).toHaveLength(1); expect(panel().textContent).not.toContain('admission uncertain');
    await submit('LOC_OC'); expect(admissions).toHaveLength(2); await accept(1);
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/requests/'), expect.objectContaining({ timeout: 12000, signal: expect.any(AbortSignal), params: { expected_connection_generation: 7 } }));
});
it('fences late admission responses and never resumes dropped old-generation clicks', async () => {
    await render(); await submit('LOC_TC'); await submit('LOC_OC'); state.generation = 8; await render();
    await accept(0); await advance(4000); expect(admissions).toHaveLength(1);
    expect(panel().textContent).toContain('queue-0');
    expect([...panel().querySelectorAll('button')].find(b => b.textContent?.includes('queue-0'))?.disabled).toBe(true);
});
it('renders canonical active/queued identities and distinguishes missing from empty', async () => {
    catalog = structuredClone(canonicalProducer);
    await render();
    for (const row of canonicalProducer.dashboard.command_queue.items) expect(panel().textContent).toContain(row.command_id);
    delete catalog.dashboard.command_queue; await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp'] }); }); await advance();
    expect(panel().textContent).toContain('Robot command queue: unknown');
});
it('retains a definite refusal and leaves addressed Stop independent of held admission', async () => {
    await render(); await submit('LOC_TC'); await submit('LOC_OC');
    const stop = [...container.querySelectorAll('button')].find(b => b.textContent === 'Stop X')!;
    expect(stop.disabled).toBe(false); await act(async () => stop.click());
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining('interrupts/oem.x.stop'), expect.anything());
    await act(async () => admissions[0].reject({ response: { status: 422, data: { detail: 'invalid target' } } })); await advance();
    expect(panel().textContent).toContain('not accepted'); expect(admissions).toHaveLength(1);
    await submit('LOC_OC'); expect(admissions).toHaveLength(2);
});
it('bounds settled local rows and receipt queries over ongoing synchronous admissions', async () => {
    await render();
    vi.mocked(api.post).mockImplementation(async (_url, body: any) => {
        const id = admissions.length;
        admissions.push({ body, resolve: () => {}, reject: () => {} });
        return { data: receipt(id) };
    });
    for (let start = 0; start < 30; start += 3) {
        for (let i = start; i < start + 3; i++) await submit(i % 2 ? 'LOC_TC' : 'LOC_OC');
        expect(admissions).toHaveLength(start + 3);
        for (let i = start; i < start + 3; i++) completed.add(i);
        await notify([0, 1, 2].map(i => `queue-${start + i}`)); await advance();
        expect(panel().querySelectorAll('[data-request-key]')).toHaveLength(0);
        expect(client.getQueryCache().getAll().filter(q => q.queryKey[3] === 'receipt' && q.queryKey[4] != null).length).toBeLessThanOrEqual(2); // existing selected and latest result only
        expect((panel().querySelector('[data-testid="deck-submissions"]') as HTMLDetailsElement).open).toBe(false);
    }
    expect(new Set(admissions.map(x => x.body.idempotency_key)).size).toBe(30);
});

it.each(['missing', 'aged'])('submits with %s telemetry/reference/epoch displays, retaining robot refusal', async evidence => {
    if (evidence === 'missing') catalog.dashboard.telemetry = null;
    else {
        catalog.dashboard.telemetry.motion.enabled = true;
        catalog.dashboard.telemetry.snapshot.freshness = { state: 'stale', age_s: 99999 };
        catalog.dashboard.telemetry.axes = [];
    }
    catalog.dashboard.deck = null;
    const action = catalog.actions.find((a: any) => a.action_id === 'oem.deck.move_to_location');
    action.expected_board_epoch_by_board = null;
    state.error = true;
    await render(); await submit('LOC_OC');
    expect(admissions).toHaveLength(1);
    expect(admissions[0].body.expected_board_epoch_by_board).toEqual({});
    expect(admissions[0].body.inputs).toEqual({ target: 'LOC_OC', camera_offset: false });
    if (evidence === 'aged') expect(container.querySelector('[aria-label="Controller preparation and recovery"]')?.textContent).toContain('Enabled');
    else expect(container.textContent).toContain('Unknown');
    await act(async () => admissions[0].reject({ response: { status: 409, data: { detail: 'OEM door interlock denied' } } }));
    await advance();
    expect(panel().textContent).toContain('OEM door interlock denied');
    expect(admissions).toHaveLength(1);
    action.enabled = false; action.disabled_reason = 'OEM controller denied';
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'operator-controls', 'catalog'] }); });
    await advance();
    expect(move().disabled).toBe(true);
    expect(panel().textContent).toContain('OEM controller denied');
});

it('terminal notification refreshes its receipt without broad status/catalog invalidation or POST replay', async () => {
    await render(); await submit('LOC_TC'); await accept(0);
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    completed.add(0);
    await notify(); await advance();
    for (const key of [
        ['bioxp', 'status'], ['bioxp', 'operator-controls', 'catalog'],
        ['bioxp', 'operator-controls', 'catalog'],
    ]) expect(invalidate.mock.calls.filter(([options]) => JSON.stringify(options?.queryKey) === JSON.stringify(key))).toHaveLength(0);
    await act(async () => { await client.refetchQueries({ queryKey: ['bioxp', 'operator-controls', 'v2', 'receipt'] }); });
    await advance();
    expect(invalidate.mock.calls.filter(([options]) => JSON.stringify(options?.queryKey) === JSON.stringify(['bioxp', 'status']))).toHaveLength(0);
    expect(admissions).toHaveLength(1);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.get).mock.calls.every(([url]) => !url.includes('collect'))).toBe(true);
});

it('bounds a stalled browser admission by the existing request policy without retry', async () => {
    await render();
    vi.mocked(api.post).mockImplementation((_url, body: any, options) => new Promise((_resolve, reject) => {
        admissions.push({ body, resolve: () => {}, reject });
        setTimeout(() => reject(new Error('browser admission timeout')), options?.timeout);
    }));
    await submit('LOC_TC'); await submit('LOC_OC');
    await advance(12001);
    expect(admissions).toHaveLength(1);
    expect(panel().textContent).toContain('admission uncertain');
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining('/oem.deck.move_to_location'), expect.anything(), { timeout: 12000 });
    await advance(6000); expect(admissions).toHaveLength(1);
});
