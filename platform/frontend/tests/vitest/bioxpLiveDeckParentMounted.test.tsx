import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import destinations from '../fixtures/bioxp_deck_admission_catalog.json';
import manual from '../fixtures/bioxpManualCatalogProducer.json';
import park from '../fixtures/bioxp_park_completed_receipt.json';
import { deckResources } from '../../src/lib/bioxpWorkflowDeck';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
import App from '../../src/App';

// Keep the real router, cockpit, submission hooks and shared/live deck. Only
// unrelated global chrome/camera and external HTTP transport are replaced.
vi.mock('../../src/components/Layout', () => ({ Layout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ GlobalExperimentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/components/molbio-ngs/NgsMolBioProjectHub', () => ({ default: () => null }));
vi.mock('../../src/runtime/installFeatures', () => ({ useResolvedBmsFeatures: () => ({ features: { bioxp: true }, resolved: true, known: true }) }));
vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => <span data-testid="parent-test-camera" /> }));

let host: HTMLDivElement, root: Root, client: QueryClient;
let catalog: any, generation: number, active: boolean, autoAccept: boolean;
let held: Array<{ config: any; body: any; resolve: (value: any) => void }>;
let requests: Array<{ method: string; url: string; body?: any; params?: any }>;
let receipts: Map<string, any>;
const adapter = api.defaults.adapter, exported: any[] = [];
const advance = async (ms = 5) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const panel = () => host.querySelector<HTMLElement>('#control-panel-live-deck')!;
const live = () => panel().querySelector<HTMLElement>('[data-testid="oem-deck-movement"]')!;
const mutations = () => requests.filter(row => row.method !== 'get');
const deckWrites = () => mutations().filter(row => row.url.includes('/v2/actions/oem.deck.move_to_'));
const button = (label: string, owner: ParentNode = host) => {
    const result = [...owner.querySelectorAll<HTMLButtonElement>('button')].find(el => el.textContent?.trim() === label);
    expect(result, label).toBeTruthy(); return result!;
};
const click = async (el: Element, detail = 1, options: MouseEventInit = {}) => {
    await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true, detail, ...options })); });
    await advance();
};
const tab = (name: string) => click(host.querySelector(`#control-tab-${name}`)!);
const key = async (el: Element, value: string, repeat = false) => {
    await act(async () => {
        el.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true, repeat }));
        el.dispatchEvent(new KeyboardEvent('keyup', { key: value, bubbles: true }));
    }); await advance();
};
const well = (station: string, address: string) => live().querySelector(`[data-station="${station}"][data-well="${address}"]`)!;
const station = (label: string) => {
    const result = [...live().querySelectorAll('[role="button"]')].find(el => !el.hasAttribute('data-well') && el.getAttribute('aria-label')?.includes(label));
    expect(result, label).toBeTruthy(); return result!;
};
const mount = async (initialTab: 'robot' | 'live-deck' = 'live-deck', route?: string) => {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route ?? '/bioxp']}>
        {route ? <App /> : <BioXpCockpit initialTab={initialTab} />}
    </MemoryRouter></QueryClientProvider>)); await advance();
};
const response = (config: any, data: any) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
const admit = (config: any, body: any) => {
    const id = `parent-${body.idempotency_key}`;
    const row = { ...structuredClone(park), action_id: config.url.split('/').at(-1), command_id: id,
        status: 'dispatched', terminal: false, terminal_receipt_id: null, completion_class: null,
        canonical_inputs: body.inputs, requested_values: body.inputs };
    receipts.set(id, row); return response(config, row);
};
beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('crypto', webcrypto);
    generation = 7; active = true; autoAccept = true; held = []; requests = []; receipts = new Map();
    catalog = structuredClone(metadata.catalog);
    catalog.dashboard.generated_at = Date.now() / 1000;
    catalog.dashboard.active_commands = []; catalog.dashboard.latest_receipts = [];
    catalog.dashboard.deck = structuredClone(destinations.deck);
    // Controlled catalog state for parent wiring. Native producer replay is a
    // separate receiving test; this synthetic availability isn't hardware proof.
    catalog.actions = catalog.actions.filter((a: any) => !a.action_id.startsWith('oem.deck.move_to_')).concat([
        structuredClone(destinations.action),
        { action_id: 'oem.deck.move_to_well', request_schema_version: 'bioxp.operator_action_request.v2',
            response_schema_version: 'bioxp.operator_action_receipt.v2', interrupt: false,
            enabled: true, disabled_reason: null, expected_board_epoch_by_board: { '4': 64, '5': 1 } },
    ]);
    api.defaults.adapter = async config => {
        const method = config.method ?? 'get', url = config.url ?? '';
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method, url, body, params: config.params });
        if (method === 'get') {
            if (url === '/api/bioxp/status') return response(config, { connection: { generation, active, configured: true, reachable: true, runtime_ready: true, hardware_fresh: true }, mutation_access: { enabled: true } });
            if (url.endsWith('/catalog')) return response(config, catalogWireFixture({ schema_version: 'bioxp.operator_control_catalog.v1',
                dashboard: catalog.dashboard.telemetry ?? metadata.catalog.dashboard.telemetry,
                actions: manual.referenced, canonical: structuredClone(catalog) }, config.params?.view));
            if (url.endsWith('/history')) return response(config, { items: [], next_cursor: null, limit: 8 });
            if (url === '/api/bioxp/calibration-settings') throw new Error('Controlled display-only geometry outage');
            if (url.includes('/receipts/')) return response(config, receipts.get(decodeURIComponent(url.split('/').at(-1)!)));
            if (url === '/api/bioxp/protocols/jobs') return response(config, { rows: [] });
            if (url === '/api/bioxp/protocols/presets') return response(config, []);
            throw new Error(`Unexpected GET ${url}`);
        }
        if (url.includes('/v2/actions/oem.deck.move_to_')) {
            if (autoAccept) return admit(config, body);
            return new Promise(resolve => held.push({ config, body, resolve }));
        }
        if (url.includes('/v2/interrupts/')) return response(config, { ...park, action_id: url.split('/').at(-1), command_id: 'stop-parent' });
        if (url.includes('/operator-controls/actions/route.motion_thermal_door_')) return response(config, { ...metadata.legacy, action_id: url.split('/').at(-1), command_id: `door-${mutations().length}` });
        throw new Error(`Unexpected mutation ${method} ${url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => {
    exported.push(...mutations());
    await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter;
    vi.unstubAllGlobals(); vi.useRealTimers();
});
afterAll(() => {
    if (process.env.BIOXP_LIVE_DECK_UI_EXPORT) writeFileSync(process.env.BIOXP_LIVE_DECK_UI_EXPORT,
        JSON.stringify({ source: 'actual mounted cockpit and Axios serialization', transport_fixture_only: true, requests: exported }, null, 2));
});

it('relocates deck controls to a fourth tab without changing the default robot landing or issuing commands', async () => {
    await mount('robot');
    expect(host.querySelector('[data-testid="oem-deck-movement"]')).toBeNull();
    expect([...host.querySelectorAll('[role="tablist"][aria-label="Robot controls"] [role="tab"]')].map(el => el.textContent))
        .toEqual(['Robot controls', 'Pipettes', 'Workflows', 'Live deck movement']);
    await tab('live-deck');
    expect(panel().hidden).toBe(false); expect(live()).not.toBeNull();
    expect(host.querySelector('#control-panel-robot [data-testid="oem-deck-movement"]')).toBeNull();
    expect(live().querySelector('[data-testid="live-deck-door-controls"]')).not.toBeNull();
    expect(live().textContent).toContain('Refresh deck readiness');
    expect(live().textContent).toContain('Plate and cover');
    expect(mutations()).toEqual([]);
});

it('opens the real robot-local deep link with one default-open camera and no robot command', async () => {
    await mount('live-deck', '/bioxp/live-deck');
    expect(host.querySelector('#control-tab-live-deck')?.getAttribute('aria-selected')).toBe('true');
    expect(panel().hidden).toBe(false); expect(live()).not.toBeNull();
    expect(host.querySelectorAll('[data-testid="parent-test-camera"]')).toHaveLength(1);
    expect(panel().querySelector<HTMLDetailsElement>('[data-testid="live-deck-camera"]')?.open).toBe(true);
    expect(mutations()).toEqual([]);
});

it('wires the actual Robot destination dropdown to a nonmoving highlight, not the inspection selector or last submitted target', async () => {
    await mount();
    await click(well('LOC_OC', 'H12'));
    const writes = structuredClone(mutations());
    const guns = [...live().querySelectorAll('[data-gun-module]')].map(e => e.getAttribute('transform'));
    const select = [...live().querySelectorAll('label')].find(el => el.textContent?.trim().startsWith('Robot destination'))!.querySelector('select')!;
    for (const [value, mapped] of [['LOC_TC', 'LOC_TC'], ['LOC_TC_BARCODE', 'LOC_TC'], ['TECANRACK2', 'TECANRACK2'], ['', null]]) {
        await act(async () => { select.value = value!; select.dispatchEvent(new Event('change', { bubbles: true })); }); await advance();
        expect([...live().querySelectorAll('g[data-selected-station]')].map(e => e.getAttribute('data-selected-station'))).toEqual(mapped ? [mapped] : []);
        expect(live().querySelector('[aria-label="Reported pose"]')?.textContent).toContain('Requested destinationOutput chiller · H12');
        expect([...live().querySelectorAll('[data-gun-module]')].map(e => e.getAttribute('transform'))).toEqual(guns);
        expect(mutations()).toEqual(writes);
    }
});
it('serializes one exact named request per explicit SVG Move click or key through the existing owner', async () => {
    await mount();
    const moves = [...live().querySelectorAll('svg g[data-move-to]')]; expect(moves).toHaveLength(13);
    for (const move of moves) {
        const before = deckWrites().length, target = move.getAttribute('data-move-to');
        await click(move); await click(move, 2); await key(move, 'Enter', true);
        expect(deckWrites()).toHaveLength(before + 1);
        expect(deckWrites().at(-1)?.body.inputs).toEqual({ target, camera_offset: false });
        expect(deckWrites().at(-1)?.url).toBe('/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_location');
        await key(move, 'Enter'); expect(deckWrites()).toHaveLength(before + 2);
        expect(deckWrites().at(-1)?.body.inputs).toEqual({ target, camera_offset: false });
    }
    expect(mutations()).toHaveLength(26);
    expect(new Set(deckWrites().map(r => r.body.idempotency_key)).size).toBe(26);
}, 90000);
it('honors native disabled named action for explicit Move controls without adding pose gates', async () => {
    const action = catalog.actions.find((a: any) => a.action_id === 'oem.deck.move_to_location');
    action.enabled = false; action.disabled_reason = 'Native action unavailable';
    await mount();
    for (const move of live().querySelectorAll('svg g[data-move-to]')) {
        expect(move.getAttribute('aria-disabled')).toBe('true'); await click(move); await key(move, ' ');
    }
    expect(live().querySelectorAll('svg g[data-move-to]')).toHaveLength(13); expect(mutations()).toEqual([]);
});

it('sends native named travel from map regions and keeps selector camera offset out of map and well intent', async () => {
    await mount();
    const select = [...live().querySelectorAll('label')].find(el => el.textContent?.trim().startsWith('Robot destination'))!.querySelector('select')!;
    await act(async () => { select.value = 'LOC_TC'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    const offset = live().querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    await click(offset);
    await click(button('Move to destination', live()));
    expect(deckWrites().at(-1)?.body.inputs).toEqual({ target: 'LOC_TC', camera_offset: true });
    await click(station('Waste'));
    expect(deckWrites().at(-1)?.body.inputs).toEqual({ target: 'WASTE_BIN', camera_offset: false });
    await click(station('Park'));
    expect(deckWrites().at(-1)?.body.inputs).toEqual({ target: 'LOC_PARK', camera_offset: false });
    await click(well('LOC_TC', 'D7'));
    expect(deckWrites().at(-1)?.body.inputs).toEqual({ location_id: 2, well: 'D7', position_flag: 1 });
    expect(deckWrites().at(-1)?.url).toBe('/api/bioxp/operator-controls/v2/actions/oem.deck.move_to_well');
    expect(mutations()).toHaveLength(4);
});

it('preserves exact first/interior/last addressing for every rendered resource through the real parent serializer', async () => {
    await mount();
    for (const resource of deckResources) {
        for (const address of [resource.points[0].well, resource.points[Math.floor(resource.points.length / 2)].well, resource.points.at(-1)!.well]) {
            await click(well(resource.id, address));
            expect(deckWrites().at(-1)?.body.inputs).toEqual({ location_id: resource.locationId, well: address, position_flag: 1 });
        }
    }
    expect(deckWrites()).toHaveLength(deckResources.length * 3);
    expect(new Set(deckWrites().map(row => row.body.idempotency_key)).size).toBe(deckResources.length * 3);
    expect(mutations().every(row => row.url.endsWith('/oem.deck.move_to_well'))).toBe(true);
}, 90000);

it('keeps view navigation, focus, arrow exploration and repeated keys nonmoving while Enter activates once', async () => {
    await mount();
    const first = well('LOC_TC', 'A1');
    await act(async () => { (first as SVGElement).focus(); });
    await key(first, 'ArrowRight');
    await key(first, 'Enter', true);
    await click(button('Fit deck', live()));
    await click(live().querySelector('[aria-label="Zoom in"]')!);
    await click(live().querySelector('[aria-label="Pan left"]')!);
    expect(mutations()).toEqual([]);
    await key(first, 'Enter'); expect(deckWrites()).toHaveLength(1);
    await click(first, 2); expect(deckWrites()).toHaveLength(1);
    await tab('workflows'); await tab('live-deck');
    expect(deckWrites()).toHaveLength(1);
});

it('keeps synchronous deck admission ownership across tabs, leaves Stop independent and accepts the next explicit intent after POST', async () => {
    autoAccept = false; await mount();
    const first = well('LOC_TC', 'B3'), second = well('LOC_OC', 'H12');
    await act(async () => {
        first.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
        second.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
    }); await advance();
    expect(held).toHaveLength(1); expect(held[0].body.inputs).toEqual({ location_id: 2, well: 'B3', position_flag: 1 });
    await tab('robot'); await tab('live-deck'); await click(second);
    expect(held).toHaveLength(1);
    await click(button('Stop X', host.querySelector('[aria-label="Stop controls"]')!));
    expect(mutations().some(row => row.url.endsWith('/interrupts/oem.x.stop'))).toBe(true);
    await act(async () => held[0].resolve(admit(held[0].config, held[0].body))); await advance();
    await click(second); expect(held).toHaveLength(2);
    expect(held[1].body.inputs).toEqual({ location_id: 1, well: 'H12', position_flag: 1 });
    expect(held[1].body.idempotency_key).not.toBe(held[0].body.idempotency_key);
});

it('does not make absent pose, head alignment or calibrated-layout reads a move or door admission requirement', async () => {
    catalog.dashboard.telemetry = null; catalog.dashboard.deck = null;
    await mount();
    expect(live().textContent).toContain('Position unavailable');
    await click(well('LOC_STRIP1', 'H1'));
    expect(deckWrites()[0].body.inputs).toEqual({ location_id: 11, well: 'H1', position_flag: 1 });
    const doors = live().querySelector('[data-testid="live-deck-door-controls"]')!;
    expect(button('Open', doors).disabled).toBe(false);
    await click(button('Open', doors));
    expect(mutations().at(-1)?.url).toContain('route.motion_thermal_door_open');
    expect(mutations()).toHaveLength(2);
});

it.each(['Open', 'Close'])('quick door %s uses the exact existing manual action and effective payload, not a script or travel', async label => {
    await mount();
    await click(button(label, live().querySelector('[data-testid="live-deck-door-controls"]')!));
    const quick = structuredClone(mutations().at(-1));
    await tab('robot');
    const door = [...host.querySelectorAll('#control-panel-robot article')].find(el => el.querySelector('h3')?.textContent === 'Thermal Door')!;
    expect(door).toBeTruthy(); await click(button(label, door));
    const legacy = mutations().at(-1)!;
    expect(legacy.url).toBe(quick?.url);
    expect(legacy.body.inputs).toEqual(quick?.body.inputs);
    expect(legacy.body.expected_connection_generation).toBe(quick?.body.expected_connection_generation);
    expect(legacy.body.expected_ownership_generation).toBe(quick?.body.expected_ownership_generation);
    expect(mutations()).toHaveLength(2);
    expect(deckWrites()).toHaveLength(0);
});
