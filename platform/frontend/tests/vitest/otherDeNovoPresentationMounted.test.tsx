import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { execFileSync } from 'node:child_process';
import { afterEach, expect, it, vi } from 'vitest';
import { OtherDeNovoSettings } from '../../src/components/OtherDeNovoSettings';
import { BioXpSchemaInput } from '../../src/components/BioXpSchemaInput';
import { api } from '../../src/lib/api';

const inventories = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', `import json
from scripts.lib.rfd3_scientific_settings import scientific_inventory
from scripts.lib.disco_contract import settings_schema, native_profile_reference
from scripts.lib.laproteina_contract import settings_schema as la_schema, native_profile_defaults
print(json.dumps({'rfd3':scientific_inventory(),'disco':{'parameter_schema':settings_schema(),'native_profile_reference':native_profile_reference()},'laproteina':{'parameter_schema':la_schema(),'native_profile_defaults':native_profile_defaults()}}))`], { cwd: '../..', encoding: 'utf8' }));
let root: Root | undefined;
let client: QueryClient;
const adapter = api.defaults.adapter;
let unexpected: string[] = [];
afterEach(async () => {
    if (root) await act(async () => root!.unmount()); root = undefined;
    client?.clear(); api.defaults.adapter = adapter; document.body.replaceChildren(); vi.restoreAllMocks();
    expect(unexpected).toEqual([]); unexpected = [];
});
async function mount(node: React.ReactNode) {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => { unexpected.push(String(input)); throw Error('Unexpected fetch'); });
    vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation((...args: any[]) => { unexpected.push(String(args[1])); throw Error('Unexpected XHR'); });
    api.defaults.adapter = async config => {
        if (config.url !== '/api/models/protein_modification_experimental/generation-settings') { unexpected.push(String(config.url)); throw Error('Unexpected API'); }
        return { data: inventories[config.params.generator], status: 200, statusText: 'OK', headers: {}, config };
    };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function click(text: string) {
    const element = [...document.querySelectorAll('summary,button')].find(x => x.textContent === text);
    expect(element, text).toBeTruthy(); await act(async () => (element as HTMLElement).click());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}
async function edit(label: string, value: string) {
    const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
    expect(input, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!.call(input, value); input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
const field = (name: string) => document.querySelector<HTMLElement>(`[data-native-setting="${name}"]`)!;
const params = () => JSON.parse(document.querySelector('output')!.textContent!);

it.each(['designable/fast', 'designable/max', 'diverse/fast', 'diverse/max'])('renders every known nested DISCO %s observation in an inherited typed control without authoring it', async selection => {
    const [experiment, effort] = selection.split('/'); const changed = vi.fn();
    await mount(<OtherDeNovoSettings model="disco" task="unconditional" value={{}} requestParams={{ disco_experiment: experiment, disco_effort: effort }} onChange={changed} />);
    await click('Cross-modal guidance'); await click('Sequence sampling');
    const observations = inventories.disco.native_profile_reference.variants[selection];
    for (const [key, observation] of Object.entries(observations) as [string, any][]) {
        if (!key.includes('.')) continue;
        const [group, name] = key.split('.');
        const label = `${group === 'noisy_guidance' ? 'Cross-modal guidance' : 'Sequence sampling'}.${name}`;
        const element = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`);
        const value = observation.value;
        if (value !== null) {
            expect(element, key).not.toBeNull();
            if (typeof value === 'boolean') expect((element as HTMLInputElement).checked, key).toBe(value);
            else if (element!.tagName === 'SELECT') expect((element as HTMLSelectElement).selectedOptions[0].textContent, key).toBe(String(value));
            else expect(element!.value, key).toBe(String(value));
        } else expect(field(group).textContent).toContain('No sequence planner (null)');
        expect(document.querySelector<HTMLSelectElement>(`[aria-label="${label} presence"]`)!.value).toBe('omitted');
    }
    expect(document.body.textContent).toContain('not effective configuration'); expect(changed).not.toHaveBeenCalled();
    // No metadata/default mutation of the query cache.
    expect(client.getQueryData(['other-denovo-settings', 'disco', 'unconditional'])).toEqual(inventories.disco);
});
it('editing an inherited nested leaf submits only that leaf, with zero/false/null/empty/order and unknown siblings retained', async () => {
    const initial = { pcad_disco_settings_json: { seeds: [0, 9, 0], num_inference_seeds: null, deterministic: false, noisy_guidance: { omega_seq: 0, imported: [] } } };
    function Harness() { const [value, setValue] = useState<Record<string, unknown>>(initial); return <><OtherDeNovoSettings model="disco" task="unconditional" value={value} onChange={setValue} /><output>{JSON.stringify(value)}</output></>; }
    await mount(<Harness />); await click('Cross-modal guidance');
    expect(params()).toEqual(initial);
    await edit('Cross-modal guidance.omega_struct', '0');
    expect(params()).toEqual({ pcad_disco_settings_json: { ...initial.pcad_disco_settings_json, noisy_guidance: { ...initial.pcad_disco_settings_json.noisy_guidance, omega_struct: 0 } } });
    await edit('Cross-modal guidance.omega_struct presence', 'omitted');
    expect(params()).toEqual(initial);
    expect(document.querySelector<HTMLInputElement>('[aria-label="Cross-modal guidance.omega_struct"]')!.value).toBe(String(inventories.disco.native_profile_reference.variants['designable/fast']['noisy_guidance.omega_struct'].value));
});
it('RFD3 readable help and checkpoint-null choice preserve sparse sampler settings', async () => {
    const initial = { inference_sampler: { cfg_t_max: null, cfg_scale: 0, allow_realignment: false, cfg_features: [] } };
    const changed = vi.fn();
    await mount(<OtherDeNovoSettings model="rfd3" task="unconditional_monomer" value={initial} onChange={changed} />);
    await click('Sampler and classifier-free guidance');
    expect(field('inference_sampler').textContent).toContain('Noise inflation');
    expect(field('inference_sampler').textContent).toContain('Translation augmentation (Å)');
    expect(field('inference_sampler').textContent).toContain('exceeds this value');
    expect(field('inference_sampler').textContent).toContain('Native checkpoint fallback (null)');
    expect([...field('inference_sampler').querySelectorAll('p')].some(p => p.textContent === 'Explicit null')).toBe(false);
    expect(changed).not.toHaveBeenCalled();
});
it('La null is a qualified no-clamp choice, not Auto or an empty number', async () => {
    const changed = vi.fn();
    await mount(<OtherDeNovoSettings model="laproteina" task="unconditional" selectedPreset="ucond_notri_long" value={{}} onChange={changed} />);
    await click('Backbone sampler');
    expect(field('bb_ca_gt_clamp_val').textContent).toContain('No upper clamp (null)');
    expect(field('bb_ca_gt_clamp_val').querySelector('input')).toBeNull(); expect(changed).not.toHaveBeenCalled();
});
it('shared BioXP consumers remain opt-out: existing labels and explicit-null wording are unchanged', async () => {
    const changed = vi.fn();
    await mount(<BioXpSchemaInput label="Original field" schema={{ type: ['number', 'null'], title: 'Native title', 'x-null-label': 'Native meaning' }} value={null} onChange={changed} fallback={() => null} />);
    expect(document.querySelector('legend')!.textContent).toBe('Original field');
    expect(document.body.textContent).toContain('Explicit null'); expect(document.body.textContent).not.toContain('Native meaning');
    expect(changed).not.toHaveBeenCalled();
});
