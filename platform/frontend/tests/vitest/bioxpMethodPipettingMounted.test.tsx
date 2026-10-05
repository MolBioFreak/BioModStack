import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodPipettingEditor } from '../../src/components/BioXpMethodPipettingEditor';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import type { MethodValue, MethodCatalog } from '../../src/lib/bioxpMethods';
import catalog from '../fixtures/bioxp_method_deck_catalog.json';

let host: HTMLDivElement, root: Root, current: MethodValue;
async function settle() { await act(async () => { await new Promise(r => setTimeout(r, 5)); }); }
function Owner({ initial }: { initial: MethodValue }) {
    const [value, setValue] = useState(initial); current = value;
    return <BioXpMethodDeckWorkbench method={value} onChange={setValue} catalog={catalog as unknown as MethodCatalog} rootSchema={{}} />;
}
async function mount(initial: MethodValue) { await act(async () => root.render(<Owner initial={initial} />)); await settle(); }
async function click(name: string) {
    const button = [...host.querySelectorAll('button')].find(e => e.textContent === name || e.getAttribute('aria-label') === name)!;
    expect(button, name).toBeTruthy(); await act(async () => button.click()); await settle();
}
async function input(name: string, value: string) {
    const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${name}"]`)!;
    expect(el, name).toBeTruthy();
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    }); await settle();
}
async function well(station: string, address: string) {
    const el = host.querySelector<SVGGElement>(`[aria-label="Method deck"] [data-station="${station}"][data-well="${address}"]`)!;
    expect(el).toBeTruthy(); await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); await settle();
}
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

it('authors a native Move from a deck well through plain station/reference/height controls', async () => {
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Move authoring', steps: [] });
    await well('LOC_RC', 'A3'); await click('Add Move');
    await input('Move height', '1');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ location_id: 3, well: 'A3', position_flag: '1' });
    await input('Move reference well', 'B4');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ location_id: 3, well: 'B4', position_flag: '1' });
    await click('Sequence only'); expect(host.querySelector('[aria-label="Method deck"]')?.hasAttribute('hidden')).toBe(true);
    await click('Deck & sequence'); expect((current.steps as MethodValue[])[0].inputs).toMatchObject({ well: 'B4' });
});

it('keeps omitted fields, null lift, numeric spelling and endpoint extensions when editing a Transfer', async () => {
    const original = { source: { station: 'LOC_RC', location_id: 3, wells: ['A3'], labware_id: 'stable-source' }, destination: { station: 'LOC_OC', location_id: 1, wells: ['B4'] }, channels: [0], volume_ul: '001.200', source_lift_height_steps: null, future: { explicit: false } };
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Transfer authoring', steps: [{ step_id: 'transfer', type: 'action', action: 'transfer', inputs: original }] });
    await input('Volume per channel (µL)', '002.5000');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ ...original, volume_ul: '002.5000' });
    await input('Inputs transfer.aspirate_speed presence', 'value');
    await input('Inputs transfer.aspirate_speed', '030.00');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ ...original, volume_ul: '002.5000', aspirate_speed: '030.00' });
    await well('LOC_MS', 'A2'); await click('Use as source');
    expect(((current.steps as MethodValue[])[0].inputs as MethodValue).source).toEqual({ station: 'LOC_MS', location_id: 0, wells: ['A2'], labware_id: 'stable-source' });
});

it('keeps class and recipe values unchanged when a common Transfer field is edited', async () => {
    const original = { source: { station: 'LOC_RC', location_id: 3, wells: ['A3'] }, destination: { station: 'LOC_OC', location_id: 1, wells: ['B4'] }, liquid: { context: { mode: 'single-dispense' }, future: null }, recipe: { mode: 'single', future: false }, volume_ul: '010.00' };
    await mount({ schema: 'bms.bioxp-method.v1', name: 'Class Transfer', steps: [{ step_id: 'class-transfer', type: 'action', action: 'transfer', inputs: original }] });
    await input('Volume per channel (µL)', '012.500');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ ...original, volume_ul: '012.500' });
    expect(host.querySelector('[aria-label="Aspirate speed"]')).toBeNull();
    expect(host.textContent).toContain('Liquid class & recipe');
});

function TransferOwner({ initial }: { initial: MethodValue }) {
    const [node, setNode] = useState(initial);
    const [selection, onSelect] = useState({ station: '', wells: [] as string[] });
    current = node;
    return <BioXpMethodPipettingEditor node={node} onChange={setNode} catalog={catalog as unknown as MethodCatalog} selection={selection} onSelect={onSelect} endpointLabels={{ source: 'Authored reagent', destination: 'Planned reaction plate' }} />;
}
const transferNode = (inputs: MethodValue) => ({ step_id: 'direct', type: 'action', action: 'transfer', inputs });
async function direct(inputs: MethodValue) { await act(async () => root.render(<TransferOwner initial={transferNode(inputs)} />)); await settle(); }

