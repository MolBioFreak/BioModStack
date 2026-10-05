import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import { methodNumber } from '../../src/lib/bioxpMethodNumber';
import type { MethodValue, MethodCompile } from '../../src/lib/bioxpMethods';
import catalog from '../fixtures/bioxp_method_deck_catalog.json';
const discovery = process.env.BIOXP_METHOD_MODEL_CONTRACT ? JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT, 'utf8')) : null;
let host: HTMLDivElement, root: Root, current: MethodValue, values: MethodValue;
async function mount(initial: MethodValue, preview?: MethodCompile, initialBindings: MethodValue = {}, actionProperties?: (node: MethodValue, change: (node: MethodValue) => void) => React.ReactNode) {
    function Owner() { const [method, setMethod] = useState(initial), [bindings, setBindings] = useState(initialBindings); current = method; values = bindings; return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} bindings={bindings} onBindingsChange={setBindings} catalog={discovery?.catalog ?? catalog} rootSchema={discovery?.schema.method ?? {}} preview={preview} actionProperties={actionProperties} />; }
    root = createRoot(host); await act(async () => root.render(<Owner />));
}
async function click(label: string) { const button = [...host.querySelectorAll<HTMLButtonElement>('button,[role=button]')].find(b => b.textContent === label || b.getAttribute('aria-label') === label)!; expect(button, label).toBeTruthy(); await act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true }))); }
async function input(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
// Canvas presentation and native receiving must both pass after integration.
it('R07: opens the canvas thermal profile compactly rather than defaulting to full', async () => {
    await mount({ steps: [{ type: 'action', step_id: 'program', action: 'thermal_profile', inputs: { segments: [] } }] });
    await click('Edit selected step');
    expect(host.querySelector('[aria-label="Thermal workflow editor"]')?.classList.contains('compact')).toBe(true);
});
it('R07: keeps the composed thermal group in the profile editor instead of falling back to generic structure', async () => {
    await mount({ steps: [{ type: 'action', step_id: 'program', action: 'thermal_profile', inputs: { segments: [{ bank: 'nest', target_temp_c: methodNumber('10'), duration_s: methodNumber('0.125'), start: 'dispatch' }] } }] });
    await click('Edit selected step'); await click('Edit full program'); await input('Repeat first step', '0'); await input('Repeat last step', '0'); await input('Selected group total passes', '2'); await click('Repeat selected steps');
    expect((current.steps as any[])[0].type).toBe('group');
    expect(host.querySelector('[aria-label="Temperature program group"]')).not.toBeNull();
});
const receipts: unknown[] = [];
function compile() {
    const request = { method: current, bindings: values, dependencies: {} };
    const run = spawnSync(process.env.BMS_TEST_PYTHON!, ['-c', 'import json,sys;from bioxp_method_compiler import compile_method;print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: '../api', encoding: 'utf8', input: JSON.stringify(request) });
    expect(run.status, run.stderr).toBe(0);
    const result = JSON.parse(run.stdout); receipts.push({ request, result });
    if (process.env.BIOXP_CANVAS_THERMAL_EXPORT) writeFileSync(process.env.BIOXP_CANVAS_THERMAL_EXPORT, JSON.stringify(receipts, null, 2));
    expect(result.issues).toEqual([]); expect(result.document).toBeTruthy();
    return result.document.stages.flatMap((s: any) => s.actions);
}
async function open(title: string) {
    const summary = [...host.querySelectorAll('summary')].find(s => s.textContent === title)!;
    expect(summary, title).toBeTruthy();
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
const receiving = it.runIf(!!discovery && !!process.env.BMS_TEST_PYTHON);
receiving.each(['0', '2'])('lowers a real bound canvas profile subgroup (%s passes), preserves original binding, and cold recompiles exact child targets', async passes => {
    const bound = { segments: ['10', '20', '30', '40'].map(target => ({ bank: 'nest', target_temp_c: methodNumber(target), duration_s: methodNumber('0.1250'), start: 'dispatch' })) };
    const reference = { expr: { version: 1, op: 'param', id: 'program' } };
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Canvas thermal integration', parameters: [{ id: 'program', type: 'object' }], steps: [{ type: 'action', step_id: 'program-step', label: 'Bound program', action: 'thermal_profile', inputs: reference, on_error: 'pause_for_operator', required_capability: null }] }, undefined, { program: bound });
    await click('Edit selected step');
    await input('Stage 2 Target temperature (°C)', '0021.2500');
    const retained = structuredClone(values);
    expect((current.steps as any[])[0].inputs).toEqual(reference);
    await click('Edit full program');
    await input('Repeat first step', '1'); await input('Repeat last step', '2'); await input('Selected group total passes', passes);
    await click('Repeat selected steps');
    const group = (current.steps as any[])[0];
    expect(group.type).toBe('group'); expect(group).not.toHaveProperty('inputs');
    expect(group.steps.map((s: any) => s.action)).toEqual(['thermal_hold', 'thermal_profile', 'thermal_hold']);
    expect(values).toEqual(retained);
    expect(group.steps.every((s: any) => s.on_error === 'pause_for_operator' && s.required_capability === null)).toBe(true);
    const cold = JSON.parse(JSON.stringify({ method: current, bindings: values }));
    await act(async () => root.unmount()); await mount(cold.method, undefined, cold.bindings);
    expect(current).toEqual(cold.method); expect(values).toEqual(cold.bindings);
    const actions = compile();
    expect(actions.map((a: any) => a.kind)).toEqual(['thermal_hold', 'thermal_profile', 'thermal_hold']);
    expect(actions[0].params.target_temp_c).toBe(10); expect(actions[2].params.target_temp_c).toBe(40);
    expect(actions[1].params.repeat).toBe(Number(passes));
    expect(actions[1].params.segments.map((s: any) => [s.target_temp_c, s.duration_s])).toEqual([[21.25, 0.125], [30, 0.125]]);
});
receiving('composes both bound chillers through the real canvas writer and receives independent native elapsed timers', async () => {
    const bindings = { rc: { bank: 'rc', target_temp_c: methodNumber('004.00') }, oc: { bank: 'oc', target_temp_c: methodNumber('008.00') } };
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Canvas cooler integration', parameters: ['rc', 'oc'].map(id => ({ id, type: 'object' })), steps: ['rc', 'oc'].map(id => ({ type: 'action', step_id: id, label: id, action: 'chiller_setpoint', inputs: { expr: { version: 1, op: 'param', id } } })) }, undefined, bindings);
    for (const bank of ['rc', 'oc']) {
        const button = host.querySelector<HTMLButtonElement>(`[data-method-path="/steps/${bank === 'rc' ? 0 : 1}"]`)!;
        await act(async () => button.click()); await click('Edit selected step'); await open('Elapsed conditioning timer');
        await input('Chiller timer identity', bank + '-timer'); await input('Chiller elapsed seconds', '000.2500');
        await input('Chiller timing', 'here'); await click('Add elapsed timer');
    }
    expect(values).toEqual(bindings);
    const cold = JSON.parse(JSON.stringify({ method: current, bindings: values }));
    await act(async () => root.unmount()); await mount(cold.method, undefined, cold.bindings);
    const actions = compile();
    expect(actions.map((a: any) => a.kind)).toEqual(['chiller_setpoint', 'timer_start', 'timer_wait', 'chiller_setpoint', 'timer_start', 'timer_wait']);
    expect(actions.filter((a: any) => a.kind === 'chiller_setpoint').map((a: any) => a.params.bank)).toEqual(['rc', 'oc']);
    expect(actions.filter((a: any) => a.kind === 'timer_start').map((a: any) => [a.params.timer_id, a.params.seconds])).toEqual([['rc-timer', 0.25], ['oc-timer', 0.25]]);
});
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); host = document.createElement('div'); document.body.append(host); });
afterEach(async () => { if (root) await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });


receiving('inserts an explicitly selected cooler wait after other work and cold compiles each timer identity', async () => {
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Later waits', steps: [
        { type: 'action', step_id: 'rc', action: 'chiller_setpoint', inputs: { bank: 'rc', target_temp_c: methodNumber('004.00') } },
        { type: 'action', step_id: 'oc', action: 'chiller_setpoint', inputs: { bank: 'oc', target_temp_c: methodNumber('008.00') } },
        { type: 'action', step_id: 'other-work', action: 'note', inputs: { message: 'Other ordered work' } },
    ] });
    for (const [index, bank] of ['rc', 'oc'].entries()) {
        await act(async () => host.querySelector<HTMLButtonElement>(`[data-method-path="/steps/${index}"]`)!.click());
        await open('Elapsed conditioning timer'); await input('Chiller timer identity', bank + '-elapsed'); await input('Chiller elapsed seconds', '000.1250'); await input('Chiller timing', 'later'); await click('Add elapsed timer');
    }
    await act(async () => host.querySelector<HTMLButtonElement>('[data-method-path="/steps/2"]')!.click());
    for (const bank of ['rc', 'oc']) { await input('Elapsed timer to wait for', bank + '-elapsed'); await click('Insert timer wait'); }
    const cold = JSON.parse(JSON.stringify(current)); await act(async () => root.unmount()); await mount(cold);
    const actions = compile();
    expect(actions.map((a: any) => a.kind)).toEqual(['chiller_setpoint', 'timer_start', 'chiller_setpoint', 'timer_start', 'note', 'timer_wait', 'timer_wait']);
    expect(actions.filter((a: any) => a.kind === 'timer_wait').map((a: any) => a.params.timer_id)).toEqual(['rc-elapsed', 'oc-elapsed']);
    expect(actions.filter((a: any) => a.kind === 'timer_start').map((a: any) => a.params.seconds)).toEqual([0.125, 0.125]);
});
