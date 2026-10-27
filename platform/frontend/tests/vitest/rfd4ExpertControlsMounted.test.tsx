import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { Rfd4ExpertSettings } from '../../src/components/Rfd4ExpertSettings';

// The actual discovery owner supplies both schema and shipped profile snapshots.
const inventory = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
    "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;print(json.dumps(generation_settings('generate')))"],
{ cwd: resolve(process.cwd(), '../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
let root: Root | undefined;
let latest: Record<string, any>;
let replace: (request: Record<string, any>) => void;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); });
async function mount(initial: Record<string, any> = {}, discovery: unknown = inventory.native_profile_defaults) {
    function Harness() {
        const [request, setRequest] = useState(initial); latest = request; replace = setRequest;
        return <Rfd4ExpertSettings request={request} schema={inventory.parameter_schema} discovery={discovery} onChange={setRequest} />;
    }
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<Harness />)); await show('Profiles and recycling');
}
async function show(title: string) {
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim() === title)!;
    expect(summary).toBeTruthy();
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
const input = (label: string) => document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
async function edit(label: string, value: string) {
    const node = input(label); expect(node).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); });
}
async function reset(key: string) {
    const node = document.querySelector<HTMLButtonElement>(`button[aria-label="Use profile value for ${key}"]`)!;
    expect(node).toBeTruthy(); await act(async () => node.click());
}
it('shows authoritative inherited recycling values without authoring defaults', async () => {
    await mount();
    expect(input('Recycling passes').type).toBe('number');
    expect(input('Recycling passes').step).toBe('1');
    expect(input('Recycling passes').value).toBe(String(inventory.native_profile_defaults.default_generation.n_recycle));
    expect(input('Condition on remaining recycling passes').checked).toBe(inventory.native_profile_defaults.default_generation.condition_on_recycle_remaining);
    expect(document.querySelector('[data-rfd4-override="n_recycle"] code')?.textContent).toBe('generation.n_recycle');
    expect(latest).toEqual({});
    expect(document.body.textContent).toContain('Not a requested override');
});
it('preserves explicit zero, false, cleared numeric drafts, and override reset without pruning retained siblings', async () => {
    const initial = { settings: { generation: {}, metrics_tags: [], seed: 0 }, providers: {} };
    await mount(initial);
    await edit('Recycling passes', '3'); await edit('Recycling passes', '0');
    expect(latest.settings.generation.n_recycle).toBe(0);
    await act(async () => input('Condition on remaining recycling passes').click());
    expect(latest.settings.generation.condition_on_recycle_remaining).toBe(false);
    await edit('Recycling passes', '');
    expect(latest.settings.generation.n_recycle).toBe('');
    expect(JSON.parse(JSON.stringify(latest)).settings.generation.n_recycle).toBe('');
    expect(input('Recycling passes').value).toBe('');
    await reset('n_recycle'); await reset('condition_on_recycle_remaining');
    expect(latest).toEqual(initial);
});
it('prunes only editor-created ancestors when the last scalar override is reset', async () => {
    await mount(); await edit('Recycling passes', '2'); await reset('n_recycle'); expect(latest).toEqual({});
});
it('uses one selected authoritative snapshot but never combines independent group snapshots or schema defaults', async () => {
    const profiles = inventory.native_profile_defaults.generation_by_group;
    const generation = Object.keys(profiles.generation).find(key => key !== inventory.parameter_schema['x-selected-default-groups'].generation)!;
    const recycle = Object.keys(profiles.recycle_strategy).find(key => key !== inventory.parameter_schema['x-selected-default-groups'].recycle_strategy)!;
    await mount({ groups: { generation } });
    expect(input('Recycling passes').value).toBe(String(profiles.generation[generation].n_recycle));
    await act(async () => replace({ groups: { generation, recycle_strategy: recycle } }));
    expect(input('Recycling passes').value).toBe('');
    expect(input('Condition on remaining recycling passes').indeterminate).toBe(true);
    expect(document.body.textContent).toContain('Inherited value not reported');
    expect(latest).toEqual({ groups: { generation, recycle_strategy: recycle } });
    await act(async () => replace({ settings: { generation: { n_recycle: 0, condition_on_recycle_remaining: false } } }));
    expect(input('Recycling passes').value).toBe('0'); expect(input('Condition on remaining recycling passes').checked).toBe(false);
});
it('does not guess an inherited value when discovery is unavailable', async () => {
    await mount({}, null); expect(input('Recycling passes').value).toBe(''); expect(latest).toEqual({});
});
it('retains cleared and false values on a cold serialized reopen and exposes scalar amounts as numbers', async () => {
    const initial = { settings: { generation: { n_recycle: '', condition_on_recycle_remaining: false }, seed: 0, batch_size: 2 }, guidance: { metrics: [] } };
    await mount(JSON.parse(JSON.stringify(initial)));
    expect(input('Recycling passes').value).toBe(''); expect(input('Condition on remaining recycling passes').checked).toBe(false);
    await show('Primary amount overrides'); expect(input('Random seed').type).toBe('number'); expect(input('Random seed').value).toBe('0');
    await edit('Batch size', '4'); expect(latest).toEqual({ ...initial, settings: { ...initial.settings, batch_size: 4 } });
});
