import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import destinations from '../fixtures/bioxp_deck_admission_catalog.json';
import manual from '../fixtures/bioxpManualCatalogProducer.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';
import type { BioXpOperatorUpdates } from '../../src/lib/bioxpClient';

// No component or hook replacement: real cockpit, camera, map and Axios serializer.
let host: HTMLDivElement, root: Root, client: QueryClient, catalog: any;
let generation: number, wire: BioXpOperatorUpdates, held: Array<{ send: (value: BioXpOperatorUpdates) => void; cancel: () => void }>;
let requests: Array<{ method: string; url: string; body: any; params: any }>;
let receipts: Map<string, any>;
const adapter = api.defaults.adapter;
const tick = async (ms = 5) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
async function mount() { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><BioXpCockpit initialTab="live-deck" /></MemoryRouter></QueryClientProvider>)); await tick(); }
const writes = () => requests.filter(r => r.method !== 'get');
const readReceipts = () => requests.filter(r => r.url.includes('/receipts/'));
const click = async (element: Element) => { expect(element).toBeTruthy(); await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))); await tick(); };
const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!;
const transform = () => host.querySelector('[data-gun-module="0"]')?.getAttribute('transform');
async function publish(next: Partial<BioXpOperatorUpdates>) { wire = { ...wire, ...next }; await act(async () => { expect(held).toHaveLength(1); held[0].send(structuredClone(wire)); }); await tick(); }
async function pick(target: string) {
    await act(async () => { const select = [...host.querySelectorAll('label')].find(e => e.textContent?.startsWith('Robot destination'))!.querySelector('select')!; select.value = target; select.dispatchEvent(new Event('change', { bubbles: true })); }); await tick();
}
beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('crypto', webcrypto);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    generation = 7; requests = []; receipts = new Map(); held = [];
    wire = { schema_version: 'bioxp.operator_updates.v1', source_instance_id: 'fixture-source', ownership_generation: 4,
        next_after_sequence: 1, pose_sequence: 1, changed_command_ids: [], active_command_ids: [], reset: false, has_more: false,
        pose: { ownership_generation: 4, axes: [{ axis: 'x', position_steps: 50000, observed_at: 1 }, { axis: 'y', position_steps: 10000, observed_at: 1 }] } };
    catalog = structuredClone(metadata.catalog); catalog.dashboard.generated_at = 0; // deliberately stale throughout
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = []; catalog.dashboard.deck = structuredClone(destinations.deck);
    catalog.actions = catalog.actions.filter((a: any) => !a.action_id.startsWith('oem.deck.move_to_')).concat([
        structuredClone(destinations.action), { action_id: 'oem.deck.move_to_well', request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2', interrupt: false, enabled: true, disabled_reason: null, expected_board_epoch_by_board: { '4': 64, '5': 1 } },
    ]);
    api.defaults.adapter = async config => {
        const method = config.method!, url = config.url!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method, url, body, params: config.params });
        const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
        if (method === 'get') {
            if (url.endsWith('/updates')) {
                if (config.params.after_sequence === undefined) return response(structuredClone(wire));
                return new Promise((resolve, reject) => {
                    const clear = () => { held = held.filter(w => w !== wait); config.signal?.removeEventListener?.('abort', wait.cancel); };
                    const wait = { send: (value: BioXpOperatorUpdates) => { clear(); resolve(response(value)); }, cancel: () => { clear(); reject(new Error('aborted')); } };
                    held.push(wait); config.signal?.addEventListener?.('abort', wait.cancel);
                });
            }
            if (url === '/api/bioxp/status') return response({ connection: { generation, active: true, configured: true, reachable: true, runtime_ready: true, hardware_fresh: true }, mutation_access: { enabled: true } });
            if (url.endsWith('/catalog')) return response(catalogWireFixture({ schema_version: 'bioxp.operator_control_catalog.v1', dashboard: metadata.catalog.dashboard.telemetry, actions: manual.referenced, canonical: structuredClone(catalog) }, config.params?.view));
            if (url.includes('/history')) return response({ items: [], next_cursor: null, limit: 8 });
            if (url.includes('/receipts/')) return response(receipts.get(decodeURIComponent(url.split('/').at(-1)!)));
            if (url === '/api/bioxp/calibration-settings') return response({ active_motion_positions: [], saved_motion_positions: [] });
            if (url === '/api/bioxp/camera/stream/state') return response({ active: false, connection_generation: generation });
            if (url === '/api/bioxp/camera/status') return response({ available: false, connected: false, connection_generation: generation });
            if (url === '/api/bioxp/camera/illumination/state') return response({ connection_generation: generation, channels: [] });
            if (url === '/api/bioxp/protocols/jobs') return response({ rows: [] });
            if (url === '/api/bioxp/protocols/presets') return response([]);
            throw new Error(`Blocked fixture GET ${url}`);
        }
        if (url.includes('/v2/actions/oem.deck.move_to_') || url.includes('/v2/interrupts/')) {
            const id = `command-${writes().length}`, action = url.split('/').at(-1);
            const value = { ...park, action_id: action, command_id: id, status: 'dispatched', terminal: false, terminal_receipt_id: null, completion_class: null, requested_values: body.inputs };
            receipts.set(id, value); return response(value);
        }
        throw new Error(`Forbidden fixture mutation ${method} ${url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); vi.useRealTimers(); });

it('named then well FIFO intents, failed/Stopped outcomes, and feed gantry changes with stale catalog and zero observation mutations', async () => {
    await mount(); expect(writes()).toHaveLength(0); const initial = transform(); expect(initial).toBeTruthy();
    await pick('LOC_TC'); expect(transform()).toBe(initial); expect(writes()).toHaveLength(0);
    await click(button('Move to destination'));
    expect(writes()[0].body.inputs).toEqual({ target: 'LOC_TC', camera_offset: false });
    await click(host.querySelector('[data-station="LOC_OC"][data-well="H12"]')!);
    expect(writes()[1].body.inputs).toEqual({ location_id: 1, well: 'H12', position_flag: 1 });
    expect(writes().map(r => r.url.split('/').at(-1))).toEqual(['oem.deck.move_to_location', 'oem.deck.move_to_well']);
    expect(new Set(writes().map(r => r.body.idempotency_key)).size).toBe(2);
    receipts.set('command-1', { ...receipts.get('command-1'), terminal: true, status: 'failed', completion_class: 'recovery_required' });
    await publish({ next_after_sequence: 2, pose_sequence: 2, changed_command_ids: ['command-1'], pose: { ownership_generation: 4, axes: [{ axis: 'x', position_steps: 60000, observed_at: 2 }, { axis: 'y', position_steps: 15000, observed_at: 2 }] } });
    expect(transform()).not.toBe(initial); const namedPose = transform();
    expect(host.textContent).toContain('robot failed');
    expect(host.querySelector('[data-selected-station]')?.getAttribute('data-selected-station')).toBe('LOC_TC');
    const reads = readReceipts().length; const catalogs = requests.filter(r => r.url.endsWith('/catalog')).length;
    await publish({ next_after_sequence: 3, pose_sequence: 3, changed_command_ids: ['command-2'], pose: { ownership_generation: 4, axes: [{ axis: 'x', position_steps: 65000, observed_at: 3 }] } });
    expect(transform()).not.toBe(namedPose); expect(readReceipts().length).toBe(reads + 1);
    expect(requests.filter(r => r.url.endsWith('/catalog'))).toHaveLength(catalogs);
    expect(writes()).toHaveLength(2);
    await click(button('Stop X')); expect(writes()).toHaveLength(3);
    expect(writes()[2].url).toContain('/interrupts/oem.x.stop');
    receipts.set('command-2', { ...receipts.get('command-2'), terminal: true, status: 'stopped' });
    await publish({ next_after_sequence: 4, changed_command_ids: ['command-2'] });
    expect(host.textContent).toContain('robot stopped');
    const settledReads = readReceipts().length; await tick(60000);
    expect(readReceipts()).toHaveLength(settledReads); expect(writes()).toHaveLength(3);
    expect(host.textContent).toContain('robot failed');
});
it('one real retained camera across hidden/resume; open-only detail and unchanged coordinates from selection', async () => {
    await mount();
    const camera = host.querySelector('[data-testid="live-deck-camera"]'); expect(camera).toBeTruthy();
    expect(host.querySelectorAll('[data-testid="live-deck-camera"]')).toHaveLength(1);
    await pick('LOC_OC'); await click(button('Move to destination'));
    expect(readReceipts().filter(r => r.params.detail)).toHaveLength(0);
    const disclosure = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === 'Command details')!;
    await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); }); await tick();
    expect(readReceipts().filter(r => r.params.detail)).toHaveLength(1);
    await act(async () => { disclosure.open = false; disclosure.dispatchEvent(new Event('toggle')); }); await tick();
    await publish({ next_after_sequence: 2, changed_command_ids: ['command-1'] });
    expect(readReceipts().filter(r => r.params.detail)).toHaveLength(1);
    const before = transform(); await pick('LOC_TC'); expect(transform()).toBe(before);
    await click(host.querySelector('#control-tab-robot')!); await click(host.querySelector('#control-tab-live-deck')!);
    expect(host.querySelector('[data-testid="live-deck-camera"]')).toBe(camera);
    const count = requests.length;
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    await tick(60000); expect(requests).toHaveLength(count); expect(held).toHaveLength(0);
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); }); await tick();
    expect(held).toHaveLength(1); expect(writes()).toHaveLength(1);
});
