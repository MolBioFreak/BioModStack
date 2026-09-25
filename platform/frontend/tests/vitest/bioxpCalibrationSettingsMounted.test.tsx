import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { BioXpCalibrationSettings } from '../../src/components/BioXpCalibrationSettings';
import { api } from '../../src/lib/api';
import fixture from '../../../api/tests/fixtures/bioxp_calibration_settings.json';

let host: HTMLDivElement, root: Root, saved: boolean, calls: any[], fail: boolean, failRead: boolean;
const original = api.defaults.adapter;
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount(generation = 77) { await act(async () => root.render(<BioXpCalibrationSettings generation={generation} connected />)); await tick(); }
async function change(label: string, value: string) {
    const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement;
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function click(text: string) { await act(async () => [...host.querySelectorAll('button')].find(el => el.textContent === text)!.click()); await tick(); }
async function editBatch() {
    await change('Calibration station', 'TECANRACK1');
    for (const [field, value] of Object.entries({ x: 0, y: -17, zLow: 34000, zDelta: 1000, inc_factor: 0 })) await change(`Saved ${field}`, String(value));
    await change('Calibration station', 'CAMERA_OFFSET'); await change('Saved x', '-4');
}
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    calls = []; saved = false; fail = false; failRead = false;
    api.defaults.adapter = async config => {
        const request = config.method === 'patch' ? JSON.parse(config.data) : config.params;
        calls.push({ method: config.method, request });
        if (config.method === 'patch' && fail) throw new Error('settings storage failed');
        if (config.method === 'get' && saved && failRead) throw new Error('readback unavailable');
        if (config.method === 'patch') saved = true;
        return { data: saved ? fixture.after : fixture.before, status: 200, statusText: 'fixture', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); api.defaults.adapter = original; });

it('edits every saved station and five native fields without raw JSON, preserving a multi-row batch', async () => {
    await mount();
    expect(host.querySelectorAll('select option').length).toBe(fixture.before.saved_positions.length);
    for (const row of fixture.before.saved_positions) {
        await change('Calibration station', row.name);
        for (const field of ['x', 'y', 'zLow', 'zDelta', 'inc_factor'] as const)
            expect((host.querySelector(`[aria-label="Saved ${field}"]`) as HTMLInputElement).value).toBe(String(row[field]));
    }
    expect(host.querySelector('textarea, pre')).toBeNull();
    expect(host.querySelector('[aria-label="Saved zHigh"]')).toBeNull();
    await editBatch(); await change('Calibration station', 'TECANRACK1');
    expect((host.querySelector('[aria-label="Saved y"]') as HTMLInputElement).value).toBe('-17');
    await click('Save for next startup');
    expect(calls.map(c => c.method)).toEqual(['get', 'patch', 'get']);
    expect(calls[1].request).toEqual({ expected_connection_generation: 77, positions: [
        { name: 'TECANRACK1', x: 0, y: -17, zLow: 34000, zDelta: 1000, inc_factor: 0 }, { name: 'CAMERA_OFFSET', x: -4 },
    ] });
    expect(host.textContent).toContain('pending next ordinary startup');
    expect(host.textContent).toContain('read back. No live application or motion');
    expect(host.textContent).toContain('53000');
});

it.each(['', '1.5', '2147483648'])('invalid integer %s does not save', async value => {
    await mount(); await change('Saved x', value); await click('Save for next startup');
    expect(calls).toHaveLength(1); expect(host.textContent).toContain('signed 32-bit integer');
});
it('does not claim applied or discard edits after failure; failed readback is distinct from failed save', async () => {
    await mount(); await editBatch(); fail = true; await click('Save for next startup');
    expect(host.textContent).toContain('settings storage failed'); expect(host.textContent).toContain('2 station(s) edited');
    expect(host.textContent).not.toContain('read back.');
    fail = false; failRead = true; await click('Save for next startup');
    expect(host.textContent).toContain('but readback failed');
    expect(host.textContent).not.toContain('read back.');
});
it('reserves only its own save request and reads active startup configuration honestly after reopen', async () => {
    await mount(); await editBatch();
    const adapter = api.defaults.adapter as (config: any) => Promise<any>;
    let release!: () => void;
    api.defaults.adapter = async config => {
        if (config.method === 'patch') await new Promise<void>(resolve => { release = resolve; });
        return adapter(config);
    };
    const save = [...host.querySelectorAll('button')].find(el => el.textContent === 'Save for next startup')!;
    await act(async () => { save.click(); save.click(); }); await tick();
    expect(save.disabled).toBe(true);
    await act(async () => release()); await tick();
    expect(calls.filter(c => c.method === 'patch')).toHaveLength(1); expect(save.disabled).toBe(false);
    const bound = { ...fixture.after, active_positions: fixture.after.saved_positions,
        active_motion_positions: fixture.after.saved_motion_positions, active_loader_adjustments: fixture.after.saved_loader_adjustments,
        active_revision_id: fixture.after.saved_revision_id, pending_restart: false, application_status: 'bound_configuration' };
    api.defaults.adapter = async config => ({ data: bound, status: 200, statusText: 'offline startup fixture', config, headers: {} });
    await mount(78);
    expect(host.textContent).toContain('Configuration bound at startup; physical calibration not verified');
    expect(host.textContent).not.toContain('pending next ordinary startup');
});

it('clears old connection drafts when generation changes', async () => {
    await mount(); await editBatch(); await mount(78);
    expect(host.textContent).toContain('0 station(s) edited');
    expect(calls.at(-1).request.expected_connection_generation).toBe(78);
});
it('mounted read/save/readback traverses the actual BMS routes and robot HTTP transport', async () => {
    const relays: any[] = [];
    api.defaults.adapter = async config => {
        const request = config.method === 'patch' ? JSON.parse(config.data) : config.params;
        const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/bioxp_calibration_route_bridge.py'], {
            cwd: '../api', encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:tests' },
            input: JSON.stringify({ method: config.method, request, saved }),
        });
        expect(result.status, result.stderr).toBe(0);
        const relay = JSON.parse(result.stdout.trim().split('\n').at(-1)!); relays.push(relay);
        expect(relay.status).toBe(200); if (config.method === 'patch') saved = true;
        return { data: relay.data, status: relay.status, statusText: 'fixture', config, headers: {} };
    };
    await mount(); await editBatch(); await click('Save for next startup');
    expect(relays.map(r => r.robot_requests[0].method)).toEqual(['GET', 'PATCH', 'GET']);
    expect(relays.every(r => r.robot_requests[0].path === '/motion/oem/calibration_settings')).toBe(true);
    expect(relays[1].robot_requests[0].body.positions[0]).toEqual({ name: 'TECANRACK1', x: 0, y: -17, zLow: 34000, zDelta: 1000, inc_factor: 0 });
    expect(relays[2].data.active_positions).toEqual(relays[0].data.active_positions);
    expect(host.textContent).toContain('pending next ordinary startup');
    expect(host.textContent).toContain('read back. No live application or motion');
}, 30000);
