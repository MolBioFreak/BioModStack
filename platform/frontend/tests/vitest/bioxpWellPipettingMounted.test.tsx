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
    exports.push({ name: 'fluid-offset-scan', request: requests[0].request });
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state,
        action_results: [{ kind: 'pipette_manual_physical', completed_children: [{ result: {
            source_return: 88000, samples: [{ well: 'A1' }, { well: 'B1' }] } }] }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('OEM fluid offset (Z steps)');
    expect(host.textContent).toContain('88000');
    expect(host.textContent).toContain('A1, B1');
    expect(host.textContent).toContain('Neither diagnostic saves calibration');
    await append('source_fluid_offset');
    await act(async () => (host.querySelector('[aria-label="Copy step 1 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Offset scan plate"]') as HTMLSelectElement).value).toBe('RC');
    expect((host.querySelector('[aria-label="Sample every N wells"]') as HTMLInputElement).value).toBe('12');
    await change('Sample every N wells', '0'); await click('OEM fluid offset scan now');
    expect(requests).toHaveLength(1);
});

it('exposes the distinct five-station OEM Detect Fluid caller and its measured results', async () => {
    await mount(); await click('OEM Detect Fluid now');
    expect(requests).toHaveLength(1);
    expect(requests[0].request.document.stages[0].actions).toMatchObject([
        { kind: 'pipette_manual_physical', params: { operation: 'diagnostic_detect_fluid' } },
    ]);
    exports.push({ name: 'detect-fluid', request: requests[0].request });
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state,
        action_results: [{ kind: 'pipette_manual_physical', completed_children: [{ result: {
            completed: true, scans: [{ plate: 'TC', measured_raw_z: 88210 }, { plate: 'STRIP', measured_raw_z: 87990 }] } }] }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('OEM Detect Fluid (raw Z steps)');
    expect(host.textContent).toContain('TC: 88210');
    expect(host.textContent).toContain('STRIP: 87990');
    expect(host.textContent).toContain('Calibration savedNot reported');
});

it('exposes OEM fluid calibration as its own save-producing native action', async () => {
    await mount(); await click('OEM calibrate with fluid now');
    expect(requests).toHaveLength(1);
    expect(requests[0].request.document.stages[0].actions).toMatchObject([
        { kind: 'pipette_manual_physical', params: { operation: 'source_calwith_fluid' } },
    ]);
    exports.push({ name: 'calibrate-with-fluid', request: requests[0].request });
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state,
        action_results: [{ kind: 'pipette_manual_physical', calibration_persisted: true,
            completed_children: [{ result: { saved_revision_id: 'fluid-rev', pending_restart: true,
                measurements: [{ plate: 'TC', measured_raw_z: 88210 }, { plate: 'STRIP', measured_raw_z: 87990 }] } }] }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('OEM fluid calibration (raw Z steps)');
    expect(host.textContent).toContain('TC: 88210');
    expect(host.textContent).toContain('STRIP: 87990');
    expect(host.textContent).toContain('revision fluid-rev · pending restart');
    expect(host.textContent).toContain('Reject restores the FULL pre-run calibration');
});

it.each([
    ['source_fluid_offset', 'OEM fluid offset scan now'],
    ['diagnostic_detect_fluid', 'OEM Detect Fluid now'],
    ['source_calwith_fluid', 'OEM calibrate with fluid now'],
] as const)('routes %s through the real BMS submit relay', async (operation, control) => {
    await mount(); await click(control);
    const request = requests[0].request;
    const python = process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python';
    const result = spawnSync(python, ['tests/bioxp_manual_route_bridge.py'], { cwd: '../api', encoding: 'utf8',
        env: { ...process.env, PYTHONPATH: '.:tests' },
        input: JSON.stringify({ request, status: 'dispatched' }) });
    expect(result.status, result.stderr).toBe(0);
    const relayed = JSON.parse(result.stdout.trim().split('\n').at(-1)!);
    expect(relayed.status).toBe(202);
    expect(relayed.robot_requests[0].body.document.stages[0].actions[0]).toMatchObject({
        kind: 'pipette_manual_physical', params: { operation },
    });
}, 30000);

it.each(['completed', 'failed'] as const)('renders compact %s calibration replies through actual protocol relay and discovers its exact run', async status => {
    const outcome = { kind: 'source_calwith_fluid', run_id: 'run-from-native-result', body_completed: status === 'completed',
        measurements: [{ plate: 'TC', measured_raw_z: 70000, saved_revision_id: 'station-1' },
            { plate: 'MS', measured_raw_z: 71000, saved_revision_id: 'station-2' }],
        saved_revision_id: 'station-2', active_revision_id: 'station-2', pending_restart: false,
        comparison_choice: null, error: status === 'failed' ? 'third-station-failure' : null, finalization_error: 'Park-failure' };
    const reads: string[] = [];
    api.defaults.adapter = async config => {
        if (config.method === 'get') { reads.push(config.url!); throw new Error('passive read unavailable'); }
        const request = JSON.parse(config.data);
        const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/bioxp_manual_route_bridge.py'], {
            cwd: '../api', encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:tests' },
            input: JSON.stringify({ request, status, action_results: [{ action_id: 'calibrate-1', pipette_result: outcome }] }),
        });
        expect(result.status, result.stderr).toBe(0);
        const relay = JSON.parse(result.stdout.trim().split('\n').at(-1)!);
        expect(relay.data.execution.runtime_state.action_results[0].pipette_result).toEqual(outcome);
        requests.push({ url: config.url, request });
        return { data: relay.data, status: relay.status, statusText: 'fixture', config, headers: {} };
    };
    await mount(); await click('OEM calibrate with fluid now');
    expect(host.textContent).toContain('Action IDcalibrate-1');
    expect(host.textContent).toContain('TC: 70000'); expect(host.textContent).toContain('station-2');
    expect(host.textContent).toContain('Park-failure');
    if (status === 'failed') expect(host.textContent).toContain('third-station-failure');
    expect(reads).toContain('/api/bioxp/calibration-settings/runs/run-from-native-result');
    expect(button('Accept calibration').disabled).toBe(false);
    await mount(78);
    expect(button('Accept calibration').disabled).toBe(true);
    expect(button('OEM calibrate with fluid now').disabled).toBe(false);
    expect(reads.filter(url => url.endsWith('/runs/run-from-native-result'))).toHaveLength(1);
}, 30000);

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
    await advanced();
    for (const op of ['source_load_tips', 'source_mix', 'source_aspirate_air', 'source_dispense_air', 'source_purge']) await append(op);
    for (const action of ['aspirate', 'dispense', 'eject', 'plunger_up', 'plunger_down', 'dispense_all', 'diagnoses', 'initialize', 'get_data', 'last_error']) { await change('Diagnostic action', action); await append('diagnostic_pipette'); }
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

