import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: () => <div>Inert WebGL boundary</div> }));
import { api } from '../../src/lib/api';
import { BinderNextRound } from '../../src/components/BinderNextRound';
import { Rfd4RetainedStateContinuation } from '../../src/components/Rfd4RetainedStateContinuation';
import { readRoundStorage, writeRoundStorage, candidateKey, uniqueCandidates } from '../../src/lib/binderSelectedRound';
import { activeInvocationPlan } from '../../src/lib/binderInvocationPlan';
import { hydrateGeneralSequenceDesign, generalSequenceRequest } from '../../src/lib/generalSequenceDesign';
import { repeatDeNovoSettings, sequenceDestinations } from '../../src/lib/deNovoContinuation';

const original = api.defaults.adapter;
let root: Root | undefined, client: QueryClient;
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = original; localStorage.clear(); window.history.replaceState({}, '', '/'); vi.restoreAllMocks(); });
async function click(text: string) { await act(async () => { const button = [...document.querySelectorAll('button')].find(b => b.textContent === text); expect(button, text).toBeTruthy(); button!.click(); }); }
const doc = (model: number) => ({ kind: 'native_document' as const, job_id: 'source', artifact_id: 'same-artifact', target_state: 'retained', model });

it('mounted selected operations save twice and cold-load through real migrated Project HTTP owners', async () => {
    const cwd = resolve(process.cwd(), '../..');
    const home = mkdtempSync(`${process.env.TMPDIR!}/operations-http-`);
    const calls: string[] = [];
    const bridge = (request: unknown) => JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['platform/api/tests/operations_receiving_http_bridge.py'], {
        cwd, input: JSON.stringify(request), encoding: 'utf8', env: { ...process.env, PYTHONPATH: `${cwd}/platform/api:${cwd}/platform/api/tests`, BMS_HOME: cwd, BMS_DATA: home, BMS_OPERATIONS_HTTP_HOME: home },
    }).trim().split('\n').at(-1)!);
    const setup = bridge({ url: '/test/setup' }); expect(setup.status).toBe(201);
    const reference = { project_id: setup.data.project_id, setup_context_id: setup.data.setup_context_id };
    api.defaults.adapter = async config => {
        calls.push(`${config.method} ${config.url}`);
        const result = config.url?.startsWith('/api/projects/') ? bridge({ method: config.method?.toUpperCase(), url: config.url, headers: config.headers.toJSON(), data: typeof config.data === 'string' ? JSON.parse(config.data) : config.data })
            : { status: 200, data: config.url?.startsWith('/api/models/') ? { params: [], modes: [] } : [] };
        expect(result.status, JSON.stringify(result.data)).toBeLessThan(300);
        return { data: result.data, status: result.status, statusText: 'OK', headers: {}, config };
    };
    const stages: any[] = [
        { enabled: false, drafts: { 'one:ensemble_design': { seed: 0, output: '', retained: false } }, stateDrafts: { 'one:ensemble_design': { s2: null } }, stage: { id: 'design', kind: 'sequence_design', input: { selected: true, scope: 'whole_composition' }, models: [{ invocation_id: 'one', model_id: 'caliby_experimental', mode: 'ensemble_design', params: { seed: 0 } }] } },
        { enabled: true, drafts: {}, stage: { id: 'predict', kind: 'prediction', input: { stage_id: 'design', invocation_id: 'one', scope: 'whole_composition' }, models: [{ invocation_id: 'two', model_id: 'protenix', mode: 'complex', params: {} }, { invocation_id: 'three', model_id: 'protenix', mode: 'predict', params: { seed: 0 }, execution_target_id: null }] } },
    ];
    writeRoundStorage('source', 'draft', { stages, target: 'remote-worker' });
    writeRoundStorage('source', 'selection', [doc(1), doc(2)]);
    const mount = async () => {
        client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
        await act(async () => root!.render(<QueryClientProvider client={client}><BinderNextRound sourceJobId="source" selectedDesignIds={[]} projectDraftRef={reference} operationScope="whole_composition" onOpenJob={() => {}} renderEditor={() => null} /></QueryClientProvider>));
        await click('Next round (2)');
    };
    await mount();
    expect(document.body.textContent).toContain('Source is off');
    expect(calls.every(call => call.startsWith('get '))).toBe(true);
    await click('Save operation draft to Project');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
    expect([...document.querySelectorAll('[role="alert"]')].map(node => node.textContent).filter(text => !text?.startsWith('Selected worker'))).toEqual([]);
    await click('Save operation draft to Project');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
    expect([...document.querySelectorAll('[role="alert"]')].map(node => node.textContent).filter(text => !text?.startsWith('Selected worker'))).toEqual([]);
    const saved = readRoundStorage<any>('source', 'draft', {});
    expect(saved.stages).toEqual(stages);
    await close(); localStorage.clear();
    // Load is explicit, never a page-visit overwrite/launch.
    writeRoundStorage('source', 'selection', [doc(1), doc(2)]);
    await mount(); await click('Load operation draft from Project');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
    expect([...document.querySelectorAll('[role="alert"]')].map(node => node.textContent).filter(text => !text?.startsWith('Selected worker'))).toEqual([]);
    expect(readRoundStorage<any>('source', 'draft', {})).toEqual(saved);
    expect(readRoundStorage('source', 'selection', [])).toEqual([doc(1), doc(2)]);
    const wire = activeInvocationPlan(saved.stages);
    expect(wire.stages[0].input).toEqual({ stage_id: 'design', invocation_id: 'one', scope: 'whole_composition' });
    expect((wire.stages[0] as any).models[0]).not.toHaveProperty('execution_target_id');
    expect((wire.stages[0] as any).models[1].execution_target_id).toBeNull();
    expect(calls.filter(call => call.startsWith('put '))).toHaveLength(2);
    const url = `/api/projects/${reference.project_id}/workflow-setups/${reference.setup_context_id}`;
    const cold = bridge({ url });
    expect(cold.data.draft.native_job_request.params.rfd4_request.settings.seed).toBe(0);
    expect(cold.data.draft.native_mode_drafts.inactive).toEqual({ seed: 0, enabled: false, optional: null, rows: [], sequence: '' });
    const conflict = bridge({ url: `${url}/draft`, method: 'PUT', headers: { 'Idempotency-Key': 'stale' }, data: { expected_generation: 0, draft: {} } });
    expect(conflict.status).toBe(409);
    expect(bridge({ url }).data.draft).toEqual(cold.data.draft);
    expect(calls.some(call => call.includes('/selected'))).toBe(false);
}, 120000);

