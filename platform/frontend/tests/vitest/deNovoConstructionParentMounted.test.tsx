import React, { act } from 'react';
import { webcrypto } from 'node:crypto';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { api } from '../../src/lib/api';
import { deNovoAuthoringEnvelope } from '../../src/lib/deNovoAuthoring';
import { emptyConstructionDraft } from '../../src/lib/molecularConstruction';

// Real parent, workbench, child serializers and native graph ASGI transport.
// Only placement/graphics/optional peers and Job execution are inert.
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
const repo = resolve('../..');
const python = process.env.BMS_TEST_PYTHON ?? 'python';
const graphPython = process.env.BMS_CONSTRUCTION_PYTHON;
if (!graphPython) throw Error('BMS_CONSTRUCTION_PYTHON must select the isolated native graph interpreter');
const inventories = JSON.parse(execFileSync(python, ['-c', `
import sys,json,asyncio
sys.path.insert(0,'platform/api')
from routers.models import get_model,get_generation_settings
from services.rfd4_proteina import generation_settings
from services.complexa import discovery
async def capture():
    result = {'details': {k: await get_model(k) for k in ('protein_modification_experimental','rfd4_proteina','complexa')}}
    result['rfd4_proteina'] = generation_settings('generate')
    result['complexa'] = discovery()
    for engine in ('rfd3','disco','laproteina'):
        result[engine] = {task:await get_generation_settings('protein_modification_experimental','de_novo_design',engine,task) for task in ('unconditional','unconditional_monomer','dna_conditioned','rna_conditioned')}
    return result
print(json.dumps(asyncio.run(capture())))
`], { cwd: repo, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 }));
function native(method: string, url: string, body?: string) {
    return JSON.parse(execFileSync(graphPython!, ['platform/api/tests/molecular_construction_ui_transport.py'], {
        cwd: repo, encoding: 'utf8', input: JSON.stringify({ method, url, body }),
        env: { ...process.env, PYTHONPATH: `${repo}:${repo}/platform/api` },
    }));
}
const schema = native('GET', '/api/molecular-construction/schema').data;
const scientific = { input: { kind: 'unconditional', name: 'construction-parent', chains: [{ chain_id: 'A', min_length: 37, max_length: 37 }] }, settings: { seed: 0, compress_outputs: false } };
const recipe = () => ({ ...emptyConstructionDraft(), imported_note: { zero: 0, flag: false, empty: [], nil: null }, editor: {
    graph_provider: 'rdkit', phosphate_state: 'neutral_acid', geometry: 'unpositioned',
    strands: [{ id: 'strand-a', polymer: 'DNA', copy_ids: ['copy-a'], residues: [{ id: 'r1', base: 'A' }, { id: 'r2', base: 'C' }], five_prime: 'hydroxyl', three_prime: 'hydroxyl' }],
} });
let root: Root | undefined, client: QueryClient, latest: any;
let calls: Array<{ url: string; method: string; body: any }>;
let unexpected: string[], failSchema: boolean, failGraph: boolean;
let previous: typeof api.defaults.adapter;
const manager = vi.fn();
const settle = () => act(async () => { await new Promise(yes => setTimeout(yes, 10)); });
const wait = async (check: () => void) => vi.waitFor(async () => { await settle(); check(); });
beforeEach(() => {
    calls = []; unexpected = []; latest = undefined; failSchema = false; failGraph = false; manager.mockClear();
    localStorage.clear(); sessionStorage.clear(); previous = api.defaults.adapter;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => { unexpected.push(`fetch:${String(input)}`); throw Error('No network'); });
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw Error('No network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? '', method = config.method ?? 'get';
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, method, body }); let data: unknown;
        if (url === '/api/molecular-construction/schema') { if (failSchema) throw Error('schema unavailable'); data = schema; }
        else if (url.startsWith('/api/molecular-construction/')) {
            if (failGraph) throw Error('graph provider unavailable');
            const result = native(method.toUpperCase(), url, config.data);
            if (result.status >= 400) throw Error(JSON.stringify(result));
            data = result.data;
        } else if (url === '/api/models/protein_modification_experimental/generation-settings') data = inventories[config.params.generator][config.params.task ?? 'unconditional'];
        else if (url === '/api/models/rfd4_proteina/generation-settings') data = inventories.rfd4_proteina;
        else if (url === '/api/models/complexa/generation-settings') data = inventories.complexa;
        else if (/^\/api\/models\/[^/]+$/.test(url)) data = inventories.details[url.split('/').at(-1)!];
        else if (url === '/api/jobs' && method === 'post') data = { id: 'inert-no-scientific-job' };
        else { unexpected.push(`${method}:${url}`); throw Error(`Unexpected ${url}`); }
        expect(data, `response for ${url}`).not.toBeUndefined();
        return { data, config, status: 200, statusText: 'OK', headers: {} };
    };
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await unmount(); api.defaults.adapter = previous; vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected).toEqual([]); });
async function mount(values: Record<string, unknown> = {}) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate
        onBack={() => {}} initialValues={{ generator: 'rfd4_proteina', rfd4_request: scientific, sequence_design: null, sequence_design_null: true, target_length_list: '37', ...values }}
        onDraftChange={value => { latest = value; }} onOpenTemplateManager={manager} />
    </MemoryRouter></QueryClientProvider>));
    await wait(() => expect(latest?.native_job_request).toBeTruthy());
}
async function open() {
    const detail = document.querySelector<HTMLDetailsElement>('[data-molecular-construction-panel]')!;
    expect(detail).toBeTruthy();
    await act(async () => { detail.open = true; detail.dispatchEvent(new Event('toggle')); });
    await wait(() => expect(document.querySelector('[aria-label="Molecular construction workbench"]')).not.toBeNull());
}
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => n.textContent?.trim() === text);
    expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle();
}
function control(label: string) {
    return document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`) ??
        [...document.querySelectorAll('label')].find(node => [...node.childNodes].filter(c => c.nodeType === Node.TEXT_NODE).map(c => c.textContent).join('').trim() === label)?.querySelector<HTMLInputElement | HTMLSelectElement>('input,select');
}
async function edit(label: string, value: string) {
    const node = control(label); expect(node, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node!.dispatchEvent(new Event(node!.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
    await settle();
}

it.each(['rfd4_proteina', 'rfd3', 'disco', 'laproteina', 'complexa'])('shared optional construction leaves untouched %s request byte-identical', async generator => {
    await mount({ generator, complexa_request: { task: 'protein_binder', task_name: 'unchanged', seed: 0, reward: null } });
    const before = JSON.stringify(latest.native_job_request);
    expect(calls.filter(c => c.url.startsWith('/api/molecular-'))).toEqual([]);
    await open();
    await wait(() => expect(control('Graph provider')).toBeTruthy());
    expect(JSON.stringify(latest.native_job_request)).toBe(before);
    expect(latest).not.toHaveProperty('molecular_construction');
    expect(calls.filter(c => c.method !== 'get')).toEqual([]);
});
it.each(['dna_conditioned', 'rna_conditioned'])('untouched DISCO %s shorthand retains exact request and historical complement behavior', async design_task => {
    await mount({ generator: 'disco', design_task, disco_na_sequence: design_task === 'dna_conditioned' ? 'ACGT' : 'ACGU' });
    const before = JSON.stringify(latest.native_job_request);
    await open(); await wait(() => expect(control('Graph provider')).toBeTruthy());
    expect(JSON.stringify(latest.native_job_request)).toBe(before);
    expect(calls.filter(c => c.method !== 'get')).toEqual([]);
});
it('incomplete recipe stays shared across engine/goal switches and Template manager without entering science', async () => {
    await mount(); const original = structuredClone(latest.native_job_request); await open();
    await click('Add independently authored strand'); await edit('Strand 1 ID', 'unfinished');
    const retained = structuredClone(latest.molecular_construction);
    expect(retained.editor.strands[0]).toMatchObject({ id: 'unfinished', residues: [], polymer: '' });
    expect(latest.native_job_request).toEqual(original);
    await edit('Engine', 'disco'); await edit('Goal', 'rna_conditioned'); await edit('Engine', 'rfd3'); await edit('Engine', 'rfd4_proteina');
    await wait(() => expect(latest.generator).toBe('rfd4_proteina'));
    expect(latest.molecular_construction).toEqual(retained);
    expect(latest.native_job_request).toEqual(original);
    expect(JSON.stringify(latest.native_job_request)).not.toContain('molecular_construction');
    expect(Object.values(latest.de_novo_drafts).some((d: any) => Object.hasOwn(d, 'molecular_construction'))).toBe(false);
    await click('Template Manager');
    expect(manager.mock.calls.at(-1)![0].currentParams.molecular_construction).toEqual(retained);
    expect(deNovoAuthoringEnvelope(latest)).toMatchObject({ editor_state: { molecular_construction: retained }, native_job_request: original });
});
it('real graph and Undo survive JSON cold reopen and repeated parent Save without construction replay', async () => {
    await mount({ molecular_construction: recipe() }); await open(); await click('Build connected graph');
    await wait(() => expect(latest.molecular_construction.graph?.atoms.length).toBeGreaterThan(0));
    const saved = structuredClone(latest), graph = saved.molecular_construction.graph;
    expect(graph.coordinates).toBeNull(); expect(graph.connected_components).toHaveLength(1);
    await edit('Strand 1 sequence', 'AG');
    expect(latest.molecular_construction.graph).toBeUndefined();
    await click('Undo construction edit');
    expect(latest.molecular_construction.graph).toEqual(graph);
    expect(latest.molecular_construction.imported_note).toEqual({ zero: 0, flag: false, empty: [], nil: null });
    await click('Template Manager'); await click('Template Manager');
    expect(manager.mock.calls[0][0].currentParams).toEqual(manager.mock.calls[1][0].currentParams);
    const envelope = JSON.parse(JSON.stringify(deNovoAuthoringEnvelope(latest)));
    expect(envelope.native_job_request.params).not.toHaveProperty('molecular_construction');
    if (process.env.BMS_CONSTRUCTION_PARENT_WIRE) writeFileSync(process.env.BMS_CONSTRUCTION_PARENT_WIRE, JSON.stringify(envelope));
    await unmount(); const count = calls.filter(c => c.method !== 'get').length;
    await mount(envelope.editor_state); await open();
    expect(latest.molecular_construction).toEqual(envelope.editor_state.molecular_construction);
    expect(calls.filter(c => c.method !== 'get')).toHaveLength(count);
    expect(JSON.stringify(latest.native_job_request)).toBe(JSON.stringify(saved.native_job_request));
});
it.skipIf(!process.env.BMS_PREPARED_NATIVE_WIRE)('explicit prepared adoption survives construction invalidation, engine switch and parent cold persistence', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const construction = JSON.parse(readFileSync(process.env.BMS_PREPARED_NATIVE_WIRE!, 'utf8'));
    const geometry = construction.geometry.result;
    const previousAdapter = api.defaults.adapter as (config: any) => Promise<any>;
    api.defaults.adapter = async config => {
        const url = config.url;
        if (url === '/api/files/upload-immutable-structure') {
            expect(config.data.get('path')).toBe('inputs/protein_local_redesign');
            expect(config.data.get('sha256')).toBe(geometry.coordinate_sha256);
            return { data: { path: `inputs/protein_local_redesign/constructed-${geometry.coordinate_sha256}.pdb`, sha256: geometry.coordinate_sha256, size: geometry.coordinate_size_bytes }, config, status: 200, statusText: 'OK', headers: {} };
        }
        if (url === '/api/files/materialize-structure') {
            const source = JSON.parse(config.data);
            return { data: { source_structure: source, path: source.path, format: 'pdb', native_format: 'pdb', sha256: geometry.coordinate_sha256, author_residues: [], model_numbers: [1] }, config, status: 200, statusText: 'OK', headers: {} };
        }
        if (url === '/api/molecular-geometry/schema') {
            const result = JSON.parse(execFileSync(graphPython!, ['platform/api/tests/molecular_geometry_ui_transport.py'], { cwd: repo, encoding: 'utf8', input: JSON.stringify({ method: 'GET', url }), env: { ...process.env, PYTHONPATH: `${repo}:${repo}/platform/api` } }));
            return { data: result.data, config, status: 200, statusText: 'OK', headers: {} };
        }
        return previousAdapter(config);
    };
    vi.mocked(fetch).mockImplementation(async () => ({ ok: true, arrayBuffer: async () => new TextEncoder().encode(geometry.coordinate_text).buffer }) as Response);
    await mount({ molecular_construction: construction, rfd4_request: { input: { kind: 'annotated', slots: [] }, settings: { seed: 0 } } });
    expect(latest.prepared_geometry_documents).toBeUndefined();
    await click('Choose / import document'); await click('Use constructed geometry');
    await wait(() => expect(Object.keys(latest.prepared_geometry_documents ?? {})).toHaveLength(1));
    const retained = structuredClone(latest.prepared_geometry_documents);
    expect(Object.values(retained)[0]).toMatchObject({ construction, geometry, format: 'pdb' });
    expect(latest.rfd4_request.input.slots).toHaveLength(1);
    const adopted = structuredClone(latest.native_sources);
    expect(JSON.stringify(latest.native_job_request)).not.toContain('prepared_geometry_documents');
    await open(); await edit('Strand 1 sequence', 'AGGT');
    expect(latest.molecular_construction.geometry.result).toBeUndefined();
    expect(latest.prepared_geometry_documents).toEqual(retained);
    await edit('Engine', 'disco'); await edit('Engine', 'rfd4_proteina');
    expect(latest.native_sources).toEqual(adopted);
    await click('Template Manager'); await click('Template Manager');
    expect(manager.mock.calls[0][0].currentParams.prepared_geometry_documents).toEqual(retained);
    expect(manager.mock.calls[1][0].currentParams).toEqual(manager.mock.calls[0][0].currentParams);
    const envelope = JSON.parse(JSON.stringify(deNovoAuthoringEnvelope(latest)));
    if (process.env.BMS_PREPARED_PARENT_WIRE) writeFileSync(process.env.BMS_PREPARED_PARENT_WIRE, JSON.stringify(envelope));
    await unmount(); await mount(envelope.editor_state);
    expect(latest.prepared_geometry_documents).toEqual(retained);
    expect(latest.native_sources).toEqual(adopted);
});

it('unavailable optional schema and graph never disable source-free Generate or mutate its request', async () => {
    await mount({ molecular_construction: recipe() }); const before = structuredClone(latest.native_job_request);
    failSchema = true; await open(); await wait(() => expect(document.body.textContent).toContain('schema unavailable'));
    failGraph = true; await click('Build connected graph');
    expect(document.body.textContent).toContain('graph provider unavailable');
    expect(latest.native_job_request).toEqual(before);
    const generate = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === 'Generate candidates')!;
    expect(generate.disabled).toBe(false); await click('Generate candidates');
    await wait(() => expect(calls.filter(c => c.url === '/api/jobs')).toHaveLength(1));
    expect(calls.find(c => c.url === '/api/jobs')!.body.params).toEqual(before.params);
});
