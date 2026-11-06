import React, { act } from 'react';
import recipes from './fixtures/deNovoContinuationRecipes.json';
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
const protenixSchema = JSON.parse(execFileSync(process.env.BMS_TEST_API_PYTHON ?? process.env.BMS_TEST_PYTHON ?? resolve('../api/.venv/bin/python'), ['-c', 'import json; from scripts.protenix_chemistry import parameter_schema; print(json.dumps(parameter_schema()))'], { cwd: resolve('../..'), encoding: 'utf8' }));
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { isolateDeNovoTransport } from './deNovoTransport';
import { api } from '../../src/lib/api';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { JobSubmission } from '../../src/components/JobSubmission';
// Resolve the actual lazy receiving modules during collection, before bounded
// UI waits. This keeps cold Vite transformation outside the interaction window.
import '../../src/components/MolecularDynamicsTemplate';
import '../../src/components/ProteinModificationTemplate';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import ShapeBlueprintTemplate from '../../src/components/ShapeBlueprintTemplate';
import { ThemeProvider } from '../../src/components/ThemeProvider';

// Offline transport fixtures, not inference, native-writer or API acceptance.
// All receiving/editor/serializer code is real. Only WebGL and HTTP are inert.
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div data-webgl /> }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => <div data-webgl /> }));
vi.mock('../../src/components/CanonicalMeshPreview', () => ({ default: () => <div data-webgl /> }));
vi.mock('react-plotly.js', () => ({ default: () => <div data-chart /> }));

