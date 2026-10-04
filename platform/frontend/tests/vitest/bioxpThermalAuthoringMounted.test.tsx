import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { BioXpMethodThermalEditor, thermalMethodActions } from '../../src/components/BioXpMethodThermalEditor';
import type { MethodValue, MethodCatalog } from '../../src/lib/bioxpMethods';
import { methodNumber } from '../../src/lib/bioxpMethodNumber';
// Actual served discovery capture, source_revision 610e396d; no synthetic schema.
import published from '../fixtures/bioxpThermalActionsPublished.json';
// Compile-only HTTP receiving capture; deliberately not a scientific recipe or UI default.
import receiving from '../fixtures/bioxpThermalCompileReceiving.json';
const catalog = published as MethodCatalog;
let host: HTMLDivElement, root: Root, current: MethodValue;
let changes = 0;
afterEach(async () => { await act(async () => root?.unmount()); host?.remove(); vi.unstubAllGlobals(); });
async function mount(node: MethodValue) {
    current = node; changes = 0;
    function Editor() { const [value, setValue] = useState(node); current = value; return <BioXpMethodThermalEditor node={value} catalog={catalog} onChange={next => { changes++; setValue(next); }} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Editor />));
}
const input = (label: string) => host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
async function change(label: string, raw: string) {
    const element = input(label); expect(element, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(element, raw); element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
async function click(label: string) {
    const button = host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`) ?? [...host.querySelectorAll('button')].find(b => b.textContent === label);
    expect(button, label).toBeTruthy(); await act(async () => button!.click());
}
async function open(title: string) {
    const summary = [...host.querySelectorAll('summary')].find(s => s.textContent === title)!;
    expect(summary, title).toBeTruthy();
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
it('matches actual published thermal action coverage and never seeds science or issues requests', async () => {
    expect([...thermalMethodActions].sort()).toEqual(published.actions.map(a => a.action).sort());
    const fetch = vi.fn(() => { throw Error('Authoring must be offline'); }); vi.stubGlobal('fetch', fetch);
    for (const action of thermalMethodActions) {
        await mount({ type: 'action', action, inputs: {}, future: null });
        expect(current.inputs).toEqual({}); expect(changes).toBe(0);
        expect(host.querySelector('details[open]')).toBeNull();
        // Advanced generic schema controls are not mounted on the primary surface.
        expect(host.querySelector('[aria-label$=" presence"]')).toBeNull();
        await act(async () => root.unmount()); host.remove();
    }
    expect(fetch).not.toHaveBeenCalled();
});
it.each(['rc', 'oc'])('authors %s chiller target while retaining full node and inputs', async bank => {
    await mount({ step_id: 'stable', type: 'action', action: 'chiller_setpoint', inputs: { unknown: null }, future: { untouched: true } });
    await change('Step Deck block', bank); await change('Step Target temperature (°C)', '0010.000');
    expect(current).toEqual({ step_id: 'stable', type: 'action', action: 'chiller_setpoint', inputs: { bank, target_temp_c: '0010.000', unknown: null }, future: { untouched: true } });
    await change('Step Target temperature (°C)', ''); expect((current.inputs as MethodValue).target_temp_c).toBe('');
    await click('Step Omit Target temperature (°C)'); expect(Object.hasOwn(current.inputs as object, 'target_temp_c')).toBe(false);
});
it('authors door Open/Close without immediate effects or normalization of retained fields', async () => {
    await mount({ action: 'thermal_door', inputs: { door_command: null, unknown: [0, false] }, other: 'keep' });
    expect(input('Thermal door').value).toBe('__retained'); expect(changes).toBe(0);
    await change('Thermal door', 'DO'); expect(current.inputs).toEqual({ door_command: 'DO', unknown: [0, false] });
    await change('Thermal door', 'DC'); expect(current.inputs).toEqual({ door_command: 'DC', unknown: [0, false] });
});
it.each(['thermal_setpoint', 'thermal_hold', 'incubate'])('authors all TC banks and advanced fields for %s', async action => {
    await mount({ action, inputs: { target_temp_c: null, unknown: false } });
    expect(input('Step Target temperature (°C)').disabled).toBe(true);
    await click('Step Omit Target temperature (°C)'); await change('Step Target temperature (°C)', '40.00');
    await change('Step Thermal bank', 'lid');
    if (action !== 'thermal_setpoint') { await change('Step Hold time (seconds)', '30.00'); await change('Step Start hold timer', 'attainment'); }
    await open('Ramp, fan and attainment settings');
    await change('Step Fan speed (0–255)', '128'); await change('Step Heating rate (°C/s)', '1.00'); await change('Step Cooling rate (°C/s)', '-1.00');
    if (action !== 'thermal_setpoint') { await change('Step Target tolerance (°C)', '0.5'); await change('Step Target timeout (seconds)', '120'); await change('Step Start hold timer', 'dispatch'); }
    const before = structuredClone(current.inputs as MethodValue);
    await change('Step Thermal bank', 'pedestal');
    expect(current.inputs).toEqual({ ...before, bank: 'pedestal' });
    expect(input('Step Heating rate (°C/s)')).toBeNull();
    await change('Step Thermal bank', 'nest'); expect(input('Step Heating rate (°C/s)').value).toBe('1.00');
});
it('edits ordered PCR stages, repeats, duplication, reordering, removal and cold reopening without lost extras', async () => {
    await mount({ action: 'thermal_profile', step_id: 'profile', inputs: { future: null } });
    await change('Cycle count', '003'); await click('Add stage');
    expect((current.inputs as MethodValue).segments).toEqual([{}]);
    await change('Stage 1 Thermal bank', 'nest'); await change('Stage 1 Target temperature (°C)', '95.00'); await change('Stage 1 Hold time (seconds)', '30.00'); await change('Stage 1 Start hold timer', 'attainment');
    await open('Ramp, fan and attainment settings');
    await change('Stage 1 Target tolerance (°C)', '0.5'); await change('Stage 1 Target timeout (seconds)', '120'); await change('Stage 1 Fan speed (0–255)', '128'); await change('Stage 1 Heating rate (°C/s)', '1.00'); await change('Stage 1 Cooling rate (°C/s)', '-1.00');
    await click('Stage 1 Duplicate'); await change('Stage 2 Target temperature (°C)', '55.00');
    await click('Stage 2 Duplicate'); await change('Stage 3 Target temperature (°C)', '72.00'); await change('Stage 3 Hold time (seconds)', '60.00');
    const stages = () => (current.inputs as MethodValue).segments as MethodValue[];
    await click('Stage 3 Move up'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '72.00', '55.00']);
    await click('Stage 2 Move down'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '55.00', '72.00']);
    await click('Stage 2 Remove'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '72.00']);
    const saved = JSON.parse(JSON.stringify(current)); await act(async () => root.unmount()); host.remove(); await mount(saved);
    expect(current).toEqual(saved); expect(changes).toBe(0); expect(input('Cycle count').value).toBe('003');
    expect(input('Stage 2 Hold time (seconds)').value).toBe('60.00');
    await change('Cycle count', '0'); expect((current.inputs as MethodValue).repeat).toBe('0');
});
it('retains null, expressions, unknown scalar stages and raw numeric AST until an explicit edit', async () => {
    const expr = { expr: { version: 1, op: 'param', id: 'temp', future: true } };
    const node = { action: 'thermal_profile', inputs: { repeat: methodNumber('03.000'), segments: [null, 'future', { bank: 'lid', target_temp_c: expr, duration_s: null, extension: { raw: '0001' } }] }, extension: false };
    await mount(node); expect(current).toEqual(node); expect(changes).toBe(0);
    expect(input('Stage 3 Target temperature (°C)').disabled).toBe(true);
    await change('Stage 3 Thermal bank', 'nest');
    expect(((current.inputs as MethodValue).segments as MethodValue[])[2]).toEqual({ ...node.inputs.segments[2] as object, bank: 'nest' });
    await change('Cycle count', '0004'); expect((current.inputs as MethodValue).repeat).toEqual(methodNumber('0004'));
    await open('Advanced profile controls / expressions and retained fields');
    expect(host.textContent).toContain('Profile inputs');
});
it('reproduces exact raw requests accepted by the real served compile-only receiver', async () => {
    expect(receiving.http_status).toBe(200);
    expect(receiving.result.issues).toEqual([]);
    expect(receiving.result.document).toBeTruthy();
    for (const saved of receiving.request.method.steps) {
        const expected = saved as MethodValue;
        await mount({ ...expected, inputs: {} });
        const values = expected.inputs as MethodValue;
        if (expected.action === 'thermal_door') await change('Thermal door', String(values.door_command));
        else if (expected.action === 'thermal_profile') {
            await change('Cycle count', String(values.repeat));
            for (const [i, raw] of (values.segments as MethodValue[]).entries()) {
                await click('Add stage');
                const prefix = `Stage ${i + 1}`;
                await change(`${prefix} Thermal bank`, String(raw.bank));
                await change(`${prefix} Target temperature (°C)`, String(raw.target_temp_c));
                await change(`${prefix} Hold time (seconds)`, String(raw.duration_s));
                await change(`${prefix} Start hold timer`, String(raw.start));
                const summary = [...host.querySelectorAll('li')][i].querySelector('details')!;
                await act(async () => { summary.open = true; summary.dispatchEvent(new Event('toggle')); });
                for (const [key, title] of [['tolerance_c', 'Target tolerance (°C)'], ['timeout_s', 'Target timeout (seconds)'], ['fan_speed', 'Fan speed (0–255)'], ['heat_rate_c_s', 'Heating rate (°C/s)'], ['cool_rate_c_s', 'Cooling rate (°C/s)']]) {
                    if (Object.hasOwn(raw, key)) await change(`${prefix} ${title}`, String(raw[key]));
                }
            }
        } else {
            await change(`Step ${expected.action === 'chiller_setpoint' ? 'Deck block' : 'Thermal bank'}`, String(values.bank));
            await change('Step Target temperature (°C)', String(values.target_temp_c));
        }
        expect(current).toEqual(expected);
        await act(async () => root.unmount()); host.remove();
    }
});
it.each([null, { expr: { version: 1, op: 'param', id: 'whole-input' } }])('keeps nonstandard whole inputs untouched on mount', async inputs => {
    await mount({ action: 'thermal_hold', inputs }); expect(current.inputs).toEqual(inputs); expect(changes).toBe(0);
    expect(input('Step Thermal bank')).toBeNull();
    await open('Advanced thermal inputs / expressions'); expect(current.inputs).toEqual(inputs);
});
