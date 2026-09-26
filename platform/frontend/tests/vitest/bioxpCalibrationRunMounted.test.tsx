import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { BioXpCalibrationRun } from '../../src/components/BioXpCalibrationRun';
import { BioXpPipetteResults } from '../../src/components/BioXpPipetteResults';
import { api } from '../../src/lib/api';
import type { CalibrationRun } from '../../src/lib/bioxpCalibration';

let host: HTMLDivElement, root: Root, calls: any[], run: CalibrationRun;
let deny: boolean, unavailable: boolean, wrongIdentity: boolean, delay: (() => Promise<void>) | null;
const original = api.defaults.adapter;
const snapshot = (zLow: number, revision_id: string | null) => ({ revision_id,
    positions: [{ name: 'LOC_TC', x: 0, y: -10, zLow, zDelta: 5000, inc_factor: 0 }],
    liquid_calibration: { fluid_reference_revision: '206', m_PLLow: zLow, m_current_tool: 0 } });
function fixture(): CalibrationRun { return { run_id: 'fluid-run-1', before: snapshot(76000, 'before'),
    after: snapshot(77000, 'after-2'), decision: null, body_completed: false,
    saved_revision_id: 'after-2', active_revision_id: 'after-2', pending_restart: false,
    error: 'third station scan failed', finalization_error: 'Park finalization failed',
    measurements: [{ plate: 'TC', measured_raw_z: 80000, saved_revision_id: 'after-1' },
        { plate: 'MS', measured_raw_z: 81000, saved_revision_id: 'after-2' }] }; }
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function mount(connected = true, runId: string | undefined = 'fluid-run-1') {
    await act(async () => root.render(<BioXpCalibrationRun generation={77} connected={connected} runId={runId} />)); await tick();
}
function button(text: string) { return [...host.querySelectorAll('button')].find(el => el.textContent === text)!; }
async function click(text: string) { await act(async () => button(text).click()); await tick(); }
const restore = 'Reject / restore full pre-run calibration';
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    run = fixture(); calls = []; deny = false; unavailable = false; wrongIdentity = false; delay = null;
    api.defaults.adapter = async config => {
        const request = config.method === 'post' ? JSON.parse(config.data) : config.params;
        calls.push({ method: config.method, url: config.url, request });
        if (config.method === 'post') {
            if (delay) await delay();
            if (deny) throw { response: { status: 409, data: { detail: 'OEM owner denied decision' } } };
            run = { ...run, decision: request.decision,
                saved_revision_id: request.decision === 'restore' ? run.before.revision_id : run.after.revision_id,
                active_revision_id: request.decision === 'restore' ? run.before.revision_id : run.after.revision_id };
        } else if (unavailable) throw new Error('run read unavailable');
        return { data: wrongIdentity ? { ...run, run_id: 'wrong-run' } : run, status: 200, statusText: 'fixture', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); api.defaults.adapter = original; });

it.each(['accept', 'restore'] as const)('compares partial saves, submits %s, and reads the exact run back', async decision => {
    await mount();
    expect(host.textContent).toContain('Incomplete / partial result');
    expect(host.textContent).toContain('third station scan failed');
    expect(host.textContent).toContain('Park finalization failed');
    expect(host.textContent).toContain('after-1'); expect(host.textContent).toContain('after-2');
    expect(host.textContent).toContain('FULL pre-run calibration'); expect(host.textContent).toContain('any later calibration edits');
    const table = host.querySelector('[aria-label="Before and after calibration"]')!;
    expect(table.textContent).toContain('76000'); expect(table.textContent).toContain('77000');
    expect(table.querySelectorAll('tbody tr')).toHaveLength(5);
    await click(decision === 'accept' ? 'Accept calibration' : restore);
    expect(calls.map(c => c.method)).toEqual(['get', 'post', 'get']);
    expect(calls[1]).toEqual({ method: 'post', url: '/api/bioxp/calibration-settings/runs/fluid-run-1/decision',
        request: { expected_connection_generation: 77, decision } });
    expect(calls[2].url).toBe('/api/bioxp/calibration-settings/runs/fluid-run-1');
    expect(host.textContent).toContain(`Decision read back: ${decision}`);
    expect(button('Accept calibration').disabled).toBe(false);
});

