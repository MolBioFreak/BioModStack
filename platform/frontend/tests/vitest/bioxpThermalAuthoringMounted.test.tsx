import React, { act, useState } from 'react';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { composeThermalRepeat, composeChillerTimer, thermalTimerWait } from '../../src/lib/bioxpMethodThermal';
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
    expect(current).toEqual({ step_id: 'stable', type: 'action', action: 'chiller_setpoint', inputs: { bank, target_temp_c: methodNumber('0010.000'), unknown: null }, future: { untouched: true } });
    await change('Step Target temperature (°C)', ''); expect((current.inputs as MethodValue).target_temp_c).toEqual(methodNumber(''));
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
    await click('Stage 3 Move up'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '72.00', '55.00'].map(methodNumber));
    await click('Stage 2 Move down'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '55.00', '72.00'].map(methodNumber));
    await click('Stage 2 Remove'); expect(stages().map(s => s.target_temp_c)).toEqual(['95.00', '72.00'].map(methodNumber));
    const saved = JSON.parse(JSON.stringify(current)); await act(async () => root.unmount()); host.remove(); await mount(saved);
    expect(current).toEqual(saved); expect(changes).toBe(0); expect(input('Cycle count').value).toBe('003');
    expect(input('Stage 2 Hold time (seconds)').value).toBe('60.00');
    await change('Cycle count', '0'); expect((current.inputs as MethodValue).repeat).toEqual(methodNumber('0'));
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
                const summary = [...[...host.querySelectorAll('li')][i].querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === 'Ramp, fan and attainment settings')!;
                await act(async () => { summary.open = true; summary.dispatchEvent(new Event('toggle')); });
                for (const [key, title] of [['tolerance_c', 'Target tolerance (°C)'], ['timeout_s', 'Target timeout (seconds)'], ['fan_speed', 'Fan speed (0–255)'], ['heat_rate_c_s', 'Heating rate (°C/s)'], ['cool_rate_c_s', 'Cooling rate (°C/s)']]) {
                    if (Object.hasOwn(raw, key)) await change(`${prefix} ${title}`, String(raw[key]));
                }
            }
        } else {
            await change(`Step ${expected.action === 'chiller_setpoint' ? 'Deck block' : 'Thermal bank'}`, String(values.bank));
            await change('Step Target temperature (°C)', String(values.target_temp_c));
        }
        const numericKeys = new Set(['repeat', 'target_temp_c', 'duration_s', 'tolerance_c', 'timeout_s', 'fan_speed', 'heat_rate_c_s', 'cool_rate_c_s']);
        const expectedAuthored = JSON.parse(JSON.stringify(expected), (key, value) => numericKeys.has(key) ? methodNumber(String(value)) : value);
        expect(current).toEqual(expectedAuthored);
        await act(async () => root.unmount()); host.remove();
    }
});
it.each([null, { expr: { version: 1, op: 'param', id: 'whole-input' } }])('keeps nonstandard whole inputs untouched on mount', async inputs => {
    await mount({ action: 'thermal_hold', inputs }); expect(current.inputs).toEqual(inputs); expect(changes).toBe(0);
    expect(input('Step Thermal bank')).toBeNull();
    await open('Advanced thermal inputs / expressions'); expect(current.inputs).toEqual(inputs);
});

