import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../src/lib/api';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import { insertMethodAction, plateBoundEndpoint, groupCanvasSteps, canUngroupCanvasNode, methodCarryLinks, duplicateCanvasNode } from '../../src/lib/bioxpMethodCanvas';
import type { MethodValue, MethodCompile } from '../../src/lib/bioxpMethods';
import catalog from '../fixtures/bioxp_method_deck_catalog.json';
const discovery = process.env.BIOXP_METHOD_MODEL_CONTRACT ? JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT, 'utf8')) : null;
let host: HTMLDivElement, root: Root, current: MethodValue, values: MethodValue;
let client: QueryClient, oldAdapter: typeof api.defaults.adapter;
async function mount(initial: MethodValue, preview?: MethodCompile, initialBindings: MethodValue = {}, actionProperties?: (node: MethodValue, change: (node: MethodValue) => void) => React.ReactNode) {
    function Owner() { const [method, setMethod] = useState(initial), [bindings, setBindings] = useState(initialBindings); current = method; values = bindings; return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} bindings={bindings} onBindingsChange={setBindings} catalog={discovery?.catalog ?? catalog} rootSchema={discovery?.schema.method ?? {}} preview={preview} actionProperties={actionProperties} />; }
    root = createRoot(host); await act(async () => root.render(<QueryClientProvider client={client}><Owner /></QueryClientProvider>));
}
async function click(label: string) { const button = [...host.querySelectorAll<HTMLButtonElement>('button,[role=button]')].find(b => b.textContent === label || b.getAttribute('aria-label') === label)!; expect(button, label).toBeTruthy(); await act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true }))); }
async function input(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
async function well(station: string, well: string) { const el = host.querySelector(`[data-station="${station}"][data-well="${well}"]`)!; await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); }
const plan = { labware: [{ id: 'plate', name: 'Reaction plate', station: 'LOC_MS', profile_id: '' }], materials: [], assignments: [] };
const transfer = { source: { station: 'LOC_RC', location_id: 3, wells: ['A1'] }, destination: { station: 'LOC_MS', location_id: 0, wells: ['A1'], labware_id: 'plate', retained: null }, volume_ul: '002.5000', channels: [0], aspirate_speed: '030.00', dispense_speed: '040.00', source_position_flag: '0', destination_position_flag: '0', source_lift_height_steps: null, destination_lift_height_steps: null };
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    oldAdapter = api.defaults.adapter;
    // The integrated class selector reads the workspace library even before a class is chosen.
    // Keep the real query/Axios owners; this canvas fixture has an empty saved-class library.
    api.defaults.adapter = async config => {
        expect(config.method).toBe('get');
        expect(config.url).toBe('/api/bioxp/methods/liquid-classes');
        expect(config.params).toEqual({ search: '', offset: 0, limit: 25 });
        return { config, status: 200, statusText: 'OK', headers: {}, data: [] };
    };
    host = document.createElement('div'); document.body.append(host);
});
afterEach(async () => { if (root) await act(async () => root.unmount()); client.clear(); api.defaults.adapter = oldAdapter; host.remove(); vi.unstubAllGlobals(); });

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
    expect(current.steps).toHaveLength(1);
    await click('Move to Thermal cycler');
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


it('frames exactly adjacent selected children and preserves behavior-bearing wrappers', () => {
    const nodes = ['a', 'b', 'c'].map(step_id => ({ step_id, type: 'action', action: 'note', inputs: { message: step_id }, extension: null }));
    const grouped = groupCanvasSteps(nodes, ['a', 'b'], 'group', 'frame')!;
    expect(grouped[0].steps).toEqual(nodes.slice(0, 2));
    expect(grouped[1]).toBe(nodes[2]);
    expect(groupCanvasSteps(nodes, ['a', 'c'], 'group', 'frame')).toBeNull();
    expect(canUngroupCanvasNode(grouped[0])).toBe(true);
    expect(canUngroupCanvasNode({ ...grouped[0], enabled: false })).toBe(false);
    expect(canUngroupCanvasNode({ ...grouped[0], on_error: 'pause_for_operator' })).toBe(false);
    expect(canUngroupCanvasNode({ ...grouped[0], future: null })).toBe(false);
    expect(groupCanvasSteps(nodes, ['b'], 'repeat', 'repeat')![1]).toMatchObject({ count: '', steps: [nodes[1]] });
});