it('retains the OEM no-backup comparison source without fabricating operator acceptance', async () => {
    run = { ...run, before: snapshot(76000, null), decision: null, comparison_choice: true,
        comparison_source: 'no_previous_values' };
    await mount();
    expect(host.textContent).toContain('No prior saved revision');
    expect(host.textContent).toContain('Recorded decision: Not decided');
    expect(host.textContent).toContain('Comparison choicetrue');
    expect(host.textContent).toContain('no_previous_values');
    await click(restore);
    expect(host.textContent).toContain('No saved revision');
    expect(host.textContent).toContain('Active revisionBaseline / no revision');
});

it('failed readback is evidence, not a new decision lock; robot denial is retained without retry', async () => {
    unavailable = true; await mount();
    expect(host.textContent).toContain('run read unavailable');
    expect(button('Accept calibration').disabled).toBe(false);
    deny = true; await click('Accept calibration');
    expect(host.textContent).toContain('OEM owner denied decision');
    expect(calls.filter(c => c.method === 'post')).toHaveLength(1);
    deny = false; await click(restore);
    expect(host.textContent).toContain('Decision response retained; durable readback unavailable');
    expect(host.textContent).not.toContain('Decision read back: restore');
    expect(calls.filter(c => c.method === 'post')).toHaveLength(2);
});

it('excludes duplicate in-flight decisions and fences a late response after disconnect', async () => {
    await mount(); let release!: () => void;
    delay = () => new Promise(resolve => { release = resolve; });
    await act(async () => { button('Accept calibration').click(); button(restore).click(); }); await tick();
    expect(calls.filter(c => c.method === 'post')).toHaveLength(1);
    expect(button('Accept calibration').disabled).toBe(true);
    await mount(false);
    await act(async () => release()); await tick();
    expect(calls.map(c => c.method)).toEqual(['get', 'post']);
    expect(host.textContent).toContain('Recorded decision: Not decided');
    expect(host.textContent).not.toContain('Decision read back');
    expect(button(restore).disabled).toBe(true);
});

it('does not hold decision exclusion during passive readback or let an older read overwrite a newer decision', async () => {
    await mount();
    const adapter = api.defaults.adapter as (config: any) => Promise<any>;
    let release!: () => void, held = false;
    api.defaults.adapter = async config => {
        const result = await adapter(config);
        if (config.method === 'get' && !held) {
            held = true; await new Promise<void>(resolve => { release = resolve; });
        }
        return result;
    };
    await click('Accept calibration');
    expect(button(restore).disabled).toBe(false);
    await click(restore);
    expect(host.textContent).toContain('Decision read back: restore');
    await act(async () => release()); await tick();
    expect(host.textContent).toContain('Recorded decision: restore');
    expect(calls.filter(c => c.method === 'post')).toHaveLength(2);
});

it('does not apply a response belonging to another run', async () => {
    wrongIdentity = true; await mount();
    expect(host.textContent).toContain('identity mismatch');
    expect(host.textContent).not.toContain('third station scan failed');
    await click('Accept calibration');
    expect(host.textContent).not.toContain('Decision read back');
});

it('recovers a durable comparison by literal run ID without submitting motion', async () => {
    await act(async () => root.render(<BioXpCalibrationRun generation={77} connected />));
    const input = host.querySelector('input')!;
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'fluid-run-1');
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click('Load comparison');
    expect(calls).toHaveLength(1); expect(calls[0].method).toBe('get');
    expect(host.textContent).toContain('Run fluid-run-1 · connection 77');
});