async function advanced() { await act(async () => { const summary = [...host.querySelectorAll('summary')].find(s => s.textContent === 'Advanced OEM source procedures and diagnostics')!; summary.click(); }); }
async function toggle(label: string) { await act(async () => (host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement).click()); }
it.each([-1, 0, 1, 2, 3])('source-selected pipette %s authors explicit ordered transfer preserving source indices', async pipette => {
    await mount(); await advanced(); await fields(); await check(2);
    if (pipette === -1) for (const c of [1,2,3,4]) await check(c); else await check(pipette + 1);
    await change('Source pipette', String(pipette)); await change('Source tip type', '200'); await toggle('Force new tip');
    await append('source_load_tips'); await append('move'); await append('lower'); await append('aspirate'); await append('lift');
    await change('Block', '2'); await well('G8'); await append('move'); await append('lower'); await append('dispense'); await append('lift');
    await act(async () => (host.querySelector('[aria-label="Copy step 1 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Source pipette"]') as HTMLSelectElement).value).toBe(String(pipette));
    await click('Run ordered steps');
    const actions = requests[0].request.document.stages[0].actions;
    expect(actions[0].params).toEqual({ operation: 'source_load_tips', tip_type: 200, pipette, force_new_tip: true });
    expect(actions[3].params.channels).toEqual(pipette === -1 ? [0,1,2,3] : [pipette]);
    expect(actions[5].params.well).toBe('G8');
    exports.push({ name: `selected-transfer-${pipette}`, request: requests[0].request });
    await toggle('Force new tip'); await click('OEM selected tips now');
    expect(requests[1].request.document.stages[0].actions[0].params.force_new_tip).toBe(false);
    exports.push({ name: `selected-early-return-${pipette}`, request: requests[1].request });
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state, action_results: [{ pipette_result: { requested_pipette: pipette, tip_location: 2, alignment_published: false, already_matching_tip_type: true, physical_effect_verified: false } }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    expect(host.textContent).toContain('Existing alignment retained'); expect(host.textContent).toContain('Robot source TipLocation');
    expect(button('OEM selected tips now').disabled).toBe(false);
});
it('authors every source mixing/air/purge field and copies typed values without changing mmix semantics', async () => {
    await mount(); await advanced();
    for (const [key, value] of Object.entries({ volume_ul: '23.5', air_ul: '7.5', aspirate_speed: '81', dispense_speed: '42', aspirate_delay_ms: '0', dispense_delay_ms: '-1', cycles: '0' })) await change(`Source mix ${key}`, value);
    await change('Source mix type', 'C'); await toggle('Source mix tip dip'); await append('source_mix');
    await change('Source mix volume_ul', '99'); await act(async () => (host.querySelector('[aria-label="Copy step 1 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Source mix volume_ul"]') as HTMLInputElement).value).toBe('23.5');
    await change('OEM source aspirate air volume (µL)', '6.25'); await append('source_aspirate_air');
    await change('OEM source dispense air volume (µL)', '3.5'); await append('source_dispense_air');
    await change('Source purge speed', '41.5'); await toggle('Source purge AMP'); await toggle('Source purge NTD'); await append('source_purge');
    await click('Run ordered steps');
    expect(requests[0].request.document.stages[0].actions.map((a: any) => a.params)).toEqual([
        { operation: 'source_mix', volume_ul: 23.5, air_ul: 7.5, aspirate_speed: 81, dispense_speed: 42, aspirate_delay_ms: 0, dispense_delay_ms: -1, cycles: 0, mix_type: 'C', tip_dip: false },
        { operation: 'source_aspirate_air', volume_ul: 6.25 }, { operation: 'source_dispense_air', volume_ul: 3.5 }, { operation: 'source_purge', speed: 41.5, amp: true, ntd: true },
    ]);
    exports.push({ name: 'source-all-fields', request: requests[0].request });
    await change('Source mix aspirate_delay_ms', ''); await change('Source mix dispense_delay_ms', ''); await change('Source mix type', 'H'); await click('OEM source mix now');
    expect(requests[1].request.document.stages[0].actions[0].params.aspirate_delay_ms).toBeNull();
    exports.push({ name: 'source-null-delays', request: requests[1].request });
});
it.each(['aspirate', 'dispense', 'eject', 'plunger_up', 'plunger_down', 'dispense_all', 'diagnoses', 'initialize', 'get_data', 'last_error'])('authors OEM diagnostic %s with exact discriminated fields and ordered-copy support', async action => {
    await mount(); await advanced(); await change('Diagnostic action', action);
    const diagnostic: any = { action };
    if (['aspirate', 'dispense', 'eject'].includes(action)) {
        await toggle('Diagnostic pipette 1 (ID 0)'); await toggle('Diagnostic pipette 3 (ID 2)'); diagnostic.channels = [0,2];
    }
    if (['aspirate', 'dispense'].includes(action)) { await change('Diagnostic volume (µL)', '12.25'); await change('Diagnostic speed', '71'); Object.assign(diagnostic, { volume_ul: 12.25, speed: 71 }); }
    if (action.startsWith('plunger_')) { await change('Diagnostic Z steps', '321'); diagnostic.steps = 321; }
    await append('diagnostic_pipette'); await change('Diagnostic action', 'last_error');
    await act(async () => (host.querySelector('[aria-label="Copy step 1 to editor"]') as HTMLButtonElement).click());
    expect((host.querySelector('[aria-label="Diagnostic action"]') as HTMLSelectElement).value).toBe(action);
    await click('Run ordered steps');
    expect(requests[0].request.document.stages[0].actions[0].params).toEqual({ operation: 'diagnostic_pipette', diagnostic });
    exports.push({ name: `oem-diagnostic-${action}`, request: requests[0].request });
    if (diagnostic.channels) { for (const c of [1,3]) await toggle(`Diagnostic pipette ${c} (ID ${c - 1})`); await click('OEM diagnostic now'); expect(requests[1].request.document.stages[0].actions[0].params.diagnostic.channels).toEqual([]); exports.push({ name: `oem-diagnostic-empty-${action}`, request: requests[1].request }); }
});
it('renders useful compact diagnostic values without events or raw results and retains primitive controls', async () => {
    await mount(); await advanced(); await click('OEM diagnostic now');
    job = { ...job, execution: { ...job.execution, runtime_state: { ...job.execution.runtime_state, action_results: [{ pipette_result: { action: 'get_data', completed: false, channels: [{ channel: 0, part_number: 'ADP-123', revision: 'R2', firmware: 'FW-7', data: 42, information: 'RAW_HIDDEN', result: 'RAW_HIDDEN' }], error: 'partial channel read failure', events: ['RAW_HIDDEN'], tests: [{ number: 0, label: 'Plunger force', channels: [{ channel: 0, diagnosis: 1, display: 'Passed' }], result: 'RAW_HIDDEN' }], attempts: [{}, {}] } }] } } };
    await act(async () => { await client.invalidateQueries({ queryKey: ['bioxp', 'protocols', 'jobs'] }); }); await tick();
    for (const text of ['ADP-123', 'R2', 'FW-7', 'Plunger force', 'Passed', 'partial channel read failure', 'Initialize attempts']) expect(host.textContent).toContain(text);
    expect(host.textContent).not.toContain('RAW_HIDDEN'); expect(button('Aspirate now').disabled).toBe(false);
});
it('rejects schema-invalid diagnostic inputs without refusing later valid source actions', async () => {
    await mount(); await advanced(); await change('Diagnostic action', 'aspirate'); await change('Diagnostic speed', '1.5'); await click('OEM diagnostic now');
    expect(requests).toHaveLength(0); expect(host.textContent).toContain('positive integer');
    await change('Diagnostic speed', '1'); await change('Diagnostic volume (µL)', '0'); await click('OEM diagnostic now'); expect(requests).toHaveLength(1);
    await change('Diagnostic action', 'plunger_up'); await change('Diagnostic Z steps', '-1'); await click('OEM diagnostic now'); expect(requests).toHaveLength(1);
    await click('OEM selected tips now'); expect(requests).toHaveLength(2);
});
