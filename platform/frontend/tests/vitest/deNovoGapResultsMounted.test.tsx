import React, { act } from 'react';
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
const discovery = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;print(json.dumps({m:generation_settings(m) for m in ('generate','evaluate')}))"], { cwd: resolve('../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
import recipes from './fixtures/deNovoContinuationRecipes.json';


import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { isolateDeNovoTransport } from './deNovoTransport';
import { api } from '../../src/lib/api';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { JobSubmission } from '../../src/components/JobSubmission';
import { RFD3GenerationResultsContent } from '../../src/components/RFD3GenerationResultsPane';
import type { RFD3GenerationReadModel } from '../../src/lib/api';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import { ThemeProvider } from '../../src/components/ThemeProvider';

// Offline transport fixtures, not inference, native-writer or API acceptance.
// All receiving/editor/serializer code is real. Only WebGL and HTTP are inert.
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: object) => <div data-rfd3-viewer>{JSON.stringify(props)}</div> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div data-webgl /> }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => <div data-webgl /> }));
vi.mock('../../src/components/CanonicalMeshPreview', () => ({ default: () => <div data-webgl /> }));
vi.mock('react-plotly.js', () => ({ default: () => <div data-chart /> }));

const jobId = '11111111-1111-4111-8111-111111111111';
let candidate: any = { kind: 'native_document', job_id: jobId, artifact_id: 'alternate', target_state: 'B', model: 2 };
const documents: any[] = [
    { artifact_id: 'primary', primary: true, target_state: 'A', logical_path: 'primary.cif', download_url: '/fixture/primary.cif' },
    { artifact_id: 'alternate', target_state: 'B', sha256: 'a'.repeat(64), logical_path: 'alternate.cif', download_url: '/fixture/alternate.cif' },
];
const source = { job_id: jobId, document: { artifact_id: 'alternate', target_state: 'B' }, model_number: 2 };
const materialized = {
    path: 'inputs/retained/alternate.pdb', format: 'pdb', native_format: 'pdb', sha256: 'a'.repeat(64), model_number: 2, model_numbers: [2], source_positions: [],
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
async function mount(node: React.ReactNode, route = `/designs/${jobId}?native_candidate=native&artifact_id=alternate&target_state=B`) {
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
beforeEach(() => {
    candidate = { kind: 'native_document', job_id: jobId, artifact_id: 'alternate', target_state: 'B', model: 2 };
    delete documents[1].model;
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
        if (url === `/api/jobs/${jobId}/generation-results`) data = { model_id: 'rfd4_proteina', records: [{ candidate_key: 'native', candidate_ref: candidate, metrics: {}, structures: documents }], total: 1, offset: 0, limit: 100, receipt: {}, publication: {}, artifacts: [{ role: 'sampling_state', artifact_id: 'state-B', path: 'states/B.pt', source_path: 'outputs/states/B.pt', sha256: 'b'.repeat(64) }] };
        else if (url === `/api/jobs/${jobId}`) data = { id: jobId, saved_selection_sets: [] };
        else if (url === `/api/jobs/${jobId}/binder-evidence`) data = { records: [], total: 0, offset: 0, limit: 100 };
        else if (url === `/api/binder-continuation/${jobId}/round`) data = { job_id: jobId, state: 'not_requested', steps: {}, errors: {} };
        else if (url === '/api/models/rfd4_proteina/generation-settings') data = discovery[config.params?.mode ?? 'generate'];
        else if (url === '/api/files/materialize-structure') data = { ...materialized, model_number: body.model_number, source_structure: body };
        else if (url === '/api/jobs' && config.method === 'post') throw Error('Offline capture: no Job created');
        else if (['/api/templates', '/api/user-templates'].includes(url)) data = [];
        else if (url === '/api/models/frustrampnn/integration') data = { workflows: {} };
        else if (url === '/api/models/protenix') data = { id: 'protenix', params: [], modes: [{ id: 'complex', parameter_schema: {} }] };
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
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `results-${expect.getState().currentTestName!.replace(/[^A-Za-z0-9]/g, '_')}.json`), JSON.stringify(posts, null, 2));
    vi.unstubAllGlobals();
    try { restore(); } finally { vi.restoreAllMocks(); }
    expect(unexpected).toEqual([]);
});

it('RFD3 labels keep exact IDs in copyable details, metrics, downloads and continuation intact', async () => {
    const result: RFD3GenerationReadModel = {
        schema: 'bms.rfd3.generation.read-model.v1', job_id: jobId, request: {}, result_manifest_sha256: 'a'.repeat(64),
        counts: { requested: 2, generated: 2, accepted: 1 },
        aggregates: Object.fromEntries(['length', 'radius', 'helix', 'strand'].map(key => [key, { min: 1, mean: 2, max: 3 }])) as RFD3GenerationReadModel['aggregates'],
        candidates: ['internal-generated-A', 'internal-generated-B'].map(candidate_id => ({ candidate_id, status: 'generated', length: 100, radius: 0, helix_count: null, strand_count: null, structure_url: `/fixture/${candidate_id}.cif`, source_structure: source })),
    };
    await mount(<RFD3GenerationResultsContent result={result} />, '/designs/' + jobId);
    await act(async () => document.querySelectorAll<HTMLButtonElement>('tbody button')[1].click());
    expect(document.querySelector<HTMLInputElement>('[aria-label="Exact candidate identifier"]')?.value).toBe('internal-generated-B');
    expect(JSON.parse(document.querySelector('[data-rfd3-viewer]')!.textContent!)).toMatchObject({ label: 'Candidate 2', structureUrl: '/fixture/internal-generated-B.cif', format: 'cif' });
    expect(document.body.textContent).toContain('0.00');
    expect(document.body.textContent).toContain('—');
    expect([...document.querySelectorAll('a[download]')].map(a => a.getAttribute('href'))).toEqual(result.candidates.map(c => c.structure_url));
    const link = [...document.querySelectorAll<HTMLAnchorElement>('a')].find(a => a.textContent === 'Redesign')!;
    expect(JSON.parse(new URL(link.href).searchParams.get('source_structure')!)).toEqual(source);
});

