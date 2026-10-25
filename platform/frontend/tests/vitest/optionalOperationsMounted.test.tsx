import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { invocationSettingsFields } from '../../src/lib/invocationSettingsFields';
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { invocationCandidateLabel, initialInvocationEnvelope, activeInvocationPlan, changeInvocationMode, invocationModes, type InvocationDraftStage } from '../../src/lib/binderInvocationPlan';

let actualCatalogs: Record<string, any> | undefined;
vi.mock('../../src/lib/api', () => ({ fetchModelById: async (id: string) => ({ data: actualCatalogs?.[id] ?? { id, params: [
    { name: 'seed', type: 'integer', default: 7 }, { name: 'verbose', type: 'boolean', default: true },
], modes: invocationModes[id].map(id => ({ id, params: ['seed', 'verbose'] })) } }) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => <span>Placement picker</span> }));
vi.mock('../../src/components/BinderMdInvocationEditor', () => ({ BinderMdInvocationEditor: () => null }));
let root: Root; let client: QueryClient; let latest: InvocationDraftStage[];
afterEach(async () => { actualCatalogs = undefined; if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); });
const make = (model: string, id = model): InvocationDraftStage => ({ enabled: true, drafts: {}, stage: { id: `stage-${id}`, kind: ['protenix', 'boltz2', 'esmfold2', 'boltz_cp_experimental'].includes(model) ? 'prediction' : 'sequence_design', input: { selected: true, scope: 'whole_composition' }, models: [{ invocation_id: id, model_id: model, mode: invocationModes[model][0], params: { seed: 0, verbose: false } }] } });
async function mount(stages: InvocationDraftStage[], candidates?: any[]) {
    latest = stages; client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); host.style.width = '390px'; document.body.append(host); root = createRoot(host);
    function Form() { const [value, set] = useState(stages); return <BinderInvocationPlan candidates={candidates} stages={value} onChange={value => { latest = value; set(value); }} />; }
    await act(async () => { root.render(<QueryClientProvider client={client}><Form /></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function change(selector: string, value: string) { await act(async () => { const e = document.querySelector<HTMLSelectElement>(selector)!; e.value = value; e.dispatchEvent(new Event('change', { bubbles: true })); }); }
async function click(text: string) { await act(async () => { const button = [...document.querySelectorAll('button')].find(b => b.textContent === text)!; expect(button).toBeTruthy(); button.click(); }); }
it('mounted repeated calls emit exact predecessor and disabled drafts survive serialized reopen', async () => {
    const first = make('fampnn', 'one'); const second = make('fampnn', 'two'); const predict = make('esmfold2');
    await mount([first, second, predict]);
    expect(document.body.textContent).toContain('Future generated outputs');
    await change('[aria-label="Stage 3 input"]', 'stage-one:one');
    await change('[aria-label="Mode one"]', 'binder_design');
    await change('[aria-label="Mode one"]', 'design');
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="Enable invocation two"]')!.click());
    const saved = JSON.parse(JSON.stringify(latest)); const wire = activeInvocationPlan(saved);
    expect(wire.stages).toHaveLength(2); expect(wire.stages[1].input).toEqual({ stage_id: 'stage-one', invocation_id: 'one', scope: 'whole_composition' });
    expect(saved[1].stage.models[0].params).toEqual({ seed: 0, verbose: false });
    expect(saved[0].stage.models[0].params).toEqual({ seed: 0, verbose: false });
    expect(document.querySelector('[aria-label="Stage 3 input"]')!.textContent).toContain('Stage 1 · FA-MPNN call 1');
    await act(async () => root.unmount()); await mount(saved);
    expect(document.querySelector<HTMLInputElement>('[aria-label="Enable invocation two"]')!.checked).toBe(false);
    expect(activeInvocationPlan(latest)).toEqual(wire);
});
it.each(Object.keys(invocationModes).filter(id => id !== 'molecular_dynamics'))('%s exposes every declared mode and sparse native controls', async model => {
    await mount([make(model)]);
    expect([...document.querySelectorAll(`[aria-label="Mode ${model}"] option`)].map(e => e.textContent)).toEqual(invocationModes[model]);
    expect(document.body.textContent).toContain('Requested override');
    await click('Remove override: Prediction seed');
    expect(activeInvocationPlan(latest).stages[0]).toMatchObject({ models: [{ params: { verbose: false } }] });
    expect(document.body.textContent).toContain('Inherited native default');
    expect(document.querySelector<HTMLInputElement>('[aria-label="seed"]')!.value).toBe('7');
});
it('selected packing retains ordered repeated exact states and independent Local placement', async () => {
    const stage = make('caliby_experimental'); if (stage.stage.kind === 'maturation') throw Error();
    stage.stage.models[0].mode = 'sidechain_pack'; stage.stage.models[0].execution_target_id = null;
    const refs = [1, 2].map(model => ({ kind: 'native_document', job_id: 'producer', artifact_id: 'exact', model }));
    await mount([stage], refs); await click('Add ensemble');
    await change('[aria-label="Add state 1"]', '1'); await change('[aria-label="Add state 1"]', '0'); await change('[aria-label="Add state 1"]', '1');
    const call = (activeInvocationPlan(latest).stages[0] as any).models[0];
    expect(call.input_groups[0].states.map((s: any) => s.candidate_ref.model)).toEqual([2, 1, 2]);
    expect(call.execution_target_id).toBeNull(); expect(call.params).not.toHaveProperty('binder_chains');
    expect(document.body.textContent).toContain('Local (explicit)');
    expect(document.body.textContent).toContain('Exact selected outputs');
    const explicit = [...document.querySelectorAll('label')].find(label => label.textContent?.includes('Explicit placement'))!.querySelector<HTMLInputElement>('input')!;
    await act(async () => explicit.click());
    expect((activeInvocationPlan(latest).stages[0] as any).models[0]).not.toHaveProperty('execution_target_id');
    expect(document.body.textContent).toContain('Inherited round placement');
});
const discovery = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json,asyncio;sys.path.insert(0,'platform/api');from routers.models import get_model;ids='proteinmpnn fampnn caliby_binder caliby_experimental ligandmpnn protonpottsmpnn protenix boltz2 esmfold2 boltz_cp_experimental'.split();print(json.dumps({i:asyncio.run(get_model(i)) for i in ids}))"], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8', maxBuffer: 16000000 }));
const evidence: any[] = [];
it.each(Object.entries(invocationModes).filter(([id]) => id !== 'molecular_dynamics').flatMap(([id, modes]) => modes.map(mode => [id, mode])))('real discovery %s/%s covers every field without JSON textareas', async (model, mode) => {
    actualCatalogs = discovery;
    const stage = make(model); if (stage.stage.kind === 'maturation') throw Error();
    stage.stage.models[0].mode = mode; stage.stage.models[0].params = {};
    await mount([stage]);
    const fields = invocationSettingsFields(discovery[model], mode);
    const rendered = [...document.querySelectorAll('[data-operation-field]')].map(e => e.getAttribute('data-operation-field'));
    expect(rendered.sort()).toEqual(fields.map(f => f.name).sort());
    for (const field of fields) {
        const node = document.querySelector(`[data-operation-field="${field.name}"]`)!;
        expect(!!node.querySelector('input, select, button') || !!field.read_only || node.textContent?.includes('off for blind prediction')).toBe(true);
    }
    expect(document.querySelector('textarea')).toBeNull();
    expect(document.body.textContent).not.toContain('typed schema unavailable');
    expect(activeInvocationPlan(latest).stages[0]).toMatchObject({ models: [{ params: {} }] });
    const editable = fields.find(f => f.schema && ['integer', 'boolean'].includes(String(f.schema.type)) && Object.hasOwn(f.schema, 'default'));
    if (editable) await click(`Set explicit ${editable.name}`);
    const wire = activeInvocationPlan(latest);
    const normalized = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json;sys.path.insert(0,'platform/api');from schemas import SelectedRoundRequest;from services.binder_selected_round import normalize;p=json.load(sys.stdin);r=SelectedRoundRequest.model_validate({'operation':'round','source_job_id':'source','idempotency_key':'mounted','selection':{'candidate_refs':[{'kind':'design','job_id':'source','design_id':'selected'}]},'round_plan':p});print(normalize(r).model_dump_json())"], { cwd: resolve(process.cwd(), '../..'), input: JSON.stringify(wire), encoding: 'utf8' }));
    expect(normalized.round_plan.stages[0].models[0].mode).toBe(mode);
    evidence.push({ model, mode, fields: fields.map(f => ({ name: f.name, type: f.type, schema: f.schema ?? null, applicable: f.display_applicable })), draft: latest, envelope: initialInvocationEnvelope({ enabled: true, stages: latest }), wire, normalized });
    if (process.env.BMS_OPERATIONS_EVIDENCE) writeFileSync(process.env.BMS_OPERATIONS_EVIDENCE, JSON.stringify(evidence, null, 2));
});
it('real LigandMPNN nested dictionary controls edit zero and survive cold reopen', async () => {
    actualCatalogs = discovery;
    const stage = make('ligandmpnn'); if (stage.stage.kind === 'maturation') throw Error();
    stage.stage.models[0].params = { bias: { A: 0 }, symmetry_residues: [['A1', 'A2']] };
    await mount([stage]);
    expect(document.querySelector<HTMLInputElement>('[aria-label="bias.A"]')?.value).toBe('0');
    await act(async () => { const input = document.querySelector<HTMLInputElement>('[aria-label="bias.A"]')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '2'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    expect((activeInvocationPlan(latest).stages[0] as any).models[0].params.bias).toEqual({ A: 2 });
    const saved = JSON.parse(JSON.stringify(latest));
    await act(async () => root.unmount()); await mount(saved);
    expect(document.querySelector<HTMLInputElement>('[aria-label="bias.A"]')?.value).toBe('2');
    expect((activeInvocationPlan(latest).stages[0] as any).models[0].params.symmetry_residues).toEqual([['A1', 'A2']]);
});
it('source labels prefer supplied human metadata without changing exact identity', () => {
    const ref = { kind: 'native_document' as const, job_id: 'owner', artifact_id: 'artifact', target_state: 'Open conformation', model: 2 };
    expect(invocationCandidateLabel(ref)).toBe('Open conformation · MODEL 2');
    expect(invocationCandidateLabel(ref, 'Published sample')).toBe('Published sample · MODEL 2');
    expect(ref.artifact_id).toBe('artifact');
});
it('mode draft helper preserves empty/null values and same-model siblings', () => {
    const stage = make('fampnn'); if (stage.stage.kind === 'maturation') throw Error();
    stage.stage.models[0].params = { empty: '', nullable: null, zero: 0, off: false };
    stage.stage.models.push({ ...stage.stage.models[0], invocation_id: 'sibling', params: { seed: 8 } });
    const switched = changeInvocationMode(stage, 'fampnn', 'binder_design');
    const reopened = changeInvocationMode(JSON.parse(JSON.stringify(switched)), 'fampnn', 'design');
    expect(reopened.stage).toEqual(stage.stage);
});
