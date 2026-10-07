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
const familyCases = contracts?.examples.flatMap((example: any) => [{example, variant: undefined}, ...(example.authoring_variants ?? []).filter((v: any) => v.id === 'on_deck_magnetic').map((variant: any) => ({example, variant}))]).map((entry: any) => ({...entry, caseId:`${entry.example.id}/${entry.variant?.id ?? 'base'}`})) ?? [];
it('published ordinary manifest contains eight bases and four additional magnetic variants', () => {
    expect(familyCases).toHaveLength(12); expect(familyCases.filter((c:any) => !c.variant)).toHaveLength(8); expect(familyCases.filter((c:any) => c.variant)).toHaveLength(4);
});
it.each(familyCases)('ordinary $caseId → real HTTP Save/exact revision/diff/cold Open/native compile', async ({example, variant}: any) => {
    const corpus: any[] = [];
    await mount();
    requests = []; results = [];
        const row: any = {workflow_id:example.id, variant_id:variant?.id ?? 'base', status:'started'}; rows.push(row);
        try {
        await input('Workflow kind', example.id);

        if (example.authoring_variants?.length) await input('Purification approach', variant?.id ?? 'external');
        await click('New experiment'); await input('Method name', `UI authored ${example.id} ${variant?.id ?? 'base'} — software inputs only`);
        const selected = variant ?? example, original = selected.method;
        await click('Save');
        expect(db.m1.method.editor_state?.run_inputs?.bindings ?? {}).toEqual(selected.bindings ?? {});
        const incomplete = structuredClone(db.m1.method);
        await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', savedId); await click('Open');
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
        const exact = await wire('get', `/api/bioxp/methods/library/${savedId}`); expect(exact.method).toEqual(saved);
        const priorRevision = exact.revision; await input('Method name', saved.name + ' revision probe'); await click('Save');
        const diff = await wire('get', `/api/bioxp/methods/library/${savedId}/diff?from_revision=${priorRevision}&to_revision=${db.m1.revision}`);
        expect(diff.changes.some((c: any) => c.path === '/name')).toBe(true);
        Object.assign(saved, db.m1.method); row.saved_id = savedId; row.revision = db.m1.revision; row.diff = diff; row.exact = await wire('get', `/api/bioxp/methods/library/${savedId}/revisions/${db.m1.revision}`); expect(row.exact.method).toEqual(saved);
        const checkRefs = (nodes: any[], originals: any[]) => nodes.forEach((n, i) => { if (originals[i].inputs?.expr) expect(n.inputs).toEqual(originals[i].inputs); if (n.type === 'repeat') expect(n.count).toEqual(originals[i].count); if (n.steps) checkRefs(n.steps, originals[i].steps); });
        checkRefs(saved.steps, original.steps);
        if (variant) {
            const values = saved.editor_state.run_inputs.bindings, plateId = saved.deck_plan.labware[1].id;
            expect(values.wash_count).toBe('02');
            for (const key of ['binding_on_magnet_inputs', 'wash_off_magnet_inputs', 'wash_on_magnet_inputs', 'elution_off_magnet_inputs', 'final_on_magnet_inputs']) expect(values[key]).toMatchObject({ plate_id: 'PL_POOL', labware_id: plateId });
            for (const key of ['supernatant_remove_inputs', 'wash_remove_inputs', 'eluate_collect_inputs']) expect(values[key].source).toMatchObject({ station: 'LOC_MS', location_id: 0, labware_id: plateId });
        }
        await act(async () => root.unmount()); client.clear(); await mount(); await input('Library entry', savedId); await click('Open'); await click('Preview');
        const compiled = results.at(-1); expect(compiled.input.method).toEqual(saved);
        corpus.push({ workflow_id: example.id, ...(variant ? { variant_id: variant.id } : {}), input: compiled.input, result: compiled.result });
        row.compilation = compiled;
        expect(compiled.result.document, JSON.stringify(compiled.result.issues)).toBeTruthy();
        expect(compiled.result.issues.filter((issue: any) => issue.category === 'representation'), JSON.stringify(compiled.result.issues)).toHaveLength(0);
        row.status = 'passed';
        } catch (e) { row.status = 'failed'; row.error = String(e); row.stack = e instanceof Error ? e.stack : null; row.dom = host.textContent; }
        row.requests = structuredClone(requests); writeFileSync(output + '.tmp', JSON.stringify(rows, null, 2)); renameSync(output + '.tmp', output);
    expect(row.status, row.error).toBe('passed');
    expect(requests.filter(r => /quick-runs|\/runs|execute|operator/.test(r.url))).toHaveLength(0);
}, 300000);
