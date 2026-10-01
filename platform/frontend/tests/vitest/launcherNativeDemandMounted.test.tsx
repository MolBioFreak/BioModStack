import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { appendFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { BindCraft2Settings, type BC2Section } from '../../src/components/BindCraft2Settings';
import { bc2Label } from '../../src/lib/bindcraft2ControlMetadata';
import { api } from '../../src/lib/api';

// Invoke the actual route-free, read-only server discovery serializer. No DB,
// native runtime, installation probe or scientific operation is invoked.
const inventory = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', 'import json; from services.bindcraft2_typed import schema; print(json.dumps(schema()))'], {
    encoding: 'utf8', env: { ...process.env, BMS_HOME: resolve('../..'), PYTHONPATH: resolve('../api') },
}));
let root: Root | undefined; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); vi.restoreAllMocks(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); }
async function mount(node: React.ReactNode) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(api, 'get').mockResolvedValue({ data: [] } as any);
    await act(async () => root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await settle();
}
async function open(summary: string) {
    const node = [...document.querySelectorAll('summary')].find(node => node.textContent?.startsWith(summary))!;
    expect(node, summary).toBeTruthy(); await act(async () => node.click()); await settle();
}
async function edit(label: string, value: string) {
    const input = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
    expect(input, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); await settle();
}
it('BC2 first inspection constructs complete 47/37 metric inventories, no untouched nested bodies, and retains exact parent request across section changes', async () => {
    const initial = { max_trajectories: 2, trajectory_only: false, losses: { induced_fit_interface: { params: { interface_mask: [0, .25, 1] } } }, filters: { i_pTM: { threshold: null, higher: false, mandatory: false, params: { prediction_state: '' } } }, weights_induced_fit_interface: 0 };
    let latest: any;
    function Parent() {
        const [section, setSection] = React.useState<BC2Section>('sources'); const [value, setValue] = React.useState(initial); latest = value;
        return <><button onClick={() => setSection('objectives')}>Objectives</button><button onClick={() => setSection('sources')}>Sources</button><BindCraft2Settings inventory={inventory} value={value} onChange={setValue as any} section={section} /></>;
    }
    await mount(<Parent />);
    expect(Object.keys(inventory.registered_metrics.filters)).toHaveLength(47); expect(Object.keys(inventory.registered_metrics.losses)).toHaveLength(37);
    if (process.env.BMS_LAUNCHER_MEASUREMENTS) appendFileSync(process.env.BMS_LAUNCHER_MEASUREMENTS, JSON.stringify({ phase: process.env.BMS_LAUNCHER_PHASE ?? 'changed', fixture: 'complete BC2 inventory / untouched sources', dom_nodes: document.querySelectorAll('*').length, controls: document.querySelectorAll('input,select,textarea,button').length, metric_rows: document.querySelectorAll('[aria-label$=".enabled"]').length, nested_metric_controls: document.querySelectorAll('[aria-label*=".params."], [aria-label$=".threshold.mode"]').length }) + '\n');
    expect(document.querySelector('[aria-label="Find losses"]')).toBeNull();
    expect(document.querySelector('[aria-label="Find filters"]')).toBeNull();

    expect(latest).toEqual(initial);
    await act(async () => (document.querySelector('button') as HTMLButtonElement).click()); await settle();
    const selectedCount = (['losses', 'filters'] as const).reduce((total, kind) => total + new Set([...Object.keys(initial[kind]), ...Object.keys(inventory.fields[kind].native_default)]).size, 0);
    expect(document.querySelectorAll('[aria-label$=".enabled"]')).toHaveLength(selectedCount);
    expect(document.querySelectorAll('[aria-label*=".params."], [aria-label$=".threshold.mode"], [aria-label$=".weight"]')).toHaveLength(0);
    await open('Add objectives ·'); await open('Add acceptance metrics ·');
    expect(document.querySelectorAll('[aria-label$=".enabled"]')).toHaveLength(84);
    expect(document.querySelectorAll('[aria-label*=".params."], [aria-label$=".threshold.mode"]')).toHaveLength(0);
    for (const [kind, metrics] of Object.entries(inventory.registered_metrics) as [string, any][]) {
        for (const metric of Object.keys(metrics)) {
            const checkbox = document.querySelector(`[aria-label="${kind}.${metric}.enabled"]`)!;
            const summary = checkbox.closest('div')!.parentElement!.querySelector('summary')!;
            await act(async () => summary.click()); await settle();
        }
        for (const metric of Object.keys(metrics)) expect(document.querySelector(`[aria-label="${kind}.${metric}.prediction_state"]`)).not.toBeNull();
    }
    expect(latest).toEqual(initial);
    await edit('losses.induced_fit_interface.params.interface_mask.1', '0.5');
    expect(latest.losses.induced_fit_interface.params.interface_mask).toEqual([0, .5, 1]);
    const saved = structuredClone(latest);
    await act(async () => (document.querySelectorAll('button')[1] as HTMLButtonElement).click()); await settle();
    expect(latest).toEqual(saved);
    await act(async () => (document.querySelector('button') as HTMLButtonElement).click()); await settle();
    expect(latest).toEqual(saved);
});
it('metric search reaches an unopened inventory and constructs only its matching row, not nested fields', async () => {
    await mount(<BindCraft2Settings inventory={inventory} value={{ max_trajectories: 2 }} onChange={() => {}} section="objectives" />);
    await edit('Find filters', 'All_Atom_Clashes');
    expect(document.querySelectorAll('[aria-label^="filters."][aria-label$=".enabled"]')).toHaveLength(1);
    expect(document.querySelector('[aria-label="filters.All_Atom_Clashes.enabled"]')).not.toBeNull();
    expect(document.querySelectorAll('[aria-label*=".params."]')).toHaveLength(0);
    await open(`Configure ${bc2Label('All_Atom_Clashes')}`);
    expect(document.querySelector('[aria-label="filters.All_Atom_Clashes.params.cutoff"]')).not.toBeNull();
});
