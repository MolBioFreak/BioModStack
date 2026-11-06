import '../../src/components/NativeBinderGenerationResults';
import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { writeBinderSelection, writeBinderNativeSources, readBinderNativeSources } from '../../src/lib/binderContinuation';
import { declaredBinderRoleDefaults } from '../../src/lib/binderSourceRoles';
import { submitSelectedRound, writeRoundStorage } from '../../src/lib/binderSelectedRound';

// TRANSPORT FIXTURE ONLY: actual Results parent, readers, composer, native editors,
// serializer and remote mutation owner. No inference, publication or yield claim.
vi.mock('react-plotly.js', () => ({ default: () => <div data-plot /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => <div data-molecule={props.structureUrl} /> }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
let tree: ReactTestRenderer | undefined, client: QueryClient;
const original = api.defaults.adapter;
const calls: Array<{ url: string; body: any }> = [];
let rounds: any[] = [], sets: any[] = [];
const job = { id: 'parent', name: 'TRANSPORT FIXTURE binder', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed', params: {}, design_count: 1, created_at: '2026-09-01T00:00:00Z' };
const doc = { binder_chains: 'B', target_chains: 'A', artifact_id: 'complex', primary: true, target_state: 'complex', logical_path: 'exact/complex.pdb', download_url: '/exact/complex.pdb' };
const monomer = { binder_chains: 'A', target_chains: '', artifact_id: 'monomer', target_state: 'binder', logical_path: 'exact/binder.cif', download_url: '/exact/binder.cif' };
const records = Array.from({ length: 32 }, (_, i) => ({ candidate_key: `producer:${i}`, ...(i === 0 ? { design_id: 'design' } : {}), metrics: { score: i, outcome: i % 2 ? 'rejected' : 'passed', rank: i === 0 ? 1 : null }, structures: i === 0 ? [doc, monomer] : [{ ...doc, artifact_id: `native-${i}`, download_url: `/exact/${i}.pdb` }] }));
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree!.root.findAllByType('button').find(node => text(node) === label)!;
const flush = async () => {
    const deadline = Date.now() + 5000;
    do {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
        if (!client?.isFetching() && !client?.isMutating()) return;
    } while (Date.now() < deadline);
    throw new Error('Results queries did not settle within 5s');
};
const click = async (label: string) => { expect(button(label), `${label}: ${text(tree!.root)}; calls=${calls.map(call => call.url).join(',')}`).toBeTruthy(); await act(async () => button(label).props.onClick()); await flush(); };
const change = async (label: string, value: string | boolean) => { await act(async () => tree!.root.findByProps({ 'aria-label': label }).props.onChange({ target: typeof value === 'boolean' ? { checked: value } : { value } })); await flush(); };
async function mount() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<MemoryRouter initialEntries={['/designs/parent']}><QueryClientProvider client={client}><ThemeProvider><Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes></ThemeProvider></QueryClientProvider></MemoryRouter>); });
    const deadline = Date.now() + 10000;
    while (!text(tree!.root).includes('32 loaded') && Date.now() < deadline) await flush();
    // Reopen can intentionally restore the exact-document Structure view.
    expect(text(tree!.root)).toContain('32 loaded');
}
async function close() { await act(async () => tree?.unmount()); tree = undefined; client?.clear(); }
beforeEach(() => {
    rounds = []; sets = []; calls.length = 0; localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, '', '/');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })));
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    api.defaults.adapter = async config => {
        const url = String(config.url), params = config.params ?? {}, body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url, body });
        let data: any;
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === '/api/jobs/parent') data = { ...job, saved_selection_sets: sets };
        else if (url === '/api/execution-targets') data = [];
        else if (url === '/api/designs/by-job/parent/backbone-summary') data = { backbones: [] };
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
        else throw new Error(`Unexpected transport fixture request ${config.method} ${url}`);
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { await close(); api.defaults.adapter = original; vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

// Full parent/editor/reload journeys exceed the default 5s even with settled queries.
it('actual Results parent serializes independent stages and preserves null/false/zero/clear across cold draft reload; three rounds read back all fixture samples', async () => {
    writeBinderSelection('parent', ['design']);
    writeBinderNativeSources('parent', [{ job_id: 'parent', artifact_id: 'native-31' }]);
    await mount();
    await click('Next round (2)');
    await click('Add Maturation');
    await click('PPIFlow settings');
    // Edit the actual native metadata fields, not a replacement JSON form.
    const field = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!;
    await act(async () => field('ppiflow_task').findByType('select').props.onChange({ target: { value: 'protein_binder_partial' } }));
    await act(async () => field('enabled').findByType('input').props.onChange({ target: { checked: false } }));
    await act(async () => field('samples').findByType('input').props.onChange({ target: { value: '0' } }));
    await act(async () => field('fixed_positions').findByType('input').props.onChange({ target: { value: '' } }));
    await click('Back to round');
    await click('Add Sequence design');
    await click('Add fampnn call'); await click('Add proteinmpnn call');
    await click('FA-MPNN settings'); await click('Clear Samples (null)'); await click('Back to round');
    await click('Add Blind prediction');
    await click('Add protenix call'); await click('Add boltz2 call');
    await close(); await mount(); await click('Next round (2)');
    await click('Queue round');
    const sent = calls.filter(call => call.url === '/api/binder-continuation/selected')[0].body;
    expect(sent.execution_target_id).toBeNull();
    expect(sent.selection.candidate_refs.map((ref: any) => ref.kind)).toEqual(['design', 'native_document']);
    expect(sent.round_plan.stages.map((stage: any) => stage.kind)).toEqual(['maturation', 'sequence_design', 'prediction']);
    expect(sent.round_plan.stages[0].params).toMatchObject({ ppiflow_task: 'protein_binder_partial', enabled: false, samples: 0, fixed_positions: '' });
    expect(sent.round_plan.stages[1].models.map((model: any) => model.model_id)).toEqual(['fampnn', 'proteinmpnn']);
    expect(sent.round_plan.stages[1].models[0].params.samples).toBeNull();
    expect(sent.round_plan.stages[2].models.map((model: any) => model.model_id)).toEqual(['protenix', 'boltz2']);
    expect(sent.round_plan.stages[1].input.stage_id).toBe(sent.round_plan.stages[0].id);
    expect(sent.round_plan.stages[2].input.stage_id).toBe(sent.round_plan.stages[1].id);
    expect(tree!.root.findAll(node => node.type === 'input' && /^Select Protenix output /.test(node.props['aria-label'] ?? ''))).toHaveLength(33);
    await change('Select Protenix output 33', true);
    expect(readBinderNativeSources('parent')).toEqual([{ job_id: 'parent', artifact_id: 'native-31' }]);
    await change('Select Protenix output 33', false);
    expect(button('Next round (2)')).toBeTruthy();
    await change('Select Protenix output 33', true);
    await click('Next round (3)'); await click('Queue round');
    await close(); await mount();
    expect(tree!.root.findByProps({ 'aria-label': 'Returned round' }).findAllByType('option')).toHaveLength(2);
    await click('Next round (3)'); await click('Queue round');
    const posts = calls.filter(call => call.url === '/api/binder-continuation/selected').map(call => call.body);
    expect(new Set(posts.map(post => post.idempotency_key)).size).toBe(3);
    expect(posts[2].selection.candidate_refs.at(-1)).toEqual({ kind: 'native_sequence', job_id: 'fixture-child', publication_ref: 'fixture-publication', native_output_id: 'sample-32' });
    expect(tree!.root.findByProps({ 'aria-label': 'Returned round' }).findAllByType('option')).toHaveLength(3);
}, 30000);

it('round ref updates preserve independent native sources, remove mirrored refs and honor explicit replacement', async () => {
    const source = { job_id: 'parent', artifact_id: 'native-31', document: { ...doc, artifact_id: 'native-31', model: 2 } };
    writeBinderNativeSources('parent', [source]);
    await mount(); expect(button('Next round (1)')).toBeTruthy();
    const update = async (refs: any[], replace = false) => {
        await act(async () => window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId: 'parent', refs, replace } })));
        await flush();
    };
    const mirrored = { kind: 'native_document', job_id: 'fixture-child', artifact_id: 'child-doc', model: 3 };
    await update([mirrored]);
    expect(button('Next round (2)')).toBeTruthy();
    expect(readBinderNativeSources('parent')).toHaveLength(2);
    expect(readBinderNativeSources('parent')[0]).toEqual(source);
    await update([{ kind: 'native_sequence', job_id: 'fixture-child', publication_ref: 'fixture-publication', native_output_id: 'sequence' }]);
    expect(button('Next round (2)')).toBeTruthy();
    expect(readBinderNativeSources('parent')).toEqual([source]);
    await update([]); expect(button('Next round (1)')).toBeTruthy();
    expect(readBinderNativeSources('parent')).toEqual([source]);
    await update([], true); expect(button('Next round (0)')).toBeTruthy();
    expect(readBinderNativeSources('parent')).toEqual([]);
}, 30000);

