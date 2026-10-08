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
        const r = httpRequest(origin + path, { agent: false, method: method.toUpperCase(), headers: { 'Content-Type': 'application/json' } }, response => {
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
        try { const query = new URLSearchParams(Object.entries(config.params ?? {}).filter(([,v])=>v!==undefined).map(([k,v])=>[k,String(v)])).toString(); data = await wire(config.method!, config.url! + (query ? '?' + query : ''), body); } finally { inFlight--; }
        requests.push({ method: config.method, url: config.url, body, response: data });
        if (/\/library(?:\/[^/]+)?$/.test(config.url!) && ['post','put'].includes(config.method!)) { db.m1 = data; savedId = data.id; }
        if (/\/(compile|check)$/.test(config.url!)) results.push({ input: body, result: data });
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); });

async function importJSON(value: any) { const el = host.querySelector<HTMLInputElement>('[aria-label="Import JSON"]')!; expect(el).toBeTruthy(); Object.defineProperty(el,'files',{configurable:true,value:[{text:async()=>JSON.stringify(value)}]}); await act(async()=>el.dispatchEvent(new Event('change',{bubbles:true}))); await settle(); }
async function coldOpen() { await act(async()=>root.unmount());client.clear();await mount(); for(let i=0;i<100&&!host.querySelector(`[aria-label="Library entry"] option[value="${savedId}"]`);i++) await settle(); if(!host.querySelector(`[aria-label="Library entry"] option[value="${savedId}"]`)) { writeFileSync(output+'.cold-list-failure.json',JSON.stringify({savedId,dom:host.textContent,requests},null,2));throw new Error(`Cold library absent ${savedId}`); } await input('Library entry',savedId);await click('Open'); }
it('nonempty referenced class revision and active dependency content freeze before asynchronous submit dispatch',async()=>{
    await mount(); await act(async()=>root.render(<QueryClientProvider client={client}><BioXpMethodsWorkspace generation={7} connected controlsEnabled /></QueryClientProvider>));await settle();
    const starters=await wire('get','/api/bioxp/methods/liquid-classes/starters');
    const entry=structuredClone(starters.find((x:any)=>x.settings.aspirate_speed_ul_s));entry.id='frontend-frozen-class';entry.settings={aspirate_speed_ul_s:'050.000'};entry.authored_settings={...entry.settings};entry.context.applicable_fields=['aspirate_speed_ul_s'];
    const savedClass=await wire('post','/api/bioxp/methods/liquid-classes',{method:entry,name:'RP16 frontend async class'});
    const classPath=`/api/bioxp/methods/liquid-classes/${savedClass.id}`;
    const pin=await wire('get',classPath+'/revisions/1');const dependency={...pin.method,revision:pin.revision};
    const recipe={mode:'single',channels:[0,1,2,3],timeout_ms:1000,target_liquid_ul:20,commanded_aspiration_ul:22.5,aspiration_delay_ms:0,leading_air:{volume_ul:20,speed_ul_s:50},trailing_air:{volume_ul:1.25,speed_ul_s:50},before_leading_air:[],before_liquid:[],after_liquid:[],before_dispense:[],after_dispense:[],dispense_segments:[{volume_ul:22.5,speed_ul_s:875}],final_empty_speed_ul_s:null,final_empty_before:[],multi:null};
    const method:any={schema:'bms.bioxp-method.v1',name:'RP16 frozen class method',steps:[{type:'action',step_id:'liquid',action:'liquid_recipe',inputs:{recipe,liquid:{liquid_class:entry.id,context:entry.context}}}],editor_state:{run_inputs:{bindings:{},dependencies:{liquid_classes:[dependency]},initial_state:{}}}};
    await importJSON(method);await click('Save');const exact=await wire('get',`/api/bioxp/methods/library/${savedId}/revisions/1`);
    let release!:()=>void;const held=new Promise<void>(r=>release=r);let hashing=false;let submission:any;
    vi.stubGlobal('crypto',{randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:async(algorithm:any,value:any)=>{hashing=true;await held;return webcrypto.subtle.digest(algorithm,value);}}});
    const adapter=api.defaults.adapter as AxiosAdapter;
    api.defaults.adapter=async config=>{if(config.method==='post'&&config.url!.endsWith('/runs')){submission=JSON.parse(config.data);throw Object.assign(new Error('Inert frontend receiving boundary; not dispatched'),{response:{status:403,data:{detail:{delivery:'not_submitted'}}}});}return adapter(config);};
    await check('Acknowledge live robot execution');await click('Run saved revision');expect(hashing).toBe(true);expect(submission).toBeUndefined();
    const changed=structuredClone(entry);changed.settings.aspirate_speed_ul_s='075.2500';changed.authored_settings.aspirate_speed_ul_s='075.2500';
    const revision2=await wire('put',classPath,{method:changed,expected_base_revision:1});expect(revision2.revision).toBe(2);expect((await wire('get',classPath)).method.settings.aspirate_speed_ul_s).toBe('075.2500');
    const next=structuredClone(method);next.editor_state.run_inputs.dependencies.liquid_classes=[{...revision2.method,revision:2}];await importJSON(next);
    await act(async()=>release());await settle();expect(submission).toBeTruthy();expect(submission.dependencies).toEqual(method.editor_state.run_inputs.dependencies);expect(submission.revision).toBe(1);
    const retained=JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1')!);expect(retained.snapshot.method).toEqual(exact.method);expect(retained.snapshot.dependencies).toEqual(method.editor_state.run_inputs.dependencies);
    const compilation=await wire('post','/api/bioxp/methods/compile',{method:exact.method,bindings:submission.bindings,dependencies:submission.dependencies,initial_state:submission.initial_state});expect(compilation.document,JSON.stringify(compilation.issues)).toBeTruthy();
    const recipes=compilation.document.stages.flatMap((s:any)=>s.actions).filter((a:any)=>a.params?.recipe).map((a:any)=>a.params.recipe);expect(recipes.length).toBeGreaterThan(0);expect(recipes.every((r:any)=>r.aspiration_speed_ul_s===50)).toBe(true);
    writeFileSync(output+'.frontend-async-class.json',JSON.stringify({pin,revision2,exact,submission,retained,compilation,scope:'actual frontend snapshot before deferred WebCrypto; real class HTTP store mutation; inert submit adapter and real HTTP compile, no robot dispatch'},null,2));
});