const jobId = '11111111-1111-4111-8111-111111111111';
const candidate = { kind: 'native_document', job_id: jobId, artifact_id: 'alternate', target_state: 'B', model: 2 };
const documents = [
    { artifact_id: 'primary', primary: true, target_state: 'A', logical_path: 'primary.cif', download_url: '/fixture/primary.cif' },
    { artifact_id: 'alternate', target_state: 'B', logical_path: 'alternate.cif', download_url: '/fixture/alternate.cif' },
];
const source = { job_id: jobId, document: { artifact_id: 'alternate', target_state: 'B' }, model_number: 2 };
const materialized = {
    path: 'inputs/retained/alternate.pdb', format: 'pdb', native_format: 'pdb', sha256: 'a'.repeat(64), model_number: 2, model_numbers: [2],
    source_structure: source, native_recipe: recipes.simple,
    author_residues: [
        { model_number: 2, auth_asym_id: 'A', auth_seq_id: 1, residue_name: 'ALA' },
        { model_number: 2, auth_asym_id: 'A', auth_seq_id: 2, residue_name: 'GLY' },
    ],
};
const pdb = 'MODEL        2\nATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00 20.00           C  \nTER\nENDMDL\nEND\n';
let root: Root | undefined;
let client: QueryClient;
let restore: () => void;
let fallback: typeof api.defaults.adapter;
const posts: Array<{ url: string; body: any }> = [];
const unexpected: string[] = [];
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
async function close() { await act(async () => root?.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function mount(node: React.ReactNode, route = `/designs/${jobId}?native_candidate=native&artifact_id=alternate&target_state=B&model_number=2`) {
    window.history.replaceState({}, '', route);
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    await act(async () => root!.render(<MemoryRouter initialEntries={[route]}><QueryClientProvider client={client}><ThemeProvider>{node}</ThemeProvider></QueryClientProvider></MemoryRouter>));
    await settle();
}
async function click(label: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => n.textContent?.trim() === label);
    expect(node, `Missing button ${label}: ${document.body.textContent}`).toBeTruthy();
    await act(async () => node!.click()); await settle();
}
async function edit(node: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
    expect(node).toBeTruthy();
    const proto = node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); });
}
async function resultLink(label: string) {
    await mount(<NativeBinderGenerationResults jobId={jobId} artifactId="alternate" targetState="B" />);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Continue exact native document"]')).not.toBeNull(); });
    const link = [...document.querySelectorAll<HTMLAnchorElement>('a')].find(n => n.textContent === label);
    expect(link, document.body.textContent ?? '').toBeTruthy();
    const route = link!.getAttribute('href')!;
    await close();
    return route;
}
beforeEach(() => {
    localStorage.clear(); sessionStorage.clear(); posts.length = 0; unexpected.length = 0;
    restore = isolateDeNovoTransport(); fallback = api.defaults.adapter;
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    // jsdom has no layout/scroll implementation.
    HTMLElement.prototype.scrollIntoView = () => {};
    if (!File.prototype.text) Object.defineProperty(File.prototype, 'text', { configurable: true, value() { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    api.defaults.adapter = async config => {
        const url = String(config.url), body = config.data ? JSON.parse(String(config.data)) : undefined;
        let data: unknown;
        if (config.method === 'post') posts.push({ url, body });
        if (url === `/api/jobs/${jobId}/generation-results`) data = { records: [{ candidate_key: 'native', candidate_ref: candidate, metrics: {}, structures: documents }], total: 1, offset: 0, limit: 100, receipt: {}, publication: {}, artifacts: [] };
        else if (url === `/api/jobs/${jobId}`) data = { id: jobId, saved_selection_sets: [] };
        else if (url === `/api/jobs/${jobId}/binder-evidence`) data = { records: [], total: 0, offset: 0, limit: 100 };
        else if (url === `/api/binder-continuation/${jobId}/round`) data = { job_id: jobId, state: 'not_requested', steps: {}, errors: {} };
        else if (url === '/api/files/materialize-structure') data = materialized;
        else if (url === '/api/jobs' && config.method === 'post') throw Error('Offline capture: no Job created');
        else if (['/api/templates', '/api/user-templates'].includes(url)) data = [];
        else if (url === '/api/models/frustrampnn/integration') data = { workflows: {} };
        else if (url === '/api/models/protenix') data = { id: 'protenix', params: [], modes: [{ id: 'complex', parameter_schema: protenixSchema }] };
        else if (url === '/api/models/boltz2') data = { id: 'boltz2', params: [], modes: [] };
        else if (url === `/api/designs/by-job/${jobId}/binder-evidence`) data = { records: [], total: 0, offset: 0, limit: 100 };
        else if (url === '/api/gpu/status' || url === '/api/system/status') data = { gpus: [{ index: 0, name: 'Offline transport fixture GPU', memory_total_mb: 24000 }], gpu_error: null };
        else if (url === '/api/system/model-integration') data = { workflows: {} };
        else if (url === '/api/shape-blueprint/geometries') data = { geometries: [{ geometry_id: 'fixture-geometry', source_format: 'obj', source_parser: 'obj', source_unit: 'angstrom', angstrom_per_unit: 1, dimensions_angstrom: [20, 30, 40], geometry_sha256: 'b'.repeat(64), source_sha256: 'a'.repeat(64), manifest_sha256: 'c'.repeat(64), preview_obj_sha256: 'd'.repeat(64), point_pool_sha256: 'e'.repeat(64), sdf_sha256: 'f'.repeat(64), sdf_sign: 'negative_inside', sdf_grid_shape: [10, 10, 10], vertex_count: 8, face_count: 12, point_count: 10 }] };
        else if (url === '/api/shape-blueprint/requests') throw Error('Offline Shape capture: no Job created');
        else if (url === '/api/molecular-dynamics/chemistry-profiles') data = { schema: 'bms.md.chemistry-profile-inventory.v1', catalog_digest: 'c'.repeat(64), profiles: [], selectable_profile_ids: [], count: 0, bounded: true };
        else if (url === '/api/molecular-dynamics/starting-structures/inspect') data = {
            schema_version: 'bms.md.starting-structure-inspection.v1', source_ref: body.source_ref,
            identity: { label: 'Alternate MODEL 2 fixture', format: 'pdb', size_bytes: pdb.length, sha256: 'a'.repeat(64), pdb_id: null, producer_job_id: jobId, design_id: null },
            viewer: { url: '/fixture/alternate.pdb', format: 'pdb', sha256: 'a'.repeat(64) },
            inspection: { model_count: 1, chains: ['A'], atom_count: 2, hetero_components: [], parser: { name: 'biopython', version: '1.85' } },
            admission: { state: 'profile_required', profile_id: null, code: 'fixture', message: 'Transport fixture; no chemistry inferred.' },
        };
        else if (url === '/api/msa/status') data = { providers: {}, cache_entries: 0 };
        else return (fallback as (config: any) => Promise<any>)(config);
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    vi.mocked(fetch).mockImplementation(async input => {
        const url = String(input);
        if (url.includes('/api/files/download/') && url.includes('alternate.pdb')) return { ok: true, blob: async () => new Blob([pdb]) } as Response;
        if (url === '/api/structures' || url === '/api/pdb/cache') return { ok: true, json: async () => ({ structures: [], cached: [] }) } as Response;
        unexpected.push(`fetch ${url}`); throw Error(`Unexpected fetch ${url}`);
    });
});
afterEach(async () => {
    await close();
    console.info('OFFLINE_CONTINUATION_TRANSPORT', JSON.stringify(posts));
    vi.unstubAllGlobals();
    try { restore(); } finally { vi.restoreAllMocks(); }
    expect(unexpected).toEqual([]);
});

it('Results alternate document reaches actual blind prediction and emits no generated-pose conditioning', async () => {
    const route = await resultLink('Predict exact structure');
    await mount(<JobSubmission />, route);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Selected candidate protein chain"]')).not.toBeNull(); });
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Selected candidate protein chain"]')!, '2:A');
    const esm = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => n.querySelector('span')?.textContent === 'ESMFold2')!;
    expect(esm).toBeTruthy(); await act(async () => esm.click());
    await click('Launch Prediction');
    const request = posts.find(p => p.url === '/api/jobs')?.body;
    expect(request, document.body.textContent ?? '').toMatchObject({ execution_target_id: null, source_structure: source, params: { sequence: 'AG' } });
    expect(request.parent_job_id).toBeFalsy();
    for (const key of ['input_pdb', 'input_structure', 'templates', 'initial_guess', 'restraints', 'fixed_target_source_path', 'target_source', 'source_structure']) expect(request.params[key], key).toBeUndefined();
});

it('Results prediction materialization retains the inspected MODEL as well as artifact/state', async () => {
    for (const label of ['Design sequence from exact structure', 'Redesign exact structure']) {
        const link = new URL(await resultLink(label), window.location.origin);
        expect(JSON.parse(link.searchParams.get('source_structure')!)).toEqual(source);
    }
    const route = await resultLink('Predict exact structure');
    await mount(<JobSubmission />, route);
    await vi.waitFor(() => expect(posts.some(p => p.url === '/api/files/materialize-structure')).toBe(true));
    expect(posts.find(p => p.url === '/api/files/materialize-structure')!.body).toMatchObject({ ...source, output_format: 'native' });
});

it('Results prediction does not silently delete a modified residue from the selected native chain', async () => {
    const route = await resultLink('Predict exact structure');
    const prior = api.defaults.adapter as (config: any) => Promise<any>;
    api.defaults.adapter = async config => {
        if (config.url !== '/api/files/materialize-structure') return prior(config);
        posts.push({ url: config.url, body: JSON.parse(String(config.data)) });
        return { config, status: 200, statusText: 'OK', headers: {}, data: { ...materialized, native_recipe: recipes.modified, author_residues: [
            { model_number: 2, auth_asym_id: 'A', auth_seq_id: 1, residue_name: 'ALA' },
            { model_number: 2, auth_asym_id: 'A', auth_seq_id: 2, residue_name: 'SEP' },
            { model_number: 2, auth_asym_id: 'A', auth_seq_id: 3, residue_name: 'GLY' },
        ] } };
    };
    await mount(<JobSubmission />, route);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Selected candidate protein chain"]')).not.toBeNull(); });
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Selected candidate protein chain"]')!, '2:A');
    // No guessed canonicalization or new refusal gate: the modified position
    // must remain represented or explicitly disclosed, not silently become AG.
    const sequence = [...document.querySelectorAll('textarea')].find(n => n.value === 'AG');
    expect(sequence && !document.body.textContent?.includes('SEP'), 'ALA/SEP/GLY was silently adopted as AG without the modified position').toBeFalsy();
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
});

it('adopts emitted native mixed recipe through the real roster and captures converter-ready wire', async () => {
    const route = await resultLink('Predict exact structure');
    const prior = api.defaults.adapter as (config: any) => Promise<any>;
    api.defaults.adapter = async config => {
        if (config.url !== '/api/files/materialize-structure') return prior(config);
        posts.push({ url: config.url, body: JSON.parse(String(config.data)) });
        return { config, status: 200, statusText: 'OK', headers: {}, data: { ...materialized, native_recipe: recipes.mixed } };
    };
    await mount(<JobSubmission />, route);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Adopt native molecular recipe"]')).not.toBeNull(); });
    expect([...document.querySelectorAll('textarea')].some(n => n.value === 'ASGMPX')).toBe(false);
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Adopt native molecular recipe"]')!, 'A');
    const protenix = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => n.querySelector('span')?.textContent === 'Protenix')!;
    expect(protenix).toBeTruthy(); await act(async () => protenix.click()); await settle();
    const field = (name: string) => document.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)!;
    expect(field('Entity 1 sequence').value).toBe('ASGMPX');
    expect(field('Entity 1 modification 1 CCD').value).toBe('CCD_SEP');
    expect(field('Entity 3 sequence').value).toBe('A');
    expect(field('Entity 4 sequence').value).toBe('U');
    await edit(field('Entity 1 ID'), 'renamed');
    await edit(field('Entity 1 count'), '2');
    await edit(field('Entity 1 copy IDs (comma separated)'), 'copyA,copyB');
    await edit(field('Entity 1 unpairedMsa'), '>query\nASGMPX\n');
    await edit(field('Entity 1 pairedMsa'), '');
    await edit(field('Entity 2 ligand_file'), 'inputs/structure_prediction/ligand.pdb');
    expect(field('Bond 1 left entity').value).toBe('1');
    expect(document.body.textContent).toContain('ZZZ');
    await click('Launch Prediction');
    const request = posts.find(p => p.url === '/api/jobs')?.body;
    expect(request, document.body.textContent ?? '').toMatchObject({ model_id: 'protenix', execution_target_id: null, source_structure: source });
    expect(request.params.complex_components).toHaveLength(4);
    expect(request.params.complex_components[0]).toMatchObject({ id: 'renamed', sequence: 'ASGMPX', count: 2, copy_ids: ['copyA', 'copyB'], unpairedMsa: '>query\nASGMPX\n', modifications: recipes.mixed.complex_components[0].modifications });
    expect(request.params.covalent_bonds).toEqual(recipes.mixed.covalent_bonds);
    for (const key of ['input_pdb', 'input_structure', 'templates', 'initial_guess', 'restraints', 'fixed_target_source_path', 'target_source', 'source_structure']) expect(request.params[key], key).toBeUndefined();
    expect(request.parent_job_id).toBeFalsy();
    if (process.env.BMS_CONTINUATION_WIRE) writeFileSync(process.env.BMS_CONTINUATION_WIRE, JSON.stringify(request, null, 2));
});

it('native recipe survives cold draft reopen and rebases existing bonds after roster deletion', async () => {
    const prior = api.defaults.adapter as (config: any) => Promise<any>;
    api.defaults.adapter = async config => {
        if (config.url !== '/api/files/materialize-structure') return prior(config);
        return { config, status: 200, statusText: 'OK', headers: {}, data: { ...materialized, native_recipe: recipes.mixed } };
    };
    const prepared = await prepareDeNovoContinuation(source, 'prediction');
    let draft: any;
    const receive = (value: any) => { draft = JSON.parse(JSON.stringify(value.editor_state)); };
    await mount(<StructurePredictionTemplate initialValues={{ ...prepared, pred_method: 'protenix' }} onBack={() => {}} onDraftChange={receive} />);
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Adopt native molecular recipe"]')!, 'A');
    const field = (name: string) => document.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)!;
    await edit(field('Entity 1 ID'), 'retained-primary');
    await settle();
    expect(draft.complex_components.map((c: any) => c.id)).toEqual(['retained-primary', 'L', 'D', 'R']);
    expect(draft._continuation_recipe).toEqual(recipes.mixed);
    await close();
    await mount(<StructurePredictionTemplate initialValues={draft} onBack={() => {}} onDraftChange={receive} />);
    expect(field('Entity 1 modification 1 CCD').value).toBe('CCD_SEP');
    expect(field('Bond 1 right entity').value).toBe('3');
    const remove = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === '×')!;
    expect(remove).toBeTruthy(); await act(async () => remove.click()); await settle();
    expect(draft.complex_components.map((c: any) => c.id)).toEqual(['retained-primary', 'D', 'R']);
    expect(field('Bond 1 right entity').value).toBe('2');
    expect(draft.covalent_bonds[0]).toMatchObject({ left_entity: 1, right_entity: 2, left_position: 2 });
    expect(draft.source_structure).toEqual(source);
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
});

