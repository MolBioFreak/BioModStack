import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWellPipettingPanel } from '../../src/components/BioXpWellPipettingPanel';
import { api } from '../../src/lib/api';
import type { BioXpDeckDestinationV1 } from '../../src/lib/bioxpClient';
import { manualPipettingDocument, type BioXpManualStep } from '../../src/lib/bioxpManualPipetting';

let host: HTMLDivElement, root: Root, client: QueryClient;
let requests: any[], job: any, delay: (() => Promise<void>) | null, denial: boolean;
const exports: any[] = [];
const originalAdapter = api.defaults.adapter;
const destinations = [{ target: 'catalog-only', label: 'Source supplied block', location_id: 4, enabled: false,
    disabled_reason: 'historical projection missing' }, { target: 'other', label: 'Destination supplied block', location_id: 2 }] as BioXpDeckDestinationV1[];
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount(generation = 77, options = destinations) {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpWellPipettingPanel generation={generation}
        connected destinations={options} positionTableRevision="fixture-calibration-r1" /></QueryClientProvider>));
}
function button(text: string) { return [...host.querySelectorAll('button')].find(el => el.textContent === text)!; }
async function click(text: string) { await act(async () => button(text).click()); await tick(); }
async function change(label: string, value: string) {
    const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(el, label).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function check(channel: number) { await act(async () => (host.querySelector(`[aria-label="Plunger ${channel}"]`) as HTMLInputElement).click()); }
async function well(value: string) { await act(async () => (host.querySelector(`[aria-label="Reference well ${value}"]`) as HTMLButtonElement).click()); }
async function fields() {
    await change('Block', '4'); await well('C3'); await change('Move Z position', '1');
    await change('Lift target', 'high'); await change('Volume (µL)', '12.5');
    await change('Aspirate speed', '80'); await change('Dispense speed', '65'); await change('Mix cycles', '2'); await check(2);
}
async function append(operation: string) { await change('Step to append', operation); await click('Append step'); }
function reply(request: any) {
    const id = 'protocol-live-' + Buffer.from(requireDigest(request.idempotency_key)).toString('hex');
    return { job_id: id, status: 'dispatched', command: { command_id: id, idempotency_key: request.idempotency_key, status: 'dispatched', terminal: false },
        execution: { dry_run: false, runtime_state: { workflow: { command_id: id, phase: 'executing', child_command_ids: [] }, action_results: [] } } };
}
// Synchronous digest for the explicitly synthetic robot fixture only.
import { createHash } from 'node:crypto';
function requireDigest(key: string) { return createHash('sha256').update(key).digest(); }

beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); requests = []; job = null; delay = null; denial = false;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    api.defaults.adapter = async config => {
        if (config.method === 'get') {
            if (!job) throw new Error('historical observation unavailable');
            return { data: job, status: 200, statusText: 'OK', config, headers: {} };
        }
        const request = JSON.parse(config.data); requests.push({ url: config.url, request });
        if (delay) await delay();
        if (denial) throw { response: { status: 409, data: { detail: 'OEM door interlock denied' } } };
        job = reply(request);
        return { data: job, status: 202, statusText: 'Accepted', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); });
afterAll(() => { if (process.env.BIOXP_MANUAL_UI_EXPORT) writeFileSync(process.env.BIOXP_MANUAL_UI_EXPORT, JSON.stringify({ fixture_only: true, physical_acceptance: false, requests: exports }, null, 2)); });

it.each(['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix'] as const)('mounted %s sends only its native action through the actual submit hook and Axios', async op => {
    await mount(); await fields(); await click(`${op[0].toUpperCase()}${op.slice(1)} now`);
    expect(requests).toHaveLength(1);
    const { url, request } = requests[0];
    expect(url).toBe('/api/bioxp/protocols/submit');
    expect(request).toMatchObject({ source_type: 'native', dry_run: false, expected_connection_generation: 77, live_execution: { live_execution_ack: true } });
    const expected: Record<typeof op, BioXpManualStep> = {
        move: { operation: 'move', location_id: 4, well: 'C3', position_flag: 1 },
        lower: { operation: 'lower', location_id: 4 }, lift: { operation: 'lift', location_id: 4, height_steps: null },
        aspirate: { operation: 'aspirate', channels: [1], volume_ul: 12.5, speed: 80 },
        dispense: { operation: 'dispense', channels: [1], volume_ul: 12.5, speed: 65 },
        mix: { operation: 'mix', channels: [1], volume_ul: 12.5, aspirate_speed: 80, dispense_speed: 65, cycles: 2 },
    };
    expect(request.document).toEqual(manualPipettingDocument({ protocol_id: 'bms-manual-pipetting', steps: [expected[op]] }));
    const wire = JSON.stringify(request);
    for (const forbidden of ['tip_loaded', '"tip_location":', 'oem_prepared', 'source_location', 'initialize', 'Park']) expect(wire).not.toContain(forbidden);
    if (op !== 'move') expect(wire).not.toContain('C3');
    expect(host.textContent).toContain('not established by this panel');
    expect(button(`${op[0].toUpperCase()}${op.slice(1)} now`).disabled).toBe(false);
    exports.push({ name: op, authoring: { protocol_id: 'bms-manual-pipetting', steps: [expected[op]] }, request });
});

