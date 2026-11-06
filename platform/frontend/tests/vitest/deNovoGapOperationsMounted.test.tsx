import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { activeInvocationPlan, changeInvocationMode, type InvocationDraftStage } from '../../src/lib/binderInvocationPlan';
import { hydrateGeneralSequenceDesign } from '../../src/lib/generalSequenceDesign';
import type { SelectedRoundModel } from '../../src/lib/binderSelectedRound';
vi.mock('../../src/lib/api', () => ({ fetchModelById: vi.fn(async () => ({ data: { params: [], modes: [] } })) }));
vi.mock('../../src/components/BinderMdInvocationEditor', () => ({ BinderMdInvocationEditor: () => <div>MD settings</div> }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => <div>Local</div> }));
let root: Root | undefined;
let host: HTMLDivElement;
let draft: InvocationDraftStage[];
const ref = { kind: 'native_document' as const, job_id: 'job', artifact_id: 'exact-artifact', target_state: 'Bound state', model: 2 };
const keys = ['fixed_pos_seq', 'fixed_pos_scn', 'fixed_pos_override_seq', 'pos_restrict_aatype', 'symmetry_pos'] as const;
const calls = () => (draft[0].stage as { models: SelectedRoundModel[] }).models;
const emitted = () => JSON.parse(JSON.stringify(activeInvocationPlan(draft)));
const byLabel = <T extends HTMLElement>(label: string) => host.querySelector<T>(`[aria-label="${label}"]`)!;
async function select(label: string, value: string) {
    await act(async () => { const el = byLabel<HTMLSelectElement>(label); el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function click(text: string) {
    const button = [...host.querySelectorAll('button')].filter(el => el.textContent === text).at(-1)!;
    expect(button).toBeTruthy(); await act(async () => button.click());
}
async function mount(selected = true) {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = () => root!.render(<QueryClientProvider client={client}><BinderInvocationPlan stages={draft} candidates={selected ? [ref] : undefined} onChange={value => { draft = value; render(); }} /></QueryClientProvider>);
    await act(async () => render());
}
async function cold(selected = true) {
    const saved = JSON.stringify(draft); await act(async () => root!.unmount()); root = undefined; host.remove();
    draft = hydrateGeneralSequenceDesign({ invocation_draft: { enabled: true, stages: JSON.parse(saved) } }).invocation_draft!.stages; await mount(selected);
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); });

it('retains all five Caliby state constraints per call across packing, off, serialized cold reopen and design', async () => {
    draft = [{ enabled: true, drafts: {}, stage: { id: 'sequence', kind: 'sequence_design', input: { selected: true }, models: ['first', 'second'].map(invocation_id => ({ invocation_id, model_id: 'caliby_experimental', mode: 'ensemble_design', params: { seed: 0 }, input_groups: [{ ensemble_id: invocation_id, states: [{ state_id: 'same-state', candidate_ref: ref }] }] })) } }];
    await mount();
    for (const [i, key] of keys.entries()) {
        const input = byLabel<HTMLElement>('Invocation first').querySelector<HTMLInputElement>(`[aria-label="same-state ${key}"]`)!;
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, `A${i + 1}`); input.dispatchEvent(new Event('input', { bubbles: true })); });
    }
    const settings = structuredClone(calls()[0].input_groups![0].states[0].settings);
    expect(Object.keys(settings!)).toEqual(keys);
    await select('Mode first', 'sidechain_pack');
    expect(byLabel<HTMLElement>('Invocation first').querySelector('[aria-label="same-state fixed_pos_seq"]')).toBeNull();
    expect(emitted().stages[0].models[0].input_groups[0].states).toEqual([{ state_id: 'same-state', candidate_ref: ref }]);
    await act(async () => byLabel<HTMLInputElement>('Enable invocation first').click());
    expect(host.textContent).toContain('Off · settings kept'); expect(emitted().stages[0].models).toHaveLength(1);
    await cold();
    await act(async () => byLabel<HTMLInputElement>('Enable invocation first').click());
    await select('Mode first', 'ensemble_design');
    expect(calls()[0].input_groups![0].states[0].settings).toEqual(settings);
    expect(calls()[0].params).toEqual({ seed: 0 });
    expect(calls()[1].input_groups![0].states[0]).not.toHaveProperty('settings');
    expect(emitted().stages[0].models[0].input_groups[0].states[0]).toEqual({ state_id: 'same-state', candidate_ref: ref, settings });
    await cold(); expect(calls()[0].input_groups![0].states[0].settings).toEqual(settings);
});

it('keeps original defaults and exact explicit dependencies through repeats, off and cold reopen', async () => {
    draft = []; await mount(false);
    await click('Add Sequence design'); await click('Add ProteinMPNN'); await click('Add ProteinMPNN');
    await click('Add Blind prediction'); await click('Add Protenix');
    await click('Add Molecular dynamics'); await click('Add Molecular dynamics');
    expect(draft.map(item => item.stage.input)).toEqual(Array(3).fill({ selected: true, scope: 'whole_composition' }));
    expect(host.textContent).toContain('Stages use the input chosen below; order alone does not connect them.');
    const sequence = draft[0].stage.id; const second = calls()[1].invocation_id!;
    await select('Stage 2 input', `${sequence}:${second}`);
    await select('Stage 3 input', `${draft[1].stage.id}:`);
    expect(byLabel('Stage 2 source').textContent).toContain('ProteinMPNN · model 2');
    const plan = emitted(); await cold(false); expect(emitted()).toEqual(plan);
    await act(async () => byLabel<HTMLInputElement>(`Enable invocation ${second}`).click());
    expect(byLabel('Stage 2 source').textContent).toContain('Source is off');
    expect(emitted().stages[1].input).toEqual(plan.stages[1].input);
    await act(async () => byLabel<HTMLInputElement>(`Enable invocation ${second}`).click());
    expect(emitted()).toEqual(plan);
    const card = byLabel(`Invocation ${second}`);
    expect(card.querySelector('button:last-child')?.getAttribute('aria-label')).toBe('Remove ProteinMPNN · model 2');
    const location = card.querySelector<HTMLInputElement>('input:not([aria-label])')!;
    await act(async () => location.click()); expect(calls()[1].execution_target_id).toBeNull();
    await cold(false); expect(Object.hasOwn(calls()[0], 'execution_target_id')).toBe(false); expect(calls()[1].execution_target_id).toBeNull();
});

it('projects legacy pack constraints out without mutating saved drafts and restores literal null/empty states', () => {
    draft = [{ enabled: true, drafts: {}, stage: { id: 's', kind: 'sequence_design', input: { selected: true }, models: [{ invocation_id: 'legacy', model_id: 'caliby_experimental', mode: 'sidechain_pack', params: {}, input_groups: [{ ensemble_id: 'e', states: [{ state_id: 'a', candidate_ref: ref, settings: { fixed_pos_seq: '' } }, { state_id: 'b', candidate_ref: ref, settings: null }] }] }] } }];
    const saved = JSON.stringify(draft);
    expect(emitted().stages[0].models[0].input_groups[0].states.every((s: object) => !Object.hasOwn(s, 'settings'))).toBe(true);
    expect(JSON.stringify(draft)).toBe(saved);
    draft[0] = changeInvocationMode(draft[0], 'legacy', 'ensemble_design');
    expect(calls()[0].input_groups![0].states.map(s => s.settings)).toEqual([{ fixed_pos_seq: '' }, null]);
});
