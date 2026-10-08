import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import * as ReactQuery from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import deckCatalog from '../fixtures/bioxp_deck_admission_catalog.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
// Only unrelated status/history and camera presentation are stubbed; command,
// request-key reconciliation, catalog parsing and interrupt hooks remain real.
vi.mock('../../src/lib/bioxpClient', async importOriginal => ({
    ...await importOriginal<typeof import('../../src/lib/bioxpClient')>(),
    useBioXpStatus: () => ({ data: { connection: { active: true, configured: true, generation: 7, reachable: true, runtime_ready: true }, mutation_access: { enabled: true } }, isError: false }),
    useBioXpOperatorControlCatalog: () => ({ data: { actions: [], dashboard: {} }, error: null }),
    useBioXpOperatorActionHistory: () => ({ data: { items: [], next_cursor: null }, error: null }),
}));
vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
vi.mock('../../src/components/BioXpQuickDashboard', () => ({ BioXpQuickDashboard: () => null }));

let catalog: any;
let admissions: Array<{ body: any; resolve: (value: any) => void; reject: (error: unknown) => void }>;
let lookup: any;
const receipt = { ...park, command_id: 'identity-command', terminal_receipt_id: null, status: 'dispatched', terminal: false, completion_class: null };
const lifetimes: Array<{ root: Root; client: ReactQuery.QueryClient; frame: HTMLIFrameElement }> = [];
const advance = async (ms = 1) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

async function newDocument() {
    // Reset the product module, not just React component state. Retain the test
    // renderer's React/context identities so the mounted hooks use one renderer.
    vi.resetModules();
    vi.doMock('react', () => React);
    vi.doMock('@tanstack/react-query', () => ReactQuery);
    const { BioXpCockpit } = await import('../../src/components/BioXpCockpit');
    const frame = document.createElement('iframe'); document.body.append(frame);
    const doc = frame.contentDocument!;
    const nativeCrypto = frame.contentWindow!.crypto;
    const entropy = vi.fn(nativeCrypto.getRandomValues.bind(nativeCrypto));
    const cryptoWithoutUUID = { getRandomValues: entropy };
    vi.stubGlobal('crypto', cryptoWithoutUUID);
    const container = doc.createElement('div'); doc.body.append(container);
    const client = new ReactQuery.QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const root = createRoot(container);
    const lifetime = { root, client, frame }; lifetimes.push(lifetime);
    await act(async () => root.render(<ReactQuery.QueryClientProvider client={client}><BioXpCockpit /></ReactQuery.QueryClientProvider>));
    await advance();
    const panel = () => container.querySelector('[data-testid="oem-deck-movement"]')!;
    const click = async (label: string) => {
        const button = [...container.querySelectorAll('button')].find(b => b.textContent === label)!;
        expect(button).toBeDefined(); expect(button.disabled).toBe(false);
        await act(async () => button.click()); await advance();
    };
    const submit = async () => {
        const select = panel().querySelector('select')!;
        await act(async () => { select.value = 'LOC_TC'; select.dispatchEvent(new frame.contentWindow!.Event('change', { bubbles: true })); });
        await click('Move to destination');
    };
    const close = async () => {
        await act(async () => root.unmount()); client.clear(); frame.remove();
        lifetimes.splice(lifetimes.indexOf(lifetime), 1);
    };
    return { doc, BioXpCockpit, container, entropy, cryptoWithoutUUID, panel, click, submit, close };
}
beforeEach(() => {
    vi.useFakeTimers(); vi.resetAllMocks();
    catalog = structuredClone(metadata.catalog);
    catalog.dashboard.deck = structuredClone(deckCatalog.deck);
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.dashboard.command_queue = { schema_version: 'bioxp.oem_command_queue.v1', generated_at: Date.now() / 1000, items: [] };
    catalog.actions = catalog.actions.filter((a: any) => a.action_id !== 'oem.deck.move_to_location').concat(structuredClone(deckCatalog.action));
    admissions = []; lookup = null;
    vi.mocked(api.get).mockImplementation(async url => {
        if (url.includes('/v2/catalog')) { catalog.dashboard.generated_at = Date.now() / 1000; return { data: structuredClone(catalog) }; }
        if (url.includes('/requests/')) { if (lookup == null) throw { response: { status: 404 } }; return { data: lookup }; }
        if (url.includes('/receipts/')) return { data: receipt };
        throw new Error(`Unexpected GET ${url}`);
    });
    vi.mocked(api.post).mockImplementation((url, body) => {
        if (url.endsWith('/oem.x.stop')) return Promise.resolve({ data: { ...park, action_id: 'oem.x.stop' } });
        expect(url).toContain('/oem.deck.move_to_location');
        return new Promise((resolve, reject) => admissions.push({ body, resolve, reject }));
    });
});
afterEach(async () => {
    for (const { root, client, frame } of lifetimes.splice(0)) { await act(async () => root.unmount()); client.clear(); frame.remove(); }
    vi.unstubAllGlobals(); vi.useRealTimers(); vi.doUnmock('react'); vi.doUnmock('@tanstack/react-query');
});

