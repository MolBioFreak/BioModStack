import React, { useState } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { api } from '../../src/lib/api';
import { ProtonPottsSettings } from '../../src/components/ProtonPottsSettings';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import ProtonPottsResults from '../../src/components/ProtonPottsResults';
import { criteriaFields } from '../../src/lib/protonPottsSettings';
import { parseProtonPottsResults, predictProtonPottsSelected } from '../../src/lib/protonPottsResults';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { CohortPlot } from '../../src/components/CohortAnalytics';
import '../../src/components/ExecutionPlanApproval';
vi.mock('react-plotly.js', () => ({ default: () => <div data-native-chart /> }));
vi.mock('../../src/components/StructuralSourceFiles', () => ({ StructuralSourceFiles: () => null }));
const contractRoot = process.env.BMS_PROTON_CONTRACT_ROOT || resolve('../..');
const schema = JSON.parse(readFileSync(resolve(contractRoot, 'schemas/protonpottsmpnn_parameters.v1.json'), 'utf8'));
const nativeRequest = JSON.parse(readFileSync(resolve(contractRoot, 'schemas/protonpottsmpnn_default_request.v1.json'), 'utf8'));
const model = { id: 'protonpottsmpnn', name: 'ProtonPottsMPNN', modes: [{ id: 'redesign', params: Object.keys(schema.properties) }], params: Object.entries(schema.properties).map(([name, s]: [string, any]) => ({ name, type: s.type === 'array' ? 'array' : s.type === 'object' ? 'object' : s.type === 'integer' ? 'integer' : s.type, default: s.default, label: name })) };
const original = api.defaults.adapter;
let client: QueryClient, tree: ReactTestRenderer;
const posted: any[] = [];
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (name: string) => tree.root.findAllByType('button').find(node => text(node) === name)!;
const edit = async (name: string, value: string) => { await act(async () => tree.root.findByProps({ 'aria-label': name }).props.onChange({ target: { value } })); };
const check = async (name: string, checked: boolean) => { await act(async () => tree.root.findByProps({ 'aria-label': name }).props.onChange({ target: { checked } })); };
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); };
async function mount(node: React.ReactNode) { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }); await act(async () => { tree = create(<QueryClientProvider client={client}>{node}</QueryClientProvider>); }); await flush(); }
const native = { binder_chain: 'A', canonical_sequence: 'HD', extended_tokens: ['HIS-P', 'ASP-D'], extended_vocab: true, scheme: 'native', sample: 0, final_potts_energy: 0, seed_idx: null, protonation_type: 'HIS-P', site_rank: 0, center_res_id: 17, n_neighbours: 1, selective_energy: 0, n_centers: 1, center_res_ids: [17], center_protonation_types: ['HIS-P'], selective_energies: [0], global_protonation_dH: -2, placement_prob: 0, placement_entropy: 0, sequence_decoded_prob_score: null, sequence_entropy: 0, method: 'block_descent', backend: 'potts', selective_source: 'potts', placement_label: '', placement_region: 'all', placement_by: 'scan_potts', combined_lambda: 0.3, repetitive_window_weight: 1, block_size: 3, sweep_order: 'position', neighbour_k: 16, max_mutations: 20, energy_trajectory: [{ step: 0, selective_energy: 0, global_protonation_dH: -2, potts_energy: 0, canonical_sequence: 'HD', extended_tokens: 'HIS-P ASP-D' }] };
const result = { contract: 'protonpottsmpnn_design.v1', source: nativeRequest.source, request: nativeRequest, designs: [{ design_id: 'criteria0:native0', criteria_index: 0, native_design_id: 'native0', native }], runtime: { checkpoint: 'v6' }, artifacts: ['designs.json'] };
beforeEach(() => {
    posted.length = 0;
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    api.defaults.adapter = async config => {
        let data: any = [];
        if (config.url === '/api/models/protonpottsmpnn') data = model;
        else if (config.url?.startsWith('/api/models/')) data = { modes: [{ id: 'complex', params: ['sequence', 'cycles'] }], params: [{ name: 'sequence', type: 'string', default: '' }, { name: 'cycles', type: 'integer', default: 3, label: 'Prediction cycles' }] };
        else if (config.url === '/api/execution-targets') data = [{ id: 'worker', name: 'Fixture worker', active: true, state: 'ready', capabilities: {} }];
        else if (config.url?.startsWith('/api/designs/')) data = { job_id: 'source', pdb_path: 'input.pdb' };
        else if (config.url?.endsWith('/selection-context')) data = { candidate_documents: {} };
        else if (config.url?.endsWith('/protonpottsmpnn/results')) data = result;
        if (config.method === 'post') { posted.push({ url: config.url, body: JSON.parse(config.data) }); data = { source_job_id: 'source', root_job_id: 'source', selected_design_count: 1, operation: 'predict_protenix', launched_jobs: [{ id: 'child', name: 'Fixture child', status: 'queued' }] }; }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = original; vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); document.body.replaceChildren(); });