it('actual Results MD action opens the real receiving editor and inspects exact CandidateRef without launch', async () => {
    await mount(<NativeBinderGenerationResults jobId={jobId} artifactId="alternate" targetState="B" />);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Continue exact native document"]')).not.toBeNull(); });
    // Browser navigation is a transport boundary: capture jsdom's otherwise
    // unimplemented navigation, not the route builder or any scientific owner.
    const implKey = Object.getOwnPropertySymbols(window.location).find(key => key.description === 'impl')!;
    const impl = (window.location as any)[implKey];
    let destination = '';
    const navigation = vi.spyOn(impl, '_locationObjectNavigate').mockImplementation((url: any) => { destination = `/${url.path.join('/')}?${url.query}`; });
    await click('Use as MD starting structure'); navigation.mockRestore();
    expect(destination).toContain('template=molecular_dynamics');
    const query = new URL(destination, window.location.origin).searchParams;
    expect(JSON.parse(query.get('source_ref')!)).toEqual({ kind: 'candidate', id: jobId, candidate_ref: candidate });
    expect(query.get('source_design_id')).toBeNull();
    expect(query.get('return_to')).toContain('artifact_id=alternate');
    await close(); await mount(<JobSubmission />, destination);
    await vi.waitFor(async () => { await settle(); expect(posts.some(p => p.url === '/api/molecular-dynamics/starting-structures/inspect')).toBe(true); });
    expect(posts.find(p => p.url.endsWith('/inspect'))!.body).toEqual({ source_ref: { kind: 'candidate', id: jobId, candidate_ref: candidate }, chemistry_profile_id: null });
    await settle();
    expect(document.body.textContent).toContain('Alternate MODEL 2 fixture');
    expect(posts.filter(p => p.url === '/api/jobs' || p.url.includes('launch'))).toEqual([]);
});

