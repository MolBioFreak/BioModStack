import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { resolve } from 'node:path';
import { writeFileSync, readFileSync } from 'node:fs';
import { request as httpRequest } from 'node:http';
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
import { JobSubmission } from '../../src/components/JobSubmission';
import '../../src/components/ProteinModificationTemplate';
import { continuationHref, type Rfd4Destination } from '../../src/lib/deNovoContinuation';
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <output data-inert-webgl /> }));
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
const evidence = process.env.BMS_PARENT_LIFECYCLE_ROOT;
const suite = evidence ? it : it.skip;
let root: Root | undefined, client: QueryClient;
let previous: typeof api.defaults.adapter;
let requests: any[];
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
async function http(method: string, path: string, body?: any, headers: any = {}) {
    return new Promise<any>((resolve, reject) => {
        const req = httpRequest(`http://127.0.0.1:${process.env.BMS_PARENT_PORT ?? '18879'}${path}`, { method, headers: { 'Content-Type': 'application/json', ...headers } }, res => {
            let text = ''; res.on('data', chunk => text += chunk); res.on('end', () => {
                let data; try { data = JSON.parse(text); } catch { data = text; }
                resolve({ status: res.statusCode, data });
            });
        });
        req.on('error', reject); req.end(body === undefined ? undefined : JSON.stringify(body));
    });
}
beforeEach(() => {
    if (!evidence) return;
    previous = api.defaults.adapter; requests = []; localStorage.clear(); sessionStorage.clear();
    window.history.replaceState({}, '', '/submit'); // Match the ordinary launcher placement owner, not MemoryRouter alone.
    api.defaults.adapter = async config => {
        const query = new URLSearchParams(Object.entries(config.params ?? {}).filter(([,v]) => v !== undefined).map(([k,v]) => [k,String(v)])).toString();
        const path = config.url! + (query ? '?' + query : '');
        const body = config.data ? JSON.parse(config.data) : undefined;
        if (config.method !== 'get' && !path.startsWith('/api/user-templates') && !path.endsWith('/draft') && path !== '/api/files/materialize-structure') throw new Error(`Forbidden parent mutation ${path}`);
        const response = await http(config.method ?? 'get', path, body, config.headers.toJSON());
        requests.push({ path, method: config.method, body, ...response });
        if (response.status >= 400) throw Object.assign(new Error(JSON.stringify(response)), { response });
        return { config, status: response.status, statusText: 'native', headers: {}, data: response.data };
    };
});
afterEach(async () => {
    if (!evidence) return;
    writeFileSync(resolve(evidence, expect.getState().currentTestName!.replace(/[^a-z0-9]/gi, '_') + '.json'), JSON.stringify(requests,null,2));
    await unmount(); api.defaults.adapter = previous;
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
const nativeRequest = { input: { kind: 'unconditional', name: 'native parent', chains: [{ chain_id: 'A', min_length: 73, max_length: 99 }] }, settings: { seed: 0, delete_failing_pre_oracle: false, search: { checkpoints: null, save_states: false }, metrics_tags: [] } };
async function mount(route = '/submit?model=rfd4_proteina&mode=generate', clone?: any) {
    if (clone) localStorage.setItem('clonedJobData', JSON.stringify(clone));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    const expectedModel = clone?.model_id === 'complexa' || route.includes('model=complexa') ? 'complexa' : 'rfd4_proteina';
    await vi.waitFor(async () => { await settle(); expect(document.querySelector(`[data-native-de-novo="${expectedModel}"] fieldset`),document.body.textContent ?? '').not.toBeNull(); }, { timeout: 10000 });
}
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(e => !e.closest('[hidden]') && e.textContent?.trim() === text);
    expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle();
}
async function edit(node: HTMLInputElement | HTMLSelectElement, value: string) {
    expect(node).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node,value); node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input',{bubbles:true})); });
}
async function saveTemplate(name: string) {
    await click('Template Manager');
    await edit(document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!,`${name} ${crypto.randomUUID()}`);
    const before = requests.filter(r => r.path === '/api/user-templates' && r.method === 'post').length;
    await click('Save Template');
    await vi.waitFor(() => expect(requests.filter(r => r.path === '/api/user-templates' && r.method === 'post')).toHaveLength(before+1));
    const saved = requests.filter(r => r.path === '/api/user-templates' && r.method === 'post').at(-1);
    expect(saved.status).toBe(201); return saved.data;
}
async function coldLoad(saved: any) {
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY,'ambient-remote');
    const route = saved.params.native_job_request ? `/submit?model=${saved.params.native_job_request.model_id}&mode=${saved.mode}` : undefined;
    await mount(route); await click('Template Manager'); await click('← Back to list');
    if (!saved.params.native_job_request) {
        const allWorkflows = [...document.querySelectorAll('label')].find(node => node.textContent?.includes('All workflows'))?.querySelector<HTMLInputElement>('input[type="checkbox"]');
        expect(allWorkflows).toBeTruthy();
        await act(async () => allWorkflows!.click());
    }
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain(saved.name); });
    const row = [...document.querySelectorAll('button')].find(e => e.textContent === 'Load' && e.parentElement?.parentElement?.textContent?.includes(saved.name));
    expect(row).toBeTruthy(); await act(async () => row!.click()); await settle();
}
for (const destination of ['rfd4_partial','rfd4_inverse','rfd4_evaluate','rfd4_state'] as Rfd4Destination[]) {
    suite(`ordinary parent receives emitted ${destination} route with real MODEL 2 materialization and retained hash`, async () => {
        const fixture = JSON.parse(readFileSync(resolve(evidence!,'source.json'),'utf8'));
        const url = new URL(continuationHref(fixture.source,destination),'http://localhost');
        if (destination === 'rfd4_state') url.searchParams.set('rfd4_state',JSON.stringify(fixture.state));
        await mount(url.pathname+url.search);
        await vi.waitFor(async () => { await settle(); expect(requests.some(r => r.path === '/api/files/materialize-structure' && r.status === 200)).toBe(true); });
        const saved = await saveTemplate(destination);
        const state = saved.params.editor_state;
        const request = saved.params.native_job_request;
        const materialized = requests.find(r => r.path === '/api/files/materialize-structure');
        expect(materialized.body).toEqual({...fixture.source,output_format:'native'});
        expect(materialized.data.model_number).toBe(2);
        expect(materialized.data.native_sha256).toBe(fixture.source.expected_sha256);
        expect(state.native_recipe).toBe(destination === 'rfd4_partial' ? 'partial' : destination === 'rfd4_inverse' ? 'inverse' : 'annotated');
        expect(request.model_id).toBe('rfd4_proteina');
        expect(request.mode).toBe(destination === 'rfd4_evaluate' ? 'evaluate' : 'generate');
        expect(request.params.rfd4_request.input).toEqual({kind:'annotated',slots:['selected']});
        expect(request.params.native_sources.selected).toEqual(materialized.data.source_structure ?? fixture.source);
        expect(request.source_structure).toEqual(request.params.native_sources.selected);
        if (destination === 'rfd4_inverse') expect(request.params.rfd4_request.groups).toEqual({generation:'inverse_folding'});
        if (destination === 'rfd4_state') {
            expect(request.params.rfd4_request.resume_slot).toBe('resume');
            expect(request.params.rfd4_request.settings.seed).toBe(0);
            expect(request.params.native_sources.resume).toEqual({kind:'file',path:fixture.state.path,expected_sha256:fixture.state.sha256});
        }
        expect(request.params).not.toHaveProperty('native_recipe');
        await coldLoad(saved);
        const reopened = await saveTemplate(`${destination} cold readback`);
        expect(reopened.params.native_job_request).toEqual(request);
        expect(reopened.params.editor_state.native_recipe).toBe(state.native_recipe);
        // A Job clone restores submitted science, not editor-only inactive history.
        await unmount();
        await mount(`/submit?model=rfd4_proteina&mode=${request.mode}`, request);
        const cloned = await saveTemplate(`${destination} native Job clone`);
        expect(cloned.params.native_job_request).toEqual(request);
    },30000);
}
suite('ordinary parent retains inactive partial recipe and MODEL source across SQLite cold Template reopen', async () => {
    const fixture = JSON.parse(readFileSync(resolve(evidence!,'source.json'),'utf8'));
    const route = new URL(continuationHref(fixture.source,'rfd4_partial'),'http://localhost');
    await mount(route.pathname+route.search);
    await edit(document.querySelector<HTMLInputElement>('[aria-label="partial_t_backbone"]')!,'0');
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="RFD4 task"]')!,'source_free');
    const saved = await saveTemplate('Inactive partial');
    const state = saved.params.editor_state;
    expect(Object.values(state.native_task_drafts).some((value:any) => value.settings?.generation?.args?.partial_t_backbone === 0)).toBe(true);
    expect(Object.values(state.native_source_drafts).some((value:any) => value?.selected?.model_number === 2)).toBe(true);
    expect(saved.params.native_job_request.params).not.toHaveProperty('native_sources');
    await coldLoad(saved);
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="RFD4 task"]')!,'partial');
    expect(document.querySelector<HTMLInputElement>('[aria-label="partial_t_backbone"]')!.value).toBe('0');
    const restored = await saveTemplate('Restored partial');
    expect(restored.params.native_job_request.params.native_sources.selected.model_number).toBe(2);
    expect(restored.params.native_job_request.params.native_sources.selected.expected_sha256).toBe(fixture.source.expected_sha256);
    expect(restored.params.native_job_request.params).not.toHaveProperty('native_task_drafts');
},30000);
suite('ordinary parent Complexa inactive reward controls survive cold SQLite Template Load without entering active request', async () => {
    const reward = { af2_weight: 0, rf3folding: null };
    await mount('/submit?model=complexa&mode=generate',{...clone,model_id:'complexa',params:{complexa_request:{task:'protein_binder',operation:'generate',reward,seed:0}}});
    const disclose = async () => {
        const summary = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Reward activation')!;
        expect(summary).toBeTruthy();
        await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
    };
    await disclose(); await edit(document.querySelector<HTMLSelectElement>('[aria-label="Native reward state"]')!,'off');
    const saved = await saveTemplate('Inactive reward');
    expect(saved.params.native_job_request.params.complexa_request.reward).toBeNull();
    expect(Object.values(saved.params.editor_state.native_control_drafts)).toContainEqual({configured_reward:reward});
    await coldLoad(saved); await disclose();
    expect(document.querySelector<HTMLSelectElement>('[aria-label="Native reward state"]')!.value).toBe('off');
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Native reward state"]')!,'configured');
    const restored = await saveTemplate('Restored reward');
    expect(restored.params.native_job_request.params.complexa_request.reward).toEqual(reward);
    expect(restored.params.native_job_request.params).not.toHaveProperty('native_control_drafts');
},30000);
const clone = { name:'Parent retained exact', model_id:'rfd4_proteina', mode:'generate', execution_target_id:null, sequence_design:null, params:{rfd4_request:nativeRequest} };
suite.each(['omitted', 'null'])('cloned %s optional-operation envelope survives real SQLite Template cold Load', async presence => {
    const source: any = { ...clone };
    if (presence === 'omitted') delete source.sequence_design;
    await mount(undefined, source);
    const saved = await saveTemplate(`Optional operation ${presence}`);
    expect(Object.hasOwn(saved.params.native_job_request, 'sequence_design')).toBe(presence === 'null');
    if (presence === 'null') expect(saved.params.native_job_request.sequence_design).toBeNull();
    await coldLoad(saved);
    const reopened = await saveTemplate(`Optional operation ${presence} reopened`);
    expect(Object.hasOwn(reopened.params.native_job_request, 'sequence_design')).toBe(presence === 'null');
    if (presence === 'null') expect(reopened.params.native_job_request.sequence_design).toBeNull();
}, 30000);
suite('all five populated engine drafts survive real SQLite template Save and cold Load', async () => {
    const opaque = { document: { hash: 'retained' }, provenance: null, selection: [] };
    await mount(undefined, { ...clone, params: { ...clone.params, opaque_import: opaque } });
    const named = (name: string) => [...document.querySelectorAll('label')].find(node => !node.closest('[hidden]') && node.firstChild?.textContent === name)?.querySelector('input,select') as HTMLInputElement | HTMLSelectElement;
    const engine = async (name: string) => { await edit(named('Engine'), name); await settle(); };
    await engine('rfd3'); await edit(named('Minimum length'), '111');
    await engine('disco'); await edit(named('Target lengths'), '144');
    await engine('laproteina'); await edit(named('Target lengths'), '155');
    await engine('complexa');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Complexa task"]')).not.toBeNull(); });
    await edit(document.querySelector<HTMLSelectElement>('[aria-label="Complexa task"]')!, 'AME');
    await engine('rfd4_proteina');
    const saved = await saveTemplate('Five-engine drafts');
    expect(saved.params.native_job_request.params.rfd4_request).toEqual(nativeRequest);
    expect(saved.params.editor_state.opaque_import).toEqual(opaque);
    expect(saved.params.native_job_request.params).not.toHaveProperty('opaque_import');
    await coldLoad(saved);
    await engine('rfd3'); expect(named('Minimum length').value).toBe('111');
    await engine('disco'); expect(named('Target lengths').value).toBe('144');
    await engine('laproteina'); expect(named('Target lengths').value).toBe('155');
    await engine('complexa');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector<HTMLSelectElement>('[aria-label="Complexa task"]')?.value).toBe('AME'); });
    await engine('rfd4_proteina');
    const again = await saveTemplate('Five-engine second save');
    expect(again.params.native_job_request.params.rfd4_request).toEqual(nativeRequest);
    expect(again.params.editor_state.opaque_import).toEqual(opaque);
    expect(again.params.native_job_request.params).not.toHaveProperty('opaque_import');
    expect(requests.some(r => r.path === '/api/jobs')).toBe(false);
}, 30000);
suite('last generation selection survives redesign and a real SQLite cold Template Load', async () => {
    await mount();
    const named = (name: string) => [...document.querySelectorAll('label')].find(node => !node.closest('[hidden]') && node.firstChild?.textContent === name)?.querySelector('input,select') as HTMLInputElement | HTMLSelectElement;
    await edit(named('Engine'), 'rfd3');
    await edit(named('Goal'), 'rna_conditioned');
    await edit(named('Engine'), 'disco');
    await edit(named('Target lengths'), '144');
    await edit(named('Goal'), 'dna_conditioned');
    await click('Redesign structure');
    const saved = await saveTemplate('Return to generation cold');
    expect(saved.params.editor_state.suite_last_generation).toMatchObject({ generator: 'disco', design_task: 'dna_conditioned' });
    await coldLoad(saved);
    await click('Generate');
    expect(named('Engine').value).toBe('disco');
    expect(named('Goal').value).toBe('dna_conditioned');
    await edit(named('Goal'), 'unconditional');
    expect(named('Target lengths').value).toBe('144');
    await edit(named('Engine'), 'rfd3');
    expect(named('Goal').value).toBe('rna_conditioned');
}, 30000);

