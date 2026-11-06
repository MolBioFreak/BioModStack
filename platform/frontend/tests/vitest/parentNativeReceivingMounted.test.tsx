import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { readFileSync, writeFileSync } from 'node:fs';
import { projectRfd3SourceSelection } from '../../src/lib/rfd3SourceSelection';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { api } from '../../src/lib/api';

// Real parent, discovery editors, draft owners and submitJob/Axios interceptors.
// Only placement and WebGL are inert; no server persistence/inference is claimed.
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: any) => <output data-viewer>{JSON.stringify(props)}</output> }));
const python = process.env.BMS_TEST_API_PYTHON ?? process.env.BMS_SCHEMA_PYTHON ?? 'python';
const cwd = resolve(process.cwd(), '../..');
function native(code: string, value?: unknown) {
    return JSON.parse(execFileSync(python, ['-c', code], { cwd, input: value === undefined ? undefined : JSON.stringify(value), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 }));
}
const inventories = native("import sys,json,asyncio;sys.path.insert(0,'platform/api');from routers.models import get_generation_settings;print(json.dumps({k:asyncio.run(get_generation_settings('protein_modification_experimental','de_novo_design',k,'motif_scaffolding' if k=='laproteina' else 'unconditional_monomer')) for k in ('rfd3','laproteina','disco')}))");
const receipt = JSON.parse(readFileSync(process.env.BMS_PARENT_RECEIPT_WIRE!, 'utf8'));
const detail = native("import sys,json,asyncio;sys.path.insert(0,'platform/api');from routers.models import get_model;print(json.dumps(asyncio.run(get_model('protein_modification_experimental'))))");
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
    fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
        if (String(input) === `/api/files/download/${encodeURIComponent(receipt.path)}`) return { ok: true, text: async () => readFileSync(receipt.path, 'utf8') } as Response;
        unexpected.push(`fetch ${String(input)}`); throw new Error('No network');
    });
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
        else if (/^\/api\/models\/[^/]+$/.test(url)) data = detail;
        else if (url === '/api/files/materialize-structure') data = receipt;
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
    if (initialValues.generator === 'rfd3') {
        const details = [...document.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent?.includes('Input structure'));
        expect(details).toBeTruthy();
        await act(async () => { details!.open = true; details!.dispatchEvent(new Event('toggle')); });
        await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-viewer]')).not.toBeNull(); });
    }
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