it('uses distinct durable keys in independent documents/modules and a reloaded document without randomUUID', async () => {
    const first = await newDocument(); await first.submit();
    const second = await newDocument(); await second.submit();
    expect(second.doc).not.toBe(first.doc);
    expect(second.BioXpCockpit).not.toBe(first.BioXpCockpit);
    await first.close();
    const reload = await newDocument(); await reload.submit();
    expect(reload.doc).not.toBe(first.doc);
    expect(reload.BioXpCockpit).not.toBe(first.BioXpCockpit);
    expect(admissions).toHaveLength(3);
    // This assertion must fail with the exact pre-repair product: all three
    // genuinely fresh modules start the old sequence at bioxp-oem-1.
    expect(new Set(admissions.map(x => x.body.idempotency_key)).size).toBe(3);
    for (const view of [first, second, reload]) expect(view.entropy).toHaveBeenCalledTimes(1);
    for (const { body } of admissions) {
        expect(body).toEqual({ schema_version: 'bioxp.operator_action_request.v2', expected_connection_generation: 7,
            expected_ownership_generation: catalog.dashboard.ownership_generation,
            expected_board_epoch_by_board: deckCatalog.action.expected_board_epoch_by_board,
            idempotency_key: expect.stringMatching(/^bioxp-oem-[0-9a-f]{32}$/),
            inputs: { target: 'LOC_TC', camera_offset: false } }); // action ID is carried in the URL by the real transport
    }
});
it('retains the original fallback key through timeout/404 reconciliation without replay and keeps addressed Stop independent', async () => {
    const view = await newDocument(); await view.submit();
    const original = structuredClone(admissions[0].body);
    await act(async () => admissions[0].reject(new Error('timeout'))); await advance(4001);
    expect(view.panel().textContent).toContain('admission uncertain');
    const requests = () => vi.mocked(api.get).mock.calls.filter(([url]) => url.includes('/requests/'));
    expect(requests().length).toBeGreaterThan(0);
    for (const [url] of requests()) expect(url).toContain(`/requests/${original.idempotency_key}`);
    expect(admissions).toHaveLength(1); expect(admissions[0].body).toEqual(original);
    await view.click('Stop X');
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining('/interrupts/oem.x.stop'), expect.objectContaining({
        schema_version: 'bioxp.operator_interrupt_request.v1', expected_connection_generation: 7,
        idempotency_key: expect.stringMatching(/^bioxp-stop-[0-9a-f]{32}$/), observed_board_epoch_by_board: {},
    }));
    lookup = { ...receipt, terminal: true, status: 'completed', completion_class: 'completed' };
    await advance(4001);
    expect(view.panel().textContent).not.toContain('admission uncertain');
    expect(admissions).toHaveLength(1); expect(admissions[0].body).toEqual(original);
    expect(view.entropy).toHaveBeenCalledTimes(2);
});
it.each(['missing crypto', 'missing getRandomValues', 'throwing getRandomValues', 'throwing randomUUID'])('reports %s before either caller sends a request', async mode => {
    const view = await newDocument();
    vi.stubGlobal('crypto', mode === 'missing crypto' ? undefined : mode === 'missing getRandomValues' ? {} : mode === 'throwing randomUUID'
        ? { randomUUID: () => { throw new Error('entropy unavailable'); } }
        : { getRandomValues: () => { throw new Error('entropy unavailable'); } });
    await view.submit(); await view.click('Stop X');
    expect(api.post).not.toHaveBeenCalled();
    expect(view.container.textContent).toContain('Request not sent: this browser could not generate a secure request identity. Existing requests are unchanged.');
    expect(view.panel().querySelectorAll('[data-request-key]')).toHaveLength(0);
});
it('uses randomUUID without requiring getRandomValues', async () => {
    const view = await newDocument();
    const uuid = vi.fn().mockReturnValueOnce('f2a79f0c-2260-4992-90cb-64fe8b9aaade').mockReturnValueOnce('c8b72808-e71a-4c52-a6b2-1ea786bdbd30');
    vi.stubGlobal('crypto', { randomUUID: uuid });
    await view.submit(); await view.click('Stop X');
    expect(admissions[0].body.idempotency_key).toBe('f2a79f0c-2260-4992-90cb-64fe8b9aaade');
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining('/interrupts/oem.x.stop'), expect.objectContaining({ idempotency_key: 'c8b72808-e71a-4c52-a6b2-1ea786bdbd30' }));
    expect(view.entropy).not.toHaveBeenCalled(); expect(uuid).toHaveBeenCalledTimes(2);
});
it('does not rekey an uncertain request when entropy later fails', async () => {
    const view = await newDocument(); await view.submit();
    const key = admissions[0].body.idempotency_key;
    await act(async () => admissions[0].reject(new Error('timeout'))); await advance();
    vi.stubGlobal('crypto', {}); await view.submit(); await view.click('Stop X'); await advance(4001);
    expect(admissions).toHaveLength(1); expect(api.post).toHaveBeenCalledTimes(1);
    expect(view.panel().textContent).toContain('admission uncertain');
    expect(view.panel().querySelectorAll('[data-request-key]')).toHaveLength(1);
    for (const [url] of vi.mocked(api.get).mock.calls.filter(([url]) => url.includes('/requests/'))) expect(url).toContain(`/requests/${key}`);
    lookup = { ...receipt, terminal: true, status: 'completed', completion_class: 'completed' }; await advance(4001);
    expect(view.panel().textContent).not.toContain('admission uncertain'); expect(api.post).toHaveBeenCalledTimes(1);
});
