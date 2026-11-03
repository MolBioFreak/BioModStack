import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ExecutionPolicyControl } from '../../src/components/ExecutionPolicyControl';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { deNovoSavedValues } from '../../src/components/deNovoWorkflowRoute';
import { deNovoAuthoringEnvelope } from '../../src/lib/deNovoAuthoring';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import { DeNovoDraftPreparationContext } from '../../src/lib/deNovoDocumentCapture';
import models from './fixtures/resequenceDesignerModels.json';
import { execFileSync } from 'node:child_process';
const inventory = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python', ['tests/test_de_novo_gap_rfd3.py', 'inventory'], { cwd: '../..', encoding: 'utf8' }));

vi.mock('../../src/components/DeNovoNativeSources', () => ({ NativeSourceInput: ({ onAcquire }: any) => <button onClick={() => onAcquire({ job_id: 'source-job', design_id: 'source-design', model_number: 7, output_format: 'native' })}>Pick exact source</button> }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ onChange }: any) => <><button onClick={() => onChange(null)}>Place Local</button><button onClick={() => onChange('worker')}>Place worker</button></> }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/TargetAntigenSelector', () => ({ TargetAntigenSelector: () => null }));
vi.mock('../../src/components/ExecutionPlanApproval', () => ({ reviewExecutionPlan: vi.fn(async () => false) }));
let root: Root | undefined;
let query: QueryClient;
let latest: any;
let prep: any;
const calls: Array<{ url: string; body: any }> = [];
const unexpected: string[] = [];
const previous = api.defaults.adapter;
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
const q = (label: string) => document.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!;
async function edit(label: string, value: string) { const node = q(label); expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function click(text: string) { const node = [...document.querySelectorAll('button')].find(node => node.textContent === text); expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle(); }
async function toggle(label: string) { await act(async () => q(label).click()); await settle(); }
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = previous; sessionStorage.clear(); calls.length = 0; expect(unexpected.splice(0)).toEqual([]); });
async function mount(values: Record<string, unknown>) {
    api.defaults.adapter = async config => {
        const url = String(config.url), body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body }); let data: any;
        const model = /^\/api\/models\/([^/]+)$/.exec(url);
        if (model) data = (models as any)[model[1]] ?? { engine_references: {}, params: [] };
        else if (url.endsWith('/generation-settings')) data = inventory;
        else if (url === '/api/jobs/resequence/from-structures/prepare') data = { name: body.name, model_id: 'protein_modification_experimental', mode: 'region_redesign', params: { backbone_input_pdbs: 'owned/staging', seq_method: body.seq_method, structure_validators: body.structure_validators } };
        else if (url === '/api/jobs/execution-plan/preview') data = { approval_digest: 'a'.repeat(64), admissible: true, request: body };
        else if (url === '/api/jobs/resequence/from-structures' || url === '/api/jobs') data = { id: 'new-job' };
        else if (url === '/api/files/materialize-structure') data = { path: 'native.cif', sha256: 'a'.repeat(64), format: 'cif', model_number: 7, model_numbers: [7], source_structure: body };
        else { unexpected.push(url); throw Error(`Unexpected ${url}`); }
        return { data, config, status: 200, statusText: 'OK', headers: {} };
    };
    latest = undefined; prep = { current: null };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter><DeNovoDraftPreparationContext.Provider value={prep}><ProteinModificationTemplate initialValues={values} runDetails={<ExecutionPolicyControl initialPolicy={values.execution_policy as any} />} onBack={() => {}} onDraftChange={draft => { latest = draft; }} /></DeNovoDraftPreparationContext.Provider></MemoryRouter></QueryClientProvider>)); await settle();
}
const loop = { modification_mode: 'resequence', suite_last_generation: { generator: 'disco', design_task: 'unconditional' } };
it('retains the real suite loop draft, sparse per-designer and validator settings, and cold authoring envelope', async () => {
    await mount(loop); await click('Pick exact source');
    await edit('Resequence job name', 'durable-loop'); await edit('Resequence design chains', 'A,B');
    await edit('Designer setting mpnn_backbone_noise', '0'); await edit('Designer setting mpnn_relax_output', 'false');
    await edit('Resequence engine', 'fampnn'); await edit('Designer setting fampnn_seed', '0');
    await edit('Resequence engine', 'mpnn');
    expect(q('Designer setting mpnn_backbone_noise').value).toBe('0'); expect(q('Designer setting mpnn_relax_output').value).toBe('false');
    await edit('Validator setting protenix_v2 protenix_use_msa', 'false');
    await toggle('Validate with Protenix v2'); await toggle('Validate with Protenix v2');
    expect(q('Validator setting protenix_v2 protenix_use_msa').value).toBe('false');
    await edit('Resequence MSA provider', 'neurosnap_api');
    await click('Generate'); await click('Resequence');
    expect(q('Resequence job name').value).toBe('durable-loop'); expect(q('Resequence design chains').value).toBe('A,B');
    expect(latest.native_job_request).toBeNull();
    await act(async () => { await prep.current(); });
    const envelope: any = deNovoAuthoringEnvelope(latest);
    expect(envelope.native_job_request).toMatchObject({ mode: 'region_redesign', execution_target_id: null });
    expect(envelope.editor_state).toMatchObject({ modification_mode: 'resequence', msa_provider: 'neurosnap_api', native_sources: { input: { model_number: 7 } } });
    await close(); await mount(envelope.editor_state);
    expect(q('Resequence job name').value).toBe('durable-loop');
    await click('Run resequence and validate');
    const staged = calls.find(call => call.url.endsWith('/prepare'))!.body;
    expect(staged).toMatchObject({ msa_provider: 'neurosnap_api', designer_settings: { mpnn_backbone_noise: 0, mpnn_relax_output: false }, validator_settings: { protenix_v2: { protenix_use_msa: false } } });
    expect(calls.filter(call => call.url.endsWith('/prepare'))).toHaveLength(1);
});
it('retains suite loop inputs across Generate and Resequence independently of typed settings', async () => {
    await mount(loop); await edit('Resequence job name', 'saved-name'); await edit('Resequence fixed positions', 'A1');
    await click('Generate'); await click('Resequence');
    expect(q('Resequence job name').value).toBe('saved-name'); expect(q('Resequence fixed positions').value).toBe('A1');
    expect(latest.modification_mode).toBe('resequence'); expect(latest.native_job_request).toBeNull();
});
it('receives the existing parent transfer-policy control and serializes its explicit edit', async () => {
    await mount({ ...loop, execution_policy: { remote_result_policy: 'manual' } }); await click('Pick exact source');
    await edit('Successful remote results', 'automatic'); await click('Run resequence and validate');
    expect(calls.find(call => call.url === '/api/jobs/resequence/from-structures')!.body.execution_policy).toEqual({ remote_result_policy: 'automatic' });
});
it('authors the already-supported MSA provider on the real loop request', async () => {
    await mount(loop); await click('Pick exact source'); await edit('Resequence MSA provider', 'neurosnap_api');
    await click('Run resequence and validate');
    expect(calls.find(call => call.url === '/api/jobs/resequence/from-structures')!.body.msa_provider).toBe('neurosnap_api');
});
it('retains invalid structured text independently across engine and suite switches', async () => {
    await mount(loop); await click('Pick exact source'); await edit('Resequence engine', 'ligandmpnn');
    await edit('Structured designer settings', '{broken');
    await edit('Resequence engine', 'mpnn'); await edit('Resequence engine', 'ligandmpnn');
    expect(q('Structured designer settings').value).toBe('{broken');
    await click('Generate'); await click('Resequence');
    expect(q('Structured designer settings').value).toBe('{broken');
    expect([...document.querySelectorAll('button')].find(button => button.textContent === 'Run resequence and validate')!.disabled).toBe(true);
});
it('reopens current loop clones in the loop owner, retaining source basket and settings', async () => {
    const values = deNovoSavedValues('protein_modification_experimental', 'region_redesign', { backbone_input_pdbs: 'retained/basket', seq_method: 'fampnn', structure_validators: ['esmfold2'], fampnn_seed: 0, fampnn_seq_only: false, protenix_msa_backend: 'neurosnap_api' });
    await mount(values);
    expect(q('Resequence engine')?.value).toBe('fampnn');
    expect(q('Designer setting fampnn_seed').value).toBe('0');
    expect(latest.backbone_input_pdbs).toBe('retained/basket');
    await act(async () => { await prep.current(); });
    expect(calls.find(call => call.url.endsWith('/prepare'))!.body).toMatchObject({ backbone_input_pdbs: 'retained/basket', designer_settings: { fampnn_seed: 0, fampnn_seq_only: false } });
});
it('keeps a retained LigandMPNN native template authoritative instead of inventing a clone mode', async () => {
    await mount(deNovoSavedValues('protein_modification_experimental', 'region_redesign', { backbone_input_pdbs: 'retained/basket', seq_method: 'ligandmpnn', designer_template: 'retained/metal-template.json', structure_validators: ['protenix_v2'] }));
    expect(q('LigandMPNN mode').value).toBe('');
    await act(async () => { await prep.current(); });
    const body = calls.find(call => call.url.endsWith('/prepare'))!.body;
    expect(body).not.toHaveProperty('designer_mode'); expect(body).not.toHaveProperty('designer_settings');
    expect(body.retained_params.designer_template).toBe('retained/metal-template.json');
});
it('reuses its exact staged source on cancelled remote review and retains explicit Local/policy in envelopes', async () => {
    await mount({ ...loop, execution_policy: { remote_result_policy: 'manual' } }); await click('Pick exact source'); await click('Place worker');
    await click('Run resequence and validate'); await click('Run resequence and validate');
    expect(calls.filter(call => call.url.endsWith('/prepare'))).toHaveLength(1);
    expect(calls.filter(call => call.url === '/api/jobs')).toHaveLength(0);
    await click('Place Local');
    sessionStorage.setItem('bms.jobLauncher.executionTargetId', 'unrelated-session-worker');
    const envelope: any = deNovoAuthoringEnvelope(latest);
    expect(envelope.editor_state.execution_target_id).toBeNull();
    expect(envelope.editor_state.execution_policy).toEqual({ remote_result_policy: 'manual' });
});
it.each([false, true])('cleans only direct-Run owned preparation, never a draft-owned receipt (draft=%s)', async draftOwned => {
    await mount(loop); await click('Pick exact source'); await click('Place worker');
    const adapter = api.defaults.adapter as any;
    const receipt = { path: 'owned/staging', token: 'owned-token' };
    api.defaults.adapter = async config => {
        if (config.url === '/api/jobs/resequence/from-structures/cleanup') {
            calls.push({ url: config.url, body: JSON.parse(config.data) });
            return { data: { cleaned: true }, config, status: 200, statusText: 'OK', headers: {} };
        }
        const response = await adapter(config);
        if (config.url === '/api/jobs/resequence/from-structures/prepare') response.data.params.resequence_preparation = receipt;
        return response;
    };
    if (draftOwned) await act(async () => { await prep.current(); });
    await click('Run resequence and validate'); await click('Run resequence and validate');
    const cleanup = calls.filter(call => call.url.endsWith('/cleanup'));
    expect(cleanup).toHaveLength(draftOwned ? 0 : 2);
    if (!draftOwned) expect(cleanup.map(call => call.body)).toEqual([{ preparation: receipt }, { preparation: receipt }]);
    expect(calls.filter(call => call.url.endsWith('/prepare'))).toHaveLength(draftOwned ? 1 : 2);
    expect(calls.filter(call => call.url === '/api/jobs')).toHaveLength(0);
});
it.each(['ligand_conditioned', 'dna_conditioned', 'rna_conditioned'])('uses the DISCO native file authority instead of inactive %s fields and lengths', async task => {
    await mount({ generator: 'disco', modification_mode: 'de_novo_design', design_task: task, disco_input_json_path: 'native/jobs.json', target_lengths: '', sequence_design: null });
    expect(latest.native_job_request).toMatchObject({ params: { disco_input_json_path: 'native/jobs.json', target_lengths: '' } });
});
it('allows explicit empty DISCO jobs without inactive lengths', async () => {
    await mount({ generator: 'disco', modification_mode: 'de_novo_design', design_task: 'custom_json', pcad_disco_jobs_json: [], target_lengths: '', sequence_design: null });
    expect(latest.native_job_request).toMatchObject({ params: { pcad_disco_jobs_json: [] } });
});
it('allows La atom-spec-only motif without inactive contig', async () => {
    await mount({ generator: 'laproteina', modification_mode: 'de_novo_design', design_task: 'motif_scaffolding', target_lengths: '100', laproteina_motif_pdb: 'motif.pdb', laproteina_motif_atom_spec: { A1: ['CA'] }, laproteina_contig_string: '', sequence_design: null });
    expect(latest.native_job_request).toMatchObject({ params: { laproteina_motif_atom_spec: { A1: ['CA'] } } });
});
it.each(['partial_diffusion', 'minimal_insertion'])('migrates flat historical %s into the unified generation request without dropping sparse values', async task => {
    await mount({ modification_mode: 'rfd3_local_redesign', generator: 'rfd3', input_structure: 'native.cif', redesign_mode: task, model_number: 7, design_chains: 'AB', context_chains: ['CD'], redesign_ranges: 'AB10-12', insertion_anchor: 'AB12', insertion_min_length: 1, insertion_max_length: 3, ...(task === 'partial_diffusion' ? { partial_t: 0 } : {}), plddt_enhanced: false, sequence_policy: 'preserve' });
    expect(latest.native_job_request).toMatchObject({ mode: 'de_novo_design', params: { generation_task: task, source_model: 7, redesign: { design_chains: ['AB'], context_chains: ['CD'], redesign_ranges: 'AB10-12', insertion_anchor: 'AB12', insertion_min_length: 1, insertion_max_length: 3 }, conditioning: { plddt_enhanced: false, ...(task === 'partial_diffusion' ? { partial_t: 0 } : {}) } } });
    expect(latest.native_job_request.params).not.toHaveProperty('redesign_mode');
});
it('continuation retains native CIF and places a zero continuation seed only under search.resume_seed', async () => {
    await mount(loop);
    const source = { job_id: 'source', document: { artifact_id: 'native', model: 7 }, model_number: 7 };
    const result: any = await prepareDeNovoContinuation(source, 'redesign');
    expect(result.input_structure).toBe('native.cif');
    expect(calls.filter(call => call.url.endsWith('materialize-structure'))).toHaveLength(1);
});
it('writes zero to resume_seed without replacing the historical global seed owner', async () => {
    await mount(loop);
    const source = { job_id: 'source', document: { artifact_id: 'native', model: 7 }, model_number: 7 };
    const state: any = await prepareDeNovoContinuation(source, 'rfd4_state', { path: 'state.pt', seed: 0 });
    expect(state.rfd4_request.settings).toEqual({ search: { resume_seed: 0 } });
});
