import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { afterEach, expect, it } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import type { MethodValue } from '../../src/lib/bioxpMethods';
const path = process.env.BIOXP_METHOD_MODEL_CONTRACT;
const contract = path ? JSON.parse(readFileSync(path, 'utf8')) : null;
let root: Root | undefined, host: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; host?.remove(); });
async function edit(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
async function choose(label: string, text: string) { const el = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!; const option = [...el.options].find(o => o.textContent === text)!; expect(option).toBeTruthy(); await edit(label, option.value); }
async function mounted(initial: MethodValue, initialBindings: MethodValue = {}) {
    let current = initial, bindings = initialBindings;
    function Owner() { const [method, setMethod] = useState(initial), [values, setValues] = useState(initialBindings); current = method; bindings = values; return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} bindings={values} onBindingsChange={setValues} catalog={contract.catalog} rootSchema={contract.schema.method} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => root!.render(<Owner />)); return () => ({ current, bindings });
}
it.runIf(!!contract)('custody picker is documentary, preserves retained native tokens/null/extensions and selects only compatible destinations', async () => {
    const reference = { expr: { version: 1, op: 'param', id: 'plate', future: null } };
    const retained = { plate_id: 'retained-token', target_location: null, move_mode: null, future: { raw: '001.00' } };
    const state = await mounted({ parameters: [{ id: 'plate', type: 'object', future: false }], deck_plan: { labware: [{ id: 'stable-plate', name: 'My reaction plate', station: 'LOC_TC' }] }, steps: [{ step_id: 'place', type: 'action', action: 'plate_move', inputs: reference }] }, { plate: retained });
    expect(state().bindings).toEqual({ plate: retained });
    expect(host.textContent).toContain('Physical custody');
    const destination = host.querySelector<HTMLSelectElement>('[aria-label="Placement destination"]')!;
    expect([...destination.options].some(o => o.textContent === 'Reagent cover position')).toBe(false);
    await choose('Named labware', 'My reaction plate'); await choose('Plate to carry', 'Pool plate'); await choose('Placement destination', 'Magnetic station — plate placement (LOC_P_MS)');
    expect(state().bindings.plate).toEqual({ ...retained, plate_id: 'PL_POOL', target_location: 'LOC_MS', labware_id: 'stable-plate' });
    expect((state().current.steps as MethodValue[])[0].inputs).toEqual(reference);
    expect(host.querySelector('[aria-label="Placement mode"]')!.textContent).toContain('Retained native / null / expression');
});
it.runIf(!!contract)('wait raw spelling and repeat integer binding stay independent of untouched ASTs and nulls', async () => {
    const ref = { expr: { version: 1, op: 'param', id: 'count', future: null } };
    const state = await mounted({ parameters: [{ id: 'count', type: 'integer', future: false }], steps: [{ step_id: 'washes', type: 'repeat', label: 'Wash repeats', count: ref, future: null, steps: [{ step_id: 'wait', type: 'action', action: 'wait', inputs: { seconds: '001.2500', future: null } }] }] });
    expect((host.querySelector('[aria-label="Repeat count"]') as HTMLInputElement).value).toBe(''); expect(state().bindings).toEqual({});
    await edit('Repeat count', '03'); expect(state().bindings.count).toBe('03'); expect((state().current.steps as MethodValue[])[0].count).toEqual(ref);
    const wait = host.querySelector<HTMLButtonElement>('fieldset .bioxp-method-sequence button')!; await act(async () => wait.click());
    await edit('Wait time (seconds)', '002.5000');
    expect(((state().current.steps as MethodValue[])[0].steps as MethodValue[])[0].inputs).toEqual({ seconds: '002.5000', future: null });
});
it.runIf(!!contract)('cover, pickup, release, press and preparation use source names without inserting numerical or native defaults', async () => {
    const actions = ['move_cover', 'plate_catch', 'plate_release', 'plate_press', 'plate_prepare'];
    for (const action of actions) {
        const state = await mounted({ steps: [{ step_id: 'step', type: 'action', action, inputs: { future: null } }] });
        expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: null });
        if (action === 'move_cover') { await choose('Cover to carry', 'Output cover'); await choose('Placement destination', 'Output cover storage'); expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: null, cover_id: 'CV_OUTPUT', target_location: 'LOC_OCS' }); }
        else if (action === 'plate_catch' || action === 'plate_press') { await choose('Plate to handle', 'Output plate'); expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: null, plate: 1 }); if (action === 'plate_press') expect(host.textContent).toContain('Standalone pool-plate Press uses the thermal-cycler press position'); }
        else if (action === 'plate_release') { await choose('Release destination', 'Thermal cycler'); expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: null, destination: 23 }); }
        else { expect(host.querySelector('[aria-label="Prepare Synthesis plate"]')).toBeNull(); const box = host.querySelector<HTMLInputElement>('[aria-label="Prepare Reagent plate"]')!; await act(async () => box.click()); expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: null, plate_ids: ['PL_REAGENT'] }); }
        await act(async () => root!.unmount()); root = undefined; host.remove();
    }
});
it.runIf(!!contract)('ordinary Transfer states its actual calibrated-depth and pellet-offset limitations without changing authored inputs', async () => {
    const inputs = { source: { station: 'LOC_MS', location_id: 0, wells: ['A1'] }, destination: { station: 'LOC_OC', location_id: 1, wells: ['A1'] }, channels: [0], volume_ul: '001.20' };
    const state = await mounted({ steps: [{ step_id: 'supernatant', type: 'action', action: 'transfer', inputs }] });
    expect(host.textContent).toContain('Ordinary Transfer lowers to calibrated depth');
    expect(host.textContent).toContain('pellet avoidance is not automatic');
    expect((state().current.steps as MethodValue[])[0].inputs).toEqual(inputs);
});