it('manual physical controls preserve explicit flags and expose structured measurement without logs', async () => {
    await mount(); await change('Tip tray', '5'); await change('Tip well', 'B12');
    await act(async () => (host.querySelector('[aria-label="Overpress"]') as HTMLInputElement).click());
    await act(async () => (host.querySelector('[aria-label="Lift Z after pickup"]') as HTMLInputElement).click());
    await click('Load tip now');
    expect(requests[0].request.document.stages[0].actions[0]).toMatchObject({ kind: 'pipette_manual_physical', params: { operation: 'load_tip', tray: 5, well: 'B12', overpress: true, lift_z: true } });
    await change('Detection speed', '0'); await click('Measure fluid height now');
    expect(requests[1].request.document.stages[0].actions[0].params).toEqual({ operation: 'measure_fluid_height', speed: 0 });
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state, action_results: [{ kind: 'pipette_manual_physical', position_steps: 78000, lost_steps: -110, lost_steps_warning: true, raw_debug: 'DO_NOT_RENDER_RAW' }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('Measured fluid height (Z steps)');
    expect(host.textContent).toContain('78000'); expect(host.textContent).toContain('-110');
    expect(host.textContent).not.toContain('DO_NOT_RENDER_RAW'); expect(host.querySelector('pre')).toBeNull();
});

it('mounts the typed OEM fluid offset scan without confusing it with the diagnostic wizard', async () => {
    await mount();
    await change('Offset scan plate', 'RC'); await change('Detection speed', '280'); await change('Sample every N wells', '12');
    await act(async () => (host.querySelector('[aria-label="Prefill scan plate"]') as HTMLInputElement).click());
    await click('OEM fluid offset scan now');
    expect(requests).toHaveLength(1);
    expect(requests[0].request.document.stages[0].actions).toMatchObject([
        { kind: 'pipette_manual_physical', params: { operation: 'source_fluid_offset', plate: 'RC', speed: 280,
            transfer_fluid: true, skip_steps: 12 } },
    ]);
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state,
        action_results: [{ kind: 'pipette_manual_physical', source_children: [{ result: {
            source_return: 88000, samples: [{ well: 'A1' }, { well: 'B1' }] } }] }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('OEM fluid offset (Z steps)');
    expect(host.textContent).toContain('88000');
    expect(host.textContent).toContain('A1, B1');
    expect(host.textContent).toContain('not the multi-station Detect Fluid wizard');
    await append('source_fluid_offset');
    await act(async () => (host.querySelector('[aria-label="Copy step 1 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Offset scan plate"]') as HTMLSelectElement).value).toBe('RC');
    expect((host.querySelector('[aria-label="Sample every N wells"]') as HTMLInputElement).value).toBe('12');
    await change('Sample every N wells', '0'); await click('OEM fluid offset scan now');
    expect(requests).toHaveLength(1);
});

it('authors an ordered source/destination transfer with immutable per-step wells and no hidden lifecycle', async () => {
    await mount(); await fields();
    for (const op of ['move', 'lower', 'aspirate', 'lift']) await append(op);
    await change('Block', '2'); await well('H12'); await change('Move Z position', '2');
    await change('Lift target', 'height'); await change('Lift height (steps)', '0');
    for (const op of ['move', 'lower', 'dispense', 'lift']) await append(op);
    expect(requests).toHaveLength(0); expect(host.querySelectorAll('[data-manual-step]')).toHaveLength(8);
    await click('Run ordered steps');
    const request = requests[0].request;
    const actions = request.document.stages[0].actions;
    expect(actions.map((a: any) => a.kind)).toEqual(['pipette_position', 'pipette_position', 'pipette_aspirate', 'pipette_position', 'pipette_position', 'pipette_position', 'pipette_dispense', 'pipette_position']);
    expect(actions[0].params).toEqual({ operation: 'move', location_id: 4, well: 'C3', position_flag: 1 });
    expect(actions[4].params).toEqual({ operation: 'move', location_id: 2, well: 'H12', position_flag: 2 });
    expect(actions[7].params).toEqual({ operation: 'lift', location_id: 2, height_steps: 0 });
    exports.push({ name: 'ordered-transfer', request });
});

it('explicit four-plunger strokes do not change well alignment and selection alone sends nothing', async () => {
    await mount(); await fields(); await check(1); await check(3); await check(4); await well('G9');
    expect(requests).toHaveLength(0); await click('Aspirate now');
    expect(requests[0].request.document.stages[0].actions[0].params).toEqual({ channels: [0, 1, 2, 3], volume_ul: 12.5, speed: 80 });
    expect(host.textContent).toContain('other channels retain fixed spacing');
});

it('permits explicit canonical IDs without catalog/proof and does not invent defaults for required fields', async () => {
    await mount(77, []); await click('Move now'); expect(requests).toHaveLength(0);
    await change('Canonical locationID', '4'); await well('A1'); await change('Move Z position', '0'); await click('Move now');
    expect(requests[0].request.document.stages[0].actions[0].params).toEqual({ operation: 'move', location_id: 4, well: 'A1', position_flag: 0 });
});

it('reserves only its own delayed HTTP and never locks against a retained active job or failed receipt', async () => {
    await mount(); await fields(); let release!: () => void;
    delay = () => new Promise(resolve => { release = resolve; });
    await act(async () => { button('Move now').click(); button('Move now').click(); }); await tick();
    expect(requests).toHaveLength(1); expect(button('Move now').disabled).toBe(true);
    await act(async () => release()); await tick();
    expect(button('Move now').disabled).toBe(false);
    delay = null; denial = true; await click('Lower now');
    expect(requests).toHaveLength(2); expect(host.textContent).toContain('OEM door interlock denied');
    expect(button('Lower now').disabled).toBe(false);
});

it('fences generation changes during digest without converting uncertainty into a new lock', async () => {
    await mount(); await fields(); let release!: (value: ArrayBuffer) => void;
    vi.spyOn(webcrypto.subtle, 'digest').mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
    await act(async () => button('Move now').click()); await mount(78);
    await act(async () => release(new Uint8Array(32).buffer)); await tick();
    expect(requests).toHaveLength(0); expect(host.textContent).toContain('Connection changed before submission');
    expect(button('Move now').disabled).toBe(false);
});

it('edits order with copy, reorder and remove without any submission', async () => {
    await mount(); await fields(); await append('move'); await append('lower');
    await act(async () => (host.querySelector('[aria-label="Move step 2 up"]') as HTMLButtonElement).click());
    expect(host.querySelector('[data-manual-step="0"]')?.textContent).toContain('Lower');
    await act(async () => (host.querySelector('[aria-label="Copy step 2 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Step to append"]') as HTMLSelectElement).value).toBe('move');
    await act(async () => (host.querySelector('[aria-label="Remove step 1"]') as HTMLButtonElement).click());
    expect(host.querySelectorAll('[data-manual-step]')).toHaveLength(1); expect(requests).toHaveLength(0);
});

it.each(['completed', 'failed', 'denied'])('mounted submission traverses real BMS route and robot HTTP transport (%s)', async status => {
    const python = process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python';
    let bridged: any;
    api.defaults.adapter = async config => {
        if (config.method === 'get') throw new Error('passive observation unavailable');
        const request = JSON.parse(config.data);
        const result = spawnSync(python, ['tests/bioxp_manual_route_bridge.py'], { cwd: '../api', encoding: 'utf8',
            env: { ...process.env, PYTHONPATH: '.:tests' },
            input: JSON.stringify({ request, status: status === 'denied' ? 'dispatched' : status, error_status: status === 'denied' ? 409 : null }) });
        expect(result.status, result.stderr).toBe(0);
        bridged = JSON.parse(result.stdout.trim().split('\n').at(-1)!);
        requests.push({ url: config.url, request });
        if (bridged.status >= 400) throw { response: { status: bridged.status, data: bridged.data } };
        return { data: bridged.data, status: bridged.status, statusText: 'fixture', headers: {}, config };
    };
    await mount(); await fields(); await append('move'); await append('lower'); await append('aspirate'); await append('lift');
    await change('Block', '2'); await well('B7');
    await append('move'); await append('lower'); await append('dispense'); await append('lift');
    await change('Tip tray', '5'); await change('Tip well', 'B12');
    await append('load_tip'); await append('measure_fluid_height');
    await click('Run ordered steps');
    expect(requests).toHaveLength(1);
    expect(bridged.robot_requests[0].body.document).toEqual(requests[0].request.document);
    expect(bridged.robot_requests[0].path).toBe('/protocol/execute');
    expect(bridged.robot_requests[0].body.document.stages[0].actions.slice(-2)).toMatchObject([
        { kind: 'pipette_manual_physical', params: { operation: 'load_tip', tray: 5, well: 'B12', overpress: false, lift_z: false } },
        { kind: 'pipette_manual_physical', params: { operation: 'measure_fluid_height', speed: 300 } },
    ]);
    expect(host.querySelector('pre')).toBeNull();
    expect(host.textContent).toContain(status === 'denied' ? 'OEM door interlock denied' : `Robot job: ${status}`);
    expect(button('Run ordered steps').disabled).toBe(false);
    exports.push({ name: `bms-route-${status}`, request: requests[0].request, relay: bridged });
}, 30000);