it.each([
    ['native CandidateRef only', undefined, undefined, 2, false],
    ['design-document CandidateRef only', undefined, undefined, 2, true],
    ['document wins URL and CandidateRef', 4, 3, 4, false],
    ['URL wins CandidateRef', undefined, 3, 3, false],
    ['MODEL omitted', undefined, undefined, undefined, false],
] as const)('%s: actual Results emits matching ordinary/retained source and receiver keeps seed zero', async (_name, documentModel, urlModel, expectedModel, design) => {
    if (design) candidate = { kind: 'design', job_id: jobId, design_id: 'design-B', document: { artifact_id: 'alternate', target_state: 'B', model: 2 } };
    if (expectedModel === undefined) delete candidate.model;
    if (documentModel !== undefined) documents[1].model = documentModel;
    await mount(<NativeBinderGenerationResults jobId={jobId} artifactId="alternate" targetState="B" />, `/designs/${jobId}?native_candidate=native&artifact_id=alternate&target_state=B${urlModel === undefined ? '' : `&model_number=${urlModel}`}`);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="RFD4 retained state"]')).not.toBeNull(); });
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="RFD4 retained state"]')!, 'state-B');
    await edit(document.querySelector<HTMLInputElement>('[aria-label="RFD4 continuation seed"]')!, '0');
    const links = [...document.querySelectorAll<HTMLAnchorElement>('a')].filter(a => ['Refine with partial diffusion', 'Native inverse folding', 'Evaluate with RFD4', 'Continue retained RFD4 state'].includes(a.textContent ?? ''));
    expect(links).toHaveLength(4);
    const routes = links.map(a => new URL(a.href));
    for (const route of routes) {
        const selected = JSON.parse(route.searchParams.get('source_structure')!);
        expect(selected).toMatchObject({ job_id: jobId, document: { artifact_id: 'alternate', target_state: 'B' } });
        expect(selected.model_number).toBe(expectedModel);
        if (expectedModel === undefined) expect(selected).not.toHaveProperty('model_number');
    }
    const retained = routes.find(route => route.searchParams.get('continuation') === 'rfd4_state')!;
    expect(JSON.parse(retained.searchParams.get('source_structure')!).expected_sha256).toBe('a'.repeat(64));
    const state = JSON.parse(retained.searchParams.get('rfd4_state')!);
    expect(state).toEqual({ path: 'outputs/states/B.pt', artifact_id: 'state-B', sha256: 'b'.repeat(64), seed: 0 });
    const prepared = await prepareDeNovoContinuation(JSON.parse(retained.searchParams.get('source_structure')!), 'rfd4_state', state);
    expect(prepared).toMatchObject({ native_sources: { selected: { document: { artifact_id: 'alternate', target_state: 'B' } }, resume: { kind: 'file', path: state.path, expected_sha256: state.sha256 } }, rfd4_request: { resume_slot: 'resume', settings: { seed: 0 } } });
    expect(document.querySelector<HTMLInputElement>('[aria-label="Exact candidate identifier"]')?.value).toBe('native');
    expect(document.querySelector('h4')?.textContent).not.toBe('native');
    await close();
    for (const route of [routes[0], retained]) {
        posts.length = 0;
        await mount(<JobSubmission />, `${route.pathname}${route.search}`);
        await vi.waitFor(async () => { await settle(); expect(posts.some(p => p.url === '/api/files/materialize-structure')).toBe(true); });
        const ingress = posts.find(p => p.url === '/api/files/materialize-structure')!.body;
        expect(ingress).toMatchObject({ job_id: jobId, document: { artifact_id: 'alternate', target_state: 'B' }, output_format: 'native' });
        expect(ingress.model_number).toBe(expectedModel);
        await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-native-de-novo="rfd4_proteina"] fieldset'), document.body.textContent ?? '').not.toBeNull(); }, { timeout: 3500 });
        await click('Generate candidates');
        const emitted = posts.find(p => p.url === '/api/jobs')?.body;
        expect(emitted, document.body.textContent ?? '').toBeTruthy();
        expect(emitted.params.native_sources.selected).toMatchObject({ document: { artifact_id: 'alternate', target_state: 'B' } });
        expect(emitted.params.native_sources.selected.model_number).toBe(expectedModel);
        if (route === retained) {
            expect(emitted.params.native_sources.resume).toEqual({ kind: 'file', path: state.path, expected_sha256: state.sha256 });
            expect(emitted.params.rfd4_request).toMatchObject({ resume_slot: 'resume', settings: { seed: 0 } });
        }
        await close();
    }
});
