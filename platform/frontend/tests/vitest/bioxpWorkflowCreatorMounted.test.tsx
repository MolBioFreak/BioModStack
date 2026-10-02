import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
import { BioXpWellPipettingPanel } from '../../src/components/BioXpWellPipettingPanel';
import { api } from '../../src/lib/api';

let host: HTMLDivElement, root: Root, client: QueryClient;
let requests: any[], db: Record<string, any>, fail: string | null;
const exported: any[] = [], adapter = api.defaults.adapter;
async function mount(connected = false, generation = 1) {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpWellPipettingPanel connected={connected} generation={generation} /></QueryClientProvider>));
}
async function fresh() {
    await act(async () => root.unmount()); client.clear(); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); await mount();
}
function button(text: string) { const result = [...host.querySelectorAll('button')].find(el => el.textContent === text); expect(result, text).toBeTruthy(); return result!; }
async function click(text: string) { await act(async () => button(text).click()); }
async function control(label: string) { await act(async () => (host.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement).click()); }
async function change(label: string, value: string) {
    const el = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(el, label).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function append(op: string) { await change('Step to append', op); await click('Append step'); }
const rows = () => [...host.querySelectorAll('[data-step-id]')].map(el => el.getAttribute('data-step-id'));
const writes = () => requests.filter(r => ['post', 'put'].includes(r.method));
function seed(steps: any[], editor_state: any = {}) {
    db.saved = { id: 'saved', name: 'Stored draft', mode: 'bioxp_workflow', model_id: null, base_template_id: null,
        params: { schema: 'bms.bioxp-workflow-draft.v1', steps, editor_state } };
}
async function open() { await click('Open workflow'); await click('Stored draft'); }
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); requests = []; db = {}; fail = null;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(config.data) : undefined;
        requests.push({ method: config.method, url: config.url, params: config.params, body });
        if (fail === config.method) throw new Error('Storage network unavailable');
        if (!config.url?.startsWith('/api/user-templates')) throw new Error(`Unexpected robot request ${config.url}`);
        let data: any;
        if (config.method === 'post') {
            const id = `workflow-${Object.keys(db).length + 1}`; data = db[id] = { ...body, id }; exported.push({ method: config.method, url: config.url, body });
        } else if (config.method === 'put') { const id = config.url.split('/').at(-1)!; data = db[id] = { ...db[id], ...body }; exported.push({ method: config.method, url: config.url, body }); }
        else if (config.url === '/api/user-templates') data = [...Object.values(db).map(row => ({ ...row, params: { stale_listing: true } })), { id: 'wrong', name: 'NOT A WORKFLOW', mode: 'pipeline' }];
        else data = db[decodeURIComponent(config.url.split('/').at(-1)!)];
        return { data: structuredClone(data), status: 200, statusText: 'OK', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); });
afterAll(() => { if (process.env.BIOXP_WORKFLOW_UI_EXPORT) writeFileSync(process.env.BIOXP_WORKFLOW_UI_EXPORT, JSON.stringify({ fixture_only: true, requests: exported }, null, 2)); });

