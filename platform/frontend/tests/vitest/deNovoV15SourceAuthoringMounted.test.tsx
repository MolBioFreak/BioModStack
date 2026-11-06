import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';

const inventories = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
    "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;from services.complexa import discovery;print(json.dumps({'rfd4_proteina':generation_settings('generate'),'complexa':discovery()}))",
], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
// Only the WebGL rendering boundary is inert; source picker and HTTP are real.
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: any) => <div data-viewer-url={props.structureUrl} data-viewer-format={props.format}><button type="button" onClick={() => props.onMetricSelection({ metricId: 'fixture-canvas-copy', origin: 'canvas', identities: [{ documentId: props.structureDocumentId, labelAsymId: 'AA', labelSeqId: 7, operatorInstanceId: 'copy-2' }] })}>Inspect transformed canvas copy</button></div> }));
import { JobSubmission } from '../../src/components/JobSubmission';
import '../../src/components/ProteinModificationTemplate';
import '../../src/components/DeNovoNativeGenerationSettings';
import { api } from '../../src/lib/api';
let root: Root | undefined, client: QueryClient, previous: typeof api.defaults.adapter;
let requests: Array<{ url: string; method: string; body: any; params: any }>, unexpected: string[], saved: any[];
let fetchGuard: ReturnType<typeof vi.spyOn>, xhrGuard: ReturnType<typeof vi.spyOn>;
let materializer: (body: any) => Promise<any>, previewer: (body: any) => Promise<any>;
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
const receipt = (body: any) => {
    const artifact = body.candidate?.artifact_id ?? 'uploaded';
    const model = body.model_number ?? body.candidate?.model ?? null;
    return { path: `inputs/structure-${artifact}/selected${model ?? 'all'}.cif`, format: 'cif', sha256: 'b'.repeat(64),
        original_path: `inputs/structure-${artifact}/original.cif.gz`, original_sha256: 'a'.repeat(64),
        native_path: `inputs/structure-${artifact}/native.cif`, native_format: 'cif', native_sha256: 'b'.repeat(64),
        decoded_path: `inputs/structure-${artifact}/decoded.cif`, model_number: model, model_numbers: [1, 2], author_residues: [], source_positions: [], source_identity: {}, source_path: body.path ?? '',
        source_structure: { ...body, path: `inputs/structure-${artifact}/original.cif.gz`, expected_sha256: 'a'.repeat(64), output_format: 'native' } };
};
const preview = (body: any) => ({ schema: 'bms.job.execution-preview.v1', request: body, approval_digest: 'inert', admissible: false,
    plan: { requested_json: body.params, effective_json: body.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } },
    blockers: [{ reason: 'Fixture runtime absent; this does not gate authoring' }], deferred_preparation: [],
    native_review: { model_id: body.model_id, requested: body.params.rfd4_request, native_request: body.params.rfd4_request, native_overlay: { marker: body.name } } });