it('projects MODEL identities without collapsing extra and legacy selections; suite scope is additive only', async () => {
    writeRoundStorage('source', 'selection', [doc(1)]);
    api.defaults.adapter = async config => ({ data: [], status: 200, statusText: 'OK', headers: {}, config });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><BinderNextRound sourceJobId="source" selectedDesignIds={[]} selectedNativeSources={[{ job_id: 'source', artifact_id: 'same-artifact', document: { artifact_id: 'same-artifact', target_state: 'retained', model: 2 } } as any]} operationScope="whole_composition" onOpenJob={() => {}} renderEditor={() => null} /></QueryClientProvider>));
    await click('Next round (2)'); await click('Add Blind prediction');
    expect(readRoundStorage<any>('source', 'draft', {}).stages[0].stage.input.scope).toBe('whole_composition');
    expect(uniqueCandidates([doc(1), doc(2), doc(1)]).map(candidateKey)).toEqual([candidateKey(doc(1)), candidateKey(doc(2))]);
    expect(generalSequenceRequest(hydrateGeneralSequenceDesign()).sequence_design).toMatchObject({ schema_version: 2, enabled: false });
    expect(sequenceDestinations).toContainEqual(['caliby_experimental', 'sidechain_pack', 'Caliby sequence-preserving side-chain packing']);
});

