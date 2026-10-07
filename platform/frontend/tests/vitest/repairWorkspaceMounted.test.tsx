import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AxiosAdapter } from 'axios';
import { webcrypto } from 'node:crypto';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { BioXpMethodPipettingEditor } from '../../src/components/BioXpMethodPipettingEditor';
import { RepairWorkspaceContext, RepairWorkspaceClassEditor, editClassSettings } from '../../src/components/repairWorkspaceClasses';
import { api } from '../../src/lib/api';
import { workspaceHydrate, workspaceSnapshot } from '../../src/lib/repairWorkspaceSnapshot';
import { switchTransferMode, transferFootprint } from '../../src/lib/repairWorkspaceTransfer';
import type { MethodValue } from '../../src/lib/bioxpMethods';
import { duplicateCanvasNode } from '../../src/lib/bioxpMethodCanvas';
import { methodInputBinding, removeMethodInputParameters } from '../../src/lib/bioxpMethodInputBinding';
import { BioXpSchemaInput } from '../../src/components/BioXpSchemaInput';
let host: HTMLDivElement, root: Root, client: QueryClient, oldAdapter: typeof api.defaults.adapter;
let requests: { url: string; method: string; body: any }[], db: any, reportRelease: (() => void) | undefined, delayReport: boolean, generation: number;
let exports: { name: string; blob: Blob }[], blob: Blob;
const A = `protocol-live-${'a'.repeat(64)}`, B = `protocol-live-${'b'.repeat(64)}`;
const base = { schema: 'bms.bioxp-method.v1', name: 'Prior populated', parameters: [], procedures: [], steps: [] };
const prior = { ...base, editor_state: { run_inputs: { bindings: { prior: '99' }, dependencies: { liquid_classes: [{ id: 'old', revision: 7 }] }, initial_state: { old: true } } } };
const incoming = { ...base, name: 'Imported', unknown: { missing: null }, editor_state: { run_inputs: { bindings: { new: '003.7500', fraction: '1/3' }, dependencies: { liquid_classes: [{ id: 'new', revision: 2, settings: { future: null } }] }, initial_state: null, extension: false } } };
const result = { document: null, issues: [], provenance: [], simulation: {} };
async function settle() { await act(async () => { await new Promise(r => setTimeout(r, 10)); }); }
async function render(element = <BioXpMethodsWorkspace generation={generation} connected={false} controlsEnabled={false} />) { await act(async () => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await settle(); }
function button(name: string) { const el = [...host.querySelectorAll('button')].find(b => b.textContent === name || b.getAttribute('aria-label') === name); expect(el, name).toBeTruthy(); return el!; }
async function click(name: string) { await act(async () => button(name).click()); await settle(); }
async function change(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function importJSON(value: unknown, pending?: Promise<string>) { const el = host.querySelector<HTMLInputElement>('[aria-label="Import JSON"]')!; Object.defineProperty(el, 'files', { configurable: true, value: [{ text: () => pending ?? Promise.resolve(JSON.stringify(value)) }] }); await act(async () => el.dispatchEvent(new Event('change', { bubbles: true }))); await settle(); }
async function exported() { await click('Export draft JSON'); return JSON.parse(await exports.at(-1)!.blob.text()); }
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); generation = 4; delayReport = false; reportRelease = undefined; requests = []; exports = []; db = { id: 'old', revision: 1, name: prior.name, method: prior };
    oldAdapter = api.defaults.adapter;
    vi.stubGlobal('Blob', globalThis.Blob);
    // jsdom's Blob may omit text; capture the exact serialized download argument.
    class TextBlob { parts: unknown[]; constructor(parts: unknown[]) { this.parts = parts; } async text() { return this.parts.join(''); } }
    vi.stubGlobal('Blob', TextBlob);
    URL.createObjectURL = vi.fn((v: Blob) => { blob = v; return 'blob:offline'; }); URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { exports.push({ name: this.download, blob }); });
    const adapter: AxiosAdapter = async config => {
        const url = config.url!, method = config.method!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ url, method, body }); let data: any;
        if (url.endsWith('/catalog')) data = { actions: [] };
        else if (url.endsWith('/schema')) data = { method: { type: 'object', properties: {} } };
        else if (url.endsWith('/examples') || url.endsWith('/starters') || url.endsWith('/presets') || url.endsWith('/liquid-classes')) data = [];
        else if (/\/(compile|check)$/.test(url)) data = result;
        else if (url.endsWith('/report')) { if (delayReport) await new Promise<void>(r => { reportRelease = r; }); data = { job_id: url.split('/').at(-2), occurrences: [], method_snapshot: { compilation: { provenance: [] } } }; }
        else if (url.endsWith('/runs')) data = [{ job_id: A, status: 'completed' }, { job_id: B, status: 'completed' }];
        else if (url.endsWith('/library') && method === 'get') data = [db];
        else if (method === 'post' && url.endsWith('/library')) data = db = { id: 'saved', revision: 1, name: body.name, method: body.method };
        else if (url.endsWith('/revisions')) data = [db];
        else if (/\/library\//.test(url)) data = db;
        else throw new Error(`Unmatched ${url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
    api.defaults.adapter = adapter; client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it.each(['Methods', 'Quick run'])('Import replaces populated %s inputs, exports raw snapshot without Save, legacy clears prior values', async tab => {
    await render(); if (tab === 'Quick run') await click(tab);
    await change('Library entry', 'old'); await click('Open'); await click('Compile');
    expect(requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body.bindings).toEqual({ prior: '99' });
    await importJSON(incoming); await click('Compile');
    const request = requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body;
    expect(request.bindings).toEqual(incoming.editor_state.run_inputs.bindings); expect(request.dependencies).toEqual(incoming.editor_state.run_inputs.dependencies); expect(request.initial_state).toBeNull();
    expect(await exported()).toEqual(incoming);
    await change('Method name', 'Unsaved edits'); const snapshot = await exported(); expect(snapshot.name).toBe('Unsaved edits'); expect(snapshot.editor_state.run_inputs).toEqual(incoming.editor_state.run_inputs);
    await importJSON(base); await click('Compile'); const empty = requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body;
    expect(empty.bindings).toEqual({}); expect(empty.dependencies).toEqual({}); expect(empty.initial_state).toEqual({}); expect(await exported()).toEqual(base);
    expect(requests.filter(r => r.method !== 'get' && !/\/(compile|check)$/.test(r.url))).toEqual([]);
});
it('Save and unsaved Export use identical raw bundle, exact Open retains omissions and nulls', async () => {
    await render(); await importJSON(incoming); const snapshot = await exported(); await click('Save'); expect(db.method).toEqual(snapshot);
    await change('Library entry', 'saved'); await click('Open'); expect(await exported()).toEqual(snapshot);
    for (const run_inputs of [{}, { initial_state: null }, { dependencies: null, bindings: null }, null]) {
        const method = { ...base, editor_state: { run_inputs } }; expect(workspaceSnapshot(method, workspaceHydrate(method).inputs)).toEqual(method);
    }
});
it.each(['edit', 'tab'])('Late file read after %s cannot overwrite newer owner', async action => {
    await render(); let resolve!: (s: string) => void; const pending = new Promise<string>(r => { resolve = r; }); await importJSON(null, pending);
    if (action === 'edit') await change('Method name', 'Newer name'); else await click('Quick run');
    await act(async () => resolve(JSON.stringify(incoming))); await settle();
    expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).not.toBe('Imported');
    if (action === 'edit') expect((await exported()).name).toBe('Newer name');
    else { await click('Methods'); expect((await exported()).name).not.toBe('Imported'); }
});
it.each(['edit', 'tab'])('Late exact Open after %s keeps the newer draft owner', async action => {
    await render(); await change('Library entry', 'old'); const adapter = api.defaults.adapter as AxiosAdapter; let release!: () => void;
    api.defaults.adapter = async config => { if (config.url!.endsWith('/library/old')) await new Promise<void>(r => { release = r; }); return adapter(config); };
    await click('Open'); expect(release).toBeTypeOf('function');
    if (action === 'edit') await change('Method name', 'Newer name'); else await click('Quick run');
    await act(async () => release()); await settle(); if (action === 'tab') await click('Methods');
    const snapshot = await exported(); expect(snapshot.name).not.toBe(prior.name); if (action === 'edit') expect(snapshot.name).toBe('Newer name');
});
it('Creation/import notice belongs to draft tab', async () => { await render(); await importJSON(incoming); expect(host.querySelector('[role="status"]')?.textContent).toContain('Imported'); await click('Liquid classes'); expect(host.textContent).not.toContain('Imported into a new unsaved'); await click('Methods'); expect(host.textContent).toContain('Imported into a new unsaved'); });
async function runs() { await render(<BioXpMethodsWorkspace generation={generation} connected controlsEnabled={false} />); await click('Runs'); }
it.each([[A, B], [B, A]])('Late report %s does not render/export under %s', async (first, second) => {
    await runs(); await click(`${first} · completed`); delayReport = true;
    await act(async () => button('Load method report').click()); await settle(); expect(reportRelease).toBeTypeOf('function');
    await click(`${second} · completed`); await act(async () => reportRelease!()); await settle(); expect(host.textContent).not.toContain(`Report: ${first}`); expect([...host.querySelectorAll('button')].some(b => b.textContent === 'Export report')).toBe(false);
    delayReport = false; await click('Load method report'); await click('Export report');
    expect(exports.at(-1)!.name).toBe(`${second}-report.json`); expect(JSON.parse(await exports.at(-1)!.blob.text()).job_id).toBe(second); expect(host.textContent).toContain(`Report: ${second}`);
    await click(`${first} · completed`); expect(host.textContent).toContain(`Report: ${first}`); await click('Export report'); expect(JSON.parse(await exports.at(-1)!.blob.text()).job_id).toBe(first);
});
it('Generation change fences late report without changing run identity', async () => { await runs(); await click(`${A} · completed`); delayReport = true; await act(async () => button('Load method report').click()); await settle(); generation++; await render(<BioXpMethodsWorkspace generation={generation} connected controlsEnabled={false} />); await act(async () => reportRelease!()); await settle(); expect(host.textContent).toContain(`Original run: ${A}`); expect(host.textContent).not.toContain(`Report: ${A}`); });
const manual = { step_id: 'same', type: 'action', action: 'transfer', inputs: { source: { station: 'LOC_RC', location_id: 2, wells: ['A1', 'A1'] }, destination: { station: 'LOC_TC', location_id: 4, wells: ['A1', 'B1'] }, channels: [0, 1, 2, 3], volume_ul: '003.7500', aspirate_speed: null, dispense_speed: '03.0', future: { x: false } } };
it.each(['manual', 'class'] as const)('Mounted %s reverse branch switching and cold snapshot retain shared/raw values', async start => {
    let current: MethodValue = start === 'manual' ? structuredClone(manual) : { ...manual, inputs: { ...manual.inputs, liquid: { liquid_class: 'pinned', context: { mode: 'single-dispense' } }, recipe: { future: null, mode: 'single' } } };
    if (start === 'class') { delete (current.inputs as any).aspirate_speed; delete (current.inputs as any).dispense_speed; }
    const original = structuredClone(current);
    function Editor() { const [node, setNode] = useState(current); return <BioXpMethodPipettingEditor node={node} onChange={v => { current = v; setNode(v); }} selection={{ station: '', wells: [] }} onSelect={() => {}} catalog={{ actions: [] }} />; }
    await render(<Editor />); await change('Transfer mode', start === 'manual' ? 'class' : 'manual'); expect((current.inputs as any).volume_ul).toBe('003.7500'); expect((current.inputs as any).source).toEqual(manual.inputs.source);
    if (start === 'manual') expect(current.inputs).not.toHaveProperty('aspirate_speed'); else expect(current.inputs).not.toHaveProperty('recipe');
    await change('Volume per channel (µL)', '01.250'); await change('Transfer mode', start);
    expect(current.inputs).toEqual({ ...(original.inputs as object), volume_ul: '01.250' });
    const cold = JSON.parse(JSON.stringify(current)); expect(switchTransferMode(switchTransferMode(cold, start === 'manual' ? 'class' : 'manual'), start).inputs).toEqual(current.inputs);
});
it('Bound Transfer mode edits retain the parameter AST and inactive fields through Save/Open', async () => {
    const reference = { expr: { version: 1, op: 'param', id: 'addition' }, retained: null };
    const method = { ...base, parameters: [{ id: 'addition', type: 'object', default: manual.inputs }], steps: [{ ...manual, inputs: reference }] };
    await render(); await importJSON(method); await click('Select step 1'); await change('Transfer mode', 'class'); await change('Volume per channel (µL)', '01.250'); await click('Save');
    expect(db.method.steps[0].inputs).toEqual(reference); expect(db.method.editor_state.run_inputs.bindings.addition.volume_ul).toBe('01.250'); expect(db.method.editor_state.run_inputs.bindings.addition).not.toHaveProperty('aspirate_speed');
    await change('Library entry', 'saved'); await click('Open'); await click('Select step 1'); await change('Transfer mode', 'manual'); const restored = await exported();
    expect(restored.steps[0].inputs).toEqual(reference); expect(restored.editor_state.run_inputs.bindings.addition).toEqual({ ...manual.inputs, volume_ul: '01.250' });
});
it('Existing structural duplicate/removal owns independent bound Transfer retention', () => {
    const switched = switchTransferMode(manual, 'class'); const reference = { expr: { version: 1, op: 'param', id: 'addition' } };
    const node = { ...switched, inputs: reference }, method = { ...base, parameters: [{ id: 'addition', type: 'object' }], steps: [node] }, bindings = { addition: switched.inputs };
    const copy = duplicateCanvasNode(method, node, bindings), next = { ...method, parameters: copy.parameters, steps: [node, copy.node] };
    const bound = methodInputBinding(next, copy.node, copy.bindings); expect(bound.id).not.toBe('addition'); expect(copy.node.step_id).not.toBe(node.step_id);
    const restored = switchTransferMode({ ...copy.node, inputs: bound.inputs }, 'manual'); expect(restored.inputs).toEqual(manual.inputs);
    (restored.inputs as any).volume_ul = 'different'; expect((bindings.addition as any).volume_ul).toBe('003.7500');
    const removed = removeMethodInputParameters({ ...next, steps: [node] }, copy.node, copy.bindings); expect(removed.bindings).toEqual(bindings); expect(removed.method.parameters).toEqual(method.parameters);
});
it('Signed four-channel footprints render every repeated pair and unknown geometry', async () => {
    const method = { deck_plan: { labware: [{ id: 's', station: 'LOC_RC', profile_id: 'different' }, { id: 'd', station: 'LOC_TC', profile_id: 'signed' }] } };
    const dependencies = { labware_profiles: [{ id: 'signed', native_addressing: { reference_channel: 0, row_increment: 2, column_increment: 0 } }] };
    await render(<RepairWorkspaceContext.Provider value={{ method, dependencies, entries: [], pin: () => {} }}><BioXpMethodPipettingEditor node={manual} onChange={() => {}} selection={{ station: '', wells: [] }} onSelect={() => {}} catalog={{}} /></RepairWorkspaceContext.Provider>);
    const source = host.querySelector('[aria-label="Source per-channel footprints"]')!; expect(source.children).toHaveLength(2); expect(source.textContent).toContain('P1 A1, P2 C1, P3 E1, P4 G1'); expect(source.textContent).toContain('003.7500 µL per channel');
    expect(transferFootprint('A1', [0, 1], { row_increment: -2, column_increment: 0, reference_channel: 0 })).toBeNull(); expect(transferFootprint('A1', [0], undefined)).toBeNull();
});
it('Only touched class fields get authored presence, including null and blank', () => { const original = { source: { immutable: [null, '03.0'] }, settings: { aspirate_speed_ul_s: '03.0', unreported: null } }; const next = editClassSettings(original, { ...original.settings, aspirate_speed_ul_s: null }); expect(next.authored_settings).toEqual({ aspirate_speed_ul_s: null }); expect(next.source).toEqual(original.source); expect(original.settings.aspirate_speed_ul_s).toBe('03.0'); expect(editClassSettings(next, { ...next.settings as object, aspirate_speed_ul_s: '' }).authored_settings).toEqual({ aspirate_speed_ul_s: '' }); });
it('Task class groups lazy-mount, retain inherited source null and record an explicit null edit', async () => {
    let current: MethodValue = { context: {}, source: { immutable: '03.0' }, settings: { aspirate_speed_ul_s: '03.0', unreported: null } };
    function Class() { const [value, set] = useState(current); return <RepairWorkspaceClassEditor value={value} onChange={v => { current = v; set(v); }} />; }
    await render(<Class />); expect(host.querySelector('[aria-label="aspirate speed ul s"]')).toBeNull();
    const disclosure = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === 'Aspiration')!;
    await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); });
    await change('aspirate speed ul s', '004.250'); expect((current.authored_settings as any).aspirate_speed_ul_s).toBe('004.250');
    await change('aspirate speed ul s variant', '1'); expect((current.authored_settings as any).aspirate_speed_ul_s).toBeNull(); expect((current.settings as any).unreported).toBeNull(); expect(current.source).toEqual({ immutable: '03.0' });
    expect(editClassSettings({ settings: { speed: null } }, { speed: null }, 'speed').authored_settings).toEqual({ speed: null }); expect(requests).toEqual([]);
});
it('Ordinary Transfer selects saved exact revision and starter content into dependencies without changing volume', async () => {
    const source = { id: 'starter', revision: 3, context: { mode: 'single-dispense' }, settings: { aspirate_speed_ul_s: '003.7500' }, source: { original: null } };
    const saved = { ...source, id: 'saved-class', revision: 2, name: 'Saved exact' };
    const fallback = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        const url = config.url!;
        if (url.includes('/liquid-classes')) { const data = url.endsWith('/revisions/2') ? { id: saved.id, revision: 2, method: saved } : [{ id: saved.id, revision: 2, name: saved.name }]; requests.push({ url, method: config.method!, body: undefined }); return { config, status: 200, statusText: 'OK', headers: {}, data }; }
        return fallback(config);
    };
    let current: MethodValue = switchTransferMode(manual, 'class'), dependencies: MethodValue[] = [];
    function Transfer() { const [node, set] = useState(current); return <RepairWorkspaceContext.Provider value={{ method: {}, dependencies: {}, entries: [source], pin: entry => { dependencies.push(structuredClone(entry)); } }}><BioXpMethodPipettingEditor node={node} onChange={v => { current = v; set(v); }} selection={{ station: '', wells: [] }} onSelect={() => {}} catalog={{}} /></RepairWorkspaceContext.Provider>; }
    await render(<Transfer />); await change('Saved liquid class', 'saved-class'); await change('Exact class revision', '2'); await click('Pin exact class revision'); expect((current.inputs as any).liquid.liquid_class).toEqual(saved); expect(dependencies).toEqual([saved]);
    await change('Class and exact revision', '0'); expect((current.inputs as any).liquid.liquid_class).toEqual(source); expect(dependencies[1]).toEqual(source); expect((current.inputs as any).volume_ul).toBe('003.7500'); expect(requests.every(r => r.method === 'get')).toBe(true);
});
it('Report read failure retains last-good identity and its export body', async () => {
    await runs(); await click(`${A} · completed`); await click('Load method report'); const adapter = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => { if (config.url!.endsWith('/report')) throw new Error('offline read failed'); return adapter(config); };
    await click('Load method report'); expect(host.textContent).toContain('offline read failed'); expect(host.textContent).toContain(`Report: ${A}`); await click('Export report'); expect(exports.at(-1)!.name).toBe(`${A}-report.json`); expect(JSON.parse(await exports.at(-1)!.blob.text()).job_id).toBe(A);
});
it('Generic object union preserves shared/unknown fields and restores branch values on return', async () => {
    let current: unknown; function Union() { const [v, set] = useState({ shared: '003.7500', a: null, future: false }); current = v; return <BioXpSchemaInput label="Union" rawDraft schema={{ oneOf: [{ type: 'object', properties: { shared: { type: 'string' }, a: {} } }, { type: 'object', properties: { shared: { type: 'string' }, b: {} } }] }} value={v} onChange={x => set(x as any)} fallback={() => null} />; }
    await render(<Union />); await change('Union variant', '1'); expect(current).toEqual({ shared: '003.7500', future: false }); await change('Union variant', '0'); expect(current).toEqual({ shared: '003.7500', future: false, a: null });
});