beforeEach(() => {
    requests = []; unexpected = []; saved = []; previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear(); materializer = async body => receipt(body); previewer = async body => preview(body);
    fetchGuard = vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
        const url = new URL(String(input), 'http://localhost');
        const path = decodeURIComponent(url.pathname.replace('/api/files/download/', ''));
        if (url.pathname.startsWith('/api/files/download/') && path.startsWith('inputs/structure-') && path.endsWith('.cif')) {
            return new Response(readFileSync('tests/fixtures/rfd4-saved-conditions.cif', 'utf8'));
        }
        unexpected.push(`fetch ${url}`); throw new Error('No live network');
    });
    xhrGuard = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No live network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? '', method = config.method ?? 'get';
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ url, method, body, params: config.params }); let data: any;
        const native = url.match(/^\/api\/models\/(rfd4_proteina|complexa)\/generation-settings$/);
        if (native) data = inventories[native[1]];
        else if (url === '/api/files/materialize-structure') data = await materializer(body);
        else if (url === '/api/files/browse') data = { entries: [{ name: 'annotated.bcif.gz', path: 'inputs/annotated.bcif.gz', is_directory: false }] };
        else if (url === '/api/files/upload') data = { path: 'inputs/annotated.bcif.gz' };
        else if (url === '/api/jobs/execution-plan/preview') data = await previewer(body);
        else if (url === '/api/jobs' && method === 'post') data = { id: 'inert-job' };
        else if (url === '/api/jobs') data = { jobs: [{ id: 'producer', name: 'Native producer', model_id: 'rfd4_proteina', mode: 'generate', status: 'failed', created_at: '2026-01-01', design_count: 0 }], total: 1 };
        else if (url === '/api/jobs/producer/generation-results') data = { receipt: {}, publication: {}, total: 1, offset: 0, limit: 50, records: [{ candidate_key: 'retained failed candidate', structures: [
            { artifact_id: 'primary', primary: true, target_state: 'primary-state', logical_path: 'primary.cif', sha256: 'a'.repeat(64) },
            { artifact_id: 'alternate', primary: false, target_state: 'state-B', logical_path: 'alternate.cif.gz', sha256: 'a'.repeat(64) },
        ] }] };
        else if (url === '/api/user-templates' && method === 'post') { data = { ...body, id: 'saved-native', created_at: '2026-01-01', updated_at: '2026-01-01' }; saved = [data]; }
        else if (url === '/api/user-templates') data = saved;
        else if (['/api/models', '/api/templates', '/api/input-presets', '/api/execution-targets'].includes(url)) data = [];
        else if (url === '/api/system/status') data = { gpus: [], gpu_error: null };
        else if (url.startsWith('/api/models/')) data = { id: url.split('/').at(-1), params: [], modes: [{ id: 'design', params: [] }] };
        else { unexpected.push(`${method} ${url}`); throw new Error(`Unexpected HTTP ${url}`); }
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => { await unmount(); api.defaults.adapter = previous; fetchGuard.mockRestore(); xhrGuard.mockRestore(); expect(unexpected).toEqual([]); });
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function mount(model = 'rfd4_proteina', params: any = { rfd4_request: { input: { kind: 'annotated', slots: ['selected'] }, settings: { seed: 0 } }, native_sources: { selected: { output_format: 'native' } } }) {
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Source authoring', model_id: model, mode: 'generate', params, execution_target_id: null }));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/submit?model=${model}&mode=generate`]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-native-de-novo] fieldset')).not.toBeNull(); });
}
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => !n.closest('[hidden]') && (n.textContent?.trim() === text || n.getAttribute('aria-label') === text));
    expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle();
}
async function edit(label: string, value: string) {
    const node = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(node, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
async function sources() {
    const summary = [...document.querySelectorAll('summary')].find(n => n.textContent === 'Input structures')!;
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
async function nativeDocuments() {
    await click('Choose native result for selected');
    await vi.waitFor(async () => { await settle(); expect([...document.querySelectorAll('h4')].some(n => n.textContent === 'Native producer')).toBe(true); });
    await act(async () => { const title = [...document.querySelectorAll('h4')].find(n => n.textContent === 'Native producer')!; title.click(); });
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Use native document alternate"]')).not.toBeNull(); });
}
async function choose(artifact: string) { await act(async () => (document.querySelector(`[aria-label="Use native document ${artifact}"]`) as HTMLButtonElement).click()); await settle(); }
it('actual parent binds alternate native document and explicit MODEL without primary reads or native annotation conversion', async () => {
    await mount(); expect(requests.some(r => r.url.includes('materialize'))).toBe(false); await sources();
    expect(requests.some(r => r.url.includes('materialize'))).toBe(false); await nativeDocuments(); await choose('alternate');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Source MODEL for selected"]')).not.toBeNull(); });
    await edit('Source MODEL for selected', '2'); await settle();
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-viewer-url]')?.getAttribute('data-viewer-url')).toContain('selected2.cif'); });
    await click('Generate candidates');
    const acquisitions = requests.filter(r => r.url === '/api/files/materialize-structure');
    expect(acquisitions.map(r => r.body.candidate.artifact_id)).toEqual(['alternate', 'alternate']);
    expect(acquisitions[1].body).toMatchObject({ candidate: { kind: 'native_document', job_id: 'producer', artifact_id: 'alternate', target_state: 'state-B', model: 2 }, model_number: 2, output_format: 'native' });
    const wire = requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body;
    expect(wire).not.toHaveProperty('native_sources'); expect(wire.params.native_sources.selected).toEqual(receipt(acquisitions[1].body).source_structure);
    expect(wire.params.rfd4_request.input).toEqual({ kind: 'annotated', slots: ['selected'] });
    expect(requests.find(r => r.url === '/api/jobs' && r.method === 'get')!.params.status).toBeUndefined();
});
it('clear while exact acquisition is pending never restores that document or another primary', async () => {
    let release!: (value: any) => void; materializer = body => new Promise(resolve => { release = value => resolve(value ?? receipt(body)); });
    await mount(); await sources(); await nativeDocuments(); await choose('alternate'); await click('Remove source selected');
    await act(async () => release(null)); await settle(); await click('Generate candidates');
    expect(document.querySelector('[aria-label="Native source selected"]')).toBeNull();
    expect(requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body.params.native_sources).toEqual({});
    expect(requests.filter(r => r.url.includes('materialize')).map(r => r.body.candidate.artifact_id)).toEqual(['alternate']);
});
it('legacy exact-document MODEL changes update both selector fields without stale primary or model identity', async () => {
    const source = { job_id: 'producer', design_id: 'design', document: { artifact_id: 'alternate', target_state: 'state-B', model: 2 }, model_number: 2, output_format: 'native' };
    await mount('rfd4_proteina', { rfd4_request: { input: { kind: 'annotated', slots: ['selected'] } }, native_sources: { selected: source } });
    await sources(); await edit('Source MODEL for selected', '1'); await settle();
    const acquisitions = requests.filter(r => r.url.includes('materialize'));
    expect(acquisitions).toHaveLength(2);
    expect(acquisitions[1].body).toMatchObject({ document: { artifact_id: 'alternate', target_state: 'state-B', model: 1 }, model_number: 1 });
    await edit('Source MODEL for selected', ''); await settle(); await click('Generate candidates');
    const retained = requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body.params.native_sources.selected;
    expect(retained.document).toEqual({ artifact_id: 'alternate', target_state: 'state-B' });
    expect(retained).not.toHaveProperty('model_number');
});
it('new document selection wins when an older acquisition finishes last', async () => {
    let old!: (value: any) => void; materializer = body => body.candidate.artifact_id === 'alternate' ? new Promise(resolve => { old = resolve; }) : Promise.resolve(receipt(body));
    await mount(); await sources(); await nativeDocuments(); await choose('alternate'); await choose('primary');
    const oldRequest = requests.find(r => r.url.includes('materialize') && r.body.candidate.artifact_id === 'alternate')!.body;
    await act(async () => old(receipt(oldRequest))); await settle(); await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body.params.native_sources.selected.candidate.artifact_id).toBe('primary');
});
it('managed compressed BCIF stays original-bound while only its decoded CIF is viewed', async () => {
    materializer = async body => ({ ...receipt(body), path: 'inputs/structure-uploaded/native.bcif', format: 'bcif', native_format: 'bcif', decoded_path: 'inputs/structure-uploaded/decoded.cif',
        source_structure: { ...body, path: 'inputs/structure-uploaded/original.bcif.gz', expected_sha256: 'a'.repeat(64) } });
    await mount(); await sources(); await click('Browse native file for selected');
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain('annotated.bcif.gz'); });
    await act(async () => [...document.querySelectorAll('span')].find(n => n.textContent === 'annotated.bcif.gz')!.click()); await settle();
    expect(document.querySelector('[data-viewer-url]')?.getAttribute('data-viewer-url')).toContain('decoded.cif');
    expect(document.querySelector('[data-viewer-format]')?.getAttribute('data-viewer-format')).toBe('cif');
    await click('Generate candidates');
    const wire = requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body;
    expect(wire.params.native_sources.selected.path).toBe('inputs/structure-uploaded/original.bcif.gz');
    expect(requests.find(r => r.url.includes('materialize'))!.body).toEqual({ path: 'inputs/annotated.bcif.gz', output_format: 'native' });
});
it('independent task source bindings survive real template save and cold explicit Load', async () => {
    const binding = { candidate: { kind: 'native_document', job_id: 'producer', artifact_id: 'alternate', target_state: 'state-B', model: 2 }, path: 'inputs/retained-original.cif.gz', output_format: 'native', expected_sha256: 'a'.repeat(64) };
    const request = { task: 'protein_binder', task_name: 'retained', seed: 0, source: { document_id: 'selected' } };
    await mount('complexa', { complexa_request: request, native_sources: { selected: binding } });
    expect(inventories.complexa.schema.properties.task.enum).toEqual(expect.arrayContaining(['ligand_binder', 'protein_binder']));
    await edit('Complexa task', 'ligand_binder');
    await click('Template Manager');
    const input = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Independent source tasks'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].params.native_job_request.params).toEqual({ complexa_request: { task: 'ligand_binder' } });
    expect(Object.values(saved[0].params.editor_state.native_source_drafts)).toContainEqual({ selected: binding });
    expect(saved[0].params.editor_state.de_novo_drafts).toEqual({});
    await unmount(); await mount('complexa', { complexa_request: { task: 'ligand_binder', seed: 999 } });
    await click('Template Manager');
    if ([...document.querySelectorAll('button')].some(n => n.textContent?.trim() === '← Back to list')) await click('← Back to list');
    await click('Load'); await settle(); await edit('Complexa task', 'protein_binder'); await settle();
    expect(document.querySelector('[aria-label="Source sequence for selected"]')).not.toBeNull();
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body.params).toEqual({ complexa_request: request, native_sources: { selected: binding } });
    // Integrated Complexa automatically inspects the active source on each mount,
    // but neither inspection may rebind the retained task or materialize its inactive draft.
    expect(requests.filter(r => r.url === '/api/files/materialize-structure').map(r => r.body)).toEqual([binding, binding]);
});
it('visited source disclosure keeps its inspector without reacquiring source bytes', async () => {
    await mount(); await sources(); await nativeDocuments(); await choose('alternate');
    const count = requests.filter(r => r.url.includes('materialize')).length;
    const details = [...document.querySelectorAll('summary')].find(n => n.textContent === 'Input structures')!.parentElement as HTMLDetailsElement;
    const viewer = document.querySelector('[data-viewer-url]'); expect(viewer).not.toBeNull();
    await act(async () => { details.open = false; details.dispatchEvent(new Event('toggle')); });
    await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
    expect(document.querySelector('[data-viewer-url]')).toBe(viewer);
    expect(requests.filter(r => r.url.includes('materialize'))).toHaveLength(count);
});
it('native preview is explicit, retains requested values, and never gates Generate on its diagnostics', async () => {
    await mount(); expect(requests.some(r => r.url.includes('execution-plan'))).toBe(false);
    await click('Preview settings'); expect(document.body.textContent).toContain('Fixture runtime absent');
    await click('Generate candidates');
    const posts = requests.filter(r => r.method === 'post');
    expect(posts.find(r => r.url.includes('execution-plan'))!.body.params).toEqual(posts.find(r => r.url === '/api/jobs')!.body.params);
    expect(posts.find(r => r.url === '/api/jobs')!.body).not.toHaveProperty('native_review');
});
it('A to B to A cannot revive an older native preview response', async () => {
    const releases: Array<(value: any) => void> = []; previewer = () => new Promise(resolve => releases.push(resolve));
    await mount(); await click('Preview settings');
    const name = () => document.querySelector<HTMLInputElement>('input[placeholder="Run name"]') ?? [...document.querySelectorAll<HTMLInputElement>('input')].find(n => n.value === 'Source authoring')!;
    const changeName = async (value: string) => { const node = name(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); };
    await changeName('B');
    const node = [...document.querySelectorAll<HTMLInputElement>('input')].find(n => n.value === 'B')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, 'Source authoring'); node.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Preview settings');
    const previews = requests.filter(r => r.url.includes('execution-plan')).map(r => r.body);
    await act(async () => releases[1]({ ...preview(previews[1]), native_review: { ...preview(previews[1]).native_review, native_overlay: { marker: 'new-result' } } }));
    await act(async () => releases[0]({ ...preview(previews[0]), native_review: { ...preview(previews[0]).native_review, native_overlay: { marker: 'stale-result' } } }));
    expect(document.body.textContent).toContain('new-result'); expect(document.body.textContent).not.toContain('stale-result');
});