it('connects two native well panes with exact volume, explicit adoption and ordered pairs, not broadcast', async () => {
    const original = { source: { station: 'LOC_RC', location_id: 3, wells: ['A3'], future: false }, destination: { station: 'LOC_MS', location_id: 0, wells: ['B2', 'C2'] }, volume_ul: '001.200', future: null };
    await direct(original);
    expect(host.querySelectorAll('[aria-label="Source wells"] button')).toHaveLength(96);
    expect(host.querySelectorAll('[aria-label="Destination wells"] button')).toHaveLength(96);
    expect(host.textContent).toContain('Authored reagent');
    expect(host.querySelectorAll('[aria-label="Transfer pairs"] li')).toHaveLength(2);
    expect(host.querySelector('[aria-label="Transfer pairs"]')?.textContent).toContain('Not set → C2');
    expect(host.querySelectorAll('.bioxp-transfer-scene > details')).toHaveLength(2);
    expect(host.querySelector('details[open]')).toBeNull();
    expect(host.querySelector('.bioxp-plan-leaf')).toBeNull();
    await click('Source well D4'); await click('Source well A1');
    expect(current.inputs).toEqual(original);
    await click('Use deck selection as source');
    expect(current.inputs).toEqual({ ...original, source: { ...original.source, wells: ['D4', 'A1'] } });
    await click('Move source reference 2 earlier');
    expect((current.inputs as MethodValue).source).toEqual({ ...original.source, wells: ['A1', 'D4'] });
    expect(host.querySelector('[aria-label="Transfer pairs"]')?.textContent).toBe('A1 → B2D4 → C2');
    await input('Volume per channel (µL)', '000.0500');
    expect((current.inputs as MethodValue).volume_ul).toBe('000.0500');
    expect((current.inputs as MethodValue).destination).toEqual(original.destination);
});

it('retains raw endpoint entries, nulls, expressions and unknown channels through unrelated edits and reorder', async () => {
    const original = { source: { station: 'LOC_RC', location_id: { expr: { op: 'param', id: 'location' } }, wells: ['A3', null, { expr: { op: 'param', id: 'well' } }, 'A3'], future: false }, destination: null, channels: [0, 'future', null, 9], volume_ul: { expr: { op: 'param', id: 'volume' } }, source_lift_height_steps: null, recipe: null };
    await direct(original);
    expect((host.querySelector('[aria-label="Volume per channel (µL)"]') as HTMLInputElement).disabled).toBe(true);
    expect((host.querySelector('[aria-label="Native recipe mode"]') as HTMLSelectElement).disabled).toBe(true);
    const toggle = host.querySelector<HTMLInputElement>('[aria-label="Transfer pipette 2"]')!;
    await act(async () => toggle.click()); await settle();
    expect(current.inputs).toEqual({ ...original, channels: [0, 'future', null, 9, 1] });
    await click('Move source reference 3 earlier');
    expect((current.inputs as MethodValue).source).toEqual({ ...original.source, wells: ['A3', original.source.wells[2], null, 'A3'] });
    expect((current.inputs as MethodValue).volume_ul).toEqual(original.volume_ul);
    expect((current.inputs as MethodValue).recipe).toBeNull();
});

it('uses catalog recipe choices without adding channel, liquid, tip or mixing defaults', async () => {
    const original = { recipe: { mode: 'unrecognized', future: false }, liquid: { context: { mode: 'single-dispense' }, future: null }, volume_ul: 0 };
    await direct(original);
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Native recipe mode"]')!;
    expect([...select.options].map(o => o.value)).toEqual(['', 'unrecognized', 'single', 'multi']);
    await input('Native recipe mode', 'multi');
    expect(current.inputs).toEqual({ ...original, recipe: { ...original.recipe, mode: 'multi' } });
    expect(host.querySelector('[aria-label="Inputs direct.recipe.mode"]')).toBeTruthy();
});

it('keeps expression-valued channels untouched and exposes native expression editing', async () => {
    const original = { source: null, channels: { expr: { op: 'param', id: 'channels' } }, volume_ul: null, recipe: { expr: { op: 'param', id: 'recipe' } }, future: false };
    await direct(original);
    expect(current.inputs).toEqual(original);
    expect((host.querySelector('[aria-label="Transfer pipette 1"]') as HTMLInputElement).disabled).toBe(true);
    expect(host.textContent).toContain('Advanced input fields & expressions');
    expect(host.textContent).toContain('Retained');
    await click('Show source on deck');
    expect(current.inputs).toEqual(original);
    await input('Inputs direct.channels reference', 'other-channels');
    expect(current.inputs).toEqual({ ...original, channels: { expr: { op: 'param', id: 'other-channels' } } });
});
