import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import destinations from '../fixtures/bioxp_deck_admission_catalog.json';
import manual from '../fixtures/bioxpManualCatalogProducer.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';

// Real cockpit, deck submission owner and Axios serialization; only HTTP transport and camera are replaced.
vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));

let host: HTMLDivElement, root: Root, client: QueryClient, catalog: any;
let held: Array<{ url: string; body: any; resolve: () => void }>;
let requests: Array<{ method: string; url: string; body?: any }>;
let receipts: Map<string, any>;
const adapter = api.defaults.adapter;
const advance = async (ms = 5) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const live = () => host.querySelector<HTMLElement>('[data-testid="oem-deck-movement"]')!;
const writes = () => requests.filter(r => r.method !== 'get' && r.url.includes('/v2/actions/'));
const button = (label: string, owner: ParentNode = live()) => {
    const el = [...owner.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === label);
    expect(el, label).toBeTruthy(); return el!;
};
const click = async (el: Element) => { await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 })); }); await advance(); };
const well = (station: string, address: string) => live().querySelector(`[data-station="${station}"][data-well="${address}"]`)!;
const move = (target: string) => live().querySelector(`svg g[data-move-to="${target}"]`)!;
const status = () => live().querySelector('[data-testid="deck-lane-status"]')?.textContent;
const response = (config: any, data: any) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
const admit = (url: string, body: any) => {
    const id = `cmd-${body.idempotency_key}`;
    const row = { ...structuredClone(park), action_id: url.split('/').at(-1), command_id: id, status: 'dispatched', terminal: false,
        terminal_receipt_id: null, completion_class: null };
    receipts.set(id, row); return row;
};
/** The robot finishes every admitted command; the real receipt observer retires it. */
const finishAll = async () => {
    for (const [id, row] of receipts) receipts.set(id, { ...row, status: 'completed', terminal: true, completion_class: 'completed' });
    await act(async () => { await client.refetchQueries({ queryKey: ['bioxp'] }); }); await advance(50);
};
const mount = async () => {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/bioxp']}>
        <BioXpCockpit initialTab="live-deck" /></MemoryRouter></QueryClientProvider>)); await advance();
};
const envelope = (epochs: Record<string, number>) => ({ schema_version: 'bioxp.operator_action_request.v2', expected_connection_generation: 7,
    expected_ownership_generation: catalog.dashboard.ownership_generation, expected_board_epoch_by_board: epochs,
    idempotency_key: expect.any(String) });
const SEQUENCE_EPOCHS = { '4': 65, '5': 2 }, RETARGET_EPOCHS = { '4': 66, '5': 3 };

beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('crypto', webcrypto);
    held = []; requests = []; receipts = new Map();
    catalog = structuredClone(metadata.catalog);
    catalog.dashboard.generated_at = Date.now() / 1000;
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.dashboard.deck = structuredClone(destinations.deck);
    const row = (action_id: string, epochs: Record<string, number>) => ({ action_id, request_schema_version: 'bioxp.operator_action_request.v2',
        response_schema_version: 'bioxp.operator_action_receipt.v2', interrupt: false, enabled: true, disabled_reason: null, expected_board_epoch_by_board: epochs });
    catalog.actions = catalog.actions.filter((a: any) => !a.action_id.startsWith('oem.deck.move_to_')).concat([
        structuredClone(destinations.action), row('oem.deck.move_to_well', { '4': 64, '5': 1 }),
        row('oem.xy.move_sequence', SEQUENCE_EPOCHS), row('oem.xy.retarget', RETARGET_EPOCHS)]);
    api.defaults.adapter = async config => {
        const method = config.method ?? 'get', url = config.url ?? '';
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method, url, body });
        if (method === 'get') {
            if (url === '/api/bioxp/status') return response(config, { connection: { generation: 7, active: true, configured: true, reachable: true, runtime_ready: true, hardware_fresh: true }, mutation_access: { enabled: true } });
            if (url.endsWith('/catalog')) return response(config, catalogWireFixture({ schema_version: 'bioxp.operator_control_catalog.v1',
                dashboard: metadata.catalog.dashboard.telemetry, actions: manual.referenced, canonical: structuredClone(catalog) }, config.params?.view));
            if (url.endsWith('/history')) return response(config, { items: [], next_cursor: null, limit: 8 });
            if (url === '/api/bioxp/calibration-settings') return response(config, { active_motion_positions: [] });
            if (url.includes('/receipts/')) return response(config, receipts.get(decodeURIComponent(url.split('/').at(-1)!)));
            if (url.endsWith('/updates')) return new Promise(() => undefined);
            if (url === '/api/bioxp/operator-controls/deck/state') return response(config, { schema_version: 'bioxp.operator_deck_state.v1', semantic: {}, movable_objects: {}, physical_observation: false });
            if (url === '/api/bioxp/operator-controls/tip-trays') return response(config, { schema_version: 'bioxp.operator_tip_trays.v1', trays: [], physical_observation: false });
            throw new Error(`Unexpected GET ${url}`);
        }
        if (url.includes('/v2/actions/')) {
            return new Promise(resolve => held.push({ url, body, resolve: () => resolve(response(config, admit(url, body))) }));
        }
        if (url.includes('/v2/interrupts/')) return response(config, { ...park, action_id: url.split('/').at(-1), command_id: 'stop' });
        throw new Error(`Unexpected mutation ${method} ${url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); vi.useRealTimers(); });
const release = async () => { const next = held.shift()!; await act(async () => next.resolve()); await advance(); };

it('defaults to Full report with the three plain mode buttons and unchanged per-click requests', async () => {
    await mount();
    const group = live().querySelector('[role="group"][aria-label="Movement mode"]')!;
    expect([...group.querySelectorAll('button')].map(b => [b.textContent, b.getAttribute('aria-pressed')]))
        .toEqual([['Full report', 'true'], ['Fast lane', 'false'], ['Retarget', 'false']]);
    expect(status()).toBeUndefined();
    await click(well('LOC_TC', 'D7'));
    expect(writes().map(w => [w.url, w.body])).toEqual([['/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_well',
        { ...envelope({ '4': 64, '5': 1 }), inputs: { location_id: 2, well: 'D7', position_flag: 1 } }]]);
    await release();
    await click(move('LOC_TC'));
    expect(writes().at(-1)).toEqual({ method: 'post', url: '/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_location',
        body: { ...envelope(destinations.action.expected_board_epoch_by_board), inputs: { target: 'LOC_TC', camera_offset: false } } });
    expect(writes()).toHaveLength(2);
});

it('Fast lane batches pending clicks into one sequence and sends clicks made while it runs as the next sequence', async () => {
    await mount();
    await click(button('Fast lane'));
    expect(button('Fast lane').getAttribute('aria-pressed')).toBe('true');
    expect(button('Stop X', host).disabled).toBe(false); // Stops stay available
    await click(well('LOC_TC', 'A1'));
    // The first click goes out alone immediately; the next ones queue behind the in-flight sequence.
    await click(move('WASTE_BIN')); await click(well('LOC_OC', 'H12'));
    expect(writes()).toHaveLength(1);
    expect(writes()[0]).toEqual({ method: 'post', url: '/api/bioxp/operator-controls/v2/actions/oem.xy.move_sequence',
        body: { ...envelope(SEQUENCE_EPOCHS), inputs: { targets: [{ location_id: 2, well: 'A1' }] } } });
    expect(status()).toBe('Active: Thermal cycler · A1 · Queued: WASTE_BIN → Output chiller · H12');
    await release();
    await click(well('LOC_MS', 'B2'));
    expect(writes()).toHaveLength(1); // still running on the robot
    await finishAll();
    expect(writes()).toHaveLength(2);
    expect(writes()[1].body).toEqual({ ...envelope(SEQUENCE_EPOCHS), inputs: { targets: [
        { target: 'WASTE_BIN', camera_offset: false }, { location_id: 1, well: 'H12' }, { location_id: 0, well: 'B2' }] } });
    expect(writes()[1].url).toBe('/api/bioxp/operator-controls/v2/actions/oem.xy.move_sequence');
    expect(new Set(writes().map(w => w.body.idempotency_key)).size).toBe(2);
    await release(); await finishAll();
    expect(writes()).toHaveLength(2);
    expect(status()).toBe('None queued');
});

it('Fast lane station clicks carry the current camera-offset choice', async () => {
    await mount();
    const select = [...live().querySelectorAll('label')].find(el => el.textContent?.trim().startsWith('Robot destination'))!.querySelector('select')!;
    await act(async () => { select.value = 'LOC_TC'; select.dispatchEvent(new Event('change', { bubbles: true })); }); await advance();
    await click(live().querySelector<HTMLInputElement>('input[type="checkbox"]')!);
    await click(button('Fast lane'));
    await click(move('LOC_RC')); await click(move('LOC_PARK'));
    await release(); await finishAll();
    expect(writes().map(w => w.body.inputs)).toEqual([
        { targets: [{ target: 'LOC_RC', camera_offset: true }] },
        { targets: [{ target: 'LOC_PARK', camera_offset: false }] }]); // no camera offset option for park
});

it('Retarget keeps one request in flight and coalesces clicks to the newest target', async () => {
    await mount();
    await click(button('Retarget'));
    await click(well('LOC_TC', 'A1'));
    await click(well('LOC_TC', 'B1')); await click(move('LOC_RC')); await click(well('LOC_OC', 'C3'));
    expect(writes()).toHaveLength(1);
    expect(writes()[0]).toEqual({ method: 'post', url: '/api/bioxp/operator-controls/v2/actions/oem.xy.retarget',
        body: { ...envelope(RETARGET_EPOCHS), inputs: { target: { location_id: 2, well: 'A1' } } } });
    expect(status()).toBe('Active: Thermal cycler · A1 · Queued: Output chiller · C3');
    await release(); await finishAll();
    expect(writes()).toHaveLength(2);
    expect(writes()[1].body).toEqual({ ...envelope(RETARGET_EPOCHS), inputs: { target: { location_id: 1, well: 'C3' } } });
    await release(); await finishAll();
    expect(writes()).toHaveLength(2);
});

it('switching back to Full report drops unsent lane clicks and restores the per-click requests', async () => {
    await mount();
    await click(button('Fast lane'));
    await click(well('LOC_TC', 'A1')); await click(well('LOC_TC', 'B1'));
    await click(button('Full report'));
    await release(); await finishAll();
    expect(writes()).toHaveLength(1);
    await click(well('LOC_TC', 'C1'));
    expect(writes().at(-1)?.url).toBe('/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_well');
    expect(writes().at(-1)?.body.inputs).toEqual({ location_id: 2, well: 'C1', position_flag: 1 });
});