const positionReceipt = (body: any) => ({ ...receipt(body), model_number: 2, model_numbers: [1, 2], source_positions: [{
    model_number: 2, label_asym_id: 'AA', auth_asym_id: 'author-chain', label_seq_id: '7', auth_seq_id: '101', pdbx_PDB_ins_code: 'B', label_entity_id: '2', label_comp_id: 'SEP',
    atoms: [{ label_atom_id: 'CA', auth_atom_id: 'CAX', model_atom_index: 0, label_alt_id: '.', type_symbol: 'C' }, { label_atom_id: 'CB', auth_atom_id: 'CBX', model_atom_index: 1, label_alt_id: '.', type_symbol: 'C' }],
}] });
const selectedConditions = { input: { kind: 'condition_spec', examples: [{ name: 'source-conditioned', sources: [{ slot: 'selected', input_as_annotated_cif: true }], conditions: {
    C_CRD: { selected: [{ select: 'manual-coordinates', group: 'fixed' }], different: [{ select: 'different-source' }] },
    C_SEQ: { selected: [{ select: 'manual-sequence' }] },
} }] }, settings: { seed: 0 } };
it('exact source atoms enter only the chosen native condition and survive actual HTTP normalization', async () => {
    materializer = async body => positionReceipt(body);
    await mount('rfd4_proteina', { rfd4_request: selectedConditions, native_sources: { selected: { output_format: 'native' } } });
    await sources(); await nativeDocuments(); await choose('alternate');
    await edit('Retained residue for selected', '0'); await edit('Retained atom for selected', '1'); await click('Add exact selection from selected');
    const select = document.querySelector<HTMLInputElement>('[aria-label="C_CRD selected selection 1"]')!;
    expect(select.value).toBe('manual-coordinates');
    await edit('Condition family', 'C_CRD'); await edit('Native condition value', 'true');
    await edit('New condition group', 'fixed');
    await click('Use visual selection in new condition');
    const expression = '(chain_id == "AA" and res_id == 7 and atom_name == "CB")';
    expect(document.querySelector<HTMLInputElement>('[aria-label="Selection expression"]')!.value).toBe(expression);
    await click('Apply native condition');
    expect(select.value).toBe('manual-coordinates'); await click('Generate candidates');
    const wire = requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body;
    const expected: any = structuredClone(selectedConditions);
    expected.input.examples[0].conditions.C_CRD.true = [{ select: expression, group: 'fixed' }];
    expect(wire.params.rfd4_request).toEqual(expected);
    const normalized = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
        "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import normalize_params;p=json.load(sys.stdin);print(json.dumps(normalize_params('generate',p)))",
    ], { cwd: resolve(process.cwd(), '../..'), input: JSON.stringify(wire.params), encoding: 'utf8' }));
    expect(normalized.rfd4_request.input).toEqual(expected.input);
    expect(wire.params.native_sources.selected.candidate.artifact_id).toBe('alternate');
});
it('unsupported copy observations leave manually authored conditions and Generate unchanged', async () => {
    materializer = async body => positionReceipt(body);
    await mount('rfd4_proteina', { rfd4_request: selectedConditions, native_sources: { selected: { output_format: 'native' } } });
    await sources(); await nativeDocuments(); await choose('alternate'); await click('Inspect transformed canvas copy');
    expect(document.body.textContent).toContain('Assembly/copy identity has no qualified native source mapping');
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body.params.rfd4_request).toEqual(selectedConditions);
});

