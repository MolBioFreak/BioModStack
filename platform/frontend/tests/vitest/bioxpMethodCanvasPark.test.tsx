import { writeFileSync } from 'node:fs';
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import type { MethodValue } from '../../src/lib/bioxpMethods';

let host: HTMLDivElement, root: Root | undefined;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; host?.remove(); vi.unstubAllGlobals(); });
const catalog = { actions: [{ action: 'park', inputs: { type: 'object', properties: { rehome: { type: 'boolean' } } } }] };
async function mounted(initial: MethodValue, initialBindings: MethodValue = {}) {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    let current = initial, bindings = initialBindings;
    const fetch = vi.fn(() => { throw new Error('Authoring must not submit'); }); vi.stubGlobal('fetch', fetch);
    function Owner() { const [method, setMethod] = useState(initial), [values, setValues] = useState(initialBindings); current = method; bindings = values; return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} bindings={values} onBindingsChange={setValues} catalog={catalog} rootSchema={{}} />; }
    host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => root!.render(<Owner />)); return () => ({ current, bindings, fetch });
}
const step = (inputs: MethodValue) => ({ step_id: 'park', type: 'action', action: 'park', inputs });
function toggle() { const input = host.querySelector<HTMLInputElement>('[aria-label="Home X/Y on Park"]'); expect(input).toBeTruthy(); return input!; }
it('projects omitted Park homing ON without rewriting the retained draft; edits emit explicit booleans only', async () => {
    const original = { schema: 'bms.bioxp-method.v1', steps: [step({ future: { raw: '001.00' } })] };
    const state = await mounted(original);
    expect(toggle().checked).toBe(true); expect(state().current).toEqual(original);
    await act(async () => toggle().click());
    expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: { raw: '001.00' }, rehome: false });
    await act(async () => toggle().click());
    expect((state().current.steps as MethodValue[])[0].inputs).toEqual({ future: { raw: '001.00' }, rehome: true });
    expect(state().fetch).not.toHaveBeenCalled();
    expect(host.textContent).toContain('more than 100'); expect(host.textContent).toContain('pauses'); expect(host.textContent).toContain('no automatic retry');
});

it('new Park from the ordinary action palette authors rehome true explicitly without submitting', async () => {
    const state = await mounted({ schema: 'bms.bioxp-method.v1', steps: [] });
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Action palette"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, 'park'); select.dispatchEvent(new Event('change', { bubbles: true })); });
    const add = [...host.querySelectorAll('button')].find(b => b.textContent === 'Add step')!;
    await act(async () => add.click());
    expect((state().current.steps as MethodValue[])[0]).toMatchObject({ action: 'park', inputs: { rehome: true } });
    expect(toggle().checked).toBe(true); expect(state().fetch).not.toHaveBeenCalled();
});

it.each([false, true])('cold reopened explicit %s and parameter-bound Park keep their exact boolean through editing', async rehome => {
    const reference = { expr: { version: 1, op: 'param', id: 'park_settings' } };
    const initial = JSON.parse(JSON.stringify({ schema: 'bms.bioxp-method.v1', parameters: [{ id: 'park_settings', type: 'object', default: { rehome } }], steps: [{ ...step({}), inputs: reference }] }));
    const state = await mounted(initial);
    expect(toggle().checked).toBe(rehome); expect(state().bindings).toEqual({});
    await act(async () => toggle().click());
    expect(state().bindings).toEqual({ park_settings: { rehome: !rehome } });
    expect((state().current.steps as MethodValue[])[0].inputs).toEqual(reference);
    expect(state().fetch).not.toHaveBeenCalled();
});
it.each([null, 'false', { expr: { version: 1, op: 'param', id: 'home' } }])('does not coerce a retained nonboolean homing value %s', async rehome => {
    const initial = { steps: [step({ rehome })] }, state = await mounted(initial);
    expect(toggle().disabled).toBe(true); expect(state().current).toEqual(initial);
});
it.each(['move_cover', 'thermal_door', 'move'])('does not add Park homing to %s', async action => {
    const initial = { steps: [{ ...step({}), action }] }, state = await mounted(initial);
    expect(host.querySelector('[aria-label="Home X/Y on Park"]')).toBeNull(); expect(state().current).toEqual(initial);
});
it('exports the ordinary toggle OFF and ON requests for the real disconnected compiler', async () => {
    const state = await mounted({ schema: 'bms.bioxp-method.v1', name: 'Park toggle receiving', steps: [step({})] });
    const requests: MethodValue[] = [];
    for (const rehome of [false, true]) {
        await act(async () => toggle().click());
        const method = JSON.parse(JSON.stringify(state().current));
        expect(method.steps[0].inputs).toEqual({ rehome });
        requests.push({ method });
    }
    if (process.env.BMS_PARK_EXPORT) writeFileSync(process.env.BMS_PARK_EXPORT, JSON.stringify(requests, null, 2));
    expect(state().fetch).not.toHaveBeenCalled();
});
