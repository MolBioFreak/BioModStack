import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { api } from '../../src/lib/api';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { readRoundStorage, writeRoundStorage } from '../../src/lib/binderSelectedRound';
import { activeInvocationPlan, initialInvocationEnvelope } from '../../src/lib/binderInvocationPlan';

const cwd = resolve(process.cwd(), '../..');
const python = process.env.BMS_SCHEMA_PYTHON ?? 'python';
const discovery = JSON.parse(execFileSync(python, ['-c', "import sys,json,asyncio;sys.path.insert(0,'platform/api');from routers.models import get_model;print(json.dumps({i:asyncio.run(get_model(i)) for i in 'fampnn proteinmpnn protenix boltz2 esmfold2'.split()}))"], { cwd, encoding: 'utf8' }));
const original = api.defaults.adapter;
let root: Root | undefined; let client: QueryClient; const evidence: any[] = [];
async function mount() {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.defaults.adapter = async config => {
        let data: any = [];
        if (config.url?.startsWith('/api/models/')) data = discovery[config.url.split('/').at(-1)!];
        if (config.url?.startsWith('/api/designs/')) data = { job_id: 'source' };
        if (config.url?.endsWith('/selection-context')) data = { candidate_documents: {} };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><BinderSelectedControls sourceJobId="source" selectedDesignIds={['exact']} onOpenJob={() => {}} /></QueryClientProvider>));
    await click('Next round (1)');
}
async function click(text: string) { await act(async () => { const button = [...document.querySelectorAll('button')].find(b => b.textContent === text); expect(button, text).toBeTruthy(); button!.click(); }); }
async function input(selector: string, value: string) { await act(async () => { const node = document.querySelector<HTMLInputElement>(selector)!; expect(node).toBeTruthy(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); }
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = original; localStorage.clear(); sessionStorage.clear(); });
const cases = [ ['fampnn', 'design', 'FA-MPNN'], ['fampnn', 'binder_design', 'FA-MPNN'], ['proteinmpnn', 'design', 'ProteinMPNN'], ['protenix', 'complex', 'Protenix'], ['boltz2', 'complex', 'Boltz-2'], ['esmfold2', 'predict', 'ESMFold2'] ];
it.each(cases)('actual selected route %s/%s retains native schema and cold draft', async (model, mode, label) => {
    const params: any = model === 'boltz2' ? { constraints: [], properties: [], boltz_extra_config: '--write_full_pae' } : model === 'esmfold2' ? { covalent_bonds: [] } : model === 'protenix' ? { covalent_bonds: [{ left_entity: 1, right_entity: 2, left_position: 1, right_position: 1, left_atom: 'SG', right_atom: 'SG' }] } : {};
    const stages = [{ enabled: true, drafts: {}, stage: { id: 'stage', kind: ['protenix', 'boltz2', 'esmfold2'].includes(model) ? 'prediction' : 'sequence_design', input: { selected: true, scope: 'whole_composition' }, models: [{ invocation_id: 'call', model_id: model, mode, params }] } }];
    writeRoundStorage('source', 'draft', { stages, target: null });
    await mount(); await click(`${label} settings`);
    const stored = () => readRoundStorage<any>('source', 'draft', {});
    expect(stored().stages[0].stage.models[0].params).toEqual(params); // Opening does not freeze defaults.
    if (model === 'protenix') await input('[aria-label="covalent_bonds[0].left_position"]', '2');
    if (model === 'esmfold2') {
        await click('Add covalent_bonds item');
        for (const name of ['chain_id1', 'chain_id2', 'res_idx1', 'res_idx2', 'atom_idx1', 'atom_idx2']) await act(async () => {
            const presence = document.querySelector<HTMLSelectElement>(`[aria-label="covalent_bonds[0].${name} presence"]`)!;
            presence.value = 'value'; presence.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await input('[aria-label="covalent_bonds[0].chain_id1"]', 'A');
        await input('[aria-label="covalent_bonds[0].chain_id2"]', 'A');
        await input('[aria-label="covalent_bonds[0].atom_idx2"]', '1');
    }
    if (model === 'boltz2' || model === 'esmfold2' || model === 'protenix') {
        for (const name of Object.keys(params).filter(key => !key.endsWith('extra_config'))) {
            const field = document.querySelector(`[data-sequence-designer-field="${name}"]`);
            expect(field).toBeTruthy(); expect(field!.querySelector('textarea')).toBeNull();
        }
        expect(document.querySelector('[data-sequence-designer-field="constraint"]')).toBeNull();
    }
    if (model === 'fampnn') {
        const selector = document.querySelector<HTMLSelectElement>('[aria-label="Mode call"]')!;
        await act(async () => { selector.value = mode === 'design' ? 'binder_design' : 'design'; selector.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => { selector.value = mode; selector.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(stored().stages[0].stage.models[0].mode).toBe(mode);
    }
    const saved = JSON.parse(JSON.stringify(stored()));
    await close(); await mount(); await click(`${label} settings`);
    expect(stored()).toEqual(saved);
    const wire = activeInvocationPlan(saved.stages);
    const envelope = initialInvocationEnvelope({ enabled: true, stages: saved.stages });
    await close();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const initialHost = document.createElement('div'); document.body.append(initialHost); root = createRoot(initialHost);
    let initialStages = saved.stages;
    await act(async () => root!.render(<QueryClientProvider client={client}><BinderInvocationPlan stages={saved.stages} onChange={value => { initialStages = value; }} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(activeInvocationPlan(initialStages)).toEqual(wire);
    expect(document.body.textContent).toContain('Original generated outputs');
    if (model === 'protenix') expect(document.querySelector<HTMLInputElement>('[aria-label="covalent_bonds[0].left_position"]')?.value).toBe('2');
    if (model === 'esmfold2') expect(document.querySelector<HTMLInputElement>('[aria-label="covalent_bonds[0].atom_idx2"]')?.value).toBe('1');
    const normalized = JSON.parse(execFileSync(python, ['-c', "import sys,json;sys.path.insert(0,'platform/api');from schemas import SelectedRoundRequest;from services.binder_selected_round import normalize;from services.sequence_round_inputs import normalize_request;p=json.load(sys.stdin);initial=normalize_request(p['envelope']);assert initial.round_plan.model_dump(mode='json')==SelectedRoundRequest.model_validate({'operation':'round','source_job_id':'source','idempotency_key':'mounted','selection':{'candidate_refs':[{'kind':'design','job_id':'source','design_id':'exact'}]},'round_plan':p['wire']}).round_plan.model_dump(mode='json');r=SelectedRoundRequest.model_validate({'operation':'round','source_job_id':'source','idempotency_key':'mounted','selection':{'candidate_refs':[{'kind':'design','job_id':'source','design_id':'exact'}]},'round_plan':p['wire']});print(normalize(r).model_dump_json())"], { cwd, input: JSON.stringify({ envelope, wire }), encoding: 'utf8' }));
    evidence.push({ model, mode, draft: saved.stages, envelope, wire, normalized });
    if (process.env.BMS_SELECTED_OPERATIONS_EVIDENCE) writeFileSync(process.env.BMS_SELECTED_OPERATIONS_EVIDENCE, JSON.stringify(evidence, null, 2));
});