const hold = (target: string) => ({ bank: 'nest', target_temp_c: methodNumber(target), duration_s: methodNumber('000.1250'), start: 'dispatch', fan_speed: null, extension: { retained: true } });
it('composes only selected repeat children and retains literal/binding declarations through cold Open', async () => {
    const node = { type: 'action', step_id: 'program', action: 'thermal_profile', required_capability: null, inputs: { segments: [hold('10'), hold('20'), hold('30'), hold('40')], future: null }, extension: ['raw'] };
    const original = structuredClone(node);
    let n = 0;
    const group = composeThermalRepeat(node, 1, 2, methodNumber('0'), () => `child-${++n}`);
    const children = group.steps as MethodValue[];
    expect(children.map(c => c.action)).toEqual(['thermal_hold', 'thermal_profile', 'thermal_hold']);
    expect((children[1].inputs as MethodValue).segments).toEqual(node.inputs.segments.slice(1, 3));
    expect((children[1].inputs as MethodValue).repeat).toEqual(methodNumber('0'));
    expect(children[0].inputs).toEqual(hold('10')); expect(children[2].inputs).toEqual(hold('40'));
    expect(node).toEqual(original);
    const nested = { type: 'repeat', step_id: 'outer', count: methodNumber('2'), steps: [group], future: null };
    await mount(JSON.parse(JSON.stringify(nested))); expect(current).toEqual(nested); expect(changes).toBe(0);
    const bound = { ...node, inputs: { expr: { version: 1, op: 'param', id: 'profile' } } };
    expect(() => composeThermalRepeat(bound, 0, 0, methodNumber('2'))).toThrow('binding owner');
    expect(bound.inputs.expr.id).toBe('profile');
});
it('compact/full edits share exact values and selected scope composes through the ordinary AST', async () => {
    await mount({ type: 'action', step_id: 'p', action: 'thermal_profile', inputs: {} });
    await click('Add stage'); await click('Add stage'); await click('Add stage');
    await change('Stage 2 Target temperature (°C)', '0023.400');
    await click('Compact program'); expect(input('Stage 2 Target temperature (°C)').value).toBe('0023.400');
    await change('Stage 2 Hold time (seconds)', '0.1250'); await click('Edit full program');
    await change('Repeat first step', '1'); await change('Repeat last step', '1'); await change('Selected group total passes', '0');
    await click('Repeat selected steps'); expect(current.type).toBe('group');
    const children = current.steps as MethodValue[];
    expect(children.map(c => c.action)).toEqual(['thermal_hold', 'thermal_profile', 'thermal_hold']);
    expect(((children[1].inputs as MethodValue).segments as MethodValue[])[0].duration_s).toEqual(methodNumber('0.1250'));
});
it('preserves partial subgroup drafts across parent-controlled expansion without editing retained inputs', async () => {
    const node = { type: 'action', step_id: 'controlled', action: 'thermal_profile', inputs: { segments: [hold('10'), hold('20')], repeat: null, extension: { expr: { version: 1, op: 'param', id: 'retained' } } } };
    const onChange = vi.fn();
    const onExpandedChange = vi.fn();
    function Controlled() {
        const [expanded, setExpanded] = useState(false);
        return <><button onClick={() => setExpanded(value => !value)}>Outer expansion</button><BioXpMethodThermalEditor compact expanded={expanded} onExpandedChange={next => { onExpandedChange(next); setExpanded(next); }} node={node} catalog={catalog} onChange={onChange} /></>;
    }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<Controlled />));
    expect(host.querySelector('.bioxp-method-thermal.compact')).toBeTruthy();
    expect(input('Repeat first step')).toBeNull();
    await click('Edit full program'); expect(onExpandedChange).toHaveBeenLastCalledWith(true);
    const stage = input('Stage 1 Target temperature (°C)');
    await change('Repeat first step', '1'); await change('Selected group total passes', '00.');
    expect(input('Repeat last step').value).toBe('');
    await click('Outer expansion'); expect(input('Repeat first step')).toBeNull();
    await click('Outer expansion');
    expect(input('Repeat first step').value).toBe('1');
    expect(input('Repeat last step').value).toBe('');
    expect(input('Selected group total passes').value).toBe('00.');
    await click('Compact program'); expect(onExpandedChange).toHaveBeenLastCalledWith(false);
    await click('Edit full program');
    expect(input('Selected group total passes').value).toBe('00.');
    expect(input('Stage 1 Target temperature (°C)')).toBe(stage);
    expect(onChange).not.toHaveBeenCalled();
});
it('keeps compact uncontrolled defaults and treats controlled expansion as parent-owned', async () => {
    const node = { action: 'thermal_profile', inputs: { segments: [hold('10')] } };
    const onExpandedChange = vi.fn();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<BioXpMethodThermalEditor compact node={node} catalog={catalog} onChange={vi.fn()} />));
    expect(input('Repeat first step')).toBeNull();
    await click('Edit full program'); expect(input('Repeat first step')).toBeTruthy();
    await act(async () => root.render(<BioXpMethodThermalEditor compact expanded={false} onExpandedChange={onExpandedChange} node={node} catalog={catalog} onChange={vi.fn()} />));
    await click('Edit full program');
    expect(onExpandedChange).toHaveBeenLastCalledWith(true);
    expect(input('Repeat first step')).toBeNull();
});
it.each(['rc', 'oc'])('composes independent %s elapsed conditioning, with wait here or later', async bank => {
    await mount({ type: 'action', step_id: bank, action: 'chiller_setpoint', inputs: { bank, target_temp_c: methodNumber('004.00'), extension: null } });
    await open('Elapsed conditioning timer'); await change('Chiller timer identity', bank + '-timer'); await change('Chiller elapsed seconds', '000.2500');
    await change('Chiller timing', 'later'); await click('Add elapsed timer');
    const children = current.steps as MethodValue[];
    expect(children.map(c => c.action)).toEqual(['chiller_setpoint', 'timer_start']);
    expect(children[0].inputs).toEqual({ bank, target_temp_c: methodNumber('004.00'), extension: null });
    expect(children[1].inputs).toEqual({ timer_id: bank + '-timer', seconds: methodNumber('000.2500') });
    const waited = composeChillerTimer(children[0], methodNumber('0'), bank + '-next', true);
    expect((waited.steps as MethodValue[]).map(c => c.action)).toEqual(['chiller_setpoint', 'timer_start', 'timer_wait']);
});
it('authors a blank group, one hold and keep-target continuation without science defaults', async () => {
    await mount({ type: 'group', step_id: 'blank', steps: [], extension: null });
    expect(changes).toBe(0); await click('Add hold'); await click('Add keep-target continuation');
    expect((current.steps as MethodValue[]).map(n => [n.action, n.inputs])).toEqual([['thermal_hold', {}], ['thermal_setpoint', {}]]);
    const saved = structuredClone(current); await act(async () => root.unmount()); host.remove(); await mount(saved);
    expect(current).toEqual(saved); expect(changes).toBe(0);
});
it('receives helper-produced fractional/zero/subgroup and independent cooler programs in the real offline compiler', () => {
    let serial = 0; const id = () => `thermal-${++serial}`;
    const profile = { type: 'action', step_id: 'program', action: 'thermal_profile', inputs: { segments: ['10', '20', '30', '40'].map(t => ({ bank: 'nest', target_temp_c: methodNumber(t), duration_s: methodNumber('0.1250'), start: 'dispatch' })) } };
    const requests = ['0', '2'].map(count => ({ method: { schema: 'bms.bioxp-method.v1', name: 'Offline thermal receiving', parameters: [], procedures: [], steps: [
        { type: 'repeat', step_id: 'outer', count: methodNumber('2'), steps: [composeThermalRepeat(profile, 1, 2, methodNumber(count), id)] },
        ...['rc', 'oc'].map(bank => composeChillerTimer({ type: 'action', step_id: bank, action: 'chiller_setpoint', inputs: { bank, target_temp_c: methodNumber(bank === 'rc' ? '4.00' : '8.00') } }, methodNumber('0.2500'), bank + '-timer', bank === 'rc', id)),
        { type: 'action', step_id: 'keep', action: 'thermal_setpoint', inputs: { bank: 'nest', target_temp_c: methodNumber('20.000') } },
        thermalTimerWait('oc-timer', id),
    ] } }));
    const results = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python', ['-c', 'import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps([compile_method(x) for x in json.load(sys.stdin)]))'], { cwd: '../api', input: JSON.stringify(requests), encoding: 'utf8' }));
    if (process.env.BIOXP_THERMAL_EVIDENCE) writeFileSync(process.env.BIOXP_THERMAL_EVIDENCE, JSON.stringify({ requests, results }, null, 2));
    for (const [index, result] of results.entries()) {
        expect(result.issues).toEqual([]); expect(result.document).toBeTruthy();
        const actions = result.document.stages.flatMap((s: { actions: MethodValue[] }) => s.actions);
        expect(actions.map((a: MethodValue) => a.kind)).toEqual(['thermal_hold', 'thermal_profile', 'thermal_hold', 'thermal_hold', 'thermal_profile', 'thermal_hold', 'chiller_setpoint', 'timer_start', 'timer_wait', 'chiller_setpoint', 'timer_start', 'thermal_setpoint', 'timer_wait']);
        const profiles = actions.filter((a: MethodValue) => a.kind === 'thermal_profile');
        expect(profiles[0].params.repeat).toBe(index === 0 ? 0 : 2);
        expect(profiles[0].params.segments.map((s: MethodValue) => s.target_temp_c)).toEqual([20, 30]);
        expect(profiles[0].params.segments[0].duration_s).toBe(0.125);
    }
});

