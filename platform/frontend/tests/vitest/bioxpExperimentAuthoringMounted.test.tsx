import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { BioXpMethodDeckWorkbench } from '../../src/components/BioXpMethodDeckWorkbench';
import type { MethodValue } from '../../src/lib/bioxpMethods';
import publishedCatalog from '../fixtures/bioxp_method_deck_catalog.json';
import { methodInputBinding, removeMethodInputParameters } from '../../src/lib/bioxpMethodInputBinding';
import { api } from '../../src/lib/api';

const contractPath = process.env.BIOXP_METHOD_MODEL_CONTRACT;
const contracts = contractPath ? JSON.parse(readFileSync(contractPath, 'utf8')) : null;
let host: HTMLDivElement, root: Root, client: QueryClient, oldAdapter: typeof api.defaults.adapter;
let requests: any[], db: any, results: any[];
async function settle() { await act(async () => { await new Promise(r => setTimeout(r, 12)); }); }
async function mount() { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); root = createRoot(host); await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={7} connected={false} controlsEnabled={false} /></QueryClientProvider>)); await settle(); }
async function reveal(el: Element) {
    const ancestors: HTMLDetailsElement[] = [];
    for (let p = el.parentElement; p; p = p.parentElement) if (p instanceof HTMLDetailsElement && !p.open) ancestors.push(p);
    await act(async () => { ancestors.reverse().forEach(p => (p.querySelector(':scope > summary') as HTMLElement)?.click()); });
    await settle();
}
async function click(name: string) { const el = [...host.querySelectorAll('button')].find(n => n.textContent === name || n.getAttribute('aria-label') === name)!; expect(el, name).toBeTruthy(); expect(el.disabled, name).toBe(false); await reveal(el); await act(async () => el.click()); await settle(); }
function control(name: string) { return host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${name}"]`) ?? [...host.querySelectorAll('label')].find(n => [...n.childNodes].filter(c => c.nodeType === Node.TEXT_NODE).map(c => c.textContent).join('') === name)?.querySelector<HTMLInputElement | HTMLSelectElement>('input,select'); }
async function input(name: string, value: string) { const el = control(name)!; expect(el, name).toBeTruthy(); await reveal(el); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function choose(name: string, label: string) { const el = control(name) as HTMLSelectElement; expect(el, name).toBeTruthy(); const option = [...el.options].find(o => o.textContent === label)!; expect(option, `${name}: ${label}`).toBeTruthy(); await input(name, option.value); }
async function text(name: string, value: string) { const el = host.querySelector<HTMLTextAreaElement>(`textarea[aria-label="${name}"]`)!; expect(el).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); }); await settle(); }
async function selectPath(path: number[]) {
    for (const [depth, index] of path.entries()) {
        let section = host.querySelector<HTMLElement>('.bioxp-method-inspector > [aria-label="Method outline"]')!;
        for (let level = 0; level < depth; level++) section = section.querySelector<HTMLElement>(':scope > details > fieldset > [aria-label="Method outline"], :scope > fieldset > [aria-label="Method outline"]')!;
        expect(section, `outline at depth ${depth}, path ${path}`).toBeTruthy();
        const button = section.querySelector<HTMLButtonElement>(`:scope > .bioxp-method-sequence > li:nth-child(${index + 1}) > button`)!; expect(button, `step path ${path}`).toBeTruthy();
        await act(async () => button.click()); await settle();
    }
}
async function check(name: string) { const el = control(name)!; expect(el, name).toBeTruthy(); await act(async () => el.click()); await settle(); }
async function well(station: string, address: string) { const el = host.querySelector(`[aria-label="Method deck"] [data-station="${station}"][data-well="${address}"]`)!; expect(el).toBeTruthy(); await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); await settle(); }
async function selectStep(text: string) { const el = [...host.querySelectorAll<HTMLButtonElement>('.bioxp-method-sequence button')].find(e => e.textContent?.includes(text))!; expect(el, text).toBeTruthy(); await act(async () => el.click()); await settle(); }
async function transfer(source: string, destination: string, volume: string, sourceStation = 'LOC_RC', destinationStation = 'LOC_TC') {
    await well(sourceStation, source); await click('Use as source'); await well(destinationStation, destination); await click('Use as destination');
    await input('Volume per channel (µL)', volume); await input('Aspirate speed', '030.00'); await input('Dispense speed', '040.00');
    await input('Source move Z position', '1'); await input('Destination move Z position', '2');
    await input('Source lift target', 'high'); await input('Destination lift target', 'high');
    if (!(control('Pipette 1') as HTMLInputElement).checked) await check('Pipette 1');
}
async function hold(temp: string) { await input('Step Thermal bank', 'nest'); await input('Step Target temperature (°C)', temp); await input('Step Hold time (seconds)', '01.00'); await input('Step Start hold timer', 'dispatch'); }
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; results = []; db = {};
    host = document.createElement('div'); document.body.append(host); oldAdapter = api.defaults.adapter;
    const adapter: AxiosAdapter = async config => {
        const path = config.url!.split('/').at(-1)!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method: config.method, url: config.url, body }); let data: any;
        if (['catalog', 'schema', 'examples'].includes(path)) data = contracts?.[path];
        else if (path === 'compile' || path === 'check') {
            const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? 'python', ['-c', 'import json,sys;from bioxp_method_compiler import compile_method;print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: process.env.BIOXP_METHOD_API_DIR ?? '../api', encoding: 'utf8', input: JSON.stringify(body) });
            expect(produced.status, produced.stderr).toBe(0); data = JSON.parse(produced.stdout); results.push({ input: body, result: data });
        } else if (path === 'presets') data = [];
        else if (path === 'library' && config.method === 'get') data = Object.values(db);
        else if (path === 'library' && config.method === 'post') data = db.m1 = { id: 'm1', revision: 1, name: body.name, method: body.method };
        else if (path === 'revisions') data = [db.m1];
        else if (path === 'm1' && config.method === 'put') { expect(body.expected_base_revision).toBe(db.m1.revision); data = db.m1 = { ...db.m1, revision: db.m1.revision + 1, method: body.method, name: body.name }; }
        else if (path === 'm1' || /\/revisions\/\d+$/.test(config.url!)) data = db.m1;
        else throw new Error(`No robot transport allowed: ${config.method} ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
    api.defaults.adapter = adapter;
});
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

it('projects only declared direct object parameters, preserving explicit null/non-object and inherited defaults', () => {
    const reference = { expr: { version: 1, op: 'param', id: 'reaction', future: null } };
    const method = { parameters: [{ id: 'reaction', type: 'object', default: { volume_ul: '001.20', retained: null } }] };
    expect(methodInputBinding(method, { inputs: reference }, {})).toMatchObject({ id: 'reaction', inherited: true, editable: true, inputs: { volume_ul: '001.20', retained: null } });
    for (const value of [null, false, 'raw', [], { expr: { version: 1, op: 'add', operands: [] } }]) expect(methodInputBinding(method, { inputs: reference }, { reaction: value })).toMatchObject({ editable: false, inputs: value });
    expect(methodInputBinding(method, { inputs: { expr: { version: 1, op: 'add', operands: [] } } }, {})).toMatchObject({ editable: false });
    expect(methodInputBinding({}, { inputs: reference }, {})).toMatchObject({ editable: false });
});

it('removes only orphaned step inputs and retains references from other steps or parameter defaults', () => {
    const expr = { expr: { version: 1, op: 'param', id: 'reagent' } };
    const removed = { step_id: 'removed', action: 'transfer', inputs: expr };
    const method = { parameters: [{ id: 'reagent', type: 'object', future: null }], steps: [] };
    expect(removeMethodInputParameters(method, removed, { reagent: {} })).toMatchObject({ method: { parameters: [] }, bindings: {}, changed: true });
    expect(removeMethodInputParameters({ ...method, steps: [{ inputs: expr }] }, removed, { reagent: {} }).changed).toBe(false);
    expect(removeMethodInputParameters({ ...method, parameters: [...method.parameters, { id: 'other', type: 'object', default: expr }] }, removed, { reagent: {} }).changed).toBe(false);
});

it('mounted inherited defaults are visible without bindings; editing and duplicating preserve AST/default/null/unknown values independently', async () => {
    const reference = { expr: { version: 1, op: 'param', id: 'reaction', future: null } };
    const inherited = { source: { station: 'LOC_RC', location_id: 3, wells: ['A1'], future: null }, volume_ul: '001.2000', channels: [0], source_lift_height_steps: null, future: { raw: false } };
    const initial: MethodValue = { parameters: [{ id: 'reaction', type: 'object', default: inherited, future: false }], steps: [{ step_id: 'addition', type: 'action', action: 'transfer', inputs: reference }] };
    let current: MethodValue = initial, bindings: MethodValue = {};
    function Owner() {
        const [method, setMethod] = useState(initial), [values, setValues] = useState<MethodValue>({}); current = method; bindings = values;
        return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} bindings={values} onBindingsChange={setValues} rootSchema={{}} catalog={publishedCatalog} />;
    }
    root = createRoot(host); await act(async () => root.render(<Owner />)); await settle();
    expect((control('Volume per channel (µL)') as HTMLInputElement).value).toBe('001.2000'); expect(bindings).toEqual({}); expect(host.textContent).toContain('Inherited parameter default');
    await input('Volume per channel (µL)', '002.5000'); expect(bindings.reaction).toEqual({ ...inherited, volume_ul: '002.5000' }); expect((current.parameters as MethodValue[])[0].default).toEqual(inherited); expect((current.steps as MethodValue[])[0].inputs).toEqual(reference);
    await click('Duplicate step'); await input('Volume per channel (µL)', '003.7500');
    const copy = (current.steps as MethodValue[])[1], copyId = (copy.inputs as any).expr.id;
    expect(copyId).not.toBe('reaction'); expect(bindings.reaction).toEqual({ ...inherited, volume_ul: '002.5000' }); expect(bindings[copyId]).toEqual({ ...inherited, volume_ul: '003.7500' });
    expect((current.parameters as MethodValue[])[1]).toMatchObject({ default: inherited, future: false });
    await click('Remove step');
    expect((current.parameters as MethodValue[]).map(p => p.id)).toEqual(['reaction']);
    expect(bindings).toEqual({ reaction: { ...inherited, volume_ul: '002.5000' } });
});

it('mounted explicit null and arbitrary expressions stay advanced instead of being replaced by empty friendly controls', async () => {
    for (const value of [null, 'retained raw', { expr: { version: 1, op: 'add', operands: [] } }]) {
        const method = { parameters: [{ id: 'reaction', type: 'object' }], steps: [{ step_id: 'addition', type: 'action', action: 'transfer', inputs: { expr: { version: 1, op: 'param', id: 'reaction' } } }] };
        const changes = vi.fn(), bindings = { reaction: value };
        root = createRoot(host); await act(async () => root.render(<BioXpMethodDeckWorkbench method={method} onChange={changes} bindings={bindings} onBindingsChange={changes} rootSchema={{}} catalog={publishedCatalog} />)); await settle();
        expect(control('Volume per channel (µL)')).toBeFalsy(); expect(host.textContent).toContain('Retained expression or non-object input'); expect(changes).not.toHaveBeenCalled(); expect(bindings.reaction).toEqual(value);
        await act(async () => root.unmount());
    }
});

it('inserts holds around a selected nested profile and puts named additions before its containing cycle group', async () => {
    const initial: MethodValue = { steps: [{ step_id: 'program', type: 'group', label: 'Program', steps: [{ step_id: 'cycle', type: 'action', action: 'thermal_profile', inputs: { segments: [] } }] }] };
    let current = initial;
    function Owner() { const [method, setMethod] = useState(initial); current = method; return <BioXpMethodDeckWorkbench method={method} onChange={setMethod} rootSchema={{}} catalog={publishedCatalog} />; }
    root = createRoot(host); await act(async () => root.render(<Owner />)); await settle();
    await selectStep('Program');
    const cycle = host.querySelector<HTMLButtonElement>('.bioxp-method-inspector fieldset .bioxp-method-sequence button')!;
    await act(async () => cycle.click()); await settle();
    await input('Insert step', 'before'); await click('Insert initial / final hold');
    expect(((current.steps as MethodValue[])[0].steps as MethodValue[]).map(n => n.action)).toEqual(['thermal_hold', 'thermal_profile']);
    await input('Addition name', 'Reagent before nested cycling'); await click('Add reagent / aliquot before cycling');
    expect((current.steps as MethodValue[]).map(n => n.label)).toEqual(['Reagent before nested cycling', 'Program']);
    await click('Remove step'); expect((current.steps as MethodValue[])).toHaveLength(1);
});

it.runIf(!!contracts)('ordinary New PCR → named deck setup → independent additions and holds → cold Save/Open → actual compiler without robot submission', async () => {
    await mount(); await click('New PCR'); await input('Method name', 'Operator-authored PCR transport test');
    expect(control('Volume per channel (µL)')).toBeTruthy();
    await click('Save');
    expect(db.m1.method.editor_state.run_inputs).toBeUndefined(); // Viewing empty controls authored no bindings.
    expect(db.m1.method.steps.find((n: any) => n.step_id === 'assemble').inputs).toEqual(contracts.examples[4].method.steps[1].inputs);
    // Author the source-defined mixing compound through its ordinary selected-step controls.
    await selectStep('Mix PCR reaction');
    const child = async (index: number) => { const el = host.querySelectorAll<HTMLButtonElement>('.bioxp-method-inspector fieldset [aria-label="Method outline"] .bioxp-method-sequence button')[index]!; expect(el).toBeTruthy(); await act(async () => el.click()); await settle(); };
    await child(0); await input('Move station', 'LOC_TC'); await input('Move reference well', 'A1'); await input('Move height', '0');
    await child(1); await input('Pipetting station', 'LOC_TC');
    await child(2); await check('Mix pipette 1'); await input('Mix volume per plunger (µL)', '002.00'); await input('Mix aspirate speed', '030.00'); await input('Mix dispense speed', '040.00'); await input('Mix cycles', '02');
    await child(3); await input('Pipetting station', 'LOC_TC'); await input('Lift height (steps)', '0100');
    await selectStep('Combine authored reaction components');
    await click('Set up labware & reagents'); await well('LOC_RC', 'A1'); await click('Add labware at selected station'); await input('Labware 1 name', 'Reagent plate');
    await click('Add reagent'); await input('Material 1 name', 'Primer mix'); await input('Planned amount per selected well (µL)', '020.000'); await click('Assign material to selected wells');
    await well('LOC_TC', 'A1'); await click('Add labware at selected station'); await input('Labware 2 name', 'Reaction vessels');
    await transfer('A1', 'A1', '002.5000'); await input('Label assemble', 'Primer addition');
    await click('Duplicate step');
    await input('Volume per channel (µL)', '003.7500'); await well('LOC_RC', 'A2'); await click('Use as source');
    const label = [...host.querySelectorAll<HTMLInputElement>('input')].find(n => n.getAttribute('aria-label')?.startsWith('Label '))!;
    await input(label.getAttribute('aria-label')!, 'Polymerase addition');
    await click('Move step up'); await click('Move step down');
    await input('New Transfer settings', 'manual'); await input('Addition name', 'Buffer aliquot'); await click('Add reagent / aliquot before cycling'); await transfer('A3', 'A1', '004.1250');
    await selectStep('Repeated PCR cycles'); await input('Cycle count', '03'); await click('Add stage');
    await input('Stage 1 Thermal bank', 'nest'); await input('Stage 1 Target temperature (°C)', '061.00'); await input('Stage 1 Hold time (seconds)', '01.00'); await input('Stage 1 Start hold timer', 'dispatch');
    await click('Add stage'); await input('Stage 2 Thermal bank', 'nest'); await input('Stage 2 Target temperature (°C)', '072.00'); await input('Stage 2 Hold time (seconds)', '02.00'); await input('Stage 2 Start hold timer', 'dispatch');
    await click('Stage 2 Move up'); await click('Stage 1 Move down');
    await input('Insert step', 'before'); await click('Insert initial / final hold'); await hold('090.00');
    await selectStep('Repeated PCR cycles'); await input('Insert step', 'after'); await click('Insert initial / final hold'); await hold('020.00');
    await selectStep('Collect amplified material'); await transfer('A1', 'A1', '010.3750');
    await click('Sequence only'); await click('Deck & sequence'); await click('Save');
    const saved = structuredClone(db.m1.method), bindings = saved.editor_state.run_inputs.bindings;
    const assemble = saved.steps.find((n: any) => n.step_id === 'assemble');
    const duplicate = saved.steps.find((n: any) => n.label === 'Polymerase addition');
    expect(duplicate.inputs.expr.id).not.toBe(assemble.inputs.expr.id);
    expect(bindings.assemble_inputs.volume_ul).toBe('002.5000'); expect(bindings[duplicate.inputs.expr.id].volume_ul).toBe('003.7500');
    expect(bindings.assemble_inputs.source.wells).toEqual(['A1']); expect(bindings[duplicate.inputs.expr.id].source.wells).toEqual(['A2']);
    expect(saved.deck_plan.labware.map((l: any) => l.name)).toEqual(['Reagent plate', 'Reaction vessels']);
    expect(saved.steps.findIndex((n: any) => n.label === 'Buffer aliquot')).toBeLessThan(saved.steps.findIndex((n: any) => n.step_id === 'seal'));
    const cycleIndex = saved.steps.findIndex((n: any) => n.step_id === 'cycle'); expect(saved.steps[cycleIndex - 1].action).toBe('thermal_hold'); expect(saved.steps[cycleIndex + 1].action).toBe('thermal_hold');
    await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', 'm1'); await click('Open'); await click('Preview');
    const compiled = results.at(-1); expect(compiled.input.method).toEqual(saved); expect(compiled.input.bindings).toEqual(bindings); expect(compiled.result.document, JSON.stringify(compiled.result.issues)).toBeTruthy(); expect(compiled.result.issues.filter((issue: any) => issue.category === 'representation'), JSON.stringify(compiled.result.issues)).toHaveLength(0);
    if (process.env.BIOXP_EXPERIMENT_UI_EXPORT) writeFileSync(process.env.BIOXP_EXPERIMENT_UI_EXPORT, JSON.stringify({ requests, compilation: compiled }, null, 2));
    expect(requests.filter(r => /quick-runs|\/runs|execute|operator/.test(r.url))).toHaveLength(0);
}, 60000);

it.runIf(!!contracts)('all eight actual scientific skeletons still open, bind and produce native documents', async () => {
    await mount(); expect(contracts.examples).toHaveLength(8);
    const examples = [...host.querySelectorAll('summary')].find(n => n.textContent === 'Examples & software fixtures')!; await act(async () => examples.click()); await settle();
    for (const [index, example] of contracts.examples.entries()) {
        await input('Scientific example', String(index)); expect((control('Method name') as HTMLInputElement).value).toBe(example.method.name);
        await input('Bound software fixture', String(index)); await click('Preview'); expect(results.at(-1).result.document, JSON.stringify(results.at(-1).result.issues)).toBeTruthy(); expect(results.at(-1).result.issues).toHaveLength(0);
    }
    expect(requests.filter(r => /quick-runs|\/runs|execute|operator/.test(r.url))).toHaveLength(0);
}, 60000);


it.runIf(!!contracts)('ordinary typed authoring of every discovered family and explicit magnetic variant → incomplete and cold complete Save/Open → real Python compile', async () => {
    const corpus: any[] = [];
    await mount();
    for (const example of contracts.examples) {
        await input('Workflow kind', example.id);
        const variant = example.authoring_variants?.find((v: any) => v.id === 'on_deck_magnetic');
        if (variant) {
            expect([...host.querySelectorAll('button')].find(b => b.textContent === 'New experiment')!.disabled).toBe(true);
            await input('Purification approach', 'external'); await click('New experiment'); await click('Save');
            expect(db.m1.method.steps).toEqual(example.method.steps);
            await input('Purification approach', variant.id);
        }
        await click('New experiment'); await input('Method name', `UI authored ${example.id} — software inputs only`);
        const selected = variant ?? example, original = selected.method;
        await click('Save');
        expect(db.m1.method.editor_state?.run_inputs?.bindings ?? {}).toEqual(selected.bindings ?? {});
        const incomplete = structuredClone(db.m1.method);
        await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', 'm1'); await click('Open');
        await click('Save'); expect(db.m1.method).toEqual(incomplete);
        await click('Set up labware & reagents');
        await well('LOC_RC', 'A1'); await click('Add labware at selected station'); await input('Labware 1 name', 'Authored reagent plate');
        await well('LOC_TC', 'A1'); await click('Add labware at selected station'); await input('Labware 2 name', 'Authored reaction plate');
        await well('LOC_OC', 'A1'); await click('Add labware at selected station'); await input('Labware 3 name', 'Authored collection and waste plate');
        const author = async (nodes: any[], prefix: number[] = []) => {
            for (const [index, node] of nodes.entries()) {
                const path = [...prefix, index]; await selectPath(path);
                if (node.type === 'repeat') { await input('Repeat count', '02'); await author(node.steps, path); }
                else if (node.type === 'group') await author(node.steps, path);
                else if (node.action === 'transfer') {
                    // Explicit SOFTWARE test chemistry/positioning values, never product defaults.
                    const magneticSource = ['supernatant_remove', 'wash_remove', 'eluate_collect'].includes(node.step_id);
                    const collection = magneticSource || node.step_id === 'collect';
                    await transfer('A1', node.step_id.includes('remove') ? 'A2' : 'A1', '002.5000', magneticSource ? 'LOC_MS' : collection ? 'LOC_TC' : 'LOC_RC', collection ? 'LOC_OC' : 'LOC_TC');
                    await choose('Source labware', collection ? 'Authored reaction plate' : 'Authored reagent plate');
                    await choose('Destination labware', collection ? 'Authored collection and waste plate' : 'Authored reaction plate');
                } else if (node.action === 'move') { await input('Move station', 'LOC_TC'); await input('Move reference well', 'A1'); await input('Move height', '0'); }
                else if (node.action === 'lower') await input('Pipetting station', 'LOC_TC');
                else if (node.action === 'lift') { await input('Pipetting station', 'LOC_TC'); await input('Lift height (steps)', '0100'); }
                else if (node.action === 'mix') { if (!(control('Mix pipette 1') as HTMLInputElement).checked) await check('Mix pipette 1'); await input('Mix volume per plunger (µL)', '002.00'); await input('Mix aspirate speed', '030.00'); await input('Mix dispense speed', '040.00'); await input('Mix cycles', '02'); }
                else if (node.action === 'wait') await input('Wait time (seconds)', '001.2500');
                else if (node.action === 'plate_move') {
                    await choose('Named labware', 'Authored reaction plate'); await choose('Plate to carry', 'Pool plate');
                    const off = node.step_id.includes('off_magnet');
                    await well(off ? 'LOC_TC' : 'LOC_MS', 'A1'); await click('Use selected station for placement');
                } else if (node.action === 'thermal_hold') await hold('025.00');
                else if (node.action === 'thermal_profile') { await input('Cycle count', '03'); await click('Add stage'); await input('Stage 1 Thermal bank', 'nest'); await input('Stage 1 Target temperature (°C)', '061.00'); await input('Stage 1 Hold time (seconds)', '01.00'); await input('Stage 1 Start hold timer', 'dispatch'); }
                else if (node.action === 'checkpoint') await text('Operator checkpoint message', `${node.inputs.message} — operator-authored software stage`);
                else throw new Error(`Uncovered ordinary control ${node.action}`);
            }
        };
        await author(original.steps);
        await click('Save'); const saved = structuredClone(db.m1.method);
        const checkRefs = (nodes: any[], originals: any[]) => nodes.forEach((n, i) => { if (originals[i].inputs?.expr) expect(n.inputs).toEqual(originals[i].inputs); if (n.type === 'repeat') expect(n.count).toEqual(originals[i].count); if (n.steps) checkRefs(n.steps, originals[i].steps); });
        checkRefs(saved.steps, original.steps);
        if (variant) {
            const values = saved.editor_state.run_inputs.bindings, plateId = saved.deck_plan.labware[1].id;
            expect(values.wash_count).toBe('02');
            for (const key of ['binding_on_magnet_inputs', 'wash_off_magnet_inputs', 'wash_on_magnet_inputs', 'elution_off_magnet_inputs', 'final_on_magnet_inputs']) expect(values[key]).toMatchObject({ plate_id: 'PL_POOL', labware_id: plateId });
            for (const key of ['supernatant_remove_inputs', 'wash_remove_inputs', 'eluate_collect_inputs']) expect(values[key].source).toMatchObject({ station: 'LOC_MS', location_id: 0, labware_id: plateId });
        }
        await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', 'm1'); await click('Open'); await click('Preview');
        const compiled = results.at(-1); expect(compiled.input.method).toEqual(saved);
        corpus.push({ workflow_id: example.id, ...(variant ? { variant_id: variant.id } : {}), input: compiled.input, result: compiled.result });
        if (process.env.BIOXP_WHOLE_UI_EXPORT) writeFileSync(process.env.BIOXP_WHOLE_UI_EXPORT, JSON.stringify(corpus, null, 2));
        expect(compiled.result.document, JSON.stringify(compiled.result.issues)).toBeTruthy();
        expect(compiled.result.issues.filter((issue: any) => issue.category === 'representation'), JSON.stringify(compiled.result.issues)).toHaveLength(0);
    }
    expect(corpus.map(row => row.workflow_id)).toEqual(contracts.examples.map((e: any) => e.id));
    expect(corpus.filter(row => row.variant_id === 'on_deck_magnetic')).toHaveLength(4);
    expect(requests.filter(r => /quick-runs|\/runs|execute|operator/.test(r.url))).toHaveLength(0);
}, 900000);
