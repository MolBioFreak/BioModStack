import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { BioXpMethodsWorkspace } from '../../src/components/BioXpMethodsWorkspace';
import { BioXpWorkflowMaterials } from '../../src/components/BioXpWorkflowMaterials';
import { publishedTipProfiles, pinLabwareProfile } from '../../src/components/BioXpWorkflowProfiles';
import { api } from '../../src/lib/api';

function python(code: string, input?: unknown) {
    const result = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', code], { cwd: '../api', encoding: 'utf8', input: input === undefined ? undefined : JSON.stringify(input) });
    expect(result.status, result.stderr).toBe(0); return JSON.parse(result.stdout);
}
const discovery = python('import json; from bioxp_method_model import method_catalog, method_schema; from bioxp_method_liquids import starter_entries, starter_profiles; print(json.dumps(dict(catalog=method_catalog(), schema={"method":method_schema()}, starters=starter_entries(), profiles=starter_profiles())))');
let root: Root, host: HTMLDivElement, client: QueryClient;
const oldAdapter = api.defaults.adapter;
async function settle() { await act(async () => { await new Promise(r => setTimeout(r, 15)); }); }
async function mount() { host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={0} connected={false} controlsEnabled={false} /></QueryClientProvider>)); await settle(); }
async function click(name: string) { const button = [...host.querySelectorAll('button')].find(b => b.textContent === name || b.getAttribute('aria-label') === name); expect(button, name).toBeTruthy(); await act(async () => button!.click()); await settle(); }
async function select(label: string, value: string) { const el = host.querySelector<HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(el, label).toBeTruthy(); await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); }
afterEach(async () => { await act(async () => root?.unmount()); client?.clear(); host?.remove(); api.defaults.adapter = oldAdapter; });
it('projects the real published IDs exactly like the Python profile owner; preserves dependency expressions and pins', () => {
    expect(publishedTipProfiles(discovery.starters)).toEqual(discovery.profiles);
    for (const retained of [null, { expr: { version: 1, op: 'param', id: 'profiles' } }]) {
        const deps = { labware_profiles: retained, extra: null }; expect(pinLabwareProfile(deps, discovery.profiles[0])).toBe(deps);
    }
    const deps = { labware_profiles: [{ ...discovery.profiles[0], revision: 9, extra: '00.100' }] };
    expect(pinLabwareProfile(deps, discovery.profiles[0])).toBe(deps);
});
it('discovers, assigns and pins a rack profile through real workspace; Save/exact GET/cold Open retain raw data without execution', async () => {
    const method: any = { schema: 'bms.bioxp-method.v1', name: 'Rack plan', parameters: [], procedures: [], steps: [{ step_id: 'note', type: 'action', action: 'note', inputs: { message: 'Planning only' } }],
        deck_plan: { labware: [{ id: 'rack', name: 'Rack one', station: 'LOC_T1', profile_id: null, native_plate_id: null, future: { expr: { version: 1, op: 'param', id: 'future' } } }], materials: [{ id: 'r', name: 'Reagent', kind: 'reagent', concentration: '01.250', description: '' }], assignments: [{ id: 'a', labware_id: 'rack', material_id: 'r', well: 'A1', volume_ul: null }] }, unknown: null };
    let record: any = { id: 'm1', revision: 1, name: method.name, method };
    const requests: any[] = [];
    api.defaults.adapter = async config => {
        const url = config.url!, body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ method: config.method, url, body }); let data: any;
        if (url.endsWith('/catalog')) data = discovery.catalog;
        else if (url.endsWith('/schema')) data = discovery.schema;
        else if (url.endsWith('/starters')) data = discovery.starters;
        else if (url.endsWith('/check')) data = { document: null, issues: [] };
        else if (url.endsWith('/examples') || url.endsWith('/presets')) data = [];
        else if (url.endsWith('/library')) data = [record];
        else if (config.method === 'put' && url.endsWith('/library/m1')) { record = { ...record, revision: record.revision + 1, method: body.method }; data = record; }
        else if (url.endsWith('/revisions')) data = [record];
        else if (/\/library\/m1(?:\/revisions\/\d+)?$/.test(url)) data = record;
        else throw new Error(`Unexpected ${config.method} ${url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: structuredClone(data) };
    };
    await mount(); await select('Library entry', 'm1'); await click('Open');
    // The contextual canvas setup is mounted; choose its named labware via map.
    const rackButton = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Rack one'));
    if (rackButton) { await act(async () => rackButton.click()); await settle(); }
    const setup = [...host.querySelectorAll('button')].find(b => b.textContent === 'Plate, tips & contents');
    if (setup) { await act(async () => setup.click()); await settle(); }
    await select('Labware 1 published profile', 'tecan-liha-T10');
    await select('Labware 1 native movable object', 'PL_POOL');
    await click('Save');
    expect(record.method.deck_plan.labware[0]).toEqual({ ...method.deck_plan.labware[0], profile_id: 'tecan-liha-T10', native_plate_id: 'PL_POOL' });
    expect(record.method.deck_plan.materials).toEqual(method.deck_plan.materials);
    expect(record.method.deck_plan.assignments).toEqual(method.deck_plan.assignments);
    expect(record.method.editor_state.run_inputs.dependencies.labware_profiles).toEqual([discovery.profiles.find((p: any) => p.id === 'tecan-liha-T10')]);
    expect(record.method.steps).toEqual(method.steps);
    const saved = structuredClone(record.method);
    expect(python('import json,sys; from bioxp_method_compiler import compile_method; from bioxp_method_model import validate_method; m=json.load(sys.stdin); validate_method(m); r=compile_method({"method":m,"dependencies":m["editor_state"]["run_inputs"]["dependencies"]}); print(json.dumps({"document":r["document"],"issues":r["issues"]}))', saved).document).not.toBeNull();
    await act(async () => root.unmount()); client.clear(); host.remove();
    await mount(); await select('Library entry', 'm1'); await click('Open'); await click('Save');
    expect(record.method).toEqual(saved);
    expect(requests.filter(r => r.method !== 'get').every(r => r.method === 'put' && r.url.endsWith('/library/m1') || r.method === 'post' && r.url.endsWith('/check'))).toBe(true);
    expect(requests.some(r => /\/revisions\/2$/.test(r.url))).toBe(true);
});
it('preserves retained null, unknown and expression profile/native associations on unrelated mounted edits', async () => {
    for (const value of [null, 'unknown-token', { expr: { version: 1, op: 'param', id: 'plate' } }]) {
        let plan: any = { labware: [{ id: 'p', station: 'LOC_TC', name: '', profile_id: value, native_plate_id: value, unknown: null }], materials: [], assignments: [] };
        host = document.createElement('div'); document.body.append(host); root = createRoot(host);
        await act(async () => root.render(<BioXpWorkflowMaterials methodAuthoring plan={plan} selection={{ station: 'LOC_TC', wells: [] }} custodyObjects={discovery.catalog.authoring.custody.objects} onChange={v => { plan = v; }} />));
        await click('Add sample'); expect(plan.labware[0].profile_id).toEqual(value); expect(plan.labware[0].native_plate_id).toEqual(value);
        await act(async () => root.unmount()); host.remove();
    }
});