it('groups, ungroups and nests repeats through the actual ordered selection controls', async () => {
    const nodes = ['before', 'first', 'second', 'after'].map(step_id => ({ step_id, type: 'action', action: 'note', inputs: { message: step_id, future: null } }));
    await mount({ steps: nodes });
    const mark = async (index: number) => { const box = host.querySelector<HTMLInputElement>(`[data-method-outline="/steps"] > ol [aria-label="Include step ${index} in group"]`)!; await act(async () => box.click()); };
    await mark(2); await mark(3); await click('Group selected');
    expect((current.steps as any[]).map(n => n.step_id)).toEqual(['before', expect.any(String), 'after']);
    expect((current.steps as any[])[1].steps).toEqual(nodes.slice(1, 3));
    await click('Ungroup steps'); expect(current.steps).toEqual(nodes);
    await mark(2); await mark(3); await click('Repeat selected'); await input('Repeat count', '02');
    const repeated = (current.steps as any[])[1];
    expect(repeated).toMatchObject({ type: 'repeat', count: '02', steps: nodes.slice(1, 3) });
    await mark(2);
    const outerRepeat = [...host.querySelectorAll<HTMLButtonElement>('[data-method-outline="/steps"] > .bioxp-method-group-tools > button')].find(b => b.textContent === 'Repeat selected')!;
    await act(async () => outerRepeat.click()); await input('Repeat count', '0');
    expect((current.steps as any[])[1]).toMatchObject({ type: 'repeat', count: '0', steps: [repeated] });
    const cold = JSON.parse(JSON.stringify(current)); await act(async () => root.unmount()); await mount(cold); expect(current).toEqual(cold);
});

it('projects distinct outgoing and return carry links without asserting conditional custody', async () => {
    const make = (id: string, target: string) => ({ step_id: id, type: 'action', action: 'plate_move', inputs: { labware_id: 'plate', plate_id: 'PL_POOL', target_location: target, retained: null } });
    const method = { deck_plan: plan, steps: [make('out', 'LOC_TC'), make('back', 'LOC_MS')] };
    const actualCatalog = discovery?.catalog ?? catalog;
    const links = methodCarryLinks(method, {}, plan, actualCatalog);
    expect(links.map(l => [l.source, l.destination])).toEqual([['LOC_MS', 'LOC_TC'], ['LOC_TC', 'LOC_MS']]);
    expect(methodCarryLinks({ steps: [{ step_id: 'scope', type: 'if', condition: {}, then: method.steps }, make('later', 'LOC_TC')] }, {}, plan, actualCatalog).every(l => l.source === undefined)).toBe(true);
    await mount(method); await click('Edit plate carry Reaction plate');
    expect(host.querySelector('[aria-label="On-deck editor"] [aria-label="Plate to carry"]')).toBeTruthy();
    expect(current).toEqual(method);
    expect(host.querySelectorAll('[data-carry-from="LOC_MS"][data-carry-to="LOC_TC"]')).toHaveLength(1);
    expect(host.querySelectorAll('[data-carry-from="LOC_TC"][data-carry-to="LOC_MS"]')).toHaveLength(1);
});

it('adds a carry through pointer drag without panning, retargeting or adding on cancellation', async () => {
    await mount({ deck_plan: plan, steps: [] });
    const plate = host.querySelector('[data-labware="plate"]')!;
    const target = host.querySelector('[data-station="LOC_TC"][data-well="A1"]')!;
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: vi.fn(() => target) });
    const pointer = async (type: string, x: number, y: number) => { await act(async () => plate.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0, clientX: x, clientY: y }))); };
    await pointer('pointerdown', 10, 10); await pointer('pointermove', 90, 70); await pointer('pointercancel', 90, 70); await pointer('pointerup', 90, 70);
    expect(current.steps).toEqual([]);
    await pointer('pointerdown', 10, 10); await pointer('pointermove', 90, 70); await pointer('pointerup', 90, 70);
    expect(current.steps).toHaveLength(1);
    expect((current.steps as any[])[0]).toMatchObject({ action: 'plate_move', inputs: { labware_id: 'plate', target_location: 'LOC_TC' } });
    expect((current.steps as any[])[0].inputs).not.toHaveProperty('plate_id');
    expect(current.deck_plan).toEqual(plan);
    delete (document as any).elementFromPoint;
});