it.each(['accept', 'restore'] as const)('mounted %s traverses actual BMS routes and MockTransport, including readback', async decision => {
    const relays: any[] = [];
    api.defaults.adapter = async config => {
        const request = config.method === 'post' ? JSON.parse(config.data) : config.params;
        if (config.method === 'post') run = { ...run, decision: request.decision };
        const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/bioxp_calibration_route_bridge.py'], {
            cwd: '../api', encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:tests' },
            input: JSON.stringify({ method: config.method, request, run_id: 'fluid-run-1', run_payload: run }),
        });
        expect(result.status, result.stderr).toBe(0);
        const relay = JSON.parse(result.stdout.trim().split('\n').at(-1)!); relays.push(relay);
        expect(relay.status).toBe(200);
        return { data: relay.data, status: relay.status, statusText: 'fixture', config, headers: {} };
    };
    await mount(); await click(decision === 'accept' ? 'Accept calibration' : restore);
    expect(relays.map(r => r.robot_requests[0].method)).toEqual(['GET', 'POST', 'GET']);
    expect(relays[1].robot_requests[0]).toEqual({ method: 'POST', path: '/motion/oem/calibration_settings/runs/fluid-run-1/decision', body: { decision } });
    expect(host.textContent).toContain(`Decision read back: ${decision}`);
    expect(host.textContent).toContain('third station scan failed');
}, 30000);

it.each(['compact', 'completed_children', 'source_children', 'provider_results'] as const)('renders all critical complete/partial %s results without diagnostic bulk', async shape => {
    const data = { kind: 'source_calwith_fluid', ...fixture(), source_return: false };
    const value = shape === 'compact' ? { pipette_result: data, completed_children: [{ result: { samples: [{ well: 'DUPLICATE' }] } }], raw_debug: 'RAW_DIAGNOSTIC' }
        : shape === 'provider_results' ? { error: 'outer provider error', response: { provider_results: [{ result: data }] } }
        : { [shape]: [{ result: { samples: [{ well: 'A1', position_steps: 0 }], source_return: 0 } }, { result: data }] };
    await act(async () => root.render(<BioXpPipetteResults value={value} />));
    for (const text of ['fluid-run-1', 'TC: 80000', 'MS: 81000', 'after-1', 'after-2', 'Incomplete / partial result', 'third station scan failed', 'Park finalization failed', 'Source returnfalse']) expect(host.textContent).toContain(text);
    expect(host.textContent).not.toContain('RAW_DIAGNOSTIC'); expect(host.textContent).not.toContain('DUPLICATE');
    if (shape === 'provider_results') expect(host.textContent).toContain('outer provider error');
    if (shape.endsWith('children')) expect(host.textContent).toContain('A1');
    expect(host.querySelector('pre')).toBeNull();
});

it.each([true, false])('keeps indexed and direct failed-provider result objects (indexed=%s)', async indexed => {
    const result = { ...fixture(), comparison_choice: false };
    await act(async () => root.render(<BioXpPipetteResults value={{ error: 'terminal error', detail: 'terminal explanation',
        response: { provider_results: indexed ? { child_1: { result } } : result } }} />));
    for (const value of ['terminal error', 'terminal explanation', 'third station scan failed', 'Park finalization failed', 'after-1', 'Comparison choicefalse'])
        expect(host.textContent).toContain(value);
});

it('displays completed large repeated samples and all saved stations while keeping finalization distinct', async () => {
    const samples = Array.from({ length: 96 }, (_, i) => ({ well: `A${i + 1}`, position_steps: i }));
    const data = { ...fixture(), body_completed: true, error: null, samples,
        measurements: ['TC', 'MS', 'OC', 'RC', 'STRIP'].map((plate, i) => ({ plate, measured_raw_z: i, saved_revision_id: `saved-${i}` })) };
    await act(async () => root.render(<BioXpPipetteResults value={{ pipette_result: data }} />));
    expect(host.textContent).toContain('Source bodyCompleted'); expect(host.textContent).toContain('A96');
    for (const [i, plate] of ['TC', 'MS', 'OC', 'RC', 'STRIP'].entries()) {
        expect(host.textContent).toContain(`${plate}: ${i}`); expect(host.textContent).toContain(`saved-${i}`);
    }
    expect(host.textContent).toContain('Park finalization failed');
});