it('real parent receives selected-MODEL derivative picks, Apply/Undo only nested conditioning, inspection highlights exact observed picks', async () => {
    const initial = { generator: 'rfd3', input_structure: receipt.original_path, source_model: 2, source_mapping: 'bms.rfd3.source-map.v1', min_length: 100, max_length: 100, conditioning: { select_unfixed_sequence: false, select_hotspots: null } };
    await mount(initial);
    const before = JSON.parse(JSON.stringify(params()));
    const pick = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label')?.includes('author AA:42B'))!;
    expect(pick).toBeTruthy();
    await act(async () => pick.click()); await settle();
    expect(params()).toEqual(before);
    await click('Apply RFD3 selection');
    expect(params().conditioning).toEqual({ ...initial.conditioning, select_fixed_atoms: { AA42B: ['CAX', 'NX'] } });
    expect(params()).not.toHaveProperty('select_fixed_atoms');
    if (process.env.BMS_PARENT_SELECTION_WIRE) writeFileSync(process.env.BMS_PARENT_SELECTION_WIRE, JSON.stringify({ params: params(), receipt }));
    // Change temporary picks, then inspect the applied native selector observation.
    const other = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label')?.includes('author DNA:90'))!;
    await act(async () => other.click());
    await click('Inspect select_fixed_atoms');
    const viewer = JSON.parse(document.querySelector('[data-viewer]')!.textContent!);
    expect(viewer.structureDocumentId).toBe(receipt.path);
    expect(viewer.residueSelections).toMatchObject([{ documentId: receipt.path, modelNumber: 2, authAsymId: 'AA', authSeqId: 42, insertionCode: 'B' }]);
    expect(pick.getAttribute('aria-pressed')).toBe('true');
    await click('Undo RFD3 selection'); expect(params()).toEqual(before);
    await click('Inspect select_hotspots'); expect(document.body.textContent).toContain('not evaluated in the browser');
});
it.each(['inputPath', 'sourceModel', 'provenance', 'documentId'])('exact retained binding rejects stale %s without relabeling the inspector', key => {
    // The complete parent cold-reopen case below qualifies durable observations.
    const identity = { documentId: receipt.path, modelNumber: 2, authAsymId: 'AA', authSeqId: 42, insertionCode: 'B' };
    const visual: any = { identities: [identity], inputPath: receipt.original_path, sourceModel: 2, sourceMapping: 'bms.rfd3.source-map.v1', source: { documentId: receipt.path, source_positions: receipt.source_positions, model_number: 2 }, retainedBinding: { receipt, inputPath: receipt.original_path, sourceModel: 2 } };
    expect(projectRfd3SourceSelection(visual)).toHaveProperty('residues', 'AA42B');
    if (key === 'inputPath') visual.inputPath = 'different-original.cif';
    if (key === 'sourceModel') visual.sourceModel = 1;
    if (key === 'provenance') visual.retainedBinding = { ...visual.retainedBinding, receipt: { ...receipt, selected_sha256: '0'.repeat(64) } };
    if (key === 'documentId') visual.source.documentId = receipt.original_path;
    expect(projectRfd3SourceSelection(visual)).toHaveProperty('issue');
    expect(identity.documentId).toBe(receipt.path);
});
it('parent retains exact observed selectors across cold reopen without adding scientific params', async () => {
    await mount({ generator: 'rfd3', input_structure: receipt.original_path, source_model: 2, source_mapping: 'bms.rfd3.source-map.v1' });
    const pick = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label')?.includes('author AA:42B'))!;
    await act(async () => pick.click()); await click('Apply RFD3 selection');
    const saved = JSON.parse(JSON.stringify(latest()));
    expect(saved.rfd3_selection_observations.select_fixed_atoms.source.consumedSha256).toBe(receipt.sha256);
    expect(params()).not.toHaveProperty('rfd3_selection_observations');
    const scientific = JSON.parse(JSON.stringify(params()));
    await unmount(); await mount(saved); await click('Inspect select_fixed_atoms');
    const viewer = JSON.parse(document.querySelector('[data-viewer]')!.textContent!);
    expect(viewer.residueSelections).toMatchObject([{ documentId: receipt.path, modelNumber: 2, authAsymId: 'AA', authSeqId: 42, insertionCode: 'B' }]);
    expect(params()).toEqual(scientific);
});
it('mapping-off manual conditioning remains literal and visual Apply is observational', async () => {
    const conditioning = { contig: 'manual native expression', select_hbond_donor: true };
    await mount({ generator: 'rfd3', input_structure: receipt.original_path, source_model: 2, source_mapping: 'off', conditioning });
    const pick = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label')?.includes('author AA:42B'))!;
    await act(async () => pick.click());
    await click('Apply RFD3 selection'); expect(params().conditioning).toEqual(conditioning);
    expect(document.body.textContent).toContain('Historical mapping-off');
});
it.each([false, true])('single La task editor respects historical/newest precedence (newest=%s), sparse dictionary inheritance, save/reopen', async newest => {
    const initial: any = { generator: 'laproteina', design_task: 'motif_scaffolding', target_lengths: '100', laproteina_motif_task_name: '1PRW_AA', laproteina_settings: { motif_task_name: '1YCR_AA', self_cond: false }, ...(newest ? { pcad_laproteina_settings_json: { motif_task_name: '1PRW_AA_TIP', seed: 0 } } : {}) };
    await mount(initial);
    expect(document.querySelectorAll('[aria-label="La native motif task"]')).toHaveLength(1);
    expect(control('La native motif task').value).toBe(newest ? '1PRW_AA_TIP' : '1YCR_AA');
    expect(document.querySelectorAll('datalist option')).toHaveLength(52);
    await edit('La native motif task', '1BCF_AA');
    expect(project(params()).pcad_laproteina_settings_json).toMatchObject({ motif_task_name: '1BCF_AA', self_cond: false });
    expect(project(params()).pcad_laproteina_settings_json).not.toHaveProperty('contig_string');
    expect(project(params()).pcad_laproteina_settings_json).not.toHaveProperty('atom_selection_mode');
    expect(latest().laproteina_motif_task_name).toBe('1PRW_AA');
    const saved = JSON.parse(JSON.stringify(latest())); await unmount(); await mount(saved);
    expect(control('La native motif task').value).toBe('1BCF_AA');
    await reset(newest ? 'Use retained override for motif_task_name' : 'Use primary value for motif_task_name');
    expect(control('La native motif task').value).toBe(newest ? '1YCR_AA' : '1PRW_AA');
    expect(project(params()).pcad_laproteina_settings_json.self_cond).toBe(false);
});

it.each([undefined, null])('parent visual Undo restores exact conditioning presence (%s)', async conditioning => {
    const initial: any = { generator: 'rfd3', input_structure: receipt.original_path, source_model: 2, source_mapping: 'bms.rfd3.source-map.v1', ...(conditioning === undefined ? {} : { conditioning }) };
    await mount(initial);
    const before = JSON.parse(JSON.stringify(latest()));
    const pick = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.getAttribute('aria-label')?.includes('author AA:42B'))!;
    await act(async () => pick.click()); await click('Apply RFD3 selection');
    expect(params().conditioning.select_fixed_atoms).toEqual({ AA42B: ['CAX', 'NX'] });
    await click('Undo RFD3 selection');
    expect(Object.hasOwn(latest(), 'conditioning')).toBe(Object.hasOwn(before, 'conditioning'));
    expect(latest().conditioning).toEqual(before.conditioning);
});
