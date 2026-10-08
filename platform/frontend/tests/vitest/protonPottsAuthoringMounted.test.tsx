import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ model: null as any, library: null as any, project: null as any, save: vi.fn() }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), fetchModels: vi.fn(async () => ({ data: [state.model] })), fetchModelById: vi.fn(async () => ({ data: state.model })), fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })), fetchFiles: vi.fn(async () => ({ data: { entries: [] } })), fetchExecutionTargets: vi.fn(async () => ({ data: [{ id: 'worker', name: 'Fixture worker', active: true, state: 'ready', capabilities: {} }] })) }));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(), getProjectWorkflowSetup: vi.fn(async () => structuredClone(state.project)), saveProjectWorkflowSetupDraft: state.save }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: (props: any) => { state.library = props; return null; } }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api } from '../../src/lib/api';
import '../../src/components/ExecutionPlanApproval';
const contractRoot = process.env.BMS_PROTON_CONTRACT_ROOT || resolve('../..');
const python = process.env.BMS_TEST_PYTHON || 'python3';
const model = JSON.parse(execFileSync(python, ['-c', 'import json,sys,yaml;print(json.dumps(yaml.safe_load(open(sys.argv[1]))))', resolve(contractRoot, 'platform/api/config/models/protonpottsmpnn.yaml')], { encoding: 'utf8' }));
const options = Object.fromEntries(model.params.filter((p: any) => p.default !== undefined).map((p: any) => [p.name, p.default]));
let root: Root | undefined, client: QueryClient;
const original = api.defaults.adapter;
let requests: any[], previews: any[];
const textButton = (name: string) => [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === name)!;
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); }
async function click(name: string) { expect(textButton(name), name).toBeTruthy(); await act(async () => textButton(name).click()); await settle(); }
async function edit(name: string, value: string) { const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(`input[aria-label="${name}"],select[aria-label="${name}"]`)!; expect(input, name).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function mount(params?: any, project = false) {
    if (params) localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Reopened fixture', model_id: 'protonpottsmpnn', mode: 'redesign', params, execution_target_id: null }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/submit?model=protonpottsmpnn&mode=redesign${project ? '&project_id=destination&setup_context_id=setup' : ''}`]}><JobSubmission /></MemoryRouter></QueryClientProvider>)); await settle(); await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
beforeEach(() => {
    state.model = structuredClone(model); state.library = null; requests = []; previews = [];
    state.project = { project_id: 'destination', setup_context_id: 'setup', generation: 1, draft: {}, project_label: 'Destination', experiment_label: 'Experiment', workflow_label: 'Redesign', state: 'open', return_uri: '/projects/destination', field_errors: {}, global_experiment_id: 'global', domain_experiment_id: 'domain' };
    state.save.mockImplementation(async (_p, _s, request) => { state.project = { ...state.project, generation: state.project.generation + 1, draft: structuredClone(request.draft) }; return structuredClone(state.project); });
    api.defaults.adapter = async config => {
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        if (config.url === '/api/jobs' && config.method === 'post') { requests.push(body); return { config, data: { id: 'fixture' }, status: 200, statusText: 'OK', headers: {} }; }
        if (config.url === '/api/jobs/execution-plan/preview') { previews.push(body); return { config, data: { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: body, plan: { requested_json: body.params, effective_json: body.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] }, status: 200, statusText: 'OK', headers: {} }; }
        throw new Error(`Unexpected transport ${config.method} ${config.url}`);
    };
});
afterEach(async () => { await unmount(); api.defaults.adapter = original; localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); });
it('fresh standalone uses model-global defaults and saved/clone/reopen retains full native request including false/zero/null/empty', async () => {
    await mount(); await edit('Sequence job name', 'Native fixture'); await edit('target_pdb', 'inputs/complex.pdb'); await edit('binder_chain', 'B'); await edit('criteria.0.temperature', '0'); await edit('criteria.0.placement_label', '');
    expect(document.querySelector('[aria-label="engine_options.extended_vocab"]')).toBeTruthy();
    await click('Template Manager'); const saved = structuredClone(state.library.currentParams);
    expect(saved).toMatchObject({ ...options, target_pdb: 'inputs/complex.pdb', binder_chain: 'B', criteria: [{ ...options.criteria[0], temperature: 0, placement_label: '' }] });
    await act(async () => state.library.onSelect({ name: 'Saved fixture', model_id: 'protonpottsmpnn', mode: 'redesign', params: saved })); await settle();
    await unmount(); await mount(saved); expect((document.querySelector('[aria-label="criteria.0.temperature"]') as HTMLInputElement).value).toBe('0');
    await click('Launch Experiment'); expect(requests).toHaveLength(1); expect(requests[0]).toMatchObject({ model_id: 'protonpottsmpnn', mode: 'redesign', execution_target_id: null, params: saved });
    const normalized = JSON.parse(execFileSync(python, ['-c', `import sys,json;sys.path.insert(0,sys.argv[1]);from protonpottsmpnn_contract import normalize_params;print(json.dumps(normalize_params(json.load(sys.stdin))))`, resolve(contractRoot, 'scripts/lib')], { input: JSON.stringify(requests[0].params), encoding: 'utf8' }));
    expect(normalized).toEqual(requests[0].params);
    if (process.env.BMS_PROTON_UI_EVIDENCE) { mkdirSync(process.env.BMS_PROTON_UI_EVIDENCE, { recursive: true }); writeFileSync(resolve(process.env.BMS_PROTON_UI_EVIDENCE, 'standalone-payload.json'), JSON.stringify({ request: requests[0], normalized }, null, 2)); }
});
it('standalone remote launches only after existing prepared review, preserving exact native settings', async () => {
    await mount({ ...options, target_pdb: 'inputs/complex.pdb', binder_chain: 'B' }); await click('Vast · Fixture worker');
    await act(async () => textButton('Launch Experiment').click()); await settle(); expect(previews).toHaveLength(1); expect(requests).toHaveLength(0);
    await click('Approve and submit'); await settle(); expect(requests[0]).toMatchObject({ params: { ...options, target_pdb: 'inputs/complex.pdb', binder_chain: 'B' }, execution_target_id: 'worker', execution_plan_approval: 'a'.repeat(64) });
});
it('Project setup save and reopen keep the same global native parameters and explicit Local destination', async () => {
    state.project.draft = { model_id: 'protonpottsmpnn', mode: 'redesign', job_name: 'Project fixture', ...options, target_pdb: 'inputs/project.pdb', binder_chain: 'C', execution_target_id: null };
    await mount(undefined, true); await edit('criteria.0.neighbour_k', '0'); await click('Save draft');
    expect(state.project.draft).toMatchObject({ binder_chain: 'C', target_pdb: 'inputs/project.pdb', criteria: [{ ...options.criteria[0], neighbour_k: 0 }] });
    expect(requests).toHaveLength(0); await unmount(); await mount(undefined, true); expect((document.querySelector('[aria-label="criteria.0.neighbour_k"]') as HTMLInputElement).value).toBe('0'); expect(state.project.draft.execution_target_id).toBeNull();
});