it('actual Results redesign opens the unified RFD3 editor with the exact source and submits a region task', async () => {
    const route = await resultLink('Redesign exact structure');
    await mount(<JobSubmission />, route);
    await vi.waitFor(async () => { await settle(); expect([...document.querySelectorAll('button')].some(n => n.textContent === 'Choose / edit RFD3 context'), document.body.textContent ?? '').toBe(true); });
    await click('Choose / edit RFD3 context');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="RFD3 region task"]'), document.body.textContent ?? '').not.toBeNull(); });
    const task = document.querySelector<HTMLSelectElement>('[aria-label="RFD3 region task"]')!;
    expect(task.value).toBe('partial_diffusion');
    await edit(document.querySelector<HTMLInputElement>('[aria-label="Redesign ranges"]')!, 'A1');
    await edit(document.querySelector<HTMLInputElement>('[aria-label="Partial noise exact Å"]')!, '4');
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
    await click('Generate candidates');
    await vi.waitFor(async () => { await settle(); expect(posts.some(p => p.url === '/api/jobs'), document.body.textContent ?? '').toBe(true); });
    const request = posts.find(p => p.url === '/api/jobs')?.body;
    expect(request).toMatchObject({ model_id: 'protein_modification_experimental', mode: 'de_novo_design',
        params: { generator: 'rfd3', generation_task: 'partial_diffusion', input_structure: 'inputs/retained/alternate.pdb',
            redesign: { redesign_ranges: 'A1' }, conditioning: { partial_t: 4 } } });
    expect(request.parent_job_id).toBeFalsy();
    expect(request.params.seq_method).toBeUndefined();
});

