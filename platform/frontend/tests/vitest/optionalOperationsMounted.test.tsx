import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { activeInvocationPlan, changeInvocationMode, invocationModes, type InvocationDraftStage } from '../../src/lib/binderInvocationPlan';

vi.mock('../../src/lib/api', () => ({ fetchModelById: async (id: string) => ({ data: { id, params: [
    { name: 'seed', type: 'integer', default: 7 }, { name: 'verbose', type: 'boolean', default: true },
], modes: invocationModes[id].map(id => ({ id, params: ['seed', 'verbose'] })) } }) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => <span>Placement picker</span> }));
vi.mock('../../src/components/BinderMdInvocationEditor', () => ({ BinderMdInvocationEditor: () => null }));
let root: Root; let client: QueryClient; let latest: InvocationDraftStage[];
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); });
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
it('mode draft helper preserves empty/null values and same-model siblings', () => {
    const stage = make('fampnn'); if (stage.stage.kind === 'maturation') throw Error();
    stage.stage.models[0].params = { empty: '', nullable: null, zero: 0, off: false };
    stage.stage.models.push({ ...stage.stage.models[0], invocation_id: 'sibling', params: { seed: 8 } });
    const switched = changeInvocationMode(stage, 'fampnn', 'binder_design');
    const reopened = changeInvocationMode(JSON.parse(JSON.stringify(switched)), 'fampnn', 'design');
    expect(reopened.stage).toEqual(stage.stage);
});
