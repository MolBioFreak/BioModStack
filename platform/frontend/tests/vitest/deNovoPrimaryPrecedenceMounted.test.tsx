import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { api } from '../../src/lib/api';

// Real parent, discovery editors, draft owners and submitJob/Axios interceptors.
// Only placement and WebGL are inert; no server persistence/inference is claimed.
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => null }));
const python = process.env.BMS_TEST_API_PYTHON ?? process.env.BMS_SCHEMA_PYTHON ?? 'python';
const cwd = resolve(process.cwd(), '../..');
function native(code: string, value?: unknown) {
    return JSON.parse(execFileSync(python, ['-c', code], { cwd, input: value === undefined ? undefined : JSON.stringify(value), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 }));
}
const inventories = native("import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings as r;from services.complexa import discovery as c;from scripts.lib.laproteina_contract import settings_schema as l;from scripts.lib.disco_contract import settings_schema as d;from scripts.lib.rfd3_scientific_settings import scientific_inventory as f;print(json.dumps({'rfd4_proteina':{m:r(m) for m in ('generate','evaluate')},'complexa':{m:c() for m in ('generate','evaluate')},'rfd3':f(),'laproteina':{'parameter_schema':l()},'disco':{'parameter_schema':d()}}))");
const project = (params: unknown) => native('import json,sys;from scripts.lib.protein_cad_settings import settings_projection;print(json.dumps(settings_projection(json.load(sys.stdin))))', params);
let root: Root | undefined, client: QueryClient;
let prior: typeof api.defaults.adapter;
let requests: Array<{ url: string; method: string; body: any }>;
let unexpected: string[];
let xhr: ReturnType<typeof vi.spyOn>, fetch: ReturnType<typeof vi.spyOn>;
const draft = vi.fn();
const latest = () => draft.mock.calls.at(-1)![0];
const params = () => latest().native_job_request.params;
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
beforeEach(() => {
    requests = []; unexpected = []; prior = api.defaults.adapter; draft.mockClear();
    localStorage.clear(); sessionStorage.clear();
    xhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No network'); });
    fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? ''; const body = config.data ? JSON.parse(config.data) : undefined;
        requests.push({ url, method: config.method ?? 'get', body });
        let data: any;
        if (url === '/api/models/protein_modification_experimental/generation-settings') {
            const model = config.params.generator;
            data = inventories[model];
        } else if (/^\/api\/models\/(rfd4_proteina|complexa)\/generation-settings$/.test(url)) data = inventories[url.split('/')[3]][config.params.mode];
        else if (url === '/api/jobs' && config.method === 'post') data = { id: 'inert-job' };
        else if (['/api/models', '/api/input-presets', '/api/inputs/presets', '/api/execution-targets'].includes(url)) data = [];
        else if (/^\/api\/models\/[^/]+$/.test(url)) data = { id: url.split('/').at(-1), params: [], modes: [], engine_references: {} };
        else { unexpected.push(`${config.method} ${url}`); throw new Error(`Unmatched HTTP: ${url}`); }
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => {
    await unmount(); api.defaults.adapter = prior; xhr.mockRestore(); fetch.mockRestore(); expect(unexpected).toEqual([]);
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function mount(initialValues: Record<string, unknown>) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate onBack={() => {}} initialValues={initialValues} onDraftChange={draft} /></MemoryRouter></QueryClientProvider>));
    await settle();
}
function control(name: string): HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement {
    const direct = document.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${name}"]`);
    if (direct) return direct;
    const label = [...document.querySelectorAll('label')].find(el => [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim() === name);
    const node = label?.querySelector('input, select, textarea'); expect(node, name).toBeTruthy();
    return node as ReturnType<typeof control>;
}
async function edit(name: string, value: string) {
    const node = control(name);
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
    await settle();
}
async function click(text: string) {
    const node = [...document.querySelectorAll('button')].find(el => el.textContent?.trim() === text); expect(node, text).toBeTruthy();
    await act(async () => node!.click()); await settle();
}
async function reset(label: string) {
    const node = document.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`); expect(node, label).toBeTruthy();
    await act(async () => node!.click());
}
async function disclosures() {
    // Demand-mounted bodies may contain further disclosures.
    for (let pass = 0; pass < 3; pass++) await act(async () => {
        for (const node of document.querySelectorAll('details')) if (!node.open) { node.open = true; node.dispatchEvent(new Event('toggle')); }
    });
}

it.each([false, true])('edits the historical La winner (JSON string=%s), retains siblings and cold reopens', async string => {
    const historical = { num_steps: 600, self_cond: false, local_latents_gt_clamp_val: null };
    await mount({ generator: 'laproteina', target_lengths: '120', laproteina_num_steps: 400, laproteina_settings: string ? JSON.stringify(historical) : historical });
    await click('Sampling'); expect(control('Sampling steps').value).toBe('600');
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject(historical);
    await edit('Sampling steps', '450');
    expect(latest().laproteina_num_steps).toBe(400);
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ ...historical, num_steps: 450 });
    await click('Generate candidates');
    const wire = requests.filter(r => r.url === '/api/jobs' && r.method === 'post').at(-1)!.body;
    expect(project(wire.params).pcad_laproteina_settings_json.num_steps).toBe(450);
    expect(wire.params).not.toHaveProperty('de_novo_drafts');
    const saved = JSON.parse(JSON.stringify(latest())); await unmount(); await mount(saved); await click('Sampling');
    expect(control('Sampling steps').value).toBe('450');
    await reset('Use primary value for num_steps'); expect(control('Sampling steps').value).toBe('400');
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ num_steps: 400, self_cond: false, local_latents_gt_clamp_val: null });
});

it('resets only the winning layer, accurately exposing historical then flat values', async () => {
    await mount({ generator: 'laproteina', target_lengths: '120', laproteina_num_steps: 400, laproteina_settings: { num_steps: 600, self_cond: false }, pcad_laproteina_settings_json: { num_steps: 700, seed: 0 } });
    await click('Sampling'); expect(control('Sampling steps').value).toBe('700');
    await edit('Sampling steps', '750'); expect(project(params()).pcad_laproteina_settings_json.num_steps).toBe(750);
    expect(latest().laproteina_settings.num_steps).toBe(600);
    await reset('Use retained override for num_steps'); expect(control('Sampling steps').value).toBe('600');
    expect(project(params()).pcad_laproteina_settings_json.num_steps).toBe(600);
    await edit('Sampling steps', '650'); expect(project(params()).pcad_laproteina_settings_json.num_steps).toBe(650);
    await reset('Use primary value for num_steps'); expect(control('Sampling steps').value).toBe('400');
    expect(latest().pcad_laproteina_settings_json).toEqual({ seed: 0 });
    expect(latest().laproteina_settings).toEqual({ self_cond: false });
});

it('uses one effective DISCO editor for experiment, effort, count, attention and exact seeds', async () => {
    await mount({ generator: 'disco', target_lengths: '120', disco_seeds: '8,9', disco_settings: JSON.stringify({ experiment: 'diverse', effort: 'max', num_inference_seeds: null, seeds: [0, 2], use_deepspeed_evo_attention: false, deterministic: false }), pcad_disco_settings_json: { seeds: [] } });
    await click('Sampling'); await disclosures();
    expect(control('DISCO experiment').value).toBe('diverse'); expect(control('Inference effort').value).toBe('max');
    expect(control('Inference seed count').value).toBe(''); expect(control('Exact seeds').value).toBe('');
    expect((control('DeepSpeed Evoformer attention') as HTMLInputElement).checked).toBe(false);
    for (const key of ['experiment', 'effort', 'num_inference_seeds', 'seeds', 'use_deepspeed_evo_attention']) expect(document.querySelectorAll(`[data-native-setting="${key}"]`)).toHaveLength(0);
    expect([...document.querySelectorAll('label')].filter(n => n.firstChild?.textContent?.trim() === 'Exact seeds')).toHaveLength(1);
    await edit('Exact seeds', '0,'); expect(control('Exact seeds').value).toBe('0,');
    await edit('Exact seeds', '0,3'); await edit('Inference seed count', '0'); await edit('Inference effort', 'fast'); await edit('DISCO experiment', 'designable');
    await act(async () => control('DeepSpeed Evoformer attention').click());
    expect(project(params()).pcad_disco_settings_json).toMatchObject({ experiment: 'designable', effort: 'fast', num_inference_seeds: 0, seeds: [0, 3], use_deepspeed_evo_attention: true, deterministic: false });
    await reset('Use retained override for seeds'); expect(control('Exact seeds').value).toBe('0,2');
    await edit('Exact seeds', ''); expect(project(params()).pcad_disco_settings_json.seeds).toEqual([]);
    await reset('Use primary value for seeds'); expect(control('Exact seeds').value).toBe('8,9');
    expect(project(params()).pcad_disco_settings_json.seeds).toEqual([8, 9]);
    await click('Set explicit null for seeds'); expect(project(params()).pcad_disco_settings_json.seeds).toBeNull();
    await click('Set empty seeds list'); expect(project(params()).pcad_disco_settings_json.seeds).toEqual([]);
});

it.each([{ seeds: null }, { seeds: [] }])('retains flat DISCO seeds $seeds and pcad flat aliases without authoring a native map', async ({ seeds }) => {
    await mount({ generator: 'disco', target_lengths: '120', pcad_disco_seeds: seeds, pcad_disco_num_inference_seeds: null, pcad_disco_effort: 'max', pcad_disco_use_deepspeed_evo_attention: false });
    expect(project(params()).pcad_disco_settings_json).toMatchObject({ seeds, num_inference_seeds: null, effort: 'max', use_deepspeed_evo_attention: false });
    expect(params()).not.toHaveProperty('pcad_disco_settings_json');
    await edit('Job name', 'unrelated');
    expect(project(params()).pcad_disco_settings_json.seeds).toEqual(seeds);
});

it('retains nullable and zero flat motif aliases even outside the motif task', async () => {
    await mount({ generator: 'laproteina', target_lengths: '120', pcad_laproteina_motif_min_length: null, pcad_laproteina_motif_max_length: 0, pcad_laproteina_motif_atom_spec: null, pcad_laproteina_contig_string: '5/A1/5' });
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ motif_min_length: null, motif_max_length: 0, motif_atom_spec: null, contig_string: '5/A1/5' });
    await edit('Job name', 'unrelated');
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ motif_min_length: null, motif_max_length: 0, motif_atom_spec: null });
});

it('does not resurrect a flat seed alias after a primary clear and cold reopen', async () => {
    await mount({ generator: 'disco', target_lengths: '120', pcad_disco_seeds: '0,2' });
    expect(control('Exact seeds').value).toBe('0,2');
    await edit('Exact seeds', '');
    expect(project(params()).pcad_disco_settings_json).not.toHaveProperty('seeds');
    const saved = JSON.parse(JSON.stringify(latest())); await unmount(); await mount(saved);
    expect(control('Exact seeds').value).toBe('');
    expect(project(params()).pcad_disco_settings_json).not.toHaveProperty('seeds');
    expect(latest().pcad_disco_seeds).toBe('0,2'); // Editor ancestry is retained, not reapplied.
});

it('honors canonical flat presence over pcad aliases, including zero and false', async () => {
    await mount({ generator: 'disco', target_lengths: '120', disco_num_inference_seeds: 0, pcad_disco_num_inference_seeds: 4, disco_use_deepspeed_evo_attention: false, pcad_disco_use_deepspeed_evo_attention: true });
    expect(control('Inference seed count').value).toBe('0');
    expect(project(params()).pcad_disco_settings_json).toMatchObject({ num_inference_seeds: 0, use_deepspeed_evo_attention: false });
});

it('shows historical motif overrides, authors deliberate edits, and resets named motifs to inheritance', async () => {
    await mount({ generator: 'laproteina', design_task: 'motif_scaffolding', target_lengths: '120', pcad_laproteina_motif_task_name: 'retained-task', pcad_laproteina_contig_string: '5/A1/5', pcad_laproteina_motif_atom_spec: null, laproteina_settings: { contig_string: '10/A1/10', motif_min_length: null, atom_selection_mode: 'ca' } });
    expect(control('La native motif task').value).toBe('retained-task');
    expect(control('Motif contig').value).toBe('10/A1/10'); expect(control('Atom selection').value).toBe('ca');
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ contig_string: '10/A1/10', motif_min_length: null });
    await edit('Motif contig', '20/A1/20'); expect(project(params()).pcad_laproteina_settings_json.contig_string).toBe('20/A1/20');
    await reset('Use retained override for contig_string'); expect(control('Motif contig').value).toBe('10/A1/10');
    await reset('Use named motif profile for contig_string'); expect(control('Motif contig').value).toBe('');
    expect(project(params()).pcad_laproteina_settings_json).not.toHaveProperty('contig_string');
    expect(latest().laproteina_contig_string).toBe('5/A1/5');
    expect(document.body.textContent).toContain('retained flat motif values are not requested overrides');
});

it('preserves aliases, sparse maps and inactive five-engine drafts through navigation and serialized reopen', async () => {
    await mount({ generator: 'laproteina', target_lengths: '120', pcad_laproteina_num_steps: 0, laproteina_settings: { self_cond: false }, de_novo_drafts: { 'de_novo_design:disco:unconditional': { target_lengths: '140', disco_settings: { seeds: [0], num_inference_seeds: null } } } });
    const la = JSON.stringify(params());
    await click('Advanced'); await click('Simple'); expect(JSON.stringify(params())).toBe(la);
    for (const engine of ['rfd4_proteina', 'complexa', 'rfd3', 'disco']) await edit('Engine', engine);
    expect(project(params()).pcad_disco_settings_json).toMatchObject({ seeds: [0], num_inference_seeds: null });
    const saved = JSON.parse(JSON.stringify(latest())); await unmount(); await mount(saved); await edit('Engine', 'laproteina');
    expect(control('Sampling steps').value).toBe('0'); expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ num_steps: 0, self_cond: false });
    expect(params()).not.toHaveProperty('pcad_laproteina_settings_json');
    expect(Object.keys(latest().de_novo_drafts).some(key => key.includes('complexa'))).toBe(true);
    expect(Object.keys(latest().de_novo_drafts).some(key => key.includes('rfd4_proteina'))).toBe(true);
});

it.each(['generate', 'evaluate'])('has one RFD4 amount owner and preserves native reachability in %s', async mode => {
    await mount({ generator: 'rfd4_proteina', native_mode: mode });
    if (mode === 'generate') await edit('RFD4 task', 'source_free');
    await click('Advanced'); await disclosures();
    expect(document.body.textContent).not.toContain('Primary amount overrides');
    if (mode === 'generate') {
        for (const label of ['Designs per input', 'Batch size', 'Seed']) expect(document.querySelectorAll(`input[aria-label="${label}"]`)).toHaveLength(1);
        await edit('Seed', '0'); await edit('Batch size', '2'); await edit('Designs per input', '3');
        expect(latest().rfd4_request.settings).toMatchObject({ seed: 0, batch_size: 2, num_replicates: 3 });
    } else {
        // The actual native evaluation schema excludes these fields; they are
        // not valid controls requiring an alternative expert owner.
        for (const key of ['seed', 'batch_size', 'num_replicates']) expect(inventories.rfd4_proteina.evaluate.parameter_schema.properties.settings.properties).not.toHaveProperty(key);
        expect(document.querySelectorAll('input[aria-label="Seed"]')).toHaveLength(0);
    }
});