it('actual cockpit exposes offline authoring and keeps it mounted across connection changes', async () => {
    const templateAdapter = api.defaults.adapter as (config: any) => Promise<any>;
    let connection = { active: false, configured: false, generation: 0 };
    const robotPosts: string[] = [];
    api.defaults.adapter = async config => {
        if (config.url?.startsWith('/api/user-templates')) return templateAdapter(config);
        if (config.method !== 'get') { robotPosts.push(config.url!); throw new Error('No robot mutation expected'); }
        if (config.url === '/api/bioxp/status') return { data: { connection }, status: 200, statusText: 'OK', config, headers: {} };
        throw new Error('Robot observations unavailable');
    };
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    await click('Pipettes'); await change('Workflow name', 'Cockpit offline'); await append('lower'); const ids = rows();
    connection = { active: true, configured: true, generation: 2 };
    await act(async () => { await client.invalidateQueries(); await new Promise(resolve => setTimeout(resolve, 30)); });
    connection = { active: false, configured: true, generation: 3 };
    await act(async () => { await client.invalidateQueries(); await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(rows()).toEqual(ids); await click('Save workflow');
    expect(writes()[0].body.name).toBe('Cockpit offline'); expect(robotPosts).toEqual([]);
});

it('creates a named empty workflow offline and verifies the exact ID without any robot access', async () => {
    await mount(); await change('Workflow name', 'Empty offline workflow'); await click('Save workflow');
    expect(writes()).toHaveLength(1);
    expect(writes()[0].body).toMatchObject({ name: 'Empty offline workflow', mode: 'bioxp_workflow', model_id: null, base_template_id: null,
        params: { schema: 'bms.bioxp-workflow-draft.v1', steps: [], editor_state: {} } });
    expect(requests.map(r => [r.method, r.url])).toEqual([['post', '/api/user-templates'], ['get', '/api/user-templates/workflow-1']]);
    expect(host.textContent).toContain('Saved draft'); expect(button('Run ordered steps').disabled).toBe(true);
});

it('updates the stable selected row after reordering, clones a new ID and cancels removed selection', async () => {
    await mount(); await change('Workflow name', 'Stable edit'); await change('Location number', '4');
    await append('lower'); await append('measure_fluid_height'); const ids = rows();
    await control('Edit step 1'); await change('Location number', '0'); await control('Move step 1 down');
    await click('Update step'); expect(rows()).toEqual([ids[1], ids[0]]);
    await control('Clone step 2'); expect(new Set(rows()).size).toBe(3);
    await control('Edit step 1'); await control('Remove step 1'); expect(host.textContent).not.toContain('Cancel editing');
    await click('Save workflow');
    expect(writes()[0].body.params.steps).toEqual([
        { step_id: ids[0], intent: { operation: 'lower', location_id: '0' } },
        { step_id: rows()[1], intent: { operation: 'lower', location_id: '0' } },
    ]);
    expect(requests.every(r => r.url.startsWith('/api/user-templates'))).toBe(true);
});

it('saves cleared numeric text, source raw precision, null, false, zero and omitted fields losslessly', async () => {
    const intent = { operation: 'source_mix', volume_ul: null, air_ul: 0, cycles: 0, tip_dip: false, aspirate_delay_ms: null,
        unknown: { zero: 0, no: false, nil: null, text: '' } };
    seed([{ step_id: 'stable-source', intent }], { foreign: { zero: 0, nil: null, no: false }, form: { future: 'preserve' } });
    await mount(); await open(); await control('Edit step 1'); await change('Source mix air_ul', '0.00');
    await change('Source mix cycles', ''); await click('Update step');
    await change('Source purge speed', ''); await change('Aspirate air volume (µL)', '6.250');
    await append('source_aspirate_air'); await click('Save workflow');
    const saved = writes()[0].body.params;
    expect(saved.steps[0]).toEqual({ step_id: 'stable-source', intent: { ...intent, air_ul: '0.00', cycles: '' } });
    expect(saved.steps[0].intent).not.toHaveProperty('dispense_speed');
    expect(saved.steps[1].intent).toEqual({ operation: 'source_aspirate_air', volume_ul: '6.250' });
    expect(saved.editor_state.foreign).toEqual({ zero: 0, nil: null, no: false });
    expect(saved.editor_state.form.future).toBe('preserve');
    expect(saved.editor_state.form.sourceDrafts.source_purge.speed).toBe('');
    await fresh(); await open(); await click('Save workflow');
    expect(writes()[1].body.params).toEqual(saved);
});

it('fresh mount and fresh cache Open use a mode-only list then exact ID, not list params or localStorage', async () => {
    await mount(); await change('Workflow name', 'Persistent'); await append('lower'); await click('Save workflow');
    db['workflow-1'].name = 'Database authoritative'; db['workflow-1'].params.steps[0].intent.location_id = '7';
    localStorage.setItem('bioxp-workflow', 'stale'); await fresh(); await click('Open workflow');
    expect(host.textContent).not.toContain('NOT A WORKFLOW');
    await click('Database authoritative'); await control('Edit step 1');
    expect((host.querySelector('[aria-label="Location number"]') as HTMLInputElement).value).toBe('7');
    expect(requests.find(r => r.url === '/api/user-templates' && r.method === 'get').params).toEqual({ mode: 'bioxp_workflow' });
    expect(requests.at(-1).url).toBe('/api/user-templates/workflow-1'); localStorage.clear();
});

it('unknown and incomplete steps stay visible drafts, save unchanged, and never silently execute', async () => {
    const steps = [{ step_id: 'unknown', intent: { operation: 'future_operation', n: null } }, { step_id: 'incomplete', intent: { operation: 'source_mix' } }];
    seed(steps); await mount(true); await open(); await click('Save workflow');
    expect(writes()[0].body.params.steps).toEqual(steps); expect(host.textContent).toContain('Draft · future_operation');
    await click('Run ordered steps'); expect(host.textContent).toContain('Incomplete or unknown draft');
    expect(requests.filter(r => r.url.includes('/bioxp/'))).toHaveLength(0);
});

it('retains arbitrary editor JSON and malformed source diagnostics without coercion or render failure', async () => {
    const editor = { editing_step_id: 'future-selection', edit_baseline: { future: null }, form: { volume: null, future: false, sourceDrafts: { diagnostic_pipette: { operation: 'diagnostic_pipette', diagnostic: { action: 'aspirate', channels: null, speed: null } } } } };
    const steps = [{ step_id: 'malformed', intent: { operation: 'diagnostic_pipette', diagnostic: null } }];
    seed(steps, editor); await mount(); await open(); await click('Save workflow');
    expect(writes()[0].body.params.editor_state).toEqual(editor);
    await control('Edit step 1'); await click('Update step'); await click('Save workflow');
    expect(writes()[1].body.params.steps).toEqual(steps);
});

it('retains the accepted create ID if readback fails, so retry updates instead of duplicating', async () => {
    await mount(); await change('Workflow name', 'Readback recovery'); fail = 'get'; await click('Save workflow');
    expect(writes()).toHaveLength(1); expect(host.textContent).toContain('Storage network unavailable');
    fail = null; await click('Save workflow'); expect(writes().map(r => r.method)).toEqual(['post', 'put']);
    expect(Object.keys(db)).toHaveLength(1); expect(host.textContent).toContain('Saved draft');
});

it('keeps unselected source tip controls blank instead of selecting pipette zero', async () => {
    await mount(true); await change('Workflow name', 'Unselected tips');
    await change('Tip size', ''); await change('Pipettes to load', ''); await append('source_load_tips');
    await click('Save workflow');
    expect(writes()[0].body.params.steps[0].intent).toEqual({ operation: 'source_load_tips', tip_type: '', pipette: '', force_new_tip: false });
    await click('Load selected tips now'); expect(requests.filter(r => r.url.includes('/bioxp/'))).toHaveLength(0);
});

it('network Save/Open errors retain unsaved name, rows, selection and native form', async () => {
    await mount(); await change('Workflow name', 'Keep me'); await append('lower'); await control('Edit step 1'); await change('Location number', '11');
    const ids = rows(); fail = 'post'; await click('Save workflow'); expect(host.textContent).toContain('Storage network unavailable');
    fail = 'get'; await click('Open workflow'); expect(rows()).toEqual(ids);
    expect((host.querySelector('[aria-label="Workflow name"]') as HTMLInputElement).value).toBe('Keep me');
    expect((host.querySelector('[aria-label="Location number"]') as HTMLInputElement).value).toBe('11');
    expect(host.textContent).toContain('Cancel editing'); fail = null; await click('Update step'); await click('Save workflow');
    expect(writes().at(-1).body.params.steps[0].intent.location_id).toBe('11');
});

it('keeps unsaved authoring across robot connection generation changes', async () => {
    await mount(); await change('Workflow name', 'Connection independent'); await append('lower'); const ids = rows();
    await mount(true, 2); await mount(false, 3);
    expect(rows()).toEqual(ids); await click('Save workflow');
    expect(writes()[0].body.name).toBe('Connection independent'); expect(writes()[0].body.params.steps[0].step_id).toBe(ids[0]);
});

it('saving an in-progress edit restores the editor separately without implicitly updating its row', async () => {
    await mount(); await change('Workflow name', 'Stored draft'); await change('Location number', '1'); await append('lower');
    await control('Edit step 1'); await change('Location number', '2'); await click('Save workflow');
    expect(writes()[0].body.params.steps[0].intent.location_id).toBe('1');
    await fresh(); await open(); expect(host.textContent).toContain('Cancel editing');
    expect((host.querySelector('[aria-label="Location number"]') as HTMLInputElement).value).toBe('2');
    await click('Update step'); await click('Save workflow'); expect(writes()[1].body.params.steps[0].intent.location_id).toBe('2');
});

it('exposes missing diagnostic fields for repair without replacing untouched null or omitted fields', async () => {
    const intent = { operation: 'diagnostic_pipette', diagnostic: { action: 'aspirate', channels: null, speed: null } };
    seed([{ step_id: 'repair-diagnostic', intent }]); await mount(); await open(); await control('Edit step 1');
    await control('Diagnostic pipette 1 (ID 0)'); await change('Diagnostic volume (µL)', '0'); await click('Update step'); await click('Save workflow');
    expect(writes()[0].body.params.steps[0]).toEqual({ step_id: 'repair-diagnostic', intent: { operation: 'diagnostic_pipette', diagnostic: { action: 'aspirate', channels: [0], speed: null, volume_ul: '0' } } });
});

it('retains newer edits while a selected workflow GET is delayed', async () => {
    seed([]); await mount(); await change('Workflow name', 'Unsaved local'); await click('Open workflow');
    const normal = api.defaults.adapter as (config: any) => Promise<any>;
    let release!: () => void;
    api.defaults.adapter = async config => {
        if (config.url === '/api/user-templates/saved') await new Promise<void>(resolve => { release = resolve; });
        return normal(config);
    };
    await click('Stored draft'); await change('Workflow name', 'Newer local'); await append('lower');
    await act(async () => release());
    expect((host.querySelector('[aria-label="Workflow name"]') as HTMLInputElement).value).toBe('Newer local');
    expect(rows()).toHaveLength(1); expect(host.textContent).toContain('Editor changed while opening');
});

it('labels a saved earlier snapshot when native fields change while Save is pending', async () => {
    await mount(); await change('Workflow name', 'Pending save'); await append('lower');
    const normal = api.defaults.adapter as (config: any) => Promise<any>;
    let release!: () => void;
    api.defaults.adapter = async config => {
        if (config.method === 'post') await new Promise<void>(resolve => { release = resolve; });
        return normal(config);
    };
    await click('Save workflow'); await change('Location number', '17'); await act(async () => release());
    expect(host.textContent).toContain('Newer editor changes are not saved');
    expect((host.querySelector('[aria-label="Location number"]') as HTMLInputElement).value).toBe('17');
});