it('actual native Results preserves cross-page mixed selection, exact complex/binder inspection and backend frozen ranking/final sets', async () => {
    await mount();
    const rankPicker = tree!.root.findAll(node => typeof node.type === 'function' && node.props.label === 'Rank by')[0];
    await act(async () => rankPicker.props.onChange('score'));
    await change('Ranking direction', 'descending');
    const rankingOwner = tree!.root.findAll(node => typeof node.type === 'function' && node.props.ranking?.metric === 'score' && node.props.onLoad)[0];
    await act(async () => rankingOwner.findAllByType('button').find(node => text(node) === 'Save set (32)')!.props.onClick()); await flush();
    expect(sets.at(-1).ranking).toMatchObject({ metric: 'score', direction: 'descending', missing: 'last', scope: 'matching' });
    expect(sets.at(-1).candidate_refs[0]).toMatchObject({ kind: 'native_document', artifact_id: 'native-31' });
    await change('Ranking direction', 'ascending');
    await change('Select Candidate 1', true);
    await click('Next native records'); await change('Select Candidate 32', true);
    await click('Rejected');
    expect(text(tree!.root)).toContain('outside this view');
    await click('Previous native records'); await click('All');
    await click('Candidate 1'); await change('Published document', '1');
    expect(tree!.root.findByProps({ 'data-molecule': monomer.download_url })).toBeTruthy();
    expect(window.location.search).toContain('artifact_id=monomer');
    await close(); await mount();
    expect(tree!.root.findByProps({ 'data-molecule': monomer.download_url })).toBeTruthy();
    const setsOwner = tree!.root.findAll(node => typeof node.type === 'function' && node.props.candidates?.length === 2 && node.props.onLoad)[0];
    expect(setsOwner).toBeTruthy();
    const setInput = setsOwner.findByProps({ 'aria-label': 'Candidate set name' });
    await act(async () => setInput.props.onChange({ target: { value: 'Mixed native set' } }));
    const save = setsOwner.findAllByType('button').find(node => text(node) === 'Mark final')!;
    await act(async () => save.props.onClick()); await flush();
    expect(sets.at(-1)).toMatchObject({ name: 'Mixed native set', final_designation: { designated: true } });
    expect(sets.at(-1).candidate_refs).toHaveLength(2);
    expect(calls.filter(call => call.url === '/api/jobs/parent').length).toBeGreaterThan(1);
}, 30000);

