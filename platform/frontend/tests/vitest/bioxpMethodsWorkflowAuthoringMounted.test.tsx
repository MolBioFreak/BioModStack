import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { spawnSync } from 'node:child_process';
import { webcrypto } from 'node:crypto';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { BioXpWorkflowJobMonitor } from '../../src/components/BioXpWorkflowJobMonitor';
import { api } from '../../src/lib/api';

// Real React workspace + real Python discovery/compiler behind Axios. Robot routes are refused.
function python(code: string, input?: unknown) {
    const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', code], { cwd: '../api', encoding: 'utf8', input: input === undefined ? undefined : JSON.stringify(input) });
    expect(result.status, result.stderr).toBe(0); return JSON.parse(result.stdout);
}
const discovery = python('import json; from bioxp_method_model import method_catalog, method_schema; from bioxp_method_liquids import starter_entries; print(json.dumps(dict(catalog=method_catalog(), schema={"method":method_schema()}, starters=starter_entries())))');
const compile = (body: unknown) => python('import json,sys; from bioxp_method_compiler import compile_method; print(json.dumps(compile_method(json.load(sys.stdin))))', body);
let root: Root, host: HTMLDivElement, client: QueryClient, requests: { method: string; url: string; body: any }[], db: Record<string, any>;
const oldAdapter = api.defaults.adapter;
async function settle(ms = 15) { await act(async () => { await new Promise(r => setTimeout(r, ms)); }); }
async function checked() { await settle(800); }
function button(name: string, scope: ParentNode = host) { const b = [...scope.querySelectorAll('button')].find(n => n.textContent === name || n.getAttribute('aria-label') === name); expect(b, name).toBeTruthy(); return b!; }
async function click(name: string, scope?: ParentNode) { await act(async () => button(name, scope).click()); await settle(); }
async function input(label: string, value: string) {
    const el = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle();
}
async function station(id: string) { const el = host.querySelector<SVGGElement>(`[data-station="${id}"]:not([data-well])`) ?? host.querySelector<SVGGElement>(`[data-station="${id}"]`)!; expect(el, id).toBeTruthy(); await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); await settle(); }
async function well(id: string, w: string) { const el = host.querySelector<SVGGElement>(`[data-station="${id}"][data-well="${w}"]`)!; expect(el, `${id} ${w}`).toBeTruthy(); await act(async () => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))); await settle(); }
const posts = (suffix: string) => requests.filter(r => r.method === 'post' && r.url.endsWith(suffix));
async function mount(connected = false) {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={4} connected={connected} controlsEnabled={connected} /></QueryClientProvider>)); await settle();
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; db = {};
    api.defaults.adapter = async config => {
        const url = config.url!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method: config.method!, url, body }); let data: any;
        if (url.endsWith('/catalog')) data = discovery.catalog;
        else if (url.endsWith('/schema')) data = discovery.schema;
        else if (url.endsWith('/starters')) data = discovery.starters;
        else if (url.endsWith('/examples') || url.endsWith('/presets')) data = [];
        else if (url.endsWith('/check')) data = compile(body);
        else if (url.endsWith('/library') && config.method === 'get') data = Object.values(db);
        else if (url.endsWith('/library') && config.method === 'post') { data = db.s1 = { id: 's1', revision: 1, name: body.name, method: body.method }; }
        else if (url.endsWith('/library/s1') && config.method === 'put') { data = db.s1 = { id: 's1', revision: db.s1.revision + 1, name: body.name, method: body.method }; }
        else if (/\/library\/s1(\/revisions\/\d+)?$/.test(url)) data = db.s1;
        else if (url.endsWith('/revisions')) data = Object.values(db);
        else if (url.endsWith('/quick-runs')) { const id = `protocol-live-${[...new Uint8Array(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(body.idempotency_key.trim())))].map(b => b.toString(16).padStart(2, '0')).join('')}`; data = { job_id: id, status: 'queued' }; }
        else if (/\/runs\/protocol-live-/.test(url)) throw new Error('readback unavailable');
        else throw new Error(`Unexpected ${config.method} ${url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
});
afterEach(async () => { await act(async () => root?.unmount()); client?.clear(); host?.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

it('edits starting tip racks, covers and slot holders as typed deck_plan records and shows plain advisories on the step', async () => {
    await mount(); await input('Method name', 'Deck setup');
    await station('TECANRACK1');
    await click('Full', host.querySelector('[aria-label="Tip rack 1 contents"]')!);
    await input('Tip rack 1 tip type', 'T200');
    await station('TECANRACK2'); await click('Empty', host.querySelector('[aria-label="Tip rack 2 contents"]')!);
    await station('LOC_OC'); await input('Output chiller cover at start', 'on');
    await station('LOC_OC_COVER_STORAGE'); await input('Output cover storage holder', 'fluorometer'); await input('Output cover storage label', '4X DIYnaFLUOR');
    // The deck never offers tray 5 / tip hotel.
    expect(host.querySelector('[data-station="LOC_TIP_HOTEL"]')).toBeNull();
    expect(host.textContent).not.toContain('Tip hotel');
    // Palette: first-class Move plate / cover group with Inspect covers.
    const group = host.querySelector('[aria-label="Move plate or cover"]')!;
    expect([...group.querySelectorAll('button')].map(b => b.textContent)).toEqual(['Move plate', 'Move cover', 'Inspect covers']);
    await act(async () => button('Move cover', group).click()); await settle();
    // Author the cover move into the fluorometer slot: the advisory lands on that step and never blocks.
    const covers = discovery.catalog.authoring.custody.objects.filter((o: any) => o.kind === 'cover');
    const coverTargets = discovery.catalog.authoring.custody.destinations.filter((d: any) => d.cover_destination != null);
    await input('Cover to carry', String(covers.findIndex((o: any) => o.token === 'CV_OUTPUT')));
    await input('Placement destination', String(coverTargets.findIndex((d: any) => d.token === 'LOC_OCS')));
    await click('Save');
    const plan = db.s1.method.deck_plan;
    expect(plan.tip_racks).toEqual([{ tips: 'full', tray: 1, tip_type: 'T200' }, { tips: 'empty', tray: 2 }]);
    expect(plan.movable_objects).toEqual({ OUTPUT_COVER: 'LOC_OC_COVER' });
    expect(plan.slots).toEqual([{ holder: 'fluorometer', slot: 'LOC_OC_COVER_STORAGE', label: '4X DIYnaFLUOR' }]);
    expect(plan.labware).toEqual([]);
    expect(db.s1.method.steps.map((n: any) => [n.action, n.inputs])).toEqual([['move_cover', { cover_id: 'CV_OUTPUT', target_location: 'LOC_OCS' }]]);
    await checked();
    const result = compile(posts('/check').at(-1)!.body);
    expect(result.document).toBeTruthy();
    expect(result.simulation.issues.map((i: any) => i.code)).toContain('cover_slot_holder_mismatch');
    const summary = host.querySelector('[aria-label="Plan summary"]')!;
    expect(summary.textContent).toContain('Tip rack 1: 0 used, 96 left');
    expect(summary.textContent).toContain('Tip rack 2: 0 used, 0 left');
    expect(summary.textContent).toContain('Notes are advice only');
    expect(host.querySelector('.bioxp-step-note')?.textContent).toMatch(/\d+ notes?/);
    expect(host.querySelector('.bioxp-step-note')?.getAttribute('title')).toContain('fluorometer');
    expect(button('Run').disabled).toBe(true); // disconnected only; advisories never disable Run
    expect(host.textContent).not.toContain('Tips, time, volumes and unknowns');
    expect(requests.every(r => r.url.startsWith('/api/bioxp/methods/'))).toBe(true);
    expect(requests.filter(r => r.method === 'post').every(r => /\/(check|library)$/.test(r.url))).toBe(true);
}, 30000);

it('rack profile assignment writes deck_plan.tip_racks, not labware, so no deck_station_unknown advisory', async () => {
    await mount(); await input('Method name', 'Rack profile');
    await station('TECANRACK3');
    const profiles = host.querySelector('[aria-label="Assign rack profile"]')!;
    const first = profiles.querySelector('button')!; await act(async () => first.click()); await settle();
    await checked(); await click('Save');
    const plan = db.s1.method.deck_plan;
    expect(plan.labware).toEqual([]);
    expect(plan.tip_racks).toHaveLength(1); expect(plan.tip_racks[0]).toMatchObject({ tray: 3, tips: 'full', profile_id: expect.any(String) });
    const issues = compile({ method: db.s1.method }).issues.map((i: any) => i.code);
    expect(issues).not.toContain('deck_station_unknown');
    expect(db.s1.method.editor_state.run_inputs.dependencies.labware_profiles.length).toBe(1);
}, 30000);

it('Transfer Material mode writes {material_id} and the compiler resolves wells from the starting deck', async () => {
    await mount(); await input('Method name', 'Material transfer');
    const palette = host.querySelector('[aria-label="Reagent additions"]')!;
    await act(async () => button('Add reagent / aliquot', palette).click()); await settle();
    await click('Material', host.querySelectorAll('[aria-label="Source by"]')[0]);
    await click('Save');
    const source = db.s1.method.steps[0].inputs.source;
    expect(source).toEqual({ material_id: '' });
    await click('Wells', host.querySelectorAll('[aria-label="Source by"]')[0]);
    await click('Save');
    expect(db.s1.method.steps[0].inputs.source).toEqual({ station: '', location_id: '', wells: [] });
}, 30000);

it('Pause and Abort stay enabled when the job read fails and send to the robot', async () => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const controls: any[] = [];
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/control')) { const body = JSON.parse(config.data); controls.push(body); return { config, status: 200, statusText: 'OK', headers: {}, data: { accepted: true, reached: false, command_id: body.command_id, job_id: body.command_id, idempotency_key: body.idempotency_key } }; }
        throw new Error('read failed');
    };
    const jobId = `protocol-live-${'a'.repeat(64)}`;
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpWorkflowJobMonitor methodsFacade jobId={jobId} generation={3} connected controlsEnabled={false} busyRef={{ current: false }} onBusyChange={() => {}} /></QueryClientProvider>)); await settle(50);
    expect(button('Pause workflow').disabled).toBe(false);
    expect(host.textContent).not.toContain(jobId);
    expect(button('Copy job ID').getAttribute('data-copy-value')).toBe(jobId);
    await click('Pause workflow');
    expect(controls[0]).toMatchObject({ action: 'pause', mode: 'ordinary', command_id: jobId, expected_connection_generation: 3 });
    const confirm = [...host.querySelectorAll('label')].find(l => l.textContent?.includes('Confirm Abort'))!.querySelector('input')!;
    await act(async () => confirm.click()); await settle();
    expect(button('Abort workflow').disabled).toBe(false);
    await click('Abort workflow');
    expect(controls[1]).toMatchObject({ action: 'abort', command_id: jobId });
});
