import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
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
let inFlight = 0;
async function settle() { for (let i = 0; i < 300; i++) { await act(async () => { await new Promise(r => setTimeout(r, 25)); }); if (!inFlight) { await act(async () => { await new Promise(r => setTimeout(r, 25)); }); if (!inFlight) return; } } throw new Error('HTTP did not settle'); }
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
    for (let depth = 0; depth < path.length; depth++) {
        const pointer = path.slice(0, depth + 1).map(index => `/steps/${index}`).join('');
        const button = host.querySelector<HTMLButtonElement>(`[data-method-path="${pointer}"]`)!;
        expect(button, `step path ${pointer}`).toBeTruthy();
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

const origin = process.env.RECEIVING_ORIGIN!;
const output = process.env.RECEIVING_OUTPUT!;
const rows: any[] = [];
let savedId = '';
async function wire(method: string, path: string, body?: any) {
    const result: any = await new Promise((resolve, reject) => {
        const r = httpRequest(origin + path, { method: method.toUpperCase(), headers: { 'Content-Type': 'application/json' } }, response => {
            let text = ''; response.on('data', b => text += b); response.on('end', () => resolve({ status: response.statusCode, data: JSON.parse(text) }));
        }); r.on('error', reject); if (body !== undefined) r.write(JSON.stringify(body)); r.end();
    });
    expect(result.status, JSON.stringify(result.data)).toBeLessThan(400); return result.data;
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; results = []; db = {}; savedId = '';
    host = document.createElement('div'); document.body.append(host); oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        inFlight++; let data: any;
        try { const query = new URLSearchParams(Object.entries(config.params ?? {}).filter(([,v]) => v !== undefined && v !== null).map(([k,v]) => [k,String(v)])).toString(); data = await wire(config.method!, config.url! + (query ? '?' + query : ''), body); } finally { inFlight--; }
        if (!config.url!.endsWith('/check')) requests.push({ method: config.method, url: config.url, body, ...(/\/library(?:\/|$)/.test(config.url!) ? {response:data} : {}) });
        if (/\/library(?:\/[^/]+)?$/.test(config.url!) && ['post','put'].includes(config.method!)) { db.m1 = data; savedId = data.id; }
        if (config.url!.endsWith('/compile')) results.push({ input: body, result: data });
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

async function importJSON(value: any) { const el = host.querySelector<HTMLInputElement>('[aria-label="Import JSON"]')!; expect(el).toBeTruthy(); Object.defineProperty(el, 'files', { configurable: true, value: [{ text: async () => JSON.stringify(value) }] }); await act(async () => el.dispatchEvent(new Event('change', { bubbles: true }))); await settle(); }
it('changed active import and bound branch reversal survive real Save/exact GET/revision/cold Open/compile', async () => {
    const manual = { source:{station:'LOC_RC',location_id:3,wells:['A1']},destination:{station:'LOC_TC',location_id:2,wells:['A1']},channels:[0],volume_ul:'003.7500',aspirate_speed:'030.00',dispense_speed:'040.00',source_position_flag:1,destination_position_flag:2,source_lift_height_steps:null,destination_lift_height_steps:100 };
    const reference = {expr:{version:1,op:'param',id:'addition'}};
    const incoming = {schema:'bms.bioxp-method.v1',name:`RP16 changed inputs ${process.pid}`,parameters:[{id:'addition',type:'object'}],steps:[{step_id:'addition',type:'action',action:'transfer',inputs:reference}],editor_state:{run_inputs:{bindings:{addition:manual},dependencies:{labware_profiles:[{id:'real-input',revision:3,native_addressing:{reference_channel:0,row_increment:2,column_increment:0}}]},initial_state:null}}};
    await mount(); await input('Bound software fixture','4'); await click('Compile');
    await importJSON(incoming); await click('Compile');
    expect(results.at(-1).input.bindings).toEqual(incoming.editor_state.run_inputs.bindings);expect(results.at(-1).input.dependencies).toEqual(incoming.editor_state.run_inputs.dependencies);expect(results.at(-1).input.initial_state).toBeNull();
    expect(results.at(-1).result.document,JSON.stringify(results.at(-1).result.issues)).toBeTruthy();
    await selectPath([0]); await input('Transfer mode','class'); await input('Volume per channel (µL)','01.250'); await click('Save');
    const selected = savedId, first=structuredClone(db.m1); expect(first.method.steps[0].inputs).toEqual(reference);
    expect(first.method.editor_state.run_inputs.bindings.addition).not.toHaveProperty('aspirate_speed');
    expect((await wire('get',`/api/bioxp/methods/library/${selected}/revisions/${first.revision}`)).method).toEqual(first.method);
    await act(async () => root.unmount());client.clear();await mount(); for (let n=0;n<100 && !host.querySelector(`[aria-label="Library entry"] option[value="${selected}"]`);n++) await settle(); expect([...host.querySelectorAll('[aria-label="Library entry"] option')].map(o => (o as HTMLOptionElement).value),host.textContent ?? '').toContain(selected); await input('Library entry',selected);await click('Open');await selectPath([0]);await input('Transfer mode','manual');await click('Save');
    const second=structuredClone(db.m1);expect(second.method.steps[0].inputs).toEqual(reference);expect(second.method.editor_state.run_inputs.bindings.addition).toEqual({...manual,volume_ul:'01.250'});
    await click('Compile');expect(results.at(-1).result.document,JSON.stringify(results.at(-1).result.issues)).toBeTruthy();
    const captured=structuredClone(results.at(-1));
    await importJSON({schema:'bms.bioxp-method.v1',name:'Legacy input-less',steps:[]});await click('Compile');expect(results.at(-1).input.bindings).toEqual({});expect(results.at(-1).input.dependencies).toEqual({});expect(results.at(-1).input.initial_state).toEqual({});
    writeFileSync(process.env.RECEIVING_RETENTION_OUTPUT!,JSON.stringify({first,second,compilation:captured,legacy:results.at(-1),requests},null,2));
});