it.each(['auto', 'skip', 'external'] as const)('actual Shape cold %s replay emits geometry and contextual validation without duplicate general sequence stages', async policy => {
    const initial = { shape_geometry_id: 'fixture-geometry', shape_geometry_sha256: 'b'.repeat(64), shape_sequence_policy: policy, shape_sequence_engine: 'proteinmpnn', shape_seed: 0, shape_target_length: 100 };
    await mount(<ShapeBlueprintTemplate initialValues={JSON.parse(JSON.stringify(initial))} />, '/submit?template=protein_modification_experimental&modification_mode=shape_blueprint');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-webgl]'), document.body.textContent ?? '').not.toBeNull(); });
    expect(posts).toEqual([]);
    await click('Launch Shape Blueprint');
    const request = posts.find(p => p.url === '/api/shape-blueprint/requests')?.body;
    expect(request, document.body.textContent ?? '').toMatchObject({ geometry_id: 'fixture-geometry', expected_geometry_sha256: 'b'.repeat(64), sequence_policy: policy, seed: 0 });
    expect(request.validator_suite).toEqual(['boltz2', 'esmfold2', 'protenix_v2']);
    expect(request.sequences_per_backbone).toBe(policy === 'skip' ? 0 : 1);
    expect(request.sequence_engine).toBe(policy === 'external' ? 'proteinmpnn' : undefined);
    expect(request.round_plan).toBeUndefined(); expect(request.sequence_design).toBeUndefined();
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
});