it('cancels only the unfinished carry menu, and supports keyboard-equivalent creation', async () => {
    await mount({ deck_plan: plan, steps: [] });
    const plate = host.querySelector('[data-labware="plate"]')!;
    await act(async () => plate.dispatchEvent(new KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true })));
    await click('Move plate'); await click('Cancel plate move'); expect(current.steps).toEqual([]);
    await click('Move plate'); await click('Move to Thermal cycler');
    expect((current.steps as any[])[0].inputs).toEqual({ labware_id: 'plate', target_location: 'LOC_TC' });
    expect(host.querySelector('[aria-label="Choose plate destination"]')).toBeNull();
});


it('duplicates repeat counts, condition references and dependent bindings independently while calls stay shared', () => {
    const param = (id: string) => ({ expr: { version: 1, op: 'param', id, retained: null } });
    const method = { parameters: [{ id: 'count', type: 'integer', default: param('base') }, { id: 'base', type: 'integer', default: '02' }], procedures: [{ id: 'procedure', steps: [] }] };
    const node = { step_id: 'repeat', type: 'repeat', count: param('count'), steps: [{ step_id: 'call', type: 'call', procedure_id: 'procedure', arguments: { passes: param('count') } }] };
    const copied = duplicateCanvasNode(method, node, { count: param('base'), base: '003' });
    const count = (copied.node.count as any).expr.id;
    const base = (copied.bindings[count] as any).expr.id;
    expect(count).not.toBe('count'); expect(base).not.toBe('base');
    expect(copied.parameters).toHaveLength(4);
    expect(copied.bindings).toMatchObject({ count: param('base'), base: '003', [base]: '003' });
    expect((copied.node.steps as any[])[0]).toMatchObject({ procedure_id: 'procedure', arguments: { passes: param(count) } });
    expect((copied.node.steps as any[])[0].step_id).not.toBe('call');
    expect(method.parameters).toHaveLength(2);
});

it.runIf(!!discovery && !!process.env.BMS_TEST_PYTHON)('compiles UI-created repeat scopes with exact total passes, zero omission and raw cold-reopened counts', async () => {
    const original = { schema: 'bms.bioxp-method.v1', name: 'Repeat receiving', steps: ['before', 'one', 'two', 'after'].map(step_id => ({ step_id, type: 'action', action: 'note', inputs: { message: step_id } })) };
    await mount(original);
    for (const index of [2, 3]) await act(async () => host.querySelector<HTMLInputElement>(`[data-method-outline="/steps"] > ol [aria-label="Include step ${index} in group"]`)!.click());
    await click('Repeat selected'); await input('Repeat count', '02');
    const compile = () => { const result = spawnSync(process.env.BMS_TEST_PYTHON!, ['-c', 'import json,sys;from bioxp_method_compiler import compile_method;print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: '../api', encoding: 'utf8', input: JSON.stringify({ method: current }) }); expect(result.status, result.stderr).toBe(0); const compiled = JSON.parse(result.stdout); expect(compiled.document, JSON.stringify(compiled.issues)).toBeTruthy(); return compiled; };
    const first = compile(); expect(first.provenance.map((p: any) => p.step_id)).toEqual(['before', 'one', 'two', 'one', 'two', 'after']);
    const cold = JSON.parse(JSON.stringify(current)); await act(async () => root.unmount()); await mount(cold);
    await click('Select step 2'); expect((host.querySelector('[aria-label="Repeat count"]') as HTMLInputElement).value).toBe('02');
    await input('Repeat count', '0'); expect(compile().provenance.map((p: any) => p.step_id)).toEqual(['before', 'after']);
});

it('copies only the explicitly authored native plate association into a newly requested carry', async () => {
    const associated = { ...plan, labware: [{ ...plan.labware[0], native_plate_id: 'PL_POOL', retained: null }] };
    await mount({ deck_plan: associated, steps: [] });
    await click('Prepare Reaction plate'); await click('Move plate'); await click('Move to Thermal cycler');
    expect((current.steps as any[])[0].inputs).toEqual({ labware_id: 'plate', plate_id: 'PL_POOL', target_location: 'LOC_TC' });
    expect(current.deck_plan).toEqual(associated);
});