suite('ordinary parent clone saves native SQLite Template and cold Load preserves sparse science and explicit Local', async () => {
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY,'ambient-remote');
    await mount(undefined,clone);
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    const saved = await saveTemplate('Parent HTTP cold');
    expect(saved.params.native_job_request.params.rfd4_request).toEqual(nativeRequest);
    expect(saved.params.native_job_request.execution_target_id).toBeNull();
    const list = await http('GET','/api/user-templates');
    expect(list.data.find((r:any) => r.id === saved.id)).toEqual(saved);
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY,'ambient-remote');
    await mount(`/submit?model=${saved.params.native_job_request.model_id}&mode=${saved.mode}`); await click('Template Manager'); await click('← Back to list');
    const row = [...document.querySelectorAll('button')].find(e => e.textContent === 'Load' && e.parentElement?.parentElement?.textContent?.includes(saved.name));
    expect(row).toBeTruthy(); await act(async () => row!.click()); await settle();
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    const again = await saveTemplate('Parent HTTP cold second');
    expect(again.params.native_job_request).toEqual(saved.params.native_job_request);
    expect(requests.some(r => r.path === '/api/jobs')).toBe(false);
}, 30000);
suite.each(['/submit','/bms/submit'])('ordinary fresh parent at %s snapshots inherited ambient placement separately from explicit Local clone', async (pathname) => {
    window.history.replaceState({}, '', pathname);
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY,'ambient-remote');
    await mount();
    const saved = await saveTemplate('Inherited parent placement');
    expect(saved.params.editor_state.execution_target_id).toBe('ambient-remote');
    expect(saved.params.native_job_request.execution_target_id).toBe('ambient-remote');
    expect(saved.params.native_job_request.params).toEqual({rfd4_request:{}});
},30000);
suite('ordinary Project retains MODEL 2 partial recipe through two Saves cold reopen and Prepare', async () => {
    const seed = JSON.parse(readFileSync(resolve(evidence!,'seed.json'),'utf8'));
    const fixture = JSON.parse(readFileSync(resolve(evidence!,'source.json'),'utf8'));
    const emitted = new URL(continuationHref(fixture.source,'rfd4_partial'),'http://localhost');
    await mount(emitted.pathname+emitted.search);
    await edit(document.querySelector<HTMLInputElement>('[aria-label="partial_t_backbone"]')!,'0');
    const saved = await saveTemplate('Partial Project source'); await unmount();
    const created = await http('POST',`/api/projects/${seed.project_id}/workflow-setups`,{schema:'bms.project-workflow-setup.create.v1',relationship_kind:'primary',experiment:{name:'Partial MODEL 2',objective:'Cold native source custody'},domain_kind:'protein_in_silico',capability_id:'protein.native.rfd4_proteina.generate'},{'Idempotency-Key':crypto.randomUUID()});
    expect(created.status).toBe(201);
    const path = `/api/projects/${seed.project_id}/workflow-setups/${created.data.setup_context_id}`;
    const route = `/submit?model=rfd4_proteina&mode=generate&project_id=${seed.project_id}&setup_context_id=${encodeURIComponent(created.data.setup_context_id)}`;
    expect((await http('PUT',path+'/draft',{expected_generation:0,draft:saved.params},{'Idempotency-Key':crypto.randomUUID()})).status).toBe(200);
    await mount(route);
    for (let i=0;i<2;i++) {
        const before=(await http('GET',path)).data;
        await click('Save draft');
        await vi.waitFor(async () => expect((await http('GET',path)).data.generation).toBe(before.generation+1));
    }
    await unmount(); await mount(route);
    expect(document.querySelector<HTMLSelectElement>('[aria-label="RFD4 task"]')!.value).toBe('partial');
    expect(document.querySelector<HTMLInputElement>('[aria-label="partial_t_backbone"]')!.value).toBe('0');
    const cold=(await http('GET',path)).data;
    expect(cold.draft.native_job_request).toEqual(saved.params.native_job_request);
    expect(cold.draft.native_sources.selected.model_number).toBe(2);
    expect(cold.draft.native_sources.selected.expected_sha256).toBe(fixture.source.expected_sha256);
    const prepared=await http('POST',path+'/prepare-launch',{expected_generation:cold.generation},{'Idempotency-Key':crypto.randomUUID()});
    writeFileSync(resolve(evidence!,'source-project-prepared.json'),JSON.stringify(prepared,null,2));
    expect(prepared.status).toBe(200); expect(prepared.data.preparation_id).toBeTruthy();
},30000);
suite('ordinary parent Project two acknowledged Saves cold reopen and native Prepare retain exact active request', async () => {
    const seed = JSON.parse(readFileSync(resolve(evidence!,'seed.json'),'utf8'));
    const path = `/api/projects/${seed.project_id}/workflow-setups/${seed.setup.setup_context_id}`;
    const route = `/submit?model=rfd4_proteina&mode=generate&project_id=${seed.project_id}&setup_context_id=${encodeURIComponent(seed.setup.setup_context_id)}`;
    await mount(undefined,clone); const saved = await saveTemplate('Parent Project source'); await unmount();
    const initial = await http('GET',path);
    const seeded = await http('PUT',path+'/draft',{expected_generation:initial.data.generation,draft:saved.params},{'Idempotency-Key':crypto.randomUUID()});
    expect(seeded.status).toBe(200);
    await mount(route);
    for (let i = 0; i < 2; i++) {
        const before = (await http('GET',path)).data;
        await click('Save draft');
        await vi.waitFor(async () => expect((await http('GET',path)).data.generation).toBe(before.generation+1));
    }
    const stored = (await http('GET',path)).data;
    expect(stored.draft.native_job_request).toEqual(saved.params.native_job_request);
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY,'ambient-remote'); await mount(route);
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    await click('Save draft');
    await vi.waitFor(async () => expect((await http('GET',path)).data.generation).toBe(stored.generation+1));
    const cold = (await http('GET',path)).data;
    expect(cold.draft.native_job_request).toEqual(saved.params.native_job_request);
    const key = crypto.randomUUID();
    const exact = { expected_generation: cold.generation, draft: saved.params };
    const updated = await http('PUT',path+'/draft',exact,{'Idempotency-Key':key});
    expect(updated.status).toBe(200);
    const replay = await http('PUT',path+'/draft',exact,{'Idempotency-Key':key});
    expect(replay.status).toBe(200); expect(replay.data).toEqual(updated.data);
    const changed = await http('PUT',path+'/draft',{...exact,draft:{...saved.params,editor_state:{...saved.params.editor_state,job_name:'conflicting edit'}}},{'Idempotency-Key':key});
    expect(changed.status).toBe(409);
    const stale = await http('PUT',path+'/draft',exact,{'Idempotency-Key':crypto.randomUUID()});
    expect(stale.status).toBe(409);
    const afterConflict = (await http('GET',path)).data;
    expect(afterConflict.generation).toBe(updated.data.generation);
    expect(afterConflict.draft.native_job_request).toEqual(saved.params.native_job_request);
    const prepared = await http('POST',path+'/prepare-launch',{expected_generation:afterConflict.generation},{'Idempotency-Key':crypto.randomUUID()});
    writeFileSync(resolve(evidence!,'project-prepared.json'),JSON.stringify(prepared,null,2));
    expect(prepared.status).toBe(200);
    expect(prepared.data.preparation_id).toBeTruthy();
    expect(requests.some(r => r.path.includes('/launch') || r.path === '/api/jobs')).toBe(false);
}, 30000);
