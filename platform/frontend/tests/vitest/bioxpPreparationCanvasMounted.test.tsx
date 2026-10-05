import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import type { MethodValue, MethodCatalog } from '../../src/lib/bioxpMethods';
import catalog from '../fixtures/bioxp_method_deck_catalog.json';

let root: Root | undefined, host: HTMLDivElement, current: MethodValue, bindings: MethodValue;
const reference = (id: string) => ({ expr: { version: 1, op: 'param', id } });
const plate = { id: 'reaction', name: 'Reaction plate', station: 'LOC_MS', profile_id: '', retained: null };
const input = (volume: string) => ({ source: { station: 'LOC_RC', location_id: 3, wells: ['A1'] }, destination: { station: 'LOC_MS', location_id: 0, labware_id: 'reaction', wells: ['A1'], retained: null }, volume_ul: volume, channels: [0], extension: { keep: null } });
async function mount(method: MethodValue, values: MethodValue) {
    host = document.createElement('div'); document.body.append(host);
    function Owner() { const [draft, setDraft] = useState(method), [value, setValue] = useState(values); current = draft; bindings = value; return <BioXpMethodDeckWorkbench method={draft} onChange={setDraft} bindings={value} onBindingsChange={setValue} rootSchema={{}} catalog={catalog as unknown as MethodCatalog} />; }
    root = createRoot(host); await act(async () => root!.render(<Owner />));
}
async function click(label: string) {
    const target = [...host.querySelectorAll<HTMLElement>('button,[role=button]')].find(e => e.getAttribute('aria-label') === label || e.textContent === label)!;
    expect(target, label).toBeTruthy(); await act(async () => target.dispatchEvent(new MouseEvent('click', { bubbles: true })));
}
async function fill(label: string, raw: string) {
    const input = host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
    expect(input).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, raw); input.dispatchEvent(new Event('input', { bubbles: true })); });
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); host?.remove(); root = undefined; });

it('edits preparation amounts and destination wells through the existing independent binding owner', async () => {
    const method = { schema: 'bms.bioxp-method.v1', deck_plan: { labware: [plate], materials: [], assignments: [] }, parameters: ['a', 'b'].map(id => ({ id, type: 'object' })), steps: ['a', 'b'].map(id => ({ step_id: id, type: 'action', action: 'transfer', label: `Reagent ${id.toUpperCase()}`, inputs: reference(id) })) };
    const initial = { a: input('001.2500'), b: input('002.5000') };
    await mount(method, initial); await click('Prepare Reaction plate');
    expect(host.querySelector('[aria-label="Plate preparation"]')).not.toBeNull();
    await fill('Reagent B preparation volume (µL)', '003.7500'); await click('Select addition Reagent B'); await click('Preparation well B2');
    expect(bindings.a).toEqual(initial.a);
    expect(bindings.b).toEqual({ ...initial.b, volume_ul: '003.7500', destination: { ...initial.b.destination, wells: ['A1', 'B2'] } });
    expect(current).toEqual(method);
    await click('Close editor'); await click('Prepare Reaction plate');
    expect((host.querySelector('[aria-label="Reagent B preparation volume (µL)"]') as HTMLInputElement).value).toBe('003.7500');
    const cold = structuredClone({ method: current, bindings }); await act(async () => root!.unmount()); host.remove(); root = undefined;
    await mount(cold.method, cold.bindings); await click('Prepare Reaction plate');
    expect(current).toEqual(cold.method); expect(bindings).toEqual(cold.bindings);
    expect(host.querySelector('[aria-label="Preparation well B2"]')?.getAttribute('aria-pressed')).toBe('false');
    await click('Select addition Reagent B');
    expect(host.querySelector('[aria-label="Preparation well B2"]')?.getAttribute('aria-pressed')).toBe('true');
});

it('does not decorate a retained endpoint expression or coerce an explicit null amount from preparation', async () => {
    const retained = { ...input(''), volume_ul: null, destination: reference('complex-destination') };
    await mount({ deck_plan: { labware: [plate], materials: [], assignments: [] }, parameters: [{ id: 'a', type: 'object' }], steps: [{ type: 'action', step_id: 'a', action: 'transfer', label: 'Retained addition', inputs: reference('a') }] }, { a: retained });
    await click('Prepare Reaction plate');
    expect(bindings.a).toEqual(retained);
    expect(current.steps).toEqual([{ type: 'action', step_id: 'a', action: 'transfer', label: 'Retained addition', inputs: reference('a') }]);
    expect(host.querySelector('[aria-label="Retained addition preparation volume (µL)"]')).toBeNull();
});