it('renders one connected in-place graph across before/repeat/after scopes without changing raw AST', async () => {
    const node = composeThermalRepeat({ type: 'action', step_id: 'profile', action: 'thermal_profile', inputs: { segments: [hold('37'), hold('32'), hold('50'), hold('20')] }, extension: null }, 1, 2, methodNumber('003'));
    await mount(node);
    expect(host.querySelectorAll('.bioxp-thermal-target-line')).toHaveLength(1);
    expect(host.querySelectorAll('.bioxp-thermal-stages > li')).toHaveLength(4);
    expect(host.querySelector('fieldset')).toBeNull();
    const frame = host.querySelector<HTMLElement>('.bioxp-thermal-repeat-frame')!;
    expect(frame.style.left).toBe('25%'); expect(frame.style.width).toBe('50%');
    expect(host.querySelectorAll('.bioxp-thermal-target-line path')).toHaveLength(4);
    expect([...host.querySelectorAll('.bioxp-thermal-target-line path')][1].getAttribute('d')).toMatch(/^M80 /);
    expect(current).toEqual(node); expect(changes).toBe(0);
    await change('Stage 1 Target temperature (°C)', '0032.000');
    const children = current.steps as MethodValue[];
    expect(((children[1].inputs as MethodValue).segments as MethodValue[])[0].target_temp_c).toEqual(methodNumber('0032.000'));
    expect(children[0]).toEqual((node.steps as MethodValue[])[0]); expect(children[2]).toEqual((node.steps as MethodValue[])[2]);
    await click('Compact program'); expect(host.querySelector('[data-program-view="compact"]')).toBeTruthy();
    expect(input('Stage 1 Target temperature (°C)').value).toBe('0032.000');
});
it('selects a repeat range on the graph without authoring a count or scientific value', async () => {
    await mount({ type: 'action', step_id: 'p', action: 'thermal_profile', inputs: { segments: [hold('37'), hold('32'), hold('50')] } });
    const leaves = host.querySelectorAll('.bioxp-thermal-stages > li');
    await act(async () => leaves[1].dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await act(async () => leaves[2].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
    expect(input('Repeat first step').value).toBe('1'); expect(input('Repeat last step').value).toBe('2');
    expect(input('Selected group total passes').value).toBe(''); expect(changes).toBe(0);
    expect(host.querySelectorAll('.range-selected')).toHaveLength(2);
    expect(host.querySelector<HTMLDetailsElement>('.bioxp-thermal-repeat-builder')?.open).toBe(true);
});
it('has no phantom stage for an empty sequence and preserves empty method-repeat editing', async () => {
    await mount({ type: 'action', action: 'thermal_profile' });
    expect(host.querySelectorAll('.bioxp-thermal-stages > li')).toHaveLength(0);
    expect(host.textContent).toContain('Build a temperature program'); expect(changes).toBe(0);
    await act(async () => root.unmount()); host.remove();
    await mount({ type: 'repeat', count: null, steps: [] });
    expect(input('Method repeat total passes').disabled).toBe(true); expect(current.count).toBeNull();
});
it('breaks graph connections at unknown values and bank boundaries without normalizing them', async () => {
    const values = [hold('20'), { ...hold('30'), bank: 'lid' }, { ...hold('40'), target_temp_c: null }, hold('50')];
    await mount({ action: 'thermal_profile', inputs: { segments: values, repeat: null } });
    const paths = [...host.querySelectorAll('.bioxp-thermal-target-line path')].map(path => path.getAttribute('d'));
    expect(paths).toHaveLength(3); expect(paths[1]).toMatch(/^M120 /); expect(paths[2]).toMatch(/^M320 /);
    expect(current.inputs).toEqual({ segments: values, repeat: null }); expect(changes).toBe(0);
});
it('allows only one local options panel at a time without losing retained edits', async () => {
    await mount({ action: 'thermal_profile', inputs: { segments: [hold('20'), hold('30')] } });
    await open('Stage 1 options'); await change('Stage 1 Thermal bank', 'lid');
    await open('Stage 2 options');
    expect(host.querySelectorAll('.bioxp-thermal-step-options[open]')).toHaveLength(1);
    expect(((current.inputs as MethodValue).segments as MethodValue[])[0].bank).toBe('lid');
});
