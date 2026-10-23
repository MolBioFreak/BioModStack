import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BinderInvocationPlan } from '../../src/components/BinderInvocationPlan';
import { activeInvocationPlan, type InvocationDraftStage } from '../../src/lib/binderInvocationPlan';

vi.mock('../../src/lib/api', () => ({ fetchModelById: async () => ({ data: { params: [], modes: [
    { id: 'ligand_aware' }, { id: 'interface_context' }, { id: 'ntp_aware' },
] } }) }));
vi.mock('../../src/components/NativeBinderGeneration', () => ({ NativeSetting: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
let root: Root | undefined;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; });

it('operation mode choices do not promote native diagnostic modes into designers; per-call OFF and mode restore preserve intent', async () => {
    let latest: InvocationDraftStage[] = [{ enabled: true, drafts: {}, stage: { id: 'design', kind: 'sequence_design',
        input: { selected: true }, models: [{ invocation_id: 'exact-call', model_id: 'ligandmpnn', mode: 'ligand_aware',
            params: { write_structures: false, write_fasta: false, temperature: 0 } }] } }];
    function Form() {
        const [stages, setStages] = React.useState(latest);
        return <BinderInvocationPlan stages={stages} onChange={next => { latest = next; setStages(next); }} />;
    }
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => { root!.render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Form /></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    const mode = document.querySelector<HTMLSelectElement>('[aria-label="Mode exact-call"]')!;
    expect([...mode.options].map(option => option.value)).toEqual(['ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware']);
    await act(async () => { mode.value = 'dna_aware'; mode.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(async () => { mode.value = 'ligand_aware'; mode.dispatchEvent(new Event('change', { bubbles: true })); });
    const wire = activeInvocationPlan(latest);
    expect(wire.stages[0].models[0].params).toEqual({ write_structures: false, write_fasta: false, temperature: 0 });
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="Enable invocation exact-call"]')!.click());
    expect(activeInvocationPlan(latest).stages).toEqual([]);
    expect(latest[0].stage.models[0].invocation_id).toBe('exact-call');
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="Enable invocation exact-call"]')!.click());
    expect(activeInvocationPlan(latest)).toEqual(wire);
});

it('authors MD as an independent ordinary operation without inventing chemistry or requiring prediction', async () => {
    let latest: InvocationDraftStage[] = [];
    function Form() {
        const [stages, setStages] = React.useState(latest);
        return <BinderInvocationPlan stages={stages} onChange={next => { latest = next; setStages(next); }} />;
    }
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><Form /></QueryClientProvider>));
    const press = async (text: string) => { const button = [...host.querySelectorAll('button')].find(node => node.textContent === text)!; expect(button).toBeTruthy(); await act(async () => button.click()); };
    await press('Add Molecular dynamics'); await press('Add molecular_dynamics call');
    await press('molecular_dynamics settings');
    const intent = { schema_version: 'bms.md.launch-intent.v2', input: { kind: 'prepared', coordinates: 'exact.gro', topology: 'exact.top' },
        stages: [{ name: 'adsorption', mdp: { 'pull-coord2-k': 0, 'nstxout-compressed': 0 } }], analysis: { enabled: false, selection: null } };
    const textarea = host.querySelector('textarea')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, JSON.stringify(intent)); textarea.dispatchEvent(new Event('input', { bubbles: true })); });
    await press('Apply native MD intent');
    expect(activeInvocationPlan(latest).stages).toHaveLength(1);
    expect(activeInvocationPlan(latest).stages[0]).toMatchObject({ kind: 'md', input: { selected: true }, models: [{ model_id: 'molecular_dynamics', mode: 'simulate', params: { intent } }] });
    client.clear();
});
