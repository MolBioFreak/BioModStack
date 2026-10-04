import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, type AxiosAdapter } from 'axios';
import { webcrypto, createHash } from 'node:crypto';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { gunzipSync } from 'node:zlib';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { api } from '../../src/lib/api';
import { previewActionDestination } from '../../src/lib/bioxpWorkflowPlan';
import { definiteMethodRefusal } from '../../src/lib/bioxpMethods';

// Transport-only fixture: real React, query owner, Axios serialization, editors and monitor.
const exportedRequests: unknown[] = [];
let host: HTMLDivElement, root: Root, client: QueryClient, oldAdapter: typeof api.defaults.adapter;
let requests: { method: string; url: string; body: any; params: any }[], db: Record<string, any>, records: Record<string, any>;
let failure: 'none' | 'timeout' | 'refused' | 'conflict', generation: number, visible: boolean;
let phase: string, terminal: boolean, readFailure: boolean, saveReadFailure: boolean;
let deferSave: null | ((done: () => void) => void);
const sourceMethod = { schema: 'bms.bioxp-method.v1', name: 'PCR author draft', parameters: [{ id: 'volume', type: 'number', unit: 'uL' }], procedures: [], steps: [{ step_id: 'move-original', type: 'action', action: 'move', inputs: { location_id: 2, well: 'a1', height_steps: '01.20', future: null } }], unknown: { empty: '', explicit: null, flag: false } };
const scalar = { type: 'string' };
const fieldObject = (properties: Record<string, unknown>) => ({ type: 'object', properties });
const listOf = (items: unknown) => ({ type: 'array', items });
const schema = fieldObject({
    name: scalar,
    parameters: listOf(fieldObject({ id: scalar, type: { enum: ['number', 'integer', 'boolean', 'string', 'array', 'object'] }, unit: scalar })),
    steps: listOf(fieldObject({ step_id: scalar, type: { enum: ['action', 'repeat', 'if', 'call', 'group'] }, count: { type: 'number' }, condition: { type: 'boolean' }, procedure_id: scalar })),
    procedures: listOf(fieldObject({ id: scalar, parameters: listOf({}), steps: listOf({}) })),
    deck_plan: fieldObject({ labware: listOf(fieldObject({ id: scalar, station: scalar })) }),
    tip_policy: fieldObject({ mode: { enum: ['manual', 'per_transfer', 'per_source', 'per_step'] } }),
});
const catalog = { actions: [
    { id: 'move', label: 'Move', input_schema: fieldObject({ location_id: { type: 'integer' }, well: scalar, height_steps: { type: 'number' }, channels: listOf({ type: 'integer' }) }) },
    { id: 'thermal_profile', label: 'Thermal profile', input_schema: fieldObject({ segments: listOf(fieldObject({ temperature: { type: 'number' }, duration: { type: 'number' } })) }) },
] };
const provenance = [{ occurrence_id: 'loop/0/move-original', step_id: 'move-original', path: '/steps/0', call_path: ['procedure:aliquot'], loop_path: [{ index: 0 }], native_action_ids: ['a1'] }];
const compiled = { document: { stages: [{ actions: [{ action_id: 'a1', kind: 'pipette_position', params: { operation: 'move', location_id: 2, well: 'a1', height_steps: 1.2 } }] }] }, issues: [], digest: 'test-digest', provenance, resolved: { parameters: {}, occurrences: [] }, water_substitutions: [{ field: 'aspiration_speed_ul_s', requested: 'unspecified', resolved: '50', water_revision: 1, emitted: 'unknown' }], simulation: { duration: null, volume: 'unknown' } };
function job(id: string) { return { job_id: id, status: terminal ? 'completed' : 'running', command: { command_id: id, status: terminal ? 'ambiguous' : 'dispatched', terminal, ownership_generation: 7, state_version: 3 }, execution: { dry_run: false, runtime_state: { workflow: { command_id: id, phase, source_occurrence_id: 'loop/0/move-original', requested_control: null } } } }; }
function fail(config: any, status: number, detail: unknown): never { throw new AxiosError('test transport error', 'ERR_BAD_RESPONSE', config, undefined, { config, status, statusText: 'error', headers: {}, data: { detail } }); }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); }
async function render() { await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={generation} connected controlsEnabled visible={visible} /></QueryClientProvider>)); await settle(); }
async function mount() { root = createRoot(host); await render(); }
function button(name: string) { const node = [...host.querySelectorAll('button')].find(n => n.textContent === name || n.getAttribute('aria-label') === name); expect(node, name).toBeTruthy(); return node!; }
async function click(name: string) { await act(async () => button(name).click()); await settle(); }
async function input(label: string, value: string) { const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function openOriginal() { await input('Library entry', 'm1'); await click('Open'); }
async function acknowledge() { const el = [...host.querySelectorAll('label')].find(n => n.textContent === 'Acknowledge live robot execution')!.querySelector('input')!; await act(async () => el.click()); }
const submits = () => requests.filter(r => r.method === 'post' && /\/runs$|quick-runs$/.test(r.url));
const reads = () => requests.filter(r => r.method === 'get' && /\/runs\/protocol-live-/.test(r.url) && !r.url.endsWith('/report'));
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); oldAdapter = api.defaults.adapter;
    generation = 9; visible = true; failure = 'none'; phase = 'executing'; terminal = false; readFailure = false; saveReadFailure = false; deferSave = null; requests = []; records = {};
    db = { m1: { id: 'm1', revision: 1, name: sourceMethod.name, method: structuredClone(sourceMethod) } };
    const adapter: AxiosAdapter = async config => {
        const url = config.url!, method = config.method!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ url, method, body, params: config.params }); let data: any;
        if (url.endsWith('/starters')) data = [];
        else if (url.endsWith('/catalog')) data = catalog;
        else if (url.endsWith('/schema')) data = { method: schema, requests: {}, results: {}, openapi: '/openapi.json' };
        else if (url.endsWith('/examples')) data = [{ name: 'PCR', method: sourceMethod }];
        else if (/\/(check|compile)$/.test(url)) data = compiled;
        else if (url.endsWith('/report')) data = { method_snapshot: { method: sourceMethod, compilation: compiled }, occurrences: [{ ...provenance[0], status: 'failed', effects: [{ action_id: 'a1', status: 'completed' }] }], water_substitutions: compiled.water_substitutions };
        else if (url.endsWith('/clone')) data = { method: sourceMethod, bindings: {}, dependencies: {}, submitted: false };
        else if (url.endsWith('/recovery-draft')) data = { method: sourceMethod, bindings: {}, dependencies: {}, initial_state: body.initial_state, recovery: { original_job_id: url.split('/').at(-2), occurrence: body.occurrence, automatic_setup: [], excluded_actions: [], submitted: false, strategy: 'whole_original_draft_for_explicit_editing' } };
        else if (url.endsWith('/control')) data = { accepted: true, reached: false, command_id: body.command_id, control_command_id: 'control-1' };
        else if (method === 'post' && /\/runs$|quick-runs$/.test(url)) {
            const id = `protocol-live-${createHash('sha256').update(body.idempotency_key).digest('hex')}`;
            expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).jobId).toBe(id);
            if (failure === 'refused') fail(config, 409, { delivery: 'not_submitted', code: 'method_submission_error' });
            if (failure === 'conflict') fail(config, 409, { error: 'idempotency_conflict' });
            records[id] = job(id); if (failure === 'timeout') throw new AxiosError('response lost', 'ECONNABORTED', config);
            data = records[id];
        } else if (/\/runs\/protocol-live-/.test(url)) { if (readFailure) fail(config, 404, 'Job not available'); data = job(url.split('/').at(-1)!); }
        else if (url.endsWith('/runs')) data = { rows: Object.values(records) };
        else if (/\/diff$/.test(url)) data = { from_revision: config.params.from_revision, to_revision: config.params.to_revision, raw: true };
        else if (/\/revisions\/\d+$/.test(url)) { if (saveReadFailure) fail(config, 503, 'Readback unavailable'); data = db[url.split('/').at(-3)!]; }
        else if (url.endsWith('/revisions')) data = [db[url.split('/').at(-2)!]];
        else if (/\/(library|liquid-classes|presets)$/.test(url) && method === 'get') data = url.endsWith('/library') ? Object.values(db) : [];
        else if (/\/(library|liquid-classes|presets)$/.test(url) && method === 'post') { const id = `m${Object.keys(db).length + 1}`; data = db[id] = { id, revision: 1, name: body.name, method: body.method }; if (deferSave) await new Promise<void>(resolve => deferSave!(resolve)); }
        else if (method === 'put') { const id = url.split('/').at(-1)!; expect(body.expected_base_revision).toBe(db[id].revision); data = db[id] = { id, revision: db[id].revision + 1, name: body.name, method: body.method }; }
        else if (/\/library\/[^/]+$/.test(url)) data = db[url.split('/').at(-1)!];
        else throw new Error(`Unmatched offline transport ${method} ${url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
    api.defaults.adapter = adapter;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host);
});
afterAll(() => { if (process.env.BIOXP_METHOD_UI_EXPORT) writeFileSync(process.env.BIOXP_METHOD_UI_EXPORT, JSON.stringify({ transport_fixture_only: true, requests: exportedRequests }, null, 2)); });
afterEach(async () => { exportedRequests.push(...requests); await act(async () => root?.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

it.runIf(!!process.env.BIOXP_METHOD_MODEL_CONTRACT)('mounts committed discovery and all scientific skeletons through real Axios', async () => {
    const contracts = JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT!, 'utf8'));
    const transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        const part = config.url?.split('/').at(-1);
        if (['catalog', 'schema', 'examples'].includes(part ?? '')) return { config, status: 200, statusText: 'OK', headers: {}, data: contracts[part!] };
        return transport(config);
    };
    await mount();
    for (const [index, example] of contracts.examples.entries()) {
        await input('Scientific example', String(index));
        expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).toBe(example.method.name);
        expect(host.textContent).toContain(example.method.steps[0].label ?? example.method.steps[0].type);
    }
    expect(submits()).toHaveLength(0);
});
it.runIf(!!process.env.BIOXP_METHOD_MODEL_CONTRACT)('loads actual bound companion bindings/dependencies and compiles every scientific fixture through the real compiler', async () => {
    const contracts = JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT!, 'utf8'));
    const transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        const part = config.url?.split('/').at(-1);
        if (['catalog', 'schema', 'examples'].includes(part ?? '')) return { config, status: 200, statusText: 'OK', headers: {}, data: contracts[part!] };
        if (part === 'compile') {
            const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
            const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', 'import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: process.env.BIOXP_METHOD_API_DIR ?? '../api', encoding: 'utf8', input: JSON.stringify(body) });
            expect(produced.status, produced.stderr).toBe(0); const data = JSON.parse(produced.stdout);
            expect(data.document, JSON.stringify(data.issues)).toBeTruthy();
            requests.push({ method: config.method!, url: config.url!, body, params: config.params });
            return { config, status: 200, statusText: 'OK', headers: {}, data };
        }
        return transport(config);
    };
    await mount();
    for (const [index, example] of contracts.examples.entries()) {
        expect(example.bound_fixture).toBeTruthy();
        await input('Bound software fixture', String(index)); await click('Compile');
        expect(requests.at(-1)?.body).toMatchObject({ method: example.bound_fixture.method, bindings: example.bound_fixture.bindings, dependencies: example.bound_fixture.dependencies });
        expect(host.textContent).toContain(example.bound_fixture.fixture_provenance);
    }
    expect(submits()).toHaveLength(0);
}, 30000);
it.runIf(!!process.env.BIOXP_METHOD_MODEL_CONTRACT)('authors a generic numeric parameter through actual discovery, raw Save/Open and the supported literal compiler contract', async () => {
    const contracts = JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT!, 'utf8'));
    const results: any[] = [], transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        const part = config.url?.split('/').at(-1);
        if (['catalog', 'schema', 'examples'].includes(part ?? '')) return { config, status: 200, statusText: 'OK', headers: {}, data: contracts[part!] };
        if (part === 'compile') {
            const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
            const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', 'import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: process.env.BIOXP_METHOD_API_DIR ?? '../api', encoding: 'utf8', input: JSON.stringify(body) });
            expect(produced.status, produced.stderr).toBe(0); const data = JSON.parse(produced.stdout); results.push(data);
            requests.push({ method: config.method!, url: config.url!, body, params: config.params });
            return { config, status: 200, statusText: 'OK', headers: {}, data };
        }
        return transport(config);
    };
    await mount(); await input('Method name', 'Numeric contract'); await click('Add Parameters item');
    await input('Parameters[0].id presence', 'value'); await input('Parameters[0].id', 'precise');
    await input('Parameters[0].type presence', 'value'); await input('Parameters[0].type', '0');
    await input('Parameters[0].default presence', 'value'); await input('Parameters[0].default type', 'number');
    await input('Action palette', 'wait'); await click('Add step');
    await input('Wait time (seconds)', '001.2500');
    await click('Save'); await click('Compile');
    expect(results.at(-1).document).toBeNull(); expect(results.at(-1).issues.length).toBeGreaterThan(0);
    await click('New'); await input('Library entry', 'm2'); await click('Open');
    expect((host.querySelector('[aria-label="Parameters[0].default type"]') as HTMLSelectElement).value).toBe('number');
    await input('Parameters[0].default', '9007199254740993.000100'); await click('Save');
    await click('New'); await input('Library entry', 'm2'); await click('Open');
    expect((host.querySelector('[aria-label="Parameters[0].default"]') as HTMLInputElement).value).toBe('9007199254740993.000100');
    await click('Compile');
    expect(results.at(-1).document, JSON.stringify(results.at(-1).issues)).toBeTruthy();
    expect(results.at(-1).resolved.parameters.precise).toBe('9007199254740993.0001');
    expect(db.m2.method.parameters[0].default).toEqual({ expr: { version: 1, op: 'literal', type: 'number', value: '9007199254740993.000100' } });
    expect(submits()).toHaveLength(0);
}, 30000);
it.runIf(!!process.env.BIOXP_NATIVE_METHOD_PRODUCER)('renders the real native ASGI/executor/SQLite thermal producer without new mutation or outcome rewriting', async () => {
    const producer = JSON.parse(readFileSync(process.env.BIOXP_NATIVE_METHOD_PRODUCER!, 'utf8'));
    const native = producer.execution.runtime_state.action_results;
    expect(native.length).toBeGreaterThan(0);
    localStorage.setItem('bms.bioxp.method-run.v1', JSON.stringify({ jobId: producer.job_id, key: producer.command.idempotency_key, generation, snapshot: { native_document: producer.protocol.document } }));
    const transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        if (config.url === `/api/bioxp/methods/runs/${producer.job_id}`) { requests.push({ method: config.method!, url: config.url, body: undefined, params: config.params }); return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(producer) }; }
        return transport(config);
    };
    await mount();
    expect(host.textContent).toContain(`Robot status: ${producer.command.status}`);
    const progress = host.querySelector('[aria-label="Occurrence outcomes"]')!;
    expect(progress.querySelectorAll('tbody tr')).toHaveLength(native.length);
    for (const row of native) expect(progress.textContent).toContain(row.source_occurrence_id);
    expect(submits()).toHaveLength(0); expect(button('Pause workflow').disabled).toBe(true);
    expect(requests.filter(r => r.method !== 'get')).toHaveLength(0);
});
it.runIf(!!process.env.BIOXP_NATIVE_METHOD_EXPORTS).each(['cavro-device-pause_for_operator.json', 'cavro-device-stop.json', 'cavro-missing-stop.json'])('renders actual native partial/held producer %s without retry/Continue or outcome rewriting', async filename => {
    const exported = JSON.parse(readFileSync(`${process.env.BIOXP_NATIVE_METHOD_EXPORTS}/${filename}`, 'utf8'));
    const producer = exported.held ?? exported.result, original = JSON.stringify(producer);
    localStorage.setItem('bms.bioxp.method-run.v1', JSON.stringify({ jobId: producer.job_id, key: producer.command.idempotency_key, generation, snapshot: { native_document: producer.protocol.document } }));
    const transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => {
        if (config.url === `/api/bioxp/methods/runs/${producer.job_id}`) {
            requests.push({ method: config.method!, url: config.url, body: undefined, params: config.params });
            if (readFailure) fail(config, 503, 'observation unavailable');
            return { config, status: 200, statusText: 'OK', headers: {}, data: producer };
        }
        return transport(config);
    };
    await mount();
    expect(host.textContent).toContain(`Robot status: ${producer.command.status}`);
    const progress = host.querySelector('[aria-label="Occurrence outcomes"]')!;
    expect(progress.textContent).toContain('partial_effects');
    expect(progress.textContent).toContain('sourceCavroApplication');
    expect([...host.querySelectorAll('button')].some(b => /Continue|Retry|Skip/.test(b.textContent ?? ''))).toBe(false);
    if (exported.held) {
        expect(host.textContent).toContain('Held reason: source_error_hold');
        expect(button('Request safe-state stop').disabled).toBe(false);
        readFailure = true; await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
        expect(button('Request safe-state stop').disabled).toBe(false);
        await click('Request safe-state stop');
        expect(requests.filter(r => r.method !== 'get')).toHaveLength(1);
        expect(requests.find(r => r.url.endsWith('/control'))!.body).toMatchObject({ action: 'safe_stop', command_id: producer.command.command_id, expected_ownership_generation: producer.command.ownership_generation });
    } else expect(button('Request safe-state stop').disabled).toBe(true);
    expect(JSON.stringify(producer)).toBe(original); expect(submits()).toHaveLength(0);
});
it('opens raw exact revision, edits typed scientific value, preserves unknown/null/precision and saves a revision without robot mutation', async () => {
    await mount(); await openOriginal();
    await input('Inputs move-original.height_steps', '002.3400'); await click('Save');
    const sent = requests.find(r => r.method === 'put')!;
    expect(sent.body.method.steps[0].inputs).toEqual({ location_id: 2, well: 'a1', height_steps: '002.3400', future: null });
    expect(sent.body.method.unknown).toEqual(sourceMethod.unknown); expect(sent.body.expected_base_revision).toBe(1);
    expect(host.textContent).toContain('Saved exact revision 2'); expect(submits()).toHaveLength(0);
    expect(requests.every(r => r.url.startsWith('/api/bioxp/methods/'))).toBe(true);
});
it('provides structured nodes and expression AST, stable reorder and new duplicate identities', async () => {
    await mount(); await openOriginal(); await click('Use expression for Inputs move-original.height_steps');
    await input('Inputs move-original.height_steps operator', 'param'); await input('Inputs move-original.height_steps reference', 'volume');
    await click('Duplicate step'); await click('Save');
    const steps = requests.find(r => r.method === 'put')!.body.method.steps;
    expect(steps).toHaveLength(2); expect(steps[0].step_id).not.toBe(steps[1].step_id);
    expect(steps[0].inputs.height_steps).toMatchObject({ expr: { version: 1, op: 'param', id: 'volume' } });
});
it('saved Run freezes exact saved revision, bindings and generation despite uncommitted editor and class changes', async () => {
    await mount(); await openOriginal(); await input('Method name', 'Next edited draft'); await acknowledge();
    await click('Run saved revision'); await click('Liquid classes'); await input('Method name', 'New liquid');
    expect(submits()).toHaveLength(1); expect(submits()[0].body).toEqual({ revision: 1, bindings: {}, dependencies: {}, initial_state: {}, idempotency_key: expect.any(String), expected_generation: 9, acknowledge_live: true });
    expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).snapshot.method.name).toBe(sourceMethod.name);
    expect(host.textContent).not.toContain('Continue (try again)');
    await click('Methods'); expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).toBe('Next edited draft');
});
it('quick run retains frozen next-draft snapshot and reload observes only the original after accepted response loss', async () => {
    failure = 'timeout'; await mount(); await click('Quick run'); await input('Method name', 'Unsaved quick'); await acknowledge(); await click('Run immutable quick snapshot'); await click('New');
    expect(submits()).toHaveLength(1); expect(submits()[0].body.method.name).toBe('Unsaved quick');
    const original = JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!);
    await act(async () => root.unmount()); client.clear(); generation = 10; await mount();
    expect(submits()).toHaveLength(1); expect(reads().at(-1)?.url).toBe(`/api/bioxp/methods/runs/${original.jobId}`);
    expect(reads().at(-1)?.params.expected_connection_generation).toBe(10);
});
it.each(['refused', 'conflict', 'timeout'] as const)('distinguishes %s from uncertain delivery; never retries POST', async outcome => {
    failure = outcome; readFailure = true; await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision');
    expect(submits()).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).refused === true).toBe(outcome === 'refused');
    expect(host.textContent?.includes('nothing started for this submission')).toBe(outcome === 'refused');
    if (outcome === 'refused') { const count = reads().length; await render(); expect(reads()).toHaveLength(count); }
});
it('edits complete source-class field groups without defaults, saving raw explicit/null/omitted values as a new class', async () => {
    await mount(); await click('Liquid classes'); await input('Method name', 'Authored test class');
    await input('Liquid class.settings presence', 'value');
    await input('Liquid class.settings.aspirate_speed_ul_s presence', 'value');
    await input('Liquid class.settings.aspirate_speed_ul_s', '050.000');
    await input('Liquid class.settings.dispense_segments presence', 'value');
    await click('Add Liquid class.settings.dispense_segments item');
    await input('Liquid class.settings.dispense_segments[0].volume_ul presence', 'value');
    await input('Liquid class.settings.dispense_segments[0].volume_ul', '010.2500');
    await input('Liquid class.settings.aspiration_delay_ms presence', 'value');
    await input('Liquid class.settings.aspiration_delay_ms variant', '1');
    await click('Save');
    const payload = requests.find(r => r.url.endsWith('/liquid-classes') && r.method === 'post')!.body.method;
    expect(payload.settings).toEqual({ aspirate_speed_ul_s: '050.000', dispense_segments: [{ volume_ul: '010.2500' }], aspiration_delay_ms: null });
    expect(submits()).toHaveLength(0); expect(host.textContent).toContain('Saved exact revision 1');
});
it('clones the original immutable snapshot through facade without modifying the run', async () => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision'); const snapshot = localStorage.getItem('bms.bioxp.method-run.v1');
    await click('Clone original method'); expect(submits()).toHaveLength(1); expect(localStorage.getItem('bms.bioxp.method-run.v1')).toBe(snapshot);
    expect(host.textContent).toContain('Original immutable method cloned as an unsaved draft');
    expect(button('Run saved revision').disabled).toBe(true);
});
it.each(['Methods', 'Quick run'])('%s retains accepted create through readback failure and retries PUT rather than duplicate POST', async tab => {
    saveReadFailure = true; await mount(); await click(tab); await input('Method name', 'Incomplete');
    const save = tab === 'Quick run' ? 'Save as method' : 'Save';
    await click(save);
    if (tab === 'Methods') expect(button('Run saved revision').disabled).toBe(true);
    saveReadFailure = false; await click(save);
    expect(requests.filter(r => r.url.endsWith('/library') && r.method === 'post')).toHaveLength(1);
    expect(requests.filter(r => r.method === 'put')).toHaveLength(1);
    expect(host.textContent).toContain('Saved exact revision 2');
});
it('Quick Save retry retains accepted identity independently of Methods and Clear creates a new target', async () => {
    await mount(); await openOriginal(); await click('Quick run');
    await input('Method name', 'Quick incomplete'); saveReadFailure = true;
    await click('Save as method');
    const create = requests.filter(r => r.url.endsWith('/library') && r.method === 'post');
    expect(create).toHaveLength(1); expect(db.m2.method.name).toBe('Quick incomplete');
    await click('Methods'); await click('Save');
    expect(requests.filter(r => r.method === 'put').at(-1)?.url).toMatch(/library\/m1$/);
    await click('Quick run'); saveReadFailure = false; await input('Method name', 'Quick edited');
    await click('Save as method');
    expect(requests.filter(r => r.url.endsWith('/library') && r.method === 'post')).toHaveLength(1);
    expect(requests.filter(r => r.method === 'put').at(-1)).toMatchObject({ url: '/api/bioxp/methods/library/m2', body: { expected_base_revision: 1 } });
    expect(host.textContent).toContain('Saved exact revision 2');
    expect(db.m1.method.name).toBe(sourceMethod.name); expect(db.m2.method.name).toBe('Quick edited');
    await click('Save as method'); expect(db.m2.revision).toBe(3);
    await click('Clear next draft'); await input('Method name', 'Next Quick'); await click('Save as method');
    expect(requests.filter(r => r.url.endsWith('/library') && r.method === 'post')).toHaveLength(2);
    expect(db.m3.method.name).toBe('Next Quick'); expect(submits()).toHaveLength(0);
});
it('Quick Open, duplicate and late Quick Save do not replace an unrelated Methods saved snapshot', async () => {
    db.m2 = { id: 'm2', revision: 1, name: 'Other', method: { ...sourceMethod, name: 'Other' } };
    await mount(); await openOriginal(); await click('Quick run'); await input('Library entry', 'm2'); await click('Open');
    await click('Duplicate');
    let finish!: () => void; deferSave = done => { finish = done; };
    await act(async () => button('Save as method').click()); await settle();
    await input('Method name', 'Newer Quick'); await act(async () => finish()); await settle();
    expect(host.textContent).toContain('newer edits remain unsaved');
    await click('Methods');
    expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).toBe(sourceMethod.name);
    await acknowledge(); await click('Run saved revision');
    expect(submits()[0].url).toBe('/api/bioxp/methods/library/m1/runs');
    expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).snapshot.method.name).toBe(sourceMethod.name);
});
it('retains newer edits while Save settles and exposes only saved readback', async () => {
    let finish!: () => void; deferSave = done => { finish = done; };
    await mount(); await input('Method name', 'Original save'); await act(async () => button('Save').click()); await settle();
    await input('Method name', 'Newer edit'); await act(async () => finish()); await settle();
    expect(host.textContent).toContain('newer edits remain unsaved'); expect((host.querySelector('[aria-label="Method name"]') as HTMLInputElement).value).toBe('Newer edit');
    await acknowledge(); await click('Run saved revision'); expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).snapshot.method.name).toBe('Original save');
});
it('uses existing same-job controls and exact occurrence recovery with no replay or generated setup', async () => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision'); await click('Pause workflow');
    const control = requests.find(r => r.url.endsWith('/control'))!; expect(control.body).toMatchObject({ action: 'pause', mode: 'ordinary', expected_ownership_generation: 7, expected_connection_generation: 9 });
    await click('Load method report'); await input('Exact recovery occurrence', 'loop/0/move-original'); await click('Create recovery draft');
    expect(requests.find(r => r.url.endsWith('/recovery-draft'))!.body.occurrence).toEqual(provenance[0]); expect(submits()).toHaveLength(1);
    expect(host.textContent).toContain('Recovery is an unsaved editable draft');
});
it.runIf(!!process.env.BIOXP_NATIVE_BMS_SNAPSHOTS).each([
    ['bms-46-aspirate.json.gz', 'result'], ['bms-46-dispense.json.gz', 'result'],
    ['bms-0-error-hold.json.gz', 'held'], ['bms-0-error-hold.json.gz', 'result'],
])('receives original BMS native snapshot %s/%s and cold reopens explicit recovery through actual ASGI', async (filename, state) => {
    const original = JSON.parse(gunzipSync(readFileSync(`${process.env.BIOXP_NATIVE_BMS_SNAPSHOTS}/${filename}`)).toString())[state];
    const unchanged = JSON.stringify(original), transport = api.defaults.adapter as AxiosAdapter;
    const project = (path: string, body?: unknown) => {
        const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['tests/fixtures/bioxpNativeMethodProjection.py'], { encoding: 'utf8', input: JSON.stringify({ filename, state, path, body }) });
        expect(result.status, result.stderr).toBe(0); return JSON.parse(result.stdout);
    };
    const report = project('report');
    const metadata = original.protocol.document.metadata;
    const source = metadata.bms_method_run ?? metadata.bms_method;
    expect(source.method).toBeTruthy();
    expect(report.snapshot_available, 'API must losslessly project the original native BMS metadata variant').toBe(true);
    expect(report.method_snapshot).toMatchObject({ method: source.method, bindings: source.bindings, dependencies: source.dependencies });
    const snapshot = report.method_snapshot;
    records[original.job_id] = original;
    api.defaults.adapter = async config => {
        const url = config.url!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        if (url.includes(`/runs/${original.job_id}`)) {
            requests.push({ method: config.method!, url, body, params: config.params });
            const data = url.endsWith('/report') ? report : url.endsWith('/recovery-draft') ? project('recovery-draft', body) : original;
            return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
        }
        return transport(config);
    };
    await mount(); await click('Runs'); await click(`${original.job_id} · ${original.status}`); await click('Load method report');
    const selected = snapshot.compilation.provenance[0];
    await input('Exact recovery occurrence', selected.occurrence_id);
    await input('Recovery native action', selected.native_action_ids[0]);
    await input('Recovery initial assumptions type', 'object');
    await input('Recovery initial assumptions new field', 'custody'); await click('Add Recovery initial assumptions field');
    await input('Recovery initial assumptions.custody type', 'null');
    await click('Create recovery draft');
    const sent = requests.find(r => r.url.endsWith('/recovery-draft'))!.body;
    expect(sent).toEqual({ occurrence: { ...selected, native_action_id: selected.native_action_ids[0] }, initial_state: { custody: null } });
    await input('Method name', `Recovery ${filename} ${state}`); await click('Save');
    const saved = Object.values(db).find(r => r.name === `Recovery ${filename} ${state}`)!;
    expect(saved.method.steps).toEqual(snapshot.method.steps);
    expect(saved.method.editor_state.run_inputs).toEqual({ bindings: snapshot.bindings ?? {}, dependencies: snapshot.dependencies ?? {}, initial_state: { custody: null } });
    expect(saved.method.editor_state.recovery_linkage).toMatchObject({ original_job_id: original.job_id, occurrence: sent.occurrence, automatic_setup: [], excluded_actions: [], submitted: false });
    await act(async () => root.unmount()); client.clear(); await mount();
    await input('Library entry', saved.id); await click('Open'); await click('Compile');
    const reopened = requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body;
    expect(reopened.initial_state).toEqual({ custody: null });
    expect(reopened.bindings).toEqual(snapshot.bindings ?? {}); expect(reopened.dependencies).toEqual(snapshot.dependencies ?? {});
    expect(reopened.method).toEqual(saved.method);
    expect(JSON.stringify(original)).toBe(unchanged); expect(submits()).toHaveLength(0);
    expect(requests.filter(r => /\/(control|review)$/.test(r.url))).toHaveLength(0);
}, 30000);
it('cold reopens edited recovery assumptions and original bindings without replay', async () => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision');
    await click('Load method report'); await input('Exact recovery occurrence', 'loop/0/move-original');
    await input('Recovery initial assumptions type', 'object');
    await input('Recovery initial assumptions new field', 'custody'); await click('Add Recovery initial assumptions field');
    await input('Recovery initial assumptions.custody', 'operator reviewed unknown');
    await click('Create recovery draft'); await input('Method name', 'Edited recovery');
    await click('Save');
    const saved = Object.values(db).find(r => r.name === 'Edited recovery')!;
    expect(saved).toBeTruthy();
    await act(async () => root.unmount()); client.clear(); localStorage.clear(); await mount();
    await input('Library entry', saved.id); await click('Open');
    expect((host.querySelector('[aria-label="Initial assumptions.custody"]') as HTMLInputElement)?.value).toBe('operator reviewed unknown');
    expect(saved.method.editor_state.recovery_linkage.occurrence).toEqual(provenance[0]);
    expect(submits()).toHaveLength(1);
});
it.each(['omitted', 'null', 'object'])('recovery %s assumptions are independent from next-draft edits and preserve exact native action', async kind => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision');
    await click('Load method report'); await input('Exact recovery occurrence', 'loop/0/move-original');
    await input('Recovery native action', 'a1');
    await input('Initial assumptions new field', 'next_only'); await click('Add Initial assumptions field');
    await input('Initial assumptions.next_only', 'not original recovery state');
    if (kind !== 'omitted') await input('Recovery initial assumptions type', kind);
    if (kind === 'object') {
        await input('Recovery initial assumptions new field', 'tips'); await click('Add Recovery initial assumptions field');
        await input('Recovery initial assumptions.tips type', 'null');
    }
    await click('Create recovery draft');
    const body = requests.find(r => r.url.endsWith('/recovery-draft'))!.body;
    expect(body.occurrence).toEqual({ ...provenance[0], native_action_id: 'a1' });
    expect(Object.hasOwn(body, 'initial_state')).toBe(kind !== 'omitted');
    if (kind !== 'omitted') expect(body.initial_state).toEqual(kind === 'null' ? null : { tips: null });
    await click('Compile');
    const check = requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body;
    expect(Object.hasOwn(check, 'initial_state')).toBe(kind !== 'omitted');
    if (kind !== 'omitted') expect(check.initial_state).toEqual(body.initial_state);
    await input('Method name', `Recovered ${kind}`); await click('Save');
    const saved = Object.values(db).find(r => r.name === `Recovered ${kind}`)!;
    await act(async () => root.unmount()); client.clear(); localStorage.clear(); await mount();
    await input('Library entry', saved.id); await click('Open'); await click('Compile');
    const cold = requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body;
    expect(Object.hasOwn(cold, 'initial_state')).toBe(kind !== 'omitted');
    if (kind !== 'omitted') expect(cold.initial_state).toEqual(body.initial_state);
    expect(submits()).toHaveLength(1);
});
it('compiles separately, presents Water without new acknowledgement and derives map from emitted station', async () => {
    await mount(); await openOriginal(); await click('Compile');
    expect(host.textContent).toContain('Before Run: matching Water substitutions'); expect(host.textContent).toContain('Station: LOC_TC');
    expect(host.querySelector('[aria-label="Compiled occurrence preview"]')).toBeTruthy(); expect(submits()).toHaveLength(0);
    expect([...host.querySelectorAll('label')].filter(n => /Acknowledge live robot execution/.test(n.textContent ?? ''))).toHaveLength(1);
});
it('retains active reconciliation across subtabs and settles ambiguous terminal observation without disabling warm-error controls', async () => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision');
    readFailure = true; await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
    expect(button('Pause workflow').disabled).toBe(false);
    expect(button('Request safe-state stop').disabled).toBe(false);
    readFailure = false; phase = 'reconciling'; await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
    expect(host.textContent).toContain('Phase: reconciling');
    visible = false; await render(); const before = reads().length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)); });
    expect(reads().length).toBeGreaterThan(before);
    visible = true; terminal = true; phase = 'terminal'; await render();
    await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
    const settled = reads().length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)); });
    expect(reads()).toHaveLength(settled); expect(button('Pause workflow').disabled).toBe(true);
}, 10000);
it('does not interpret HTTP status or absent ACK as definitive refusal and displays non-XY without coordinates', () => {
    expect(definiteMethodRefusal({ response: { status: 422, data: { detail: 'invalid' } } })).toBe(false);
    expect(previewActionDestination({ kind: 'thermal', params: {}, station: null, well: null })).toEqual({ station: null, well: null });
    expect(previewActionDestination({ kind: 'pipette_position', params: { location_id: 0, well: 'a1' }, station: null, well: null })).toEqual({ station: 'LOC_MS', well: 'A1' });
});

it('shows live progress paths carried in native action metadata', async () => {
    const { renderToStaticMarkup } = await import('react-dom/server');
    const { BioXpMethodProgress } = await import('../../src/components/BioXpMethodPreview');
    const html = renderToStaticMarkup(<BioXpMethodProgress report={{ action_results: [{ status: 'failed', source_occurrence_id: 'occ-1',
        metadata: { bms_method: { path: '/method/steps/0', call_path: [], loop_path: [1] } } }] }} />);
    expect(html).toContain('/method/steps/0');
    expect(html).toContain('&quot;loop_path&quot;:[1]');
});


const deckCatalog = JSON.parse(readFileSync('tests/fixtures/bioxp_method_deck_catalog.json', 'utf8'));
function publishedDeckCatalog() {
    const transport = api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter = async config => config.url?.endsWith('/catalog')
        ? { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(deckCatalog) }
        : transport(config);
}
async function mapWell(station: string, well: string, keyboard = false) {
    const el = host.querySelector<SVGGElement>(`[data-station="${station}"][data-well="${well}"]`)!;
    expect(el).toBeTruthy();
    await act(async () => el.dispatchEvent(keyboard ? new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }) : new MouseEvent('click', { bubbles: true })));
    await settle();
}
it.each(['manual', 'class'])('composes map-driven Move and %s Transfer, selected Properties, materials and cold Save/Open without robot mutations', async mode => {
    publishedDeckCatalog(); await mount();
    const map = host.querySelector('[aria-label="BioXP deck map"]')!;
    expect(map.closest('details')).toBeNull();
    await input('Method name', `Deck ${mode}`);
    await mapWell('LOC_RC', 'A1');
    expect(host.querySelectorAll('.bioxp-method-sequence li')).toHaveLength(0);
    await click('Add Move');
    const moveId = host.querySelector('[aria-label^="Label "]')!.getAttribute('aria-label')!.slice(6);
    await input(`Inputs ${moveId}.position_flag presence`, 'value');
    await input(`Inputs ${moveId}.position_flag`, '1');
    await mapWell('LOC_TC', 'B2', true); await click('Use as Move target');
    await input('New Transfer settings', mode); await click('Add Transfer');
    const transferId = host.querySelector('[aria-label^="Label "]')!.getAttribute('aria-label')!.slice(6);
    expect(host.querySelectorAll('.bioxp-method-inspector [aria-label^="Label "]')).toHaveLength(1);
    expect(host.querySelector(`[aria-label="Inputs ${moveId}.position_flag"]`)).toBeNull();
    await mapWell('LOC_RC', 'A3'); await click('Use as source');
    await mapWell('LOC_OC', 'B4', true); await click('Use as destination');
    await input(`Inputs ${transferId}.volume_ul presence`, 'value');
    await input(`Inputs ${transferId}.volume_ul`, '020.000100');
    await click('Set up labware & reagents'); await click('Add labware at selected station'); await click('Add reagent');
    await click('Assign material to selected wells');
    await click('Select step 1');
    expect(host.querySelector(`[aria-label="Inputs ${moveId}.position_flag"]`)).toBeTruthy();
    expect(host.querySelector(`[aria-label="Inputs ${transferId}.volume_ul"]`)).toBeNull();
    await click('Select step 2'); await click('Save');
    const saved = structuredClone(db.m2.method);
    expect(saved.steps[0]).toEqual({ step_id: moveId, type: 'action', action: 'move', inputs: { location_id: 2, well: 'B2', position_flag: 1 } });
    expect(saved.steps[1].inputs).toEqual({ source: { station: 'LOC_RC', location_id: 3, wells: ['A3'] }, destination: { station: 'LOC_OC', location_id: 1, wells: ['B4'] }, volume_ul: '020.000100', ...(mode === 'class' ? { liquid: {}, recipe: {} } : {}) });
    expect(saved.deck_plan.labware[0].station).toBe('LOC_OC');
    expect(saved.deck_plan.assignments[0]).toMatchObject({ well: 'B4', volume_ul: '' });
    await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', 'm2'); await click('Open');
    await click('Select step 2');
    expect((host.querySelector(`[aria-label="Inputs ${transferId} variant"]`) as HTMLSelectElement).value).toBe(mode === 'class' ? '1' : '0');
    expect((host.querySelector(`[aria-label="Inputs ${transferId}.volume_ul"]`) as HTMLInputElement).value).toBe('020.000100');
    await click('Preview');
    expect(requests.filter(r => r.url.endsWith('/compile')).at(-1)!.body.method).toEqual(saved);
    await click('Save'); expect(db.m2.method).toEqual(saved);
    await click('Quick run'); await mapWell('LOC_MS', 'A2'); await click('Add Move');
    await click('Methods'); await click('Save'); expect(db.m2.method).toEqual(saved);
    expect(submits()).toHaveLength(0);
    expect(requests.every(r => r.url.startsWith('/api/bioxp/methods/'))).toBe(true);
    expect(requests.filter(r => r.method !== 'get').every(r => /\/(library|check|compile)(\/[^/]+)?$/.test(r.url))).toBe(true);
});


it.runIf(!!process.env.BIOXP_METHOD_MODEL_CONTRACT)('emits deck-edited Move and Transfer endpoints through the actual pure compiler', async () => {
    const contracts = JSON.parse(readFileSync(process.env.BIOXP_METHOD_MODEL_CONTRACT!, 'utf8'));
    const fixture = contracts.examples[0].bound_fixture;
    db.m1.method = { schema: 'bms.bioxp-method.v1', name: 'Software-only deck compiler fixture', steps: [{ step_id: 'fixture-transfer', type: 'action', action: 'transfer', inputs: structuredClone(fixture.bindings.assemble_inputs) }] };
    const transport = api.defaults.adapter as AxiosAdapter;
    let result: any;
    api.defaults.adapter = async config => {
        const part = config.url?.split('/').at(-1);
        if (['catalog', 'schema', 'examples'].includes(part ?? '')) return { config, status: 200, statusText: 'OK', headers: {}, data: contracts[part!] };
        if (part === 'compile') {
            const body = JSON.parse(config.data);
            const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? 'python', ['-c', 'import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))'], { cwd: process.env.BIOXP_METHOD_API_DIR ?? '../api', encoding: 'utf8', input: JSON.stringify(body) });
            expect(produced.status, produced.stderr).toBe(0); result = JSON.parse(produced.stdout);
            return { config, status: 200, statusText: 'OK', headers: {}, data: result };
        }
        return transport(config);
    };
    await mount(); await openOriginal();
    await mapWell('LOC_RC', 'A3'); await click('Use as source');
    await mapWell('LOC_OC', 'B4'); await click('Use as destination');
    await input('Inputs fixture-transfer.volume_ul', '010.000');
    await mapWell('LOC_TC', 'B2'); await click('Add Move');
    const moveId = host.querySelector('[aria-label^="Label "]')!.getAttribute('aria-label')!.slice(6);
    await input(`Inputs ${moveId}.position_flag presence`, 'value'); await input(`Inputs ${moveId}.position_flag`, '1');
    await click('Save'); await click('Preview');
    expect(result.document, JSON.stringify(result.issues)).toBeTruthy();
    const actions = result.document.stages.flatMap((s: any) => s.actions);
    const moves = actions.filter((a: any) => a.kind === 'pipette_position' && a.params.operation === 'move');
    expect(moves.map((a: any) => [a.params.location_id, a.params.well])).toEqual([[3, 'A3'], [1, 'B4'], [2, 'B2']]);
    expect(db.m1.method.steps[0].inputs.volume_ul).toBe('010.000');
    expect(submits()).toHaveLength(0);
});


it('adopts a map target into a nested selected action without changing its raw extensions or siblings', async () => {
    db.m1.method.steps = [{ step_id: 'group', type: 'group', future: null, steps: [structuredClone(sourceMethod.steps[0]), { step_id: 'sibling', type: 'action', action: 'move', inputs: { location_id: 1, well: 7 } }] }];
    await mount(); await openOriginal();
    const children = host.querySelectorAll<HTMLButtonElement>('[aria-label="Select step 1"]');
    await act(async () => children[children.length - 1].click()); await settle();
    await mapWell('LOC_RC', 'A3'); await click('Use as Move target'); await click('Save');
    const group = db.m1.method.steps[0];
    expect(group.future).toBeNull();
    expect(group.steps[0]).toEqual({ ...sourceMethod.steps[0], inputs: { ...sourceMethod.steps[0].inputs, location_id: 3, well: 'A3' } });
    expect(group.steps[1]).toEqual({ step_id: 'sibling', type: 'action', action: 'move', inputs: { location_id: 1, well: 7 } });
    expect(host.querySelectorAll('.bioxp-method-inspector [aria-label^="Label "]')).toHaveLength(1);
    expect(submits()).toHaveLength(0);
});


it('authors chiller, cycler and door steps from stations without wells or scientific defaults, preserving the same sequence-only draft', async () => {
    publishedDeckCatalog(); await mount();
    await input('Deck station', 'LOC_RC'); await click('Add temperature step');
    await input('Deck station', 'LOC_OC'); await click('Add temperature step');
    await input('Deck station', 'LOC_TC'); await click('Add temperature step'); await click('Add hold'); await click('Add PCR cycle'); await click('Add door Open'); await click('Add door Close');
    await click('Save');
    const saved = structuredClone(db.m2.method);
    expect(saved.steps.map((n: any) => [n.action, n.inputs])).toEqual([
        ['chiller_setpoint', { bank: 'rc' }], ['chiller_setpoint', { bank: 'oc' }],
        ['thermal_setpoint', {}], ['thermal_hold', {}], ['thermal_profile', { segments: [] }],
        ['thermal_door', { door_command: 'DO' }], ['thermal_door', { door_command: 'DC' }],
    ]);
    await click('Sequence only');
    expect((host.querySelector('[aria-label="Method deck"]') as HTMLElement).hidden).toBe(true);
    await click('Deck & sequence');
    expect((host.querySelector('[aria-label="Method deck"]') as HTMLElement).hidden).toBe(false);
    await click('Save'); expect(db.m2.method).toEqual(saved);
    expect(submits()).toHaveLength(0);
});
