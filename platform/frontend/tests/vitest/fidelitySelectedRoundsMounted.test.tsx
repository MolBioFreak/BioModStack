// Focused receiving journeys ported from the read-only management audit.
// Transport is inert; serialized cold reopen is not a live database assertion.
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
const exactCatalogs = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['fampnn','fampnn_child','proteinmpnn','protenix','boltz2','esmfold2','caliby_binder']]))"], { encoding: 'utf8' }));
import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { writeBinderSelection, writeBinderNativeSources } from '../../src/lib/binderContinuation';
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
    while (!tree!.root.findAllByProps({ 'aria-label': 'Candidate data table' }).length && Date.now() < deadline) await flush();
    expect(tree!.root.findAllByProps({ 'aria-label': 'Candidate data table' })).toHaveLength(1);
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
        else if (url.startsWith('/api/models/')) data = exactCatalogs.find((m:any)=>m.id===url.split('/').at(-1)) ?? {params:[]};
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

it('audit actual selected nonfirst source with exact FA and Protein native controls cold reopens', async () => {
 writeBinderNativeSources('parent', [{job_id:'parent', artifact_id:'native-31'}]);
 await mount(); await click('Next round (1)'); await click('Add Sequence design');
 await change('Stage 1 FA-MPNN',true); await change('Stage 1 ProteinMPNN',true);
 const edits:any = { fampnn: {fampnn_seed:'19',seqs_per_design:'3',fampnn_temperature:'0.23',fampnn_psce_threshold:'0',fampnn_fix_target_sidechains:true,fixed_positions:'a:10B,1:5,A:20-30'}, proteinmpnn:{seqs_per_design:'7',mpnn_temperature:'0.31',mpnn_relax_convergence_score:'0',fixed_positions:'a:10B,1:5,A:20-30'} };
 for(const [model,label] of [['fampnn','FA-MPNN'],['proteinmpnn','ProteinMPNN']]) {
  await click(label+' settings');
  for(const [name,value] of Object.entries(edits[model])) {
   const owner=tree!.root.findAll(node=>typeof node.type==='function' && node.props.param?.name===name).at(-1)!;
   expect(owner,name).toBeTruthy();
   await act(async()=>owner.findByType('input').props.onChange({target:typeof value==='boolean'?{checked:value}:{value}}));
  }
  await click('Back to round');
 }
 await close(); await mount(); await click('Next round (1)'); await click('Queue round');
 const sent=calls.filter(c=>c.url==='/api/binder-continuation/selected').at(-1)!.body;
 if(process.env.BMS_FRONTEND_EVIDENCE)writeFileSync(process.env.BMS_FRONTEND_EVIDENCE+'/selected-round.json',JSON.stringify(sent,null,2));
 expect(sent.selection.candidate_refs).toEqual([{kind:'native_document',job_id:'parent',artifact_id:'native-31'}]);
 expect(sent.round_plan.stages[0].models[0]).toMatchObject({model_id:'fampnn',params:{fampnn_seed:19,seqs_per_design:3,fampnn_temperature:0.23,fampnn_psce_threshold:0,fampnn_fix_target_sidechains:true,fixed_positions:'a:10B,1:5,A:20-30'}});
 expect(sent.round_plan.stages[0].models[1]).toMatchObject({model_id:'proteinmpnn',params:{seqs_per_design:7,mpnn_temperature:0.31,mpnn_relax_convergence_score:0,fixed_positions:'a:10B,1:5,A:20-30'}});
}, 15000);
