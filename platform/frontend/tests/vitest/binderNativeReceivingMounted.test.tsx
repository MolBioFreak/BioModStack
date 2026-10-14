import React from 'react';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { nativeParameterDisplay } from '../../src/lib/nativeBinderAuthoring';
import { roundParameterIsBound } from '../../src/lib/binderRound';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { writeBinderSelection } from '../../src/lib/binderContinuation';

// TRANSPORT FIXTURE ONLY: actual Results parent, readers, composer, native editors,
// serializer and remote mutation owner. No inference, publication or yield claim.
vi.mock('react-plotly.js', () => ({ default: () => <div data-plot /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-molecule={props.structureUrl} /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
let tree: ReactTestRenderer | undefined, client: QueryClient;
const original = api.defaults.adapter;
const calls: Array<{ url: string; body: any }> = [];
let rounds: any[] = [], sets: any[] = [];
let job = { id: 'parent', name: 'TRANSPORT FIXTURE binder', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed', params: {}, design_count: 1, created_at: '2026-09-01T00:00:00Z' };
const doc = { binder_chains: 'B', target_chains: 'A', artifact_id: 'complex', primary: true, target_state: 'complex', logical_path: 'exact/complex.pdb', download_url: '/exact/complex.pdb' };
const monomer = { binder_chains: 'A', target_chains: '', artifact_id: 'monomer', target_state: 'binder', logical_path: 'exact/binder.cif', download_url: '/exact/binder.cif' };
const records = Array.from({ length: 32 }, (_, i) => ({ candidate_key: `producer:${i}`, ...(i === 0 ? { design_id: 'design' } : {}), metrics: { score: i, outcome: i % 2 ? 'rejected' : 'passed', rank: i === 0 ? 1 : null }, structures: i === 0 ? [doc, monomer] : [{ ...doc, artifact_id: `native-${i}`, download_url: `/exact/${i}.pdb` }] }));
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree!.root.findAllByType('button').find(node => text(node) === label)!;
const flush = async () => { for (let i = 0; i < 15; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); }); };
const click = async (label: string) => { expect(button(label), `${label}: ${text(tree!.root)}; calls=${calls.map(call => call.url).join(',')}`).toBeTruthy(); await act(async () => button(label).props.onClick()); await flush(); };
const change = async (label: string, value: string | boolean) => { await act(async () => tree!.root.findByProps({ 'aria-label': label }).props.onChange({ target: typeof value === 'boolean' ? { checked: value } : { value } })); await flush(); };
async function mount() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<MemoryRouter initialEntries={['/designs/parent']}><QueryClientProvider client={client}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
}
async function close() { await act(async () => tree?.unmount()); tree = undefined; client?.clear(); }
beforeEach(() => {
    job = { ...job, model_id: 'ppiflow', mode: 'protein_binder' };
    rounds = []; sets = []; calls.length = 0; localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })));
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    api.defaults.adapter = async config => {
        const url = String(config.url), params = config.params ?? {}, body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url, body });
        let data: any = [];
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/parent') data = { ...job, saved_selection_sets: sets };
        else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.endsWith('/generation-results')) data = { records: records.slice(params.offset, params.offset + params.limit), total: records.length, offset: params.offset, limit: params.limit, receipt: {}, publication: {}, artifacts: [] };
        else if (url.endsWith('/binder-evidence')) data = { records: [], total: 0, offset: 0, limit: 100 };
        else if (url.endsWith('/round')) data = { job_id: 'parent', state: 'not_requested', steps: {}, errors: {}, selected_rounds: rounds };
        else if (url.endsWith('/selection-context')) data = { candidate_documents: { design: [doc, monomer] }, targets: [] };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.startsWith('/api/models/')) data = { id: url.split('/').at(-1), params: [
            { name: 'ppiflow_task', label: 'Task', type: 'string', enum: ['protein_binder_partial', 'antibody_partial', 'nanobody_partial'], default: 'antibody_partial' },
            { name: 'ppiflow_start_t', label: 'Start time', type: 'number', default: null, defaults_by_task: { protein_binder_partial: 0.15, antibody_partial: 0.8, nanobody_partial: 0.8 } },
            { name: 'design_chain', label: 'Design chain', type: 'string', default: 'A' },
            { name: 'target_chain', label: 'Target chain', type: 'string', default: 'B' },
            { name: 'enabled', label: 'Enabled', type: 'boolean', default: true },
            { name: 'samples', label: 'Samples', type: 'integer', default: 5, nullable: true, minimum: 0 },
            { name: 'fixed_positions', label: 'Fixed positions', type: 'string', default: 'A:1' },
        ] };
        else if (url === '/api/designs/design') data = { id: 'design', job_id: 'parent', name: 'design', pdb_path: 'primary.pdb', provenance: { model_id: 'ppiflow' }, viewer_capabilities: [], supported_analyzers: [] };
        else if (url === '/api/binder-continuation/selected') {
            const existing = rounds.find(round => round.request.idempotency_key === body.idempotency_key);
            const round = existing ?? { round_id: `fixture-round-${rounds.length + 1}`, source_job_id: 'parent', root_job_id: 'parent', state: 'completed', request: body, selection: body.selection, steps: [{ step_id: 'fixture-step', stage_id: body.round_plan.stages.at(-1).id, model_id: 'protenix', state: 'completed', job_id: 'fixture-child', parent_step_ids: [], outputs: Array.from({ length: 33 }, (_, i) => ({ kind: 'native_sequence', job_id: 'fixture-child', publication_ref: 'fixture-publication', native_output_id: `sample-${i}`, label: `Fixture sample ${i + 1}` })) }] };
            if (!existing) rounds = [...rounds, round];
            data = { operation: 'round', source_job_id: 'parent', root_job_id: 'parent', round_id: round.round_id, state: round.state, launched_jobs: [], round };
        } else if (url.endsWith('/review-filter-sets')) { const saved = { ...body, id: `set-${sets.length}`, revision: 1 }; sets = [...sets, saved]; data = { filter_set: saved, filter_sets: sets }; }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { await close(); api.defaults.adapter = original; vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

// Initial-publication fixtures deliberately include display-only metadata and native IDs
// which are NOT Design IDs. Strict transport must retain only the bound backend ref.
const initialRef = (i: number) => ({ kind: 'native_sequence', job_id: 'parent', publication_ref: 'published/exact/receipt.json', native_output_id: `producer-output:${i}` });
function interceptResult(endpoint: string, data: any) {
    const prior = api.defaults.adapter as Function;
    api.defaults.adapter = async config => config.url === endpoint
        ? { config, data, status: 200, statusText: 'OK', headers: {} }
        : prior(config);
}
it.each(['ligandmpnn', 'caliby_experimental', 'protonpottsmpnn'])('actual Results parent selects %s initial native publications across pages, saves final/ranked sets and repeats a round without synthetic Designs', async model_id => {
    job = { ...job, model_id, mode: model_id === 'ligandmpnn' ? 'ligand_aware' : model_id === 'protonpottsmpnn' ? 'redesign' : 'ensemble_design' };
    const refs = Array.from({ length: 32 }, (_, i) => ({ ...initialRef(i), label: 'display only' }));
    if (model_id === 'ligandmpnn') interceptResult('/api/jobs/parent/ligandmpnn-design-results', {
        contract: 'ligandmpnn_design.v1', request: { write_structures: false, options: {} }, artifacts: [],
        records: refs.map((candidate_ref, i) => ({ candidate_ref, producer: { name: 'native', batch_idx: 0, design_idx: i }, native_input: {}, native_output: { designed_sequence: 'HD', sequence_recovery: i }, artifacts: [] })),
    });
    else if (model_id === 'caliby_experimental') interceptResult('/api/jobs/parent/caliby-native-results', {
        schema: 'bms.caliby-native-results.v1', request: { requested: {}, effective: {}, sources: [] }, runtime: {}, effective_sampling: {}, artifacts: [],
        records: refs.map((candidate_ref, i) => ({ candidate_ref, record_id: `native-${i}`, native: { example_id: `example-${i}`, seq: 'HD', U: i }, source: null, structure_path: '' })),
    });
    else {
        const native = { binder_chain: 'B', canonical_sequence: 'HD', extended_tokens: ['HIS-P', 'ASP-D'], extended_vocab: true, scheme: 'native', sample: 0, final_potts_energy: 0,
            ...Object.fromEntries(['seed_idx','site_rank','center_res_id','n_neighbours','selective_energy','n_centers','global_protonation_dH','placement_prob','placement_entropy','sequence_decoded_prob_score','sequence_entropy','combined_lambda','repetitive_window_weight','block_size','neighbour_k','max_mutations','protonation_type','method','backend','selective_source','placement_label','placement_region','placement_by','sweep_order','center_res_ids','center_protonation_types','selective_energies','energy_trajectory'].map(k => [k, null])) };
        interceptResult('/api/jobs/parent/protonpottsmpnn/results', { contract: 'protonpottsmpnn_design.v1', source: {}, request: { contract: 'protonpottsmpnn_design.v1', options: { criteria: [], binder_chain: 'B', seed: 0, engine_options: {} }, source: { requested_path: 'source.pdb', sha256: 'fixture' } }, runtime: {}, artifacts: [],
            designs: refs.map((candidate_ref, i) => ({ candidate_ref, design_id: `criteria0:native${i}`, native_design_id: `native${i}`, criteria_index: 0, native: { ...native, final_potts_energy: i } })) });
    }
    await mount();
    await change('Select Candidate 1', true); await click('Next native records'); await change('Select Candidate 32', true);
    expect(button('Next round (2)')).toBeTruthy();
    const selectedSets = tree!.root.findAll(node => typeof node.type === 'function' && node.props.candidates?.length === 2 && node.props.onLoad)[0];
    await act(async () => selectedSets.findAllByType('button').find(node => text(node) === 'Mark final')!.props.onClick()); await flush();
    expect(sets.at(-1).candidate_refs).toEqual([initialRef(0), initialRef(31)]);
    const metric = model_id === 'ligandmpnn' ? 'sequence_recovery' : model_id === 'caliby_experimental' ? 'U' : 'final_potts_energy';
    await act(async () => tree!.root.findAll(node => typeof node.type === 'function' && node.props.label === 'Rank by')[0].props.onChange(metric));
    await change('Ranking direction', 'descending');
    const rankOwner = tree!.root.findAll(node => typeof node.type === 'function' && node.props.ranking?.metric === metric && node.props.onLoad)[0];
    await act(async () => rankOwner.findAllByType('button').find(node => text(node) === 'Save set (32)')!.props.onClick()); await flush();
    expect(sets.at(-1).candidate_refs[0]).toEqual(initialRef(31));
    await click('Next round (2)'); await click('Add Blind prediction'); await change('Stage 1 Protenix', true); await click('Protenix settings');
    const field = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!.findByType('input');
    await act(async () => field('fixed_positions').props.onChange({ target: { value: '' } }));
    await click('Clear Samples (null)'); await click('Back to round'); await click('Local'); await click('Queue round');
    let posts = calls.filter(c => c.url === '/api/binder-continuation/selected');
    expect(posts[0].body).toMatchObject({ execution_target_id: null, selection: { candidate_refs: [initialRef(0), initialRef(31)] } });
    expect(posts[0].body.round_plan.stages[0].models[0].params).toMatchObject({ samples: null, fixed_positions: '' });
    expect(tree!.root.findAll(node => node.type === 'input' && /^Select Protenix output /.test(node.props['aria-label'] ?? ''))).toHaveLength(33);
    await change('Select Protenix output 33', true); await click('Next round (3)'); await click('Queue round');
    posts = calls.filter(c => c.url === '/api/binder-continuation/selected');
    expect(posts[1].body.selection.candidate_refs).toEqual([initialRef(0), initialRef(31), { kind: 'native_sequence', job_id: 'fixture-child', publication_ref: 'fixture-publication', native_output_id: 'sample-32' }]);
    expect(posts[1].body.idempotency_key).not.toBe(posts[0].body.idempotency_key);
    await close(); await mount();
    expect(button('Next round (3)')).toBeTruthy();
    expect(tree!.root.findByProps({ 'aria-label': 'Select Candidate 1' }).props.checked).toBe(true);
    expect(calls.some(c => /\/api\/designs\/(criteria|native|producer)/.test(c.url))).toBe(false);
    if (model_id === 'protonpottsmpnn') {
        const extra = tree!.root.findAllByType('details').find(node => node.findAllByType('summary').some(summary => text(summary).startsWith('Additional selected outputs')))!;
        await act(async () => extra.findAllByType('button').find(node => text(node) === 'Remove')!.props.onClick()); await flush();
        const readback = tree!.root.findAll(node => typeof node.type === 'function' && node.props.value?.design_ids && node.props.value?.model_id === 'protenix')[0];
        expect(readback.props.value.design_ids).toEqual(['criteria0:native31']);
        expect(tree!.root.findByProps({ 'aria-label': 'Select Candidate 1' }).props.checked).toBe(false);
    }
});
it('actual RFantibody Results uses compact all-output selection and exact backend document refs, retaining the real model workbench', async () => {
    job = { ...job, model_id: 'rfantibody', mode: 'antibody' };
    const rfRefs = Array.from({ length: 32 }, (_, i) => ({ kind: 'design', job_id: 'parent', design_id: `rf-${i}`, document: { artifact_id: `rf-complex-${i}`, target_state: 'complex', model: 2 } }));
    const designs = rfRefs.map((candidate_ref, i) => ({ id: `rf-${i}`, job_id: 'parent', name: `RF ${i}`, candidate_ref, pdb_path: null, rfd_rog: i, binder_sequence: 'HD', viewer_capabilities: [], supported_analyzers: [], provenance: { model_id: 'rfantibody' } }));
    interceptResult('/api/designs', { designs, total: designs.length, model_counts: { rfantibody: 32 } });
    interceptResult('/api/designs/rf-0', designs[0]);
    interceptResult('/api/designs/native/parent/rf-complex-0/document', { status: 'ok', document: { documentId: 'rf-exact-document', sourceKind: 'mmcif', sourceUrl: '/exact/rf-complex.cif', contentSha256: 'fixture' } });
    await mount();
    expect(text(tree!.root)).toContain('RFantibody candidates');
    expect(tree!.root.findByProps({ 'data-molecule': '/exact/rf-complex.cif' })).toBeTruthy();
    await change('Select Candidate 1', true); await click('Next native records'); await change('Select Candidate 32', true);
    await click('Next round (2)'); await click('Add Sequence design'); await change('Stage 1 ProteinMPNN', true); await click('Queue round');
    expect(calls.find(c => c.url === '/api/binder-continuation/selected')!.body.selection.candidate_refs).toEqual([rfRefs[0], rfRefs[31]]);
    await click('Open RFantibody model workbench');
    expect(button('Back to RFantibody candidates')).toBeTruthy();
    await click('Back to RFantibody candidates');
    expect(button('Next round (2)')).toBeTruthy();
    expect(tree!.root.findByProps({ 'aria-label': 'Select Candidate 1' }).props.checked).toBe(true);
});

it('actual parent renders every applicable installed protein registry field, including advanced controls and checkpoint-fixed metadata', async () => {
    // Read the actual installed-source registry; PyYAML is an API dependency. No server/science/network.
    const registry = JSON.parse(execFileSync(process.env.BMS_REGISTRY_PYTHON || 'python3', ['-c', 'import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))', resolve('../api/config/models/binder_refinement.yaml')], { encoding: 'utf8' }));
    interceptResult('/api/models/binder_refinement', registry);
    writeBinderSelection('parent', ['design']);
    await mount(); await click('Next round (1)'); await click('Add protein flow → design → predict'); await click('PPIFlow settings');
    const fields = registry.params.filter((p: any) => !p.hidden && !roundParameterIsBound(p.name) && !['pdb_paths','source_identity_json'].includes(p.name) && nativeParameterDisplay(p, { ppiflow_task: 'protein_binder_partial' }).applicable);
    for (const param of fields) {
        const field = tree!.root.findAll(node => node.props['data-sequence-designer-field'] === param.name)[0];
        expect(field, param.name).toBeTruthy();
        if (param.read_only) {
            expect(field.findAllByType('input'), param.name).toHaveLength(0);
            expect(text(field)).toContain('profile fixed');
            expect(text(field)).toContain(param.fixed_reason);
            expect(text(field)).toContain(param.authority);
        } else expect(field.findAll(node => ['input','select','textarea','button'].includes(String(node.type))).length, param.name).toBeGreaterThan(0);
    }
    for (const name of ['ppiflow_protein_num_timesteps','ppiflow_protein_scaffold_contig','ppiflow_protein_twisting_use','ppiflow_protein_twisting_scale_w_t']) expect(fields.some((p: any) => p.name === name)).toBe(true);
    expect(tree!.root.findAll(node => node.props['data-sequence-designer-field'] === 'ppiflow_retry_limit')).toHaveLength(0);
    const editable = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!;
    await act(async () => editable('ppiflow_protein_num_timesteps').findByType('input').props.onChange({ target: { value: '42' } }));
    await act(async () => tree!.root.findByProps({ 'aria-label': 'ppiflow_protein_scaffold_contig' }).props.onChange({ target: { value: '' } }));
    await click('Back to round'); await change('Stage 2 ProteinMPNN', true); await change('Stage 3 Protenix', true); await click('Queue round');
    const sent = calls.find(c => c.url === '/api/binder-continuation/selected')!.body;
    expect(sent.round_plan.stages[0].params).toMatchObject({ ppiflow_task: 'protein_binder_partial', ppiflow_protein_num_timesteps: 42, ppiflow_protein_scaffold_contig: '' });
});

it('backend exact native document refs preserve model and declared roles without duplicate guessed document refs', async () => {
    const ref = { kind: 'native_document', job_id: 'parent', artifact_id: 'complex', target_state: 'complex', model: 7 };
    interceptResult('/api/jobs/parent/generation-results', { records: [{ candidate_key: 'published-source', candidate_ref: { ...ref, label: 'not transport' }, structures: [doc], metrics: {} }], total: 1, offset: 0, limit: 100, receipt: {}, publication: {} });
    await mount(); await change('Select Candidate 1', true); await click('Next round (1)');
    await click('Add Sequence design'); await change('Stage 1 FA-MPNN', true); await click('FA-MPNN settings');
    const field = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!.findByType('input');
    expect(field('design_chain').props.value).toBe('B'); expect(field('target_chain').props.value).toBe('A');
    await act(async () => field('design_chain').props.onChange({ target: { value: '' } }));
    await click('Back to round'); await click('Queue round');
    expect(calls.find(c => c.url === '/api/binder-continuation/selected')!.body.selection.candidate_refs).toEqual([ref]);
    await close(); await mount(); await click('Next round (1)'); await click('FA-MPNN settings');
    expect(field('design_chain').props.value).toBe('');
    await click('Back to round');
    const extra = tree!.root.findAllByType('details').find(node => node.findAllByType('summary').some(summary => text(summary).startsWith('Additional selected outputs')))!;
    await act(async () => extra.findAllByType('button').find(node => text(node) === 'Remove')!.props.onClick()); await flush();
    expect(button('Next round (0)')).toBeTruthy();
    expect(tree!.root.findByProps({ 'aria-label': 'Select Candidate 1' }).props.checked).toBe(false);
});
