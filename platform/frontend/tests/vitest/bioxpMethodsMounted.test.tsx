import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, type AxiosAdapter } from 'axios';
import { webcrypto, createHash } from 'node:crypto';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync, writeFileSync } from 'node:fs';
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
function button(name: string) { const node = [...host.querySelectorAll('button')].find(n => n.textContent === name); expect(node, name).toBeTruthy(); return node!; }
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
it('retains accepted create through readback failure and retries PUT rather than duplicate POST', async () => {
    saveReadFailure = true; await mount(); await input('Method name', 'Incomplete'); await click('Save');
    expect(button('Run saved revision').disabled).toBe(true);
    saveReadFailure = false; await click('Save');
    expect(requests.filter(r => r.url.endsWith('/library') && r.method === 'post')).toHaveLength(1);
    expect(requests.filter(r => r.method === 'put')).toHaveLength(1);
    expect(host.textContent).toContain('Saved exact revision 2');
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
it('compiles separately, presents Water without new acknowledgement and derives map from emitted station', async () => {
    await mount(); await openOriginal(); await click('Compile');
    expect(host.textContent).toContain('Before Run: matching Water substitutions'); expect(host.textContent).toContain('Station: LOC_TC');
    expect(host.querySelector('[aria-label="Compiled occurrence preview"]')).toBeTruthy(); expect(submits()).toHaveLength(0);
    expect([...host.querySelectorAll('label')].filter(n => /Acknowledge live robot execution/.test(n.textContent ?? ''))).toHaveLength(1);
});
it('stops hidden-subtab demand, keeps active reconciliation and settles ambiguous terminal observation without disabling warm-error controls', async () => {
    await mount(); await openOriginal(); await acknowledge(); await click('Run saved revision');
    readFailure = true; await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
    expect(button('Pause workflow').disabled).toBe(false);
    expect(button('Request safe-state stop').disabled).toBe(false);
    readFailure = false; phase = 'reconciling'; await act(async () => { await client.refetchQueries({ predicate: q => q.queryKey.includes('observation') }); }); await settle();
    expect(host.textContent).toContain('Phase: reconciling');
    visible = false; await render(); const before = reads().length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)); });
    expect(reads()).toHaveLength(before);
    visible = true; terminal = true; phase = 'terminal'; await render();
    const settled = reads().length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)); });
    expect(reads()).toHaveLength(settled); expect(button('Pause workflow').disabled).toBe(true);
}, 10000);
it('does not interpret HTTP status or absent ACK as definitive refusal and displays non-XY without coordinates', () => {
    expect(definiteMethodRefusal({ response: { status: 422, data: { detail: 'invalid' } } })).toBe(false);
    expect(previewActionDestination({ kind: 'thermal', params: {}, station: null, well: null })).toEqual({ station: null, well: null });
    expect(previewActionDestination({ kind: 'pipette_position', params: { location_id: 0, well: 'a1' }, station: null, well: null })).toEqual({ station: 'LOC_MS', well: 'A1' });
});