it('repeat settings is an unsent ordinary clone retaining zero, false, null and native request, not a retry', async () => {
    const calls: string[] = [];
    const job: any = { id: 'failed', name: 'Native', model_id: 'rfd4_proteina', mode: 'generate', params: { rfd4_request: { settings: { seed: 0, verbose: false }, search: { resume_seed: null } } }, sequence_design: { schema_version: 2, enabled: false, round_plan: { schema_version: 2, stages: [] } }, execution_target_id: null };
    api.defaults.adapter = async config => { calls.push(`${config.method} ${config.url}`); return { data: job, status: 200, statusText: 'OK', headers: {}, config }; };
    const href = await repeatDeNovoSettings('failed');
    expect(href).toContain('/submit?');
    const clone = JSON.parse(localStorage.getItem('clonedJobData')!);
    expect(clone.params).toEqual(job.params); expect(clone.sequence_design).toEqual(job.sequence_design);
    expect(clone).not.toHaveProperty('id'); expect(calls).toEqual(['get /api/jobs/failed']);
});

it('binds loaded set/revision until exact membership changes and preserves historical binder scope', async () => {
    api.defaults.adapter = async config => ({ data: config.url === '/api/jobs/source' ? { saved_selection_sets: [{ id: 'set', name: 'Chosen', revision: 4, candidate_refs: [doc(2)] }] } : [], status: 200, statusText: 'OK', headers: {}, config });
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><BinderNextRound sourceJobId="source" selectedDesignIds={[]} onOpenJob={() => {}} renderEditor={() => null} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    await click('Load Chosen · revision 4'); await click('Next round (1)');
    await click('Add Blind prediction');
    expect(readRoundStorage<any>('source', 'draft', {}).stages[0].stage.input).toEqual({ selected: true });
    expect(document.querySelector('[aria-label="Next round composer"]')!.textContent).toContain('saved_set_id');
    expect(readRoundStorage<any>('source', 'draft', {}).selection_set).toEqual({ saved_set_id: 'set', revision: 4, candidate_refs: [doc(2)] });
    await click('Remove');
    expect(document.querySelector('[aria-label="Next round composer"]')!.textContent).not.toContain('saved_set_id');
});

it('hands S04 the exact nonprimary document/hash/MODEL and return without adopting or submitting it', async () => {
    const edit = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    window.history.replaceState({}, '', '/designs/source?native_candidate=exact&model_number=2');
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fixture');
    api.defaults.adapter = async config => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config });
    const documents = [{ artifact_id: 'primary', primary: true, model: 1 }, { artifact_id: 'alternate', target_state: 'scored', model: 2, sha256: 'hash-bound', download_url: '/exact.cif' }];
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="source" artifactId="alternate" targetState="scored" onEditExactDocument={edit} adapter={{ key: 'exact-receiving', title: 'Native fixture', label: () => 'Candidate', fetchPage: async () => ({ receipt: {}, publication: {}, total: 1, offset: 0, limit: 50, records: [{ candidate_key: 'exact', structures: documents }] }) }} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    await click('Edit this exact document');
    expect(edit).toHaveBeenCalledWith({ job_id: 'source', document: { artifact_id: 'alternate', target_state: 'scored' }, model_number: 2, expected_sha256: 'hash-bound' }, { candidateRef: { kind: 'native_document', job_id: 'source', artifact_id: 'alternate', target_state: 'scored', model: 2 }, returnTo: expect.stringContaining('model_number=2') });
    expect(localStorage.getItem('clonedJobData')).toBeNull();
});

it('retained RFD4 state carries actual artifact hash and explicit seed zero even with unknown candidate relation', async () => {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<Rfd4RetainedStateContinuation source={{ job_id: 'source', document: { artifact_id: 'structure', target_state: 'other' }, model_number: 2 }} candidate="candidate" page={{ records: [], artifacts: [{ role: 'sampling_state', artifact_id: 'state', source_path: '/retained/state.pt', path: 'state.pt', sha256: 'exact-state-hash' }] } as any} />));
    await act(async () => { const select = document.querySelector('select')!; select.value = 'state'; select.dispatchEvent(new Event('change', { bubbles: true })); const seed = document.querySelector('input')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(seed, '0'); seed.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(document.body.textContent).toContain('Association with this molecule is not reported');
    const href = new URL(document.querySelector('a')!.href);
    expect(JSON.parse(href.searchParams.get('rfd4_state')!)).toMatchObject({ artifact_id: 'state', sha256: 'exact-state-hash', seed: 0 });
    expect(JSON.parse(href.searchParams.get('source_structure')!).model_number).toBe(2);
    expect(href.searchParams.get('return_to')).toContain('model_number=2');
});