it('keeps the same idempotent intent after lost transport and reload, and preserves repeated design instances', async () => {
    writeBinderSelection('parent', ['design']);
    writeRoundStorage('parent', 'draft', { target: null, stages: [
        { enabled: true, drafts: {}, stage: { id: 'before', kind: 'sequence_design', input: { selected: true }, models: [{ model_id: 'proteinmpnn', params: { samples: 0 } }] } },
        { enabled: true, drafts: {}, stage: { id: 'after', kind: 'sequence_design', input: { stage_id: 'before' }, models: [{ model_id: 'fampnn', params: { samples: 2 } }] } },
        { enabled: false, drafts: {}, stage: { id: 'off', kind: 'prediction', input: { stage_id: 'after' }, models: [{ model_id: 'boltz2', params: {} }] } },
    ] });
    const transport = api.defaults.adapter as Function;
    let lost = true;
    api.defaults.adapter = async config => { const response = await transport(config); if (config.url === '/api/binder-continuation/selected' && lost) { lost = false; throw Error('Fixture lost response'); } return response; };
    await mount(); await click('Next round (1)'); await click('Queue round');
    expect(text(tree!.root)).toContain('Fixture lost response');
    await close(); await mount(); await click('Next round (1)'); await click('Queue round');
    const posts = calls.filter(call => call.url === '/api/binder-continuation/selected').map(call => call.body);
    expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
    expect(posts[0].round_plan.stages.map((stage: any) => stage.id)).toEqual(['before', 'after']);
    expect(rounds).toHaveLength(1);
}, 30000);

it('protein recipe visibly orders flow then intentionally chosen designers then predictors without a hidden designer', async () => {
    writeBinderSelection('parent', ['design']); await mount(); await click('Next round (1)'); await click('Add protein flow → design → predict');
    expect(tree!.root.findAll(node => node.type === 'section' && /^Invocation /.test(node.props['aria-label'] ?? ''))).toHaveLength(0);
    await click('Add proteinmpnn call'); await click('Add caliby_binder call'); await click('Add protenix call');
    await click('Queue round');
    const stages = calls.filter(call => call.url === '/api/binder-continuation/selected').at(-1)!.body.round_plan.stages;
    expect(stages.map((stage: any) => stage.kind)).toEqual(['maturation', 'sequence_design', 'prediction']);
    expect(stages[0].params).toEqual({ ppiflow_task: 'protein_binder_partial', maturation_flow_enabled: true, maturation_redesign_enabled: false });
    expect(stages[1].models.map((model: any) => model.model_id)).toEqual(['proteinmpnn', 'caliby_binder']);
}, 30000);

