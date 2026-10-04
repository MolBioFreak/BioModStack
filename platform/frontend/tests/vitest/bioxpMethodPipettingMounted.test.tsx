import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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
    await input('Aspirate speed', '030.00');
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
