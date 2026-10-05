import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import { insertMethodAction, plateBoundEndpoint } from '../../src/lib/bioxpMethodCanvas';
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
async function well(station: string, well: string) { const el = host.querySelector(`[data-station="${station}"][data-well="${well}"]`)!; await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); }
const plan = { labware: [{ id: 'plate', name: 'Reaction plate', station: 'LOC_MS', profile_id: '' }], materials: [], assignments: [] };
const transfer = { source: { station: 'LOC_RC', location_id: 3, wells: ['A1'] }, destination: { station: 'LOC_MS', location_id: 0, wells: ['A1'], labware_id: 'plate', retained: null }, volume_ul: '002.5000', channels: [0], aspirate_speed: '030.00', dispense_speed: '040.00', source_position_flag: '0', destination_position_flag: '0', source_lift_height_steps: null, destination_lift_height_steps: null };
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); host = document.createElement('div'); document.body.append(host); });
afterEach(async () => { if (root) await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

it('inserts after processing and inside nested repeats without changing retained structure', () => {
    const nodes = [{ step_id: 'repeat', type: 'repeat', count: 0, future: null, steps: [{ step_id: 'hold', action: 'thermal_hold', inputs: { duration_s: '00.500' } }] }];
    const added = { step_id: 'addition', action: 'transfer', inputs: {} };
    expect(insertMethodAction(nodes, added, 'hold', 'after')).toEqual([{ ...nodes[0], steps: [...nodes[0].steps, added] }]);
    expect(insertMethodAction(nodes, added, 'hold', 'end')).toEqual([...nodes, added]);
    expect(nodes[0].steps).toHaveLength(1);
});

it('resolves only explicit endpoint edits, preserving null/expression and unknown occurrence placement', () => {
    expect(plateBoundEndpoint(transfer.destination, 'plate', plan, { labware: { plate: { station: 'LOC_TC' } } })).toEqual({ ...transfer.destination, station: 'LOC_TC', location_id: 2 });
    expect(plateBoundEndpoint(transfer.destination, 'plate', plan, null)).toBeNull();
    expect(plateBoundEndpoint(null, 'plate', plan)).toBeNull();
    expect(plateBoundEndpoint({ expr: { op: 'param' } }, 'plate', plan)).toBeNull();
    expect(transfer.destination.location_id).toBe(0);
});

it('connects real deck wells into one ordered action and opens that same connection locally', async () => {
    await mount({ schema: 'bms.bioxp-method.v1', steps: [{ step_id: 'thermal', type: 'action', action: 'thermal_hold', inputs: { target_temp_c: null } }], deck_plan: plan });
    await click('Connect wells'); await well('LOC_RC', 'A1'); expect((current.steps as unknown[])).toHaveLength(1);
    await well('LOC_MS', 'B2');
    const step = (current.steps as MethodValue[])[1];
    expect(step.inputs).toMatchObject({ source: { station: 'LOC_RC', location_id: 3, wells: ['A1'] }, destination: { station: 'LOC_MS', location_id: 0, wells: ['B2'], labware_id: 'plate' } });
    expect(host.querySelector('[aria-label="On-deck editor"] [aria-label="Volume per channel (µL)"]')).toBeTruthy();
    await input('Volume per channel (µL)', '007.5000');
    await click('Close editor'); expect(host.querySelector('[aria-label="On-deck editor"]')?.hasAttribute('hidden')).toBe(true);
    await click('Edit connection Add reagent'); expect(host.querySelector('[aria-label="On-deck editor"]')?.hasAttribute('hidden')).toBe(false);
    expect((host.querySelector('[aria-label="Volume per channel (µL)"]') as HTMLInputElement).value).toBe('007.5000');
    await click('Expand editor'); await click('Compact editor');
    expect((current.steps as MethodValue[])[0].inputs).toEqual({ target_temp_c: null });
    await click('Connect wells'); await well('LOC_RC', 'A2'); await click('Cancel connection'); expect(current.steps).toHaveLength(2);
});

it('on-edge edits retain parameter references and independent duplicate bindings through cold serialized reopen', async () => {
    const reference = { expr: { version: 1, op: 'param', id: 'addition', extension: null } };
    await mount({ parameters: [{ id: 'addition', type: 'object' }], deck_plan: plan, steps: [{ step_id: 'add', type: 'action', action: 'transfer', inputs: reference }] }, undefined, { addition: transfer });
    await click('Edit connection add'); await input('Volume per channel (µL)', '003.7500'); await click('Duplicate step'); await input('Volume per channel (µL)', '004.1250');
    expect(values.addition).toEqual({ ...transfer, volume_ul: '003.7500' });
    const copy = (current.steps as MethodValue[])[1]; expect((copy.inputs as any).expr.id).not.toBe('addition'); expect(values[(copy.inputs as any).expr.id]).toEqual({ ...transfer, volume_ul: '004.1250' });
    expect((current.steps as MethodValue[])[0].inputs).toEqual(reference);
    const cold = JSON.parse(JSON.stringify({ method: current, bindings: values })); await act(async () => root.unmount());
    await mount(cold.method, undefined, cold.bindings); expect(current).toEqual(cold.method); expect(values).toEqual(cold.bindings);
});

it('edits the selected structural path when independent groups retain identical local IDs', async () => {
    const group = (id: string, label: string) => ({ step_id: id, type: 'group', steps: [{ step_id: 'shared-local', type: 'action', action: 'transfer', label, inputs: structuredClone(transfer) }] });
    await mount({ steps: [group('first', 'First addition'), group('second', 'Second addition')] });
    await click('Second addition'); await input('Volume per channel (µL)', '009.250');
    expect((current.steps as any[])[0].steps[0].inputs.volume_ul).toBe('002.5000');
    expect((current.steps as any[])[1].steps[0].inputs.volume_ul).toBe('009.250');
    await click('Duplicate step'); await input('Volume per channel (µL)', '010.125');
    expect((current.steps as any[])[0].steps).toHaveLength(1);
    expect((current.steps as any[])[1].steps).toHaveLength(2);
    await click('Move step up');
    expect((host.querySelector('[aria-label="Volume per channel (µL)"]') as HTMLInputElement).value).toBe('010.125');
    await click('Remove step'); expect((current.steps as any[])[1].steps).toHaveLength(1);
    expect((current.steps as any[])[1].steps[0].inputs.volume_ul).toBe('009.250');
});

it('selects the named plate independently, prepares its destination and carries its stable identity', async () => {
    await mount({ deck_plan: plan, steps: [] });
    await click('Prepare Reaction plate');
    expect(host.querySelector('[aria-label="On-deck editor"]')?.hasAttribute('hidden')).toBe(false);
    const add = host.querySelector<HTMLButtonElement>('.bioxp-method-object-actions button')!;
    await act(async () => add.click());
    expect((current.steps as any[])[0].inputs).toMatchObject({ destination: { station: 'LOC_MS', location_id: 0, labware_id: 'plate' } });
    expect((current.steps as any[])[0].inputs).not.toHaveProperty('source');
    await click('Prepare Reaction plate'); await click('Move plate');
    expect((current.steps as any[])[1]).toMatchObject({ action: 'plate_move', inputs: { labware_id: 'plate' } });
    expect((current.steps as any[])[1].inputs).not.toHaveProperty('plate_id');
});

it('accepts editor-owned structural lowering without erasing the original whole-input binding', async () => {
    const bound = { segments: [], repeat: '03', retained: null };
    const reference = { expr: { version: 1, op: 'param', id: 'program' } };
    await mount({ parameters: [{ id: 'program', type: 'object' }], steps: [{ step_id: 'program-step', type: 'action', action: 'thermal_profile', inputs: reference }] }, undefined, { program: bound }, (_node, change) => <button onClick={() => change({ step_id: 'program-step', type: 'group', steps: [] })}>Lower selected program</button>);
    await click('Edit selected step'); await click('Lower selected program');
    expect(current.steps).toEqual([{ step_id: 'program-step', type: 'group', steps: [] }]);
    expect(values).toEqual({ program: bound });
    expect(current.parameters).toEqual([{ id: 'program', type: 'object' }]);
});

it.runIf(!!discovery && !!process.env.BMS_TEST_PYTHON)('uses actual compiled occurrence placement for an explicit same-plate target update, then verifies emitted native Move targets', async () => {
    const compile = (method: MethodValue): MethodCompile => { const result = spawnSync(process.env.BMS_TEST_PYTHON!, ['-c', 'import json,sys;from bioxp_method_compiler import compile_method;print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: '../api', encoding: 'utf8', input: JSON.stringify({ method, bindings: {}, dependencies: {} }) }); expect(result.status, result.stderr).toBe(0); return JSON.parse(result.stdout); };
    const nativeTransfer = structuredClone(transfer); delete (nativeTransfer.destination as Partial<typeof transfer.destination>).retained;
    const original = { schema: 'bms.bioxp-method.v1', name: 'Software-only receiving', deck_plan: plan, steps: [{ step_id: 'carry', type: 'action', action: 'plate_move', inputs: { labware_id: 'plate', plate_id: 'PL_POOL', target_location: 'LOC_TC' } }, { step_id: 'add', type: 'action', action: 'transfer', inputs: nativeTransfer }] };
    const before = compile(original); expect(before.document, JSON.stringify(before.issues)).toBeTruthy();
    await mount(original, before); await click('Edit connection add');
    const occurrence = before.provenance!.find(p => p.step_id === 'carry')!;
    await input('Endpoint placement context', String(occurrence.occurrence_id));
    expect((current.steps as any[])[1].inputs.destination.location_id).toBe(0);
    await click('Update destination to planned plate location');
    expect((current.steps as any[])[1].inputs.destination).toEqual({ ...nativeTransfer.destination, station: 'LOC_TC', location_id: 2 });
    const after = compile(JSON.parse(JSON.stringify(current))); expect(after.document, JSON.stringify(after.issues)).toBeTruthy();
    const actions = (after.document!.stages as any[]).flatMap(s => s.actions);
    expect(actions.filter(a => a.kind === 'pipette_position' && a.params.operation === 'move').map(a => a.params.location_id)).toEqual([3, 2]);
    if (process.env.BIOXP_CANVAS_UI_EXPORT) writeFileSync(process.env.BIOXP_CANVAS_UI_EXPORT, JSON.stringify({ original, before, edited: current, after }, null, 2));
});