it('keeps task-owned protein and antibody defaults and edited drafts separate without null-default hydration', async () => {
    writeBinderSelection('parent', ['design']); await mount(); await click('Next round (1)'); await click('Add protein flow → design → predict'); await click('PPIFlow settings');
    const parameter = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!;
    const start = () => parameter('ppiflow_start_t').findByType('input');
    const task = async (value: string) => { await act(async () => parameter('ppiflow_task').findByType('select').props.onChange({ target: { value } })); };
    expect(start().props.value).toBe(0.15);
    await act(async () => start().props.onChange({ target: { value: '0.22' } }));
    await task('antibody_partial'); expect(start().props.value).toBe(0.8);
    await act(async () => start().props.onChange({ target: { value: '0.6' } }));
    await task('protein_binder_partial'); expect(start().props.value).toBe(0.22);
    await click('Back to round'); await close(); await mount(); await click('Next round (1)'); await click('PPIFlow settings');
    expect(start().props.value).toBe(0.22); await task('antibody_partial'); expect(start().props.value).toBe(0.6);
}, 30000);

it('approved role initialization uses the exact producer document and preserves explicit clears through reload', async () => {
    writeBinderSelection('parent', ['design']);
    await mount(); await click('Next round (1)'); await click('Add Sequence design'); await click('Add fampnn call'); await click('FA-MPNN settings');
    const field = (name: string) => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === name).at(-1)!.findByType('input');
    expect(field('design_chain').props.value).toBe('B');
    expect(field('target_chain').props.value).toBe('A');
    await act(async () => field('design_chain').props.onChange({ target: { value: '' } }));
    await click('Back to round'); await close(); await mount(); await click('Next round (1)'); await click('FA-MPNN settings');
    expect(field('design_chain').props.value).toBe(''); expect(field('target_chain').props.value).toBe('A');
    await click('Back to round'); await click('Queue round');
    expect(calls.filter(call => call.url === '/api/binder-continuation/selected').at(-1)!.body.round_plan.stages[0].models[0].params).toMatchObject({ design_chain: '', target_chain: 'A' });
    const fields = [{ name: 'design_chain' }, { name: 'target_chain' }];
    expect(declaredBinderRoleDefaults([monomer], fields)).toEqual({ design_chain: 'A', target_chain: '' });
    expect(declaredBinderRoleDefaults([doc, monomer], fields)).toEqual({});
    expect(declaredBinderRoleDefaults([doc, undefined], fields)).toEqual({});
}, 30000);

it('selected-round remote transport uses the actual shared prepared review and canonical Job submission', async () => {
    const prepared = { name: 'transport-only prepared', model_id: 'boltz2', mode: 'complex', execution_target_id: 'vast:fixture', launch_context_id: 'prepared-project', params: { sequence: 'ACDE' } };
    const preview = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: prepared,
        plan: { requested_json: prepared.params, effective_json: prepared.params, source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
    const post = vi.spyOn(api, 'post').mockImplementation(async (url) => {
        if (url === '/api/jobs/execution-plan/preview') return { data: preview };
        if (url === '/api/jobs') return { data: { id: 'fixture-remote', name: 'prepared' } };
        throw { isAxiosError: true, response: { status: 409, data: { detail: { code: 'remote_prepared_job_review_required', job_requests: [prepared], response_context: { round_id: 'fixture-remote-round', operation: 'round' } } } } };
    });
    try {
        const pending = submitSelectedRound({ operation: 'round', source_job_id: 'parent', idempotency_key: 'fixture-key', selection: { candidate_refs: [{ kind: 'design', job_id: 'parent', design_id: 'design' }] }, round_plan: { schema_version: 1, stages: [{ id: 'predict', input: { selected: true }, kind: 'prediction', models: [{ model_id: 'boltz2', params: {} }] }] }, execution_target_id: 'vast:fixture' });
        const approval = () => [...document.querySelectorAll('button')].find(node => node.textContent === 'Approve and submit');
        for (let i = 0; i < 100 && !approval(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
        expect(approval()).toBeTruthy();
        expect(post.mock.calls.filter(call => call[0] === '/api/jobs')).toHaveLength(0);
        await act(async () => { approval()!.click(); await pending; });
        expect(post.mock.calls.filter(call => call[0] === '/api/binder-continuation/selected')).toHaveLength(1);
        expect(post.mock.calls.find(call => call[0] === '/api/jobs')?.[1]).toMatchObject({ ...prepared, execution_plan_approval: preview.approval_digest });
        expect(await pending).toMatchObject({ round_id: 'fixture-remote-round', launched_jobs: [{ id: 'fixture-remote' }] });
    } finally { post.mockRestore(); document.body.replaceChildren(); }
}, 30000);
