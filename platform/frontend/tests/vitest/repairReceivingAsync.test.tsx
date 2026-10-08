import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosAdapter } from 'axios';
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
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
async function mount() { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); root = createRoot(host); await act(async () => root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={7} connected={true} controlsEnabled={true} /></QueryClientProvider>)); await settle(); }
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

it('holds frontend submission before dispatch while real referenced class content advances in SQLite, then compiles the captured frozen body', async () => {
    const original = JSON.parse(readFileSync(process.env.RECEIVING_ASYNC_SOURCE!, 'utf8')).request;
    const entry = structuredClone(original.dependencies.liquid_classes[0]); delete entry.revision;
    const created = await wire('post','/api/bioxp/methods/liquid-classes',{method:entry,name:`RP16 frontend pin ${process.pid}`});
    const classUrl=`/api/bioxp/methods/liquid-classes/${created.id}`;
    const pin=await wire('get',classUrl+'/revisions/1');
    const dependency={...pin.method,revision:pin.revision};
    const method={...original.method,editor_state:{run_inputs:{bindings:original.bindings,dependencies:{liquid_classes:[dependency]},initial_state:{}}}};
    await mount();await click('Quick run');
    const file=host.querySelector<HTMLInputElement>('[aria-label="Import JSON"]')!;
    Object.defineProperty(file,'files',{configurable:true,value:[{text:async()=>JSON.stringify(method)}]});
    await act(async()=>file.dispatchEvent(new Event('change',{bubbles:true})));await settle();
    const ack=[...host.querySelectorAll('label')].find(n=>n.textContent==='Acknowledge live robot execution')!.querySelector<HTMLInputElement>('input')!;
    await act(async()=>ack.click());await settle();
    let release!:()=>void; const held=new Promise<void>(r=>release=r); let hashing=false;
    vi.stubGlobal('crypto',{randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:async (...args: Parameters<typeof webcrypto.subtle.digest>)=>{hashing=true;await held;return webcrypto.subtle.digest(...args);}}});
    const transport=api.defaults.adapter as AxiosAdapter;let submitted:any=null;
    api.defaults.adapter=async config=>{
        if(config.url!.endsWith('/quick-runs')){
            submitted=JSON.parse(config.data);
            // Deliberately inert transport boundary: no scientific submission leaves this test.
            throw Object.assign(new Error('Receiving transport refused before robot dispatch'),{isAxiosError:true,response:{status:403,data:{detail:{delivery:'not_submitted'}}}});
        }
        return transport(config);
    };
    await click('Run immutable quick snapshot');expect(hashing).toBe(true);expect(submitted).toBeNull();
    const changed=structuredClone(entry);changed.settings.aspirate_speed_ul_s='075.2500';changed.authored_settings.aspirate_speed_ul_s='075.2500';
    const next=await wire('put',classUrl,{method:changed,expected_base_revision:1});expect(next.revision).toBe(2);
    expect((await wire('get',classUrl)).method.settings.aspirate_speed_ul_s).toBe('075.2500');
    expect((await wire('get',classUrl+'/revisions/1')).method).toEqual(pin.method);
    await input('Method name','Changed while hashing');await act(async()=>release());await settle();
    expect(submitted).toBeTruthy();expect(submitted.dependencies.liquid_classes).toEqual([dependency]);expect(submitted.method.name).toBe(original.method.name);
    expect(JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!).snapshot.dependencies.liquid_classes).toEqual([dependency]);
    const compiled=await wire('post','/api/bioxp/methods/compile',{method:submitted.method,bindings:submitted.bindings,dependencies:submitted.dependencies,initial_state:submitted.initial_state});expect(compiled.document,JSON.stringify(compiled.issues)).toBeTruthy();
    const recipes=compiled.document.stages.flatMap((s:any)=>s.actions).filter((a:any)=>a.params?.recipe).map((a:any)=>a.params.recipe);
    expect(recipes.length).toBeGreaterThan(0);expect(recipes.every((r:any)=>r.aspiration_speed_ul_s===50)).toBe(true);
    writeFileSync(process.env.RECEIVING_ASYNC_UI_OUTPUT!,JSON.stringify({pin,next,submitted,compiled,scope:'actual frontend digest suspension and immutable body; actual class HTTP/SQLite revision and compiler; submission transport deliberately inert before any HTTP run'},null,2));
});