it('edits every native criterion, typed map/list/sweep values, checkpoint options without JSON coercion', async () => {
    let saved: any;
    function Form() { const [value, setValue] = useState(structuredClone(nativeRequest.options)); saved = value; return <ProtonPottsSettings parameters={model.params} values={value} onChange={(key, v) => setValue(prev => ({ ...prev, [key]: v }))} renderScalar={() => null} />; }
    await mount(<Form />);
    expect(criteriaFields.map(f => f.name).sort()).toEqual(Object.keys(schema.properties.criteria.items.properties).sort());
    for (const field of criteriaFields) expect(tree.root.findByProps({ 'data-sequence-designer-field': field.name }).findAll(node => ['input', 'select', 'button'].includes(String(node.type))).length, field.name).toBeGreaterThan(0);
    await check('criteria.0.temperature.sweep', true); await edit('criteria.0.temperature.0', '0'); await act(async () => button('Add criteria.0.temperature sweep value').props.onClick()); await edit('criteria.0.temperature.1', '0.125');
    await edit('criteria.0.center_types.0', 'ASP-P'); await edit('criteria.0.center_types.1', 'ASP-P');
    await edit('criteria.0.dep_map.HIS-P.0', 'HIS-S');
    await act(async () => button('Add explicit center').props.onClick()); await edit('criteria.0.explicit_centers.0.res_id', '17'); await edit('criteria.0.explicit_centers.0.protonation_type', 'HIS-P');
    await check('engine_options.etab_hidden.enabled', true); await act(async () => button('Add etab_hidden width').props.onClick()); await edit('engine_options.etab_hidden.0', '64');
    await check('initial_sequences.enabled', true); await act(async () => button('Add initial_sequences').props.onClick()); await edit('initial_sequences.0', 'HD');
    expect(saved).toMatchObject({ criteria: [{ temperature: [0, 0.125], center_types: ['ASP-P', 'ASP-P', 'GLU-P'], dep_map: { 'HIS-P': ['HIS-S'] }, explicit_centers: [{ res_id: 17, protonation_type: 'HIS-P' }] }], engine_options: { etab_hidden: [64], field_hidden: null }, initial_sequences: ['HD'] });
    expect(tree.root.findByProps({ 'aria-label': 'engine_options.extended_vocab' }).props.readOnly).toBe(true);
    expect(tree.root.findAllByType('textarea')).toHaveLength(0);
});
it('launches pH redesign from agnostic selections with exact documents, global settings, Project and explicit placement', async () => {
    await mount(<BinderSelectedControls sourceJobId="source" selectedDesignIds={['bc2', 'other-source']} selectedNativeSources={[{ job_id: 'ppiflow', artifact_id: 'complex' }]} candidateDocuments={{ bc2: { artifact_id: 'state-B' } }} launchContextId="project-destination" onOpenJob={() => {}} onStartMD={() => {}} />);
    await edit('Binder continuation operation', 'protonpottsmpnn'); await flush(); await edit('binder_chain', 'binder-1'); await edit('criteria.0.temperature', '0');
    await act(async () => button('Vast · Fixture worker').props.onClick()); await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].body).toMatchObject({ operation: 'protonpottsmpnn', design_ids: ['bc2', 'other-source'], native_sources: [{ job_id: 'ppiflow', artifact_id: 'complex' }], candidate_documents: { bc2: { artifact_id: 'state-B' } }, execution_target_id: 'worker', launch_context_id: 'project-destination', params: { ...nativeRequest.options, binder_chain: 'binder-1', criteria: [{ ...nativeRequest.options.criteria[0], temperature: 0 }] } });
    expect(posted[0].body.params).not.toHaveProperty('target_pdb'); expect(posted[0].body.params).not.toHaveProperty('n_jobs');
    await edit('Binder continuation operation', 'caliby'); await flush(); await edit('Binder continuation operation', 'protonpottsmpnn'); await flush(); expect(tree.root.findByProps({ 'aria-label': 'criteria.0.temperature' }).props.value).toBe('0');
    await act(async () => button('Local').props.onClick()); await act(async () => button('Run selected operation').props.onClick()); expect(posted[1].body.execution_target_id).toBeNull();
    if (process.env.BMS_PROTON_UI_EVIDENCE) { mkdirSync(process.env.BMS_PROTON_UI_EVIDENCE, { recursive: true }); writeFileSync(resolve(process.env.BMS_PROTON_UI_EVIDENCE, 'selected-payloads.json'), JSON.stringify(posted, null, 2)); }
});
it('preserves native results, trajectories and structureless IDs through global workbench to optional prediction', async () => {
    await mount(<ProtonPottsResults job={{ id: 'source', status: 'completed', params: nativeRequest.options }} launchContextId="project-destination" />);
    const adapter = tree.root.findByType(NativeBinderGenerationResults).props.adapter;
    const page = await adapter.fetchPage(0); expect(page.records[0].native_record).toEqual(result.designs[0]); expect(page.records[0]).not.toHaveProperty('design_id'); expect(page.records[0].structures).toEqual([]);
    await act(async () => button('Data table').props.onClick()); await check('Select criteria0:native0', true); await act(async () => button('criteria0:native0').props.onClick()); await flush();
    expect(text(tree.root)).toContain('HIS-P ASP-D'); expect(text(tree.root)).toContain('17');
    expect(tree.root.findAllByType('a').some(node => String(node.props.href).startsWith('/designs/source?design_id='))).toBe(false);
    const trajectory = tree.root.findAllByType(CohortPlot).find(node => node.props.label === 'Native optimization energies')!; expect(trajectory.props.data[0]).toMatchObject({ x: [0], y: [0] });
    expect(posted).toHaveLength(0);
    await act(async () => button('Vast · Fixture worker').props.onClick()); await act(async () => button('Predict selected redesigned sequences').props.onClick());
    expect(posted[0]).toMatchObject({ url: '/api/jobs/source/protonpottsmpnn/predict', body: { design_ids: ['criteria0:native0'], model_id: 'protenix', params: { cycles: 3 }, execution_target_id: 'worker', launch_context_id: 'project-destination' } });
    await edit('Redesign prediction model', 'boltz2'); await flush(); await act(async () => button('Local').props.onClick()); await act(async () => button('Predict selected redesigned sequences').props.onClick()); expect(posted[1].body).toMatchObject({ model_id: 'boltz2', execution_target_id: null });
    if (process.env.BMS_PROTON_UI_EVIDENCE) writeFileSync(resolve(process.env.BMS_PROTON_UI_EVIDENCE, 'prediction-payloads.json'), JSON.stringify(posted, null, 2));
});
it('reviews retained remote prediction at the shared approval owner without reposting the native action', async () => {
    await mount(<div />);
    const prepared = { name: 'Prepared fixture', model_id: 'protenix', mode: 'complex', execution_target_id: 'worker', execution_policy: { remote_result_policy: 'manual' }, launch_context_id: 'destination', params: { sequence: 'HD:GG', cycles: 3 } };
    const preview = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: prepared, plan: { requested_json: prepared.params, effective_json: prepared.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
    const post = vi.spyOn(api, 'post').mockImplementation(async url => {
        if (url === '/api/jobs/execution-plan/preview') return { data: preview };
        if (url === '/api/jobs') return { data: { id: 'prepared-child', name: 'Prepared fixture' } };
        throw { isAxiosError: true, response: { status: 409, data: { detail: { code: 'remote_prepared_job_review_required', job_requests: [prepared], response_context: { source_job_id: 'source', root_job_id: 'source', operation: 'predict_protenix', design_ids: ['criteria0:native0'], selected_design_count: 1 } } } } };
    });
    const pending = predictProtonPottsSelected('source', { design_ids: ['criteria0:native0'], model_id: 'protenix', params: { cycles: 3 }, execution_target_id: 'worker', launch_context_id: 'destination' });
    const approve = () => [...document.querySelectorAll('button')].find(row => row.textContent === 'Approve and submit');
    await vi.waitFor(async () => { await flush(); expect(approve()).toBeTruthy(); });
    expect(post).toHaveBeenCalledTimes(2);
    await act(async () => { approve()!.click(); await pending; });
    expect(post).toHaveBeenCalledTimes(3); expect(post.mock.calls[2][1]).toEqual({ ...prepared, execution_plan_approval: preview.approval_digest });
    expect((await pending).launched_jobs[0].id).toBe('prepared-child'); post.mockRestore();
});
it('renders saved source science before results exist and rejects malformed native identity rather than fabricating rows', async () => {
    api.defaults.adapter = async config => { if (config.url?.endsWith('/results')) throw new Error('Not published yet'); return { config, data: config.url === '/api/execution-targets' ? [] : model, status: 200, statusText: 'OK', headers: {} }; };
    await mount(<ProtonPottsResults job={{ id: 'new', status: 'queued', params: { binder_chain: 'saved-chain', seed: 0, criteria: nativeRequest.options.criteria } }} />);
    expect(text(tree.root)).toContain('saved-chain'); expect(text(tree.root)).toContain('Not published yet');
    expect(parseProtonPottsResults(result)).toEqual(result);
    expect(() => parseProtonPottsResults({ ...result, designs: [...result.designs, ...result.designs] })).toThrow('identity');
    expect(() => parseProtonPottsResults({ ...result, designs: [{ ...result.designs[0], native: { ...native, extended_tokens: 'wrong' } }] })).toThrow('PHDesignOutput');
});