async function clickAria(label: string) {
    const button = document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
    expect(button, label).not.toBeNull();
    await act(async () => button!.click()); await settle();
}
async function chooseInlineFile() {
    await clickAria('Choose structure for Add annotated source');
    await click('Browse native file for Add annotated source');
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain('annotated.bcif.gz'); });
    await act(async () => [...document.querySelectorAll('span')].find(node => node.textContent === 'annotated.bcif.gz')!.click());
    await settle();
}
it('task-row acquisition binds a structure without a named empty slot, retains MODEL and cold template settings', async () => {
    await mount('rfd4_proteina', { rfd4_request: { input: { kind: 'annotated', slots: [] }, settings: { seed: 0 } } });
    expect(requests.some(row => row.url.includes('materialize'))).toBe(false);
    expect(document.querySelector('[aria-label="Native source name"]')).toBeNull();
    await chooseInlineFile();
    await vi.waitFor(() => expect(document.querySelector<HTMLSelectElement>('[aria-label="Annotated source 1"]')?.value).toBe('input_1'));
    expect(document.querySelector('[data-viewer-url]')).not.toBeNull();
    expect(requests.filter(row => row.url.includes('materialize'))).toHaveLength(1);
    await edit('Source MODEL for input_1', '2'); await settle();
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Task source MODEL 2'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); await vi.waitFor(() => expect(saved).toHaveLength(1));
    const original = saved[0].params.native_job_request.params;
    expect(original.rfd4_request.input.slots).toEqual(['input_1']);
    expect(original.native_sources.input_1).toMatchObject({ model_number: 2, expected_sha256: 'a'.repeat(64), path: 'inputs/structure-uploaded/original.cif.gz' });
    await unmount(); await mount(); await click('Template Manager');
    if ([...document.querySelectorAll('button')].some(node => node.textContent?.trim() === '← Back to list')) await click('← Back to list');
    await click('Load'); await settle(); await click('Generate candidates');
    expect(requests.find(row => row.url === '/api/jobs' && row.method === 'post')!.body.params).toEqual(original);
    expect(requests.filter(row => row.url.includes('materialize'))).toHaveLength(3);
});
it('cancelling a task-row picker while materialization is pending creates neither binding nor source row', async () => {
    let release!: (value: any) => void;
    materializer = body => new Promise(resolve => { release = value => resolve(value ?? receipt(body)); });
    await mount('rfd4_proteina', { rfd4_request: { input: { kind: 'annotated', slots: [] } } });
    await chooseInlineFile();
    const group = document.querySelector('[role="group"][aria-label="Choose structure for Add annotated source"]')!;
    const cancel = [...group.querySelectorAll('button')].find(node => node.textContent === 'Cancel')!;
    await act(async () => cancel.click()); await act(async () => release(null)); await settle();
    await click('Generate candidates');
    const params = requests.find(row => row.url === '/api/jobs' && row.method === 'post')!.body.params;
    expect(params.rfd4_request.input.slots).toEqual([]);
    expect(params).not.toHaveProperty('native_sources');
    expect(document.querySelector('[aria-label="Annotated source 1"]')).toBeNull();
});
it('using an existing input inspects read-only without rebinding or renaming shared source names', async () => {
    const source = { path: 'inputs/original.cif.gz', expected_sha256: 'a'.repeat(64), model_number: 2, output_format: 'native' };
    await mount('rfd4_proteina', { rfd4_request: { input: { kind: 'annotated', slots: [] } }, native_sources: { retained_name: source } });
    await edit('Add annotated source', 'retained_name'); await settle(); await click('Generate candidates');
    const params = requests.find(row => row.url === '/api/jobs' && row.method === 'post')!.body.params;
    expect(params.rfd4_request.input.slots).toEqual(['retained_name']);
    expect(params.native_sources).toEqual({ retained_name: source });
    expect(requests.filter(row => row.url.includes('materialize')).map(row => row.body)).toEqual([{ ...source, output_format: 'native' }]);
});
it('compact source inspection preserves author-only PDB identity without undefined labels or a false remove action', async () => {
    materializer = async body => ({ ...receipt(body), format: 'pdb', native_format: 'pdb', model_number: 2,
        source_positions: [{ model_number: 2, auth_asym_id: ' ', auth_seq_id: '0', component_id: 'ACE', atoms: [{ auth_atom_id: 'CH3', type_symbol: 'C', model_atom_index: 0 }] }] });
    await mount('rfd4_proteina', { rfd4_request: { input: { kind: 'annotated', slots: ['selected'] } }, native_sources: { selected: { path: 'inputs/native.pdb', model_number: 2, output_format: 'native' } } });
    expect(document.querySelector('[data-viewer-url]')).not.toBeNull();
    const group = document.querySelector('[role="group"][aria-label="Choose structure for Annotated source 1"]')!;
    expect(group.textContent).not.toContain('Remove source');
    expect(group.textContent).not.toContain('undefined');
    expect(group.textContent).toContain('author (blank):0'); expect(group.textContent).toContain('ACE');
    await edit('Retained residue for selected', '0'); await click('Add exact selection from selected');
    expect(document.body.textContent).toContain('Native label residue mapping is unavailable');
    await click('Generate candidates');
    const wire = requests.find(row => row.url === '/api/jobs' && row.method === 'post')!.body;
    expect(wire.params.native_sources.selected.model_number).toBe(2);
    expect(wire.params.rfd4_request.input).toEqual({ kind: 'annotated', slots: ['selected'] });
});
