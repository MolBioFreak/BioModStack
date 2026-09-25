import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { BioXpPipetteSettings } from '../../src/components/BioXpPipetteSettings';
import { api } from '../../src/lib/api';

let host: HTMLDivElement, root: Root, calls: any[], savedFlags: boolean, savedSet: boolean;
const original = api.defaults.adapter;
async function tick() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount() { await act(async () => root.render(<BioXpPipetteSettings generation={77} connected />)); await tick(); }
async function click(text: string) { await act(async () => [...host.querySelectorAll('button')].find(el => el.textContent === text)!.click()); await tick(); }
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    calls = []; savedFlags = false; savedSet = false;
    api.defaults.adapter = async config => {
        const resource = config.url!.includes('manual-tip-set') ? 'set' : config.url!.includes('operation-parameters') ? 'flags' : 'calibration';
        const request = config.method === 'get' ? config.params : JSON.parse(config.data);
        const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/bioxp_pipette_settings_route_bridge.py'], {
            cwd: '../api', encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.:tests' },
            input: JSON.stringify({ resource, method: config.method, request, saved: resource === 'flags' ? savedFlags : savedSet }),
        });
        expect(result.status, result.stderr).toBe(0);
        const relay = JSON.parse(result.stdout.trim().split('\n').at(-1)!);
        calls.push(relay);
        expect(relay.status).toBe(200);
        if (resource === 'set') savedSet = true;
        if (resource === 'flags' && config.method === 'patch') savedFlags = true;
        return { data: relay.data, status: relay.status, statusText: 'offline robot', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); api.defaults.adapter = original; });
it('mounted manual Set sends typed tray to fixed robot endpoint and displays measured, paired saved and active readback', async () => {
    await mount();
    const select = host.querySelector('[aria-label="Tip tray for manual Set"]') as HTMLSelectElement;
    await act(async () => { select.value = '3'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('Set current Z for selected tray pair');
    const request = calls.find(c => c.robot_requests[0].path.endsWith('manual_tip_set'));
    expect(request.robot_requests[0]).toEqual({ method: 'POST', path: '/motion/oem/calibration_settings/manual_tip_set', body: { tray: 3 } });
    expect(calls.at(-1).robot_requests[0].path).toBe('/motion/oem/calibration_settings');
    expect(host.textContent).toContain('Measured current Z: 0');
    expect(host.textContent).toContain('TECANRACK3, TECANRACK4');
    expect(host.textContent).toContain('142312');
    expect(host.textContent).toContain('next ordinary startup');
    expect(host.querySelector('textarea, pre')).toBeNull();
}, 30000);
it('mounted flags save explicit false and true through real relay, then read back without touching unrelated settings', async () => {
    await mount();
    const pressure = host.querySelector('[aria-label="LogPressure"]') as HTMLInputElement;
    const staticLoss = host.querySelector('[aria-label="CheckForStaticTipLoss"]') as HTMLInputElement;
    expect(pressure.checked).toBe(true);
    await act(async () => { pressure.click(); staticLoss.click(); });
    await click('Save pipette flags');
    const patch = calls.find(c => c.robot_requests[0].method === 'PATCH');
    expect(patch.robot_requests[0]).toEqual({ method: 'PATCH', path: '/liquid/oem/operation_parameters',
        body: { LogPressure: false, CheckForStaticTipLoss: true } });
    expect((host.querySelector('[aria-label="LogPressure"]') as HTMLInputElement).checked).toBe(false);
    expect((host.querySelector('[aria-label="CheckSnapTips"]') as HTMLInputElement).checked).toBe(true);
    expect(host.textContent).toContain('saved and read back');
}, 30000);
